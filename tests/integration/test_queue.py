"""P2 최소 대기열 — 동시 학습 1개(기본), 제출 순서, 대기 중 취소, 동시 수 설정 (docs/P2_QUEUE_BRIEF.md)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("xgboost")
from fastapi.testclient import TestClient  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_runs import SLOW, approved, start, wait, wait_stage  # noqa: E402

# 대기 상태를 몇 초 동안 관찰할 수 있게 더 오래 걸리는 학습(취소로 끝낸다)
VSLOW = {**SLOW, "model": {**SLOW["model"], "params": {"n_estimators": 5000, "max_depth": 12, "learning_rate": 0.001}}}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False)) as c:
        c.ws = tmp_path / "ws"
        yield c


def cancel(c, run_id, *, may_be_done=False):
    cid = c.post(f"/api/runs/{run_id}/cancel-confirmation").json()["confirmation_id"]
    r = c.post(f"/api/runs/{run_id}/cancel", json={"confirmation_id": cid})
    if may_be_done and r.status_code == 409:
        return None
    assert r.status_code == 200, r.json()
    return r.json()


def has_worker(c, run_id):
    return (c.ws / "experiments" / run_id / "worker.lock").exists()


def test_second_run_waits_until_first_finishes(client):
    c = client
    did1, a1 = approved(c, VSLOW, rows=5760)
    r1 = start(c, did1, a1).json()
    assert r1["queue_position"] is None
    wait_stage(c, r1["run_id"], "train")
    did2, a2 = approved(c)
    r2 = start(c, did2, a2).json()
    assert r2["state"] == "QUEUED" and r2["queue_position"] == 1 and r2["queue_ahead"] == 1 and r2["max_parallel"] == 1
    time.sleep(3)  # 주기 대조(2초)가 돌아도 자리가 없으면 시작하지 않는다
    assert c.get(f"/api/runs/{r2['run_id']}").json()["state"] == "QUEUED" and not has_worker(c, r2["run_id"])
    lst = {x["run_id"]: x for x in c.get("/api/runs").json()["items"]}
    assert lst[r2["run_id"]]["queue_position"] == 1 and lst[r1["run_id"]]["queue_position"] is None
    # 같은 승인으로 다시 눌러도 대기 실행 하나
    again = start(c, did2, a2).json()
    assert again["run_id"] == r2["run_id"] and again["duplicate"] and c.app.state.jobs.count_runs() == 2
    assert c.get(f"/api/runs/{r1['run_id']}").json()["state"] == "RUNNING"  # 위 관찰 동안 앞 실행이 계속 학습 중이었음
    cancel(c, r1["run_id"])
    v1 = wait(c, r1["run_id"], timeout=40)
    assert v1["state"] == "CANCELED"
    v2 = wait(c, r2["run_id"])
    assert v2["state"] == "SUCCEEDED", (v2["error_code"], v2["error_message_ko"])
    assert v2["started_at"] >= v1["finished_at"]  # 앞 실행이 끝난 뒤에 시작


def test_cancel_waiting_run_is_immediate(client):
    c = client
    did1, a1 = approved(c, VSLOW, rows=5760)
    r1 = start(c, did1, a1).json()
    did2, a2 = approved(c)
    r2 = start(c, did2, a2).json()
    assert r2["state"] == "QUEUED"
    out = cancel(c, r2["run_id"])
    assert out["state"] == "CANCELED" and out["error_message_ko"] == "대기 중에 취소했습니다"
    assert not has_worker(c, r2["run_id"]) and out["queue_position"] is None
    assert c.get(f"/api/runs/{r1['run_id']}").json()["state"] in ("QUEUED", "RUNNING")  # 앞 실행은 영향 없음
    r = c.post(f"/api/runs/{r2['run_id']}/cancel-confirmation").json()["confirmation_id"]
    again = c.post(f"/api/runs/{r2['run_id']}/cancel", json={"confirmation_id": r})
    assert again.status_code == 409 and again.json()["code"] == "RUN_FINISHED"
    cancel(c, r1["run_id"])
    assert wait(c, r1["run_id"], timeout=40)["state"] == "CANCELED"


def test_runs_start_in_submission_order_one_at_a_time(client):
    c = client
    approvals = [approved(c) for _ in range(3)]
    runs = [start(c, did, a).json() for did, a in approvals]
    # 제출 시점의 순번은 앞 실행의 진행 속도에 따라 달라지므로(빠른 PC에선 이미 끝났을 수 있음) 보지 않는다
    assert all(r["queue_position"] in (None, 1, 2) for r in runs)
    waiting = c.app.state.runs.queue_state()[1]
    assert waiting == [r["run_id"] for r in runs if r["run_id"] in waiting]  # 같은 초에 제출해도 대기 순서 = 제출 순서
    done = [wait(c, r["run_id"]) for r in runs]
    assert all(v["state"] == "SUCCEEDED" for v in done), [(v["error_code"], v["error_message_ko"]) for v in done]
    for prev, nxt in zip(done, done[1:]):
        assert nxt["started_at"] >= prev["finished_at"]  # 겹치지 않고 제출 순서대로


def test_max_parallel_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("MLWB_MAX_PARALLEL_RUNS", "2")
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False)) as c:
        c.ws = tmp_path / "ws"
        ids = []
        for _ in range(3):
            did, a = approved(c, VSLOW, rows=5760)
            ids.append(start(c, did, a).json())
        assert ids[2]["state"] == "QUEUED" and ids[2]["queue_position"] == 1 and ids[2]["queue_ahead"] == 2
        assert has_worker(c, ids[0]["run_id"]) and has_worker(c, ids[1]["run_id"]) and not has_worker(c, ids[2]["run_id"])
        assert ids[2]["max_parallel"] == 2
        for r in ids:
            cancel(c, r["run_id"], may_be_done=True)
        for r in ids:
            assert wait(c, r["run_id"], timeout=40)["state"] == "CANCELED"
    monkeypatch.setenv("MLWB_MAX_PARALLEL_RUNS", "abc")
    from mlworkbench.runner.manager import max_parallel_runs
    assert max_parallel_runs() == 1
    monkeypatch.setenv("MLWB_MAX_PARALLEL_RUNS", "99")
    assert max_parallel_runs() == 4
