"""학습 모델 어댑터 계약과 XGBoost 예측 구현.

전처리 어댑터와 같은 방식으로 Registry에 명시적으로 등록한다. 의존성이 없으면 missing_dependency.
라이브러리 호출(xgboost)은 이 파일에만 둔다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from mlworkbench.core.contracts import PluginSpec


class TrainingCanceled(Exception):
    """학습 중 취소 요청을 확인해 멈췄다."""


class ModelAdapter(Protocol):
    spec: PluginSpec

    def validate(self, params: dict[str, Any]) -> list[str]: ...

    def fit(self, X: Any, y: Any, params: dict[str, Any], *, seed: int,
            on_iteration: Callable[[int, int], bool] | None = None) -> Any:
        """on_iteration(done, total)이 False를 돌려주면 학습을 멈추고 TrainingCanceled를 낸다."""
        ...

    def predict(self, model: Any, X: Any) -> Any: ...

    def save(self, model: Any, path: Path) -> None: ...

    def load(self, path: Path) -> Any: ...

    def library_versions(self) -> dict[str, str]: ...


XGB_DEFAULTS = {"n_estimators": 300, "max_depth": 6, "learning_rate": 0.1}
IMPLEMENTED_MODELS = frozenset({"timeseries.forecasting.xgboost"})


@dataclass
class XGBoostForecaster:
    spec: PluginSpec
    n_jobs: int = 2  # 다른 작업(앱 응답·GPU 작업)과 CPU를 나눠 쓰도록 제한

    def validate(self, params):
        return []

    def _estimator(self, params, seed):
        import xgboost as xgb

        p = {**XGB_DEFAULTS, **params}
        return xgb.XGBRegressor(n_estimators=int(p["n_estimators"]), max_depth=int(p["max_depth"]),
                                learning_rate=float(p["learning_rate"]), random_state=seed, n_jobs=self.n_jobs,
                                tree_method="hist", objective="reg:squarederror")

    def fit(self, X, y, params, *, seed, on_iteration=None):
        import xgboost as xgb

        est = self._estimator(params, seed)
        total = est.n_estimators
        callbacks = []
        if on_iteration is not None:
            class _CB(xgb.callback.TrainingCallback):
                def after_iteration(self, model, epoch, evals_log):  # True = 멈춤
                    return not on_iteration(epoch + 1, total)

            callbacks.append(_CB())
        est.set_params(callbacks=callbacks or None)
        est.fit(X, y, verbose=False)
        if on_iteration is not None and est.get_booster().num_boosted_rounds() < total:
            raise TrainingCanceled()
        est.set_params(callbacks=None)
        return est

    def predict(self, model, X):
        return model.predict(X)

    def save(self, model, path):
        model.save_model(str(path))  # JSON 형식(피클 아님)

    def load(self, path):
        import xgboost as xgb

        m = xgb.XGBRegressor()
        m.load_model(str(path))
        return m

    def library_versions(self):
        import xgboost as xgb

        return {"xgboost": xgb.__version__}


def model_adapters(catalog: list[PluginSpec]) -> list[XGBoostForecaster]:
    out = []
    for s in catalog:
        if s.id == "timeseries.forecasting.xgboost" and s.version == "1.0.0":
            out.append(XGBoostForecaster(s.model_copy(update={
                "status": "ready", "reason_unavailable": None, "source_library": "xgboost",
                "dependencies": ["xgboost", "sklearn", "pandas"]})))
    return out


class Throttle:
    """진행률 이벤트를 너무 자주 보내지 않도록 간격을 둔다."""

    def __init__(self, seconds: float):
        self.seconds, self.last = seconds, 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self.last >= self.seconds:
            self.last = now
            return True
        return False
