"""실제 시계열 전처리 어댑터(ADD-2 `PreprocessorAdapter` 계약).

카탈로그 ID·버전은 기존 명세를 그대로 쓰고, 구현이 있는 항목만 status="ready"로 등록한다.
라이브러리 호출(pandas·scikit-learn)은 이 파일에만 둔다. 데이터는 `TsFrame`(그룹·시간 정렬)으로 주고받는다.

관측 가능 시점 규칙:
- 세션 분리: 직전 행과의 간격만 본다(과거만 사용).
- 리샘플링: 구간 (t-f, t]를 시각 t에 붙인다(closed=right, label=right, origin=epoch). 시각 t의 값은 t까지의 관측만 포함.
- 결측 처리: 직전 관측으로만 채운다(ffill). 다음 관측을 쓰는 선형 보간은 예측용으로 제공하지 않는다.
- lag: 같은 그룹·세션 안에서 k칸 앞의 값.
- rolling: 같은 그룹·세션 안에서 한 칸 민(현재 시점 제외) 직전 w칸의 통계. w칸이 모두 있어야 값이 있다.
- 차분: 같은 그룹·세션 안에서 x(t) - x(t-p). 현재 값은 기준 시각에 관측된 값이다.
- 시간 특징: 기준 시각 자체(시·요일·월·주말)에서만 계산. 시각 해석 정책(적힌 시각 그대로/UTC)은 데이터 읽기와 같다.
- 스케일링: 학습 분할의 행에서만 통계를 계산(fit)한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mlworkbench.core.contracts import DatasetManifest, PluginSpec

_READY = {
    "ts.session_split": dict(source_library="pandas", fit_scope="stateless", dependencies=["pandas"]),
    "ts.resample": dict(source_library="pandas", fit_scope="stateless", dependencies=["pandas"]),
    "ts.missing": dict(source_library="pandas", fit_scope="stateless", dependencies=["pandas"]),
    "ts.lag": dict(source_library="pandas", fit_scope="stateless", dependencies=["pandas"]),
    "ts.rolling": dict(source_library="pandas", fit_scope="stateless", dependencies=["pandas"]),
    "ts.diff": dict(source_library="pandas", fit_scope="stateless", dependencies=["pandas"]),
    "ts.calendar": dict(source_library="pandas", fit_scope="stateless", dependencies=["pandas"]),
    "ts.scale": dict(source_library="scikit-learn", fit_scope="train_only", train_only=True,
                     dependencies=["pandas", "sklearn"]),
}
IMPLEMENTED_PREPROCESSORS = frozenset(_READY)


def _ready(spec: PluginSpec) -> PluginSpec:
    return spec.model_copy(update={"status": "ready", "reason_unavailable": None, **_READY[spec.id]})


def _numeric_names(dataset: DatasetManifest | None) -> set[str] | None:
    if dataset is None or not dataset.columns:
        return None
    return {c.name for c in dataset.columns if c.dtype == "numeric"}


@dataclass
class _Base:
    spec: PluginSpec

    def validate(self, dataset: DatasetManifest | None, params: dict[str, Any]) -> list[str]:
        return []

    def fit(self, train_data: Any, params: dict[str, Any]) -> Any:
        return None

    def preview(self, sample: Any, params: dict[str, Any]) -> Any:
        return self.transform(sample, self.fit(sample, params), params)


class SessionSplit(_Base):
    def transform(self, data, state, params):
        from mlworkbench.pipelines.ts_frame import GROUP, SESSION, TIME, duration

        f = data.copy()
        gap = duration(params["gap"])
        df = f.df
        new = df.groupby(GROUP, sort=False)[TIME].diff() > gap
        df[SESSION] = new.astype("int64").groupby(df[GROUP], sort=False).cumsum()
        f.notes.append(f"세션 분리: 간격 {params['gap']} 초과 시 새 세션")
        return f


class Resample(_Base):
    def transform(self, data, state, params):
        import pandas as pd

        from mlworkbench.pipelines.ts_frame import GROUP, LABEL, SESSION, TIME, duration

        f = data.copy()
        freq = duration(params["frequency"])
        agg = params["aggregation"]
        cols = list(dict.fromkeys(f.value_cols + [LABEL]))
        parts = []
        for (g, s), part in f.sessions():
            r = (part.set_index(TIME)[cols]
                 .resample(freq, closed="right", label="right", origin="epoch").agg(agg))
            r[GROUP], r[SESSION] = g, s
            parts.append(r.reset_index())
        out = pd.concat(parts, ignore_index=True) if parts else f.df.iloc[0:0]
        f.df = out.sort_values([GROUP, TIME], kind="mergesort").reset_index(drop=True)
        f.freq = freq
        f.notes.append(f"리샘플링: {params['frequency']} 간격, 집계 {agg}, 구간 (t-간격, t]를 시각 t에 표시")
        return f


class Missing(_Base):
    def transform(self, data, state, params):
        from mlworkbench.pipelines.ts_frame import GROUP, SESSION, TIME, duration

        f = data.copy()
        if params["method"] == "drop":
            f.notes.append("결측 처리: 결측이 있는 표본을 학습·평가에서 제외")
            f.drop_missing = True
            return f
        df = f.df
        limit = duration(params["max_gap"]) if params.get("max_gap") else None
        keys = [df[GROUP], df[SESSION]]
        for c in f.value_cols:  # 라벨 열(__label)은 채우지 않는다
            filled = df[c].groupby(keys, sort=False).ffill()
            if limit is not None:
                seen = df[TIME].where(df[c].notna()).groupby(keys, sort=False).ffill()
                filled = filled.where(df[TIME] - seen <= limit)
            df[c] = filled
        f.notes.append("결측 처리: 같은 설비·세션의 직전 관측값으로 채움" + (f"(최대 {params['max_gap']})" if limit is not None else ""))
        return f


class _ColumnsStep(_Base):
    label = ""

    def validate(self, dataset, params):
        names = _numeric_names(dataset)
        if names is None:
            return []
        bad = [c for c in params.get("columns") or [] if c not in names]
        return [f"수치 열만 {self.label}에 쓸 수 있습니다: {', '.join(bad)}"] if bad else []

    @staticmethod
    def _columns(f, params):
        from mlworkbench.pipelines.ts_frame import PipelineError

        cols = params.get("columns") or [f.target]
        missing = [c for c in cols if c not in f.df.columns]
        if missing:
            raise PipelineError("COLUMN_MISSING", "전처리 대상 열이 데이터에 없습니다: " + ", ".join(missing))
        return cols


class Lag(_ColumnsStep):
    label = "lag"

    def history(self, params, target):
        return max(params["lags"]), {c: f"lag {params['lags']}" for c in params.get("columns") or [target]}

    def transform(self, data, state, params):
        from mlworkbench.pipelines.ts_frame import GROUP, SESSION

        f = data.copy()
        df = f.df
        cols = self._columns(f, params)
        g = df.groupby([GROUP, SESSION], sort=False)
        for c in cols:
            for k in params["lags"]:
                name = f"{c}_lag{k}"
                df[name] = g[c].shift(k)
                f.feature_cols.append(name)
        f.notes.append(f"lag: {', '.join(cols)} × {params['lags']} (같은 설비·세션 안에서만)")
        return f


class Rolling(_ColumnsStep):
    label = "rolling 통계"

    def history(self, params, target):  # 현재 제외 직전 w칸 = 이전 w칸 관측
        desc = f"rolling {params['windows']}({', '.join(params.get('stats') or ['mean'])}, 현재 제외)"
        return max(params["windows"]), {c: desc for c in params.get("columns") or [target]}

    def transform(self, data, state, params):
        from mlworkbench.pipelines.ts_frame import GROUP, SESSION

        f = data.copy()
        df = f.df
        cols = self._columns(f, params)
        stats = params.get("stats") or ["mean"]
        keys = [df[GROUP], df[SESSION]]
        for c in cols:
            prev = df[c].groupby(keys, sort=False).shift(1)  # 현재 시점 제외
            for w in params["windows"]:
                roll = prev.groupby(keys, sort=False).rolling(window=int(w), min_periods=int(w))
                for st in stats:
                    name = f"{c}_roll{w}_{st}"
                    df[name] = getattr(roll, st)().reset_index(level=[0, 1], drop=True).sort_index()
                    f.feature_cols.append(name)
        f.notes.append(f"rolling: {', '.join(cols)} × 창 {params['windows']} × {stats} (현재 시점 제외, 같은 설비·세션 안에서만, 창이 다 찬 뒤부터)")
        return f


class Diff(_ColumnsStep):
    label = "차분"

    def history(self, params, target):
        return int(params["periods"]), {c: f"차분 {params['periods']}칸" for c in params.get("columns") or [target]}

    def transform(self, data, state, params):
        from mlworkbench.pipelines.ts_frame import GROUP, SESSION

        f = data.copy()
        df = f.df
        cols = self._columns(f, params)
        p = int(params["periods"])
        g = df.groupby([GROUP, SESSION], sort=False)
        for c in cols:
            name = f"{c}_diff{p}"
            df[name] = df[c] - g[c].shift(p)
            f.feature_cols.append(name)
        f.notes.append(f"차분: {', '.join(cols)}의 현재값 - {p}칸 전 값 (같은 설비·세션 안에서만)")
        return f


class Calendar(_Base):
    def transform(self, data, state, params):
        from mlworkbench.pipelines.ts_frame import TIME

        f = data.copy()
        t = f.df[TIME].dt
        values = {"hour": t.hour, "dayofweek": t.dayofweek, "month": t.month,
                  "is_weekend": (t.dayofweek >= 5).astype("int64")}
        for name in params["features"]:
            col = f"cal_{name}"
            f.df[col] = values[name].astype("float64")
            f.feature_cols.append(col)
        f.notes.append(f"시간 특징: {', '.join(params['features'])} (기준 시각 기준)")
        return f


class Scale(_Base):
    """학습 분할에서만 fit. 상태는 열별 center·scale(JSON으로 저장 가능)."""

    def fit(self, train_data, params):
        from sklearn.preprocessing import MinMaxScaler, RobustScaler, StandardScaler

        X = train_data.df[train_data.feature_cols]
        method = params["method"]
        if method == "standard":
            sc = StandardScaler().fit(X)
            center, scale = sc.mean_, sc.scale_
        elif method == "minmax":
            sc = MinMaxScaler().fit(X)
            center, scale = sc.data_min_, sc.data_range_
        else:
            sc = RobustScaler().fit(X)
            center, scale = sc.center_, sc.scale_
        cols = list(train_data.feature_cols)
        return {"method": method, "columns": cols,
                "center": [float(v) for v in center],
                "scale": [float(v) if v and v == v else 1.0 for v in scale],
                "fit_rows": int(len(X))}

    def transform(self, data, state, params):
        f = data.copy()
        for c, m, s in zip(state["columns"], state["center"], state["scale"]):
            f.df[c] = (f.df[c] - m) / (s if s else 1.0)
        return f


_CLASSES = {"ts.session_split": SessionSplit, "ts.resample": Resample, "ts.missing": Missing,
            "ts.lag": Lag, "ts.rolling": Rolling, "ts.diff": Diff, "ts.calendar": Calendar, "ts.scale": Scale}


def timeseries_adapters(catalog: list[PluginSpec]) -> list[_Base]:
    """카탈로그의 같은 ID·버전 명세로 실제 어댑터를 만든다."""
    return [_CLASSES[s.id](_ready(s)) for s in catalog if s.id in _CLASSES and s.version == "1.0.0"]
