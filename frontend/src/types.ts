// FastAPI 응답 타입 (src/mlworkbench/api/schemas.py, core/contracts.py LocalProfile 대응)

export type DatasetState =
  | "UPLOADING"
  | "REGISTERED"
  | "PROFILING"
  | "PROFILED"
  | "UPLOAD_FAILED"
  | "PROFILE_FAILED";

export interface ErrorOut {
  code: string;
  message_ko: string;
}

export interface DatasetSummary {
  dataset_id: string;
  name: string;
  source_kind: string;
  state: DatasetState;
  state_since: string | null;
  created_at: string;
  size_bytes: number | null;
  upload_progress: number | null;
  row_count: number | null;
  column_count: number | null;
  profile_version: number;
  error: ErrorOut | null;
  sheets?: SheetInfo[] | null;
  selected_sheet?: string | null;
  needs_sheet?: boolean;
  table?: TableNormalization | null;
  copied_from?: string | null;
  modality?: "timeseries" | "vision";
  image_count?: number | null;
}

export interface SheetInfo {
  name: string;
  state: "visible" | "hidden" | "veryHidden";
  rows_estimate: number;
  cols_estimate: number;
}

/** XLSX 시트를 표로 정리한 결과(규칙 xlsx-table-v1) */
export interface TableNormalization {
  rules: string;
  epoch: "1900" | "1904";
  header_row: number;
  rows: number;
  columns: number;
  empty_columns_dropped: number;
  blank_rows_skipped: number;
  rows_above_header: number;
  formula_cells: number;
}

export interface DatasetList {
  items: DatasetSummary[];
  server_time: string;
}

export type ColumnDtype = "datetime" | "numeric" | "categorical" | "text" | "empty" | "excluded";

export interface ColumnProfile {
  alias: string;
  name: string;
  dtype: ColumnDtype;
  null_count: number;
  null_pct: number;
  distinct_count: number;
  distinct_capped: boolean;
  parse_fail_count: number;
  numeric: { min: number; max: number; mean: number } | null;
}

export interface IntervalEstimate {
  status: "estimated" | "insufficient" | "irregular";
  seconds: number | null;
  label: string | null;
  share: number | null;
  reason_ko: string | null;
}

export interface TimeCandidate {
  alias: string;
  name: string;
  parse_rate: number;
  format: string;
  backward_steps: number;
  duplicate_timestamps: number;
  start: string | null;
  end: string | null;
  interval: IntervalEstimate;
}

export interface ProfileNotice {
  code: string;
  severity: "info" | "warning";
  message_ko: string;
}

export interface LocalProfile {
  dataset_id: string;
  dataset_version: string;
  profile_version: number;
  encoding: string;
  delimiter: string;
  row_count: number;
  column_count: number;
  blank_lines: number;
  malformed_rows: number;
  duplicate_rows: number | null;
  columns: ColumnProfile[];
  time_candidates: TimeCandidate[];
  notices: ProfileNotice[];
}

export interface ValidationIssue {
  code: string;
  severity: "error" | "warning" | "info";
  path: string;
  message_ko: string;
  fix_hint_ko?: string | null;
}

export interface Goal {
  timestamp_col: string | null;
  target_col: string | null;
  group_col: string | null;
  horizons: string[];
}

export interface GoalOut {
  saved: boolean;
  draft_id: string | null;
  revision: number | null;
  goal: Goal | null;
  issues: ValidationIssue[];
}

export interface ApiErrorBody {
  code: string;
  message_ko: string;
  issues?: ValidationIssue[];
}

// --- S2 전처리 -------------------------------------------------------------
export interface JsonSchema {
  type?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: (string | number)[];
  const?: unknown;
  minimum?: number;
  maximum?: number;
  items?: JsonSchema;
  minItems?: number;
  maxItems?: number;
  pattern?: string;
  examples?: string[];
  title?: string; // 사용자 플러그인이 정한 입력란 이름(내장은 화면의 한국어 이름표 사용)
  default?: unknown; // 비워 두면 쓰는 값(화면 안내용)
  "x-column"?: boolean;
}

export interface StepIn {
  plugin_id: string;
  plugin_version: string;
  params: Record<string, unknown>;
  reason: string;
}

export interface StepView extends StepIn {
  registered: boolean;
  display_name_ko: string;
  description_ko: string;
  status: "ready" | "not_implemented" | "missing_dependency" | "invalid" | "unregistered";
  status_label: string;
  reason_unavailable: string | null;
  parameter_schema: JsonSchema;
  stage: number | null;
  locked: boolean;
  must_follow: string[];
  origin?: "builtin" | "user";
}

export interface PlanView {
  dataset_id: string;
  dataset_name: string;
  goal: Goal;
  draft_id: string;
  revision: number;
  source: "goal" | "llm" | "manual" | "preset" | "file";
  recommendation: { recommendation_id: string; mode: "ready" | "preview"; source_label: string } | null;
  steps: StepView[];
  excluded: StepView[];
  model_name: string | null;
  catalog: StepView[];
  plugin_problems?: { folder: string; code: string; message_ko: string }[];
  columns: { name: string; dtype: ColumnDtype }[];
  issues: ValidationIssue[];
  skipped: { plugin_id: string; reason_ko: string }[];
}

export interface RecommendationOut {
  recommendation_id: string;
  mode: "ready" | "preview";
  source_label: string;
  steps: StepView[];
  model_name: string | null;
  rationale: string;
  warnings: string[];
}

// --- S3 학습 설정 -------------------------------------------------------------
export interface ModelOption {
  plugin_id: string;
  plugin_version: string;
  display_name_ko: string;
  description_ko: string;
  status: string;
  status_label: string;
  reason_unavailable: string | null;
  parameter_schema: JsonSchema;
  requires_gpu: boolean;
  recommended: boolean;
}

export type TrainBudget = { mode: "all" } | { mode: "recent_days"; days: number | null };

export interface SplitPolicy {
  method: "chronological";
  train: number;
  val: number;
  test: number;
  gap: string;
}

export interface TsEvaluation {
  kind: "timeseries";
  metrics: ("mae" | "rmse" | "r2" | "smape")[];
  baseline: "naive_last_value" | "seasonal_naive" | "none";
  per_group: boolean;
  per_horizon: boolean;
}

export interface TrainingFields {
  model: StepIn | null;
  train_budget: TrainBudget;
  split_policy: SplitPolicy;
  evaluation: TsEvaluation;
}

export interface TrainingView extends TrainingFields {
  dataset_id: string;
  dataset_name: string;
  draft_id: string;
  revision: number;
  goal: Goal;
  models: ModelOption[];
  defaults_applied: string[];
  preprocessing: string[];
  excluded: string[];
  issues: ValidationIssue[];
}

// --- S4 확인·승인 ---------------------------------------------------------------
export interface SummaryStep {
  plugin_id: string;
  plugin_version: string;
  display_name_ko: string;
  params: Record<string, unknown>;
  status: string;
  status_label: string;
}

export interface IssueView extends ValidationIssue {
  step: "goal" | "preprocess" | "train" | null;
}

/** 승인 상태. 제출용 승인 토큰은 서버가 화면에 보내지 않는다. */
export interface ApprovalView {
  status: "none" | "approved" | "invalidated" | "submitted";
  plan_id: string | null;
  plan_hash: string | null;
  approved_at: string | null;
  run_id: string | null;
}

export interface ConfirmationView {
  dataset_id: string;
  dataset_name: string;
  dataset_version: string | null;
  dataset_rows: number | null;
  draft_id: string;
  revision: number;
  goal: Goal;
  preprocessing: SummaryStep[];
  excluded: string[];
  model: SummaryStep | null;
  train_budget: TrainBudget;
  split_policy: SplitPolicy | null;
  evaluation: TsEvaluation | null;
  train_period: string | null;
  train_rows: number | null;
  estimated_duration: string | null;
  issues: IssueView[];
  error_count: number;
  warning_count: number;
  approval: ApprovalView;
  can_approve: boolean;
  run_available: boolean;
}

// --- P2 실행·결과 ---------------------------------------------------------------
export type RunState = "QUEUED" | "RUNNING" | "CANCEL_REQUESTED" | "SUCCEEDED" | "FAILED" | "CANCELED";
export type RunStage = "queued" | "prepare" | "preprocess" | "train" | "evaluate" | "save";

export interface RunEvent {
  seq: number;
  occurred_at: string;
  state: RunState;
  stage: RunStage;
  progress: number | null;
  message_code: string;
}

export type MetricValues = Record<string, number | null>;
export interface MetricBlock {
  n: number;
  model: MetricValues;
  baseline?: MetricValues;
}
export interface SplitMetrics {
  overall: MetricBlock;
  per_horizon?: Record<string, MetricBlock>;
  per_group?: Record<string, MetricBlock>;
}

export interface RunSplit {
  train_end: string;
  val_end: string;
  data_start: string;
  data_end: string;
  train_period: [string, string] | null;
  val_period: [string, string] | null;
  test_period: [string, string] | null;
  train_samples: number;
  val_samples: number;
  test_samples: number;
  budget: TrainBudget;
  budget_policy: string;
  gap_policy: string;
  notes: string[];
  rows_used: number | null;
  timezone_policy: string | null;
}

export interface RunView {
  run_id: string;
  dataset_id: string;
  dataset_name: string | null;
  plan_id: string;
  plan_hash: string;
  state: RunState;
  stage: RunStage;
  progress: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message_ko: string | null;
  model: string | null;
  horizons: string[];
  train_budget: TrainBudget | null;
  events: RunEvent[];
  split: RunSplit | null;
  metrics: { val?: SplitMetrics; test?: SplitMetrics } | null;
  metrics_meta: { definitions: Record<string, string>; protocol: string; baseline: string; aggregation: string } | null;
  predictions_available: boolean;
  duplicate: boolean;
  summary: { test?: MetricBlock; split?: Partial<RunSplit> } | null;
  queue_position?: number | null;
  queue_ahead?: number | null;
  max_parallel?: number;
  steps?: string[];
  duration_s?: number | null;
  protocol_key?: string | null;
  name?: string | null;
}

// --- S6 실험 비교 ------------------------------------------------------------------
export interface CompareRun {
  run_id: string;
  name?: string | null;
  dataset_id: string;
  dataset_name: string | null;
  created_at: string;
  duration_s: number | null;
  model: string | null;
  steps: string[];
  conditions: Record<string, string>;
  protocol: Record<string, unknown>;
  protocol_key: string;
  metrics: { val?: SplitMetrics; test?: SplitMetrics };
  protocol_diffs: { key: string; label_ko: string; reference: unknown; value: unknown }[];
  same_protocol: boolean;
  comparable_horizons: string[];
}
export interface CompareView {
  reference: string;
  runs: CompareRun[];
  condition_diffs: { key: string; label_ko: string; values: string[] }[];
  metric_names: string[];
  horizons: string[];
  groups: string[];
}
export interface CompareSeries {
  horizon: string;
  group: string;
  split: string;
  points_total: number;
  points_shown: number;
  target_time: string[];
  actual: number[];
  predicted: number[][];
}

// --- S7 새 데이터 적용 ------------------------------------------------------------
export interface ModelItem {
  run_id: string;
  name?: string | null;
  plan_hash: string;
  dataset_id: string;
  dataset_name: string | null;
  finished_at: string | null;
  model: string | null;
  target: string | null;
  horizons: string[];
  test?: MetricBlock | null;
}

export interface ModelRequirements extends ModelItem {
  requirements: {
    model_error: { code: string; message_ko: string } | null;
    target?: string;
    horizons?: string[];
    columns?: { name: string; role: string; why: string }[];
    steps?: { plugin_id: string; name: string; params: Record<string, unknown> }[];
    history?: string | null;
    timezone_policy?: string | null;
    extra_columns_policy?: string;
  };
}

export interface ApplyCheck {
  rows_read: number;
  rows_used: number;
  rows_time_invalid: number;
  rows_duplicate_time_merged: number;
  timezone_policy: string;
  extra_columns_ignored: string[];
  samples_total: number;
  samples_predictable: number;
  excluded: { history_short: number; no_current_target: number; missing_features: number };
  per_horizon: Record<string, number>;
  period: [string, string] | null;
  origin_period: [string, string] | null;
  groups: string[];
  beyond_last_observation: number;
}

export interface ApplyRecord {
  application_id: string;
  run_id: string;
  plan_hash: string;
  dataset_id: string;
  input: {
    name: string;
    kind?: "csv" | "xlsx";
    sha256: string | null;
    size_bytes: number;
    sheet?: string | null;
    sheets?: SheetInfo[] | null;
    table_sha256?: string | null;
    normalization?: TableNormalization | null;
  };
  state: "SHEET_REQUIRED" | "CHECK_FAILED" | "CHECKED" | "RUNNING" | "SUCCEEDED" | "FAILED";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  check: ApplyCheck | null;
  error: { code: string; message_ko: string } | null;
  result: {
    rows: number;
    groups: string[];
    origin_period: [string, string];
    target_period: [string, string];
    per_horizon: Record<string, number>;
    beyond_last_observation: number;
    preview: Record<string, string | number>[];
    columns: string[];
  } | null;
  download_available: boolean;
}

// --- 비전(V1) ----------------------------------------------------------------------
export type ImageRole = "train_normal" | "calibration_normal" | "test_normal" | "test_defect" | "gt_mask" | "ignore";
export interface ImageFolderInfo {
  path: string;
  images: number;
  formats: Record<string, number>;
  role_guess: ImageRole;
  defect_type: string | null;
  samples: string[];
}
export interface ImageProfile {
  dataset_id: string;
  dataset_version: string;
  profile_version: number;
  image_count: number;
  formats: Record<string, number>;
  sizes: { w: number; h: number; count: number }[];
  channels: Record<string, number>;
  folders: ImageFolderInfo[];
  integrity: { method?: string; checked?: number; corrupt?: number; corrupt_examples?: string[] };
  mask_pairs: { defect_images?: number; paired?: number };
  skipped: Record<string, number>;
  notices: { code: string; severity: "info" | "warning"; message_ko: string }[];
}
export interface VisionGoal {
  task: "anomaly_detection" | "segmentation";
  roles: Record<string, ImageRole>;
  calibration: { source: "folder" | "split_from_train"; fraction: number; seed: number };
}
export interface VisionGoalOut {
  saved: boolean;
  goal: VisionGoal | null;
  issues?: ValidationIssue[];
  counts?: Record<string, number>;
}
