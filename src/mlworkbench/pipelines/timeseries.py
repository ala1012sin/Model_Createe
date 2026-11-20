"""시계열 예측 실행 경로: CSV → 전처리 → 표본 → 시간순 분할 → 학습 전용 fit → XGBoost → 평가 → 저장.

표본 정의(직접 예측, horizon마다 모델 1개):
  기준 시각 t의 피처(그 시각까지의 관측으로만 계산) → 같은 설비·세션의 t + h 시각 타깃 관측값.
  세션을 넘는 타깃은 만들지 않는다. 리샘플이 있으면 h는 간격의 정수배여야 한다. 없으면 t + h와 정확히 같은
  시각의 관측이 있을 때만 표본이 된다.

분할:
  전처리 후 모든 설비의 시각을 합친 고유 시각 목록에서 비율 위치로 경계(train_end, val_end)를 정한다.
  같은 시각은 설비와 무관하게 같은 분할에 속한다. 표본은 기준 시각으로 배정하고, 타깃 시각이 경계를 넘는
  학습·검증 표본은 제외(purge)한다(gap=auto는 각 horizon만큼, 지정 gap은 max(gap, horizon)만큼).
  학습량(최근 N일)은 경계와 무관하게 학습 표본만 train_end 기준 N일로 줄인다 → 검증·테스트 표본은 같다.

평가(rolling origin, 재학습 없음):
  모델은 학습 분할에서만 한 번 학습한다. 검증·테스트의 각 기준 시각에서 그 시각까지의 관측만으로 만든
  피처로 예측한다. 기준 예측(직전값)은 같은 표본에서 기준 시각의 타깃 값(결측 처리 후)이다.
"""

from __future__ import annotations

import json
import math
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from mlworkbench.core.registry import PluginRegistry
from mlworkbench.pipelines import metrics as M
from mlworkbench.pipelines.ts_frame import GROUP, LABEL, NO_GROUP, SESSION, TIME, PipelineError, TsFrame, duration, load_frame

SEED = 42
PIPELINE_FORMAT = "mlwb.timeseries.v1"
SPLITS = ("train", "val", "test")


class Canceled(Exception):
    pass


@dataclass
class RunHooks:
    """실행기가 넘기는 콜백. emit(stage, progress, message_code), check_cancel()은 취소 시 Canceled."""

    emit: Callable[[str, float | None, str], None] = lambda *a: None
    check_cancel: Callable[[], None] = lambda: None
    log: Callable[[str], None] = lambda s: None


# ---------------------------------------------------------------------------
# 전처리 적용
# ---------------------------------------------------------------------------
def _step_spec(registry: PluginRegistry, step: dict[str, Any]):
    adapter = registry.get_preprocessor_adapter(step["plugin_id"], step["plugin_version"])
    if adapter is None:
        raise PipelineError("PLUGIN_UNAVAILABLE", f"실행할 수 없는 전처리입니다: {step['plugin_id']}@{step['plugin_version']}")
    return adapter


def split_steps(registry: PluginRegistry, steps: list[dict[str, Any]]):
    """(무상태 단계, 학습 전용 단계). 학습 전용 단계는 무상태 단계 뒤에만 올 수 있다."""
    stateless, train_only = [], []
    for s in steps:
        a = _step_spec(registry, s)
        if a.spec.fit_scope == "train_only":
            train_only.append((a, s))
        else:
            if train_only:
                raise PipelineError("ORDER_UNSUPPORTED", "학습 구간 통계를 쓰는 전처리는 다른 전처리 뒤에 와야 합니다")
            stateless.append((a, s))
    return stateless, train_only


def apply_stateless(frame: TsFrame, stateless) -> TsFrame:
    for adapter, step in stateless:
        frame = adapter.transform(frame, None, step["params"])
    return frame


# ---------------------------------------------------------------------------
# 표본
# ---------------------------------------------------------------------------
def build_samples(frame: TsFrame, horizons: list[str], *, with_label: bool = True) -> pd.DataFrame:
    df = frame.df
    keys = [df[GROUP], df[SESSION]]
    parts = []
    for h in horizons:
        hd = duration(h)
        base = df[[GROUP, SESSION, TIME] + frame.feature_cols].copy()
        base["horizon"] = h
        base["target_time"] = df[TIME] + hd
        base["baseline"] = df[frame.target]
        if with_label:
            if frame.freq is not None:
                steps = hd / frame.freq
                if abs(steps - round(steps)) > 1e-9 or round(steps) < 1:
                    raise PipelineError("HORIZON_NOT_MULTIPLE", f"예측 시점 {h}가 리샘플 간격의 정수배가 아닙니다")
                base["y"] = df[LABEL].groupby(keys, sort=False).shift(-int(round(steps)))
            else:
                lookup = df[[GROUP, SESSION, TIME, LABEL]].rename(columns={TIME: "target_time", LABEL: "y"})
                base = base.merge(lookup, on=[GROUP, SESSION, "target_time"], how="left")
        parts.append(base)
    out = pd.concat(parts, ignore_index=True)
    return out.rename(columns={TIME: "origin_time"})


def _boundaries(times: np.ndarray, sp: dict[str, Any]) -> tuple[pd.Timestamp, pd.Timestamp]:
    n = len(times)
    if n < 3:
        raise PipelineError("TOO_FEW_TIMES", "분할하기에 시각 수가 너무 적습니다")
    i_tr = max(math.ceil(n * sp["train"]), 1) - 1
    i_va = max(math.ceil(n * (sp["train"] + sp["val"])), 1) - 1 if sp["val"] > 0 else i_tr
    return pd.Timestamp(times[i_tr]), pd.Timestamp(times[i_va])


def assign_split(samples: pd.DataFrame, frame: TsFrame, split_policy: dict[str, Any],
                 budget: dict[str, Any], *, baseline_used: bool) -> tuple[pd.DataFrame, dict[str, Any]]:
    times = np.sort(frame.df[TIME].unique())
    train_end, val_end = _boundaries(times, split_policy)
    gap = pd.Timedelta(0) if split_policy.get("gap", "auto") == "auto" else duration(split_policy["gap"])
    purge = samples["horizon"].map(lambda h: max(gap, duration(h)))
    o, tt = samples["origin_time"], samples["target_time"]
    split = pd.Series("test", index=samples.index, dtype="object")
    split[o <= val_end] = "val"
    split[o <= train_end] = "train"
    purged = ((split == "train") & (o > train_end - purge)) | ((split == "val") & (o > val_end - purge))
    split[purged] = "purged"
    if budget.get("mode") == "recent_days":
        start = train_end - pd.Timedelta(days=int(budget["days"]))
        split[(split == "train") & (o <= start)] = "outside_budget"
    missing = samples["y"].isna()
    if baseline_used:
        missing |= samples["baseline"].isna() & split.isin(["val", "test"])
    if frame.drop_missing:
        missing |= samples[frame.feature_cols].isna().any(axis=1) | samples["baseline"].isna()
    split[missing & split.isin(SPLITS)] = "excluded_missing"
    samples = samples.assign(split=split)
    summary: dict[str, Any] = {
        "train_end": train_end.isoformat(), "val_end": val_end.isoformat(),
        "data_start": pd.Timestamp(times[0]).isoformat(), "data_end": pd.Timestamp(times[-1]).isoformat(),
        "unique_times": int(len(times)), "ratios": {k: split_policy[k] for k in SPLITS},
        "gap": split_policy.get("gap", "auto"),
        "gap_policy": "학습·검증 표본 중 타깃 시각이 다음 분할 경계를 넘는 표본 제외(gap=auto: 각 horizon, 지정 시 max(gap, horizon))",
        "budget": budget,
        "budget_policy": ("최근 N일은 학습 구간의 마지막 시각(train_end) 기준. 그 전 관측은 lag 등 과거 이력으로만 쓰고 학습 표본에서는 제외"
                          if budget.get("mode") == "recent_days" else "학습 구간 전체 사용"),
        "counts": {k: {h: int(v) for h, v in samples[samples["split"] == k].groupby("horizon").size().items()}
                   for k in ("train", "val", "test", "purged", "outside_budget", "excluded_missing")},
    }
    for k in SPLITS:
        part = samples[samples["split"] == k]
        summary[f"{k}_period"] = ([part["origin_time"].min().isoformat(), part["origin_time"].max().isoformat()]
                                  if len(part) else None)
        summary[f"{k}_samples"] = int(len(part))
    return samples, summary


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def _versions() -> dict[str, str]:
    import sklearn

    out = {"python": sys.version.split()[0], "platform": platform.platform(), "pandas": pd.__version__,
           "numpy": np.__version__, "scikit-learn": sklearn.__version__}
    return out


def _feature_matrix(samples: pd.DataFrame, cols: list[str]) -> np.ndarray:
    return samples[cols].to_numpy(dtype="float64")


def _aggregate(pred: pd.DataFrame, ev: dict[str, Any]) -> dict[str, Any]:
    names = list(ev["metrics"])
    use_base = ev.get("baseline", "none") != "none"

    def block(part: pd.DataFrame) -> dict[str, Any]:
        b = {"n": int(len(part)), "model": M.compute(part["actual"], part["predicted"], names)}
        if use_base:
            b["baseline"] = M.compute(part["actual"], part["baseline"], names)
        return b

    out: dict[str, Any] = {}
    for sp in ("val", "test"):
        part = pred[pred["split"] == sp]
        if part.empty:
            continue
        r: dict[str, Any] = {"overall": block(part)}
        if ev.get("per_horizon", True):
            r["per_horizon"] = {h: block(p) for h, p in part.groupby("horizon", sort=False)}
        if ev.get("per_group"):
            r["per_group"] = {g: block(p) for g, p in part.groupby("group", sort=True)}
        out[sp] = r
    return out


def run_timeseries(config: dict[str, Any], *, csv_path: Path, csv_options: dict[str, str], numeric_cols: list[str],
                   registry: PluginRegistry, out_dir: Path, hooks: RunHooks | None = None,
                   seed: int = SEED) -> dict[str, Any]:
    """승인된 실행 설정(ExecutablePlan.execution_config())을 실행하고 산출물을 out_dir에 저장한다."""
    hooks = hooks or RunHooks()
    goal = config["user_goal"]
    ev = config["evaluation"]
    if ev.get("baseline") not in ("naive_last_value", "none"):
        raise PipelineError("EVAL_OPTION_NOT_IMPLEMENTED", "선택한 기준 예측은 아직 구현되지 않았습니다")
    model_sel = config["model"]
    model = registry.get_model_adapter(model_sel["plugin_id"], model_sel["plugin_version"])
    if model is None:
        raise PipelineError("PLUGIN_UNAVAILABLE", f"실행할 수 없는 모델입니다: {model_sel['plugin_id']}@{model_sel['plugin_version']}")

    # 1. 준비 -------------------------------------------------------------
    hooks.emit("prepare", None, "PREPARE_STARTED")
    frame, load_report = load_frame(csv_path, encoding=csv_options["encoding"], delimiter=csv_options["delimiter"],
                                    timestamp_col=goal["timestamp_col"], target_col=goal["target_col"],
                                    group_col=goal.get("group_col"), numeric_cols=numeric_cols)
    hooks.log(f"읽기: {load_report['rows_read']}행 → 사용 {load_report['rows_used']}행 "
              f"(시각 해석 불가 {load_report['rows_time_invalid']}, 같은 시각 병합 {load_report['rows_duplicate_time_merged']})")
    hooks.check_cancel()

    # 2. 전처리(무상태) → 표본 → 분할 → 학습 전용 fit ----------------------------
    stateless, train_only = split_steps(registry, config["preprocessing"])
    hooks.emit("preprocess", 0.0, "PREPROCESS_STARTED")
    for i, (adapter, step) in enumerate(stateless):
        before = frame
        frame = adapter.transform(frame, None, step["params"])
        if callable(getattr(adapter, "check_causal", None)):  # 사용자 플러그인: 과거만 쓰는지 실행 시 확인
            adapter.check_causal(before, frame, step["params"])
            hooks.log(f"사용자 플러그인 {step['plugin_id']}: 과거만 사용 검사 통과")
        hooks.emit("preprocess", (i + 1) / (len(stateless) + 2), "PREPROCESS_STEP_DONE")
        hooks.check_cancel()
    horizons = list(goal["horizons"])
    samples = build_samples(frame, horizons)
    samples, split_summary = assign_split(samples, frame, config["split_policy"], config["train_budget"],
                                          baseline_used=ev.get("baseline") != "none")
    if split_summary["train_samples"] == 0:
        raise PipelineError("NO_TRAIN_SAMPLES", "학습 표본이 없습니다. 학습량·분할 비율·예측 시점을 확인해 주세요")
    if split_summary["test_samples"] == 0:
        raise PipelineError("NO_TEST_SAMPLES", "테스트 표본이 없습니다. 분할 비율·예측 시점을 확인해 주세요")
    features = list(frame.feature_cols)
    states: list[dict[str, Any]] = []
    for adapter, step in train_only:
        train_rows = samples[samples["split"] == "train"]
        sub = TsFrame(train_rows[features].copy(), frame.target, frame.value_cols, features)
        state = adapter.fit(sub, step["params"])
        all_f = adapter.transform(TsFrame(samples[features].copy(), frame.target, frame.value_cols, features),
                                  state, step["params"])
        samples[features] = all_f.df[features]
        states.append({"plugin_id": step["plugin_id"], "plugin_version": step["plugin_version"], "state": state})
    hooks.emit("preprocess", 1.0, "PREPROCESS_DONE")
    hooks.log(f"분할: train_end {split_summary['train_end']}, val_end {split_summary['val_end']}, "
              f"학습 {split_summary['train_samples']} / 검증 {split_summary['val_samples']} / 테스트 {split_summary['test_samples']}")
    hooks.check_cancel()

    # 3. 학습(horizon별) ----------------------------------------------------
    hooks.emit("train", 0.0, "TRAIN_STARTED")
    models: dict[str, Any] = {}
    n_h = len(horizons)
    for i, h in enumerate(horizons):
        tr = samples[(samples["split"] == "train") & (samples["horizon"] == h)]

        def on_iter(done: int, total: int, _i: int = i) -> bool:
            hooks.emit("train", (_i + done / total) / n_h, "TRAIN_PROGRESS")
            try:
                hooks.check_cancel()
            except Canceled:
                return False
            return True

        try:
            models[h] = model.fit(_feature_matrix(tr, features), tr["y"].to_numpy("float64"), model_sel["params"],
                                  seed=seed, on_iteration=on_iter)
        except Exception as e:  # noqa: BLE001
            from mlworkbench.plugins.models import TrainingCanceled

            if isinstance(e, TrainingCanceled):
                raise Canceled() from None
            raise
    hooks.emit("train", 1.0, "TRAIN_DONE")
    hooks.check_cancel()

    # 4. 평가 --------------------------------------------------------------
    hooks.emit("evaluate", None, "EVALUATE_STARTED")
    rows = []
    for h in horizons:
        part = samples[samples["split"].isin(["val", "test"]) & (samples["horizon"] == h)]
        if part.empty:
            continue
        yhat = model.predict(models[h], _feature_matrix(part, features))
        rows.append(pd.DataFrame({
            "split": part["split"].to_numpy(), "group": part[GROUP].to_numpy(),
            "origin_time": part["origin_time"].to_numpy(), "target_time": part["target_time"].to_numpy(),
            "horizon": h, "actual": part["y"].to_numpy("float64"), "predicted": np.asarray(yhat, dtype="float64"),
            "baseline": part["baseline"].to_numpy("float64"),
        }))
    pred = pd.concat(rows, ignore_index=True)
    metrics = _aggregate(pred, ev)
    hooks.check_cancel()

    # 5. 저장 --------------------------------------------------------------
    hooks.emit("save", None, "SAVE_STARTED")
    (out_dir / "model").mkdir(parents=True, exist_ok=True)
    (out_dir / "predictions").mkdir(parents=True, exist_ok=True)
    model_files = {}
    for h, m in models.items():
        name = f"xgb_{h}.json"
        model.save(m, out_dir / "model" / name)
        model_files[h] = name
    pipeline_doc = {
        "format": PIPELINE_FORMAT, "goal": goal, "preprocessing": config["preprocessing"], "model": model_sel,
        "features": features, "value_cols": frame.value_cols, "numeric_cols": numeric_cols,
        "freq": str(frame.freq) if frame.freq is not None else None, "fitted_states": states,
        "model_files": model_files, "seed": seed, "drop_missing": frame.drop_missing,
        "timezone_policy": load_report["timezone_policy"],
        "plugin_code": _plugin_code(registry, config["preprocessing"]),
    }
    _write_json(out_dir / "model" / "pipeline.json", pipeline_doc)
    out_pred = pred.copy()
    out_pred["group"] = out_pred["group"].where(out_pred["group"] != NO_GROUP, "")
    for c in ("origin_time", "target_time"):
        out_pred[c] = pd.to_datetime(out_pred[c]).dt.strftime("%Y-%m-%d %H:%M:%S")
    out_pred.to_csv(out_dir / "predictions" / "predictions.csv", index=False, encoding="utf-8")
    split_rows = samples[[GROUP, "origin_time", "target_time", "horizon", "split"]].rename(columns={GROUP: "group"})
    split_rows.to_csv(out_dir / "split_rows.csv", index=False, encoding="utf-8")
    _write_json(out_dir / "split.json", {**split_summary, "load": load_report, "notes": frame.notes})
    _write_json(out_dir / "metrics.json", metrics)
    _write_json(out_dir / "metrics_meta.json", {
        "definitions": {k: M.DEFINITIONS[k] for k in ev["metrics"]},
        "evaluation": ev, "protocol": "rolling origin: 학습 분할에서 한 번 학습, 검증·테스트의 각 기준 시각까지의 관측만으로 피처 계산, 재학습 없음",
        "baseline": "직전값: 기준 시각의 타깃 값(결측 처리 후). 모델과 같은 표본에서 계산" if ev.get("baseline") != "none" else "사용 안 함",
        "aggregation": "overall=모든 horizon·설비 표본을 합쳐 계산, per_horizon/per_group=해당 표본만으로 계산",
        "sample_counts": {k: split_summary[f"{k}_samples"] for k in SPLITS},
    })
    _write_json(out_dir / "environment.json", {
        **_versions(), **model.library_versions(), "seed": seed, "n_jobs": getattr(model, "n_jobs", None),
        "plugins": [f"{s['plugin_id']}@{s['plugin_version']}" for s in config["preprocessing"]]
        + [f"{model_sel['plugin_id']}@{model_sel['plugin_version']}"],
        "catalog_version": registry.catalog_version(),
        "user_plugin_code": pipeline_doc["plugin_code"],
    })
    hooks.emit("save", 1.0, "SAVE_DONE")
    return {"split": split_summary, "metrics": metrics, "load": load_report}


def _plugin_code(registry: PluginRegistry, steps: list[dict[str, Any]]) -> dict[str, str]:
    """사용자 플러그인 key → 코드 해시(내장은 넣지 않음)."""
    out = {}
    for s in steps:
        spec = registry.get(s["plugin_id"], s["plugin_version"])
        if spec is not None and spec.code_sha256:
            out[spec.key] = spec.code_sha256
    return out


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# 저장한 파이프라인 불러오기 (S7 새 데이터 적용)
# ---------------------------------------------------------------------------


class FittedTimeseries:
    """학습 때 저장한 전처리 상태·모델로만 추론한다. fit을 호출하지 않는다."""

    def __init__(self, doc: dict[str, Any], models: dict[str, Any], model_adapter, registry: PluginRegistry,
                 stateless, train_only):
        self.doc, self.models, self.model_adapter, self.registry = doc, models, model_adapter, registry
        self.stateless, self.train_only = stateless, train_only

    @classmethod
    def load(cls, exp_dir: Path, registry: PluginRegistry) -> "FittedTimeseries":
        f = exp_dir / "model" / "pipeline.json"
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise PipelineError("MODEL_FILES_MISSING", "저장된 모델 설정(pipeline.json)이 없습니다") from None
        except (OSError, ValueError):
            raise PipelineError("MODEL_FILES_CORRUPT", "저장된 모델 설정을 읽을 수 없습니다") from None
        if not isinstance(doc, dict) or doc.get("format") != PIPELINE_FORMAT:
            raise PipelineError("PIPELINE_FORMAT", "지원하지 않는 저장 형식입니다")
        need = ("goal", "preprocessing", "model", "features", "value_cols", "numeric_cols", "fitted_states", "model_files")
        if any(k not in doc for k in need) or not doc["model_files"]:
            raise PipelineError("MODEL_FILES_CORRUPT", "저장된 모델 설정이 완전하지 않습니다")
        sel = doc["model"]
        spec = registry.get(sel["plugin_id"], sel["plugin_version"])
        adapter = registry.get_model_adapter(sel["plugin_id"], sel["plugin_version"])
        if adapter is None:
            why = spec.reason_unavailable if spec is not None else "등록되지 않은 모델"
            raise PipelineError("PLUGIN_UNAVAILABLE", f"저장한 모델을 불러올 구현이 없습니다: {why}")
        stateless, train_only = split_steps(registry, doc["preprocessing"])
        saved_code = doc.get("plugin_code") or {}
        now_code = _plugin_code(registry, doc["preprocessing"])
        if saved_code != now_code:
            changed = sorted(k for k in set(saved_code) | set(now_code) if saved_code.get(k) != now_code.get(k))
            raise PipelineError("PLUGIN_CODE_CHANGED",
                                "학습 이후 사용자 플러그인 코드가 바뀌어 같은 피처를 만들 수 없습니다: " + ", ".join(changed))
        states = doc["fitted_states"]
        if len(states) != len(train_only) or any(
                st.get("plugin_id") != s["plugin_id"] or st.get("plugin_version") != s["plugin_version"] or "state" not in st
                for st, (_, s) in zip(states, train_only)):
            raise PipelineError("PIPELINE_STATE_MISMATCH",
                                f"저장된 전처리 상태({len(states)}개)가 학습 전용 단계({len(train_only)}개)와 맞지 않습니다")
        models = {}
        for h, name in doc["model_files"].items():
            path = exp_dir / "model" / name
            if not path.exists():
                raise PipelineError("MODEL_FILES_MISSING", f"{h} 예측 모델 파일이 없습니다")
            try:
                models[h] = adapter.load(path)
            except Exception:  # noqa: BLE001 - 라이브러리마다 예외 종류가 다르다
                raise PipelineError("MODEL_FILES_CORRUPT", f"{h} 예측 모델 파일을 불러올 수 없습니다") from None
        return cls(doc, models, adapter, registry, stateless, train_only)

    # 입력 요구사항 --------------------------------------------------------------
    def _step_name(self, step: dict[str, Any]) -> str:
        spec = self.registry.get(step["plugin_id"], step["plugin_version"])
        return spec.display_name_ko if spec is not None else step["plugin_id"]

    def requirements(self) -> dict[str, Any]:
        d, g = self.doc, self.doc["goal"]
        target = g["target_col"]
        steps = {s["plugin_id"]: s["params"] for s in d["preprocessing"]}
        freq = steps.get("ts.resample", {}).get("frequency")
        # 열별로 과거 관측을 쓰는 피처와 필요한 이력(칸 수). 각 어댑터의 history()가 알려 준다(사용자 플러그인 포함).
        uses: dict[str, list[str]] = {}
        need_rows = 0
        for adapter, step in self.stateless:
            hist = getattr(adapter, "history", None)
            if not callable(hist):
                continue
            rows, by_col = hist(step["params"], target)
            need_rows = max(need_rows, int(rows))
            for c, desc in by_col.items():
                uses.setdefault(c, []).append(desc)
        target_reasons = ["기준 시각의 타깃 값이 입력 피처"] + [f"{u}의 과거 타깃 값" for u in uses.get(target, [])]
        cols = [{"name": g["timestamp_col"], "role": "시간", "why": "기준 시각·예측 대상 시각 계산"
                 + (" + 시간 특징(" + ", ".join(steps["ts.calendar"]["features"]) + ")" if "ts.calendar" in steps else "")}]
        if g.get("group_col"):
            cols.append({"name": g["group_col"], "role": "설비", "why": "설비별로 이력·세션을 나눔"})
        cols.append({"name": target, "role": "타깃(과거 관측)", "why": ", ".join(target_reasons)
                     + ". 예측 대상 시각의 실제값은 필요 없음"})
        for c in d["value_cols"]:
            if c != target:
                cols.append({"name": c, "role": "입력 수치",
                             "why": "기준 시각 값이 입력 피처" + "".join(f" + {u}" for u in uses.get(c, []))})
        history = None
        if need_rows:
            history = (f"설비·세션마다 기준 시각 이전 {need_rows}칸"
                       + (f"(= {freq} × {need_rows})" if freq else "") + "의 관측이 있어야 예측합니다")
        return {
            "target": g["target_col"], "horizons": list(d["model_files"]), "group_col": g.get("group_col"),
            "timestamp_col": g["timestamp_col"], "columns": cols,
            "steps": [{"plugin_id": s["plugin_id"], "name": self._step_name(s), "params": s["params"]}
                      for s in d["preprocessing"]],
            "history": history, "timezone_policy": d.get("timezone_policy"),
            "extra_columns_policy": "목록에 없는 열은 읽지 않습니다(열 순서는 상관없음). 문자·범주 열은 학습 때와 같이 쓰지 않습니다.",
        }

    # 점검·예측 ---------------------------------------------------------------------
    def prepare(self, csv_path: Path, *, encoding: str = "utf-8", delimiter: str = ",") -> tuple[pd.DataFrame, dict[str, Any]]:
        """새 CSV → 예측 가능한 표본과 점검 보고. 라벨(미래 실제값)은 만들지 않는다."""
        g = self.doc["goal"]
        need = [g["timestamp_col"], g["target_col"]] + ([g["group_col"]] if g.get("group_col") else []) + \
            [c for c in self.doc["value_cols"] if c != g["target_col"]]
        header = pd.read_csv(csv_path, encoding=encoding, sep=delimiter, nrows=0, dtype=str).columns.tolist()
        missing = [c for c in need if c not in header]
        if missing:
            raise PipelineError("COLUMNS_MISSING", "필요한 열이 없습니다: " + ", ".join(missing)
                                + " (학습 때와 같은 열 이름이어야 합니다)")
        try:
            frame, report = load_frame(csv_path, encoding=encoding, delimiter=delimiter, timestamp_col=g["timestamp_col"],
                                       target_col=g["target_col"], group_col=g.get("group_col"),
                                       numeric_cols=self.doc["value_cols"])
        except PipelineError as e:
            if e.code == "TARGET_NOT_NUMERIC":
                raise PipelineError("TARGET_HISTORY_MISSING",
                                    f"타깃 열({g['target_col']})의 과거 관측값이 필요합니다. 기준 시각 값과 lag 피처를 만들 때 씁니다") from None
            raise
        expected = self.doc.get("timezone_policy")
        if expected and report["timezone_policy"] != expected:
            raise PipelineError("TIMEZONE_POLICY_MISMATCH",
                                "시각 형식이 학습 때와 다릅니다: 학습 " + _TZ_KO[expected] + ", 새 파일 " + _TZ_KO[report["timezone_policy"]])
        report["extra_columns_ignored"] = [c for c in header if c not in need]
        frame = apply_stateless(frame, self.stateless)
        features = self.doc["features"]
        if list(frame.feature_cols) != features:
            raise PipelineError("FEATURES_MISMATCH", "새 데이터로 만든 입력 피처가 학습 때와 다릅니다")
        samples = build_samples(frame, list(self.doc["model_files"]), with_label=False)
        lag_cols = [c for c in features if c not in frame.value_cols]
        no_current = samples["baseline"].isna()
        short = samples[lag_cols].isna().any(axis=1) & ~no_current if lag_cols else pd.Series(False, index=samples.index)
        other = samples[features].isna().any(axis=1) & ~no_current & ~short if self.doc.get("drop_missing") \
            else pd.Series(False, index=samples.index)
        ok = ~(no_current | short | other)
        for (adapter, step), st in zip(self.train_only, self.doc["fitted_states"]):
            out = adapter.transform(TsFrame(samples[features].copy(), frame.target, frame.value_cols, features),
                                    st["state"], step["params"])
            samples[features] = out.df[features]
        good = samples[ok]
        report.update({
            "samples_total": int(len(samples)), "samples_predictable": int(len(good)),
            "excluded": {"history_short": int(short.sum()), "no_current_target": int(no_current.sum()),
                         "missing_features": int(other.sum())},
            "per_horizon": {h: int(n) for h, n in good.groupby("horizon").size().items()},
            "period": [frame.df[TIME].min().isoformat(), frame.df[TIME].max().isoformat()] if len(frame.df) else None,
            "origin_period": ([good["origin_time"].min().isoformat(), good["origin_time"].max().isoformat()]
                              if len(good) else None),
            "groups": sorted(good[GROUP].unique().tolist()) if len(good) else [],
            "beyond_last_observation": int((good["target_time"] > frame.df[TIME].max()).sum()) if len(good) else 0,
        })
        return good, report

    def predict(self, samples: pd.DataFrame) -> pd.DataFrame:
        if samples.empty:
            raise PipelineError("NO_PREDICTABLE_ROWS", "예측할 수 있는 행이 없습니다")
        features = self.doc["features"]
        parts = []
        for h, m in self.models.items():
            p = samples[samples["horizon"] == h]
            if p.empty:
                continue
            yhat = self.model_adapter.predict(m, _feature_matrix(p, features))
            parts.append(pd.DataFrame({"group": p[GROUP].to_numpy(), "origin_time": p["origin_time"].to_numpy(),
                                       "target_time": p["target_time"].to_numpy(), "horizon": h,
                                       "predicted": np.asarray(yhat, dtype="float64")}))
        return pd.concat(parts, ignore_index=True)

    def predict_csv(self, csv_path: Path, *, encoding: str = "utf-8", delimiter: str = ",") -> pd.DataFrame:
        samples, _ = self.prepare(csv_path, encoding=encoding, delimiter=delimiter)
        return self.predict(samples)


_TZ_KO = {"naive_as_written": "시간대 표기 없음(적힌 시각 그대로)", "utc_converted": "시간대 표기 있음(UTC로 변환)"}
