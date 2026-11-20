"""사용자 정의 전처리 실행 래퍼. 내장 어댑터와 같은 계약(validate·fit·transform·preview)을 제공하고,
사용자 코드가 지켜야 할 규칙을 실행할 때마다 검사한다(docs/P2_PLUGIN_BRIEF.md §2 안전 장치).

1. 라벨 열(__label, 예측 대상 시각의 정답)을 숨긴 사본만 넘긴다.
2. 행 수·인덱스, 기존 열(시간·설비·세션·관측값·이전 피처) 값, 열 역할(타깃·관측 열·간격) 변경 금지.
3. 새 피처는 feature_cols 끝에 추가된 이름만. 예약어(__)·중복·목록에 없는 열 금지, float64로 바꿀 수 있어야 한다.
4. train_only 상태는 JSON으로 저장·복원 가능해야 한다(pipeline.json에 저장).
5. 과거만 사용 검사(check_causal): 전체 시각의 앞 50%·80%까지만 잘라 다시 계산해, 겹치는 행의 새 피처가 같아야 한다.
"""

from __future__ import annotations

from typing import Any

from mlworkbench.core.contracts import DatasetManifest, PluginSpec


class GuardedPreprocessor:
    def __init__(self, spec: PluginSpec, impl: Any):
        self.spec, self.impl = spec, impl

    # 오류 변환 ---------------------------------------------------------------
    def _fail(self, code: str, what: str):
        from mlworkbench.pipelines.ts_frame import PipelineError

        return PipelineError(code, f"사용자 플러그인 {self.spec.id}: {what}")

    def _call(self, name: str, *args):
        from mlworkbench.pipelines.ts_frame import PipelineError

        try:
            return getattr(self.impl, name)(*args)
        except PipelineError:
            raise
        except Exception as e:  # noqa: BLE001 - 사용자 코드 오류는 실행 실패로 기록
            raise self._fail("PLUGIN_ERROR", f"{name} 실행 중 오류({type(e).__name__})") from None

    # 계약 ------------------------------------------------------------------
    def validate(self, dataset: DatasetManifest | None, params: dict[str, Any]) -> list[str]:
        fn = getattr(self.impl, "validate", None)
        if not callable(fn):
            return []
        try:
            out = fn(dataset, params)
        except Exception as e:  # noqa: BLE001
            return [f"플러그인 검사 중 오류가 났습니다({type(e).__name__})"]
        return [str(x) for x in out] if isinstance(out, (list, tuple)) else []

    def fit(self, train_data: Any, params: dict[str, Any]) -> Any:
        if self.spec.fit_scope != "train_only":
            return None
        from mlworkbench.plugins.user_loader import is_finite_json

        state = self._call("fit", self._hide_label(train_data)[0], params)
        if not is_finite_json(state):
            raise self._fail("PLUGIN_STATE_INVALID", "학습 상태(fit 결과)를 JSON으로 저장할 수 없습니다(숫자·문자·목록·사전만, NaN 불가)")
        return state

    def transform(self, data: Any, state: Any, params: dict[str, Any]) -> Any:
        import pandas as pd

        from mlworkbench.pipelines.ts_frame import LABEL, TsFrame

        inp, label = self._hide_label(data)
        out = self._call("transform", inp, state, params)
        if not isinstance(out, TsFrame):
            raise self._fail("PLUGIN_OUTPUT_INVALID", "transform은 TsFrame을 돌려줘야 합니다")
        before = data.df
        if len(out.df) != len(before) or not out.df.index.equals(before.index):
            raise self._fail("PLUGIN_OUTPUT_INVALID", "행 수나 순서를 바꿀 수 없습니다")
        if (out.target, out.value_cols, out.freq, out.drop_missing) != (data.target, data.value_cols, data.freq, data.drop_missing):
            raise self._fail("PLUGIN_OUTPUT_INVALID", "타깃·관측 열·간격·결측 정책을 바꿀 수 없습니다")
        n = len(data.feature_cols)
        if out.feature_cols[:n] != data.feature_cols:
            raise self._fail("PLUGIN_OUTPUT_INVALID", "기존 피처 목록을 바꿀 수 없습니다(새 피처는 끝에 추가)")
        kept = [c for c in before.columns if c != LABEL]
        for c in kept:
            if c not in out.df.columns or not out.df[c].equals(before[c]):
                raise self._fail("PLUGIN_OUTPUT_INVALID", f"기존 열을 바꾸거나 지울 수 없습니다: {c}")
        new = out.feature_cols[n:]
        if len(set(new)) != len(new) or any(c in before.columns or str(c).startswith("__") for c in new):
            raise self._fail("PLUGIN_OUTPUT_INVALID", "새 피처 이름이 겹치거나 예약된 이름(__로 시작)입니다")
        extra = [c for c in out.df.columns if c not in kept and c not in new]
        if extra:
            raise self._fail("PLUGIN_OUTPUT_INVALID", "피처 목록에 없는 열을 추가했습니다: " + ", ".join(map(str, extra[:5])))
        for c in new:
            if c not in out.df.columns:
                raise self._fail("PLUGIN_OUTPUT_INVALID", f"피처 목록의 열이 표에 없습니다: {c}")
            col = out.df[c]
            if not (pd.api.types.is_numeric_dtype(col) or pd.api.types.is_bool_dtype(col)):
                raise self._fail("PLUGIN_OUTPUT_INVALID", f"새 피처는 수치여야 합니다: {c}")
            out.df[c] = col.astype("float64")
        if label is not None:
            out.df[LABEL] = label
        out.df = out.df[list(before.columns) + new]
        return out

    def preview(self, sample: Any, params: dict[str, Any]) -> Any:
        return self.transform(sample, self.fit(sample, params), params)

    def history(self, params: dict[str, Any], target: str) -> tuple[int, dict[str, str]]:
        fn = getattr(self.impl, "history", None)
        if not callable(fn):
            return 0, {}
        try:
            rows, uses = fn(params, target)
            return max(int(rows), 0), {str(k): str(v) for k, v in dict(uses).items()}
        except Exception:  # noqa: BLE001 - 안내 문구용이라 실패해도 실행은 막지 않는다
            return 0, {}

    # 과거만 사용 검사 ----------------------------------------------------------
    def check_causal(self, before: Any, after: Any, params: dict[str, Any]) -> None:
        import numpy as np

        from mlworkbench.pipelines.ts_frame import TIME

        if self.spec.fit_scope != "stateless":
            return
        new = after.feature_cols[len(before.feature_cols):]
        times = np.sort(before.df[TIME].unique())
        if not new or len(times) < 4:
            return
        for frac in (0.5, 0.8):
            cut = times[max(int(len(times) * frac) - 1, 0)]
            mask = (before.df[TIME] <= cut).to_numpy()
            part = before.copy()
            part.df = part.df[mask]
            got = self.transform(part, None, params).df[new].to_numpy("float64")
            full = after.df.loc[mask, new].to_numpy("float64")
            if not np.allclose(got, full, rtol=1e-9, atol=1e-9, equal_nan=True):
                raise self._fail("PLUGIN_FUTURE_LEAK",
                                 "기준 시각 이후의 데이터를 쓰는 피처입니다(뒤 구간을 잘라 다시 계산한 값이 달라짐). 과거 관측만 쓰도록 고쳐 주세요")

    # 내부 ------------------------------------------------------------------
    @staticmethod
    def _hide_label(data: Any):
        from mlworkbench.pipelines.ts_frame import LABEL

        inp = data.copy()
        label = inp.df.pop(LABEL) if LABEL in inp.df.columns else None
        return inp, label
