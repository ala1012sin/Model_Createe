"""사용자 정의 전처리 예제: 지수 가중 평균(EWM) 피처.

기준 시각 t의 피처 = 같은 설비·세션에서 t 이전 관측(x(t-1), x(t-2), ...)의 지수 가중 평균(span).
현재 값 x(t)는 넣지 않는다(한 칸 민 뒤 계산). 이전 관측이 span개 이상 있어야 값이 생긴다.
"""

from mlworkbench.plugins.sdk import UserFeature, add_feature, group_keys


class EwmFeature(UserFeature):
    def validate(self, dataset, params):
        if dataset is None or not dataset.columns:
            return []
        numeric = {c.name for c in dataset.columns if c.dtype == "numeric"}
        bad = [c for c in params.get("columns") or [] if c not in numeric]
        return [f"수치 열만 EWM에 쓸 수 있습니다: {', '.join(bad)}"] if bad else []

    def history(self, params, target):
        cols = params.get("columns") or [target]
        return max(params["spans"]), {c: f"EWM span {params['spans']}(현재 제외)" for c in cols}

    def transform(self, frame, state, params):
        f = frame.copy()
        keys = group_keys(f)
        for c in params.get("columns") or [f.target]:
            prev = f.df[c].groupby(keys, sort=False).shift(1)  # 현재 시점 제외
            for span in params["spans"]:
                ew = prev.groupby(keys, sort=False).ewm(span=int(span), min_periods=int(span)).mean()
                add_feature(f, f"{c}_ewm{span}", ew.reset_index(level=[0, 1], drop=True).sort_index())
        return f
