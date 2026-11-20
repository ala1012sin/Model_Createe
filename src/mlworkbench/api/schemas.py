"""화면용 응답 스키마. 서버 절대경로·파일 내용·예외 원문·오류 detail은 담지 않는다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mlworkbench.core.contracts import DatasetManifest, ValidationIssue


class ErrorOut(BaseModel):
    code: str
    message_ko: str


class DatasetSummary(BaseModel):
    dataset_id: str
    name: str  # 원래 파일명(화면에서 일반 텍스트로 렌더링)
    source_kind: str
    state: str
    state_since: str | None  # 현재 상태가 시작된 시각(UTC) — 분석 경과 시간 계산용
    created_at: str
    size_bytes: int | None
    upload_progress: float | None
    row_count: int | None
    column_count: int | None
    profile_version: int
    error: ErrorOut | None
    # XLSX(로컬 화면 전용): 시트 목록·선택한 시트·정규화 요약. 시트 선택 전이면 needs_sheet
    sheets: list[dict] | None = None
    selected_sheet: str | None = None
    needs_sheet: bool = False
    table: dict | None = None
    copied_from: str | None = None
    modality: str = "timeseries"
    image_count: int | None = None

    @classmethod
    def of(cls, m: DatasetManifest, state_since: str | None) -> "DatasetSummary":
        x = m.xlsx or {}
        norm = x.get("normalization") or None
        return cls(
            dataset_id=m.dataset_id, name=m.name, source_kind=m.source_kind, state=m.state.value,
            state_since=state_since, created_at=m.created_at, size_bytes=m.size_bytes,
            upload_progress=m.upload_progress, row_count=m.row_count,
            column_count=len(m.columns) if m.columns else None, profile_version=m.profile_version,
            error=ErrorOut(code=m.error.code, message_ko=m.error.message_ko) if m.error else None,
            sheets=x.get("sheets"), selected_sheet=m.selected_sheet,
            needs_sheet=m.source_kind == "xlsx" and not m.selected_sheet and m.state.value == "REGISTERED",
            table={k: norm[k] for k in ("rules", "epoch", "header_row", "rows", "columns", "empty_columns_dropped",
                                        "blank_rows_skipped", "rows_above_header", "formula_cells")} if norm else None,
            copied_from=x.get("copied_from"), modality=m.modality, image_count=m.image_count,
        )


class FolderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=1000)


class VisionGoalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = "anomaly_detection"
    roles: dict[str, str] = Field(default_factory=dict)
    calibration: dict = Field(default_factory=dict)


class SheetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet: str


class DatasetList(BaseModel):
    items: list[DatasetSummary]
    server_time: str


class GoalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp_col: str | None = None
    target_col: str | None = None
    group_col: str | None = None
    horizons: list[str] = Field(default_factory=list, max_length=8)


class GoalOut(BaseModel):
    saved: bool
    draft_id: str | None = None
    revision: int | None = None
    goal: GoalIn | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)


class ApiError(BaseModel):
    code: str
    message_ko: str
    issues: list["IssueView | ValidationIssue"] | None = None  # IssueView는 고칠 화면(step) 포함


Health = Literal["ok"]


# --- S2 전처리 --------------------------------------------------------------
class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    plugin_version: str
    params: dict = Field(default_factory=dict)
    reason: str = ""


class StepView(StepIn):
    registered: bool
    display_name_ko: str
    description_ko: str
    status: str  # ready | not_implemented | missing_dependency | invalid | unregistered
    status_label: str
    reason_unavailable: str | None = None
    parameter_schema: dict = Field(default_factory=dict)
    stage: int | None = None
    locked: bool = False
    must_follow: list[str] = Field(default_factory=list)
    origin: str = "builtin"  # builtin | user(사용자 정의 플러그인)


class RecommendationInfo(BaseModel):
    recommendation_id: str
    mode: str  # ready | preview
    source_label: str


class PlanView(BaseModel):
    dataset_id: str
    dataset_name: str
    goal: "GoalIn"
    draft_id: str
    revision: int
    source: str
    recommendation: RecommendationInfo | None
    steps: list[StepView]
    excluded: list[StepView]
    model_name: str | None
    catalog: list[StepView]  # 추가할 수 있는 등록·호환 전처리
    plugin_problems: list[dict] = Field(default_factory=list)  # 불러오지 못한 사용자 플러그인(폴더 이름·사유)
    columns: list[dict]  # 열 선택 파라미터용 로컬 이름·타입
    issues: list[ValidationIssue]
    skipped: list[dict] = Field(default_factory=list)


class RecommendationOut(BaseModel):
    recommendation_id: str
    mode: str
    source_label: str
    steps: list[StepView]
    model_name: str | None
    rationale: str
    warnings: list[str]


class PlanSaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int
    steps: list[StepIn]
    excluded: list[StepIn] = Field(default_factory=list)
    source: str | None = Field(default=None, pattern="^(manual|llm)$")


class PlanValidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[StepIn]


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    expected_revision: int


# --- S3 학습 설정 ------------------------------------------------------------
class ModelOption(BaseModel):
    plugin_id: str
    plugin_version: str
    display_name_ko: str
    description_ko: str
    status: str
    status_label: str
    reason_unavailable: str | None = None
    parameter_schema: dict = Field(default_factory=dict)
    requires_gpu: bool = False
    recommended: bool = False


class TrainingView(BaseModel):
    dataset_id: str
    dataset_name: str
    draft_id: str
    revision: int
    goal: "GoalIn"
    models: list[ModelOption]
    model: StepIn | None
    train_budget: dict
    split_policy: dict
    evaluation: dict
    defaults_applied: list[str]  # 초안에 없어 기본값을 채운 항목
    preprocessing: list[str]  # 보존 중인 S2 전처리(실행 순서, 표시 이름)
    excluded: list[str]
    issues: list[ValidationIssue]


class TrainingIn(BaseModel):
    """형식 검사는 서비스가 계약 타입으로 해서 위치·한글 메시지를 돌려준다(여기서는 dict로 받음)."""

    model_config = ConfigDict(extra="forbid")

    model: dict | None = None
    train_budget: dict
    split_policy: dict
    evaluation: dict


class TrainingSaveIn(TrainingIn):
    expected_revision: int


# --- S4 확인·승인 -------------------------------------------------------------
class SummaryStep(BaseModel):
    plugin_id: str
    plugin_version: str
    display_name_ko: str
    params: dict
    status: str
    status_label: str


class IssueView(BaseModel):
    code: str
    severity: Literal["error", "warning", "info"]
    path: str
    message_ko: str
    fix_hint_ko: str | None = None
    step: Literal["goal", "preprocess", "train"] | None = None  # 고칠 화면


class ApprovalView(BaseModel):
    """승인 상태. 제출용 승인 토큰(approval_id)은 담지 않는다."""

    status: Literal["none", "approved", "invalidated", "submitted"]
    plan_id: str | None = None
    plan_hash: str | None = None
    approved_at: str | None = None
    run_id: str | None = None  # submitted: 이 승인으로 시작한 실행


class ConfirmationView(BaseModel):
    dataset_id: str
    dataset_name: str
    dataset_version: str | None
    dataset_rows: int | None
    draft_id: str
    revision: int
    goal: "GoalIn"
    preprocessing: list[SummaryStep]
    excluded: list[str]
    model: SummaryStep | None
    train_budget: dict
    split_policy: dict | None
    evaluation: dict | None
    # 실제 학습 기간·행 수·소요 시간은 파이프라인이 산출했을 때만 채운다(P1에서는 항상 None).
    train_period: str | None = None
    train_rows: int | None = None
    estimated_duration: str | None = None
    issues: list[IssueView]
    error_count: int
    warning_count: int
    approval: ApprovalView
    can_approve: bool
    run_available: bool = False  # 승인됨 + 모델 구현이 실행 가능할 때 True(S4 학습 시작)


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int
    confirmed: bool


ApiError.model_rebuild()


# --- P2 실행·결과 (S4 학습 시작, S5) ----------------------------------------------
class RunStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    plan_hash: str
    name: str | None = Field(default=None, max_length=200)  # 실행 이름(선택). 시작할 때 붙이고 나중에 바꿀 수 있음


class RunEventOut(BaseModel):
    seq: int
    occurred_at: str
    state: str
    stage: str
    progress: float | None
    message_code: str


class RunView(BaseModel):
    run_id: str
    dataset_id: str
    dataset_name: str | None
    plan_id: str
    plan_hash: str
    state: str
    stage: str
    progress: float | None  # 현재 단계의 마지막 관측 진행률(없으면 null)
    created_at: str
    started_at: str | None
    finished_at: str | None
    error_code: str | None
    error_message_ko: str | None
    model: str | None
    horizons: list[str]
    train_budget: dict | None
    events: list[RunEventOut]
    split: dict | None = None  # 실제 분할·학습 기간·표본 수(완료 후)
    metrics: dict | None = None  # metrics.json(완료 후)
    metrics_meta: dict | None = None
    predictions_available: bool = False
    duplicate: bool = False
    summary: dict | None = None  # 목록용 요약(테스트 전체 지표·분할 요약)
    queue_position: int | None = None  # 대기 중일 때 대기열 순번(1부터)
    queue_ahead: int | None = None  # 이 실행보다 먼저 돌 실행 수(실행 중 + 앞선 대기)
    max_parallel: int = 1  # 동시에 도는 학습 수
    steps: list[str] = Field(default_factory=list)  # 전처리 표시 이름(목록·비교용)
    duration_s: int | None = None  # 시작~종료 초
    protocol_key: str | None = None  # 평가 조건 짧은 해시(완료 실행). 같으면 지표를 직접 비교할 수 있음
    name: str | None = None  # 사용자가 붙인 실행 이름


class RunList(BaseModel):
    items: list[RunView]


class RunPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)  # 서버에서 60자 제한·공백 정리


class RunDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = False
    include_applications: bool = False


class DatasetDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = False
    include_runs: bool = False


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_id: str
