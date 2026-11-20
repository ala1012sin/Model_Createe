"""worker 프로세스: 승인 스냅샷 하나를 실행한다.

입력은 run_id뿐이다. 실행 설정은 DB의 승인 스냅샷(해시 검증된 immutable_config)에서만 읽는다.
화면·LLM이 보낸 값은 쓰지 않는다. 취소는 DB의 실행 상태(CANCEL_REQUESTED)로 전달된다.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from mlworkbench.core.contracts import RunState, utcnow_iso
from mlworkbench.core.errors import StateTransitionError
from mlworkbench.core.hashing import sha256_of

HEARTBEAT = "heartbeat"
log = logging.getLogger("mlworkbench.worker")


def _lower_priority() -> None:
    """앱 응답·다른 작업이 밀리지 않도록 worker 우선순위를 낮춘다."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.GetCurrentProcess.restype = wintypes.HANDLE  # 의사 핸들(-1): 64비트에서 잘리지 않게
            k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k.SetPriorityClass.restype = wintypes.BOOL
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
        else:
            os.nice(5)
    except Exception:  # noqa: BLE001 - 우선순위 조정 실패는 실행에 영향 없음
        pass


class _Heartbeat(threading.Thread):
    def __init__(self, path: Path, every: float = 2.0):
        super().__init__(daemon=True)
        self.path, self.every, self.stop = path, every, threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.path.write_text(str(time.time()), encoding="utf-8")
            except OSError:
                pass
            self.stop.wait(self.every)


def execute(ws_root: str, run_id: str) -> int:
    from mlworkbench.app.services import DatasetService, JobService, PlanService
    from mlworkbench.core.registry import default_registry
    from mlworkbench.pipelines.timeseries import Canceled, RunHooks, run_timeseries
    from mlworkbench.pipelines.ts_frame import PipelineError
    from mlworkbench.plugins.models import Throttle
    from mlworkbench.storage.database import Workspace

    ws = Workspace(ws_root)
    registry = default_registry()
    datasets = DatasetService(ws)
    plans = PlanService(ws, registry, datasets)
    jobs = JobService(ws, registry, datasets, plans)
    exp = ws.root / "experiments" / run_id
    exp.mkdir(parents=True, exist_ok=True)
    hb = _Heartbeat(exp / HEARTBEAT)
    hb.start()
    log_file = (exp / "train.log").open("a", encoding="utf-8")

    def write(msg: str) -> None:
        log_file.write(f"{utcnow_iso()} {msg}\n")
        log_file.flush()

    state = {"stage": "prepare"}
    last_check = [0.0]

    def check_cancel() -> None:
        now = time.monotonic()
        if now - last_check[0] < 0.5:
            return
        last_check[0] = now
        if jobs.get_status(run_id).state == RunState.CANCEL_REQUESTED:
            raise Canceled()

    throttle = Throttle(1.0)

    def emit(stage: str, progress: float | None, code: str) -> None:
        if stage == state["stage"] and code == "TRAIN_PROGRESS" and not throttle.ready():
            return
        state["stage"] = stage
        try:
            jobs.append_event(run_id, state=RunState.RUNNING, stage=stage, message_code=code, progress=progress)
        except StateTransitionError:  # CANCEL_REQUESTED → RUNNING 불가 = 취소 요청됨
            raise Canceled() from None
        write(f"[{stage}] {code}" + (f" {progress:.2f}" if progress is not None else ""))

    try:
        rec = jobs.get_record(run_id)
        snap = plans.get_snapshot(rec.plan_id)
        if sha256_of(snap.immutable_config) != snap.plan_hash or snap.plan_hash != rec.plan_hash:
            raise PipelineError("PLAN_HASH_MISMATCH", "승인한 설정이 변조되어 실행하지 않았습니다")
        from mlworkbench.app.services import plugin_code_changed

        if changed := plugin_code_changed(snap, registry):
            raise PipelineError("PLUGIN_CODE_CHANGED", f"승인 이후 사용자 플러그인 코드가 바뀌어 실행하지 않았습니다: {changed}")
        ds = datasets.get(snap.dataset_id)
        if ds.version != snap.dataset_version:
            raise PipelineError("DATASET_CHANGED", "승인 이후 데이터셋이 바뀌어 실행하지 않았습니다")
        prof = datasets.get_local_profile(snap.dataset_id)
        if prof is None:
            raise PipelineError("PROFILE_MISSING", "데이터셋 분석 결과가 없습니다. 다시 분석해 주세요")
        # 분석에 쓴 표(CSV 원본 또는 XLSX에서 정규화한 table.csv) — 승인한 데이터 버전(내용 해시)과 같아야 한다
        table = ws.root / (ds.local_path or "")
        if not ds.local_path or not table.exists() or _file_sha(table) != snap.dataset_version:
            raise PipelineError("DATASET_CHANGED", "승인 이후 데이터 파일이 바뀌어 실행하지 않았습니다")
        jobs.update_record(run_id, started_at=utcnow_iso())
        emit("prepare", None, "RUN_STARTED")
        write(f"run {run_id} plan {snap.plan_id} hash {snap.plan_hash}")
        result = run_timeseries(
            snap.immutable_config, csv_path=table,
            csv_options={"encoding": prof.encoding, "delimiter": prof.delimiter},
            numeric_cols=[c.name for c in prof.columns if c.dtype == "numeric"],
            registry=registry, out_dir=exp, hooks=RunHooks(emit=emit, check_cancel=check_cancel, log=write))
        (exp / "dataset_manifest.json").write_text(ds.model_dump_json(indent=2), encoding="utf-8")
        sp = result["split"]
        headline = {k: v for k, v in result["metrics"].get("test", {}).get("overall", {}).items()}
        jobs.update_record(run_id, finished_at=utcnow_iso(), train_sample_count=sp["train_samples"],
                           resolved_train_range={"start": (sp["train_period"] or [None])[0],
                                                 "end": (sp["train_period"] or [None, None])[1],
                                                 "train_end": sp["train_end"], "val_end": sp["val_end"]},
                           result={"test": headline, "split": {k: sp[k] for k in (
                               "train_end", "val_end", "train_period", "val_period", "test_period",
                               "train_samples", "val_samples", "test_samples", "budget", "gap")}})
        jobs.append_event(run_id, state=RunState.SUCCEEDED, stage="save", message_code="RUN_SUCCEEDED")
        write("SUCCEEDED")
        return 0
    except Canceled:
        write("CANCELED")
        jobs.finish(run_id, RunState.CANCELED, stage=state["stage"], message_code="RUN_CANCELED",
                    error_code="CANCELED", error_message_ko="사용자 요청으로 취소했습니다")
        return 0
    except PipelineError as e:
        write(f"FAILED {e.code}: {e.message_ko}")
        jobs.finish(run_id, RunState.FAILED, stage=state["stage"], message_code="RUN_FAILED",
                    error_code=e.code, error_message_ko=e.message_ko)
        return 1
    except Exception:  # noqa: BLE001 - 원문은 로컬 로그에만
        write("FAILED INTERNAL\n" + traceback.format_exc())
        try:
            jobs.finish(run_id, RunState.FAILED, stage=state["stage"], message_code="RUN_FAILED",
                        error_code="INTERNAL_ERROR",
                        error_message_ko="학습 중 예상하지 못한 오류가 났습니다. 실행 폴더의 train.log를 확인해 주세요")
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        hb.stop.set()
        log_file.close()


def _file_sha(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m mlworkbench.runner <workspace> <run_id>", file=sys.stderr)
        return 2
    _lower_priority()
    return execute(argv[0], argv[1])
