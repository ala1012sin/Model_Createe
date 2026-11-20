"""ADD-UX-02·06·07: 실행 이름, 실행 삭제(적용 기록 함께), 데이터셋 삭제(실행·모델 함께)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("xgboost")
from fastapi.testclient import TestClient  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_csv_dataset import sensor_csv  # noqa: E402
from test_inference import apply, predict  # noqa: E402
from test_preprocess import upload  # noqa: E402
from test_runs import SETTINGS, SLOW, approved, start, wait, wait_stage  # noqa: E402


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False)) as c:
        c.ws = tmp_path / "ws"
        yield c


def trained(c):
    did, appr = approved(c)
    run = start(c, did, appr).json()
    assert wait(c, run["run_id"])["state"] == "SUCCEEDED"
    return did, run["run_id"]


def cancel(c, run_id):
    cid = c.post(f"/api/runs/{run_id}/cancel-confirmation").json()["confirmation_id"]
    assert c.post(f"/api/runs/{run_id}/cancel", json={"confirmation_id": cid}).status_code == 200
    return wait(c, run_id, timeout=40)


def test_run_name(client):
    c = client
    did, rid = trained(c)
    r = c.patch(f"/api/runs/{rid}", json={"name": "  lag만 · 최근 5일  "})
    assert r.status_code == 200 and r.json()["name"] == "lag만 · 최근 5일"
    assert c.get("/api/runs").json()["items"][0]["name"] == "lag만 · 최근 5일"
    assert c.get("/api/models").json()["items"][0]["name"] == "lag만 · 최근 5일"
    assert c.patch(f"/api/runs/{rid}", json={"name": "a\nb"}).json()["code"] == "NAME_INVALID"
    assert c.patch(f"/api/runs/{rid}", json={"name": "가" * 61}).json()["code"] == "NAME_TOO_LONG"
    assert c.patch(f"/api/runs/{rid}", json={"name": "   "}).json()["name"] is None
    assert c.patch("/api/runs/run_nope", json={"name": "x"}).status_code == 404
    # 이름은 LLM 추천 요청에 실리지 않는다(실행 기록은 추천 입력이 아님) — 비교 응답에는 화면용으로 포함
    c.patch(f"/api/runs/{rid}", json={"name": "기준"})
    did2, rid2 = trained(c)
    v = c.get("/api/runs/compare", params=[("run_ids", rid), ("run_ids", rid2)]).json()
    assert [x["name"] for x in v["runs"]] == ["기준", None]


def test_delete_run_with_applications(client):
    c = client
    did, rid = trained(c)
    src = (c.ws / "datasets" / did / "source.csv").read_bytes()
    app = apply(c, src, "same.csv", run_id=rid)
    assert predict(c, app["application_id"])["state"] == "SUCCEEDED"
    exp, app_dir = c.ws / "experiments" / rid, c.ws / "applications" / app["application_id"]
    assert exp.is_dir() and app_dir.is_dir()
    pv = c.get(f"/api/runs/{rid}/delete-preview").json()
    assert pv["deletable"] and pv["applications"] == 1 and pv["bytes"] > 0
    r = c.request("DELETE", f"/api/runs/{rid}", json={"confirmed": False})
    assert r.status_code == 409 and r.json()["code"] == "CONFIRMATION_REQUIRED"
    r = c.request("DELETE", f"/api/runs/{rid}", json={"confirmed": True})
    assert r.status_code == 409 and r.json()["code"] == "CONFIRM_APPLICATIONS" and "1개" in r.json()["message_ko"]
    assert exp.is_dir()  # 거절되면 아무것도 지우지 않음
    r = c.request("DELETE", f"/api/runs/{rid}", json={"confirmed": True, "include_applications": True})
    assert r.status_code == 200, r.json()
    assert r.json() == {"deleted_run": rid, "applications_deleted": 1, "files_left": []}
    assert not exp.exists() and not app_dir.exists()
    assert c.get(f"/api/runs/{rid}").status_code == 404
    assert c.get(f"/api/applications/{app['application_id']}").status_code == 404
    assert rid not in [m["run_id"] for m in c.get("/api/models").json()["items"]]
    log = [json.loads(x) for x in (c.ws / "deletions.log").read_text(encoding="utf-8").splitlines()]
    assert log[-1]["kind"] == "run" and log[-1]["id"] == rid and str(c.ws) not in json.dumps(log)
    # 확정 기록은 남아 같은 설정으로 다시 확정·학습할 수 있다
    v = c.get(f"/api/datasets/{did}/confirmation").json()
    assert v["approval"]["status"] != "submitted"
    a = c.post(f"/api/datasets/{did}/approve", json={"expected_revision": v["revision"], "confirmed": True}).json()
    again = start(c, did, a["approval"]).json()
    assert wait(c, again["run_id"])["state"] == "SUCCEEDED"


def test_active_run_cannot_be_deleted(client):
    c = client
    did, appr = approved(c, SLOW)
    run = start(c, did, appr).json()
    wait_stage(c, run["run_id"], "train")
    pv = c.get(f"/api/runs/{run['run_id']}/delete-preview").json()
    assert not pv["deletable"] and "취소" in pv["reason_ko"]
    r = c.request("DELETE", f"/api/runs/{run['run_id']}", json={"confirmed": True, "include_applications": True})
    assert r.status_code == 409 and r.json()["code"] == "RUN_ACTIVE"
    dp = c.get(f"/api/datasets/{did}/delete-preview").json()
    assert not dp["deletable"] and dp["active_runs"] == 1
    r = c.request("DELETE", f"/api/datasets/{did}", json={"confirmed": True, "include_runs": True})
    assert r.status_code == 409 and r.json()["code"] == "DATASET_BUSY"
    assert cancel(c, run["run_id"])["state"] == "CANCELED"
    r = c.request("DELETE", f"/api/runs/{run['run_id']}", json={"confirmed": True})
    assert r.status_code == 200  # 취소된 실행은 지울 수 있다(적용 기록 없음)


def test_delete_dataset_with_runs(client):
    c = client
    did, rid = trained(c)
    keep_did, keep_rid = trained(c)  # 다른 데이터셋은 그대로 남아야 함
    app = apply(c, (c.ws / "datasets" / did / "source.csv").read_bytes(), "x.csv", run_id=rid)
    plan_id = c.app.state.jobs.get_record(rid).plan_id
    assert (c.ws / "plans" / plan_id).is_dir()
    pv = c.get(f"/api/datasets/{did}/delete-preview").json()
    assert pv["deletable"] and pv["runs"] == 1 and pv["applications"] == 1
    r = c.request("DELETE", f"/api/datasets/{did}", json={"confirmed": True})
    assert r.status_code == 409 and r.json()["code"] == "CONFIRM_RUNS"
    r = c.request("DELETE", f"/api/datasets/{did}", json={"confirmed": True, "include_runs": True})
    assert r.status_code == 200, r.json()
    out = r.json()
    assert out["runs_deleted"] == 1 and out["applications_deleted"] == 1 and out["plans_deleted"] == 1 and out["files_left"] == []
    assert c.get(f"/api/datasets/{did}").status_code == 404
    assert c.get(f"/api/runs/{rid}").status_code == 404
    assert c.get(f"/api/applications/{app['application_id']}").status_code == 404
    for d in (c.ws / "datasets" / did, c.ws / "experiments" / rid, c.ws / "plans" / plan_id,
              c.ws / "applications" / app["application_id"]):
        assert not d.exists(), d
    with c.app.state.jobs.ws.session() as conn:
        for table in ("drafts", "recommendations", "profiles", "approvals"):
            col = "json_extract(body_json, '$.dataset_id')" if table == "drafts" else "dataset_id"
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (did,)).fetchone()[0] == 0, table
    assert [x["dataset_id"] for x in c.get("/api/datasets").json()["items"]] == [keep_did]
    assert c.get(f"/api/runs/{keep_rid}").json()["state"] == "SUCCEEDED"
    assert (c.ws / "experiments" / keep_rid).is_dir()


def test_delete_dataset_without_runs(client):
    c = client
    d = upload(c, "broken.csv", b"time,,temp\n2026-01-01 00:00,1,2\n")
    assert d["state"] == "PROFILE_FAILED"
    pv = c.get(f"/api/datasets/{d['dataset_id']}/delete-preview").json()
    assert pv["deletable"] and pv["runs"] == 0
    r = c.request("DELETE", f"/api/datasets/{d['dataset_id']}", json={"confirmed": True})
    assert r.status_code == 200 and r.json()["runs_deleted"] == 0
    assert not (c.ws / "datasets" / d["dataset_id"]).exists()
    assert c.get("/api/datasets").json()["items"] == []


def test_name_at_start(client):
    """확인·실행에서 학습 시작할 때 이름을 붙인다. 잘못된 이름이면 실행을 만들지 않는다."""
    c = client
    did, appr = approved(c)
    body = {"plan_id": appr["plan_id"], "plan_hash": appr["plan_hash"]}
    r = c.post(f"/api/datasets/{did}/runs", json={**body, "name": "a\tb"})
    assert r.status_code == 400 and r.json()["code"] == "NAME_INVALID"
    assert c.app.state.jobs.count_runs() == 0
    r = c.post(f"/api/datasets/{did}/runs", json={**body, "name": "  첫 실험  "})
    assert r.status_code == 200 and r.json()["name"] == "첫 실험"
    again = c.post(f"/api/datasets/{did}/runs", json={**body, "name": "다른 이름"}).json()
    assert again["duplicate"] and again["name"] == "첫 실험"  # 같은 승인 재요청은 이름을 바꾸지 않음
    wait(c, r.json()["run_id"])
