"""P2 승인 → 실행 → 결과: 실제 worker 프로세스로 CPU 학습까지.

승인 없음·바뀐 승인·중복 요청·실패·worker 종료·앱 종료/재시작·취소를 확인한다.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("xgboost")
pytest.importorskip("sklearn")
from fastapi.testclient import TestClient  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_csv_dataset import sensor_csv  # noqa: E402
from test_preprocess import GOAL, upload  # noqa: E402

SETTINGS = {"model": {"plugin_id": "timeseries.forecasting.xgboost", "plugin_version": "1.0.0",
                      "params": {"n_estimators": 40, "max_depth": 4}},
            "train_budget": {"mode": "recent_days", "days": 5},
            "split_policy": {"method": "chronological", "train": 0.7, "val": 0.15, "test": 0.15, "gap": "auto"},
            "evaluation": {"kind": "timeseries", "metrics": ["mae", "rmse"], "baseline": "naive_last_value",
                           "per_group": True, "per_horizon": True}}
SLOW = {**SETTINGS, "model": {**SETTINGS["model"], "params": {"n_estimators": 5000, "max_depth": 8, "learning_rate": 0.01}}}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False)) as c:
        c.ws = tmp_path / "ws"
        yield c


def approved(c, settings=SETTINGS, rows=2880):
    d = upload(c, "sensor_A.csv", sensor_csv(rows))
    did = d["dataset_id"]
    assert c.put(f"/api/datasets/{did}/goal", json=GOAL).status_code == 200
    rid = c.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
    plan = c.post(f"/api/datasets/{did}/plan/apply", json={"recommendation_id": rid, "expected_revision": 1}).json()
    r = c.put(f"/api/datasets/{did}/training", json={**settings, "expected_revision": plan["revision"]})
    assert r.status_code == 200, r.json()
    v = c.get(f"/api/datasets/{did}/confirmation").json()
    assert v["error_count"] == 0, v["issues"]
    a = c.post(f"/api/datasets/{did}/approve", json={"expected_revision": v["revision"], "confirmed": True}).json()
    assert a["approval"]["status"] == "approved" and a["run_available"] is True
    return did, a["approval"]


def start(c, did, appr):
    return c.post(f"/api/datasets/{did}/runs", json={"plan_id": appr["plan_id"], "plan_hash": appr["plan_hash"]})


def wait(c, run_id, states=("SUCCEEDED", "FAILED", "CANCELED"), timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        v = c.get(f"/api/runs/{run_id}").json()
        if v["state"] in states:
            return v
        time.sleep(0.3)
    raise AssertionError(f"timeout: {v['state']} {v['stage']}")


def wait_stage(c, run_id, stage, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        v = c.get(f"/api/runs/{run_id}").json()
        if v["stage"] == stage or v["state"] in ("SUCCEEDED", "FAILED", "CANCELED"):
            return v
        time.sleep(0.2)
    raise AssertionError("stage timeout")


from mlworkbench.runner.manager import pid_alive  # noqa: E402  (Windows·POSIX 공통)


def kill_hard(pid: int) -> None:
    """worker 강제 종료. Windows는 os.kill이 TerminateProcess로 처리된다."""
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


# --- 정상 실행 --------------------------------------------------------------
def test_approved_plan_trains_and_saves_results(client):
    did, appr = approved(client)
    r = start(client, did, appr)
    assert r.status_code == 200, r.json()
    run = r.json()
    assert run["state"] in ("QUEUED", "RUNNING") and run["duplicate"] is False
    assert "approve_" not in r.text and "approval_id" not in r.text
    v = wait(client, run["run_id"])
    assert v["state"] == "SUCCEEDED", (v["error_code"], v["error_message_ko"])
    stages = [e["stage"] for e in v["events"]]
    order = ["queued", "prepare", "preprocess", "train", "evaluate", "save"]
    assert [s for s in order if s in stages] == order
    assert [e["seq"] for e in v["events"]] == list(range(1, len(v["events"]) + 1))
    assert v["events"][-1]["message_code"] == "RUN_SUCCEEDED"
    # 실제 계산된 지표·분할
    t = v["metrics"]["test"]
    assert t["overall"]["model"]["mae"] > 0 and t["overall"]["baseline"]["rmse"] > 0
    assert set(t["per_horizon"]) == {"15min", "1h"} and set(t["per_group"]) == {"D01", "D02"}
    sp = v["split"]
    assert sp["train_samples"] > 0 and sp["test_samples"] > 0 and sp["budget"] == {"mode": "recent_days", "days": 5}
    assert v["predictions_available"] and v["started_at"] and v["finished_at"]
    csv = client.get(f"/api/runs/{run['run_id']}/predictions.csv")
    assert csv.status_code == 200 and csv.text.startswith("split,group,origin_time,target_time,horizon,actual,predicted,baseline")
    exp = client.ws / "experiments" / run["run_id"]
    for rel in ("config.yaml", "dataset_manifest.json", "environment.json", "metrics.json", "metrics_meta.json",
                "split.json", "split_rows.csv", "predictions/predictions.csv", "model/pipeline.json", "train.log"):
        assert (exp / rel).exists(), rel
    rec = client.app.state.jobs.get_record(run["run_id"])
    assert rec.train_sample_count == sp["train_samples"] and rec.resolved_train_range["train_end"] == sp["train_end"]
    # S4: 이 승인으로 실행됨
    a = client.get(f"/api/datasets/{did}/confirmation").json()["approval"]
    assert a["status"] == "submitted" and a["run_id"] == run["run_id"]
    lst = client.get("/api/runs", params={"dataset_id": did}).json()["items"]
    assert [x["run_id"] for x in lst] == [run["run_id"]]
    # 같은 seed 재계산 = 저장 지표, 저장 예측값으로 다시 계산한 지표 = 저장 지표 (새 실행 기록 없음)
    from mlworkbench.cli import main
    assert main(["experiment", "verify", run["run_id"], "--workspace", str(client.ws), "--tolerance", "1e-9"]) == 0
    assert client.app.state.jobs.count_runs() == 1


def test_duplicate_requests_make_one_run_and_one_worker(client):
    did, appr = approved(client)
    out: list = []
    ts = [threading.Thread(target=lambda: out.append(start(client, did, appr).json())) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    out.append(start(client, did, appr).json())
    assert len({o["run_id"] for o in out}) == 1
    assert sum(not o["duplicate"] for o in out) == 1
    assert client.app.state.jobs.count_runs() == 1
    run_id = out[0]["run_id"]
    assert len(client.app.state.runs.procs) <= 1
    wait(client, run_id)
    worker = json.loads((client.ws / "experiments" / run_id / "worker.json").read_text())
    assert isinstance(worker["pid"], int)


# --- 승인 검사 --------------------------------------------------------------
def test_start_requires_matching_active_approval(client):
    did, appr = approved(client)
    r = client.post(f"/api/datasets/{did}/runs", json={"plan_id": "plan_guess", "plan_hash": appr["plan_hash"]})
    assert r.status_code == 404 and r.json()["code"] == "APPROVAL_NOT_FOUND"
    r = client.post(f"/api/datasets/{did}/runs", json={"plan_id": appr["plan_id"], "plan_hash": "sha256:" + "0" * 64})
    assert r.status_code == 409 and r.json()["code"] == "PLAN_HASH_MISMATCH"
    r = client.post("/api/datasets/ds_other/runs", json={"plan_id": appr["plan_id"], "plan_hash": appr["plan_hash"]})
    assert r.status_code == 404
    # 승인 후 실행 설정 변경 → 이전 승인으로 시작 불가
    t = client.get(f"/api/datasets/{did}/training").json()
    assert client.put(f"/api/datasets/{did}/training",
                      json={**SETTINGS, "train_budget": {"mode": "all"}, "expected_revision": t["revision"]}).status_code == 200
    r = start(client, did, appr)
    assert r.status_code == 409 and r.json()["code"] == "APPROVAL_INVALIDATED"
    assert client.app.state.jobs.count_runs() == 0


# --- 실패·종료·취소 ----------------------------------------------------------
def test_data_changed_after_approval_is_refused(client):
    did, appr = approved(client)
    src = client.ws / "datasets" / did / "source.csv"
    src.write_text("timestamp,device_id,sensor_1,mode,memo\nbad,D01,1,run,x\n", encoding="utf-8")  # 승인 후 파일 손상
    run = start(client, did, appr).json()
    v = wait(client, run["run_id"])
    assert v["state"] == "FAILED" and v["error_code"] == "DATASET_CHANGED" and "바뀌어" in v["error_message_ko"]
    assert str(client.ws) not in json.dumps(v)


def test_pipeline_failure_is_recorded(client):
    did, appr = approved(client, rows=16)  # 80분 분량: 1시간 뒤 예측의 학습·테스트 표본을 만들 수 없음
    run = start(client, did, appr).json()
    v = wait(client, run["run_id"])
    assert v["state"] == "FAILED" and v["error_code"] in ("NO_TRAIN_SAMPLES", "NO_TEST_SAMPLES", "TOO_FEW_TIMES")
    assert v["error_message_ko"] and str(client.ws) not in json.dumps(v)


def test_killed_worker_marked_failed(client):
    did, appr = approved(client, SLOW)
    run = start(client, did, appr).json()
    wait_stage(client, run["run_id"], "train")
    pid = json.loads((client.ws / "experiments" / run["run_id"] / "worker.json").read_text())["pid"]
    kill_hard(pid)
    v = wait(client, run["run_id"], timeout=20)
    assert v["state"] == "FAILED" and v["error_code"] == "WORKER_EXITED"


def test_cancel_needs_confirmation_and_stops_worker(client):
    did, appr = approved(client, SLOW)
    run = start(client, did, appr).json()
    wait_stage(client, run["run_id"], "train")
    r = client.post(f"/api/runs/{run['run_id']}/cancel", json={"confirmation_id": "confirm_guess"})
    assert r.status_code == 400 and r.json()["code"] == "CONFIRMATION_REQUIRED"
    cid = client.post(f"/api/runs/{run['run_id']}/cancel-confirmation").json()["confirmation_id"]
    r = client.post(f"/api/runs/{run['run_id']}/cancel", json={"confirmation_id": cid})
    assert r.status_code == 200 and r.json()["state"] in ("CANCEL_REQUESTED", "CANCELED")
    v = wait(client, run["run_id"], timeout=30)
    assert v["state"] == "CANCELED" and v["error_code"] == "CANCELED"
    pid = json.loads((client.ws / "experiments" / run["run_id"] / "worker.json").read_text())["pid"]
    time.sleep(1.0)
    assert not pid_alive(pid)


def test_app_shutdown_stops_worker_and_restart_recovers(tmp_path):
    ws = tmp_path / "ws"
    with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c:
        did, appr = approved(c, SLOW)
        run = start(c, did, appr).json()
        wait_stage(c, run["run_id"], "train")
        pid = json.loads((ws / "experiments" / run["run_id"] / "worker.json").read_text())["pid"]
        # 대기열에서 기다리는 실행(앞 실행이 학습 중이라 worker 없음)과
        # 시작 도중 앱이 죽은 실행(잠금 파일만 있고 worker.json 없음)을 하나씩 더 만든다
        did2, appr2 = approved(c)
        queued = start(c, did2, appr2).json()
        assert queued["state"] == "QUEUED" and queued["queue_position"] == 1
        did3, appr3 = approved(c)
        half = c.app.state.jobs.submit_approved(dataset_id=did3, plan_id=appr3["plan_id"], plan_hash=appr3["plan_hash"])
        (ws / "experiments" / half["run_id"]).mkdir(parents=True, exist_ok=True)
        (ws / "experiments" / half["run_id"] / "worker.lock").write_text("")
    # 앱 종료: worker 종료 + 기록 일치, 대기 실행은 그대로
    time.sleep(0.5)
    assert not pid_alive(pid)
    with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c2:
        v = c2.get(f"/api/runs/{run['run_id']}").json()
        assert v["state"] == "FAILED" and v["error_code"] == "APP_SHUTDOWN"
        h = c2.get(f"/api/runs/{half['run_id']}").json()
        assert h["state"] == "FAILED" and h["error_code"] == "WORKER_NOT_STARTED"
        q = wait(c2, queued["run_id"])  # 다시 켜면 대기열을 이어서 시작
        assert q["state"] == "SUCCEEDED", (q["error_code"], q["error_message_ko"])


def test_restart_marks_lost_worker(tmp_path):
    """앱이 강제 종료되어 종료 처리를 못 한 경우: heartbeat가 멈춘 RUNNING 실행은 다음 시작 때 정리."""
    ws = tmp_path / "ws"
    with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c:
        did, appr = approved(c)
        out = c.app.state.jobs.submit_approved(dataset_id=did, plan_id=appr["plan_id"], plan_hash=appr["plan_hash"])
        jobs = c.app.state.jobs
        from mlworkbench.core.contracts import RunState
        jobs.append_event(out["run_id"], state=RunState.RUNNING, stage="train", message_code="TRAIN_STARTED")
        exp = ws / "experiments" / out["run_id"]
        (exp / "worker.json").write_text(json.dumps({"pid": 999999, "started_at": "x"}))
        (exp / "heartbeat").write_text(str(time.time() - 120))
    with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c2:
        v = c2.get(f"/api/runs/{out['run_id']}").json()
        assert v["state"] == "FAILED" and v["error_code"] == "WORKER_LOST"


def test_reopened_app_detects_dead_worker_even_with_fresh_heartbeat(tmp_path):
    """앱 창을 닫자마자(15초 안에) 다시 열어도, 기록된 worker 프로세스가 없으면 바로 정리."""
    ws = tmp_path / "ws"
    with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c:
        did, appr = approved(c)
        out = c.app.state.jobs.submit_approved(dataset_id=did, plan_id=appr["plan_id"], plan_hash=appr["plan_hash"])
        from mlworkbench.core.contracts import RunState
        c.app.state.jobs.append_event(out["run_id"], state=RunState.RUNNING, stage="train", message_code="TRAIN_STARTED")
        exp = ws / "experiments" / out["run_id"]
        (exp / "worker.json").write_text(json.dumps({"pid": 2 ** 22 + 12345, "started_at": "x"}))
        (exp / "heartbeat").write_text(str(time.time()))  # 방금까지 살아 있던 것처럼
        c.app.state.runs.scan_orphans()
        v = c.get(f"/api/runs/{out['run_id']}").json()
        assert v["state"] == "FAILED" and v["error_code"] == "WORKER_LOST"
