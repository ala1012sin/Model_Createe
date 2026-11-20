"""핵심 Pydantic 계약 (docs/CONTRACTS.md §2, §11).

경계 요약
- DraftPlan      : 사용자가 편집 중인 초안. 모델·목표 필드가 비어 있어도 된다 (D-10).
- PlanProposal   : LLM 정형 출력. 모델 필수.
- ExecutablePlan : 최종 검증을 통과한 실행 계획. 해시 대상(설명 텍스트 제외).
- ApprovedSnapshot : 서버가 확정한 데이터 버전·플러그인 버전·실행 설정을 하나의 해시에 묶은 불변 기록.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mlworkbench import SCHEMA_VERSION

Modality = Literal["timeseries", "vision"]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 데이터셋 (D-05)
# ---------------------------------------------------------------------------
class DatasetState(str, Enum):
    UPLOADING = "UPLOADING"
    REGISTERED = "REGISTERED"
    PROFILING = "PROFILING"
    PROFILED = "PROFILED"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    PROFILE_FAILED = "PROFILE_FAILED"


DATASET_TRANSITIONS: dict[DatasetState, set[DatasetState]] = {
    DatasetState.UPLOADING: {DatasetState.REGISTERED, DatasetState.UPLOAD_FAILED},
    DatasetState.REGISTERED: {DatasetState.PROFILING},
    DatasetState.PROFILING: {DatasetState.PROFILED, DatasetState.PROFILE_FAILED},
    DatasetState.PROFILED: {DatasetState.PROFILING},  # 재분석
    DatasetState.PROFILE_FAILED: {DatasetState.PROFILING, DatasetState.UPLOADING},  # 같은 파일 재분석 또는 수정 파일 재등록
    DatasetState.UPLOAD_FAILED: {DatasetState.UPLOADING},
}


ColumnDtype = Literal["datetime", "numeric", "categorical", "text", "empty", "excluded"]


class ColumnInfo(_Strict):
    name: str  # 원래 헤더(로컬 표시용). LLM 전송 시에는 alias 사용
    dtype: ColumnDtype
    null_pct: float | None = Field(default=None, ge=0, le=1)
    alias: str | None = None  # column_1 ...


class DatasetError(_Strict):
    """사용자용 오류. message_ko는 화면 표시용, detail은 로컬 전용(전송 금지)."""

    code: str
    stage: Literal["upload", "profile"]
    message_ko: str
    detail: dict[str, Any] = Field(default_factory=dict)


class DatasetManifest(_Strict):
    dataset_id: str = Field(pattern=r"^ds_[A-Za-z0-9_]+$")
    name: str
    modality: Modality
    source_kind: Literal["csv", "xlsx", "image_folder", "zip", "synthetic"]
    state: DatasetState = DatasetState.REGISTERED
    version: str | None = None  # 서버가 계산한 내용 해시. PROFILED 이후 확정
    selected_sheet: str | None = None
    # XLSX 출처(로컬 전용, LLM/MCP 전송 금지): source_sha256(원본 내용 해시), sheets(이름·표시 상태·대략 크기),
    # epoch(1900/1904), normalization(정규화 규칙·헤더 행·제외 수·table_sha256). version은 정규화한 표의 해시.
    xlsx: dict[str, Any] | None = None
    # 이미지(로컬 전용): {source_sha256(ZIP 원본), root_kind(zip|folder)}. 폴더 등록은 local_path가 원래 폴더(절대경로, 읽기 전용)
    images: dict[str, Any] | None = None
    upload_progress: float | None = Field(default=None, ge=0, le=1)
    row_count: int | None = Field(default=None, ge=0)
    image_count: int | None = Field(default=None, ge=0)
    columns: list[ColumnInfo] = Field(default_factory=list)
    # 비전: 사용자가 확정한 분할 역할(train_normal, calibration_normal, val, test)
    split_roles: list[str] = Field(default_factory=list)
    # ground_truth_mask, yolo_labels 등 존재하는 산출물
    artifacts: list[str] = Field(default_factory=list)
    local_path: str | None = None  # 워크스페이스 기준 서버 관리 경로. LLM/MCP 응답에 포함 금지
    size_bytes: int | None = Field(default=None, ge=0)
    profile_version: int = Field(default=0, ge=0)  # 분석 성공마다 1 증가
    error: DatasetError | None = None  # *_FAILED 상태에서만 채움
    created_at: str = Field(default_factory=utcnow_iso)


class SafeProfile(_Strict):
    """LLM에 보낼 수 있는 비식별 요약. 절대경로·원문·픽셀 없음."""

    dataset_id: str
    profile_version: int = 1
    modality: Modality
    summary: dict[str, Any] = Field(default_factory=dict)
    hidden_field_kinds: list[str] = Field(default_factory=list)


class NumericSummary(_Strict):
    min: float
    max: float
    mean: float


class ColumnProfile(_Strict):
    """S1 오른쪽 패널용 로컬 컬럼 프로파일. name·numeric은 로컬 전용."""

    alias: str
    name: str
    dtype: ColumnDtype
    null_count: int
    null_pct: float = Field(ge=0, le=1)
    distinct_count: int
    distinct_capped: bool = False  # 상한 도달(실제 고유값은 더 많음)
    parse_fail_count: int = 0  # 추정 타입으로 해석되지 않은 값 수
    numeric: NumericSummary | None = None


class IntervalEstimate(_Strict):
    status: Literal["estimated", "insufficient", "irregular"]
    seconds: float | None = None
    label: str | None = None  # 5min, 1h ...
    share: float | None = Field(default=None, ge=0, le=1)  # 가장 흔한 간격의 비율
    reason_ko: str | None = None


class TimeCandidate(_Strict):
    alias: str
    name: str
    parse_rate: float = Field(ge=0, le=1)
    format: str
    backward_steps: int  # 파일 순서상 역순 횟수
    duplicate_timestamps: int
    start: str | None = None  # 로컬 전용
    end: str | None = None  # 로컬 전용
    interval: IntervalEstimate


class ProfileNotice(_Strict):
    code: str
    severity: Literal["info", "warning"]
    message_ko: str
    detail: dict[str, Any] = Field(default_factory=dict)  # 로컬 전용


class LocalProfile(_Strict):
    """로컬 화면용 프로파일. 원래 컬럼명·최소/최대·시각 범위를 포함하므로 외부 전송 금지."""

    dataset_id: str
    dataset_version: str
    profile_version: int
    encoding: str
    delimiter: str
    row_count: int
    column_count: int
    blank_lines: int = 0
    malformed_rows: int = 0
    malformed_line_samples: list[int] = Field(default_factory=list)
    duplicate_rows: int | None = None  # None = 계산 생략(대용량)
    columns: list[ColumnProfile]
    time_candidates: list[TimeCandidate] = Field(default_factory=list)
    notices: list[ProfileNotice] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow_iso)

    def alias_map(self) -> dict[str, str]:
        return {c.alias: c.name for c in self.columns}


ImageRole = Literal["train_normal", "calibration_normal", "test_normal", "test_defect", "gt_mask", "ignore"]


class ImageFolderInfo(_Strict):
    """이미지가 직접 들어 있는 폴더 하나(상대 경로, 로컬 전용)."""

    path: str
    images: int
    formats: dict[str, int] = Field(default_factory=dict)
    role_guess: ImageRole = "ignore"
    defect_type: str | None = None  # 불량 평가 폴더의 불량 종류(폴더 이름)
    samples: list[str] = Field(default_factory=list)  # 미리보기용 상대 경로(최대 3)


class ImageProfile(_Strict):
    """비전 데이터셋 로컬 프로파일(S1-V). 폴더·파일 이름을 포함하므로 외부 전송 금지 — 전송은 SafeProfile(개수만)."""

    dataset_id: str
    dataset_version: str
    profile_version: int
    image_count: int
    formats: dict[str, int] = Field(default_factory=dict)
    sizes: list[dict[str, int]] = Field(default_factory=list)  # [{w, h, count}] 많은 순 최대 5
    channels: dict[str, int] = Field(default_factory=dict)  # "1"·"3"·"4" → 장수
    folders: list[ImageFolderInfo] = Field(default_factory=list)
    integrity: dict[str, Any] = Field(default_factory=dict)  # {method, checked, corrupt, corrupt_examples}
    mask_pairs: dict[str, int] = Field(default_factory=dict)  # {defect_images, paired}
    skipped: dict[str, int] = Field(default_factory=dict)  # {non_image, hidden, unsafe}
    notices: list[ProfileNotice] = Field(default_factory=list)
    created_at: str = Field(default_factory=utcnow_iso)


# ---------------------------------------------------------------------------
# 플러그인 (D-07, D-08)
# ---------------------------------------------------------------------------
PluginKind = Literal["preprocessor", "model", "evaluator", "dataset_adapter"]
PluginStatus = Literal["ready", "not_implemented", "missing_dependency", "invalid"]


class OrderRule(_Strict):
    """전처리 순서 제약. UI(🔒, ▲▼)와 최종 검증기가 같은 값을 쓴다."""

    stage: int = Field(default=50, ge=0, le=100)  # 작을수록 앞
    locked: bool = False  # 사용자가 순서를 바꿀 수 없음
    must_follow: list[str] = Field(default_factory=list)  # 함께 쓰일 때 반드시 뒤에 와야 하는 plugin_id


class PluginSpec(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    kind: PluginKind
    modality: Modality
    tasks: list[str] = Field(min_length=1)
    display_name_ko: str
    description_ko: str
    description: str = ""
    required_fields: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    input_data_kind: str
    output_data_kind: str
    parameter_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    dependencies: list[str] = Field(default_factory=list)
    status: PluginStatus = "not_implemented"
    reason_unavailable: str | None = None
    requires_gpu: bool = False
    order: OrderRule = Field(default_factory=OrderRule)
    source_library: str | None = None
    fit_scope: Literal["train_only", "stateless"] | None = None
    train_only: bool = False
    mask_support: bool = False
    warnings: list[str] = Field(default_factory=list)
    cost_hint: str | None = None
    # 사용자 정의 플러그인(plugins/<폴더>): 출처와 코드 내용 해시. 내장은 builtin/None.
    origin: Literal["builtin", "user"] = "builtin"
    code_sha256: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    def public_view(self) -> dict[str, Any]:
        """MCP catalog.list_* 의 읽기 전용 공개 부분."""
        return {
            "id": self.id,
            "version": self.version,
            "kind": self.kind,
            "modality": self.modality,
            "tasks": self.tasks,
            "display_name_ko": self.display_name_ko,
            "description_ko": self.description_ko,
            "parameter_schema": self.parameter_schema,
            "ready": self.ready,
            "status": self.status,
            "reason_unavailable": self.reason_unavailable,
            "requires_gpu": self.requires_gpu,
            "required_artifacts": self.required_artifacts,
            "order": self.order.model_dump(),
        }


# ---------------------------------------------------------------------------
# 계획 구성요소 (D-01)
# ---------------------------------------------------------------------------
class ToolSelection(_Strict):
    plugin_id: str
    plugin_version: str
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""  # 설명용. 실행 설정·해시에 포함하지 않음


class TimeseriesGoal(_Strict):
    modality: Literal["timeseries"] = "timeseries"
    task: Literal["forecasting"] = "forecasting"
    timestamp_col: str | None = None
    target_col: str | None = None
    group_col: str | None = None
    horizons: list[str] = Field(default_factory=list)


class VisionCalibration(_Strict):
    """보정(임계값) 정상 이미지: 역할을 지정한 폴더 또는 정상 학습에서 고정 seed로 떼어 냄(학습과 겹치지 않음)."""

    source: Literal["folder", "split_from_train"] = "split_from_train"
    fraction: float = Field(default=0.1, ge=0.05, le=0.5)
    seed: int = 0


class VisionGoal(_Strict):
    modality: Literal["vision"] = "vision"
    task: Literal["anomaly_detection", "segmentation"] = "anomaly_detection"
    train_policy: Literal["normal_only", "supervised"] | None = None
    # 폴더(상대 경로) → 역할. 사용자가 확정(폴더명 기반 추정은 기본값일 뿐)
    roles: dict[str, ImageRole] = Field(default_factory=dict)
    calibration: VisionCalibration = Field(default_factory=VisionCalibration)


UserGoal = Annotated[Union[TimeseriesGoal, VisionGoal], Field(discriminator="modality")]


class BudgetAll(_Strict):
    mode: Literal["all"] = "all"


class BudgetRecentDays(_Strict):
    mode: Literal["recent_days"] = "recent_days"
    days: int = Field(ge=1, le=36500)


class BudgetNImages(_Strict):
    mode: Literal["n_images"] = "n_images"
    count: int = Field(ge=1)
    seed: int = 0


TrainBudget = Annotated[Union[BudgetAll, BudgetRecentDays, BudgetNImages], Field(discriminator="mode")]


class ChronologicalSplit(_Strict):
    method: Literal["chronological"] = "chronological"
    train: float = Field(default=0.70, gt=0, lt=1)
    val: float = Field(default=0.15, ge=0, lt=1)
    test: float = Field(default=0.15, gt=0, lt=1)
    gap: str = "auto"  # auto = 최대 horizon

    @model_validator(mode="after")
    def _sum_to_one(self) -> "ChronologicalSplit":
        if abs(self.train + self.val + self.test - 1.0) > 1e-6:
            raise ValueError("train + val + test 비율의 합은 1이어야 합니다")
        return self


class ExplicitManifestSplit(_Strict):
    method: Literal["explicit_manifest"] = "explicit_manifest"
    train: str = "train_normal"
    calibration: str | None = None
    validation: str | None = None
    test: str = "test"
    # 보정 데이터가 따로 없을 때 정상 학습에서 떼어낼 비율 (학습 구간 확정 전 분리)
    calibration_from_train: float | None = Field(default=None, gt=0, lt=0.5)


SplitPolicy = Annotated[Union[ChronologicalSplit, ExplicitManifestSplit], Field(discriminator="method")]


class TimeseriesEvaluation(_Strict):
    kind: Literal["timeseries"] = "timeseries"
    metrics: list[Literal["mae", "rmse", "r2", "smape"]] = Field(min_length=1)
    baseline: Literal["naive_last_value", "seasonal_naive", "none"] = "naive_last_value"
    per_group: bool = False
    per_horizon: bool = True


class ThresholdRule(_Strict):
    method: Literal["calibration_normal_max", "calibration_quantile", "fixed"]
    quantile: float | None = Field(default=None, gt=0, lt=1)
    value: float | None = None

    @model_validator(mode="after")
    def _check(self) -> "ThresholdRule":
        if self.method == "calibration_quantile" and self.quantile is None:
            raise ValueError("calibration_quantile에는 quantile이 필요합니다")
        if self.method == "fixed" and self.value is None:
            raise ValueError("fixed에는 value가 필요합니다")
        return self


class VisionAnomalyEvaluation(_Strict):
    kind: Literal["vision_anomaly"] = "vision_anomaly"
    image_metrics: list[Literal["auroc", "ng_recall", "normal_fpr"]] = Field(min_length=1)
    threshold: ThresholdRule = Field(default_factory=lambda: ThresholdRule(method="calibration_normal_max"))
    # 픽셀 FPR 구간 적분. 이미지 정상 오판율과 다른 지표. GT 없으면 N/A
    pixel_metrics: list[Literal["aupro_0.05", "aupro_0.30"]] = Field(default_factory=list)


class VisionSegmentationEvaluation(_Strict):
    kind: Literal["vision_segmentation"] = "vision_segmentation"
    metrics: list[Literal["mask_map50", "mask_map50_95"]] = Field(min_length=1)


Evaluation = Annotated[
    Union[TimeseriesEvaluation, VisionAnomalyEvaluation, VisionSegmentationEvaluation],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# 계획 (D-10)
# ---------------------------------------------------------------------------
class _PlanBody(_Strict):
    schema_version: Literal["0.2"] = SCHEMA_VERSION  # type: ignore[assignment]
    dataset_id: str
    modality: Modality
    task: str
    user_goal: UserGoal | None = None
    preprocessing: list[ToolSelection] = Field(default_factory=list)
    model: ToolSelection | None = None
    train_budget: TrainBudget = Field(default_factory=BudgetAll)
    split_policy: SplitPolicy | None = None
    evaluation: Evaluation | None = None


class DraftPlan(_PlanBody):
    """편집 중 초안. 모델·분할·평가가 비어 있어도 저장·초안검증 가능."""

    # 설명용(실행 설정 아님)
    title: str = ""
    rationale: str = ""
    # goal = 목표만 저장된 상태(S2 진입 시 자동 추천 대상), manual = 사용자가 수동 편집을 선택
    source: Literal["goal", "llm", "manual", "preset", "file"] = "manual"
    # S2: 체크 해제한 전처리(실행하지 않음, 다시 켤 수 있게 보관)와 적용한 추천 기록 ID
    excluded_steps: list[ToolSelection] = Field(default_factory=list)
    recommendation_id: str | None = None


class PlanProposal(_Strict):
    """LLM 정형 출력. 코드가 아니다."""

    schema_version: Literal["0.2"] = SCHEMA_VERSION  # type: ignore[assignment]
    dataset_id: str
    modality: Modality
    task: str
    user_goal: UserGoal | None = None
    required_user_inputs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    preprocessing: list[ToolSelection] = Field(default_factory=list)
    model: ToolSelection
    train_budget: TrainBudget = Field(default_factory=BudgetAll)
    split_policy: SplitPolicy
    evaluation: Evaluation
    warnings: list[str] = Field(default_factory=list)
    rationale: str = ""

    def to_draft(self) -> DraftPlan:
        return DraftPlan(
            dataset_id=self.dataset_id,
            modality=self.modality,
            task=self.task,
            user_goal=self.user_goal,
            preprocessing=self.preprocessing,
            model=self.model,
            train_budget=self.train_budget,
            split_policy=self.split_policy,
            evaluation=self.evaluation,
            rationale=self.rationale,
            source="llm",
        )


class ExecutablePlan(_Strict):
    """최종 검증을 통과한 실행 계획. dataset_version은 서버가 채운다."""

    schema_version: Literal["0.2"] = SCHEMA_VERSION  # type: ignore[assignment]
    dataset_id: str
    dataset_version: str
    modality: Modality
    task: str
    user_goal: UserGoal
    preprocessing: list[ToolSelection]
    model: ToolSelection
    train_budget: TrainBudget
    split_policy: SplitPolicy
    evaluation: Evaluation

    def execution_config(self) -> dict[str, Any]:
        """해시 대상. 설명 텍스트(reason)는 제외한다."""
        data = self.model_dump(mode="json")
        for step in data["preprocessing"]:
            step.pop("reason", None)
        data["model"].pop("reason", None)
        return data


def execution_view(plan: _PlanBody) -> dict[str, Any]:
    """초안의 '실행에 영향을 주는 부분'. 승인 무효화 판단에 사용."""
    data = plan.model_dump(mode="json", exclude={"title", "rationale", "source", "excluded_steps", "recommendation_id"})
    for step in data.get("preprocessing", []):
        step.pop("reason", None)
    if data.get("model"):
        data["model"].pop("reason", None)
    return data


# ---------------------------------------------------------------------------
# 검증 (D-06)
# ---------------------------------------------------------------------------
class ValidationIssue(_Strict):
    code: str
    severity: Literal["error", "warning", "info"]
    path: str  # 예: preprocessing[3].params.lags
    message_ko: str
    fix_hint_ko: str | None = None


class ValidationResult(_Strict):
    mode: Literal["draft", "final"]
    issues: list[ValidationIssue] = Field(default_factory=list)
    plan_hash: str | None = None  # final + ok 일 때만. 승인 권한 아님

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}


# ---------------------------------------------------------------------------
# 승인·실행 (D-04)
# ---------------------------------------------------------------------------
class ApprovedSnapshot(_Strict):
    plan_id: str
    plan_hash: str
    dataset_id: str
    dataset_version: str
    draft_id: str
    draft_revision: int
    plugin_versions: list[str]
    plugin_code: dict[str, str] = Field(default_factory=dict)  # 사용자 플러그인 key → 승인 당시 코드 해시
    approved_by: str
    approved_at: str
    immutable_config: dict[str, Any]


class RunState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


TERMINAL_RUN_STATES = {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELED}

RUN_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.QUEUED: {RunState.QUEUED, RunState.RUNNING, RunState.CANCEL_REQUESTED, RunState.CANCELED, RunState.FAILED},
    RunState.RUNNING: {RunState.RUNNING, RunState.CANCEL_REQUESTED, RunState.SUCCEEDED, RunState.FAILED},
    RunState.CANCEL_REQUESTED: {RunState.CANCEL_REQUESTED, RunState.CANCELED, RunState.FAILED, RunState.SUCCEEDED},
    RunState.SUCCEEDED: set(),
    RunState.FAILED: set(),
    RunState.CANCELED: set(),
}

RunStage = Literal["queued", "prepare", "preprocess", "train", "evaluate", "save"]


class RunEvent(_Strict):
    run_id: str
    seq: int = Field(ge=1)
    occurred_at: str = Field(default_factory=utcnow_iso)
    state: RunState
    stage: RunStage
    progress: float | None = Field(default=None, ge=0, le=1)  # 현재 단계의 0~1, 모르면 null
    message_code: str


class RunStatus(_Strict):
    run_id: str
    state: RunState
    stage: RunStage
    last_seq: int
    events: list[RunEvent] = Field(default_factory=list)  # after_seq 이후 이벤트


class RunRecord(_Strict):
    run_id: str
    plan_id: str
    plan_hash: str
    approval_id_ref: str  # 승인 기록 참조(해시). 원본 approval_id는 저장 안 함
    dataset_id: str
    dataset_version: str
    state: RunState
    created_at: str
    idempotency_key: str
    # 파이프라인 단계(P2/P3)에서 채움: 확정 학습 범위·선택 목록 해시·실제 샘플 수 (D-01)
    resolved_train_range: dict[str, Any] | None = None
    train_sample_count: int | None = None
    # P2 실행기가 채움
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_message_ko: str | None = None  # 화면 표시용. 경로·셀 값·예외 원문 없음
    result: dict[str, Any] | None = None  # 요약 지표·분할 요약(전체는 experiments/<run_id>/)
    name: str | None = Field(default=None, max_length=60)  # 사용자가 붙인 실행 이름(ADD-UX-06). 화면 전용, LLM에 보내지 않음


class UserPreset(_Strict):
    """개인 프리셋(D-02). 불러오기는 초안으로 복사만 한다. 관리 화면은 P5."""

    task: str
    name: str
    version: int = Field(default=1, ge=1)
    train_budget: TrainBudget = Field(default_factory=BudgetAll)
    split_policy: SplitPolicy | None = None
    evaluation: Evaluation | None = None
