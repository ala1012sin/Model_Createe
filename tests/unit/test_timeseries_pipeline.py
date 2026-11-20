"""P2 시계열 실행 경로 — 누수·경계·분할·학습 전용 fit·지표·재현성.

실제 pandas·scikit-learn·xgboost를 쓴다(설치되어 있지 않으면 건너뜀).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("xgboost")

from mlworkbench.core.registry import builtin_registry  # noqa: E402
from mlworkbench.pipelines import metrics as M  # noqa: E402
from mlworkbench.pipelines.timeseries import (  # noqa: E402
    Canceled, FittedTimeseries, RunHooks, apply_stateless, assign_split, build_samples, run_timeseries, split_steps,
)
from mlworkbench.pipelines.ts_frame import GROUP, SESSION, TIME, PipelineError, load_frame  # noqa: E402

REG = builtin_registry()
NUM = ["temp", "load"]


def step(pid, **params):
    return {"plugin_id": pid, "plugin_version": "1.0.0", "params": params}


STEPS = [step("ts.session_split", gap="30min"), step("ts.resample", frequency="15min", aggregation="mean"),
         step("ts.missing", method="ffill"), step("ts.lag", lags=[1, 2, 4]), step("ts.scale", method="standard")]


def config(**over):
    cfg = {
        "user_goal": {"timestamp_col": "ts", "target_col": "temp", "group_col": "dev", "horizons": ["15min", "30min"]},
        "preprocessing": STEPS,
        "model": {"plugin_id": "timeseries.forecasting.xgboost", "plugin_version": "1.0.0",
                  "params": {"n_estimators": 40, "max_depth": 3}},
        "train_budget": {"mode": "all"},
        "split_policy": {"method": "chronological", "train": 0.7, "val": 0.15, "test": 0.15, "gap": "auto"},
        "evaluation": {"kind": "timeseries", "metrics": ["mae", "rmse", "r2", "smape"], "baseline": "naive_last_value",
                       "per_group": True, "per_horizon": True},
    }
    cfg.update(over)
    return cfg


def make_csv(path: Path, *, days=6, every="5min", gap_at=None, seed=0, mutate_after=None) -> Path:
    """설비 2개, 5분 간격. gap_at: 그 시각부터 2시간 측정 중단. mutate_after: 그 시각 이후 값을 크게 바꿈."""
    rng = np.random.default_rng(seed)
    t = pd.date_range("2026-01-01", periods=int(pd.Timedelta(days=days) / pd.Timedelta(every)), freq=every)
    rows = []
    for d, off in (("A", 0.0), ("B", 5.0)):
        temp = 20 + off + np.sin(np.arange(len(t)) / 30) * 3 + rng.normal(0, 0.2, len(t))
        load = 1 + np.cos(np.arange(len(t)) / 20) + rng.normal(0, 0.05, len(t))
        df = pd.DataFrame({"ts": t, "dev": d, "temp": temp, "load": load})
        if gap_at is not None:
            g = pd.Timestamp(gap_at)
            df = df[(df.ts < g) | (df.ts >= g + pd.Timedelta("2h"))]
        if mutate_after is not None:
            m = df.ts > pd.Timestamp(mutate_after)
            df.loc[m, ["temp", "load"]] = df.loc[m, ["temp", "load"]] * 7 + 100
        rows.append(df)
    out = pd.concat(rows).sort_values(["ts", "dev"])
    out["ts"] = out["ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
    out.to_csv(path, index=False)
    return path


def frame_of(path, steps=STEPS):
    f, _ = load_frame(path, encoding="utf-8", delimiter=",", timestamp_col="ts", target_col="temp",
                      group_col="dev", numeric_cols=NUM)
    stateless, _ = split_steps(REG, steps)
    return apply_stateless(f, stateless)


def run(tmp_path, cfg=None, csv=None, name="exp", **kw):
    out = tmp_path / name
    out.mkdir()
    csv = csv or make_csv(tmp_path / "data.csv")
    res = run_timeseries(cfg or config(), csv_path=csv, csv_options={"encoding": "utf-8", "delimiter": ","},
                         numeric_cols=NUM, registry=REG, out_dir=out, **kw)
    return out, res


# --- 미래값을 바꿔도 과거 피처·라벨은 같다 --------------------------------------
def test_future_values_do_not_change_past_features(tmp_path):
    cut = "2026-01-04 00:00"
    a = build_samples(frame_of(make_csv(tmp_path / "a.csv")), ["15min", "30min"])
    b = build_samples(frame_of(make_csv(tmp_path / "b.csv", mutate_after=cut)), ["15min", "30min"])
    cols = [c for c in a.columns if c not in ("y",)]
    pa = a[a.origin_time <= pd.Timestamp(cut)].reset_index(drop=True)
    pb = b[b.origin_time <= pd.Timestamp(cut)].reset_index(drop=True)
    pd.testing.assert_frame_equal(pa[cols], pb[cols])
    # 라벨도 타깃 시각이 cut 이전이면 같다
    la = a[a.target_time <= pd.Timestamp(cut)].reset_index(drop=True)
    lb = b[b.target_time <= pd.Timestamp(cut)].reset_index(drop=True)
    pd.testing.assert_series_equal(la["y"], lb["y"])
    assert not pa.equals(a.reset_index(drop=True))  # 실제로 미래 표본이 있었음


def test_resample_is_closed_right_labeled_right(tmp_path):
    p = tmp_path / "r.csv"
    pd.DataFrame({"ts": ["2026-01-01 00:05", "2026-01-01 00:15", "2026-01-01 00:20"], "dev": "A",
                  "temp": [1.0, 3.0, 100.0], "load": [0, 0, 0]}).to_csv(p, index=False)
    f = frame_of(p, [step("ts.resample", frequency="15min", aggregation="mean")])
    s = f.df.set_index(TIME)["temp"]
    assert s[pd.Timestamp("2026-01-01 00:15")] == 2.0  # (00:00, 00:15] = 00:05, 00:15
    assert s[pd.Timestamp("2026-01-01 00:30")] == 100.0  # 00:20은 다음 구간


# --- 그룹·세션 경계 -----------------------------------------------------------
def test_lags_and_targets_do_not_cross_group_or_session(tmp_path):
    gap = "2026-01-02 12:00"
    f = frame_of(make_csv(tmp_path / "g.csv", gap_at=gap))
    df = f.df
    assert df.groupby(GROUP)[SESSION].nunique().eq(2).all()  # 측정 중단으로 세션 2개
    first = df.groupby([GROUP, SESSION]).head(1)
    assert first["temp_lag1"].isna().all() and first["temp_lag4"].isna().all()
    s = build_samples(f, ["30min"])
    last = s.sort_values("origin_time").groupby([GROUP, SESSION]).tail(2)  # 세션 마지막 두 기준 시각
    assert last["y"].isna().all()  # 30분 뒤 타깃이 다음 세션이면 표본 없음
    for _, part in s.dropna(subset=["y"]).groupby([GROUP, SESSION]):
        assert (part.target_time - part.origin_time == pd.Timedelta("30min")).all()


def test_missing_ffill_uses_only_past_and_respects_max_gap(tmp_path):
    p = tmp_path / "m.csv"
    ts = pd.date_range("2026-01-01", periods=8, freq="15min").strftime("%Y-%m-%d %H:%M")
    temp = [1.0, None, None, None, 5.0, None, 7.0, 8.0]
    pd.DataFrame({"ts": ts, "dev": "A", "temp": temp, "load": 0}).to_csv(p, index=False)
    f = frame_of(p, [step("ts.missing", method="ffill", max_gap="30min")])
    got = f.df["temp"].tolist()
    # 00:15·00:30은 직전값(간격 ≤ 30분), 00:45는 45분이 지나 채우지 않음, 01:15는 01:00 값
    assert got[:3] == [1.0, 1.0, 1.0] and np.isnan(got[3]) and got[4:] == [5.0, 5.0, 7.0, 8.0]
    assert f.df["__label"].isna().sum() == 4  # 라벨은 채우지 않음


# --- 분할 -------------------------------------------------------------------
def test_split_purges_labels_and_keeps_same_time_together(tmp_path):
    f = frame_of(make_csv(tmp_path / "s.csv"))
    s, summ = assign_split(build_samples(f, ["15min", "30min"]), f, config()["split_policy"], {"mode": "all"},
                           baseline_used=True)
    te, ve = pd.Timestamp(summ["train_end"]), pd.Timestamp(summ["val_end"])
    assert (s[s.split == "train"].target_time <= te).all()
    assert (s[s.split == "val"].target_time <= ve).all() and (s[s.split == "val"].origin_time > te).all()
    assert (s[s.split == "test"].origin_time > ve).all()
    per_time = s[s.split.isin(["train", "val", "test"])].groupby("origin_time")["split"].nunique()
    assert per_time.max() == 1  # 같은 시각은 설비와 무관하게 한 분할
    assert summ["counts"]["purged"]["30min"] > summ["counts"]["purged"]["15min"] > 0


def test_budget_change_keeps_eval_samples_identical(tmp_path):
    csv = make_csv(tmp_path / "d.csv")
    a, ra = run(tmp_path, config(), csv, "all")
    b, rb = run(tmp_path, config(train_budget={"mode": "recent_days", "days": 2}), csv, "recent")
    sa, sb = pd.read_csv(a / "split_rows.csv"), pd.read_csv(b / "split_rows.csv")
    for sp in ("val", "test"):
        pd.testing.assert_frame_equal(sa[sa.split == sp].reset_index(drop=True), sb[sb.split == sp].reset_index(drop=True))
    assert rb["split"]["train_samples"] < ra["split"]["train_samples"]
    start = pd.Timestamp(rb["split"]["train_period"][0])
    assert start > pd.Timestamp(rb["split"]["train_end"]) - pd.Timedelta(days=2)
    assert rb["split"]["counts"]["outside_budget"]


def test_scaler_fit_uses_train_rows_only(tmp_path):
    csv_a = make_csv(tmp_path / "a.csv")
    csv_b = make_csv(tmp_path / "b.csv", mutate_after="2026-01-05 12:00")  # 테스트 구간만 변경
    a, _ = run(tmp_path, config(), csv_a, "a")
    b, _ = run(tmp_path, config(), csv_b, "b")
    sa = json.loads((a / "model" / "pipeline.json").read_text())["fitted_states"][0]["state"]
    sb = json.loads((b / "model" / "pipeline.json").read_text())["fitted_states"][0]["state"]
    assert sa == sb and sa["method"] == "standard"
    # center = 학습 표본 피처의 평균
    f = frame_of(csv_a, STEPS[:-1])
    s, _ = assign_split(build_samples(f, ["15min", "30min"]), f, config()["split_policy"], {"mode": "all"},
                        baseline_used=True)
    tr = s[s.split == "train"]
    assert sa["center"][0] == pytest.approx(tr[sa["columns"][0]].mean())
    assert sa["fit_rows"] == len(tr)


# --- 실행 산출물·지표·재현성 ------------------------------------------------------
def test_run_saves_artifacts_and_metrics_match_predictions(tmp_path):
    out, res = run(tmp_path)
    for rel in ("metrics.json", "metrics_meta.json", "split.json", "split_rows.csv", "environment.json",
                "predictions/predictions.csv", "model/pipeline.json", "model/xgb_15min.json", "model/xgb_30min.json"):
        assert (out / rel).exists(), rel
    pred = pd.read_csv(out / "predictions" / "predictions.csv")
    assert set(pred.columns) >= {"split", "group", "origin_time", "target_time", "horizon", "actual", "predicted", "baseline"}
    saved = json.loads((out / "metrics.json").read_text())
    t = pred[pred.split == "test"]
    re = M.compute(t.actual, t.predicted, ["mae", "rmse", "r2", "smape"])
    for k, v in re.items():
        assert saved["test"]["overall"]["model"][k] == pytest.approx(v, rel=1e-9)
    b = M.compute(t.actual, t.baseline, ["mae"])
    assert saved["test"]["overall"]["baseline"]["mae"] == pytest.approx(b["mae"])
    assert set(saved["test"]["per_horizon"]) == {"15min", "30min"} and set(saved["test"]["per_group"]) == {"A", "B"}
    env = json.loads((out / "environment.json").read_text())
    assert env["seed"] == 42 and "xgboost" in env and "timeseries.forecasting.xgboost@1.0.0" in env["plugins"]


def test_same_seed_rerun_reproduces_metrics(tmp_path):
    csv = make_csv(tmp_path / "d.csv")
    _, a = run(tmp_path, config(), csv, "r1")
    _, b = run(tmp_path, config(), csv, "r2")
    for k in ("mae", "rmse"):
        assert abs(a["metrics"]["test"]["overall"]["model"][k] - b["metrics"]["test"]["overall"]["model"][k]) <= 1e-9


def test_saved_pipeline_reloads_and_reproduces_predictions(tmp_path):
    csv = make_csv(tmp_path / "d.csv")
    out, _ = run(tmp_path, config(), csv)
    fitted = FittedTimeseries.load(out, REG)
    p = fitted.predict_csv(csv)
    saved = pd.read_csv(out / "predictions" / "predictions.csv")
    saved = saved[saved.split == "test"]
    p["origin_time"] = pd.to_datetime(p.origin_time).dt.strftime("%Y-%m-%d %H:%M:%S")
    m = saved.merge(p, on=["group", "origin_time", "horizon"], suffixes=("", "_re"))
    assert len(m) == len(saved) and (m.predicted - m.predicted_re).abs().max() < 1e-9


def test_cancel_during_training(tmp_path):
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if calls["n"] > 8:
            raise Canceled()

    with pytest.raises(Canceled):
        run(tmp_path, hooks=RunHooks(check_cancel=check))


# --- 읽기 정책 ---------------------------------------------------------------
def test_timezone_offsets_converted_to_utc_and_mixed_rejected(tmp_path):
    p = tmp_path / "tz.csv"
    pd.DataFrame({"ts": ["2026-01-01T09:00:00+09:00", "2026-01-01T00:15:00Z"], "dev": "A", "temp": [1, 2],
                  "load": [0, 0]}).to_csv(p, index=False)
    f, rep = load_frame(p, encoding="utf-8", delimiter=",", timestamp_col="ts", target_col="temp", group_col="dev",
                        numeric_cols=NUM)
    assert rep["timezone_policy"] == "utc_converted"
    assert f.df[TIME].tolist() == [pd.Timestamp("2026-01-01 00:00"), pd.Timestamp("2026-01-01 00:15")]
    q = tmp_path / "mixed.csv"
    pd.DataFrame({"ts": ["2026-01-01T09:00:00+09:00", "2026-01-01 00:15"], "dev": "A", "temp": [1, 2],
                  "load": [0, 0]}).to_csv(q, index=False)
    with pytest.raises(PipelineError) as e:
        load_frame(q, encoding="utf-8", delimiter=",", timestamp_col="ts", target_col="temp", group_col="dev",
                   numeric_cols=NUM)
    assert e.value.code == "TIMESTAMP_TZ_MIXED"


def test_duplicate_times_merged_and_bad_times_dropped(tmp_path):
    p = tmp_path / "dup.csv"
    pd.DataFrame({"ts": ["2026/01/01 00:00", "2026-01-01 00:00", "not a time", "2026.01.01 00:15"], "dev": "A",
                  "temp": [1, 3, 9, 4], "load": [0, 0, 0, 0]}).to_csv(p, index=False)
    f, rep = load_frame(p, encoding="utf-8", delimiter=",", timestamp_col="ts", target_col="temp", group_col="dev",
                        numeric_cols=NUM)
    assert rep["rows_time_invalid"] == 1 and rep["rows_duplicate_time_merged"] == 1
    assert f.df["temp"].tolist() == [2.0, 4.0]


def test_horizon_must_be_multiple_of_resample(tmp_path):
    f = frame_of(make_csv(tmp_path / "h.csv"))
    with pytest.raises(PipelineError) as e:
        build_samples(f, ["20min"])
    assert e.value.code == "HORIZON_NOT_MULTIPLE"


# --- 지표 정의 ---------------------------------------------------------------
def test_metric_definitions_and_undefined_cases():
    r = M.compute([1, 2, 3], [1, 2, 5], ["mae", "rmse", "r2", "smape"])
    assert r["mae"] == pytest.approx(2 / 3) and r["rmse"] == pytest.approx((4 / 3) ** 0.5)
    assert r["r2"] == pytest.approx(1 - 4 / 2)
    assert r["smape"] == pytest.approx(100 * (2 * 2 / 8) / 3)
    c = M.compute([5, 5, 5], [5, 4, 6], ["r2"])
    assert c["r2"] is None  # 분산 0
    z = M.compute([0, 0], [0, 1], ["smape"])
    assert z["smape"] == pytest.approx(100 * (0 + 2) / 2)  # 0/0 표본은 0
    assert M.compute([], [], ["mae"])["mae"] is None


# --- S7 저장 파이프라인 불러오기 무결성 ------------------------------------------------
def _saved(tmp_path):
    out, _ = run(tmp_path)
    return out


@pytest.mark.parametrize("damage,code", [
    ("states_dropped", "PIPELINE_STATE_MISMATCH"),
    ("states_extra", "PIPELINE_STATE_MISMATCH"),
    ("state_wrong_plugin", "PIPELINE_STATE_MISMATCH"),
    ("model_file_missing", "MODEL_FILES_MISSING"),
    ("model_file_truncated", "MODEL_FILES_CORRUPT"),
    ("pipeline_missing", "MODEL_FILES_MISSING"),
    ("pipeline_broken", "MODEL_FILES_CORRUPT"),
    ("format_unknown", "PIPELINE_FORMAT"),
])
def test_damaged_saved_pipeline_is_rejected(tmp_path, damage, code):
    out = _saved(tmp_path)
    pj = out / "model" / "pipeline.json"
    doc = json.loads(pj.read_text())
    if damage == "states_dropped":
        doc["fitted_states"] = []
    elif damage == "states_extra":
        doc["fitted_states"] = doc["fitted_states"] * 2
    elif damage == "state_wrong_plugin":
        doc["fitted_states"][0]["plugin_id"] = "ts.lag"
    elif damage == "format_unknown":
        doc["format"] = "other"
    if damage in ("states_dropped", "states_extra", "state_wrong_plugin", "format_unknown"):
        pj.write_text(json.dumps(doc))
    elif damage == "model_file_missing":
        (out / "model" / "xgb_30min.json").unlink()
    elif damage == "model_file_truncated":
        f = out / "model" / "xgb_30min.json"
        f.write_bytes(f.read_bytes()[:100])
    elif damage == "pipeline_missing":
        pj.unlink()
    elif damage == "pipeline_broken":
        pj.write_text("{not json")
    with pytest.raises(PipelineError) as e:
        FittedTimeseries.load(out, REG)
    assert e.value.code == code


def test_missing_model_library_is_reported(tmp_path, monkeypatch):
    out = _saved(tmp_path)
    from mlworkbench.core import registry as R

    monkeypatch.setattr(R, "_module_available", lambda name: name != "xgboost")
    with pytest.raises(PipelineError) as e:
        FittedTimeseries.load(out, R.builtin_registry())
    assert e.value.code == "PLUGIN_UNAVAILABLE" and "xgboost" in e.value.message_ko


def test_inference_applies_saved_scaler_without_refit(tmp_path):
    csv = make_csv(tmp_path / "d.csv")
    out, _ = run(tmp_path, config(), csv)
    fitted = FittedTimeseries.load(out, REG)
    # 학습 때와 전혀 다른 크기의 새 데이터: 저장된 스케일 상태로만 변환 → 새 데이터 기준으로 다시 맞추지 않음
    big = make_csv(tmp_path / "big.csv", mutate_after="2025-12-31")
    s, rep = fitted.prepare(big)
    st = fitted.doc["fitted_states"][0]["state"]
    col = st["columns"][0]
    raw, _ = fitted.prepare(csv)
    assert rep["samples_predictable"] > 0
    assert abs(s[col].mean()) > 5  # 새 데이터 평균이 0으로 다시 맞춰지지 않음
    assert abs(raw[col].mean()) < 1.5


# --- rolling·차분·시간 특징 -----------------------------------------------------------
FEAT_STEPS = [step("ts.session_split", gap="30min"), step("ts.resample", frequency="15min", aggregation="mean"),
              step("ts.missing", method="ffill"), step("ts.lag", lags=[1, 2]),
              step("ts.rolling", windows=[3, 4], stats=["mean", "std", "max"], columns=["temp", "load"]),
              step("ts.diff", periods=2), step("ts.calendar", features=["hour", "dayofweek", "month", "is_weekend"])]
FEAT_COLS = ["temp_roll3_mean", "temp_roll4_std", "load_roll3_max", "temp_diff2", "cal_hour", "cal_dayofweek",
             "cal_month", "cal_is_weekend"]


def _series_csv(path, values_a, values_b, start="2026-01-02 00:00", gap_after=None):
    """설비 A·B, 15분 간격. gap_after=i 이면 i번째 이후 2시간 공백(새 세션)."""
    rows = []
    for dev, vals in (("A", values_a), ("B", values_b)):
        t = pd.Timestamp(start)
        for i, v in enumerate(vals):
            rows.append({"ts": t.strftime("%Y-%m-%d %H:%M"), "dev": dev, "temp": v, "load": 0.5})
            t += pd.Timedelta("15min")
            if gap_after is not None and i == gap_after:
                t += pd.Timedelta("2h")
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_rolling_excludes_current_and_stays_inside_group_and_session(tmp_path):
    a = [float(i) for i in range(10)]
    b = [100.0 + i for i in range(10)]
    f = frame_of(_series_csv(tmp_path / "r.csv", a, b, gap_after=5),
                 [FEAT_STEPS[0], FEAT_STEPS[1], step("ts.rolling", windows=[3], stats=["mean", "max"])])
    for (g, sess), part in f.df.groupby(["__group", "__session"]):
        x = part["temp"].tolist()
        got = part["temp_roll3_mean"].tolist()
        assert all(np.isnan(v) for v in got[:3])  # 현재 제외 + 창 3칸이 다 찰 때까지 없음
        for i in range(3, len(x)):
            assert got[i] == pytest.approx(np.mean(x[i - 3:i]))  # x[i](현재)는 포함하지 않음
            assert part["temp_roll3_max"].iloc[i] == pytest.approx(max(x[i - 3:i]))
    assert f.df.groupby("__group")["__session"].nunique().eq(2).all()


def test_diff_and_calendar_values(tmp_path):
    a = [1.0, 4.0, 9.0, 16.0, 25.0]
    f = frame_of(_series_csv(tmp_path / "d.csv", a, a, start="2026-01-03 23:30"),  # 토요일 23:30부터
                 [step("ts.resample", frequency="15min", aggregation="mean"), step("ts.diff", periods=2),
                  step("ts.calendar", features=["hour", "dayofweek", "month", "is_weekend"])])
    part = f.df[f.df["__group"] == "A"]
    d = part["temp_diff2"].tolist()
    assert np.isnan(d[0]) and np.isnan(d[1]) and d[2:] == [8.0, 12.0, 16.0]
    first = part.iloc[0]
    assert (first["cal_hour"], first["cal_dayofweek"], first["cal_month"], first["cal_is_weekend"]) == (23, 5, 1, 1)
    last = part.iloc[-1]  # 2026-01-04 00:30 (일요일)
    assert (last["cal_hour"], last["cal_dayofweek"], last["cal_is_weekend"]) == (0, 6, 1)


def test_new_features_do_not_see_the_future(tmp_path):
    cut = "2026-01-04 00:00"
    a = build_samples(frame_of(make_csv(tmp_path / "a.csv"), FEAT_STEPS), ["15min", "30min"])
    b = build_samples(frame_of(make_csv(tmp_path / "b.csv", mutate_after=cut), FEAT_STEPS), ["15min", "30min"])
    for c in FEAT_COLS:
        assert c in a.columns
    cols = [c for c in a.columns if c != "y"]
    pa = a[a.origin_time <= pd.Timestamp(cut)].reset_index(drop=True)
    pb = b[b.origin_time <= pd.Timestamp(cut)].reset_index(drop=True)
    pd.testing.assert_frame_equal(pa[cols], pb[cols])
    # 경계 직후(cut 다음 칸)의 rolling은 현재 값을 빼므로 아직 바뀌지 않는다
    nxt = pd.Timestamp(cut) + pd.Timedelta("15min")
    ra = a[a.origin_time == nxt]["temp_roll3_mean"].to_numpy()
    rb = b[b.origin_time == nxt]["temp_roll3_mean"].to_numpy()
    assert np.allclose(ra, rb)


def test_new_features_train_and_reload_identically_in_s7(tmp_path):
    csv = make_csv(tmp_path / "d.csv", gap_at="2026-01-03 06:00")
    cfg = config(preprocessing=FEAT_STEPS)  # 스케일링 없이: 피처 값을 그대로 비교
    out, res = run(tmp_path, cfg, csv)
    fitted = FittedTimeseries.load(out, REG)
    assert set(FEAT_COLS) <= set(fitted.doc["features"])
    # 학습 때 만든 피처와 S7에서 다시 만든 피처가 같은 키에서 같다
    train = build_samples(frame_of(csv, FEAT_STEPS), ["15min", "30min"])
    again, rep = fitted.prepare(csv)
    keys = ["__group", "origin_time", "horizon"]
    m = train.merge(again, on=keys, suffixes=("", "_s7"))
    assert len(m) == len(again) > 0
    for c in fitted.doc["features"]:
        assert np.allclose(m[c].to_numpy(float), m[c + "_s7"].to_numpy(float), equal_nan=True), c
    # 저장 평가 예측과 S7 예측도 같다
    pred = fitted.predict(again)
    saved = pd.read_csv(out / "predictions" / "predictions.csv")
    pred["origin_time"] = pd.to_datetime(pred.origin_time).dt.strftime("%Y-%m-%d %H:%M:%S")
    j = saved.merge(pred, on=["group", "origin_time", "horizon"], suffixes=("", "_s7"))
    assert len(j) > 0 and (j.predicted - j.predicted_s7).abs().max() < 1e-9
    # 필요한 이력 = max(lag 2, rolling 4(현재 제외 직전 4칸), 차분 2) = 4칸, 이력 부족 행은 이유와 함께 제외
    req = fitted.requirements()
    assert "4칸" in req["history"] and rep["excluded"]["history_short"] > 0
    # 이력이 정확히 4칸인 기준 시각부터 예측 가능(세션 첫 4칸만 제외)
    first = again.groupby("__group")["origin_time"].min()
    starts = train.groupby("__group")["origin_time"].min()
    assert ((first - starts) == pd.Timedelta("1h")).all()
    load = next(c for c in req["columns"] if c["name"] == "load")
    assert "rolling" in load["why"]
    assert "시간 특징" in req["columns"][0]["why"]
    assert res["metrics"]["test"]["overall"]["model"]["mae"] > 0


def test_rolling_diff_reject_non_numeric_columns():
    from types import SimpleNamespace

    from mlworkbench.plugins.timeseries import Diff, Rolling

    ds = SimpleNamespace(columns=[SimpleNamespace(name="temp", dtype="numeric"), SimpleNamespace(name="mode", dtype="categorical")])
    r = REG.get_preprocessor_adapter("ts.rolling", "1.0.0")
    d = REG.get_preprocessor_adapter("ts.diff", "1.0.0")
    assert isinstance(r, Rolling) and isinstance(d, Diff)
    assert r.validate(ds, {"windows": [3], "columns": ["mode"]}) and not r.validate(ds, {"windows": [3], "columns": ["temp"]})
    assert "차분" in d.validate(ds, {"periods": 1, "columns": ["mode"]})[0]
