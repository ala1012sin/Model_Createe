"""S2 전처리 — 별칭 추천 요청, 별칭 복원, 미리보기/실제 경로, 편집 저장·충돌, 승인 차단."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import mlworkbench.app.recommend_service as rs  # noqa: E402
from mlworkbench.api import create_app  # noqa: E402
from mlworkbench.app.recommend_service import AliasBook, RecommendationRejected  # noqa: E402
from mlworkbench.core.contracts import DraftPlan, execution_view  # noqa: E402
from mlworkbench.core.errors import ApprovalError  # noqa: E402
from mlworkbench.core.hashing import sha256_of  # noqa: E402
from mlworkbench.core.registry import PluginRegistry, builtin_registry  # noqa: E402
from mlworkbench.providers.mock import MockProvider  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_csv_dataset import LEAKY_HEADERS, SECRET_VALUES, leaky_csv, sensor_csv  # noqa: E402

GOAL = {"timestamp_col": "timestamp", "target_col": "sensor_1", "group_col": "device_id", "horizons": ["15min", "1h"]}


def ready_registry() -> PluginRegistry:
    return PluginRegistry(s.model_copy(update={"status": "ready", "reason_unavailable": None})
                          for s in builtin_registry(with_adapters=False).all())


def preview_registry() -> PluginRegistry:
    """구현 어댑터 없이 명세만 있는 내장 카탈로그(모두 미구현) — 계획 미리보기 흐름 검증용."""
    return builtin_registry(with_adapters=False)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False,
                               registry=preview_registry())) as c:
        c.tmp = tmp_path
        yield c


def upload(c, name, data):
    r = c.post("/api/datasets", content=data, headers={"X-File-Name": quote(name)})
    did = r.json()["dataset_id"]
    for _ in range(400):
        d = c.get(f"/api/datasets/{did}").json()
        if d["state"] in ("PROFILED", "PROFILE_FAILED", "UPLOAD_FAILED"):
            return d
        time.sleep(0.02)
    raise AssertionError


def ready_dataset(c, goal=GOAL, name="sensor_A.csv", data=None):
    d = upload(c, name, data or sensor_csv(60))
    assert c.put(f"/api/datasets/{d['dataset_id']}/goal", json=goal).status_code == 200
    return d["dataset_id"]


def use_provider(monkeypatch, **kw):
    monkeypatch.setattr(rs, "MockProvider", lambda **_: MockProvider(allow_unready=True, **kw))


# --- 진입 조건 -------------------------------------------------------------
def test_plan_requires_saved_goal(client):
    d = upload(client, "a.csv", sensor_csv(10))
    for r in (client.get(f"/api/datasets/{d['dataset_id']}/plan"),
              client.post(f"/api/datasets/{d['dataset_id']}/recommendation")):
        assert r.status_code == 409 and r.json()["code"] == "GOAL_REQUIRED"


# --- 추천 요청: 별칭만, 원본 없음 ------------------------------------------------
def test_request_uses_aliases_only_and_no_raw_data(client):
    leaky_goal = {"timestamp_col": "measured_at", "target_col": LEAKY_HEADERS[0], "group_col": "고객명",
                  "horizons": ["1h"]}
    did = ready_dataset(client, goal=leaky_goal, name="기밀_고객_목록.csv", data=leaky_csv())
    r = client.post(f"/api/datasets/{did}/recommendation")
    assert r.status_code == 200, r.json()
    req = client.app.state.recs.last_request
    blob = json.dumps(req.model_dump(mode="json"), ensure_ascii=False)
    for forbidden in LEAKY_HEADERS + SECRET_VALUES + ["기밀_고객_목록", str(client.tmp), "source.csv", "2026-02-0"]:
        assert forbidden not in blob, forbidden
    assert req.goal.model_dump() == {"modality": "timeseries", "task": "forecasting", "timestamp_col": "column_5",
                                     "target_col": "column_1", "group_col": "column_4", "horizons": ["1h"]}
    # 화면용 계획은 원래 이름으로 복원
    applied = client.post(f"/api/datasets/{did}/plan/apply",
                          json={"recommendation_id": r.json()["recommendation_id"], "expected_revision": 1}).json()
    assert applied["goal"]["target_col"] == LEAKY_HEADERS[0] and applied["goal"]["group_col"] == "고객명"


def test_column_params_restored_from_same_version_aliases(client, monkeypatch):
    did = ready_dataset(client)

    class ColumnProvider(MockProvider):
        async def generate_raw(self, request, *, schema=None):
            p = self._proposal(request)
            p["preprocessing"].append({"plugin_id": "ts.diff", "plugin_version": "1.0.0",
                                       "params": {"periods": 1, "columns": ["column_3"]}, "reason": "차분"})
            return p

    monkeypatch.setattr(rs, "MockProvider", lambda **_: ColumnProvider(allow_unready=True))
    rid = client.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
    plan = client.post(f"/api/datasets/{did}/plan/apply", json={"recommendation_id": rid, "expected_revision": 1}).json()
    diff = next(s for s in plan["steps"] if s["plugin_id"] == "ts.diff")
    assert diff["params"]["columns"] == ["sensor_1"]


@pytest.mark.parametrize("scenario,code", [("unknown_alias", "ALIAS_UNKNOWN"), ("raw_header", "ALIAS_UNKNOWN"),
                                           ("other_goal", "RECOMMENDATION_GOAL_MISMATCH"),
                                           ("bad_json", "PROVIDER_OUTPUT_INVALID")])
def test_bad_responses_rejected(client, monkeypatch, scenario, code):
    did = ready_dataset(client)
    use_provider(monkeypatch, scenario=scenario)
    r = client.post(f"/api/datasets/{did}/recommendation")
    assert r.status_code == 422 and r.json()["code"] == code and r.json()["message_ko"]
    assert client.get(f"/api/datasets/{did}/plan").json()["revision"] == 1  # 초안 변화 없음


def test_alias_book_rejects_other_version():
    from mlworkbench.core.contracts import ColumnProfile, LocalProfile

    lp = LocalProfile(dataset_id="ds_a", dataset_version="v1", profile_version=1, encoding="utf-8", delimiter=",",
                      row_count=1, column_count=1,
                      columns=[ColumnProfile(alias="column_1", name="t", dtype="numeric", null_count=0, null_pct=0,
                                             distinct_count=1)])
    book = AliasBook.of(lp)
    assert book.unalias("column_1") == "t"
    with pytest.raises(RecommendationRejected):
        book.unalias("column_2")  # 다른 버전(열이 더 많던 파일)의 별칭


def test_stale_recommendation_rejected(client):
    did = ready_dataset(client)
    rid = client.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
    with client.app.state.recs.ws.session() as conn:
        conn.execute("UPDATE recommendations SET dataset_version='sha256:old' WHERE recommendation_id=?", (rid,))
    r = client.post(f"/api/datasets/{did}/plan/apply", json={"recommendation_id": rid, "expected_revision": 1})
    assert r.status_code == 409 and r.json()["code"] == "RECOMMENDATION_STALE"
    # 목표가 바뀐 뒤의 추천 적용도 거부
    rid2 = client.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
    client.put(f"/api/datasets/{did}/goal", json={**GOAL, "horizons": ["30min"]})
    r = client.post(f"/api/datasets/{did}/plan/apply", json={"recommendation_id": rid2, "expected_revision": 2})
    assert r.status_code == 409 and r.json()["code"] == "RECOMMENDATION_STALE"


# --- 미리보기 경로 vs 실제 경로 --------------------------------------------------
def test_preview_mode_marks_unready_and_blocks_approval(client):
    did = ready_dataset(client)
    rec = client.post(f"/api/datasets/{did}/recommendation").json()
    assert rec["mode"] == "preview" and rec["source_label"] == "Mock · 오프라인 (계획 미리보기)"
    assert [s["plugin_id"] for s in rec["steps"]] == ["ts.session_split", "ts.resample", "ts.missing", "ts.lag"]
    assert all(s["status_label"] == "계획 미리보기 · 실행 불가" and s["reason"] and s["description_ko"]
               for s in rec["steps"])
    plan = client.post(f"/api/datasets/{did}/plan/apply",
                       json={"recommendation_id": rec["recommendation_id"], "expected_revision": 1}).json()
    assert plan["revision"] == 2 and plan["recommendation"]["mode"] == "preview"
    assert {i["code"] for i in plan["issues"]} == {"PLUGIN_NOT_READY"}
    plans = client.app.state.recs.plans
    with pytest.raises(ApprovalError) as e:
        plans.approve(plan["draft_id"], plan["revision"], approved_by="u")
    assert "PLUGIN_NOT_READY" in {i.code for i in e.value.issues}


def test_ready_mode_uses_gateway_with_ready_catalog(tmp_path):
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False,
                               registry=ready_registry())) as c:
        did = ready_dataset(c)
        rec = c.post(f"/api/datasets/{did}/recommendation").json()
        assert rec["mode"] == "ready" and rec["source_label"] == "Mock · 오프라인"
        assert all(s["status_label"] == "실행 가능" for s in rec["steps"])
        assert all(item["ready"] for item in c.app.state.recs.last_request.catalog)


def test_unknown_tool_in_recommendation_is_shown_but_not_applied(client, monkeypatch):
    did = ready_dataset(client)
    use_provider(monkeypatch, scenario="unknown_tool")
    rec = client.post(f"/api/datasets/{did}/recommendation").json()
    unk = [s for s in rec["steps"] if not s["registered"]]
    assert [s["plugin_id"] for s in unk] == ["ts.future_value"] and unk[0]["status_label"] == "등록되지 않은 도구"
    plan = client.post(f"/api/datasets/{did}/plan/apply",
                       json={"recommendation_id": rec["recommendation_id"], "expected_revision": 1}).json()
    assert "ts.future_value" not in [s["plugin_id"] for s in plan["steps"]]
    assert plan["skipped"][0]["plugin_id"] == "ts.future_value" and "등록되지 않은" in plan["skipped"][0]["reason_ko"]


# --- 편집 저장 -------------------------------------------------------------
def step(pid, **params):
    return {"plugin_id": pid, "plugin_version": "1.0.0", "params": params}


def test_edit_save_reload_and_issue_paths(client):
    did = ready_dataset(client)
    url = f"/api/datasets/{did}/plan"
    body = {"expected_revision": 1, "source": "manual",
            "steps": [step("ts.resample", frequency="15min", aggregation="mean"), step("ts.session_split", gap="30min"),
                      step("ts.lag", lags=[0, 2])],
            "excluded": [step("ts.scale", method="standard")]}
    r = client.put(url, json=body)
    assert r.status_code == 200
    codes = {(i["code"], i["path"].split(".")[0]) for i in r.json()["issues"]}
    assert ("ORDER_VIOLATION", "preprocessing[1]") in codes and ("INVALID_PARAM", "preprocessing[2]") in codes
    again = client.get(url).json()
    assert [s["plugin_id"] for s in again["steps"]] == ["ts.resample", "ts.session_split", "ts.lag"]
    assert again["steps"][2]["params"] == {"lags": [0, 2]} and [s["plugin_id"] for s in again["excluded"]] == ["ts.scale"]
    assert again["source"] == "manual" and again["revision"] == 2
    assert "ts.scale" not in [c["plugin_id"] for c in again["catalog"]]
    v = client.post(f"{url}/validate", json={"steps": [step("ts.session_split", gap="30min")]}).json()
    assert {i["code"] for i in v["issues"] if i["severity"] == "error"} == {"PLUGIN_NOT_READY"}


def test_manual_mode_clears_recommendation_source(client):
    did = ready_dataset(client)
    rid = client.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
    plan = client.post(f"/api/datasets/{did}/plan/apply", json={"recommendation_id": rid, "expected_revision": 1}).json()
    assert plan["recommendation"]["recommendation_id"] == rid
    manual = client.put(f"/api/datasets/{did}/plan", json={"expected_revision": plan["revision"], "steps": [],
                                                          "source": "manual"}).json()
    assert manual["source"] == "manual" and manual["recommendation"] is None and manual["steps"] == []
    # 수동 저장 이후 일반 편집 저장은 출처를 그대로 둔다
    again = client.put(f"/api/datasets/{did}/plan", json={"expected_revision": manual["revision"],
                                                         "steps": [step("ts.session_split", gap="30min")]}).json()
    assert again["source"] == "manual"


def test_unregistered_or_incompatible_tool_cannot_be_saved(client):
    did = ready_dataset(client)
    url = f"/api/datasets/{did}/plan"
    for bad in (step("ts.future_value"), step("vision.resize", height=64, width=64)):
        r = client.put(url, json={"expected_revision": 1, "steps": [bad]})
        assert r.status_code == 422 and r.json()["code"] == "PLAN_NOT_APPLICABLE"
        assert r.json()["issues"][0]["path"].startswith("preprocessing[0]")
    r = client.put(url, json={"expected_revision": 1, "steps": [], "excluded": [step("ts.future_value")]})
    assert r.status_code == 422 and r.json()["issues"][0]["path"].startswith("excluded_steps[0]")
    assert client.get(url).json()["revision"] == 1


def test_revision_conflict(client):
    did = ready_dataset(client)
    url = f"/api/datasets/{did}/plan"
    assert client.put(url, json={"expected_revision": 1, "steps": [step("ts.session_split", gap="30min")]}).status_code == 200
    r = client.put(url, json={"expected_revision": 1, "steps": []})  # 다른 탭의 오래된 편집
    assert r.status_code == 409 and r.json()["code"] == "REVISION_CONFLICT"
    rid = client.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
    r = client.post(f"{url}/apply", json={"recommendation_id": rid, "expected_revision": 1})
    assert r.status_code == 409 and r.json()["code"] == "REVISION_CONFLICT"


def test_excluded_steps_do_not_change_execution_hash():
    base = DraftPlan(dataset_id="ds_x", modality="timeseries", task="forecasting")
    a = DraftPlan.model_validate({**base.model_dump(),
                                  "excluded_steps": [{"plugin_id": "ts.scale", "plugin_version": "1.0.0"}]})
    assert a.excluded_steps and sha256_of(execution_view(base)) == sha256_of(execution_view(a))


def test_goal_change_keeps_preprocessing(client):
    did = ready_dataset(client)
    client.put(f"/api/datasets/{did}/plan", json={"expected_revision": 1, "steps": [step("ts.session_split", gap="30min")]})
    client.put(f"/api/datasets/{did}/goal", json={**GOAL, "horizons": ["30min"]})
    plan = client.get(f"/api/datasets/{did}/plan").json()
    assert plan["goal"]["horizons"] == ["30min"] and [s["plugin_id"] for s in plan["steps"]] == ["ts.session_split"]
