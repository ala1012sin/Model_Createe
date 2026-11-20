"""S6 실험 비교 — 평가 조건(같은 표본·정답·지표)과 조건 차이(실험 변수)를 나눠 보여 주는지."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("xgboost")
pd = pytest.importorskip("pandas")
from fastapi.testclient import TestClient  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_csv_dataset import sensor_csv  # noqa: E402
from test_preprocess import GOAL, upload  # noqa: E402
from test_runs import SETTINGS, start, wait  # noqa: E402


def run_with(c, did, settings, steps=None):
    plan = c.get(f"/api/datasets/{did}/plan").json()
    rev = plan["revision"]
    if steps is not None:
        r = c.put(f"/api/datasets/{did}/plan", json={"expected_revision": rev, "steps": steps, "excluded": []})
        assert r.status_code == 200, r.json()
        rev = r.json()["revision"]
    t = c.put(f"/api/datasets/{did}/training", json={**settings, "expected_revision": rev})
    assert t.status_code == 200, t.json()
    v = c.get(f"/api/datasets/{did}/confirmation").json()
    assert v["error_count"] == 0, v["issues"]
    a = c.post(f"/api/datasets/{did}/approve", json={"expected_revision": v["revision"], "confirmed": True}).json()
    run = start(c, did, a["approval"]).json()
    done = wait(c, run["run_id"])
    assert done["state"] == "SUCCEEDED", (done["error_code"], done["error_message_ko"])
    return done


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    ws = tmp_path_factory.mktemp("cmp") / "ws"
    with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c:
        c.ws = ws
        did = upload(c, "sensor_cmp.csv", sensor_csv(2880))["dataset_id"]
        assert c.put(f"/api/datasets/{did}/goal", json=GOAL).status_code == 200
        rid = c.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
        plan = c.post(f"/api/datasets/{did}/plan/apply", json={"recommendation_id": rid, "expected_revision": 1}).json()
        base_steps = [{k: s[k] for k in ("plugin_id", "plugin_version", "params", "reason")} for s in plan["steps"]]
        a = run_with(c, did, SETTINGS)                                             # 최근 5일, lag
        b = run_with(c, did, {**SETTINGS, "train_budget": {"mode": "all"}})         # 학습량만 다름
        rolling = base_steps + [{"plugin_id": "ts.rolling", "plugin_version": "1.0.0",
                                 "params": {"windows": [4]}, "reason": ""}]
        d = run_with(c, did, SETTINGS, steps=rolling)                               # 전처리만 다름
        split = {**SETTINGS["split_policy"], "train": 0.6, "val": 0.2, "test": 0.2}
        e = run_with(c, did, {**SETTINGS, "split_policy": split}, steps=base_steps)  # 평가 조건 다름
        c.ids = {"a": a["run_id"], "b": b["run_id"], "d": d["run_id"], "e": e["run_id"]}
        c.did = did
        yield c


def cmp(c, *keys):
    return c.get("/api/runs/compare", params=[("run_ids", c.ids[k]) for k in keys])


def test_list_shows_protocol_key_steps_duration(runs):
    c = runs
    items = {x["run_id"]: x for x in c.get("/api/runs").json()["items"]}
    a, b, d, e = (items[c.ids[k]] for k in "abde")
    assert a["protocol_key"] and a["protocol_key"] == b["protocol_key"] == d["protocol_key"] != e["protocol_key"]
    assert a["steps"] == ["세션 분리", "리샘플링", "결측 처리", "lag 피처"] and d["steps"][-1] == "rolling 통계"
    assert isinstance(a["duration_s"], int)


def test_same_protocol_only_condition_diffs(runs):
    c = runs
    r = cmp(c, "a", "b", "d")
    assert r.status_code == 200, r.json()
    v = r.json()
    assert v["reference"] == c.ids["a"] and all(x["same_protocol"] for x in v["runs"])
    assert {x["label_ko"] for x in v["condition_diffs"]} == {"학습량", "전처리"}
    budget = next(x for x in v["condition_diffs"] if x["key"] == "train_budget")
    assert budget["values"] == ["최근 5일", "전체", "최근 5일"]
    assert all(x["comparable_horizons"] == ["15min", "1h"] for x in v["runs"])
    # 지표는 저장된 metrics.json 그대로
    saved = __import__("json").loads((c.ws / "experiments" / c.ids["b"] / "metrics.json").read_text(encoding="utf-8"))
    assert v["runs"][1]["metrics"]["test"]["overall"] == saved["test"]["overall"]
    assert str(c.ws) not in r.text


def test_different_split_is_flagged(runs):
    c = runs
    v = cmp(c, "a", "e").json()
    e = v["runs"][1]
    assert not e["same_protocol"] and e["comparable_horizons"] == []
    keys = {d["key"] for d in e["protocol_diffs"]}
    assert "split" in keys and "test_set" in keys and "test_counts" in keys
    # 조건 차이에는 분할이 없고(평가 조건이므로), 경고 쪽에만 나온다
    assert "split" not in {x["key"] for x in v["condition_diffs"]}


def test_series_aligns_same_samples(runs):
    c = runs
    params = [("run_ids", c.ids["a"]), ("run_ids", c.ids["b"]), ("horizon", "15min"), ("group", "D01")]
    s = c.get("/api/runs/compare/series", params=params).json()
    assert s["points_shown"] == len(s["target_time"]) == len(s["actual"]) == len(s["predicted"][0]) == len(s["predicted"][1])
    assert 0 < s["points_shown"] <= 600
    pred = pd.read_csv(c.ws / "experiments" / c.ids["a"] / "predictions" / "predictions.csv", dtype={"group": str})
    part = pred[(pred.split == "test") & (pred.horizon == "15min") & (pred.group == "D01")].set_index("target_time")
    assert s["points_total"] == len(part)
    t0 = s["target_time"][0]
    assert abs(part.loc[t0, "actual"] - s["actual"][0]) < 1e-9 and abs(part.loc[t0, "predicted"] - s["predicted"][0][0]) < 1e-9


def test_errors(runs):
    c = runs
    assert c.get("/api/runs/compare", params=[("run_ids", c.ids["a"])]).json()["code"] == "COMPARE_COUNT"
    r = c.get("/api/runs/compare", params=[("run_ids", c.ids["a"]), ("run_ids", "run_nope")])
    assert r.status_code == 404 and r.json()["code"] == "RUN_NOT_FOUND"
    r = c.get("/api/runs/compare/series", params=[("run_ids", c.ids["a"]), ("run_ids", c.ids["b"]), ("horizon", "2h")])
    assert r.status_code == 400 and r.json()["code"] == "HORIZON_UNKNOWN"
    # 캐시: 다시 불러도 같은 결과, 예측 파일이 바뀌면 다시 계산
    exp = c.ws / "experiments" / c.ids["a"]
    assert (exp / "compare_key.json").exists()
