"""FastAPI 앱. 기본 주소 http://127.0.0.1:8000 (로컬 전용).

- 업로드: 요청 본문을 스트림으로 받아 기존 DatasetService.register_csv에 큐로 넘긴다(전체를 메모리에 올리지 않음).
- 등록·해시는 스레드 풀에서 실행해 이벤트 루프를 막지 않는다.
- CSV 분석은 기본적으로 별도 프로세스(spawn)에서 실행한다. 같은 프로세스의 스레드로 돌리면 대용량 정렬 등이
  GIL을 오래 잡아 목록·상태 응답이 1초 넘게 밀리는 것을 80만 행 브라우저 검증에서 확인했다.
- 응답에는 화면용 요약만 담는다. 절대경로·파일 내용·예외 원문은 넣지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import os
import queue
import threading
import json
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from mlworkbench import __version__
from mlworkbench.api.schemas import (
    ApiError,
    ApplyIn,
    ApprovalView,
    ApproveIn,
    CancelIn,
    DatasetDeleteIn,
    FolderIn,
    VisionGoalIn,
    RunDeleteIn,
    RunPatchIn,
    RunEventOut,
    RunList,
    RunStartIn,
    RunView,
    SheetIn,
    ConfirmationView,
    IssueView,
    SummaryStep,
    DatasetList,
    DatasetSummary,
    GoalIn,
    GoalOut,
    PlanSaveIn,
    PlanValidateIn,
    PlanView,
    RecommendationInfo,
    RecommendationOut,
    StepView,
    ModelOption,
    TrainingIn,
    TrainingSaveIn,
    TrainingView,
)
from mlworkbench.app.recommend_service import RecommendationRejected, RecommendationService
from mlworkbench.app.inference_service import InferenceRejected, InferenceService, predict_job
from mlworkbench.app import compare
from mlworkbench.app.deletion import DeleteRejected, DeletionService
from mlworkbench.app.services import DatasetService, JobService, PlanService
from mlworkbench.core.contracts import (
    ChronologicalSplit,
    DatasetState,
    DraftPlan,
    TimeseriesEvaluation,
    TimeseriesGoal,
    ValidationIssue,
    VisionGoal,
    ToolSelection,
    utcnow_iso,
)
from mlworkbench.core.errors import ApprovalError, NotFound, StateTransitionError, SubmitRejected, WorkbenchError
from mlworkbench.runner.manager import RunManager
from mlworkbench.core.registry import PluginRegistry, default_registry
from mlworkbench.providers.config import ProvidersFile, load_providers
from mlworkbench.storage.database import Workspace

log = logging.getLogger("mlworkbench.api")
_CHUNK = 1 << 20
_SENTINEL = None


class _QueueReader:
    """스레드 쪽에서 읽는 파일 객체. 이벤트 루프가 받은 청크를 큐로 전달받는다."""

    def __init__(self, q: "queue.Queue[bytes | None]"):
        self.q, self.buf, self.eof = q, bytearray(), False

    def read(self, n: int = -1) -> bytes:
        while not self.eof and (n < 0 or len(self.buf) < n):
            item = self.q.get()
            if item is _SENTINEL:
                self.eof = True
                break
            self.buf += item
        if n < 0 or n >= len(self.buf):
            out, self.buf = bytes(self.buf), bytearray()
        else:
            out, self.buf = bytes(self.buf[:n]), self.buf[n:]
        return out


def _put(q: "queue.Queue[bytes | None]", item: bytes | None, done: "asyncio.Future") -> bool:
    """소비자가 먼저 끝나면(용량 초과 등) 막히지 않고 포기한다."""
    while not done.done():
        try:
            q.put(item, timeout=0.2)
            return True
        except queue.Full:
            continue
    return False


def _profile_job(ws_root: str, dataset_id: str) -> None:
    """분석 프로세스 진입점(모듈 최상위 함수여야 spawn에서 불러올 수 있다)."""
    DatasetService(Workspace(ws_root)).profile(dataset_id)


def _frontend_dist() -> Path | None:
    env = os.environ.get("MLWB_FRONTEND_DIST")
    cand = Path(env) if env else Path(__file__).resolve().parents[3] / "frontend" / "dist"
    return cand if (cand / "index.html").exists() else None


_STATUS_KO = {"ready": "실행 가능", "not_implemented": "계획 미리보기 · 실행 불가",
              "missing_dependency": "계획 미리보기 · 실행 불가", "invalid": "플러그인 오류 · 실행 불가",
              "unregistered": "등록되지 않은 도구"}
_OFFLINE_PROVIDERS = {"active_provider": "offline_test",
                      "profiles": {"offline_test": {"kind": "mock", "model": "mock-recommender-v1", "auth_mode": "none"}}}


def _load_providers(path: str | os.PathLike | None) -> ProvidersFile:
    p = Path(path or os.environ.get("MLWB_PROVIDERS") or "configs/providers.yaml")
    return load_providers(p) if p.exists() else ProvidersFile.model_validate(_OFFLINE_PROVIDERS)


def step_view(registry: PluginRegistry, sel: ToolSelection) -> StepView:
    spec = registry.get(sel.plugin_id, sel.plugin_version)
    base = {"plugin_id": sel.plugin_id, "plugin_version": sel.plugin_version, "params": sel.params, "reason": sel.reason}
    if spec is None:
        return StepView(**base, registered=False, display_name_ko=sel.plugin_id,
                        description_ko="등록되지 않은 도구라 초안에 적용할 수 없습니다", status="unregistered",
                        status_label=_STATUS_KO["unregistered"])
    return StepView(**base, registered=True, display_name_ko=spec.display_name_ko, description_ko=spec.description_ko,
                    status=spec.status, status_label=_STATUS_KO[spec.status], reason_unavailable=spec.reason_unavailable,
                    parameter_schema=spec.parameter_schema, stage=spec.order.stage, locked=spec.order.locked,
                    must_follow=spec.order.must_follow, origin=spec.origin)


class CompareError(Exception):
    def __init__(self, code: str, message_ko: str):
        super().__init__(message_ko)
        self.code, self.message_ko = code, message_ko


def _duration(start: str | None, end: str | None) -> int | None:
    from datetime import datetime

    if not start or not end:
        return None
    try:
        return max(int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()), 0)
    except ValueError:
        return None


def create_app(workspace: str | os.PathLike | None = None, *, max_upload_bytes: int | None = None,
               serve_frontend: bool = True, profile_in_process: bool = True,
               providers: str | os.PathLike | ProvidersFile | None = None,
               registry: PluginRegistry | None = None) -> FastAPI:
    ws = Workspace(workspace).init()
    registry = registry or default_registry()
    datasets = DatasetService(ws, max_upload_bytes=max_upload_bytes)
    plans = PlanService(ws, registry, datasets)
    jobs = JobService(ws, registry, datasets, plans)
    runs = RunManager(ws, jobs)
    deletion = DeletionService(ws, jobs, datasets)
    inference = InferenceService(ws, registry, jobs, max_upload_bytes=datasets.max_upload_bytes)
    prov_file = providers if isinstance(providers, ProvidersFile) else _load_providers(providers)
    recs = RecommendationService(ws, datasets, plans, registry, prov_file)
    io_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mlwb-io")
    profile_pool: Executor = (
        ProcessPoolExecutor(max_workers=2, mp_context=mp.get_context("spawn")) if profile_in_process
        else ThreadPoolExecutor(max_workers=2, thread_name_prefix="mlwb-profile")
    )

    def _log_failure(fut: Future) -> None:
        if fut.cancelled():
            return
        if fut.exception() is not None:  # 상태 전이 경합 등: 로그만. 레코드는 서비스가 관리
            log.error("백그라운드 분석 실패: %s", type(fut.exception()).__name__)

    def schedule_profile(dataset_id: str) -> None:
        profile_pool.submit(_profile_job, str(ws.root), dataset_id).add_done_callback(_log_failure)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_in_threadpool(datasets.recover_interrupted)
        for m in await run_in_threadpool(datasets.list):
            if m.state == DatasetState.REGISTERED and not datasets.needs_sheet(m):  # 등록 후 분석 전에 종료 → 이어서 분석
                schedule_profile(m.dataset_id)
        await run_in_threadpool(runs.recover)  # 이전 앱 종료로 끝나지 않은 실행 정리(대기 실행은 유지)
        await run_in_threadpool(runs.dispatch)  # 남아 있던 대기열 이어서 시작
        await run_in_threadpool(inference.recover)  # 예측 도중 종료된 적용 정리
        stop = threading.Event()

        def reaper() -> None:  # worker 종료와 실행 기록을 주기적으로 대조
            while not stop.wait(2.0):
                try:
                    runs.reap()
                    runs.scan_orphans()
                    runs.dispatch()  # 앞 실행이 끝났으면 다음 대기 실행 시작
                except Exception:  # noqa: BLE001
                    log.error("실행 상태 대조 실패")

        t = threading.Thread(target=reaper, name="mlwb-run-reaper", daemon=True)
        t.start()
        yield
        stop.set()
        await run_in_threadpool(runs.shutdown)
        io_pool.shutdown(wait=False, cancel_futures=True)
        profile_pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="ML Workbench API", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.datasets, app.state.plans, app.state.schedule_profile = datasets, plans, schedule_profile
    app.state.profile_pool = profile_pool
    app.state.recs = recs
    app.state.jobs, app.state.runs, app.state.inference = jobs, runs, inference

    # 오류 응답: 한글 메시지와 코드만. 예외 원문·경로 없음 -------------------------
    def err(status: int, code: str, message_ko: str, issues=None) -> JSONResponse:
        return JSONResponse(status_code=status, content=ApiError(code=code, message_ko=message_ko, issues=issues)
                            .model_dump(mode="json", exclude_none=True))

    @app.exception_handler(NotFound)
    async def _nf(_, e: NotFound):
        return err(404, "NOT_FOUND", "대상을 찾을 수 없습니다")

    @app.exception_handler(RecommendationRejected)
    async def _rr(_, e: RecommendationRejected):
        return err(422, e.code, e.message_ko)

    @app.exception_handler(StateTransitionError)
    async def _st(_, e: StateTransitionError):
        return err(409, e.code, e.message_ko)

    @app.exception_handler(WorkbenchError)
    async def _wb(_, e: WorkbenchError):
        return err(400, e.code, e.message_ko)

    @app.exception_handler(Exception)
    async def _any(_, e: Exception):
        log.exception("처리되지 않은 오류")
        return err(500, "INTERNAL_ERROR", "서버에서 오류가 났습니다. 잠시 후 다시 시도해 주세요")

    # 라우트 ------------------------------------------------------------------
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__}

    def summary(dataset_id: str) -> DatasetSummary:
        return DatasetSummary.of(datasets.get(dataset_id), datasets.state_since(dataset_id))

    @app.get("/api/datasets", response_model=DatasetList)
    async def list_datasets():
        entries = await run_in_threadpool(datasets.list_entries)
        return DatasetList(items=[DatasetSummary.of(m, since) for m, since in entries], server_time=utcnow_iso())

    @app.get("/api/datasets/{dataset_id}", response_model=DatasetSummary)
    async def get_dataset(dataset_id: str):
        return await run_in_threadpool(summary, dataset_id)

    @app.post("/api/datasets", response_model=DatasetSummary, status_code=201)
    async def upload(request: Request, retry_of: str | None = None):
        """본문 = CSV 바이트(application/octet-stream), 헤더 X-File-Name = URI 인코딩된 원래 파일명."""
        name = unquote(request.headers.get("x-file-name", "")).strip() or "upload.csv"
        q: "queue.Queue[bytes | None]" = queue.Queue(maxsize=8)
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(io_pool, lambda: datasets.register_csv(_QueueReader(q), display_name=name,
                                                                          retry_of=retry_of))
        buf = bytearray()
        try:
            async for chunk in request.stream():
                if fut.done():
                    break
                buf += chunk
                if len(buf) >= _CHUNK:
                    if not await run_in_threadpool(_put, q, bytes(buf), fut):
                        break
                    buf.clear()
            if buf and not fut.done():
                await run_in_threadpool(_put, q, bytes(buf), fut)
        finally:
            if not fut.done():
                await run_in_threadpool(_put, q, _SENTINEL, fut)
        m = await fut
        if m.state == DatasetState.REGISTERED and not datasets.needs_sheet(m):  # XLSX는 시트 선택 후 분석
            schedule_profile(m.dataset_id)
        return await run_in_threadpool(summary, m.dataset_id)

    @app.post("/api/datasets/folder", response_model=DatasetSummary, status_code=201)
    async def register_folder(body: FolderIn):
        """로컬 이미지 폴더 등록(복사하지 않음). 분석은 바로 예약한다."""
        m = await run_in_threadpool(datasets.register_folder, body.path)
        schedule_profile(m.dataset_id)
        return await run_in_threadpool(summary, m.dataset_id)

    @app.get("/api/datasets/{dataset_id}/image-profile")
    async def image_profile(dataset_id: str):
        ip = await run_in_threadpool(datasets.get_image_profile, dataset_id)
        if ip is None:
            return err(409, "PROFILE_NOT_READY", "이미지 분석 결과가 아직 없습니다")
        return ip.model_dump(mode="json")

    @app.get("/api/datasets/{dataset_id}/image", include_in_schema=False)
    async def image_file(dataset_id: str, path: str):
        """미리보기용 이미지(등록 폴더 안 이미지 파일만, 20MB 이하). 로컬 화면 전용."""
        from mlworkbench.datasets import image_folder

        m = await run_in_threadpool(datasets.get, dataset_id)
        if m.modality != "vision" or m.state != DatasetState.PROFILED:
            return err(409, "PROFILE_NOT_READY", "이미지 분석이 끝난 뒤 볼 수 있습니다")
        f = image_folder.safe_relpath(datasets.image_root(m), path)
        if f is None:
            return err(404, "IMAGE_NOT_FOUND", "이미지를 찾을 수 없습니다")
        if f.suffix.lower() not in image_folder.PREVIEW_EXT:
            return err(415, "PREVIEW_UNSUPPORTED", "이 형식(TIFF 등)은 미리보기를 지원하지 않습니다")
        if f.stat().st_size > 20 * 1024 * 1024:
            return err(413, "PREVIEW_TOO_LARGE", "20MB가 넘는 이미지는 미리보기를 하지 않습니다")
        media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".bmp": "image/bmp"}[f.suffix.lower()]
        return FileResponse(f, media_type=media, headers={"Cache-Control": "no-store"})

    @app.get("/api/datasets/{dataset_id}/vision-goal")
    async def get_vision_goal(dataset_id: str):
        m = await run_in_threadpool(datasets.get, dataset_id)
        if m.modality != "vision":
            return err(409, "NOT_VISION", "이미지 데이터셋이 아닙니다")
        found = await run_in_threadpool(plans.latest_draft_for_dataset, dataset_id)
        if not found or found[1].user_goal is None:
            return {"saved": False, "goal": None}
        draft_id, draft, rev = found
        out = {"saved": True, "draft_id": draft_id, "revision": rev, "goal": draft.user_goal.model_dump(mode="json")}
        if m.state == DatasetState.PROFILED:
            issues, counts = await run_in_threadpool(plans.check_vision_goal, dataset_id, draft.user_goal)
            out.update(issues=[i.model_dump() for i in issues], counts=counts)
        return out

    @app.put("/api/datasets/{dataset_id}/vision-goal")
    async def put_vision_goal(dataset_id: str, body: VisionGoalIn):
        from pydantic import ValidationError as _VE

        try:
            goal = VisionGoal(task=body.task, roles=body.roles, calibration=body.calibration)
        except _VE as e:
            return err(422, "GOAL_INVALID", "목표 형식이 올바르지 않습니다", [
                ValidationIssue(code="SCHEMA_INVALID", severity="error",
                                path="user_goal." + ".".join(str(x) for x in er["loc"]), message_ko="허용되지 않는 값입니다")
                for er in e.errors()])
        out = await run_in_threadpool(plans.save_vision_goal, dataset_id, goal)
        if not out["saved"]:
            return err(422, "GOAL_INVALID", "목표를 저장할 수 없습니다. 표시된 항목을 확인해 주세요", out["issues"])
        return {"saved": True, "draft_id": out["draft_id"], "revision": out["revision"], "goal": goal.model_dump(mode="json"),
                "issues": [i.model_dump() for i in out["issues"]], "counts": out["counts"]}

    @app.post("/api/datasets/{dataset_id}/profile", response_model=DatasetSummary, status_code=202)
    async def reprofile(dataset_id: str):
        m = await run_in_threadpool(datasets.get, dataset_id)
        if m.state not in (DatasetState.REGISTERED, DatasetState.PROFILE_FAILED, DatasetState.PROFILED):
            return err(409, "INVALID_STATE", "지금 상태에서는 다시 분석할 수 없습니다")
        if datasets.needs_sheet(m):
            return err(409, "SHEET_REQUIRED", "분석할 시트를 먼저 골라 주세요")
        if m.state == DatasetState.PROFILE_FAILED and m.source_kind == "csv" and (m.local_path is None or m.version is None):
            return err(409, "SOURCE_MISSING", "원본 파일이 없습니다. 파일을 다시 선택해 주세요")
        schedule_profile(dataset_id)
        return await run_in_threadpool(summary, dataset_id)

    @app.post("/api/datasets/{dataset_id}/sheet", response_model=DatasetSummary, status_code=202)
    async def choose_sheet(dataset_id: str, body: SheetIn):
        """XLSX 시트 선택 → 그 시트만 분석. 이미 목표·설정이 있는 데이터셋이면 새 데이터셋으로 만든다(응답의 dataset_id)."""
        m = await run_in_threadpool(datasets.select_sheet, dataset_id, body.sheet)
        if m.state == DatasetState.REGISTERED:  # 새로 고른 시트(같은 시트 재선택이면 그대로)
            schedule_profile(m.dataset_id)
        return await run_in_threadpool(summary, m.dataset_id)

    @app.get("/api/datasets/{dataset_id}/profile")
    async def get_profile(dataset_id: str):
        lp = await run_in_threadpool(datasets.get_local_profile, dataset_id)
        if lp is None:
            return err(409, "PROFILE_NOT_READY", "아직 분석이 끝나지 않았습니다")
        return lp.model_dump(mode="json")

    @app.get("/api/datasets/{dataset_id}/goal", response_model=GoalOut)
    async def get_goal(dataset_id: str):
        await run_in_threadpool(datasets.get, dataset_id)
        found = await run_in_threadpool(plans.latest_draft_for_dataset, dataset_id)
        if not found or found[1].user_goal is None or found[1].user_goal.modality != "timeseries":
            return GoalOut(saved=False)  # 비전 목표는 /vision-goal (V1: 전처리·학습 화면은 아직 시계열 전용)
        draft_id, draft, rev = found
        g = draft.user_goal
        return GoalOut(saved=True, draft_id=draft_id, revision=rev,
                       goal=GoalIn(timestamp_col=g.timestamp_col, target_col=g.target_col, group_col=g.group_col,
                                   horizons=g.horizons))

    @app.put("/api/datasets/{dataset_id}/goal", response_model=GoalOut)
    async def put_goal(dataset_id: str, body: GoalIn):
        m = await run_in_threadpool(datasets.get, dataset_id)
        if m.state != DatasetState.PROFILED:
            return err(409, "DATASET_NOT_READY", "분석이 끝난 데이터셋에서만 목표를 저장할 수 있습니다")
        goal = TimeseriesGoal(timestamp_col=body.timestamp_col or None, target_col=body.target_col or None,
                              group_col=body.group_col or None, horizons=[h.strip() for h in body.horizons if h.strip()])
        out = await run_in_threadpool(plans.save_goal, dataset_id, goal)
        if not out["saved"]:
            return err(422, "GOAL_INVALID", "목표를 저장할 수 없습니다. 표시된 항목을 확인해 주세요", out["issues"])
        return GoalOut(saved=True, draft_id=out["draft_id"], revision=out["revision"],
                       goal=GoalIn(**goal.model_dump(include={"timestamp_col", "target_col", "group_col", "horizons"})),
                       issues=out["issues"])

    # S2 전처리 ---------------------------------------------------------------
    def plan_view(dataset_id: str, *, skipped: list | None = None, issues=None) -> PlanView:
        m = datasets.get(dataset_id)
        draft_id, draft, rev = plans._require_plan(dataset_id)
        g = draft.user_goal
        rec = None
        if draft.recommendation_id:
            r = recs.get(draft.recommendation_id)
            rec = RecommendationInfo(recommendation_id=draft.recommendation_id, mode=r["mode"],
                                     source_label=r["source_label"])
        present = {s.plugin_id for s in draft.preprocessing + draft.excluded_steps}
        catalog = [step_view(registry, ToolSelection(plugin_id=s.id, plugin_version=s.version))
                   for s in registry.find(kind="preprocessor", modality=m.modality, task=draft.task)
                   if s.id not in present]
        model = registry.get(draft.model.plugin_id, draft.model.plugin_version) if draft.model else None
        return PlanView(
            dataset_id=dataset_id, dataset_name=m.name,
            goal=GoalIn(timestamp_col=g.timestamp_col, target_col=g.target_col, group_col=g.group_col, horizons=g.horizons),
            draft_id=draft_id, revision=rev, source=draft.source, recommendation=rec,
            steps=[step_view(registry, s) for s in draft.preprocessing],
            excluded=[step_view(registry, s) for s in draft.excluded_steps],
            model_name=(model.display_name_ko if model else (draft.model.plugin_id if draft.model else None)),
            catalog=catalog, plugin_problems=registry.load_problems, columns=[{"name": c.name, "dtype": c.dtype} for c in m.columns],
            issues=issues if issues is not None else plans.validate_plan(draft), skipped=skipped or [])

    @app.get("/api/datasets/{dataset_id}/plan", response_model=PlanView)
    async def get_plan(dataset_id: str):
        return await run_in_threadpool(plan_view, dataset_id)

    @app.post("/api/datasets/{dataset_id}/recommendation", response_model=RecommendationOut)
    async def recommend(dataset_id: str):
        out = await recs.recommend(dataset_id)
        p = out["proposal"]
        model = p.get("model") or {}
        spec = registry.get(model.get("plugin_id", ""), model.get("plugin_version", ""))
        return RecommendationOut(
            recommendation_id=out["recommendation_id"], mode=out["mode"], source_label=out["source_label"],
            steps=[step_view(registry, ToolSelection.model_validate(s)) for s in p["preprocessing"]],
            model_name=spec.display_name_ko if spec else model.get("plugin_id"),
            rationale=p.get("rationale", ""), warnings=p.get("warnings", []))

    @app.post("/api/datasets/{dataset_id}/plan/apply", response_model=PlanView)
    async def apply(dataset_id: str, body: ApplyIn):
        rec = await run_in_threadpool(recs.get, body.recommendation_id)
        rec["recommendation_id"] = body.recommendation_id
        out = await run_in_threadpool(plans.apply_recommendation, dataset_id, rec, expected_revision=body.expected_revision)
        return await run_in_threadpool(lambda: plan_view(dataset_id, skipped=out["skipped"]))

    @app.put("/api/datasets/{dataset_id}/plan", response_model=PlanView)
    async def save_plan(dataset_id: str, body: PlanSaveIn):
        steps = [ToolSelection(**s.model_dump()) for s in body.steps]
        excluded = [ToolSelection(**s.model_dump()) for s in body.excluded]
        out = await run_in_threadpool(lambda: plans.save_steps(dataset_id, steps=steps, excluded=excluded,
                                                               expected_revision=body.expected_revision,
                                                               source=body.source))
        if not out["saved"]:
            return err(422, "PLAN_NOT_APPLICABLE", "초안에 적용할 수 없는 항목이 있습니다. 표시된 항목을 확인해 주세요",
                       out["issues"])
        return await run_in_threadpool(plan_view, dataset_id)

    @app.post("/api/datasets/{dataset_id}/plan/validate")
    async def validate_plan(dataset_id: str, body: PlanValidateIn):
        def run():
            _, base, _ = plans._require_plan(dataset_id)
            draft = DraftPlan.model_validate({**base.model_dump(), "preprocessing": [s.model_dump() for s in body.steps]})
            return plans.validate_plan(draft)
        issues = await run_in_threadpool(run)
        return {"issues": [i.model_dump() for i in issues]}

    # S3 학습 설정 -------------------------------------------------------------
    DEFAULT_SPLIT = ChronologicalSplit().model_dump(mode="json")
    DEFAULT_EVAL = TimeseriesEvaluation(metrics=["mae", "rmse"]).model_dump(mode="json")

    def training_view(dataset_id: str, *, issues=None) -> TrainingView:
        m = datasets.get(dataset_id)
        draft_id, draft, rev = plans._require_plan(dataset_id)
        g = draft.user_goal
        rec_model = None
        if draft.recommendation_id:
            rec_model = (recs.get(draft.recommendation_id)["proposal"].get("model") or {}).get("plugin_id")
        models = [ModelOption(plugin_id=s.id, plugin_version=s.version, display_name_ko=s.display_name_ko,
                              description_ko=s.description_ko, status=s.status, status_label=_STATUS_KO[s.status],
                              reason_unavailable=s.reason_unavailable, parameter_schema=s.parameter_schema,
                              requires_gpu=s.requires_gpu, recommended=(s.id == rec_model))
                  for s in registry.find(kind="model", modality=m.modality, task=draft.task)]
        selected_id = draft.model.plugin_id if draft.model else None
        models.sort(key=lambda o: (not o.recommended, o.plugin_id != selected_id, o.status != "ready", o.plugin_id))
        defaults = [k for k, v in (("split_policy", draft.split_policy), ("evaluation", draft.evaluation)) if v is None]
        names = {s.id: s.display_name_ko for s in registry.all()}
        return TrainingView(
            dataset_id=dataset_id, dataset_name=m.name, draft_id=draft_id, revision=rev,
            goal=GoalIn(timestamp_col=g.timestamp_col, target_col=g.target_col, group_col=g.group_col, horizons=g.horizons),
            models=models, model=draft.model.model_dump() if draft.model else None,
            train_budget=draft.train_budget.model_dump(mode="json"),
            split_policy=draft.split_policy.model_dump(mode="json") if draft.split_policy else DEFAULT_SPLIT,
            evaluation=draft.evaluation.model_dump(mode="json") if draft.evaluation else DEFAULT_EVAL,
            defaults_applied=defaults,
            preprocessing=[names.get(s.plugin_id, s.plugin_id) for s in draft.preprocessing],
            excluded=[names.get(s.plugin_id, s.plugin_id) for s in draft.excluded_steps],
            issues=issues if issues is not None else plans.training_issues(draft))

    @app.get("/api/datasets/{dataset_id}/training", response_model=TrainingView)
    async def get_training(dataset_id: str):
        return await run_in_threadpool(training_view, dataset_id)

    @app.put("/api/datasets/{dataset_id}/training", response_model=TrainingView)
    async def save_training(dataset_id: str, body: TrainingSaveIn):
        fields = body.model_dump(exclude={"expected_revision"})
        out = await run_in_threadpool(lambda: plans.save_training(dataset_id, fields,
                                                                  expected_revision=body.expected_revision))
        if not out["saved"]:
            return err(422, "TRAINING_INVALID", "학습 설정을 저장할 수 없습니다. 표시된 항목을 확인해 주세요", out["issues"])
        return await run_in_threadpool(training_view, dataset_id)

    @app.post("/api/datasets/{dataset_id}/training/validate")
    async def validate_training(dataset_id: str, body: TrainingIn):
        def run():
            draft, schema_issues = plans.training_candidate(dataset_id, body.model_dump())
            return schema_issues if draft is None else plans.training_issues(draft)
        issues = await run_in_threadpool(run)
        return {"issues": [i.model_dump() for i in issues]}

    # S4 확인·승인 -------------------------------------------------------------
    def summary_step(sel: ToolSelection) -> SummaryStep:
        spec = registry.get(sel.plugin_id, sel.plugin_version)
        status = spec.status if spec else "unregistered"
        return SummaryStep(plugin_id=sel.plugin_id, plugin_version=sel.plugin_version,
                           display_name_ko=spec.display_name_ko if spec else sel.plugin_id,
                           params=sel.params, status=status, status_label=_STATUS_KO[status])

    def confirmation_view(dataset_id: str) -> ConfirmationView:
        v = plans.confirmation(dataset_id)
        draft, m = v["draft"], v["dataset"]
        g = draft.user_goal
        issues = [IssueView(**i.model_dump(), step=plans.issue_step(i.path)) for i in v["issues"]]
        errors = sum(i.severity == "error" for i in issues)
        approval = ApprovalView(**v["approval"])
        return ConfirmationView(
            dataset_id=dataset_id, dataset_name=m.name, dataset_version=m.version, dataset_rows=m.row_count,
            draft_id=v["draft_id"], revision=v["revision"],
            goal=GoalIn(timestamp_col=g.timestamp_col, target_col=g.target_col, group_col=g.group_col, horizons=g.horizons),
            preprocessing=[summary_step(s) for s in draft.preprocessing],
            excluded=[summary_step(s).display_name_ko for s in draft.excluded_steps],
            model=summary_step(draft.model) if draft.model else None,
            train_budget=draft.train_budget.model_dump(mode="json"),
            split_policy=draft.split_policy.model_dump(mode="json") if draft.split_policy else None,
            evaluation=draft.evaluation.model_dump(mode="json") if draft.evaluation else None,
            issues=issues, error_count=errors, warning_count=sum(i.severity == "warning" for i in issues),
            approval=approval, can_approve=(errors == 0 and approval.status != "approved"),
            run_available=(approval.status == "approved" and draft.model is not None
                           and registry.get_model_adapter(draft.model.plugin_id, draft.model.plugin_version) is not None))

    @app.get("/api/datasets/{dataset_id}/confirmation", response_model=ConfirmationView)
    async def get_confirmation(dataset_id: str):
        return await run_in_threadpool(confirmation_view, dataset_id)

    @app.post("/api/datasets/{dataset_id}/approve", response_model=ConfirmationView)
    async def approve(dataset_id: str, body: ApproveIn):
        try:
            await run_in_threadpool(lambda: plans.approve_for_dataset(
                dataset_id, expected_revision=body.expected_revision, confirmed=body.confirmed))
        except ApprovalError as e:
            if e.code == "VALIDATION_FAILED":
                issues = [IssueView(**i.model_dump(), step=plans.issue_step(i.path))
                          for i in getattr(e, "issues", [])]
                return err(422, e.code, e.message_ko, issues)
            if e.code == "STALE_REVISION":
                return err(409, "REVISION_CONFLICT", e.message_ko)
            return err(400, e.code, e.message_ko)
        return await run_in_threadpool(confirmation_view, dataset_id)

    # P2 실행·결과 ------------------------------------------------------------
    _RUN_STATUS = {"APPROVAL_NOT_FOUND": 404, "APPROVAL_REQUIRED": 400}

    def run_view(run_id: str, *, full: bool = True, duplicate: bool = False,
                 queue: tuple[list[str], list[str]] | None = None) -> RunView:
        rec = jobs.get_record(run_id)
        status = jobs.get_status(run_id)
        position = ahead = None
        if status.state.value == "QUEUED":
            active, waiting = queue if queue is not None else runs.queue_state()
            if run_id in waiting:
                position = waiting.index(run_id) + 1
                ahead = len(active) + position - 1
        ds = datasets.find(rec.dataset_id)
        try:
            cfg = plans.get_snapshot(rec.plan_id).immutable_config
        except NotFound:
            cfg = {}
        last = status.events[-1] if status.events else None
        exp = ws.root / "experiments" / run_id
        view = RunView(
            run_id=run_id, dataset_id=rec.dataset_id, dataset_name=ds.name if ds else None, plan_id=rec.plan_id,
            plan_hash=rec.plan_hash, state=status.state.value, stage=status.stage,
            progress=last.progress if last and last.stage == status.stage else None,
            created_at=rec.created_at, started_at=rec.started_at, finished_at=rec.finished_at,
            error_code=rec.error_code, error_message_ko=rec.error_message_ko,
            model=(cfg.get("model") or {}).get("plugin_id"),
            horizons=list((cfg.get("user_goal") or {}).get("horizons") or []),
            train_budget=cfg.get("train_budget"),
            events=[RunEventOut(**e.model_dump(mode="json", exclude={"run_id"})) for e in status.events]
            if full else [], duplicate=duplicate, summary=rec.result,
            predictions_available=(exp / "predictions" / "predictions.csv").exists() and status.state.value == "SUCCEEDED",
            queue_position=position, queue_ahead=ahead, max_parallel=runs.max_parallel, name=rec.name,
            steps=[_step_name(s) for s in cfg.get("preprocessing") or []],
            duration_s=_duration(rec.started_at, rec.finished_at),
            protocol_key=(compare.protocol_key(compare.cached_protocol(cfg, rec.dataset_version, exp))
                          if status.state.value == "SUCCEEDED" and cfg else None))
        if full and status.state.value == "SUCCEEDED":
            for name, attr in (("split.json", "split"), ("metrics.json", "metrics"), ("metrics_meta.json", "metrics_meta")):
                f = exp / name
                if f.exists():
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if name == "split.json":
                        data = {k: v for k, v in data.items() if k not in ("load", "notes")} | {
                            "notes": data.get("notes", []),
                            "rows_used": (data.get("load") or {}).get("rows_used"),
                            "timezone_policy": (data.get("load") or {}).get("timezone_policy")}
                    setattr(view, attr, data)
        return view

    def _step_name(s: dict) -> str:
        spec = registry.get(s.get("plugin_id", ""), s.get("plugin_version", ""))
        return spec.display_name_ko if spec is not None else s.get("plugin_id", "")

    def _compare_view(run_ids: list[str]) -> dict:
        ids = list(dict.fromkeys(run_ids))
        if not 2 <= len(ids) <= 6:
            raise CompareError("COMPARE_COUNT", "비교할 실행을 2~6개 고르세요")
        items, protos = [], []
        for rid in ids:
            try:
                rec = jobs.get_record(rid)
            except NotFound:
                raise CompareError("RUN_NOT_FOUND", f"없는 실행입니다: {rid}") from None
            if jobs.get_status(rid).state.value != "SUCCEEDED":
                raise CompareError("RUN_NOT_SUCCEEDED", f"완료된 실행만 비교할 수 있습니다: {rid}")
            snap = plans.get_snapshot(rec.plan_id)
            cfg = snap.immutable_config
            exp = ws.root / "experiments" / rid
            proto = compare.cached_protocol(cfg, rec.dataset_version, exp)
            protos.append(proto)
            ds = datasets.find(rec.dataset_id)
            metrics = json.loads((exp / "metrics.json").read_text(encoding="utf-8")) if (exp / "metrics.json").exists() else {}
            names = {s.get("plugin_id"): _step_name(s) for s in cfg.get("preprocessing") or []}
            items.append({
                "run_id": rid, "name": rec.name, "dataset_id": rec.dataset_id, "dataset_name": ds.name if ds else None,
                "created_at": rec.created_at, "duration_s": _duration(rec.started_at, rec.finished_at),
                "model": (cfg.get("model") or {}).get("plugin_id"), "steps": [_step_name(s) for s in cfg.get("preprocessing") or []],
                "conditions": compare.conditions_of(cfg, step_names=names, dataset_name=ds.name if ds else None,
                                                    plugin_code=snap.plugin_code),
                "protocol": {k: v for k, v in proto.items() if k != "test_set_by_horizon"},
                "protocol_key": compare.protocol_key(proto), "metrics": metrics,
            })
        ref = protos[0]
        for it, pr in zip(items, protos):
            it["protocol_diffs"] = compare.protocol_diffs(ref, pr)
            it["same_protocol"] = not it["protocol_diffs"]
            it["comparable_horizons"] = compare.comparable_horizons(ref, pr)
        cond_keys = list(compare.CONDITION_KO)
        diffs = [{"key": k, "label_ko": compare.CONDITION_KO[k], "values": [it["conditions"][k] for it in items]}
                 for k in cond_keys if len({it["conditions"][k] for it in items}) > 1]
        groups = compare.groups_of(ws.root / "experiments" / ids[0])
        return {"reference": ids[0], "runs": items, "condition_diffs": diffs,
                "metric_names": list(dict.fromkeys(m for p in protos for m in p["metrics"])),
                "horizons": list(dict.fromkeys(h for p in protos for h in p["horizons"])), "groups": groups,
                "protocol_labels": compare.PROTOCOL_KO}

    @app.get("/api/runs/compare")
    async def compare_runs(run_ids: list[str] = Query(default=[])):
        try:
            return await run_in_threadpool(_compare_view, run_ids)
        except CompareError as e:
            return err(409 if e.code == "RUN_NOT_SUCCEEDED" else 400 if e.code == "COMPARE_COUNT" else 404,
                       e.code, e.message_ko)

    @app.get("/api/runs/compare/series")
    async def compare_series(horizon: str, group: str = "", run_ids: list[str] = Query(default=[]), split: str = "test"):
        def go():
            view = _compare_view(run_ids)
            if horizon not in view["horizons"]:
                raise CompareError("HORIZON_UNKNOWN", f"비교 대상에 없는 예측 시점입니다: {horizon}")
            if split not in ("val", "test"):
                raise CompareError("SPLIT_UNKNOWN", "split은 val 또는 test입니다")
            exps = [ws.root / "experiments" / it["run_id"] for it in view["runs"]]
            return compare.series(exps, horizon=horizon, group=group, split=split)
        try:
            return await run_in_threadpool(go)
        except CompareError as e:
            return err(409 if e.code == "RUN_NOT_SUCCEEDED" else 400, e.code, e.message_ko)

    @app.post("/api/datasets/{dataset_id}/runs", response_model=RunView)
    async def start_run(dataset_id: str, body: RunStartIn):
        def go():
            name = jobs.clean_name(body.name)  # 잘못된 이름이면 실행을 만들기 전에 거절
            out = jobs.submit_approved(dataset_id=dataset_id, plan_id=body.plan_id, plan_hash=body.plan_hash)
            if name and not out["duplicate"]:
                jobs.rename(out["run_id"], name)
            if out["state"] == "QUEUED":
                runs.dispatch()  # 자리가 있으면 바로 시작, 없으면 대기열(실행당 worker 1개는 잠금 파일로 보장)
            return out
        try:
            out = await run_in_threadpool(go)
        except SubmitRejected as e:
            return err(_RUN_STATUS.get(e.code, 409), e.code, e.message_ko)
        return await run_in_threadpool(lambda: run_view(out["run_id"], duplicate=out["duplicate"]))

    @app.get("/api/runs", response_model=RunList)
    async def list_runs(dataset_id: str | None = None):
        def go():
            queue = runs.queue_state()
            return RunList(items=[run_view(r.run_id, full=False, queue=queue) for r in jobs.list_runs(dataset_id)])
        return await run_in_threadpool(go)

    @app.get("/api/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str):
        await run_in_threadpool(runs.reap)
        return await run_in_threadpool(run_view, run_id)

    @app.patch("/api/runs/{run_id}", response_model=RunView)
    async def rename_run(run_id: str, body: RunPatchIn):
        await run_in_threadpool(jobs.rename, run_id, body.name)
        return await run_in_threadpool(run_view, run_id)

    @app.get("/api/runs/{run_id}/delete-preview")
    async def run_delete_preview(run_id: str):
        return await run_in_threadpool(deletion.run_preview, run_id)

    @app.delete("/api/runs/{run_id}")
    async def delete_run(run_id: str, body: RunDeleteIn):
        try:
            return await run_in_threadpool(lambda: deletion.delete_run(
                run_id, confirmed=body.confirmed, include_applications=body.include_applications))
        except DeleteRejected as e:
            return err(409, e.code, e.message_ko)

    @app.get("/api/datasets/{dataset_id}/delete-preview")
    async def dataset_delete_preview(dataset_id: str):
        return await run_in_threadpool(deletion.dataset_preview, dataset_id)

    @app.delete("/api/datasets/{dataset_id}")
    async def delete_dataset(dataset_id: str, body: DatasetDeleteIn):
        try:
            return await run_in_threadpool(lambda: deletion.delete_dataset(
                dataset_id, confirmed=body.confirmed, include_runs=body.include_runs))
        except DeleteRejected as e:
            return err(409, e.code, e.message_ko)

    @app.post("/api/runs/{run_id}/cancel-confirmation")
    async def cancel_confirmation(run_id: str):
        return {"confirmation_id": await run_in_threadpool(jobs.request_cancel_confirmation, run_id)}

    @app.post("/api/runs/{run_id}/cancel", response_model=RunView)
    async def cancel_run(run_id: str, body: CancelIn):
        try:
            await run_in_threadpool(jobs.cancel, run_id, body.confirmation_id)
            await run_in_threadpool(runs.cancel_waiting, run_id)  # 대기 중이면 worker 없이 바로 취소
        except SubmitRejected as e:
            return err(409 if e.code == "RUN_FINISHED" else 400, e.code, e.message_ko)
        return await run_in_threadpool(run_view, run_id)

    @app.get("/api/runs/{run_id}/predictions.csv", include_in_schema=False)
    async def download_predictions(run_id: str):
        await run_in_threadpool(jobs.get_record, run_id)  # 없으면 404
        f = ws.root / "experiments" / run_id / "predictions" / "predictions.csv"
        if not f.exists():
            return err(404, "NOT_FOUND", "예측 결과가 아직 없습니다")
        return FileResponse(f, media_type="text/csv", filename=f"{run_id}_predictions.csv")

    # S7 새 데이터 적용 ----------------------------------------------------------
    def _app_err(e: InferenceRejected):
        return err(409 if e.code in ("RUN_NOT_SUCCEEDED", "NOT_CHECKED", "SHEET_REQUIRED") else 400, e.code, e.message_ko)

    def _public(rec: dict) -> dict:  # 화면용: 내부 경로 없음(기록에는 원래 경로를 저장하지 않음)
        return {k: v for k, v in rec.items() if k != "model_sha256"} | {
            "download_available": rec.get("state") == "SUCCEEDED"}

    @app.get("/api/models")
    async def list_models():
        return {"items": await run_in_threadpool(inference.models)}

    @app.get("/api/models/{run_id}")
    async def model_requirements(run_id: str):
        try:
            return await run_in_threadpool(inference.requirements, run_id)
        except InferenceRejected as e:
            return _app_err(e)

    @app.post("/api/models/{run_id}/applications")
    async def upload_application(run_id: str, request: Request):
        """본문 = CSV 바이트, 헤더 X-File-Name = URI 인코딩된 파일명. 저장 후 바로 입력 점검."""
        name = unquote(request.headers.get("x-file-name", "")).strip() or "input.csv"
        q: "queue.Queue[bytes | None]" = queue.Queue(maxsize=8)
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(io_pool, lambda: inference.upload(run_id, _QueueReader(q), display_name=name))
        buf = bytearray()
        try:
            async for chunk in request.stream():
                if fut.done():
                    break
                buf += chunk
                if len(buf) >= _CHUNK:
                    if not await run_in_threadpool(_put, q, bytes(buf), fut):
                        break
                    buf.clear()
            if buf and not fut.done():
                await run_in_threadpool(_put, q, bytes(buf), fut)
        finally:
            if not fut.done():
                await run_in_threadpool(_put, q, _SENTINEL, fut)
        try:
            return _public(await fut)
        except InferenceRejected as e:
            return _app_err(e)

    @app.get("/api/applications")
    async def list_applications():
        return {"items": [_public(r) for r in await run_in_threadpool(inference.list)]}

    @app.get("/api/applications/{application_id}")
    async def get_application(application_id: str):
        return _public(await run_in_threadpool(inference.get, application_id))

    @app.post("/api/applications/{application_id}/rebind")
    async def rebind_application(application_id: str, body: dict):
        try:
            return _public(await run_in_threadpool(inference.rebind, application_id, str(body.get("run_id", ""))))
        except InferenceRejected as e:
            return _app_err(e)

    @app.post("/api/applications/{application_id}/check")
    async def recheck_application(application_id: str):
        try:
            return _public(await run_in_threadpool(inference.check, application_id))
        except InferenceRejected as e:
            return _app_err(e)

    @app.post("/api/applications/{application_id}/sheet")
    async def application_sheet(application_id: str, body: SheetIn):
        """S7 엑셀 입력의 시트 선택 → 점검. 이미 다른 시트로 점검했다면 새 적용 기록(응답의 application_id)."""
        try:
            return _public(await run_in_threadpool(inference.select_sheet, application_id, body.sheet))
        except InferenceRejected as e:
            return _app_err(e)

    @app.post("/api/applications/{application_id}/predict")
    async def predict_application(application_id: str):
        try:
            before = await run_in_threadpool(inference.get, application_id)
            rec = await run_in_threadpool(inference.begin_predict, application_id)
        except InferenceRejected as e:
            return _app_err(e)
        if before["state"] != "RUNNING" and rec["state"] == "RUNNING":
            def done(fut: Future) -> None:  # 작업 프로세스가 비정상 종료하면 RUNNING으로 남기지 않는다
                if not fut.cancelled() and fut.exception() is not None:
                    log.error("예측 작업 실패: %s", type(fut.exception()).__name__)
                    try:
                        if inference.get(application_id)["state"] == "RUNNING":
                            inference._fail(application_id, "WORKER_EXITED", "예측 작업이 결과를 남기지 못하고 종료되었습니다")
                    except Exception:  # noqa: BLE001
                        pass

            try:
                if isinstance(profile_pool, ProcessPoolExecutor):  # 별도 프로세스: 앱 응답에 영향 없음
                    fut = profile_pool.submit(predict_job, str(ws.root), application_id)
                else:
                    fut = profile_pool.submit(inference.run_predict, application_id)
                fut.add_done_callback(done)
            except RuntimeError:  # 종료 중인 풀
                rec = await run_in_threadpool(inference._fail, application_id, "WORKER_START_FAILED",
                                              "예측 작업을 시작하지 못했습니다")
        return _public(rec)

    @app.get("/api/applications/{application_id}/predictions.csv", include_in_schema=False)
    async def download_application(application_id: str):
        p = await run_in_threadpool(inference.result_path, application_id)
        return FileResponse(p, media_type="text/csv", filename=f"{application_id}_predictions.csv")

    # 빌드된 React 화면 ---------------------------------------------------------
    dist = _frontend_dist() if serve_frontend else None
    if dist:
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return err(404, "NOT_FOUND", "대상을 찾을 수 없습니다")
            return FileResponse(dist / "index.html")

    return app
