"""평가 지표. 계산식과 계산 불가 처리를 metrics_meta.json에 같이 기록한다."""

from __future__ import annotations

import math

import numpy as np

DEFINITIONS = {
    "mae": "평균(|실제 - 예측|)",
    "rmse": "제곱근(평균((실제 - 예측)²))",
    "r2": "1 - Σ(실제 - 예측)² / Σ(실제 - 평균(실제))². 표본이 2개 미만이거나 실제값 분산이 0이면 계산 불가(null)",
    "smape": "100 × 평균(2|실제 - 예측| / (|실제| + |예측|)). 분모가 0인 표본(실제=예측=0)은 오차 0으로 계산",
}


def _clean(x: float) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


def compute(y, yhat, metrics: list[str]) -> dict[str, float | None]:
    y = np.asarray(y, dtype="float64")
    yhat = np.asarray(yhat, dtype="float64")
    out: dict[str, float | None] = {}
    if len(y) == 0:
        return {m: None for m in metrics}
    err = y - yhat
    for m in metrics:
        if m == "mae":
            out[m] = _clean(np.mean(np.abs(err)))
        elif m == "rmse":
            out[m] = _clean(math.sqrt(np.mean(err ** 2)))
        elif m == "r2":
            sst = float(np.sum((y - y.mean()) ** 2))
            out[m] = None if len(y) < 2 or sst == 0 else _clean(1 - float(np.sum(err ** 2)) / sst)
        elif m == "smape":
            den = np.abs(y) + np.abs(yhat)
            term = np.where(den == 0, 0.0, 2 * np.abs(err) / np.where(den == 0, 1.0, den))
            out[m] = _clean(100 * float(np.mean(term)))
    return out
