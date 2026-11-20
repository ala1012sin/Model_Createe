"""Mock 추천 → 초안 → 검증 → 승인 → 제출, 그리고 승인 경계 (CONTRACTS §5, §8, §11 D-10)."""

import sqlite3

import pytest

from conftest import mutate, run
from mlworkbench.core.contracts import DatasetState, DraftPlan, RunState
from mlworkbench.core.errors import ApprovalError, StateTransitionError, SubmitRejected, ToolDenied
from mlworkbench.core.security import find_sensitive
from mlworkbench.mcp.catalog import RECOMMENDATION_ALLOWLIST, ToolFacade, safe_profile_from_manifest
from mlworkbench.providers.config import ProvidersFile
from mlworkbench.providers.gateway import ProviderGateway
from mlworkbench.providers.mock import MockProvider

PF = ProvidersFile.model_validate({"active_provider": "offline_test",
                                   "profiles": {"offline_test": {"kind": "mock", "model": "m", "auth_mode": "none"}}})


def test_mock_proposal_to_queued_run(services, ready_reg, ts_manifest):
    ds, plans, jobs = services
    gw = ProviderGateway(PF, ready_reg)
    from mlworkbench.core.contracts import TimeseriesGoal
    goal = TimeseriesGoal(timestamp_col="timestamp", target_col="sensor_1", group_col="device_id", horizons=["15min"])
    req = gw.build_request(safe_profile=safe_profile_from_manifest(ts_manifest), task="forecasting", goal=goal)
    assert find_sensitive(req.model_dump(mode="json")) == []
    proposal = run(gw.generate_plan(req, provider=MockProvider()))
    draft_id, rev = plans.create_draft(proposal.to_draft())
    assert plans.validate_draft(draft_id).ok
    receipt = plans.approve(draft_id, rev, approved_by="local_user")
    out = jobs.submit(plan_id=receipt["plan_id"], approval_id=receipt["approval_id"])
    assert out["state"] == "QUEUED" and not out["duplicate"]
    snap = plans.get_snapshot(receipt["plan_id"])
    assert snap.dataset_version == ts_manifest.version
    assert "timeseries.forecasting.xgboost@1.0.0" in snap.plugin_versions
    assert snap.immutable_config["train_budget"] == {"mode": "all"}
    assert (plans.ws.plan_dir(receipt["plan_id"]) / "approved_snapshot.yaml").exists()


def test_llm_unknown_tool_proposal_cannot_be_approved(services, ready_reg, ts_manifest, ts_plan_dict):
    _, plans, _ = services
    gw = ProviderGateway(PF, ready_reg)
    req = gw.build_request(safe_profile=safe_profile_from_manifest(ts_manifest), task="forecasting",
                           goal=DraftPlan.model_validate(ts_plan_dict).user_goal)
    proposal = run(gw.generate_plan(req, provider=MockProvider(scenario="unknown_tool")))
    draft_id, rev = plans.create_draft(proposal.to_draft())
    with pytest.raises(ApprovalError) as e:
        plans.approve(draft_id, rev, approved_by="u")
    assert "UNKNOWN_PLUGIN" in {i.code for i in e.value.issues}


def test_submit_without_or_with_wrong_approval_rejected(services, approved):
    _, _, jobs = services
    _, _, receipt = approved
    for pid, aid in ((receipt["plan_id"], ""), (receipt["plan_id"], "approve_forged"), ("plan_other", receipt["approval_id"])):
        with pytest.raises(SubmitRejected):
            jobs.submit(plan_id=pid, approval_id=aid)
    assert jobs.count_runs() == 0


def test_resubmit_is_idempotent(services, approved):
    _, _, jobs = services
    _, _, r = approved
    a = jobs.submit(plan_id=r["plan_id"], approval_id=r["approval_id"])
    b = jobs.submit(plan_id=r["plan_id"], approval_id=r["approval_id"])
    assert a["run_id"] == b["run_id"] and b["duplicate"] and jobs.count_runs() == 1


def test_execution_edit_invalidates_approval_but_reason_edit_does_not(services, approved, ts_plan_dict):
    _, plans, jobs = services
    draft_id, rev, r = approved
    info = plans.update_draft(draft_id, DraftPlan.model_validate(
        mutate(ts_plan_dict, lambda d: d["preprocessing"][0].update(reason="설명만 수정"))), expected_revision=rev)
    assert not info["execution_changed"] and info["approvals_invalidated"] == 0
    info = plans.update_draft(draft_id, DraftPlan.model_validate(
        mutate(ts_plan_dict, lambda d: d.update(train_budget={"mode": "recent_days", "days": 7}))),
        expected_revision=info["revision"])
    assert info["execution_changed"] and info["approvals_invalidated"] == 1
    with pytest.raises(SubmitRejected) as e:
        jobs.submit(plan_id=r["plan_id"], approval_id=r["approval_id"])
    assert e.value.code == "APPROVAL_INVALIDATED"
    # 새 확정 후에는 제출 가능
    r2 = plans.approve(draft_id, info["revision"], approved_by="u")
    assert jobs.submit(plan_id=r2["plan_id"], approval_id=r2["approval_id"])["state"] == "QUEUED"


def test_stale_revision_cannot_be_approved(services, approved, ts_plan_dict):
    _, plans, _ = services
    draft_id, rev, _ = approved
    plans.update_draft(draft_id, DraftPlan.model_validate(ts_plan_dict), expected_revision=rev)
    with pytest.raises(ApprovalError):
        plans.approve(draft_id, rev, approved_by="u")
    with pytest.raises(StateTransitionError):
        plans.update_draft(draft_id, DraftPlan.model_validate(ts_plan_dict), expected_revision=rev)


def test_dataset_change_after_approval_rejected(services, approved):
    ds, _, jobs = services
    _, _, r = approved
    ds.transition("ds_example_timeseries", DatasetState.PROFILING)
    ds.transition("ds_example_timeseries", DatasetState.PROFILED, version="sha256:new-content")
    with pytest.raises(SubmitRejected) as e:
        jobs.submit(plan_id=r["plan_id"], approval_id=r["approval_id"])
    assert e.value.code == "DATASET_CHANGED"


def test_plugin_unavailable_after_approval_rejected(services, approved, ready_reg):
    _, _, jobs = services
    _, _, r = approved
    spec = ready_reg.get("ts.lag", "1.0.0")
    ready_reg._specs["ts.lag"]["1.0.0"] = spec.model_copy(update={"status": "missing_dependency"})
    with pytest.raises(SubmitRejected) as e:
        jobs.submit(plan_id=r["plan_id"], approval_id=r["approval_id"])
    assert e.value.code == "PLUGIN_UNAVAILABLE"


def test_tampered_snapshot_rejected(services, approved):
    _, plans, jobs = services
    _, _, r = approved
    with plans.ws.session() as conn:
        row = conn.execute("SELECT snapshot_json FROM approvals WHERE plan_id=?", (r["plan_id"],)).fetchone()
        conn.execute("UPDATE approvals SET snapshot_json=? WHERE plan_id=?",
                     (row["snapshot_json"].replace('"n_estimators":300', '"n_estimators":5000'), r["plan_id"]))
    with pytest.raises(SubmitRejected) as e:
        jobs.submit(plan_id=r["plan_id"], approval_id=r["approval_id"])
    assert e.value.code == "PLAN_HASH_MISMATCH"


def test_approval_token_not_stored_in_plaintext(services, approved):
    _, plans, _ = services
    _, _, r = approved
    raw = plans.ws.db_path.read_bytes()
    assert r["approval_id"].encode() not in raw
    snap_yaml = (plans.ws.plan_dir(r["plan_id"]) / "approved_snapshot.yaml").read_text(encoding="utf-8")
    assert r["approval_id"] not in snap_yaml


def test_mcp_recommendation_context_cannot_submit(services, approved, ready_reg):
    ds, _, jobs = services
    _, _, r = approved
    facade = ToolFacade(ready_reg, datasets=ds, jobs=jobs)
    names = {t["name"] for t in facade.list_tools("recommendation")}
    assert names == set(RECOMMENDATION_ALLOWLIST) and "experiment.submit" not in names
    with pytest.raises(ToolDenied):
        facade.call("experiment.submit", {"approved_plan_id": r["plan_id"], "approval_id": r["approval_id"]},
                    context="recommendation")
    assert jobs.count_runs() == 0
    prof = facade.call("dataset.get_profile", {"dataset_id": "ds_example_timeseries"}, context="recommendation")
    assert find_sensitive(prof) == [] and "local_path" not in prof
    # 호스트(UI 승인 경로)는 서버 검증을 거쳐 제출 가능
    out = facade.call("experiment.submit", {"approved_plan_id": r["plan_id"], "approval_id": r["approval_id"]}, context="host")
    assert out["state"] == "QUEUED"


def test_unapproved_draft_has_no_submit_path(services, ts_plan_dict):
    _, plans, jobs = services
    draft_id, _ = plans.create_draft(DraftPlan.model_validate(ts_plan_dict))
    res = plans.validate_draft(draft_id, mode="final")
    assert res.ok and res.plan_hash  # 검증 해시가 있어도 승인이 아니다
    with pytest.raises(SubmitRejected):
        jobs.submit(plan_id=draft_id, approval_id=res.plan_hash)
