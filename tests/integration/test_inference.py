"""S7 새 데이터 적용 — 완료 실행의 저장 모델로 새 CSV 점검·예측·내려받기(학습 없음)."""

from __future__ import annotations

import hashlib
import io
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("xgboost")
pd = pytest.importorskip("pandas")
from fastapi.testclient import TestClient  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_runs import SETTINGS, approved, start, wait  # noqa: E402


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    ws = tmp_path_factory.mktemp("inf") / "ws"
    with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c:
        c.ws = ws
        did, appr = approved(c)
        run = start(c, did, appr).json()
        v = wait(c, run["run_id"])
        assert v["state"] == "SUCCEEDED"
        c.did, c.run_id = did, run["run_id"]
        c.source = (ws / "datasets" / did / "source.csv").read_bytes()
        yield c


def apply(c, data: bytes, name="new.csv", run_id=None):
    r = c.post(f"/api/models/{run_id or c.run_id}/applications", content=data, headers={"X-File-Name": quote(name)})
    assert r.status_code == 200, r.text
    return r.json()


def predict(c, app_id, timeout=60):
    r = c.post(f"/api/applications/{app_id}/predict")
    assert r.status_code == 200, r.json()
    end = time.time() + timeout
    while time.time() < end:
        v = c.get(f"/api/applications/{app_id}").json()
        if v["state"] in ("SUCCEEDED", "FAILED"):
            return v
        time.sleep(0.1)
    raise AssertionError("timeout")


def result_frame(c, app_id) -> pd.DataFrame:
    r = c.get(f"/api/applications/{app_id}/predictions.csv")
    assert r.status_code == 200
    return pd.read_csv(io.StringIO(r.text))


def tree_hash(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.name not in ("heartbeat",):
            h.update(str(p.relative_to(d)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def frame_of(data: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)


def to_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()


# --- 모델 선택·요구사항 ------------------------------------------------------------
def test_models_and_requirements(trained):
    c = trained
    items = c.get("/api/models").json()["items"]
    assert [m["run_id"] for m in items] == [c.run_id]
    m = items[0]
    assert m["plan_hash"].startswith("sha256:") and m["horizons"] == ["15min", "1h"] and m["dataset_name"] == "sensor_A.csv"
    req = c.get(f"/api/models/{c.run_id}").json()["requirements"]
    names = [x["name"] for x in req["columns"]]
    assert names == ["timestamp", "device_id", "sensor_1"]
    target = next(x for x in req["columns"] if x["name"] == "sensor_1")
    assert "lag" in target["why"] and "실제값은 필요 없음" in target["why"]
    assert "8칸" in req["history"] and req["timezone_policy"] == "naive_as_written"
    assert [s["plugin_id"] for s in req["steps"]] == ["ts.session_split", "ts.resample", "ts.missing", "ts.lag"]


def test_failed_or_canceled_run_not_selectable(trained):
    c = trained
    jobs = c.app.state.jobs
    did, appr = approved(c)
    out = jobs.submit_approved(dataset_id=did, plan_id=appr["plan_id"], plan_hash=appr["plan_hash"])
    from mlworkbench.core.contracts import RunState
    jobs.finish(out["run_id"], RunState.FAILED, stage="queued", message_code="RUN_FAILED", error_code="X",
                error_message_ko="x")
    r = c.get(f"/api/models/{out['run_id']}")
    assert r.status_code == 409 and r.json()["code"] == "RUN_NOT_SUCCEEDED"
    r = c.post(f"/api/models/{out['run_id']}/applications", content=c.source, headers={"X-File-Name": "a.csv"})
    assert r.status_code == 409
    assert out["run_id"] not in [m["run_id"] for m in c.get("/api/models").json()["items"]]


# --- 같은 원본 재적용 = 저장된 평가 예측 --------------------------------------------
def test_reapplying_training_csv_matches_saved_predictions(trained):
    c = trained
    exp = c.ws / "experiments" / c.run_id
    before, runs_before = tree_hash(exp), c.app.state.jobs.count_runs()
    a = apply(c, c.source, "sensor_A.csv")
    assert a["state"] == "CHECKED", a["error"]
    chk = a["check"]
    assert chk["samples_predictable"] > 0 and chk["groups"] == ["D01", "D02"]
    assert chk["excluded"]["history_short"] > 0  # 세션 시작 직후 lag 이력 부족 행은 이유와 함께 제외
    v = predict(c, a["application_id"])
    assert v["state"] == "SUCCEEDED" and v["download_available"]
    got = result_frame(c, a["application_id"])
    assert list(got.columns) == ["model_run_id", "model_plan_hash", "group", "origin_time", "target_time", "horizon", "predicted"]
    assert set(got.model_run_id) == {c.run_id} and set(got.model_plan_hash) == {a["plan_hash"]}
    saved = pd.read_csv(exp / "predictions" / "predictions.csv")
    m = saved.merge(got, on=["group", "origin_time", "target_time", "horizon"], suffixes=("", "_new"))
    assert len(m) > 0.9 * len(saved)  # 이력 부족으로 제외된 평가 표본만 빠짐(행 수가 아니라 조인으로 비교)
    assert (m.predicted - m.predicted_new).abs().max() < 1e-9
    assert len(got) > len(m)  # 학습 구간 등 평가 밖 표본도 예측됨
    assert tree_hash(exp) == before and c.app.state.jobs.count_runs() == runs_before
    assert str(c.ws) not in c.get(f"/api/applications/{a['application_id']}").text


def test_future_period_predicts_beyond_last_observation(trained):
    c = trained
    df = frame_of(c.source)
    ts = pd.to_datetime(df.timestamp)
    df = df[ts >= ts.max() - pd.Timedelta(days=2)].copy()
    df["timestamp"] = (pd.to_datetime(df.timestamp) + pd.Timedelta(days=30)).dt.strftime("%Y-%m-%d %H:%M:%S")
    a = apply(c, to_bytes(df), "future.csv")
    assert a["state"] == "CHECKED", a["error"]
    v = predict(c, a["application_id"])
    assert v["state"] == "SUCCEEDED" and v["result"]["beyond_last_observation"] > 0
    got = result_frame(c, a["application_id"])
    last = pd.to_datetime(df.timestamp).max()
    assert (pd.to_datetime(got.target_time) > last).any()
    assert set(v["result"]["per_horizon"]) == {"15min", "1h"} and len(v["result"]["preview"]) == 20


def test_column_order_and_extra_columns(trained):
    c = trained
    base = apply(c, c.source, "base.csv")
    df = frame_of(c.source)
    df["extra_note"] = "x"
    df = df[["memo", "sensor_1", "extra_note", "device_id", "timestamp", "mode"]]
    a = apply(c, to_bytes(df), "reordered.csv")
    assert a["state"] == "CHECKED" and set(a["check"]["extra_columns_ignored"]) == {"memo", "extra_note", "mode"}
    p1, p2 = predict(c, base["application_id"]), predict(c, a["application_id"])
    assert p1["state"] == p2["state"] == "SUCCEEDED"
    pd.testing.assert_frame_equal(result_frame(c, base["application_id"]).drop(columns=[]),
                                  result_frame(c, a["application_id"]))


# --- 점검 실패 ----------------------------------------------------------------------
@pytest.mark.parametrize("case,code,word", [
    ("missing_target_col", "COLUMNS_MISSING", "sensor_1"),
    ("missing_group_col", "COLUMNS_MISSING", "device_id"),
    ("empty_target", "TARGET_HISTORY_MISSING", "과거 관측값"),
    ("too_short", "NO_PREDICTABLE_ROWS", "이력 부족"),
    ("tz_offsets", "TIMEZONE_POLICY_MISMATCH", "시간대"),
    ("tz_mixed", "TIMESTAMP_TZ_MIXED", "섞여"),
])
def test_check_failures_are_explained(trained, case, code, word):
    c = trained
    df = frame_of(c.source)
    if case == "missing_target_col":
        df = df.drop(columns=["sensor_1"])
    elif case == "missing_group_col":
        df = df.drop(columns=["device_id"])
    elif case == "empty_target":
        df["sensor_1"] = ""
    elif case == "too_short":
        df = df.head(6)
    elif case == "tz_offsets":
        df["timestamp"] = df["timestamp"] + "+09:00"
    elif case == "tz_mixed":
        df.loc[df.index[:10], "timestamp"] = df.loc[df.index[:10], "timestamp"] + "Z"
    a = apply(c, to_bytes(df), f"{case}.csv")
    assert a["state"] == "CHECK_FAILED" and a["error"]["code"] == code and word in a["error"]["message_ko"], a["error"]
    r = c.post(f"/api/applications/{a['application_id']}/predict")
    assert r.status_code == 409 and r.json()["code"] == "NOT_CHECKED"
    assert c.get(f"/api/applications/{a['application_id']}/predictions.csv").status_code == 404


def test_non_csv_and_empty_rejected(trained):
    a = apply(trained, b"x", "model.xls")
    assert a["state"] == "CHECK_FAILED" and a["error"]["code"] == "UNSUPPORTED_FILE_TYPE" and ".xlsx" in a["error"]["message_ko"]
    x = apply(trained, b"not a zip", "broken.xlsx")
    assert x["state"] == "CHECK_FAILED" and x["error"]["code"] == "XLSX_INVALID"
    b = apply(trained, b"", "empty.csv")
    assert b["state"] == "CHECK_FAILED" and b["error"]["code"] == "EMPTY_FILE"


# --- 모델·입력 변경 ------------------------------------------------------------------
def test_changed_input_or_model_after_check_fails(trained):
    c = trained
    a = apply(c, c.source)
    (c.ws / "applications" / a["application_id"] / "input.csv").write_bytes(c.source + b"\n")
    v = predict(c, a["application_id"])
    assert v["state"] == "FAILED" and v["error"]["code"] == "INPUT_CHANGED" and not v["download_available"]
    assert c.get(f"/api/applications/{a['application_id']}/predictions.csv").status_code == 404
    b = apply(c, c.source)
    mf = c.ws / "experiments" / c.run_id / "model" / "xgb_1h.json"
    orig = mf.read_bytes()
    try:
        mf.write_bytes(orig[: len(orig) // 2])
        v = predict(c, b["application_id"])
        assert v["state"] == "FAILED" and v["error"]["code"] == "MODEL_CHANGED"
        bad = apply(c, c.source)  # 손상된 모델로 새로 점검
        assert bad["state"] == "CHECK_FAILED" and bad["error"]["code"] == "MODEL_FILES_CORRUPT"
    finally:
        mf.write_bytes(orig)
    assert c.post(f"/api/applications/{bad['application_id']}/check").json()["state"] == "CHECKED"  # 복구 후 재점검


def test_rebind_to_other_model_keeps_previous_record(trained):
    c = trained
    a = apply(c, c.source)
    did, appr = approved(c, {**SETTINGS, "train_budget": {"mode": "all"}})
    run2 = start(c, did, appr).json()["run_id"]
    assert wait(c, run2)["state"] == "SUCCEEDED"
    r = c.post(f"/api/applications/{a['application_id']}/rebind", json={"run_id": run2}).json()
    assert r["application_id"] != a["application_id"] and r["run_id"] == run2 and r["state"] == "CHECKED"
    assert r["input"]["sha256"] == a["input"]["sha256"]
    old = c.get(f"/api/applications/{a['application_id']}").json()
    assert old["run_id"] == c.run_id and old["state"] == "CHECKED"
    ids = [x["application_id"] for x in c.get("/api/applications").json()["items"]]
    assert ids.index(r["application_id"]) < ids.index(a["application_id"])  # 최신순


def test_inference_never_fits(trained, monkeypatch):
    from mlworkbench.plugins import models, timeseries

    def boom(*a, **k):
        raise AssertionError("추론 중 fit 호출")

    monkeypatch.setattr(models.XGBoostForecaster, "fit", boom)
    for cls in (timeseries.Scale, timeseries.Lag, timeseries.Missing, timeseries.Resample, timeseries.SessionSplit):
        monkeypatch.setattr(cls, "fit", boom)
    a = apply(trained, trained.source)
    assert predict(trained, a["application_id"])["state"] == "SUCCEEDED"


def test_duplicate_predict_requests(trained):
    c = trained
    a = apply(c, c.source)
    r1 = c.post(f"/api/applications/{a['application_id']}/predict").json()
    r2 = c.post(f"/api/applications/{a['application_id']}/predict").json()
    assert r1["state"] in ("RUNNING", "SUCCEEDED") and r2["state"] in ("RUNNING", "SUCCEEDED")
    v = predict(c, a["application_id"]) if c.get(f"/api/applications/{a['application_id']}").json()["state"] != "SUCCEEDED" \
        else c.get(f"/api/applications/{a['application_id']}").json()
    assert v["state"] == "SUCCEEDED"


def test_restart_cleans_running_application(tmp_path, trained):
    c = trained
    a = apply(c, c.source)
    svc = c.app.state.inference
    rec = svc.get(a["application_id"])
    rec["state"] = "RUNNING"
    svc._save(rec)
    with TestClient(create_app(c.ws, serve_frontend=False, profile_in_process=False)) as c2:
        v = c2.get(f"/api/applications/{a['application_id']}").json()
        assert v["state"] == "FAILED" and v["error"]["code"] == "APP_SHUTDOWN"
        assert c2.get(f"/api/applications/{a['application_id']}/predictions.csv").status_code == 404


def test_rolling_diff_calendar_plan_trains_and_reapplies(trained):
    """S2에서 rolling·차분·시간 특징을 추가한 계획 → 실제 학습 → S7 재적용이 저장 평가 예측과 같다."""
    from test_preprocess import GOAL, upload
    from test_csv_dataset import sensor_csv

    c = trained
    d = upload(c, "sensor_feat.csv", sensor_csv(2880))
    did = d["dataset_id"]
    assert c.put(f"/api/datasets/{did}/goal", json=GOAL).status_code == 200
    rid = c.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
    plan = c.post(f"/api/datasets/{did}/plan/apply", json={"recommendation_id": rid, "expected_revision": 1}).json()
    steps = [{k: s[k] for k in ("plugin_id", "plugin_version", "params", "reason")} for s in plan["steps"]]
    steps += [{"plugin_id": "ts.rolling", "plugin_version": "1.0.0", "params": {"windows": [4, 8], "stats": ["mean", "std"]},
               "reason": ""},
              {"plugin_id": "ts.diff", "plugin_version": "1.0.0", "params": {"periods": 1}, "reason": ""},
              {"plugin_id": "ts.calendar", "plugin_version": "1.0.0", "params": {"features": ["hour", "dayofweek"]},
               "reason": ""}]
    r = c.put(f"/api/datasets/{did}/plan", json={"expected_revision": plan["revision"], "steps": steps, "excluded": []})
    assert r.status_code == 200, r.json()
    assert all(s["status"] == "ready" for s in r.json()["steps"])
    t = c.put(f"/api/datasets/{did}/training", json={**SETTINGS, "expected_revision": r.json()["revision"]})
    assert t.status_code == 200
    v = c.get(f"/api/datasets/{did}/confirmation").json()
    assert v["error_count"] == 0, v["issues"]
    a = c.post(f"/api/datasets/{did}/approve", json={"expected_revision": v["revision"], "confirmed": True}).json()
    run = start(c, did, a["approval"]).json()
    done = wait(c, run["run_id"])
    assert done["state"] == "SUCCEEDED", (done["error_code"], done["error_message_ko"])
    assert any("rolling" in n for n in done["split"]["notes"]) and any("시간 특징" in n for n in done["split"]["notes"])
    req = c.get(f"/api/models/{run['run_id']}").json()["requirements"]
    assert "8칸" in req["history"]  # rolling 창 8(현재 제외 직전 8칸)
    src = (c.ws / "datasets" / did / "source.csv").read_bytes()
    app = apply(c, src, "sensor_feat.csv", run_id=run["run_id"])
    assert app["state"] == "CHECKED", app["error"]
    assert predict(c, app["application_id"])["state"] == "SUCCEEDED"
    got = result_frame(c, app["application_id"])
    saved = pd.read_csv(c.ws / "experiments" / run["run_id"] / "predictions" / "predictions.csv")
    m = saved.merge(got, on=["group", "origin_time", "target_time", "horizon"], suffixes=("", "_new"))
    assert len(m) > 0.9 * len(saved) and (m.predicted - m.predicted_new).abs().max() < 1e-9
