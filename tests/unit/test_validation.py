"""PlanValidator — 미등록·미설치·비호환·잘못된 파라미터·순서·누수 위험 차단 (D-06/D-08/D-10)."""

import pytest

from mlworkbench.core.contracts import DatasetState

from conftest import mutate
from mlworkbench.core.validation import PlanValidator


def V(reg, plan, mode="final", ds=None):
    return PlanValidator(reg).validate(plan, mode=mode, dataset=ds)


def test_valid_plan_passes_final_and_gets_hash(ready_reg, ts_plan_dict, ts_manifest):
    res, exe = V(ready_reg, ts_plan_dict, ds=ts_manifest)
    assert res.ok, res.issues
    assert res.plan_hash.startswith("sha256:") and exe.dataset_version == ts_manifest.version


def test_unregistered_plugin_from_llm_blocked(ready_reg, ts_plan_dict, ts_manifest):
    plan = mutate(ts_plan_dict, lambda d: d["preprocessing"].append(
        {"plugin_id": "ts.future_value", "plugin_version": "1.0.0", "params": {}}))
    res, exe = V(ready_reg, plan, ds=ts_manifest)
    assert not res.ok and exe is None and "UNKNOWN_PLUGIN" in res.codes()
    issue = next(i for i in res.issues if i.code == "UNKNOWN_PLUGIN")
    assert issue.path == "preprocessing[4].plugin_id" and issue.message_ko


def test_not_installed_plugin_blocked(builtin_reg, ts_plan_dict, ts_manifest):
    res, _ = V(builtin_reg, ts_plan_dict, ds=ts_manifest)
    assert "PLUGIN_NOT_READY" in res.codes() and not res.ok


def test_wrong_version_and_kind_and_modality(ready_reg, ts_plan_dict, ts_manifest):
    plan = mutate(ts_plan_dict, lambda d: d["preprocessing"][0].update(plugin_version="9.0.0"))
    assert "UNKNOWN_VERSION" in V(ready_reg, plan, ds=ts_manifest)[0].codes()
    plan = mutate(ts_plan_dict, lambda d: d.update(model={"plugin_id": "ts.lag", "plugin_version": "1.0.0", "params": {"lags": [1]}}))
    assert "WRONG_PLUGIN_KIND" in V(ready_reg, plan, ds=ts_manifest)[0].codes()
    plan = mutate(ts_plan_dict, lambda d: d.update(model={"plugin_id": "vision.anomaly.patchcore", "plugin_version": "1.0.0", "params": {}}))
    assert "INCOMPATIBLE_MODALITY" in V(ready_reg, plan, ds=ts_manifest)[0].codes()


@pytest.mark.parametrize("params,code", [
    ({"lags": [-1]}, "INVALID_PARAM"),          # 미래 값 참조
    ({"lags": [1], "cmd": "rm -rf"}, "INVALID_PARAM"),
    ({"lags": "1,2"}, "INVALID_PARAM"),
    ({}, "PARAM_REQUIRED"),
])
def test_bad_params_blocked(ready_reg, ts_plan_dict, ts_manifest, params, code):
    plan = mutate(ts_plan_dict, lambda d: d["preprocessing"][2].update(params=params))
    res, _ = V(ready_reg, plan, ds=ts_manifest)
    assert code in res.codes() and not res.ok


def test_rolling_must_shift(ready_reg, ts_plan_dict, ts_manifest):
    plan = mutate(ts_plan_dict, lambda d: d["preprocessing"][3]["params"].update(shift_first=False))
    assert "INVALID_PARAM" in V(ready_reg, plan, ds=ts_manifest)[0].codes()


def test_order_violation_blocked(ready_reg, ts_plan_dict, ts_manifest):
    plan = mutate(ts_plan_dict, lambda d: d["preprocessing"].reverse())
    res, _ = V(ready_reg, plan, ds=ts_manifest)
    assert "ORDER_VIOLATION" in res.codes()
    assert any(i.path.startswith("preprocessing[") for i in res.issues if i.code == "ORDER_VIOLATION")


def test_manual_draft_without_model_is_editable_but_not_final(ready_reg, ts_plan_dict, ts_manifest):
    plan = mutate(ts_plan_dict, lambda d: d.pop("model"))
    draft, _ = V(ready_reg, plan, mode="draft", ds=ts_manifest)
    assert draft.ok and any(i.code == "MODEL_REQUIRED" and i.severity == "info" for i in draft.issues)
    final, exe = V(ready_reg, plan, mode="final", ds=ts_manifest)
    assert not final.ok and exe is None and "MODEL_REQUIRED" in final.codes()


def test_draft_tolerates_unfinished_goal(ready_reg, ts_plan_dict, ts_manifest):
    plan = mutate(ts_plan_dict, lambda d: d["user_goal"].update(target_col=None, horizons=[]))
    assert V(ready_reg, plan, mode="draft", ds=ts_manifest)[0].ok
    assert "GOAL_FIELD_REQUIRED" in V(ready_reg, plan, mode="final", ds=ts_manifest)[0].codes()


def test_goal_column_must_exist(ready_reg, ts_plan_dict, ts_manifest):
    plan = mutate(ts_plan_dict, lambda d: d["user_goal"].update(target_col="sensor_9"))
    assert "GOAL_COLUMN_MISSING" in V(ready_reg, plan, ds=ts_manifest)[0].codes()


def test_budget_and_split_compatibility(ready_reg, ts_plan_dict, vis_plan_dict, ts_manifest, vis_manifest):
    plan = mutate(ts_plan_dict, lambda d: d.update(train_budget={"mode": "n_images", "count": 5}))
    assert "BUDGET_INCOMPATIBLE" in V(ready_reg, plan, ds=ts_manifest)[0].codes()
    plan = mutate(vis_plan_dict, lambda d: d.update(train_budget={"mode": "recent_days", "days": 3}))
    assert "BUDGET_INCOMPATIBLE" in V(ready_reg, plan, ds=vis_manifest)[0].codes()
    plan = mutate(ts_plan_dict, lambda d: d.update(split_policy={"method": "explicit_manifest"}))
    assert "SPLIT_INCOMPATIBLE" in V(ready_reg, plan, ds=ts_manifest)[0].codes()


def test_vision_calibration_and_gt_rules(ready_reg, vis_plan_dict, vis_manifest):
    res, _ = V(ready_reg, vis_plan_dict, ds=vis_manifest)
    assert res.ok and "PIXEL_METRIC_NA" in res.codes()  # GT 없음 → N/A 경고, 0점 아님
    plan = mutate(vis_plan_dict, lambda d: d["split_policy"].update(calibration=None))
    assert "CALIBRATION_REQUIRED" in V(ready_reg, plan, ds=vis_manifest)[0].codes()
    plan = mutate(vis_plan_dict, lambda d: d["split_policy"].update(calibration="train_normal"))
    assert "LEAKAGE_SPLIT_OVERLAP" in V(ready_reg, plan, ds=vis_manifest)[0].codes()


def test_segmentation_requires_labels(ready_reg, vis_plan_dict, vis_manifest):
    def seg(d):
        d["task"] = "segmentation"
        d["user_goal"].update(task="segmentation", train_policy="supervised")
        d["model"] = {"plugin_id": "vision.segmentation.yolo11_seg", "plugin_version": "1.0.0", "params": {}}
        d["evaluation"] = {"kind": "vision_segmentation", "metrics": ["mask_map50"]}
    res, _ = V(ready_reg, mutate(vis_plan_dict, seg), ds=vis_manifest)
    assert "MISSING_REQUIRED_ARTIFACT" in res.codes()


def test_dataset_must_be_profiled_for_final(ready_reg, ts_plan_dict, ts_manifest):
    ds = ts_manifest.model_copy(update={"state": DatasetState.PROFILING, "version": None})
    assert "DATASET_NOT_READY" in V(ready_reg, ts_plan_dict, ds=ds)[0].codes()


def test_schema_errors_are_structured(ready_reg):
    res, _ = V(ready_reg, {"dataset_id": "ds_x", "modality": "audio", "task": "x"})
    assert not res.ok and all(i.code == "SCHEMA_INVALID" and i.path for i in res.issues)


# --- P2 시계열 실행 검증 -------------------------------------------------------
def _p2_draft(**over):
    from mlworkbench.core.contracts import DraftPlan

    body = {"dataset_id": "x", "modality": "timeseries", "task": "forecasting",
            "user_goal": {"modality": "timeseries", "timestamp_col": "t", "target_col": "y", "horizons": ["15min", "30min"]},
            "preprocessing": [{"plugin_id": "ts.resample", "plugin_version": "1.0.0",
                               "params": {"frequency": "15min", "aggregation": "mean"}},
                              {"plugin_id": "ts.missing", "plugin_version": "1.0.0", "params": {"method": "ffill"}}],
            "model": {"plugin_id": "timeseries.forecasting.xgboost", "plugin_version": "1.0.0", "params": {}},
            "split_policy": {"method": "chronological"},
            "evaluation": {"kind": "timeseries", "metrics": ["mae", "rmse"], "baseline": "naive_last_value"}}
    body.update(over)
    return DraftPlan.model_validate(body)


def _codes(draft):
    from mlworkbench.core.registry import builtin_registry
    from mlworkbench.core.validation import PlanValidator

    r, _ = PlanValidator(builtin_registry()).validate(draft, mode="final")
    return {i.code: i for i in r.issues if i.severity == "error"}


def test_p2_legacy_linear_interpolation_explained():
    d = _p2_draft(preprocessing=[{"plugin_id": "ts.missing", "plugin_version": "1.0.0", "params": {"method": "linear"}}])
    i = _codes(d)["LEGACY_PARAM_VALUE"]
    assert i.path == "preprocessing[0].params.method" and "미래" in i.message_ko and "직전 값으로 채움" in i.fix_hint_ko


def test_p2_horizon_must_match_resample_grid():
    errs = _codes(_p2_draft(user_goal={"modality": "timeseries", "timestamp_col": "t", "target_col": "y",
                                       "horizons": ["15min", "20min"]}))
    assert errs["HORIZON_NOT_MULTIPLE"].path == "user_goal.horizons[1]"
    assert "HORIZON_NOT_MULTIPLE" not in _codes(_p2_draft())


def test_p2_unimplemented_baseline_blocked():
    d = _p2_draft(evaluation={"kind": "timeseries", "metrics": ["mae"], "baseline": "seasonal_naive"})
    assert _codes(d)["EVAL_OPTION_NOT_IMPLEMENTED"].path == "evaluation.baseline"


def test_p2_unimplemented_model_still_blocked_with_reason():
    d = _p2_draft(model={"plugin_id": "timeseries.forecasting.lightgbm", "plugin_version": "1.0.0", "params": {}})
    i = _codes(d)["PLUGIN_NOT_READY"]
    assert i.path == "model.plugin_id" and "실행할 수 없습니다" in i.message_ko
