"""실행·데이터셋 삭제 (ADD-UX-02·07, docs/ADD_UX_BRIEF.md 2차).

규칙(사용자 결정 2026-11-20):
- 실행 삭제: 끝난 실행(완료·실패·취소)만. 모델·예측·로그 폴더와 기록, **그 모델로 만든 새 데이터 적용 기록도 함께**
  (적용 기록이 있으면 확인 체크 필요). 예측 중인 적용 기록이 있으면 막는다. 승인 기록·확정 스냅샷은 남긴다(같은 설정으로 다시 확정 가능).
- 데이터셋 삭제: 진행·대기 중 학습이 있거나 분석 중이면 막는다. 학습 실행이 있으면 '실행 N개·모델·예측도 함께 삭제' 체크 필요.
  데이터 파일·분석 결과·추천·초안·승인·확정 스냅샷·실행(위 규칙)·적용 기록을 모두 지운다.
- 기록을 먼저 지우고(한 트랜잭션) 파일을 지운다. 파일을 못 지우면(Windows 파일 잠금 등) 남은 폴더 이름을 알려 준다.
- workspace/deletions.log에 무엇을 몇 개 지웠는지 남긴다(경로·데이터 값 없음).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any

from mlworkbench.core.contracts import TERMINAL_RUN_STATES, DatasetState, RunState, utcnow_iso
from mlworkbench.core.errors import NotFound, WorkbenchError

ACTIVE_RUN = {RunState.QUEUED, RunState.RUNNING, RunState.CANCEL_REQUESTED}
BUSY_DATASET = {DatasetState.UPLOADING, DatasetState.PROFILING}


class DeleteRejected(WorkbenchError):
    pass


def _dir_size(p: Path) -> int:
    total = 0
    if p.is_dir():
        for f in p.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    return total


def _rmtree(p: Path) -> bool:
    """폴더 삭제. 읽기 전용 파일은 쓰기 가능으로 바꿔 다시 시도. 성공하면 True."""
    if not p.exists():
        return True

    def onerror(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass

    for _ in range(3):
        shutil.rmtree(p, onerror=onerror)
        if not p.exists():
            return True
        time.sleep(0.2)
    return not p.exists()


class DeletionService:
    def __init__(self, ws, jobs, datasets):
        self.ws, self.jobs, self.datasets = ws, jobs, datasets

    # 공통 ---------------------------------------------------------------------
    def _apps_of(self, conn, run_ids: list[str]) -> list[tuple[str, str]]:
        if not run_ids:
            return []
        q = ",".join("?" * len(run_ids))
        return [(r["application_id"], r["state"]) for r in
                conn.execute(f"SELECT application_id, state FROM applications WHERE run_id IN ({q})", run_ids)]

    def _log(self, kind: str, ident: str, counts: dict[str, Any], left: list[str]) -> None:
        line = {"at": utcnow_iso(), "kind": kind, "id": ident, **counts, "files_left": left}
        try:
            with (self.ws.root / "deletions.log").open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _remove_dirs(self, dirs: list[Path]) -> list[str]:
        left = []
        for d in dirs:
            if not _rmtree(d):
                left.append(f"{d.parent.name}/{d.name}")
        return left

    # 실행 ---------------------------------------------------------------------
    def run_preview(self, run_id: str) -> dict[str, Any]:
        rec = self.jobs.get_record(run_id)
        state = self.jobs.get_status(run_id).state
        with self.ws.session() as conn:
            apps = self._apps_of(conn, [run_id])
        reason = None
        if state in ACTIVE_RUN:
            reason = "진행·대기 중인 실행은 삭제할 수 없습니다. 먼저 취소해 주세요"
        elif any(s == "RUNNING" for _, s in apps):
            reason = "이 모델로 예측 중인 새 데이터 적용이 있습니다. 끝난 뒤 삭제해 주세요"
        exp = self.ws.root / "experiments" / run_id
        return {"run_id": run_id, "name": rec.name, "state": state.value, "deletable": reason is None,
                "reason_ko": reason, "applications": len(apps),
                "bytes": _dir_size(exp) + sum(_dir_size(self.ws.root / "applications" / a) for a, _ in apps)}

    def delete_run(self, run_id: str, *, confirmed: bool, include_applications: bool) -> dict[str, Any]:
        if not confirmed:
            raise DeleteRejected("삭제 확인이 필요합니다", code="CONFIRMATION_REQUIRED")
        pv = self.run_preview(run_id)
        if not pv["deletable"]:
            raise DeleteRejected(pv["reason_ko"], code="RUN_ACTIVE" if pv["state"] in {s.value for s in ACTIVE_RUN} else "APPLICATION_RUNNING")
        if pv["applications"] and not include_applications:
            raise DeleteRejected(f"이 모델로 만든 새 데이터 적용 기록 {pv['applications']}개도 함께 삭제됩니다. 확인 체크가 필요합니다",
                                 code="CONFIRM_APPLICATIONS")
        apps = self._delete_run_records([run_id])
        left = self._remove_dirs([self.ws.root / "experiments" / run_id]
                                 + [self.ws.root / "applications" / a for a in apps])
        out = {"deleted_run": run_id, "applications_deleted": len(apps), "files_left": left}
        self._log("run", run_id, {"applications": len(apps)}, left)
        return out

    def _delete_run_records(self, run_ids: list[str], conn=None) -> list[str]:
        """실행·이벤트·취소 확인·적용 기록 행 삭제. 지운 적용 기록 id 목록."""
        own = conn is None
        conn = conn or self.ws.connect()
        try:
            if own:
                conn.execute("BEGIN IMMEDIATE")
            for rid in run_ids:  # 다른 요청이 그 사이에 시작시켰는지 다시 확인
                row = conn.execute("SELECT state FROM runs WHERE run_id=?", (rid,)).fetchone()
                if row is None:
                    raise NotFound(f"실행을 찾을 수 없습니다: {rid}")
                if RunState(row["state"]) not in TERMINAL_RUN_STATES:
                    raise DeleteRejected("진행·대기 중인 실행은 삭제할 수 없습니다", code="RUN_ACTIVE")
            apps = [a for a, _ in self._apps_of(conn, run_ids)]
            for rid in run_ids:
                conn.execute("DELETE FROM run_events WHERE run_id=?", (rid,))
                conn.execute("DELETE FROM confirmations WHERE run_id=?", (rid,))
                conn.execute("DELETE FROM applications WHERE run_id=?", (rid,))
                conn.execute("DELETE FROM runs WHERE run_id=?", (rid,))
            if own:
                conn.execute("COMMIT")
            return apps
        except Exception:
            if own and conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if own:
                conn.close()

    # 데이터셋 -------------------------------------------------------------------
    def _dataset_runs(self, dataset_id: str):
        return [r for r in self.jobs.list_runs() if r.dataset_id == dataset_id]

    def dataset_preview(self, dataset_id: str) -> dict[str, Any]:
        m = self.datasets.get(dataset_id)
        runs = self._dataset_runs(dataset_id)
        active = [r for r in runs if r.state in ACTIVE_RUN]
        with self.ws.session() as conn:
            apps = self._apps_of(conn, [r.run_id for r in runs])
        reason = None
        if m.state in BUSY_DATASET:
            reason = "업로드·분석 중에는 삭제할 수 없습니다. 끝난 뒤 다시 시도해 주세요"
        elif active:
            reason = f"진행·대기 중인 학습 {len(active)}개가 있습니다. 먼저 취소하거나 끝난 뒤 삭제해 주세요"
        elif any(s == "RUNNING" for _, s in apps):
            reason = "이 데이터셋의 모델로 예측 중인 새 데이터 적용이 있습니다. 끝난 뒤 삭제해 주세요"
        size = _dir_size(self.ws.root / "datasets" / dataset_id) + sum(
            _dir_size(self.ws.root / "experiments" / r.run_id) for r in runs) + sum(
            _dir_size(self.ws.root / "applications" / a) for a, _ in apps)
        return {"dataset_id": dataset_id, "name": m.name, "state": m.state.value, "deletable": reason is None,
                "reason_ko": reason, "runs": len(runs), "active_runs": len(active), "applications": len(apps),
                "bytes": size}

    def delete_dataset(self, dataset_id: str, *, confirmed: bool, include_runs: bool) -> dict[str, Any]:
        if not confirmed:
            raise DeleteRejected("삭제 확인이 필요합니다", code="CONFIRMATION_REQUIRED")
        pv = self.dataset_preview(dataset_id)
        if not pv["deletable"]:
            raise DeleteRejected(pv["reason_ko"], code="DATASET_BUSY")
        if pv["runs"] and not include_runs:
            raise DeleteRejected(f"학습 실행 {pv['runs']}개와 모델·예측 결과도 함께 삭제됩니다. 확인 체크가 필요합니다",
                                 code="CONFIRM_RUNS")
        run_ids = [r.run_id for r in self._dataset_runs(dataset_id)]
        conn = self.ws.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
            if row is None:
                raise NotFound(f"데이터셋을 찾을 수 없습니다: {dataset_id}")
            if DatasetState(row["state"]) in BUSY_DATASET:
                raise DeleteRejected("업로드·분석 중에는 삭제할 수 없습니다", code="DATASET_BUSY")
            apps = self._delete_run_records(run_ids, conn)
            plan_ids = [r["plan_id"] for r in conn.execute("SELECT plan_id FROM approvals WHERE dataset_id=?", (dataset_id,))]
            draft_ids = [r["draft_id"] for r in conn.execute(
                "SELECT DISTINCT draft_id FROM drafts WHERE json_extract(body_json, '$.dataset_id') = ?", (dataset_id,))]
            conn.execute("DELETE FROM approvals WHERE dataset_id=?", (dataset_id,))
            for d in draft_ids:
                conn.execute("DELETE FROM drafts WHERE draft_id=?", (d,))
            conn.execute("DELETE FROM recommendations WHERE dataset_id=?", (dataset_id,))
            conn.execute("DELETE FROM profiles WHERE dataset_id=?", (dataset_id,))
            conn.execute("DELETE FROM datasets WHERE dataset_id=?", (dataset_id,))
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        dirs = ([self.ws.root / "datasets" / dataset_id]
                + [self.ws.root / "experiments" / r for r in run_ids]
                + [self.ws.root / "applications" / a for a in apps]
                + [self.ws.root / "plans" / p for p in plan_ids])
        left = self._remove_dirs(dirs)
        out = {"deleted_dataset": dataset_id, "runs_deleted": len(run_ids), "applications_deleted": len(apps),
               "plans_deleted": len(plan_ids), "drafts_deleted": len(draft_ids), "files_left": left}
        self._log("dataset", dataset_id, {k: v for k, v in out.items() if k.endswith("_deleted") and k != "deleted_dataset"}, left)
        return out
