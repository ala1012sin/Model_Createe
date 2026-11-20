import pytest
from pydantic import ValidationError

from mlworkbench.core.contracts import (
    BudgetNImages,
    ChronologicalSplit,
    DraftPlan,
    ExecutablePlan,
    PlanProposal,
    RunEvent,
    RunState,
    ThresholdRule,
)
from mlworkbench.providers.config import ProviderConfig


def test_draft_allows_missing_model_but_proposal_requires_it(ts_plan_dict):
    d = dict(ts_plan_dict)
    d.pop("model")
    assert DraftPlan.model_validate(d).model is None
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(d)


def test_train_budget_split_evaluation_are_separate_typed_fields(ts_plan_dict):
    d = DraftPlan.model_validate(ts_plan_dict)
    assert d.train_budget.mode == "recent_days" and d.train_budget.days == 30
    assert isinstance(d.split_policy, ChronologicalSplit)
    assert d.evaluation.kind == "timeseries"
    assert "train_budget" not in d.split_policy.model_dump()


def test_split_fractions_must_sum_to_one():
    with pytest.raises(ValidationError):
        ChronologicalSplit(train=0.8, val=0.15, test=0.15)


def test_budget_and_threshold_bounds():
    with pytest.raises(ValidationError):
        BudgetNImages(count=0)
    with pytest.raises(ValidationError):
        ThresholdRule(method="calibration_quantile")


def test_unknown_fields_rejected(ts_plan_dict):
    bad = dict(ts_plan_dict, exec_code="import os")
    with pytest.raises(ValidationError):
        DraftPlan.model_validate(bad)


def test_execution_config_excludes_reason(ts_plan_dict, ts_manifest):
    d = DraftPlan.model_validate(ts_plan_dict)
    exe = ExecutablePlan(dataset_version=ts_manifest.version, **d.model_dump(exclude={"title", "rationale", "source", "excluded_steps", "recommendation_id"}))
    cfg = exe.execution_config()
    assert all("reason" not in s for s in cfg["preprocessing"]) and "reason" not in cfg["model"]


def test_run_event_progress_bounds():
    RunEvent(run_id="run_x", seq=1, state=RunState.RUNNING, stage="train", progress=None, message_code="X")
    with pytest.raises(ValidationError):
        RunEvent(run_id="run_x", seq=1, state=RunState.RUNNING, stage="train", progress=1.5, message_code="X")
    with pytest.raises(ValidationError):
        RunEvent(run_id="run_x", seq=0, state=RunState.RUNNING, stage="train", message_code="X")


@pytest.mark.parametrize("bad", ["sk-abcdefghijklmnopqrstuvwxyz123456", "my key", "openai_api_key"])
def test_provider_config_accepts_env_name_only(bad):
    with pytest.raises(ValidationError):
        ProviderConfig(id="x", kind="openai", base_url="https://api.openai.com/v1", model="m", api_key_env=bad)


def test_provider_external_detection():
    local = ProviderConfig(id="o", kind="openai_compatible", base_url="http://127.0.0.1:11434/v1", model="m", auth_mode="none")
    remote = ProviderConfig(id="r", kind="openai_compatible", base_url="http://10.0.0.5:8000/v1", model="m", auth_mode="none")
    assert not local.is_external and remote.is_external
