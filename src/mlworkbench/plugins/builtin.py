"""내장 플러그인 카탈로그(메타데이터만).

여기 명세는 모두 status="not_implemented"다. 구현이 있는 ID·버전(P2: ts.session_split·resample·missing·lag·scale,
timeseries.forecasting.xgboost)은 builtin_registry()가 같은 명세로 만든 어댑터 등록으로 교체하며,
의존성이 설치되어 있을 때만 ready가 된다. 나머지는 카탈로그·UI·LLM 목록에 보이지만 최종 검증과 제출에서 차단된다.

시간 정렬(그룹·시간 기준)은 플러그인이 아니라 시계열 엔진의 필수 입력 준비 규칙이다 (D-11).
"""

from __future__ import annotations

from mlworkbench.core.contracts import OrderRule, PluginSpec

_P2 = "P2(시계열 MVP)에서 구현 예정"
_P3 = "P3(비전 MVP)에서 구현 예정"
_P4 = "P4(모델 확장)에서 구현 예정"

_DURATION = {"type": "string", "pattern": r"^\d+(s|min|h|d)$", "examples": ["15min"]}
# x-column: 데이터셋 열 이름을 가리키는 파라미터. 추천 요청·응답에서 별칭(column_N)과 원래 이름을 변환할 때 쓴다.
_COLS = {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True, "x-column": True}


def _ts(id_, name, desc, schema, stage, *, locked=False, must_follow=(), reason=_P2, tasks=("forecasting",)):
    return PluginSpec(
        id=id_,
        version="1.0.0",
        kind="preprocessor",
        modality="timeseries",
        tasks=list(tasks),
        display_name_ko=name,
        description_ko=desc,
        input_data_kind="table.timeseries",
        output_data_kind="table.timeseries",
        parameter_schema={"type": "object", **schema},
        status="not_implemented",
        reason_unavailable=reason,
        order=OrderRule(stage=stage, locked=locked, must_follow=list(must_follow)),
    )


def builtin_specs() -> list[PluginSpec]:
    specs = [
        _ts(
            "ts.session_split", "세션 분리",
            "시간 간격이 gap보다 크게 벌어지면 새 세션으로 나눕니다. lag·rolling이 세션 경계를 넘지 않게 합니다.",
            {"properties": {"gap": _DURATION}, "required": ["gap"]}, 10, locked=True,
        ),
        _ts(
            "ts.resample", "리샘플링",
            "일정한 시간 간격으로 다시 집계해 샘플 주기를 맞춥니다.",
            {"properties": {"frequency": _DURATION, "aggregation": {"type": "string", "enum": ["mean", "median", "last", "sum", "max", "min"]}},
             "required": ["frequency", "aggregation"]}, 20, locked=True, must_follow=["ts.session_split"],
        ),
        _ts(
            "ts.missing", "결측 처리",
            "결측값을 같은 설비·세션의 직전 관측값으로 채우거나, 결측이 있는 표본을 제외합니다. "
            "다음 관측(미래)을 쓰는 보간(선형·bfill)은 예측용으로 제공하지 않습니다.",
            {"properties": {"method": {"type": "string", "enum": ["ffill", "drop"]}, "max_gap": _DURATION},
             "required": ["method"]}, 30, must_follow=["ts.resample"],
        ),
        _ts(
            "ts.lag", "lag 피처",
            "과거 시점의 값을 새 입력 피처로 추가합니다. 세션 경계를 넘지 않습니다.",
            {"properties": {"lags": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 10000},
                                      "minItems": 1, "maxItems": 64, "uniqueItems": True},
                            "columns": _COLS},
             "required": ["lags"]}, 40, must_follow=["ts.resample", "ts.missing"],
        ),
        _ts(
            "ts.rolling", "rolling 통계",
            "과거 구간의 이동 평균 등을 피처로 추가합니다. 현재 시점을 포함하지 않도록 한 칸 밀어 계산합니다.",
            {"properties": {"windows": {"type": "array", "items": {"type": "integer", "minimum": 2, "maximum": 10000},
                                         "minItems": 1, "maxItems": 32, "uniqueItems": True},
                            "stats": {"type": "array", "items": {"type": "string", "enum": ["mean", "std", "min", "max"]}, "minItems": 1},
                            "shift_first": {"type": "boolean", "const": True},
                            "columns": _COLS},
             "required": ["windows"]}, 40, must_follow=["ts.resample", "ts.missing"],
        ),
        _ts(
            "ts.diff", "차분",
            "이전 시점과의 차이를 피처로 추가합니다.",
            {"properties": {"periods": {"type": "integer", "minimum": 1, "maximum": 10000}, "columns": _COLS},
             "required": ["periods"]}, 40, must_follow=["ts.resample", "ts.missing"],
        ),
        _ts(
            "ts.calendar", "시간 특징",
            "시, 요일, 월 같은 달력 특징을 추가합니다.",
            {"properties": {"features": {"type": "array", "items": {"type": "string", "enum": ["hour", "dayofweek", "month", "is_weekend"]},
                                          "minItems": 1, "uniqueItems": True}},
             "required": ["features"]}, 40,
        ),
        _ts(
            "ts.scale", "스케일링",
            "학습 구간에서만 통계를 계산(fit)해 전체 구간을 같은 기준으로 변환합니다.",
            {"properties": {"method": {"type": "string", "enum": ["standard", "minmax", "robust"]}}, "required": ["method"]},
            60,
        ),
        PluginSpec(
            id="timeseries.forecasting.xgboost", version="1.0.0", kind="model", modality="timeseries",
            tasks=["forecasting"], display_name_ko="XGBoost 예측",
            description_ko="트리 부스팅 기반 시계열 예측 기준 모델. CPU로 학습할 수 있습니다.",
            input_data_kind="table.features", output_data_kind="forecast",
            parameter_schema={"type": "object", "properties": {
                # default = 비워 두면 쓰는 어댑터 기본값(plugins/models.py XGB_DEFAULTS와 같아야 함, 테스트로 고정)
                "n_estimators": {"type": "integer", "minimum": 10, "maximum": 5000, "default": 300},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 16, "default": 6},
                "learning_rate": {"type": "number", "minimum": 0.001, "maximum": 1.0, "default": 0.1},
            }},
            dependencies=["xgboost"], status="not_implemented", reason_unavailable=_P2,
        ),
        PluginSpec(
            id="timeseries.forecasting.lightgbm", version="1.0.0", kind="model", modality="timeseries",
            tasks=["forecasting"], display_name_ko="LightGBM 예측",
            description_ko="빠른 트리 부스팅 예측 모델.",
            input_data_kind="table.features", output_data_kind="forecast",
            parameter_schema={"type": "object", "properties": {"n_estimators": {"type": "integer", "minimum": 10, "maximum": 5000}}},
            dependencies=["lightgbm"], status="not_implemented", reason_unavailable=_P4,
        ),
        PluginSpec(
            id="vision.resize", version="1.0.0", kind="preprocessor", modality="vision",
            tasks=["anomaly_detection", "segmentation"], display_name_ko="크기 조정",
            description_ko="이미지를 지정 크기로 바꿉니다. GT 마스크는 최근접 보간으로 함께 바꿉니다.",
            input_data_kind="images", output_data_kind="images",
            parameter_schema={"type": "object", "properties": {
                "height": {"type": "integer", "minimum": 32, "maximum": 4096},
                "width": {"type": "integer", "minimum": 32, "maximum": 4096},
                "mask_interpolation": {"type": "string", "const": "nearest"},
            }, "required": ["height", "width"]},
            status="not_implemented", reason_unavailable=_P3, order=OrderRule(stage=10),
        ),
        PluginSpec(
            id="vision.normalize", version="1.0.0", kind="preprocessor", modality="vision",
            tasks=["anomaly_detection", "segmentation"], display_name_ko="정규화",
            description_ko="픽셀 값을 백본이 기대하는 평균·표준편차 기준으로 맞춥니다.",
            input_data_kind="images", output_data_kind="images",
            parameter_schema={"type": "object", "properties": {"preset": {"type": "string", "enum": ["imagenet"]}}, "required": ["preset"]},
            status="not_implemented", reason_unavailable=_P3, order=OrderRule(stage=50, must_follow=["vision.resize"]),
        ),
        PluginSpec(
            id="vision.anomaly.patchcore", version="1.0.0", kind="model", modality="vision",
            tasks=["anomaly_detection"], display_name_ko="PatchCore",
            description_ko="정상 이미지의 패치 특징을 기억해 다른 부분을 이상으로 점수화합니다. 정상 이미지만으로 학습합니다.",
            input_data_kind="images", output_data_kind="anomaly_scores+maps",
            parameter_schema={"type": "object", "properties": {
                "backbone": {"type": "string", "enum": ["wide_resnet50_2", "resnet18"]},
                "coreset_ratio": {"type": "number", "minimum": 0.001, "maximum": 1.0},
            }},
            dependencies=["torch"], status="not_implemented", reason_unavailable=_P3, requires_gpu=True,
        ),
        PluginSpec(
            id="vision.segmentation.yolo11_seg", version="1.0.0", kind="model", modality="vision",
            tasks=["segmentation"], display_name_ko="YOLO11-seg",
            description_ko="라벨(마스크/폴리곤)로 지도학습하는 세그멘테이션 모델.",
            input_data_kind="images", output_data_kind="masks",
            required_artifacts=["yolo_labels"],
            parameter_schema={"type": "object", "properties": {"epochs": {"type": "integer", "minimum": 1, "maximum": 1000}}},
            dependencies=["ultralytics"], status="not_implemented", reason_unavailable=_P4, requires_gpu=True,
        ),
    ]
    return specs
