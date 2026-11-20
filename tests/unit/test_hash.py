from conftest import mutate
from mlworkbench.core.validation import PlanValidator


def _hash(reg, plan, ds):
    res, _ = PlanValidator(reg).validate(plan, mode="final", dataset=ds)
    assert res.ok, res.issues
    return res.plan_hash


def test_reason_and_rationale_edits_do_not_change_hash(ready_reg, ts_plan_dict, ts_manifest):
    h = _hash(ready_reg, ts_plan_dict, ts_manifest)
    edited = mutate(ts_plan_dict, lambda d: (d["preprocessing"][0].update(reason="바뀐 설명"), d.update(rationale="새 요약")))
    assert _hash(ready_reg, edited, ts_manifest) == h


def test_execution_changes_change_hash(ready_reg, ts_plan_dict, ts_manifest):
    h = _hash(ready_reg, ts_plan_dict, ts_manifest)
    for fn in (lambda d: d["preprocessing"][2]["params"].update(lags=[1, 2]),
               lambda d: d.update(train_budget={"mode": "all"}),
               lambda d: d["split_policy"].update(train=0.6, val=0.2, test=0.2),
               lambda d: d["evaluation"].update(metrics=["mae"])):
        assert _hash(ready_reg, mutate(ts_plan_dict, fn), ts_manifest) != h


def test_dataset_version_bound_into_hash(ready_reg, ts_plan_dict, ts_manifest):
    other = ts_manifest.model_copy(update={"version": "sha256:other"})
    assert _hash(ready_reg, ts_plan_dict, ts_manifest) != _hash(ready_reg, ts_plan_dict, other)


def test_key_order_irrelevant(ready_reg, ts_plan_dict, ts_manifest):
    reordered = dict(reversed(list(ts_plan_dict.items())))
    assert _hash(ready_reg, reordered, ts_manifest) == _hash(ready_reg, ts_plan_dict, ts_manifest)
