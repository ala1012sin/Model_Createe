"""앱 창 실행기(scripts/open_workbench.py) — 창을 닫으면 서버와 하위 프로세스가 함께 종료되는지.

pywebview 대신 '창이 닫힐 때까지 기다리는' 가짜 webview 모듈을 써서 실행기 프로세스 로직을 그대로 실행한다.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "open_workbench.py"
HTTP = build_opener(ProxyHandler({}))
if not (ROOT / "frontend" / "dist" / "index.html").exists():
    pytest.skip("frontend 빌드가 없어 실행기 테스트를 건너뜀", allow_module_level=True)

FAKE_WEBVIEW = '''
import os, time
from pathlib import Path
class _Event:
    def __iadd__(self, handler):
        return self
class _Events:
    maximized = _Event()
    restored = _Event()
class _Window:
    events = _Events()
settings = {}
def create_window(*args, **kwargs):
    return _Window()
def start(**kwargs):
    flag = Path(os.environ["FAKE_WEBVIEW_CLOSE"])
    while not flag.exists():
        time.sleep(0.05)
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # 좀비(종료됐지만 수거 전)는 종료된 것으로 본다
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


def group_members(pgid: int) -> list[int]:
    if os.name == "nt" or not Path("/proc").exists():
        return []
    out = []
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                fields = (p / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == pgid and fields[0] != "Z":
                    out.append(int(p.name))
            except (OSError, IndexError, ValueError):
                pass
    return out


class App:
    def __init__(self, tmp: Path, port: int, name: str):
        self.tmp, self.port, self.close_flag = tmp, port, tmp / f"close-{name}.flag"
        fake = tmp / "fakewebview"
        fake.mkdir(exist_ok=True)
        (fake / "webview.py").write_text(FAKE_WEBVIEW, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(fake), str(ROOT / "src")]), PYTHONIOENCODING="utf-8",
                   MLWB_APP_WORKSPACE=str(tmp / "ws"), MLWB_APP_PORT=str(port), FAKE_WEBVIEW_CLOSE=str(self.close_flag))
        # Windows venv의 python.exe 중간 프로세스 PID와 실제 인터프리터 PID는 다를 수 있다.
        bootstrap = (
            "import os, runpy, sys; print(os.getpid(), flush=True); "
            "runpy.run_path(sys.argv[1], run_name='__main__')"
        )
        self.proc = subprocess.Popen([sys.executable, "-c", bootstrap, str(LAUNCHER)], cwd=ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.launcher_pid = int(self.proc.stdout.readline().strip())

    @property
    def state(self) -> dict:
        return json.loads((self.tmp / "ws" / "app-instance.json").read_text(encoding="utf-8"))

    def wait_ready(self, timeout=40) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise AssertionError(f"실행기 종료: {self.proc.communicate()}")
            try:
                if healthy(self.port) and self.state.get("launcher_pid") == self.launcher_pid:
                    return self.state
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        raise AssertionError("서버가 준비되지 않음")

    def close(self, timeout=30) -> int:
        self.close_flag.write_text("x")
        return self.proc.wait(timeout)


def healthy(port: int) -> bool:
    try:
        with HTTP.open(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as r:
            return json.load(r).get("status") == "ok"
    except OSError:
        return False


def wait_gone(pids, port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if not any(alive(p) for p in pids) and not healthy(port):
            return True
        time.sleep(0.2)
    return False


def analyse_csv(port: int) -> None:
    """분석을 한 번 돌려 분석 프로세스 풀(하위 프로세스)을 띄운다."""
    body = b"time,v\n" + b"".join(f"2026-01-01 00:{m:02d}:00,{m}\n".encode() for m in range(30))
    req = Request(f"http://127.0.0.1:{port}/api/datasets", data=body, method="POST",
                  headers={"X-File-Name": quote("t.csv"), "Content-Type": "application/octet-stream"})
    did = json.load(HTTP.open(req, timeout=10))["dataset_id"]
    end = time.time() + 30
    while time.time() < end:
        st = json.load(HTTP.open(f"http://127.0.0.1:{port}/api/datasets/{did}", timeout=5))["state"]
        if st == "PROFILED":
            return
        time.sleep(0.2)
    raise AssertionError("분석이 끝나지 않음")


def test_closing_window_stops_server_and_children(tmp_path):
    port = free_port()
    app = App(tmp_path, port, "a")
    st = app.wait_ready()
    server = st["server_pid"]
    analyse_csv(port)
    members = group_members(os.getpgid(server)) if os.name != "nt" else []
    if os.name != "nt":
        assert len(members) >= 2, members  # 서버 + 분석 프로세스
    assert app.close() == 0
    assert wait_gone([server, *members], port), "창을 닫은 뒤에도 서버·하위 프로세스가 남음"
    assert not (tmp_path / "ws" / "app-instance.json").exists()


def test_leftover_server_is_replaced_on_next_start(tmp_path):
    port = free_port()
    first = App(tmp_path, port, "a")
    old = first.wait_ready()["server_pid"]
    # 실행기만 강제 종료(창 프로세스가 비정상 종료된 상황) → 서버는 남는다
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(first.launcher_pid), "/F"], capture_output=True)
    else:
        os.kill(first.proc.pid, signal.SIGKILL)
    first.proc.wait(10)
    assert alive(old) and healthy(port)
    second = App(tmp_path, port, "b")
    new = second.wait_ready()["server_pid"]
    assert new != old and wait_gone([old], -1), "남은 서버가 정리되지 않음"
    assert second.close() == 0
    assert wait_gone([new], port)


def test_second_launch_keeps_first_window_server(tmp_path):
    port = free_port()
    first = App(tmp_path, port, "a")
    server = first.wait_ready()["server_pid"]
    second = App(tmp_path, port, "b")
    out, err = second.proc.communicate(timeout=30)
    assert second.proc.returncode == 0 and "이미 열려 있습니다" in err
    assert alive(server) and healthy(port) and first.state["server_pid"] == server
    assert first.close() == 0
    assert wait_gone([server], port)
