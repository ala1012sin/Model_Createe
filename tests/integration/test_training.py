"""S3 학습 설정 — 모델·학습량·분할·평가 저장, 기존 목표·전처리 보존, 형식 오류 위치·한글 메시지, 충돌."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402
from mlworkbench.core.errors import ApprovalError  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_csv_dataset import sensor_csv  # noqa: E402
from test_preprocess import GOAL, preview_registry, ready_dataset, ready_registry  # noqa: E402

XGB = {"plugin_id": "timeseries.forecasting.xgboost", "plugin_version": "1.0.0", "params": {"n_estimators": 300}}
S3 = {"model": XGB, "train_budget": {"mode": "recent_days", "days": 30},
      "split_policy": {"method": "chronological", "train": 0.6, "val": 0.2, "test": 0.2, "gap": "auto"},
      "evaluation": {"kind": "timeseries", "metrics": ["mae", "rmse"], "baseline": "naive_last_value",
                     "per_group": True, "per_horizon": True}}


@pytest.fixture
def client(tmp_path):  # 명세만 있는 카탈로그(모두 미구현): S3 미리보기 흐름
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False,
                               registry=preview_registry())) as c:
        yield c


def with_recommendation(c):
    did = ready_dataset(c)
    rid = c.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
    c.post(f"/api/datasets/{did}/plan/apply", json={"recommendation_id": rid, "expected_revision": 1})
    plan = c.get(f"/api/datasets/{did}/plan").json()
    # S2 편집: 결측 처리 제외, lag 파라미터 수정
    steps = [{k: s[k] for k in ("plugin_id", "plugin_version", "params", "reason")} for s in plan["steps"]]
    missing = next(s for s in steps if s["plugin_id"] == "ts.missing")
    steps = [s for s in steps if s is not missing]
    steps[-1]["params"] = {"lags": [1, 3, 6]}
    r = c.put(f"/api/datasets/{did}/plan", json={"expected_revision": plan["revision"], "steps": steps, "excluded": [missing]})
    assert r.status_code == 200
    return did, r.json()


def test_defaults_when_only_goal_saved(client):
    did = ready_dataset(client)
    t = client.get(f"/api/datasets/{did}/training").json()
    assert t["model"] is None and set(t["defaults_applied"]) == {"split_policy", "evaluation"}
    assert t["split_policy"] == {"method": "chronological", "train": 0.7, "val": 0.15, "test": 0.15, "gap": "auto"}
    assert t["evaluation"]["metrics"] == ["mae", "rmse"] and t["evaluation"]["baseline"] == "naive_last_value"
    assert t["train_budget"] == {"mode": "all"}
    ids = {m["plugin_id"]: m for m in t["models"]}
    assert ids["timeseries.forecasting.xgboost"]["plugin_version"] == "1.0.0"
    assert ids["timeseries.forecasting.xgboost"]["status_label"] == "계획 미리보기 · 실행 불가"
    assert "MODEL_REQUIRED" in {i["code"] for i in t["issues"]}


def test_recommended_model_and_preserved_s2(client):
    did, plan = with_recommendation(client)
    t = client.get(f"/api/datasets/{did}/training").json()
    rec = [m for m in t["models"] if m["recommended"]]
    assert [m["plugin_id"] for m in rec] == ["timeseries.forecasting.xgboost"] and t["model"]["plugin_id"] == rec[0]["plugin_id"]
    assert t["preprocessing"] == ["세션 분리", "리샘플링", "lag 피처"] and t["excluded"] == ["결측 처리"]
    assert t["defaults_applied"] == []


def test_save_keeps_goal_preprocessing_and_history(client):
    did, before = with_recommendation(client)
    t0 = client.get(f"/api/datasets/{did}/training").json()
    r = client.put(f"/api/datasets/{did}/training", json={**S3, "expected_revision": t0["revision"]})
    assert r.status_code == 200, r.json()
    t = r.json()
    assert t["revision"] == t0["revision"] + 1
    assert t["train_budget"] == {"mode": "recent_days", "days": 30}
    assert t["split_policy"]["train"] == 0.6 and t["evaluation"]["per_group"] is True
    after = client.get(f"/api/datasets/{did}/plan").json()
    for k in ("goal", "steps", "excluded", "recommendation", "source"):
        assert after[k] == before[k], k
    # 새로 연 것처럼 다시 읽어도 같은 값
    again = client.get(f"/api/datasets/{did}/training").json()
    assert {k: again[k] for k in ("model", "train_budget", "split_policy", "evaluation")} == \
           {k: t[k] for k in ("model", "train_budget", "split_policy", "evaluation")}
    # 이후 S2 저장도 S3 설정을 지우지 않는다
    steps = [{k: s[k] for k in ("plugin_id", "plugin_version", "params", "reason")} for s in after["steps"]]
    client.put(f"/api/datasets/{did}/plan", json={"expected_revision": after["revision"], "steps": steps[:2],
                                                  "excluded": [{k: s[k] for k in ("plugin_id", "plugin_version", "params", "reason")} for s in after["excluded"]]})
    assert client.get(f"/api/datasets/{did}/training").json()["train_budget"] == {"mode": "recent_days", "days": 30}


@pytest.mark.parametrize("change,path,msg", [
    ({"split_policy": {"method": "chronological", "train": 0.7, "val": 0.2, "test": 0.2}}, "split_policy", "합이 100%"),
    ({"split_policy": {"method": "chronological", "train": 1.2, "val": 0.0, "test": 0.1}}, "split_policy.train", "100%보다 작아야"),
    ({"train_budget": {"mode": "recent_days", "days": 0}}, "train_budget.days", "1 이상"),
    ({"train_budget": {"mode": "recent_days", "days": None}}, "train_budget.days", "정수를 입력"),
    ({"evaluation": {"kind": "timeseries", "metrics": []}}, "evaluation.metrics", "하나 이상"),
    ({"split_policy": {"method": "random"}}, "split_policy", "허용되지 않는"),
])
def test_format_errors_are_located_and_not_saved(client, change, path, msg):
    did = ready_dataset(client)
    r = client.put(f"/api/datasets/{did}/training", json={**S3, **change, "expected_revision": 1})
    assert r.status_code == 422 and r.json()["code"] == "TRAINING_INVALID"
    issue = r.json()["issues"][0]
    assert issue["path"] == path and msg in issue["message_ko"], issue
    v = client.post(f"/api/datasets/{did}/training/validate", json={**S3, **change}).json()["issues"]
    assert v[0]["path"] == path
    assert client.get(f"/api/datasets/{did}/training").json()["revision"] == 1


def test_bad_model_param_saved_with_issue_and_unknown_model_blocked(client):
    did = ready_dataset(client)
    bad = {**S3, "model": {**XGB, "params": {"max_depth": 100}}}
    r = client.put(f"/api/datasets/{did}/training", json={**bad, "expected_revision": 1})
    assert r.status_code == 200
    assert any(i["path"] == "model.params.max_depth" and i["code"] == "INVALID_PARAM" for i in r.json()["issues"])
    unknown = {**S3, "model": {"plugin_id": "timeseries.forecasting.prophet", "plugin_version": "1.0.0", "params": {}}}
    r = client.put(f"/api/datasets/{did}/training", json={**unknown, "expected_revision": 2})
    assert r.status_code == 422 and r.json()["issues"][0]["code"] == "UNKNOWN_PLUGIN"


def test_per_group_requires_group_column(client):
    did = ready_dataset(client, goal={**GOAL, "group_col": None})
    r = client.put(f"/api/datasets/{did}/training", json={**S3, "expected_revision": 1})
    assert any(i["code"] == "EVALUATION_GROUP_MISSING" and i["path"] == "evaluation.per_group"
               for i in r.json()["issues"])


def test_revision_conflict(client):
    did = ready_dataset(client)
    assert client.put(f"/api/datasets/{did}/training", json={**S3, "expected_revision": 1}).status_code == 200
    r = client.put(f"/api/datasets/{did}/training", json={**S3, "expected_revision": 1})
    assert r.status_code == 409 and r.json()["code"] == "REVISION_CONFLICT"


def test_no_approval_from_preview_and_settings_reach_snapshot_when_ready(client, tmp_path):
    did, _ = with_recommendation(client)
    t = client.get(f"/api/datasets/{did}/training").json()
    client.put(f"/api/datasets/{did}/training", json={**S3, "expected_revision": t["revision"]})
    plans = client.app.state.recs.plans
    draft_id, _, rev = plans._require_plan(did)
    with pytest.raises(ApprovalError):  # 미구현 플러그인: 승인 불가 유지
        plans.approve(draft_id, rev, approved_by="u")
    with TestClient(create_app(tmp_path / "ws2", serve_frontend=False, profile_in_process=False,
                               registry=ready_registry())) as c2:
        did2 = ready_dataset(c2)
        c2.put(f"/api/datasets/{did2}/training", json={**S3, "expected_revision": 1})
        p2 = c2.app.state.recs.plans
        d2, _, r2 = p2._require_plan(did2)
        receipt = p2.approve(d2, r2, approved_by="u")
        snap = p2.get_snapshot(receipt["plan_id"])
        assert snap.immutable_config["train_budget"] == {"mode": "recent_days", "days": 30}
        assert snap.immutable_config["split_policy"]["train"] == 0.6
