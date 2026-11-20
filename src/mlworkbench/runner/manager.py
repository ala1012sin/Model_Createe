"""서버 쪽 worker 관리.

- start(run_id): 실행당 worker 하나. experiments/<run_id>/worker.lock 을 O_EXCL로 만들어 중복 시작을 막는다
  (두 번 클릭·재전송·동시 요청에서도 하나). 시작 실패 시 FAILED(WORKER_START_FAILED)로 남긴다.
- reap(): 끝난 프로세스와 실행 기록을 대조한다. 종료 상태를 남기지 못하고 끝난 worker는 FAILED(WORKER_EXITED).
  취소 요청 후 응답이 없으면 프로세스를 끝내고 CANCELED.
- dispatch(): 대기열(P2, docs/P2_QUEUE_BRIEF.md). worker가 있는 미종료 실행이 max_parallel(기본 1)보다 적을 때만
  worker 없는 QUEUED 실행을 제출 순서대로 시작한다. 제출 API·주기 대조·앱 시작 때 호출한다.
- cancel_waiting(): 아직 worker가 없는(대기 중) 실행의 취소 요청은 바로 CANCELED.
- recover(): 앱 시작 시 끝나지 않은 실행을 정리한다. heartbeat가 멈춘 실행은 FAILED(WORKER_LOST)
  (취소 요청 중이었다면 CANCELED). 시작 도중 끝난 실행(lock만 있음)은 FAILED(WORKER_NOT_STARTED).
  worker를 기다리던 QUEUED 실행은 대기열에 그대로 둔다.
- shutdown(): 앱 종료 시 이 서버가 띄운 worker를 끝내고 FAILED(APP_SHUTDOWN)로 남긴다. 대기 실행은 그대로.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from mlworkbench.core.contracts import TERMINAL_RUN_STATES, RunState, utcnow_iso
from mlworkbench.runner.worker import HEARTBEAT

log = logging.getLogger("mlworkbench.runner")
HEARTBEAT_STALE_S = 15.0
CANCEL_GRACE_S = 20.0
_FAIL_MSG = {
    "WORKER_START_FAILED": "학습 프로세스를 시작하지 못했습니다",
    "WORKER_EXITED": "학습 프로세스가 결과를 남기지 못하고 종료되었습니다",
    "WORKER_LOST": "앱이 다시 시작되기 전에 학습 프로세스가 중단되었습니다",
    "WORKER_NOT_STARTED": "학습 프로세스가 시작되지 않은 채 앱이 종료되었습니다",
    "APP_SHUTDOWN": "앱을 종료해 학습을 중단했습니다",
}


def pid_alive(pid: int) -> bool:
    """프로세스가 살아 있는지(Windows·POSIX). 확인할 수 없으면 True(보수적으로 살아 있다고 본다)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            k = ctypes.WinDLL("kernel32", use_last_error=True)
            # 64비트에서 HANDLE이 int(32비트)로 잘리지 않도록 형식을 명시한다
            k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k.OpenProcess.restype = wintypes.HANDLE
            k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            k.GetExitCodeProcess.restype = wintypes.BOOL
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            k.CloseHandle.restype = wintypes.BOOL
            h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return ctypes.get_last_error() == 5  # 접근 거부 = 살아 있지만 권한 없음
            try:
                code = wintypes.DWORD()
                ok = k.GetExitCodeProcess(h, ctypes.byref(code))
                return bool(ok) and code.value == 259  # STILL_ACTIVE
            finally:
                k.CloseHandle(h)
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as f:  # 회수 전 좀비는 종료된 것으로 본다
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def _src_root() -> str:
    import mlworkbench

    return str(Path(mlworkbench.__file__).resolve().parents[1])


def max_parallel_runs() -> int:
    try:
        n = int(os.environ.get("MLWB_MAX_PARALLEL_RUNS") or 1)
    except ValueError:
        n = 1
    return min(max(n, 1), 4)


class RunManager:
    def __init__(self, ws, jobs, *, max_parallel: int | None = None):
        self.ws, self.jobs = ws, jobs
        self.max_parallel = max_parallel or max_parallel_runs()
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()
        self.queue_lock = threading.Lock()  # dispatch·대기 취소를 한 번에 하나씩

    def _has_worker(self, run_id: str) -> bool:
        return (self.ws.root / "experiments" / run_id / "worker.lock").exists()

    # 대기열 -----------------------------------------------------------------
    def queue_state(self) -> tuple[list[str], list[str]]:
        """(worker가 있는 미종료 실행, 기다리는 QUEUED 실행 — 제출 순)."""
        active, waiting = [], []
        # list_runs는 최신순(같은 초는 등록 역순) → 뒤집으면 제출 순서. created_at은 초 단위라 run_id로 정렬하면 안 됨
        for rec in reversed(self.jobs.list_runs()):
            if rec.state in TERMINAL_RUN_STATES:
                continue
            if self._has_worker(rec.run_id):
                active.append(rec.run_id)
            elif rec.state == RunState.QUEUED:
                waiting.append(rec.run_id)
        return active, waiting

    def dispatch(self) -> list[str]:
        """빈 자리만큼 대기 실행을 시작한다. 시작한 run_id 목록."""
        started = []
        with self.queue_lock:
            active, waiting = self.queue_state()
            for run_id in waiting[: max(self.max_parallel - len(active), 0)]:
                if self.jobs.get_status(run_id).state != RunState.QUEUED:
                    continue
                if self.start(run_id):
                    started.append(run_id)
        return started

    def cancel_waiting(self, run_id: str) -> bool:
        """취소 요청된 실행에 아직 worker가 없으면 바로 CANCELED. 처리했으면 True."""
        with self.queue_lock:
            if self._has_worker(run_id):
                return False
            status = self.jobs.get_status(run_id)
            if status.state != RunState.CANCEL_REQUESTED:
                return False
            return bool(self.jobs.finish(run_id, RunState.CANCELED, stage="queued", message_code="RUN_CANCELED",
                                         error_code="CANCELED", error_message_ko="대기 중에 취소했습니다"))

    def _dir(self, run_id: str) -> Path:
        d = self.ws.root / "experiments" / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # 시작 -------------------------------------------------------------------
    def start(self, run_id: str) -> bool:
        """worker를 시작했으면 True, 이미 시작되어 있었으면 False."""
        d = self._dir(run_id)
        try:
            fd = os.open(d / "worker.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.close(fd)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([_src_root()] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # worker는 작업 공간에서 시작하므로 사용자 플러그인 위치를 절대경로로 고정해 같은 카탈로그를 쓰게 한다
        from mlworkbench.plugins.user_loader import plugin_dirs

        env["MLWB_PLUGIN_DIRS"] = os.pathsep.join(str(p) for p in plugin_dirs())
        kw: dict = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: 앱 창 위에 콘솔이 뜨지 않게
        try:
            with (d / "worker.stderr.log").open("ab") as err:
                proc = subprocess.Popen([sys.executable, "-m", "mlworkbench.runner", str(self.ws.root), run_id],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                        cwd=str(self.ws.root), env=env, **kw)
        except OSError:
            log.error("worker 시작 실패: %s", run_id)
            self.jobs.finish(run_id, RunState.FAILED, stage="queued", message_code="RUN_FAILED",
                             error_code="WORKER_START_FAILED", error_message_ko=_FAIL_MSG["WORKER_START_FAILED"])
            return False
        (d / "worker.json").write_text(json.dumps({"pid": proc.pid, "started_at": utcnow_iso()}), encoding="utf-8")
        with self.lock:
            self.procs[run_id] = proc
        return True

    # 대조 -------------------------------------------------------------------
    def _heartbeat_age(self, run_id: str) -> float | None:
        p = self.ws.root / "experiments" / run_id / HEARTBEAT
        try:
            return time.time() - float(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def reap(self) -> None:
        with self.lock:
            items = list(self.procs.items())
        for run_id, proc in items:
            code = proc.poll()
            try:
                status = self.jobs.get_status(run_id)
            except Exception:  # noqa: BLE001
                continue
            if code is not None:
                with self.lock:
                    self.procs.pop(run_id, None)
                if status.state not in TERMINAL_RUN_STATES:
                    cancel = status.state == RunState.CANCEL_REQUESTED
                    self.jobs.finish(run_id, RunState.CANCELED if cancel else RunState.FAILED, stage=status.stage,
                                     message_code="RUN_CANCELED" if cancel else "RUN_FAILED",
                                     error_code="CANCELED" if cancel else "WORKER_EXITED",
                                     error_message_ko="사용자 요청으로 취소했습니다" if cancel else _FAIL_MSG["WORKER_EXITED"])
            elif status.state == RunState.CANCEL_REQUESTED:
                ev = status.events[-1] if status.events else None
                if ev and _age(ev.occurred_at) > CANCEL_GRACE_S:  # worker가 취소에 응답하지 않음
                    self._stop(proc)

    @staticmethod
    def _stop(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # 앱 시작·종료 -------------------------------------------------------------
    def recover(self) -> list[str]:
        fixed = []
        for rec in self.jobs.list_runs():
            if rec.state in TERMINAL_RUN_STATES:
                continue
            if self.alive(rec.run_id):
                continue
            d = self.ws.root / "experiments" / rec.run_id
            started = (d / "worker.json").exists()
            if started and self._worker_running(rec.run_id):
                continue  # 다른 서버 인스턴스의 worker가 아직 실행 중
            if rec.state == RunState.QUEUED and not started and not (d / "worker.lock").exists():
                continue  # 대기열에서 기다리던 실행 — 그대로 두고 dispatch()가 순서대로 시작
            cancel = rec.state == RunState.CANCEL_REQUESTED
            code = "CANCELED" if cancel else ("WORKER_LOST" if started else "WORKER_NOT_STARTED")
            status = self.jobs.get_status(rec.run_id)
            if self.jobs.finish(rec.run_id, RunState.CANCELED if cancel else RunState.FAILED, stage=status.stage,
                                message_code="RUN_CANCELED" if cancel else "RUN_FAILED", error_code=code,
                                error_message_ko="사용자 요청으로 취소했습니다" if cancel else _FAIL_MSG[code]):
                fixed.append(rec.run_id)
        return fixed

    def _worker_running(self, run_id: str) -> bool:
        """heartbeat가 최근이고 기록된 worker 프로세스가 살아 있으면 실행 중."""
        age = self._heartbeat_age(run_id)
        try:
            pid = int(json.loads((self.ws.root / "experiments" / run_id / "worker.json").read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            pid = -1
        if pid > 0 and not pid_alive(pid):
            return False
        return age is not None and age < HEARTBEAT_STALE_S

    def scan_orphans(self) -> list[str]:
        """이 서버가 띄우지 않은(이전 앱의) worker로 남은 실행 중 죽은 것을 정리. QUEUED 직후는 건너뜀."""
        out = []
        for rec in self.jobs.list_runs():
            if rec.state in TERMINAL_RUN_STATES or self.alive(rec.run_id):
                continue
            if not (self.ws.root / "experiments" / rec.run_id / "worker.json").exists():
                continue  # 시작 전(대기) — 앱 재시작 때 recover()가 정리
            if not self._worker_running(rec.run_id):
                out += self.recover_one(rec)
        return out

    def recover_one(self, rec) -> list[str]:
        cancel = rec.state == RunState.CANCEL_REQUESTED
        status = self.jobs.get_status(rec.run_id)
        if self.jobs.finish(rec.run_id, RunState.CANCELED if cancel else RunState.FAILED, stage=status.stage,
                            message_code="RUN_CANCELED" if cancel else "RUN_FAILED",
                            error_code="CANCELED" if cancel else "WORKER_LOST",
                            error_message_ko="사용자 요청으로 취소했습니다" if cancel else _FAIL_MSG["WORKER_LOST"]):
            return [rec.run_id]
        return []

    def shutdown(self) -> None:
        with self.lock:
            items = list(self.procs.items())
            self.procs.clear()
        for run_id, proc in items:
            if proc.poll() is None:
                self._stop(proc)
            try:
                status = self.jobs.get_status(run_id)
                if status.state not in TERMINAL_RUN_STATES:
                    cancel = status.state == RunState.CANCEL_REQUESTED
                    self.jobs.finish(run_id, RunState.CANCELED if cancel else RunState.FAILED, stage=status.stage,
                                     message_code="RUN_CANCELED" if cancel else "RUN_FAILED",
                                     error_code="CANCELED" if cancel else "APP_SHUTDOWN",
                                     error_message_ko="사용자 요청으로 취소했습니다" if cancel else _FAIL_MSG["APP_SHUTDOWN"])
            except Exception:  # noqa: BLE001
                log.error("종료 중 실행 상태 정리 실패: %s", run_id)

    def alive(self, run_id: str) -> bool:
        with self.lock:
            p = self.procs.get(run_id)
        return p is not None and p.poll() is None


def _age(iso: str) -> float:
    from datetime import datetime, timezone

    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return time.time() - t.timestamp()
    except ValueError:
        return 0.0
