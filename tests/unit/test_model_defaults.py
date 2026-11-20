"""ADD-UX-04: 화면에 보이는 모델 파라미터 기본값 = 실제로 쓰는 어댑터 기본값."""

import pytest

pytest.importorskip("xgboost")

from mlworkbench.core.registry import builtin_registry  # noqa: E402
from mlworkbench.plugins.models import XGB_DEFAULTS  # noqa: E402


def test_xgboost_schema_defaults_match_adapter():
    spec = builtin_registry().get("timeseries.forecasting.xgboost", "1.0.0")
    props = spec.parameter_schema["properties"]
    assert {k: props[k]["default"] for k in props} == XGB_DEFAULTS
    for k, v in XGB_DEFAULTS.items():  # 기본값이 허용 범위 안
        assert props[k]["minimum"] <= v <= props[k]["maximum"]
