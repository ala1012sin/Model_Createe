"""S4 확인·승인 — 계획 요약, 최종 검증(오류/경고·고칠 화면), 명시적 확인 후 승인, 승인 무효화.

실행 가능 레지스트리는 테스트에서만 주입한다(내장 Registry·어댑터 등록 경로는 그대로).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_preprocess import preview_registry, ready_dataset, ready_registry  # noqa: E402
from test_training import S3, with_recommendation  # noqa: E402

S3_70 = {**S3, "split_policy": {"method": "chronological", "train": 0.7, "val": 0.15, "test": 0.15, "gap": "auto"}}


def _client(tmp_path, registry=None):
    return TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False, registry=registry))


@pytest.fixture
def client(tmp_path):  # 명세만 있는 내장 카탈로그: 모든 플러그인 미구현
    with _client(tmp_path, preview_registry()) as c:
        yield c


@pytest.fixture
def ready_client(tmp_path):  # 테스트 전용 실행 가능 레지스트리
    with _client(tmp_path, ready_registry()) as c:
        yield c


def saved_s3(c, fields=S3_70):
    did, plan = with_recommendation(c)
    r = c.put(f"/api/datasets/{did}/training", json={**fields, "expected_revision": plan["revision"]})
    assert r.status_code == 200, r.json()
    return did


def runs(c) -> int:
    with c.app.state.plans.ws.session() as conn:
        return conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


def approvals(c) -> list:
    with c.app.state.plans.ws.session() as conn:
        return [dict(r) for r in conn.execute("SELECT plan_id, plan_hash, status FROM approvals ORDER BY approved_at")]


def approve(c, did, rev, confirmed=True):
    return c.post(f"/api/datasets/{did}/approve", json={"expected_revision": rev, "confirmed": confirmed})


# --- 요약 ------------------------------------------------------------------
def test_summary_shows_saved_plan(client):
    did = saved_s3(client)
    v = client.get(f"/api/datasets/{did}/confirmation").json()
    assert v["dataset_name"] == "sensor_A.csv" and v["dataset_version"]
    assert v["dataset_rows"] == client.get(f"/api/datasets/{did}").json()["row_count"] > 0
    assert v["goal"]["target_col"] and v["goal"]["timestamp_col"]
    assert [s["plugin_id"] for s in v["preprocessing"]][-1] == "ts.lag" and v["preprocessing"][-1]["params"] == {"lags": [1, 3, 6]}
    assert v["excluded"] == ["결측 처리"]
    assert v["model"]["plugin_id"] == "timeseries.forecasting.xgboost" and v["model"]["params"]["n_estimators"] == 300
    assert v["train_budget"] == {"mode": "recent_days", "days": 30}
    assert v["split_policy"]["train"] == 0.7 and v["evaluation"]["metrics"] == ["mae", "rmse"]
    # 산출되지 않은 값은 비워 둔다
    assert v["train_period"] is None and v["train_rows"] is None and v["estimated_duration"] is None
    assert v["approval"] == {"status": "none", "plan_id": None, "plan_hash": None, "approved_at": None, "run_id": None}
    assert v["run_available"] is False


# --- 미구현 후보는 승인 차단, 고칠 화면 표시 ---------------------------------------
def test_unready_plugins_block_approval_with_steps(client):
    did = saved_s3(client)
    v = client.get(f"/api/datasets/{did}/confirmation").json()
    errs = [i for i in v["issues"] if i["severity"] == "error"]
    assert v["error_count"] == len(errs) > 0 and v["can_approve"] is False
    steps = {i["step"] for i in errs if i["code"] == "PLUGIN_NOT_READY"}
    assert steps == {"preprocess", "train"}  # 전처리 → S2, 모델 → S3
    r = approve(client, did, v["revision"])
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    assert {i["step"] for i in r.json()["issues"] if i["code"] == "PLUGIN_NOT_READY"} == {"preprocess", "train"}
    assert approvals(client) == [] and runs(client) == 0


def test_issue_step_mapping():
    from mlworkbench.app.services import PlanService
    f = PlanService.issue_step
    assert f("user_goal.target_col") == "goal" and f("dataset_id") == "goal"
    assert f("preprocessing[2].params.lags") == "preprocess" and f("model.params.max_depth") == "train"
    assert f("split_policy.train") == "train" and f("evaluation.per_group") == "train" and f("other") is None


# --- 실행 가능 목록: 정상 승인 ------------------------------------------------------
def test_approve_and_reload_hash_without_token(ready_client, caplog):
    c = ready_client
    did = saved_s3(c)
    v = c.get(f"/api/datasets/{did}/confirmation").json()
    assert v["error_count"] == 0 and v["can_approve"] is True and v["approval"]["status"] == "none"
    with caplog.at_level(logging.DEBUG):
        r = approve(c, did, v["revision"])
    assert r.status_code == 200, r.json()
    a = r.json()["approval"]
    assert a["status"] == "approved" and a["plan_hash"].startswith("sha256:") and a["plan_id"].startswith("plan_")
    assert r.json()["can_approve"] is False and r.json()["run_available"] is False
    # 승인 토큰은 응답·로그 어디에도 없다
    assert "approve_" not in r.text and "approval_id" not in r.text
    assert "approve_" not in caplog.text
    # 다시 열어도 같은 해시, 같은 리비전 재승인은 새 승인을 만들지 않음
    again = c.get(f"/api/datasets/{did}/confirmation").json()["approval"]
    assert again == a
    assert approve(c, did, v["revision"]).json()["approval"] == a
    assert len(approvals(c)) == 1 and runs(c) == 0
    snap = c.app.state.plans.get_snapshot(a["plan_id"])
    assert snap.plan_hash == a["plan_hash"] and snap.immutable_config["train_budget"] == {"mode": "recent_days", "days": 30}


def test_confirmation_checkbox_required(ready_client):
    did = saved_s3(ready_client)
    rev = ready_client.get(f"/api/datasets/{did}/confirmation").json()["revision"]
    r = approve(ready_client, did, rev, confirmed=False)
    assert r.status_code == 400 and r.json()["code"] == "CONFIRMATION_REQUIRED"
    assert approvals(ready_client) == []


def test_wrong_revision_rejected(ready_client):
    did = saved_s3(ready_client)
    rev = ready_client.get(f"/api/datasets/{did}/confirmation").json()["revision"]
    for bad in (rev - 1, rev + 1):
        r = approve(ready_client, did, bad)
        assert r.status_code == 409 and r.json()["code"] == "REVISION_CONFLICT"
    assert approvals(ready_client) == [] and runs(ready_client) == 0


def test_execution_change_invalidates_approval(ready_client):
    c = ready_client
    did = saved_s3(c)
    rev = c.get(f"/api/datasets/{did}/confirmation").json()["revision"]
    first = approve(c, did, rev).json()["approval"]
    # S3에서 학습량 변경 → 실행 설정 변경 → 승인 무효화
    t = c.get(f"/api/datasets/{did}/training").json()
    r = c.put(f"/api/datasets/{did}/training",
              json={**S3_70, "train_budget": {"mode": "recent_days", "days": 14}, "expected_revision": t["revision"]})
    assert r.status_code == 200
    v = c.get(f"/api/datasets/{did}/confirmation").json()
    assert v["approval"]["status"] == "invalidated" and v["can_approve"] is True
    assert [a["status"] for a in approvals(c)] == ["invalidated"]
    second = approve(c, did, v["revision"]).json()["approval"]
    assert second["status"] == "approved" and second["plan_hash"] != first["plan_hash"]
    assert [a["status"] for a in approvals(c)] == ["invalidated", "active"] and runs(c) == 0


def test_description_only_change_keeps_approval(ready_client):
    c = ready_client
    did = saved_s3(c)
    rev = c.get(f"/api/datasets/{did}/confirmation").json()["revision"]
    a = approve(c, did, rev).json()["approval"]
    plan = c.get(f"/api/datasets/{did}/plan").json()
    steps = [{k: s[k] for k in ("plugin_id", "plugin_version", "params", "reason")} for s in plan["steps"]]
    steps[0]["reason"] = "설명만 바꿈"
    excluded = [{k: s[k] for k in ("plugin_id", "plugin_version", "params", "reason")} for s in plan["excluded"]]
    assert c.put(f"/api/datasets/{did}/plan", json={"expected_revision": plan["revision"], "steps": steps,
                                                     "excluded": excluded}).status_code == 200
    assert c.get(f"/api/datasets/{did}/confirmation").json()["approval"] == a


def test_warnings_only_can_be_approved(ready_client):
    c = ready_client
    no_val = {**S3_70, "split_policy": {"method": "chronological", "train": 0.8, "val": 0.0, "test": 0.2, "gap": "auto"}}
    did = saved_s3(c, no_val)
    v = c.get(f"/api/datasets/{did}/confirmation").json()
    assert v["error_count"] == 0 and v["warning_count"] >= 1
    w = next(i for i in v["issues"] if i["code"] == "SPLIT_NO_VALIDATION")
    assert w["severity"] == "warning" and w["step"] == "train"
    r = approve(c, did, v["revision"])
    assert r.status_code == 200 and r.json()["approval"]["status"] == "approved"
    assert r.json()["warning_count"] == v["warning_count"] and runs(c) == 0


def test_confirmation_requires_goal(client):
    from test_preprocess import upload
    from test_csv_dataset import sensor_csv
    d = upload(client, "a.csv", sensor_csv(10))
    r = client.get(f"/api/datasets/{d['dataset_id']}/confirmation")
    assert r.status_code == 409 and r.json()["code"] == "GOAL_REQUIRED"
    assert ready_dataset  # 같은 진입 조건(목표 저장)을 공유
