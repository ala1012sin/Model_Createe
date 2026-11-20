"""S7 새 데이터 적용: 완료된 실행의 저장 모델로 새 CSV를 예측한다(학습 없음).

적용 기록(applications 표) = 원본 실행(run_id·plan_hash) × 입력 파일(내용 해시) 하나(XLSX는 + 시트).
상태: SHEET_REQUIRED(XLSX 시트 선택 대기) / CHECK_FAILED / CHECKED → RUNNING → SUCCEEDED / FAILED.
- XLSX는 원본(input.xlsx)을 보관하고, 고른 시트를 학습 때와 같은 규칙(xlsx-table-v1)으로 input.csv(표)로 정규화해 점검·예측한다.
  점검 뒤 다른 시트를 고르면 새 적용 기록을 만든다.
- 모델이나 입력 파일을 바꾸면 새 적용 기록을 만든다(이전 점검·결과는 그대로 남음).
- 예측 직전에 입력 파일·모델 파일 해시가 점검 때와 같은지 다시 확인한다.
- 원본 실행 폴더와 입력 파일은 바꾸지 않는다. 결과는 applications/<id>/predictions.csv.
- 결과 파일은 임시 이름으로 쓴 뒤 바꿔 넣고, 상태가 SUCCEEDED일 때만 내려받을 수 있다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import IO, Any

from mlworkbench.core.contracts import RunState, utcnow_iso
from mlworkbench.core.errors import NotFound, StateTransitionError, WorkbenchError
from mlworkbench.core.registry import PluginRegistry
from mlworkbench.storage.database import Workspace

log = logging.getLogger(__name__)
CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
XLSX_SUFFIXES = {".xlsx"}
_CHUNK = 1 << 20
PREVIEW_ROWS = 20


class InferenceRejected(WorkbenchError):
    code = "INFERENCE_REJECTED"


def _sha256_files(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        with p.open("rb") as f:
            while chunk := f.read(_CHUNK):
                h.update(chunk)
    return "sha256:" + h.hexdigest()


class InferenceService:
    def __init__(self, ws: Workspace, registry: PluginRegistry, jobs, *, max_upload_bytes: int):
        self.ws, self.registry, self.jobs, self.max_upload_bytes = ws, registry, jobs, max_upload_bytes

    # 모델 목록 ------------------------------------------------------------------
    def _exp(self, run_id: str) -> Path:
        return self.ws.root / "experiments" / run_id

    def _model_files(self, run_id: str) -> list[Path]:
        d = self._exp(run_id) / "model"
        return [p for p in d.iterdir() if p.is_file()] if d.exists() else []

    def models(self) -> list[dict[str, Any]]:
        out = []
        for rec in self.jobs.list_runs():
            if rec.state != RunState.SUCCEEDED or not (self._exp(rec.run_id) / "model" / "pipeline.json").exists():
                continue
            out.append(self._model_info(rec))
        return out

    def _model_info(self, rec) -> dict[str, Any]:
        try:
            doc = json.loads((self._exp(rec.run_id) / "model" / "pipeline.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        from mlworkbench.app.services import DatasetService  # 순환 import 방지

        ds = DatasetService(self.ws).find(rec.dataset_id)
        return {"run_id": rec.run_id, "name": rec.name, "plan_hash": rec.plan_hash, "dataset_id": rec.dataset_id,
                "dataset_name": ds.name if ds else None, "finished_at": rec.finished_at,
                "model": (doc.get("model") or {}).get("plugin_id"), "target": (doc.get("goal") or {}).get("target_col"),
                "horizons": list((doc.get("model_files") or {}).keys()),
                "test": (rec.result or {}).get("test")}

    def _succeeded_run(self, run_id: str):
        rec = self.jobs.get_record(run_id)
        if rec.state != RunState.SUCCEEDED:
            raise InferenceRejected("완료된 실행의 모델만 적용할 수 있습니다 (실패·취소·진행 중 실행은 선택 불가)",
                                    code="RUN_NOT_SUCCEEDED")
        return rec

    def load_fitted(self, run_id: str):
        from mlworkbench.pipelines.timeseries import FittedTimeseries

        return FittedTimeseries.load(self._exp(run_id), self.registry)

    def requirements(self, run_id: str) -> dict[str, Any]:
        rec = self._succeeded_run(run_id)
        from mlworkbench.pipelines.ts_frame import PipelineError

        try:
            req = self.load_fitted(run_id).requirements()
            req["model_error"] = None
        except PipelineError as e:
            req = {"model_error": {"code": e.code, "message_ko": e.message_ko}}
        return {**self._model_info(rec), "requirements": req}

    # 기록 --------------------------------------------------------------------
    def _save(self, rec: dict[str, Any], *, new: bool = False) -> dict[str, Any]:
        rec["updated_at"] = utcnow_iso()
        with self.ws.session() as conn:
            if new:
                conn.execute("INSERT INTO applications VALUES (?,?,?,?,?,?,?)",
                             (rec["application_id"], rec["run_id"], rec["plan_hash"], rec["state"],
                              json.dumps(rec, ensure_ascii=False), rec["created_at"], rec["updated_at"]))
            else:
                conn.execute("UPDATE applications SET state=?, record_json=?, updated_at=? WHERE application_id=?",
                             (rec["state"], json.dumps(rec, ensure_ascii=False), rec["updated_at"], rec["application_id"]))
        return rec

    def get(self, application_id: str) -> dict[str, Any]:
        with self.ws.session() as conn:
            row = conn.execute("SELECT record_json FROM applications WHERE application_id=?", (application_id,)).fetchone()
        if row is None:
            raise NotFound(f"적용 기록을 찾을 수 없습니다: {application_id}")
        return json.loads(row["record_json"])

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.ws.session() as conn:
            rows = conn.execute("SELECT record_json FROM applications ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                (limit,)).fetchall()
        return [json.loads(r["record_json"]) for r in rows]

    def _dir(self, application_id: str) -> Path:
        d = self.ws.root / "applications" / application_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # 입력 등록·점검 --------------------------------------------------------------
    def upload(self, run_id: str, source: IO[bytes], *, display_name: str, sheet: str | None = None) -> dict[str, Any]:
        """새 CSV를 적용 폴더에 복사해 기록을 만들고 바로 점검한다."""
        run = self._succeeded_run(run_id)
        name = Path(display_name or "input.csv").name[:200] or "input.csv"
        app_id = f"app_{secrets.token_hex(6)}"
        d = self._dir(app_id)
        from mlworkbench.datasets import xlsx_table

        suffix = Path(name).suffix.lower()
        kind = "xlsx" if suffix in XLSX_SUFFIXES else "csv"
        rec = {"application_id": app_id, "run_id": run_id, "plan_hash": run.plan_hash, "dataset_id": run.dataset_id,
               "input": {"name": name, "kind": kind, "sha256": None, "size_bytes": 0, "sheet": None, "sheets": None,
                         "table_sha256": None, "normalization": None},
               "state": "CHECK_FAILED", "created_at": utcnow_iso(), "check": None, "result": None, "error": None,
               "model_sha256": None}
        if (msg := xlsx_table.unsupported_message(suffix)) is not None or suffix not in CSV_SUFFIXES | XLSX_SUFFIXES:
            rec["error"] = {"code": "UNSUPPORTED_FILE_TYPE",
                            "message_ko": msg or "CSV(.csv·.tsv·.txt) 또는 엑셀(.xlsx) 파일만 적용할 수 있습니다"}
            return self._save(rec, new=True)
        part, final = d / ".input.part", d / ("input.xlsx" if kind == "xlsx" else "input.csv")
        h, size = hashlib.sha256(), 0
        try:
            with part.open("wb") as out:
                while chunk := source.read(_CHUNK):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise InferenceRejected(f"파일이 너무 큽니다(최대 {self.max_upload_bytes // 1024**2}MB)",
                                                code="FILE_TOO_LARGE")
                    h.update(chunk)
                    out.write(chunk)
            if size == 0:
                raise InferenceRejected("빈 파일입니다", code="EMPTY_FILE")
            os.replace(part, final)
        except InferenceRejected as e:
            _unlink(part)
            rec["error"] = {"code": e.code, "message_ko": e.message_ko}
            return self._save(rec, new=True)
        rec["input"].update(sha256="sha256:" + h.hexdigest(), size_bytes=size)
        if kind == "xlsx":
            try:
                book = xlsx_table.inspect(final)
            except xlsx_table.XlsxError as e:
                rec["error"] = {"code": e.code, "message_ko": e.message_ko}
                return self._save(rec, new=True)
            rec["input"]["sheets"] = book["sheets"]
            rec["state"] = "SHEET_REQUIRED"
            self._save(rec, new=True)
            if sheet:
                return self.select_sheet(app_id, sheet)
            return rec
        rec["input"]["table_sha256"] = rec["input"]["sha256"]
        self._save(rec, new=True)
        return self.check(app_id)

    def select_sheet(self, application_id: str, sheet: str) -> dict[str, Any]:
        """XLSX 입력의 시트 선택 → 정규화 → 점검. 이미 다른 시트로 점검한 기록이면 새 적용 기록을 만든다."""
        rec = self.get(application_id)
        inp = rec["input"]
        if inp.get("kind") != "xlsx":
            raise InferenceRejected("엑셀 입력만 시트를 고를 수 있습니다", code="NOT_XLSX")
        if sheet not in [s["name"] for s in inp.get("sheets") or []]:
            raise NotFound("선택한 시트가 파일에 없습니다")
        if rec["state"] == "RUNNING":
            raise InferenceRejected("예측 중에는 시트를 바꿀 수 없습니다", code="NOT_CHECKED")
        if inp.get("sheet") and inp["sheet"] != sheet:
            src = self._dir(application_id) / "input.xlsx"
            with src.open("rb") as f:
                return self.upload(rec["run_id"], f, display_name=inp["name"], sheet=sheet)
        if inp.get("sheet") == sheet and rec["state"] not in ("SHEET_REQUIRED", "CHECK_FAILED"):
            return rec
        inp["sheet"] = sheet
        self._save(rec)
        return self.check(application_id)

    def rebind(self, application_id: str, run_id: str) -> dict[str, Any]:
        """같은 입력 파일로 다른 모델을 고른다 → 새 적용 기록(이전 기록은 그대로)."""
        old = self.get(application_id)
        xlsx = old["input"].get("kind") == "xlsx"
        src = self._dir(application_id) / ("input.xlsx" if xlsx else "input.csv")
        if not src.exists():
            raise InferenceRejected("입력 파일이 없습니다. 파일을 다시 올려 주세요", code="INPUT_MISSING")
        with src.open("rb") as f:
            return self.upload(run_id, f, display_name=old["input"]["name"], sheet=old["input"].get("sheet") if xlsx else None)

    def check(self, application_id: str) -> dict[str, Any]:
        from mlworkbench.datasets.csv_profile import detect_delimiter, detect_encoding
        from mlworkbench.pipelines.ts_frame import PipelineError

        from mlworkbench.datasets import xlsx_table

        rec = self.get(application_id)
        if rec["input"].get("kind") == "xlsx" and not rec["input"].get("sheet"):
            raise InferenceRejected("점검할 시트를 먼저 골라 주세요", code="SHEET_REQUIRED")
        path = self._dir(application_id) / "input.csv"
        try:
            if rec["input"].get("kind") == "xlsx":
                src = self._dir(application_id) / "input.xlsx"
                if "sha256:" + _file_sha(src) != rec["input"]["sha256"]:
                    raise PipelineError("INPUT_CHANGED", "올린 뒤 엑셀 파일이 바뀌었습니다. 파일을 다시 올려 주세요")
                try:
                    norm = xlsx_table.sheet_to_csv(src, rec["input"]["sheet"], path)
                except xlsx_table.XlsxError as e:
                    raise PipelineError(e.code, e.message_ko) from None
                rec["input"].update(table_sha256=norm["table_sha256"], normalization=norm)
            encoding = detect_encoding(path)
            with path.open("r", encoding=encoding, newline="") as f:
                delimiter = detect_delimiter(f.read(64 * 1024))
            fitted = self.load_fitted(rec["run_id"])
            _, report = fitted.prepare(path, encoding=encoding, delimiter=delimiter)
        except PipelineError as e:
            rec.update(state="CHECK_FAILED", error={"code": e.code, "message_ko": e.message_ko}, check=None)
            return self._save(rec)
        except (UnicodeDecodeError, ValueError, OSError):
            rec.update(state="CHECK_FAILED", check=None,
                       error={"code": "INPUT_UNREADABLE", "message_ko": "CSV를 읽을 수 없습니다. 인코딩·구분자·형식을 확인해 주세요"})
            return self._save(rec)
        report["encoding"], report["delimiter"] = ("utf-8" if encoding == "utf-8-sig" else encoding), delimiter
        rec["check"] = report
        rec["model_sha256"] = _sha256_files(self._model_files(rec["run_id"]))
        if report["samples_predictable"] == 0:
            ex = report["excluded"]
            rec.update(state="CHECK_FAILED", error={
                "code": "NO_PREDICTABLE_ROWS",
                "message_ko": f"예측할 수 있는 행이 없습니다(이력 부족 {ex['history_short']}건, 기준 시각 타깃 없음 "
                              f"{ex['no_current_target']}건). 기준 시각 이전 관측을 더 포함해 주세요"})
        else:
            rec.update(state="CHECKED", error=None)
        return self._save(rec)

    # 예측 --------------------------------------------------------------------
    def begin_predict(self, application_id: str) -> dict[str, Any]:
        rec = self.get(application_id)
        if rec["state"] == "RUNNING":
            return rec  # 이미 진행 중(중복 요청)
        if rec["state"] not in ("CHECKED", "FAILED"):
            raise InferenceRejected("입력 점검을 통과한 적용만 예측할 수 있습니다", code="NOT_CHECKED")
        with self.ws.session() as conn:  # 동시에 두 번 눌러도 하나만 RUNNING
            n = conn.execute("UPDATE applications SET state='RUNNING' WHERE application_id=? AND state IN ('CHECKED','FAILED')",
                             (application_id,)).rowcount
        rec = self.get(application_id)
        if n == 0:
            return rec
        rec.update(state="RUNNING", error=None, result=None, started_at=utcnow_iso(), finished_at=None)
        return self._save(rec)

    def run_predict(self, application_id: str) -> dict[str, Any]:
        """RUNNING 기록을 실제로 예측한다(작업 프로세스·스레드에서 호출)."""
        import pandas as pd

        from mlworkbench.pipelines.ts_frame import NO_GROUP, PipelineError

        rec = self.get(application_id)
        if rec["state"] != "RUNNING":
            raise StateTransitionError("진행 중인 적용이 아닙니다")
        d = self._dir(application_id)
        path = d / "input.csv"
        try:
            inp = rec["input"]
            original = d / ("input.xlsx" if inp.get("kind") == "xlsx" else "input.csv")
            table_sha = inp.get("table_sha256") or inp["sha256"]
            if (not original.exists() or "sha256:" + _file_sha(original) != inp["sha256"]
                    or not path.exists() or "sha256:" + _file_sha(path) != table_sha):
                raise PipelineError("INPUT_CHANGED", "점검 이후 입력 파일이 바뀌었습니다. 파일을 다시 올려 점검해 주세요")
            if _sha256_files(self._model_files(rec["run_id"])) != rec["model_sha256"]:
                raise PipelineError("MODEL_CHANGED", "점검 이후 모델 파일이 바뀌었습니다. 다시 점검해 주세요")
            run = self.jobs.get_record(rec["run_id"])
            if run.plan_hash != rec["plan_hash"] or run.state != RunState.SUCCEEDED:
                raise PipelineError("MODEL_CHANGED", "원본 실행 정보가 바뀌었습니다")
            fitted = self.load_fitted(rec["run_id"])
            chk = rec["check"]
            samples, _ = fitted.prepare(path, encoding=chk["encoding"], delimiter=chk["delimiter"])
            pred = fitted.predict(samples)
            pred.insert(0, "model_plan_hash", rec["plan_hash"])
            pred.insert(0, "model_run_id", rec["run_id"])
            pred["group"] = pred["group"].where(pred["group"] != NO_GROUP, "")
            for c in ("origin_time", "target_time"):
                pred[c] = pd.to_datetime(pred[c]).dt.strftime("%Y-%m-%d %H:%M:%S")
            pred = pred.sort_values(["group", "origin_time", "horizon"], kind="mergesort")
            tmp = d / ".predictions.part"
            pred.to_csv(tmp, index=False, encoding="utf-8")
            os.replace(tmp, d / "predictions.csv")
            last_obs = (chk.get("period") or [None, None])[1]
            rec = self.get(application_id)
            rec.update(state="SUCCEEDED", finished_at=utcnow_iso(), error=None, result={
                "rows": int(len(pred)), "groups": sorted(set(pred["group"])),
                "origin_period": [pred["origin_time"].min(), pred["origin_time"].max()],
                "target_period": [pred["target_time"].min(), pred["target_time"].max()],
                "per_horizon": {h: int(n) for h, n in pred.groupby("horizon", sort=False).size().items()},
                "beyond_last_observation": int((pd.to_datetime(pred["target_time"]) > pd.Timestamp(last_obs)).sum())
                if last_obs else 0,
                "preview": pred.tail(PREVIEW_ROWS).to_dict(orient="records"),
                "columns": list(pred.columns)})
            return self._save(rec)
        except PipelineError as e:
            return self._fail(application_id, e.code, e.message_ko)
        except Exception:  # noqa: BLE001 - 원문은 로그로만
            log.exception("예측 실패: %s", application_id)
            return self._fail(application_id, "INTERNAL_ERROR", "예측 중 예상하지 못한 오류가 났습니다")

    def _fail(self, application_id: str, code: str, message_ko: str) -> dict[str, Any]:
        rec = self.get(application_id)
        _unlink(self._dir(application_id) / ".predictions.part")
        rec.update(state="FAILED", finished_at=utcnow_iso(), error={"code": code, "message_ko": message_ko}, result=None)
        return self._save(rec)

    def recover(self) -> list[str]:
        """앱이 예측 도중 종료됐다면 RUNNING으로 남은 기록을 실패로 정리한다(결과 파일은 내려받기 불가)."""
        out = []
        for rec in self.list(limit=10_000):
            if rec["state"] == "RUNNING":
                self._fail(rec["application_id"], "APP_SHUTDOWN", "앱이 종료되어 예측을 마치지 못했습니다. 다시 실행해 주세요")
                out.append(rec["application_id"])
        return out

    def result_path(self, application_id: str) -> Path:
        rec = self.get(application_id)
        p = self._dir(application_id) / "predictions.csv"
        if rec["state"] != "SUCCEEDED" or not p.exists():
            raise NotFound("완료된 예측 결과가 없습니다")
        return p


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _unlink(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass


def predict_job(ws_root: str, application_id: str) -> None:
    """별도 프로세스에서 실행되는 예측 작업(앱의 분석용 프로세스 풀 재사용)."""
    from mlworkbench.app.services import DatasetService, JobService, PlanService
    from mlworkbench.core.registry import default_registry

    ws = Workspace(ws_root)
    reg = default_registry()
    datasets = DatasetService(ws)
    jobs = JobService(ws, reg, datasets, PlanService(ws, reg, datasets))
    InferenceService(ws, reg, jobs, max_upload_bytes=0).run_predict(application_id)
