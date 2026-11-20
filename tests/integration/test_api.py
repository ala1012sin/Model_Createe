"""FastAPI 연결 테스트 — 업로드·목록·상태·프로파일·재시도·목표 저장·응답 위생."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_csv_dataset import leaky_csv, sensor_csv  # noqa: E402

TERMINAL = {"PROFILED", "PROFILE_FAILED", "UPLOAD_FAILED"}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "ws", serve_frontend=False)
    with TestClient(app) as c:
        c.ws_root = str(tmp_path)
        yield c


def upload(c, name: str, data: bytes, retry_of: str | None = None):
    url = "/api/datasets" + (f"?retry_of={retry_of}" if retry_of else "")
    return c.post(url, content=data, headers={"X-File-Name": quote(name), "Content-Type": "application/octet-stream"})


def wait(c, dataset_id: str, timeout: float = 30) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/datasets/{dataset_id}").json()
        if d["state"] in TERMINAL:
            return d
        time.sleep(0.05)
    raise AssertionError("분석이 끝나지 않음")


def test_upload_two_csvs_and_switch(client):
    a = upload(client, "센서_A.csv", sensor_csv(100))
    b = upload(client, "line2.csv", sensor_csv(40, devices=("X",), header=("time", "dev", "temp", "mode", "memo")))
    assert a.status_code == 201 and b.status_code == 201
    a, b = wait(client, a.json()["dataset_id"]), wait(client, b.json()["dataset_id"])
    assert a["state"] == b["state"] == "PROFILED"
    assert (a["row_count"], a["column_count"]) == (200, 5) and (b["row_count"], b["column_count"]) == (40, 5)
    pa = client.get(f"/api/datasets/{a['dataset_id']}/profile").json()
    pb = client.get(f"/api/datasets/{b['dataset_id']}/profile").json()
    assert [c["name"] for c in pa["columns"]][0] == "timestamp" and [c["name"] for c in pb["columns"]][0] == "time"
    assert pa["time_candidates"][0]["interval"]["label"] == "5min"
    items = client.get("/api/datasets").json()
    assert [i["name"] for i in items["items"]] == ["line2.csv", "센서_A.csv"] and items["server_time"]


def test_reload_reads_saved_results(tmp_path):
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False)) as c:
        d = wait(c, upload(c, "a.csv", sensor_csv(20)).json()["dataset_id"])
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False)) as c2:  # 앱 재시작 = 새로고침 이후 재조회
        again = c2.get(f"/api/datasets/{d['dataset_id']}").json()
        assert again["state"] == "PROFILED" and again["row_count"] == 40
        assert c2.get(f"/api/datasets/{d['dataset_id']}/profile").status_code == 200


def test_failure_then_retry_same_id(client):
    bad = upload(client, "bad.csv", b"time,,v\n1,2,3\n").json()
    bad = wait(client, bad["dataset_id"])
    assert bad["state"] == "PROFILE_FAILED"
    assert bad["error"] == {"code": "HEADER_EMPTY_NAME",
                            "message_ko": "이름이 비어 있는 열이 있습니다. 첫 행에 모든 열 이름을 넣어 주세요"}
    assert client.get(f"/api/datasets/{bad['dataset_id']}/profile").json()["code"] == "PROFILE_NOT_READY"
    fixed = upload(client, "bad_fixed.csv", sensor_csv(10), retry_of=bad["dataset_id"])
    assert fixed.status_code == 201 and fixed.json()["dataset_id"] == bad["dataset_id"]
    done = wait(client, bad["dataset_id"])
    assert done["state"] == "PROFILED" and done["error"] is None and done["name"] == "bad_fixed.csv"


def test_retry_on_successful_dataset_rejected(client):
    ok = wait(client, upload(client, "a.csv", sensor_csv(5)).json()["dataset_id"])
    r = upload(client, "b.csv", sensor_csv(5), retry_of=ok["dataset_id"])
    assert r.status_code == 409 and "message_ko" in r.json()


def test_upload_errors(tmp_path):
    with TestClient(create_app(tmp_path / "ws", max_upload_bytes=1000, serve_frontend=False)) as c:
        big = upload(c, "big.csv", sensor_csv(200))
        assert big.status_code == 201 and big.json()["state"] == "UPLOAD_FAILED"
        assert big.json()["error"]["code"] == "FILE_TOO_LARGE"
        empty = upload(c, "e.csv", b"").json()
        assert empty["state"] == "UPLOAD_FAILED" and empty["error"]["code"] == "EMPTY_FILE"
        xls = upload(c, "a.xls", b"\xd0\xcf\x11\xe0").json()
        assert xls["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
        xlsx = upload(c, "a.xlsx", b"PK\x03\x04").json()  # .xlsx는 지원(P2): 깨진 파일은 형식 오류
        assert xlsx["error"]["code"] == "XLSX_INVALID"
        assert c.get("/api/datasets/ds_nope").status_code == 404


def test_responses_have_no_paths_or_raw_errors(client):
    d = wait(client, upload(client, r"C:\Users\kim\기밀.csv", leaky_csv()).json()["dataset_id"])
    assert d["name"] == "기밀.csv"
    bad = wait(client, upload(client, "q.csv", b'a,b\n1,"x\n').json()["dataset_id"])
    blob = json.dumps([client.get("/api/datasets").json(), d, bad,
                       client.get(f"/api/datasets/{d['dataset_id']}").json()], ensure_ascii=False)
    for forbidden in (client.ws_root, "source.csv", "local_path", "Traceback", "detail", "datasets/"):
        assert forbidden not in blob, forbidden


@pytest.fixture
def thread_client(tmp_path):
    """분석을 같은 프로세스 스레드에서 실행(테스트에서 분석을 붙잡아 두기 위함)."""
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False)) as c:
        yield c


def test_list_and_status_stay_responsive_during_long_profile(thread_client, monkeypatch):
    """분석이 끝나지 않은 동안에도 목록·상태·다른 데이터셋 조회가 바로 응답한다(이벤트 루프 비차단).

    분석 스레드를 게이트로 붙잡아 두어 기기 속도와 무관하게 '분석 중' 구간을 재현한다."""
    import mlworkbench.app.services as services

    gate, entered = threading.Event(), threading.Event()
    real = services.profile_csv

    def slow_profile(*a, **k):
        if k.get("dataset_id") == holder.get("id"):
            entered.set()
            assert gate.wait(30)
        return real(*a, **k)

    client = thread_client
    holder: dict = {}
    small = wait(client, upload(client, "small.csv", sensor_csv(5)).json()["dataset_id"])
    monkeypatch.setattr(services, "profile_csv", slow_profile)
    # 업로드 응답 전에 id를 알 수 없으므로 첫 분석 대상(다음 등록)을 잡는다
    orig_register = client.app.state.datasets.register_csv

    def register_and_mark(*a, **k):
        m = orig_register(*a, **k)
        holder["id"] = m.dataset_id
        return m

    monkeypatch.setattr(client.app.state.datasets, "register_csv", register_and_mark)
    big = upload(client, "big.csv", sensor_csv(60_000)).json()  # 12만 행
    assert entered.wait(30)
    latencies = []
    for _ in range(10):
        t = time.time()
        st = {i["dataset_id"]: i["state"] for i in client.get("/api/datasets").json()["items"]}
        assert client.get(f"/api/datasets/{small['dataset_id']}/profile").status_code == 200
        latencies.append(time.time() - t)
        assert st[big["dataset_id"]] == "PROFILING"
    assert max(latencies) < 1.0
    gate.set()
    assert wait(client, big["dataset_id"], timeout=120)["row_count"] == 120_000


def test_profile_runs_in_separate_process(client):
    """기본 설정: 분석은 별도 프로세스에서 실행되고 결과는 같은 DB에 기록된다."""
    from concurrent.futures import ProcessPoolExecutor

    assert isinstance(client.app.state.profile_pool, ProcessPoolExecutor)
    d = wait(client, upload(client, "p.csv", sensor_csv(30)).json()["dataset_id"])
    assert d["state"] == "PROFILED" and d["row_count"] == 60
    assert client.get(f"/api/datasets/{d['dataset_id']}/profile").json()["row_count"] == 60


def test_reprofile_endpoint(client):
    d = wait(client, upload(client, "a.csv", sensor_csv(5)).json()["dataset_id"])
    r = client.post(f"/api/datasets/{d['dataset_id']}/profile")
    assert r.status_code == 202
    time.sleep(0.3)
    assert wait(client, d["dataset_id"])["profile_version"] == 2


def test_goal_save_validate_and_reload(client):
    d = wait(client, upload(client, "a.csv", sensor_csv(20)).json()["dataset_id"])
    url = f"/api/datasets/{d['dataset_id']}/goal"
    assert client.get(url).json() == {"saved": False, "draft_id": None, "revision": None, "goal": None, "issues": []}
    bad = client.put(url, json={"timestamp_col": "timestamp", "target_col": "mode", "horizons": []})
    assert bad.status_code == 422
    codes = {i["code"] for i in bad.json()["issues"]}
    assert {"GOAL_COLUMN_TYPE", "GOAL_FIELD_REQUIRED"} <= codes  # 범주형 타깃, 예측 구간 없음
    ok = client.put(url, json={"timestamp_col": "timestamp", "target_col": "sensor_1", "group_col": "device_id",
                               "horizons": ["15min", "30min"]})
    assert ok.status_code == 200 and ok.json()["saved"] and ok.json()["revision"] == 1
    again = client.put(url, json={"timestamp_col": "timestamp", "target_col": "sensor_1", "horizons": ["1h"]})
    assert again.json()["revision"] == 2 and again.json()["draft_id"] == ok.json()["draft_id"]
    got = client.get(url).json()
    assert got["goal"] == {"timestamp_col": "timestamp", "target_col": "sensor_1", "group_col": None, "horizons": ["1h"]}


def test_goal_requires_profiled(client):
    bad = wait(client, upload(client, "bad.csv", b"a,,b\n1,2,3\n").json()["dataset_id"])
    r = client.put(f"/api/datasets/{bad['dataset_id']}/goal", json={"timestamp_col": "a"})
    assert r.status_code == 409


def test_interrupted_work_recovered_on_start(tmp_path):
    from mlworkbench.app.services import DatasetService
    from mlworkbench.core.contracts import DatasetManifest, DatasetState
    from mlworkbench.storage.database import Workspace

    svc = DatasetService(Workspace(tmp_path / "ws").init())
    svc.register(DatasetManifest(dataset_id="ds_stuck", name="s.csv", modality="timeseries", source_kind="csv",
                                 state=DatasetState.UPLOADING))
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False)) as c:
        d = c.get("/api/datasets/ds_stuck").json()
        assert d["state"] == "UPLOAD_FAILED" and d["error"]["code"] == "INTERRUPTED"
