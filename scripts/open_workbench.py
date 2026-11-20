"""Start the local Workbench and open a frameless Windows window.

서버 수명은 앱 창이 가진다.
- 창을 열 때 서버를 새로 띄우고 PID를 workspace/edge-app/app-instance.json에 기록한다.
- 창을 닫으면(닫기 버튼·Alt+F4·오류 종료 포함) 그 서버와 하위 분석 프로세스를 트리째 종료한다.
- 실행기가 강제 종료되어 서버가 남았거나, 이전 실행기가 띄운 서버가 같은 포트에 남아 있으면
  다음 실행 때 먼저 정리하고 새로 띄운다(항상 현재 코드로 실행).
- 창이 이미 열려 있으면 두 번째 실행은 알리고 끝난다(첫 창의 서버를 끄지 않도록).
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = Path(os.environ.get("MLWB_APP_WORKSPACE") or ROOT / "workspace" / "edge-app")
DIST = ROOT / "frontend" / "dist"
PORT = int(os.environ.get("MLWB_APP_PORT") or 8765)
URL = f"http://127.0.0.1:{PORT}"
HTTP = build_opener(ProxyHandler({}))
STATE = WORKSPACE / "app-instance.json"
WINDOWS = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AlreadyOpen(Exception):
    pass


def is_ready() -> bool:
    try:
        with HTTP.open(f"{URL}/api/health", timeout=0.5) as response:
            if json.load(response).get("status") != "ok":
                return False
        with HTTP.open(URL, timeout=0.5) as response:
            return b"<title>ML Workbench</title>" in response.read(8192)
    except (URLError, OSError, ValueError, AttributeError):
        return False


# --- 프로세스 확인·종료 -----------------------------------------------------------
def _is_python_process(pid: int) -> bool:
    """PID가 살아 있고 Python 프로세스인지(다른 프로그램이 PID를 재사용한 경우는 건드리지 않음)."""
    if not pid or pid <= 0:
        return False
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, creationflags=NO_WINDOW).stdout.lower()
        return '"python' in out
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        return b"python" in Path(f"/proc/{pid}/cmdline").read_bytes().lower()
    except OSError:
        return True


def _kill_tree(pid: int) -> None:
    """서버와 그 하위 프로세스(분석 프로세스 풀 포함)를 종료한다."""
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        return
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    for sig, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)


def _listening_pid(port: int) -> int | None:
    """포트를 쓰는 프로세스 PID(Windows: netstat). 다른 OS에서는 확인하지 않는다."""
    if not WINDOWS:
        return None
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    for line in out.splitlines():
        m = re.match(rf"\s*TCP\s+127\.0\.0\.1:{port}\s+\S+\s+LISTENING\s+(\d+)", line)
        if m:
            return int(m.group(1))
    return None


def _wait_port_free(timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_ready():
            return True
        time.sleep(0.2)
    return False


def _read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stop_server(pid: int | None) -> None:
    if pid and _is_python_process(pid):
        _kill_tree(pid)
    try:
        STATE.unlink()
    except OSError:
        pass  # 삭제가 막힌 위치에서는 다음 실행 때 PID 확인으로 처리


def cleanup_previous() -> None:
    """이전 실행이 남긴 서버를 정리한다. 다른 창이 열려 있으면 AlreadyOpen."""
    state = _read_state()
    launcher = state.get("launcher_pid")
    if launcher and launcher != os.getpid() and _is_python_process(launcher):
        raise AlreadyOpen("ML Workbench 창이 이미 열려 있습니다.")
    if state.get("server_pid"):
        stop_server(state["server_pid"])
    if is_ready():
        # 기록 없이 같은 포트에 남은 워크벤치 서버(이전 버전 실행기 등): 포트 소유 PID로 정리
        pid = _listening_pid(PORT)
        if pid and _is_python_process(pid):
            _kill_tree(pid)
    if not _wait_port_free():
        raise RuntimeError(f"{PORT} 포트의 이전 서버를 종료하지 못했습니다. 작업 관리자에서 python 프로세스를 확인해 주세요.")


def start_server() -> subprocess.Popen:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    log_path = WORKSPACE / "server.log"
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), MLWB_FRONTEND_DIST=str(DIST))
    command = [
        sys.executable, "-B", "-m", "mlworkbench", "serve",
        "--workspace", str(WORKSPACE), "--port", str(PORT),
    ]
    group = ({"creationflags": NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS
             else {"start_new_session": True})  # 종료 시 하위 프로세스까지 한 번에 정리
    with log_path.open("ab") as log:
        server = subprocess.Popen(
            command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **group,
        )
    STATE.write_text(json.dumps({"launcher_pid": os.getpid(), "server_pid": server.pid, "port": PORT}),
                     encoding="utf-8")
    deadline = time.monotonic() + 15
    while server.poll() is None and time.monotonic() < deadline:
        if is_ready():
            return server
        time.sleep(0.25)
    stop_server(server.pid)
    raise RuntimeError(f"로컬 서버를 시작할 수 없습니다. 로그: {log_path}")


class WindowControls:
    def __init__(self) -> None:
        self._maximized = False

    def _on_maximized(self) -> None:
        self._maximized = True

    def _on_restored(self) -> None:
        self._maximized = False

    def minimize(self) -> None:
        self._window.minimize()

    def toggle_maximized(self) -> bool:
        maximize = not self._maximized
        if maximize:
            self._window.maximize()
        else:
            self._window.restore()
        self._maximized = maximize
        return maximize

    def close(self) -> None:
        self._window.destroy()


def main() -> None:
    if not (DIST / "index.html").is_file():
        raise RuntimeError("웹 빌드가 없습니다. frontend에서 npm run build를 실행해 주세요.")
    try:
        import webview
    except ModuleNotFoundError as error:
        raise RuntimeError("앱 창을 열려면 프로젝트 가상환경에 pywebview를 설치해 주세요.") from error
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    cleanup_previous()
    server = start_server()
    try:
        controls = WindowControls()
        window = webview.create_window(
            "ML Workbench", URL, js_api=controls, frameless=True, easy_drag=False, shadow=True,
            width=1380, height=900, min_size=(440, 480), background_color="#105651",
        )
        controls._window = window
        window.events.maximized += controls._on_maximized
        window.events.restored += controls._on_restored
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.start(gui="edgechromium")  # 창이 닫힐 때까지 대기
    finally:
        stop_server(server.pid)


if __name__ == "__main__":
    try:
        main()
    except AlreadyOpen as notice:
        if sys.stderr is not None:
            print(notice, file=sys.stderr)
        else:
            from ctypes import windll

            windll.user32.MessageBoxW(None, str(notice), "ML Workbench", 0x40)
        sys.exit(0)
    except Exception as error:
        if sys.stderr is not None:
            print(error, file=sys.stderr)
        else:
            from ctypes import windll

            windll.user32.MessageBoxW(None, str(error), "ML Workbench", 0x10)
        sys.exit(1)
