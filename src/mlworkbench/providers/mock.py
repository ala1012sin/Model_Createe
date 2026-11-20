"""MockProvider — 오프라인/테스트용 결정적 추천.

카탈로그(요청에 담긴 ready 플러그인)만 사용한다. scenario로 비정상 응답을 재현할 수 있다.
"""

from __future__ import annotations

import json
from typing import Any

from mlworkbench.core.errors import ProviderError
from mlworkbench.providers.base import ProviderCapabilities, RecommendationRequest

_DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "ts.session_split": {"gap": "30min"},
    "ts.resample": {"frequency": "15min", "aggregation": "mean"},
    "ts.missing": {"method": "ffill"},
    "ts.lag": {"lags": [1, 2, 4, 8]},
    "ts.scale": {"method": "standard"},
    "timeseries.forecasting.xgboost": {"n_estimators": 300, "max_depth": 6},
    "vision.resize": {"height": 512, "width": 512, "mask_interpolation": "nearest"},
    "vision.normalize": {"preset": "imagenet"},
    "vision.anomaly.patchcore": {"backbone": "wide_resnet50_2"},
}
_REASON = {
    "ts.session_split": "측정이 끊긴 구간을 나눠 lag가 경계를 넘지 않게 합니다",
    "ts.resample": "샘플 주기를 일정하게 맞춥니다",
    "ts.missing": "리샘플 후 생긴 결측을 과거 기준으로 채웁니다",
    "ts.lag": "직전 관측값이 예측에 유용한 경우가 많습니다",
    "ts.scale": "학습 구간 기준으로 크기를 맞춥니다",
    "vision.resize": "백본 입력 크기에 맞춥니다",
    "vision.normalize": "사전학습 백본의 입력 분포에 맞춥니다",
}
_ORDER = {
    "forecasting": ["ts.session_split", "ts.resample", "ts.missing", "ts.lag"],
    "anomaly_detection": ["vision.resize", "vision.normalize"],
}
_MODEL = {"forecasting": "timeseries.forecasting.xgboost", "anomaly_detection": "vision.anomaly.patchcore"}


class MockProvider:
    def __init__(self, config_id: str = "offline_test", scenario: str = "valid", *, allow_unready: bool = False):
        """allow_unready=True: 계획 미리보기 전용. 실행 불가(미구현) 플러그인도 후보로 쓴다.
        실제 추천 경로(기본값)는 ready 플러그인만 쓴다."""
        self.config_id = config_id
        self.scenario = scenario
        self.allow_unready = allow_unready
        self.calls = 0
        self.last_request: RecommendationRequest | None = None

    async def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(tool_calling=False, json_schema_output=True, detected=True)

    async def health_check(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.config_id, "kind": "mock"}

    def _proposal(self, req: RecommendationRequest) -> dict[str, Any]:
        avail = {c["id"]: c for c in req.catalog if c.get("ready") or self.allow_unready}
        steps = []
        for pid in _ORDER.get(req.task, []):
            if pid in avail:
                steps.append({"plugin_id": pid, "plugin_version": avail[pid]["version"],
                              "params": _DEFAULT_PARAMS.get(pid, {}), "reason": _REASON.get(pid, "")})
        model_id = _MODEL.get(req.task)
        if model_id not in avail:
            raise ProviderError("추천할 수 있는 실행 가능 모델이 카탈로그에 없습니다", code="NO_READY_MODEL")
        if req.modality == "timeseries":
            split = {"method": "chronological", "train": 0.7, "val": 0.15, "test": 0.15}
            evaluation = {"kind": "timeseries", "metrics": ["mae", "rmse"], "baseline": "naive_last_value"}
        else:
            split = {"method": "explicit_manifest", "train": "train_normal", "calibration_from_train": 0.1, "test": "test"}
            evaluation = {"kind": "vision_anomaly", "image_metrics": ["auroc", "ng_recall", "normal_fpr"],
                          "threshold": {"method": "calibration_normal_max"}, "pixel_metrics": ["aupro_0.05", "aupro_0.30"]}
        return {
            "schema_version": "0.2",
            "dataset_id": req.dataset_id,
            "modality": req.modality,
            "task": req.task,
            "user_goal": req.goal.model_dump() if req.goal else None,
            "preprocessing": steps,
            "model": {"plugin_id": model_id, "plugin_version": avail[model_id]["version"],
                      "params": _DEFAULT_PARAMS.get(model_id, {}), "reason": "기준 모델"},
            "train_budget": {"mode": "all"},
            "split_policy": split,
            "evaluation": evaluation,
            "rationale": "Mock 규칙 기반 기본 추천",
        }

    async def generate_raw(self, request: RecommendationRequest, *, schema: dict[str, Any] | None = None) -> str | dict:
        self.calls += 1
        self.last_request = request
        if self.scenario == "bad_json":
            return "추천안입니다: {not json"
        if self.scenario == "bad_then_valid" and self.calls == 1:
            return '{"schema_version": "0.2"}'
        p = self._proposal(request)
        if self.scenario == "unknown_alias" and p.get("user_goal"):
            p["user_goal"]["target_col"] = "column_99"
        if self.scenario == "raw_header" and p.get("user_goal"):
            p["user_goal"]["target_col"] = "sensor_1"
        if self.scenario == "other_goal" and p.get("user_goal"):
            p["user_goal"]["horizons"] = ["2h"]
        if self.scenario == "unknown_tool":
            p["preprocessing"].append({"plugin_id": "ts.future_value", "plugin_version": "1.0.0", "params": {},
                                       "reason": "미래 값을 쓰면 정확합니다"})
        if self.scenario == "fenced":
            return "다음은 추천안입니다.\n```json\n" + json.dumps(p, ensure_ascii=False) + "\n```"
        return p
