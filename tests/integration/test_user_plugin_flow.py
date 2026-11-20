"""사용자 정의 플러그인 예제(ts.custom.ewm)가 core 수정 없이 S2 → 추천 카탈로그 → 확정 → 실제 학습(worker) → S7까지
같은 경로로 연결되는지, 코드가 바뀌면 승인·저장 모델이 막히는지 확인한다."""

from __future__ import annotations

import json
import os
import shutil
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
from test_inference import apply, predict, result_frame  # noqa: E402
from test_preprocess import GOAL, upload  # noqa: E402
from test_runs import SETTINGS, start, wait  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[2] / "plugins" / "ts_custom_ewm"
EWM = {"plugin_id": "ts.custom.ewm", "plugin_version": "1.0.0", "params": {"spans": [4, 12]}, "reason": ""}


@pytest.fixture(scope="module")
def plugin_root(tmp_path_factory):
    """예제 플러그인 사본(코드를 고치는 테스트용). 모듈 동안 MLWB_PLUGIN_DIRS로 지정."""
    root = tmp_path_factory.mktemp("plugins")
    shutil.copytree(EXAMPLE, root / "ts_custom_ewm")
    old = os.environ.get("MLWB_PLUGIN_DIRS")
    os.environ["MLWB_PLUGIN_DIRS"] = str(root)
    yield root
    if old is None:
        os.environ.pop("MLWB_PLUGIN_DIRS", None)
    else:
        os.environ["MLWB_PLUGIN_DIRS"] = old


@pytest.fixture(autouse=True)
def _no_project_plugins(plugin_root):  # conftest의 '사용자 플러그인 없음'을 이 모듈에서는 예제 사본으로 바꾼다
    os.environ["MLWB_PLUGIN_DIRS"] = str(plugin_root)


def draft_with_ewm(c, name="sensor_ewm.csv"):
    d = upload(c, name, sensor_csv(2880))
    did = d["dataset_id"]
    assert c.put(f"/api/datasets/{did}/goal", json=GOAL).status_code == 200
    rid = c.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
    plan = c.post(f"/api/datasets/{did}/plan/apply", json={"recommendation_id": rid, "expected_revision": 1}).json()
    catalog = {s["plugin_id"]: s for s in plan["catalog"]}
    steps = [{k: s[k] for k in ("plugin_id", "plugin_version", "params", "reason")} for s in plan["steps"]] + [EWM]
    r = c.put(f"/api/datasets/{did}/plan", json={"expected_revision": plan["revision"], "steps": steps, "excluded": []})
    assert r.status_code == 200, r.json()
    t = c.put(f"/api/datasets/{did}/training", json={**SETTINGS, "expected_revision": r.json()["revision"]})
    assert t.status_code == 200
    return did, catalog, r.json()


def approve(c, did):
    v = c.get(f"/api/datasets/{did}/confirmation").json()
    assert v["error_count"] == 0, v["issues"]
    r = c.post(f"/api/datasets/{did}/approve", json={"expected_revision": v["revision"], "confirmed": True})
    return r


@pytest.fixture(scope="module")
def trained(plugin_root, tmp_path_factory):
    ws = tmp_path_factory.mktemp("uplug") / "ws"
    with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c:
        c.ws = ws
        did, catalog, plan = draft_with_ewm(c)
        a = approve(c, did).json()
        run = start(c, did, a["approval"]).json()
        done = wait(c, run["run_id"])
        assert done["state"] == "SUCCEEDED", (done["error_code"], done["error_message_ko"])
        c.did, c.run_id, c.catalog, c.plan = did, run["run_id"], catalog, plan
        yield c


def test_s2_catalog_and_llm_catalog_expose_plugin(trained):
    c = trained
    ewm = c.catalog["ts.custom.ewm"]
    assert ewm["origin"] == "user" and ewm["status"] == "ready" and ewm["display_name_ko"] == "지수 가중 평균(EWM)"
    assert ewm["parameter_schema"]["properties"]["columns"]["x-column"] is True
    card = next(s for s in c.plan["steps"] if s["plugin_id"] == "ts.custom.ewm")
    assert card["status_label"] == "실행 가능" and c.plan["plugin_problems"] == []
    # 추천 단계 LLM이 보는 읽기 전용 카탈로그에도 같은 항목(민감정보 검사 통과, 경로 없음)
    from mlworkbench.mcp.catalog import ToolFacade

    out = ToolFacade(c.app.state.plans.registry).call(
        "catalog.list_preprocessors", {"modality": "timeseries", "task": "forecasting"}, context="recommendation")
    item = next(i for i in out["items"] if i["id"] == "ts.custom.ewm")
    assert item["ready"] and "plugins" not in json.dumps(out, ensure_ascii=False).replace("ts.custom", "")


def test_llm_proposal_with_plugin_validates_and_unknown_custom_is_blocked(trained):
    from mlworkbench.core.validation import PlanValidator

    import yaml

    reg = trained.app.state.plans.registry
    base = yaml.safe_load((Path(__file__).resolve().parents[2] / "examples" / "timeseries_plan.yaml").read_text(encoding="utf-8"))
    base["preprocessing"] = [{"plugin_id": "ts.custom.ewm", "plugin_version": "1.0.0", "params": {"spans": [4]}}]
    base["model"] = {"plugin_id": "timeseries.forecasting.xgboost", "plugin_version": "1.0.0", "params": {}}
    v = PlanValidator(reg)
    r, _ = v.validate(base, mode="draft")
    assert r.ok and not [i for i in r.issues if i.severity == "error"], r.issues
    bad = {**base, "preprocessing": [{"plugin_id": "ts.custom.future", "plugin_version": "1.0.0", "params": {}}]}
    r2, _ = v.validate(bad, mode="draft")
    assert any(i.severity == "error" and i.path.startswith("preprocessing") for i in r2.issues)
    bad_param = {**base, "preprocessing": [{"plugin_id": "ts.custom.ewm", "plugin_version": "1.0.0", "params": {"spans": [1]}}]}
    r3, _ = v.validate(bad_param, mode="draft")
    assert any(i.severity == "error" for i in r3.issues)


def test_trained_with_plugin_and_s7_rebuilds_same_features(trained):
    c = trained
    exp = c.ws / "experiments" / c.run_id
    doc = json.loads((exp / "model" / "pipeline.json").read_text(encoding="utf-8"))
    assert "sensor_1_ewm4" in doc["features"] and "sensor_1_ewm12" in doc["features"]
    code = (Path(os.environ["MLWB_PLUGIN_DIRS"]) / "ts_custom_ewm")
    from mlworkbench.plugins.user_loader import code_hash

    assert doc["plugin_code"] == {"ts.custom.ewm@1.0.0": code_hash(code)}
    env = json.loads((exp / "environment.json").read_text(encoding="utf-8"))
    assert "ts.custom.ewm@1.0.0" in env["plugins"] and env["user_plugin_code"] == doc["plugin_code"]
    assert "과거만 사용 검사 통과" in (exp / "train.log").read_text(encoding="utf-8")
    snap = c.app.state.plans.get_snapshot(c.app.state.jobs.get_record(c.run_id).plan_id)
    assert snap.plugin_code == doc["plugin_code"]
    req = c.get(f"/api/models/{c.run_id}").json()["requirements"]
    assert "12칸" in req["history"]  # EWM span 12가 가장 긴 이력
    assert any(s["name"] == "지수 가중 평균(EWM)" for s in req["steps"])
    target = next(x for x in req["columns"] if x["name"] == "sensor_1")
    assert "EWM span [4, 12]" in target["why"]
    src = (c.ws / "datasets" / c.did / "source.csv").read_bytes()
    app = apply(c, src, "sensor_ewm.csv")
    assert app["state"] == "CHECKED", app["error"]
    assert predict(c, app["application_id"])["state"] == "SUCCEEDED"
    got = result_frame(c, app["application_id"])
    saved = pd.read_csv(exp / "predictions" / "predictions.csv")
    m = saved.merge(got, on=["group", "origin_time", "target_time", "horizon"], suffixes=("", "_new"))
    assert len(m) > 0.9 * len(saved) and (m.predicted - m.predicted_new).abs().max() < 1e-9


def test_code_edit_blocks_approval_submit_and_saved_model(plugin_root, tmp_path):
    """같은 작업 공간을 두 앱이 동시에 쓰지 않도록 이 테스트만의 작업 공간에서 앱을 닫고 다시 연다."""
    ws = tmp_path / "ws"
    py = plugin_root / "ts_custom_ewm" / "plugin.py"
    original = py.read_text(encoding="utf-8")
    try:
        with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c:
            did1, _, _ = draft_with_ewm(c, "a.csv")
            run1 = start(c, did1, approve(c, did1).json()["approval"]).json()
            assert wait(c, run1["run_id"])["state"] == "SUCCEEDED"
            did, _, _ = draft_with_ewm(c, "b.csv")
            a = approve(c, did).json()["approval"]
            py.write_text(original + "\n# 승인 뒤 수정\n", encoding="utf-8")
            r = start(c, did, a)  # 앱 실행 중 파일만 바뀐 경우도 제출 전에 막는다
            assert r.status_code >= 400 and r.json()["code"] == "PLUGIN_CODE_CHANGED", r.json()
            v = c.get(f"/api/datasets/{did}/confirmation").json()
            assert v["approval"]["status"] == "invalidated"  # 화면에서도 이전 확정이 무효로 보인다
            r = c.post(f"/api/datasets/{did}/approve", json={"expected_revision": v["revision"], "confirmed": True})
            assert r.status_code == 400 and r.json()["code"] == "PLUGIN_CODE_CHANGED"  # 다시 시작 전에는 확정 불가
            assert c.app.state.jobs.count_runs() == 1
        # 앱을 다시 시작해도(새 코드를 불러와도) 이전 승인·이전 학습 모델은 쓸 수 없다
        with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c2:
            r = start(c2, did, a)  # 위에서 확정을 다시 시도하며 이전 확정은 무효화됨
            assert r.status_code >= 400 and r.json()["code"] == "APPROVAL_INVALIDATED", r.json()
            src = (ws / "datasets" / did1 / "source.csv").read_bytes()
            got = apply(c2, src, "again.csv", run_id=run1["run_id"])
            assert got["state"] == "CHECK_FAILED" and got["error"]["code"] == "PLUGIN_CODE_CHANGED", got
            # 새 코드로 다시 확정하면 실행할 수 있다
            v = c2.get(f"/api/datasets/{did}/confirmation").json()
            r = c2.post(f"/api/datasets/{did}/approve", json={"expected_revision": v["revision"], "confirmed": True})
            assert r.status_code == 200, r.json()
            run = start(c2, did, r.json()["approval"]).json()
            done = wait(c2, run["run_id"])
            assert done["state"] == "SUCCEEDED", (done["error_code"], done["error_message_ko"])
    finally:
        py.write_text(original, encoding="utf-8")


def test_removed_or_broken_plugin_is_shown_and_blocked(trained, plugin_root, tmp_path):
    c = trained
    other = tmp_path / "plugins"
    shutil.copytree(plugin_root / "ts_custom_ewm", other / "broken")
    (other / "broken" / "plugin.json").write_text("{", encoding="utf-8")
    os.environ["MLWB_PLUGIN_DIRS"] = str(other)  # 예제 폴더는 없고 망가진 폴더만 있다
    with TestClient(create_app(c.ws, serve_frontend=False, profile_in_process=False)) as c2:
        plan = c2.get(f"/api/datasets/{c.did}/plan").json()
        card = next(s for s in plan["steps"] if s["plugin_id"] == "ts.custom.ewm")
        assert card["status"] == "unregistered"
        assert plan["plugin_problems"] == [{"folder": "broken", "code": "PLUGIN_MANIFEST_INVALID",
                                            "message_ko": plan["plugin_problems"][0]["message_ko"]}]
        assert str(tmp_path) not in json.dumps(plan, ensure_ascii=False)
        v = c2.get(f"/api/datasets/{c.did}/confirmation").json()
        assert v["error_count"] > 0
        src = (c.ws / "datasets" / c.did / "source.csv").read_bytes()
        got = apply(c2, src, "x.csv", run_id=c.run_id)
        assert got["state"] == "CHECK_FAILED" and got["error"]["code"] == "PLUGIN_UNAVAILABLE", got
