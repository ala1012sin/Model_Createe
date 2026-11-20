# CONTRACTS — LLM / MCP / Plugin / Experiment API 계약

> 설계 버전 0.2 / 2026-11-19. 아래 의사코드는 인터페이스 초안이다. 구현 단계에서 Pydantic으로 고정하고 테스트할 것.

## 1. ID 및 버전 규칙

- Dataset: `ds_<uuid>` / Profile: `profile_version` / 도구: `tool_id@semver` / 추천안: `proposal_id` / 승인 스냅샷: `plan_id` + `plan_hash` / 실험: `run_id`.
- 실행 시 플러그인 이름만 저장하는 게 아니라 실제 `plugin_id@version`, config, dataset hash, split manifest를 고정한다.
- 플러그인의 `display_name`이나 LLM의 자연어 설명은 실행 식별자로 사용하지 않는다.

## 2. 핵심 타입 (Pydantic 의사코드)

> P0 구현본: `src/mlworkbench/core/contracts.py`, `providers/config.py`, `providers/base.py`. 아래는 §11 결정을 반영해 구현과 맞춘 요약이다. 모든 모델은 `extra="forbid"`(정의되지 않은 필드 거절).

```python
from typing import Literal, Protocol, Any, Annotated, Union
from pydantic import BaseModel, Field

# --- Provider -------------------------------------------------------------
class ProviderConfig(BaseModel):
    id: str
    kind: Literal["openai", "openai_compatible", "mock"]
    base_url: str | None = None          # mock 외에는 필수
    model: str
    auth_mode: Literal["bearer_env", "none"] = "bearer_env"
    api_key_env: str | None = None       # 환경변수 '이름'만 (정규식 ^[A-Z_][A-Z0-9_]*$)
    timeout_s: int = Field(default=45, ge=5, le=180)
    # is_external: openai=True, openai_compatible는 loopback이 아니면 True, mock=False

class ProviderCapabilities(BaseModel):
    tool_calling: bool = False
    json_schema_output: bool = False
    json_object_output: bool = True
    max_input_chars: int = 12000
    detected: bool = False               # 실제 연결로 확인했는지

class LLMProvider(Protocol):
    async def get_capabilities(self) -> ProviderCapabilities: ...
    async def generate_raw(self, request: "RecommendationRequest", *, schema: dict | None) -> str | dict: ...
    async def health_check(self) -> dict[str, Any]: ...
# generate_plan(request) -> PlanProposal 은 ProviderGateway가 공통 제공:
#   전송 정책 → 민감정보 검사 → generate_raw → JSON 추출(fallback) → PlanProposal 검증 → 실패 시 재시도

# --- Dataset (D-05) -------------------------------------------------------
DatasetState = Literal["UPLOADING", "REGISTERED", "PROFILING", "PROFILED", "UPLOAD_FAILED", "PROFILE_FAILED"]

class DatasetManifest(BaseModel):
    dataset_id: str                      # ds_...
    name: str
    modality: Literal["timeseries", "vision"]
    source_kind: Literal["csv", "xlsx", "image_folder", "zip", "synthetic"]
    state: DatasetState
    version: str | None                  # 서버가 계산한 내용 해시(PROFILED 이후)
    columns: list[ColumnInfo]            # 시계열
    split_roles: list[str]               # 비전: train_normal, calibration_normal, val, test
    artifacts: list[str]                 # ground_truth_mask, yolo_labels ...
    local_path: str | None               # 로컬 전용. LLM/MCP로 보내지 않음

class SafeProfile(BaseModel):            # LLM 전송 가능한 비식별 요약
    dataset_id: str; profile_version: int; modality: str
    summary: dict[str, Any]; hidden_field_kinds: list[str]

# --- Plugin (D-07, D-08) --------------------------------------------------
class OrderRule(BaseModel):
    stage: int = 50                      # 작을수록 앞. 같은 stage끼리는 순서 자유
    locked: bool = False                 # UI 🔒
    must_follow: list[str] = []          # 함께 쓰이면 반드시 뒤에 와야 하는 plugin_id

class PluginSpec(BaseModel):
    id: str; version: str                # semver
    kind: Literal["preprocessor", "model", "evaluator", "dataset_adapter"]
    modality: Literal["timeseries", "vision"]
    tasks: list[str]
    display_name_ko: str; description_ko: str; description: str = ""
    required_fields: list[str] = []
    required_artifacts: list[str] = []
    input_data_kind: str; output_data_kind: str
    parameter_schema: dict[str, Any]     # JSON Schema 부분집합, additionalProperties=false 기본
    dependencies: list[str] = []
    status: Literal["ready", "not_implemented", "missing_dependency"] = "not_implemented"
    reason_unavailable: str | None = None
    requires_gpu: bool = False
    order: OrderRule
    # ready (property) == (status == "ready")

# --- Plan 구성요소 (D-01) -------------------------------------------------
class ToolSelection(BaseModel):
    plugin_id: str; plugin_version: str
    params: dict[str, Any] = {}
    reason: str = ""                     # 설명용. 실행 설정·해시에서 제외

class TimeseriesGoal(BaseModel):         # user_goal (modality로 구분)
    modality: Literal["timeseries"]; task: Literal["forecasting"]
    timestamp_col: str | None; target_col: str | None; group_col: str | None
    horizons: list[str]                  # "15min", "1h"
class VisionGoal(BaseModel):
    modality: Literal["vision"]; task: Literal["anomaly_detection", "segmentation"]
    train_policy: Literal["normal_only", "supervised"] | None

TrainBudget = Union[                     # mode로 구분, split_policy와 별도 필드
    {"mode": "all"}, {"mode": "recent_days", "days": int}, {"mode": "n_images", "count": int, "seed": int}]
SplitPolicy = Union[
    ChronologicalSplit,      # method="chronological", train/val/test 비율(합=1), gap="auto"
    ExplicitManifestSplit]   # method="explicit_manifest", train/calibration/validation/test 역할, calibration_from_train
Evaluation = Union[
    TimeseriesEvaluation,         # kind="timeseries", metrics[mae|rmse|r2|smape], baseline, per_group, per_horizon
    VisionAnomalyEvaluation,      # kind="vision_anomaly", image_metrics[auroc|ng_recall|normal_fpr], threshold, pixel_metrics[aupro_0.05|aupro_0.30]
    VisionSegmentationEvaluation] # kind="vision_segmentation", metrics[mask_map50|mask_map50_95]

# --- Plan 경계 (D-10) -----------------------------------------------------
class DraftPlan(BaseModel):              # 편집 중 초안. model/split/evaluation/user_goal 비어도 됨
    schema_version: "0.2"; dataset_id: str; modality: str; task: str
    user_goal: TimeseriesGoal | VisionGoal | None = None
    preprocessing: list[ToolSelection] = []
    model: ToolSelection | None = None
    train_budget: TrainBudget = {"mode": "all"}
    split_policy: SplitPolicy | None = None
    evaluation: Evaluation | None = None
    title: str = ""; rationale: str = ""; source: Literal["llm", "manual", "preset", "file"]

class PlanProposal(BaseModel):           # LLM 정형 출력. model·split_policy·evaluation 필수
    ...DraftPlan의 실행 필드...
    required_user_inputs: list[str] = []; assumptions: list[str] = []
    warnings: list[str] = []; rationale: str = ""

class ExecutablePlan(BaseModel):         # 최종 검증 통과본. 모든 필드 필수
    ...실행 필드...; dataset_version: str   # 서버가 확정한 값
    # plan_hash = sha256(정규화 JSON(execution_config()))  — reason/rationale 제외

# --- Validation (D-06) ----------------------------------------------------
class ValidationIssue(BaseModel):
    code: str                            # UNKNOWN_PLUGIN, PLUGIN_NOT_READY, INVALID_PARAM, ORDER_VIOLATION ...
    severity: Literal["error", "warning", "info"]
    path: str                            # preprocessing[2].params.lags
    message_ko: str
    fix_hint_ko: str | None = None

class ValidationResult(BaseModel):
    mode: Literal["draft", "final"]
    issues: list[ValidationIssue]
    plan_hash: str | None = None         # final + ok 일 때만. 제출 권한 아님
    # ok / errors / warnings 는 issues에서 계산

# --- Approval / Run (D-04) ------------------------------------------------
class ApprovedSnapshot(BaseModel):
    plan_id: str; plan_hash: str
    dataset_id: str; dataset_version: str
    draft_id: str; draft_revision: int
    plugin_versions: list[str]           # ["ts.lag@1.0.0", ...]
    approved_by: str; approved_at: str
    immutable_config: dict[str, Any]     # ExecutablePlan.execution_config()
# approval_id는 UI(호스트)에만 반환하고 DB에는 해시만 저장한다.

RunState = Literal["QUEUED", "RUNNING", "CANCEL_REQUESTED", "SUCCEEDED", "FAILED", "CANCELED"]
class RunEvent(BaseModel):
    run_id: str; seq: int                # 실행별 1부터 단조 증가
    occurred_at: str; state: RunState
    stage: Literal["queued", "prepare", "preprocess", "train", "evaluate", "save"]
    progress: float | None               # 현재 단계 0~1, 모르면 None
    message_code: str
class RunStatus(BaseModel):
    run_id: str; state: RunState; stage: str; last_seq: int
    events: list[RunEvent]               # after_seq 이후

class RunRecord(BaseModel):
    run_id: str; plan_id: str; plan_hash: str; approval_id_ref: str
    dataset_id: str; dataset_version: str; state: RunState
    created_at: str; idempotency_key: str
    resolved_train_range: dict | None = None   # P2/P3 파이프라인이 채움 (D-01)
    train_sample_count: int | None = None

class UserPreset(BaseModel):             # D-02. 관리 화면은 P5
    task: str; name: str; version: int
    train_budget: TrainBudget; split_policy: SplitPolicy | None; evaluation: Evaluation | None
```

## 3. 모델 추천 결과 JSON 예제 (LLM의 필수 출력)

```json
{
  "schema_version": "0.2",
  "dataset_id": "ds_sample_01",
  "modality": "timeseries",
  "task": "forecasting",
  "user_goal": {"modality": "timeseries", "task": "forecasting", "timestamp_col": "timestamp",
                "target_col": "sensor_1", "group_col": "device_id", "horizons": ["15min"]},
  "required_user_inputs": [],
  "assumptions": ["timestamp가 UTC로 파싱되며 target은 센서 측정값"],
  "preprocessing": [
    {"plugin_id":"ts.resample","plugin_version":"1.0.0","params":{"frequency":"15min","aggregation":"mean"},"reason":"샘플 주기 정규화"},
    {"plugin_id":"ts.lag","plugin_version":"1.0.0","params":{"lags":[1,2,4,8]},"reason":"과거 관측을 입력 특징으로 사용"}
  ],
  "model": {"plugin_id":"timeseries.forecasting.xgboost","plugin_version":"1.0.0","params":{"n_estimators":300},"reason":"소규모 센서 예측의 기준 모델"},
  "train_budget": {"mode": "all"},
  "split_policy": {"method":"chronological","train":0.7,"val":0.15,"test":0.15,"gap":"auto"},
  "evaluation": {"kind":"timeseries","metrics":["mae","rmse"],"baseline":"naive_last_value"},
  "warnings": [],
  "rationale": "시간순 평가를 유지하고 리샘플/lag를 사용한 baseline 실험 제안"
}
```

*실제 요청 때 `schema_version`, `dataset_id`, modality/task, 등록된 플러그인/버전은 백엔드가 제공하고, LLM이 임의로 결정한 임계값이나 누락된 필수 입력은 검증에서 거절.*

## 4. 추천 요청 시 데이터 형태

```json
{
  "goal": {"modality": "vision", "task": "anomaly_detection", "user_confirmed": true},
  "safe_dataset_profile": {
    "dataset_id": "ds_example",
    "relative_folders": ["train/good", "test/good", "test/defect", "ground_truth"],
    "image_counts": {"train_normal": 240, "test_normal": 20, "test_defect": 42},
    "mask_pairing_rate": 1.0,
    "size_distribution": {"min": [512, 512], "max": [1024, 1024]}
  },
  "catalog": [
    {"id": "vision.anomaly.patchcore", "version": "1.0.0", "ready": true, "task": "anomaly_detection"},
    {"id": "vision.segmentation.yolo11_seg", "version": "1.0.0", "ready": true, "task": "segmentation", "requires_masks": true}
  ]
}
```

- `safe_dataset_profile`만 전달. 절대경로, 원시 이미지 픽셀, CSV 원문, API 키는 전달 금지.
- 외부 공급자 전송 정책: 기본 `allow_external_data=false`. 최초 사용 시 전송 요약 미리보기와 명시적 허용 필요. 이후 정책도 사용자가 변경 가능.
- 사용자 확인 없는 태스크/타깃은 `required_user_inputs`를 반환하며 실행 계획 확정 불가.

## 5. MCP 도구의 입력/출력 계약

### `catalog.list_preprocessors` 및 `catalog.list_models`

입력: `{"modality":"timeseries", "task":"forecasting"}`.  
출력: plugin spec의 **읽기 전용 공개 부분**(`id`, `version`, `description`, `parameter_schema`, `ready`, `reason_unavailable`, `capabilities`, `requires_gpu`). 추천 대상은 `ready=true`와 정확히 일치하는 버전으로 한정.

### `dataset.get_profile`

입력: `{"dataset_id":"ds_x"}`.  
출력: 허용된 `safe_profile`, profile_version, 데이터 검증 오류와 숨긴 민감 필드 종류. 민감정보를 조회하는 별도 MCP tool은 초기 범위에서 제공하지 않는다.

### `pipeline.validate`

입력: `{"candidate_plan": <PlanProposal>}`.  
출력: `ValidationResult`. 검사 항목:

1. JSON Schema/Pydantic 구조 및 버전.
2. 플러그인 존재/설치/실제 구현 완료/정확한 버전.
3. modality, task, 데이터 형식, 입력/출력 포맷 연결.
4. 예측 타깃·horizon·이미지 GT/mask/라벨 존재 여부.
5. 전처리 단계 순서·미래 정보 누수/스플릿 안전성.
6. 모델 하이퍼파라미터 범위, GPU/메모리 최소 요구 확인.
7. 데이터 경로·권한, 미승인 업로드/학습 위험.

### `pipeline.preview`

입력: `{"validated_plan_hash":"sha256...", "max_rows":500, "max_images":8}`.  
출력: 가공된 작은 샘플, feature 변동, 누락·오류 경고. 제한된 실행으로 외부 DB/네트워크 작업 금지. 샘플에 대해서도 fit 위치/평가데이터 취급 규칙 지킬 것.

### `experiment.submit`

입력: `{"approved_plan_id":"plan_x", "approval_id":"approve_x"}`.  
출력: `{"run_id":"run_x", "state":"QUEUED"}` 또는 거부 이유.

**필수 서버 검증:** 인증된 사용자 승인 기록 확인 + 승인 plan hash 일치 + 수정되지 않은 dataset/version/plugin/split + 동시 학습 자원 확인. LLM은 `approval_id`를 직접 보지 못하고 생성할 수 없다. 같은 승인 요청이 재전송되어도 중복 run 생성 방지(idempotency key).

### `experiment.cancel`

입력: `{"run_id":"run_x", "confirmation_id":"confirm_x"}`.  
출력: `cancel_requested=true` 또는 거부 이유. 실제 worker 종료와 큐 상태 전이를 기록.

### `experiment.get_results`

출력: 평가 split/threshold/normal FPR vs pixel FPR를 구분한 metrics, 평가 가능한 항목, `N/A` 이유, 산출물 참조. 원시 데이터 경로는 UI 권한에 따라서만 열기.

## 6. Provider 교체 구현 전략

```text
Settings Page
  ├── Provider=OpenAI / OpenAI-Compatible / Mock
  ├── model, base_url, timeout, response_format policy
  └── api_key_env (값은 화면·로그에 노출하지 않음)
                  ↓
       ProviderFactory.from_config()
                  ↓
     common generate_plan(...) contract
                  ↓
     schema validation + normalization
                  ↓
           PlanProposal
```

- `OpenAIProvider`와 `OpenAICompatibleProvider`는 request/response adapter를 분리할 것. provider-specific tool calling의 구조를 내부 `ToolCall` 타입으로 변환.
- 모델이 `tool_calling`을 지원하면 읽기 전용 MCP 도구 호출을 허용하고, 그렇지 않으면 backend가 카탈로그/프로파일을 미리 조회해 단일 JSON 추천 요청으로 제공.
- 정형 출력 지원이 약하면 JSON 추출 후 Pydantic 검증, schema 오류 보고, 제한된 재시도. 도구 호출 파싱 성공이 곧 실행 승인임을 의미하지 않음.
- 모델이 생성한 Tool id/arg는 반드시 catalog·Pydantic과 맞춰 검증. 미지원 tool/가짜 모델은 보여줄 수 있지만 `not_implemented`로 명확히 표시하고 실행 차단.
- 공급자 교체시 이미 승인된 plan/run 결과가 변경되면 안 됨.

## 7. 플러그인 계약과 예시

```python
# 의사코드: src/mlworkbench/plugins/preprocessing/timeseries/add_diff.py
class AddDiffFeature:
    plugin_id = "ts.diff"
    version = "1.0.0"

    def __init__(self, column: str, periods: int):
        self.column = column
        self.periods = periods

    def fit(self, train, context):
        return self

    def transform(self, frame, context):
        # 실제 구현: group/time/session 정렬과 경계 검사 필요
        result = frame.copy()
        result[f"{self.column}_diff_{self.periods}"] = result[self.column].diff(self.periods)
        return result
```

- 새 플러그인을 추가하면 `PluginSpec + Python 구현 + 테스트`를 등록. Registry 자동 발견 후 UI 드롭다운과 LLM 목록에도 노출되어야 한다.
- 스키마가 충족되지 않는 custom plugin은 로드하지 않고 사용 불가 사유 노출.
- `from zip import arbitrary code`, 동적 임의 `pip install` 및 LLM 생성 코드 실행 기능은 초기 구현에서 금지.
- 정상-only 모델의 `score_images`/`anomaly_maps`와 segmentation 모델의 `predict_masks`는 출력 타입 분리. 비전/시계열 공통 `fit,predict`로 강제 통일하지 않는다.

## 8. 운영/테스트 사례

| 사례 | 기대 동작 |
|---|---|
| OpenAI 실패/키 없음 | 사용자에게 오류 안내 + Mock/수동 플랜으로 계속 진행 |
| 로컬 Ollama 변경 | `base_url`, `model`, auth 설정만 변경; 추천안 스키마는 동일 |
| 로컬 모델이 tool calling 미지원 | 읽기 전용 목록을 백엔드가 조회한 뒤 JSON 추천 방식으로 fallback |
| LLM이 미등록 `ts.future_value` 추천 | 검증 오류; 실행 불가 |
| 정상 이미지에 YOLO-seg만 추천 | GT 부재/태스크 불일치라면 validator 거절 |
| 사용자 전처리 파라미터 변경 후 승인 | 새 plan_hash 발급; 이전 승인은 무효 |
| LLM이 `experiment.submit` 호출 시도 | 모델 노출 도구가 아니며 승인 토큰 없음 → 거부 |
| raw 회사 이미지/엑셀을 외부 provider로 전송 시도 | 전송 정책에서 차단하고 사용자에게 이유 표시 |
| AU-PRO 요청하지만 GT 마스크 부재 | 지표 `null` + `missing_gt_masks`, 0점으로 기록하지 않음 |
| 같은 run 설정, 새 provider 전환 | 기존 승인 설정/추론 결과와 관계없이 run 재현 가능 |

## 9. 보안 모델 및 권한

- **사용자 소유 도구**라도 실행 통제를 분리해야 한다. UI에서 확인하지 않은 실행 명령은 backend가 거부.
- 데이터셋 메타데이터(파일명/컬럼명/폴더명)에도 공격 문구가 삽입될 수 있어, 프롬프트의 명령으로 취급하지 않는다.
- `workspace/` 밖 경로에는 기본 접근하지 않음. 파일/디렉터리 허용 목록, 데이터 샌드박스, 로그의 민감정보 마스킹.
- 로컬 Ollama를 사내 네트워크에 연결할 때 API에서 사용하는 더미 키는 보안 장치가 아님. 사내 방화벽·프록시 인증·TLS 등을 별도 적용.
- 외부 API 사용 시 조직의 데이터 취급 정책/비용/키 관리, 회사 서버 설치 시 사내 관리자 승인 필요.

## 10. 품질 게이트

- 정형 JSON 스키마 일치율(유효성 검사 통과), 추천 tool 적합성, 승인 전 훈련 0회, 테스트 누수 0회, 결과 재현성/환경 기록, 지표 정확성.
- **LLM 추천 자체의 품질 측정:** 합성 시계열/비전 데이터셋 5~10개에 대해 기본 권장 파이프라인의 타당성·실행 가능성·환각 tool 비율을 측정. 모델 변경 전후 동일 fixture로 회귀 검사.
- 통계적 benchmark 수치가 달라 보이는 경우 GT/스플릿/해상도/평균 방식 차이를 먼저 확인. 평가가 다른 조건이면 직접적인 성능 우위로 표현하지 않기.

## 11. 화면 설계 검토 후 구현 기준

이 절을 `SCREEN_DESIGN.md` §13의 결정 기록으로 사용한다. 기존 의사코드나 예시와 충돌하면 이 절을 따른다. 각 항목의 구현 시점은 `IMPLEMENTATION_BRIEF.md`에 정리한다.

| ID | 계약 결정 |
|---|---|
| D-01 | 실행 계획에 `train_budget`을 `split_policy`와 별도 필드로 둔다. 태스크에 맞게 `all`, `recent_days(days)`, `n_images(count, seed)` 중 하나를 사용한다. 전체 데이터에서 학습·검증·테스트와 필요한 보정 구간을 먼저 확정하고 학습 구간에만 적용한다. 최근 N일의 기준점은 학습 구간의 마지막 시점이다. 확정된 범위 또는 선택 목록 참조·해시, 실제 학습 샘플 수를 실행과 함께 저장한다. 예측 목표 필드는 `user_goal`로 모으고, 평가 설정은 태스크별 검증 가능한 타입을 쓴다. |
| D-02 | 개인 프리셋에는 태스크·이름·버전과 학습량·분할·평가 기본값을 저장한다. 불러오기는 초안으로 복사한다. 실행에는 사용한 값을 그대로 남긴다. |
| D-03 | P2 시계열과 P3 비전의 완료 범위에 **로컬 배치 추론**을 포함한다. 앱의 추론 서비스가 입력 점검과 실행을 제공한다. 모델 버전은 원본 `run_id`, `plan_hash`, 아티팩트 참조, 저장된 전처리와 입력·출력 계약, 필요한 판정 임계값에 연결한다. 시계열 입력에 필요한 관측 이력과 컬럼은 저장된 피처 구성에서 결정한다. 타깃의 lag 또는 rolling을 썼다면 과거 타깃 열이 필요하다. 예측 CSV에는 모델 버전, 기준 시각, 예측 대상 시각 또는 horizon을 남긴다. 추론 MCP 노출은 외부 클라이언트 연동 범위에서 결정한다. |
| D-04 | 실행기가 `RunEvent(run_id, seq, occurred_at, state, stage, progress, message_code)`를 발행한다. `seq`는 실행별 단조 증가하며 `progress`는 현재 단계의 0~1 값 또는 null이다. 상태 응답에는 최신 상태와 마지막 순번을 포함하고, 폴링 시 이후 이벤트를 돌려줄 수 있어야 한다. UI는 진행률이 없으면 실제 단계와 경과 시간을 표시한다. 취소 요청과 실제 종료를 별도 상태로 다룬다. |
| D-05 | 데이터 상태는 `UPLOADING`, `REGISTERED`, `PROFILING`, `PROFILED`, `UPLOAD_FAILED`, `PROFILE_FAILED`를 구분한다. 목표 확정은 별도 초안 상태다. 업로드 진행률은 관측 가능한 경우 표시한다. |
| D-06 | 검증 문제는 `code`, `severity`, `path`, 한글 메시지, 선택적 수정 힌트를 갖는다. 초안 검증은 입력 중인 필드를 허용하면서 현재 조합을 점검한다. 최종 검증은 필수값 누락과 호환성 오류를 차단한다. |
| D-07 | 플러그인 카탈로그에서 `display_name_ko`, `description_ko`로 한글 표시 정보를 제공한다. 추천 `reason`은 화면 설명에 사용한다. |
| D-08 | 엔진과 UI는 같은 순서 제약을 사용한다. 플러그인 또는 파이프라인 규칙에 필수 선행 단계와 고정 여부를 선언하고, 최종 검증기가 실제 실행 순서를 검증한다. |
| D-09 | 전처리 미리보기는 서버가 해당 부분을 검증한 초안 리비전으로 실행한다. 모델이 미정이어도 전처리 검증을 통과하면 미리볼 수 있다. 데이터셋 버전과 초안 리비전이 달라지면 다시 검증한다. 미리보기 검증 결과에는 제출 권한이 없다. |
| D-10 | `DraftPlan.model`은 선택적이다. LLM `PlanProposal.model`은 필수로 유지한다. 승인 가능한 실행 계획에도 정확한 모델 버전이 필요하다. 사용자 변경은 새 초안 리비전을 만들며, 이전 승인은 새 제출에 사용할 수 없다. 이미 시작된 실행은 당시 스냅샷을 계속 사용한다. |
| D-11 | 그룹·시간 기준 정렬은 시계열 엔진의 필수 입력 준비 규칙이다. 화면에는 자동 적용으로 표시한다. |

### 실행 기록 저장과 MLflow

워크벤치가 데이터 등록·개인 프리셋·승인·작업 상태를 관리한다. 실험 기록과 모델 아티팩트는 저장 인터페이스 뒤에 두고, MLflow 연동 시 같은 인터페이스에서 `run_id`와 모델 버전 참조를 매핑한다. 일반 화면은 워크벤치의 한글 UI로 제공한다. P0는 로컬 저장만 사용한다.

### P0 구현 메모 (2026-11-19, 설계 보완 기록)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 플러그인 실행 가능 여부 | `ready: bool` 대신 `status`(`ready`/`not_implemented`/`missing_dependency`) + `reason_unavailable`. `ready`는 파생 속성 | 미구현과 의존성 부족을 화면에서 다른 사유로 보여주기 위함. P0 내장 카탈로그 14개는 모두 `not_implemented` |
| Provider 계약 | 공급자는 `generate_raw`만 구현, `generate_plan`은 Gateway 공통 | JSON fallback·스키마 검증·재시도를 공급자마다 중복 구현하지 않기 위함. 새 공급자 추가 시 파이프라인 코드 수정 없음 |
| 승인 1회성 | 승인 1건 → 실행 최대 1건. 같은 승인 재전송은 기존 `run_id` 반환(중복 등록 없음). 다시 돌리려면 다시 확정 | 재전송 멱등성과 "승인한 그 실행만" 원칙을 단순하게 보장 |
| 승인 무효화 판단 | 초안 리비전마다 실행 설정 해시를 저장. 해시가 바뀐 수정만 승인 무효화(설명·추천 이유 수정은 유지) | IMPLEMENTATION_BRIEF "요약·추천 이유 편집과 학습 설정 변경 구분" |
| 제출 시 서버 재검증 | 스냅샷 해시 무결성, 확정 리비전 이후 실행 설정 변경 여부, 데이터셋 현재 버전·상태, 플러그인 버전·실행 가능 여부 | CONTRACTS §5 submit 필수 검증. GPU 자원 확인은 worker가 생기는 P2 |
| 시간 정렬 (D-11) | 플러그인이 아니라 시계열 엔진 입력 준비 규칙. 전처리 목록에 넣지 않음 | 순서 오류 가능성 제거 |
| 순서 stage | session_split 10(🔒) → resample 20(🔒) → missing 30 → lag/rolling/diff/calendar 40 → scale 60 | 같은 피처 단계끼리는 순서 자유 |
| 비전 보정 분리 | `ExplicitManifestSplit.calibration_from_train` 추가(보정 폴더가 없을 때) | SCREEN_DESIGN S1-V "정상 학습에서 10% 떼어내기" |
| SQLite 저널 | `journal_mode=TRUNCATE` | 파일 삭제가 제한된 동기화·가상 드라이브에서 기본 방식이 `disk I/O error`로 실패하는 것을 실제로 확인 |
| MCP 전송 | P0는 카탈로그·allowlist·Facade만. stdio/HTTP 전송은 P1 | IMPLEMENTATION_BRIEF 범위 |

### P1 CSV 구현 메모 (2026-11-19)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 계약 추가 | `DatasetError{code, stage, message_ko, detail}`, `LocalProfile`(ColumnProfile·TimeCandidate·IntervalEstimate·ProfileNotice), `DatasetManifest.size_bytes/profile_version/error`, `ColumnInfo.alias`, dtype `empty` | S1 오른쪽 패널·실패 표시용. `detail`·로컬 프로파일은 전송 금지 |
| 상태 전이 | `PROFILE_FAILED → UPLOADING` 추가(수정한 파일로 같은 데이터셋에 재등록). 성공한 데이터셋에는 재등록 불가 | 명시적 재시도 흐름 |
| 저장 | `workspace/datasets/<id>/source.csv`(서버 관리 경로), `.upload.part`에 쓰고 완료 시 이름 변경. 버전 = 저장 바이트 sha256. 실패 시 부분 파일·이전 파일 제거, 레코드는 경로·버전 없음 | 부분 파일·성공처럼 보이는 실패 레코드 방지 |
| 원본 무결성 | 분석 시 저장 파일 해시를 다시 계산해 버전과 다르면 `SOURCE_CHANGED` | 등록 후 파일이 바뀐 상태로 분석되는 것 방지 |
| 프로파일 저장 | 프로파일 저장과 PROFILED 전이를 한 트랜잭션으로. 재분석 시작 시 기존 프로파일 삭제, 조회는 PROFILED + 버전 일치일 때만 | 실패 후 이전 성공 결과가 남아 보이지 않게 |
| CSV 처리 기준 | UTF-8(BOM)→CP949 순 인코딩, 구분자 자동(, ; 탭 \|), 첫 행 헤더, 빈 이름·중복 이름·숫자만 있는 헤더는 실패, 빈 줄 건너뜀, 열 개수 불일치 행은 1% 이하면 제외+경고·초과면 실패, 타입 기준 98% | `csv_profile.py` 모듈 설명에 동일 기록 |
| 시간열 후보 | datetime 열만 후보. 간격은 고유 시각 정렬 후 최빈 간격(비율 50% 미만이면 irregular, 고유 시각 3개 미만이면 insufficient). 숫자열은 epoch 등 모호해 시각으로 보지 않음 | 여러 설비가 섞인 파일에서도 간격 추정 |
| SafeProfile | allowlist로 생성: 별칭·타입·결측률·고유값 수·행/중복/제외 수·시간 후보(별칭·해석률·간격)·알림 코드. 최소/최대·시각 범위·원래 헤더·파일명·경로·줄 번호·오류 원문 제외. 모든 문자열 값이 정해진 어휘인지 마지막에 검사 | 예상하지 못한 필드로 원본이 섞이는 것을 막는 2중 장치 |
| 남은 공백 | `RecommendationRequest.goal`의 컬럼명(timestamp_col 등)은 아직 원래 헤더. 추천 연결 작업에서 별칭으로 바꾸고 응답을 되돌리는 매핑 필요 | P1 추천 단계에서 처리 |

### P1 React·FastAPI 구현 메모 (2026-11-19)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 웹 API | `GET /api/datasets`(목록+server_time), `GET /api/datasets/{id}`, `POST /api/datasets[?retry_of=id]`(본문=CSV 바이트, 헤더 `X-File-Name`=URI 인코딩 파일명), `POST /api/datasets/{id}/profile`(재분석), `GET /api/datasets/{id}/profile`(LocalProfile), `GET·PUT /api/datasets/{id}/goal` | 기존 DatasetService·PlanService를 그대로 호출하는 얇은 계층 |
| 응답 위생 | 화면용 요약만: `local_path`·오류 `detail`·예외 원문·서버 경로 제외. 처리되지 않은 오류는 `INTERNAL_ERROR` + 한글 문구만 | 브리프 "절대경로·파일 내용·예외 원문 금지" |
| 업로드 | multipart 대신 원본 바이트 스트림 → 큐 → 스레드의 `register_csv`. 기존 크기 제한·`.upload.part`·해시를 그대로 사용 | 전체 파일을 메모리·임시 폴더에 두 번 쓰지 않음 |
| 분석 실행 | 등록 응답 후 백그라운드. 기본은 **별도 프로세스(spawn) 2개**. 테스트용으로 스레드 모드 선택 가능 | 스레드 모드에서 80만 행 분석 중 목록 응답이 1.39초까지 밀리는 것을 브라우저 검증에서 확인(GIL). 프로세스 분리 후 최대 9ms |
| 재시작 복구 | 앱 시작 시 UPLOADING→UPLOAD_FAILED, PROFILING→PROFILE_FAILED(`INTERRUPTED`), REGISTERED는 분석 재개 | 진행 중으로 남는 레코드 방지 |
| 목록 순서 | 등록 순서(rowid) 역순. 재등록해도 위치 유지 | 같은 초에 등록된 항목 순서 불안정 해결 |
| 목표 저장 | `PlanService.save_goal`: 데이터셋별 최신 초안에 `user_goal` 저장. 목표 관련 오류(필수·열 존재·타깃 수치형·열 중복·구간 형식/중복)만 검사하고 오류가 있으면 저장하지 않음(422 + issues) | S1에서 목표를 확정·재조회. 모델·분할은 이후 화면 |
| 타입 규칙 보정 | 고유 비율 ≤ 50% 또는 (고유값 ≤ 50 이고 고유 비율 < 90%)이면 범주, 아니면 텍스트 | 40행짜리 메모 열(모두 고유)이 범주로 보이던 문제 |
| 화면 | React 18 + TypeScript + Vite, 외부 UI 라이브러리·웹폰트 없음. 빌드 결과를 FastAPI가 같은 주소에서 제공 | 로컬 단일 사용자, 오프라인 사용 |

### P1 S2 전처리·Mock 추천 구현 메모 (2026-11-19)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 추천 경로 경계 | **ready 경로**: 카탈로그에 ready 플러그인만 넣고 ProviderGateway의 활성 공급자를 쓴다(ready 모델이 하나라도 있을 때). **preview 경로**: ready 모델이 없을 때만 쓰며, 외부 공급자를 부르지 않고 항상 로컬 `MockProvider(allow_unready=True)`로 등록·호환 후보(실제 status 그대로)를 고른다. 출처 표시는 "Mock · 오프라인 (계획 미리보기)" | 내장 플러그인이 미구현인 동안에도 추천·편집 흐름을 확인하되, 미리보기 초안은 최종 검증(PLUGIN_NOT_READY)에서 차단되어 승인·제출 불가 |
| 별칭 | 요청: 목표의 시간열·타깃·설비 열을 같은 LocalProfile의 `column_N`으로 바꿔 보낸다(SafeProfile도 별칭). 응답: `user_goal`과 `x-column` 표시 파라미터(예: `columns`)를 해당 데이터셋·프로파일 버전의 사전으로만 복원. 사전에 없는 값(다른 버전의 별칭·원래 헤더)이면 추천 전체 거부(`ALIAS_UNKNOWN`), 복원한 목표가 저장 목표와 다르면 거부(`RECOMMENDATION_GOAL_MISMATCH`) | 원본 파일명·헤더·셀 값·경로가 추천 요청에 들어가지 않음(요청 전체 직렬화 검사 테스트) |
| 추천 기록 | 추천 결과(복원 후)를 `recommendations` 표에 데이터셋 버전·프로파일 버전과 저장. 적용은 `POST .../plan/apply`로 서버 기록만 사용, 버전 또는 목표가 바뀌었으면 `RECOMMENDATION_STALE` | 클라이언트가 추천 내용을 바꿔 보낼 수 없음 |
| 초안 필드 | `DraftPlan.excluded_steps`(체크 해제 항목 보관), `recommendation_id`, `source`에 `goal`(목표만 저장된 상태, S2 진입 시 자동 추천 대상) 추가. 앞의 두 필드는 실행 설정 해시에 포함하지 않음 | 체크 해제·출처 표시는 실행에 영향 없음 |
| 적용 불가 | 저장·적용 시 `SCHEMA_INVALID`·`UNKNOWN_PLUGIN`·`UNKNOWN_VERSION`·`WRONG_PLUGIN_KIND`·`INCOMPATIBLE_MODALITY/TASK`·`DUPLICATE_STEP`는 초안에 넣지 않음(422, 위치 포함). 미구현·파라미터·순서 문제는 초안에 저장하고 항목별로 표시 | 브리프 "미등록·비호환 도구는 초안에 적용 불가" |
| 리비전 충돌 | 저장·적용 요청에 `expected_revision`, 다르면 409 `REVISION_CONFLICT` + 최신 상태 다시 불러오기 안내 | 다른 탭·세션의 편집 보호 |
| 수동 모드 | `source=manual` 저장 시 `recommendation_id` 해제, 이후 자동 추천 안 함 | |
| 웹 API | `GET/PUT /api/datasets/{id}/plan`, `POST .../plan/validate`(저장 없이 검증), `POST .../recommendation`, `POST .../plan/apply` | 기존 Provider·Registry·PlanService 계약 재사용 |
| 화면 이동 | 해시 주소 `#/datasets/<id>`, `#/preprocess/<id>`. 전처리 메뉴는 선택한 데이터셋에 목표가 저장되면 열림("목표 저장 후 사용"). 상단 바 검색을 다른 화면에서 입력하면 데이터셋 화면으로 이동해 결과 표시 | 새로고침·메뉴 이동 후 같은 데이터셋·초안 유지 |
| 설치 선언 | `web = fastapi, uvicorn`, `dev = pytest, httpx, fastapi, uvicorn` | 각각 새 가상환경에서 설치·실행 확인 |

### P1 S3 학습 설정 구현 메모 (2026-11-19)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 웹 API | `GET/PUT /api/datasets/{id}/training`(PUT은 `expected_revision`), `POST .../training/validate`(저장 없이 검증). 형식 오류는 422 `TRAINING_INVALID` + 위치별 issues | 기존 PlanService·DraftPlan 계약 재사용, 새 계약 필드 없음 |
| 저장 범위 | `model`·`train_budget`·`split_policy`·`evaluation`만 바꾸고 목표·전처리·체크 해제 항목·추천 기록·출처는 보존. S2 저장도 S3 설정을 보존 | 화면 간 덮어쓰기 방지(통합 테스트) |
| 기본값 | 초안에 값이 없을 때 화면에 시간순 70/15/15·gap 자동·MAE·RMSE·기준 모델 직전값(`naive_last_value`)을 채우고 "기본값" 안내. 저장 전까지 초안은 변경되지 않음 | 브리프 기본값 |
| 오류 문구 | pydantic 오류를 한국어로 바꾸고 union 태그를 위치에서 제거(예: `split_policy.train`). 분할은 % 문구("합이 100%가 되어야", "0%보다 크고 100%보다 작아야") | 입력 근처 표시 |
| 저장 차단 | 형식 오류·미등록/비호환 모델은 저장 안 함(422). 미구현 모델·파라미터 범위 오류는 저장하고 항목별 표시(S2와 같은 기준). 새 규칙 `EVALUATION_GROUP_MISSING`: 그룹별 집계를 켰는데 목표에 그룹 열이 없음 | |
| 기준 모델 선택지 | 화면은 직전값 / 사용 안 함만 제공 | 다른 기준 모델은 구현 시 추가 |
| 상단 검색 | 데이터셋 화면: 입력 즉시 필터. 다른 화면: 입력만으로는 이동하지 않고 Enter로 데이터셋 화면 이동, 미저장 편집이 있으면 저장/버리기/취소 선택(S2 메모의 "입력하면 이동"을 대체). `WorkbenchBar`에 선택 prop `onSubmit` 추가 | 편집 중 입력 손실 방지 |

### P1 S4 확인·승인 구현 메모 (2026-11-19)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 웹 API | `GET /api/datasets/{id}/confirmation`(요약 + 최종 검증 + 승인 상태, 변경 없음), `POST /api/datasets/{id}/approve` `{expected_revision, confirmed}` | 기존 `PlanService.approve`·`ApprovedSnapshot`·무효화 로직 재사용 |
| 승인 토큰 | 응답·로그에 `approval_id`를 넣지 않음. 화면에는 `status`(none/approved/invalidated)·`plan_id`·`plan_hash`·`approved_at`만 | 브리프 요구. **영향:** P2 학습 시작은 서버가 활성 승인 행과 `plan_hash`를 재확인해 서버 내부에서 제출하는 경로로 연결해야 함(클라이언트가 토큰을 보내는 `experiment.submit` 형태는 화면에서 쓰지 않음) |
| 승인 상태 판정 | 이 초안의 active 승인이 현재 최종 검증 해시와 같으면 approved. active가 있으나 해시가 다르거나(데이터셋·플러그인 버전 변경) 이전 승인이 무효화되었으면 invalidated | 실행 설정 변경 시 `update_draft`가 즉시 무효화(기존), 설명만 바뀌면 유지 |
| 중복 승인 | 같은 해시가 이미 승인되어 있으면 새 승인을 만들지 않음. 새 승인 전 해시가 맞지 않는 active 승인은 invalidated로 바꿈 | 초안당 유효 승인 1개 |
| 오류 응답 | 확인 체크 없음 400 `CONFIRMATION_REQUIRED`, 리비전 불일치 409 `REVISION_CONFLICT`, 차단 오류 422 `VALIDATION_FAILED` + issues(각 항목에 고칠 화면 `step`: goal/preprocess/train) | 오류 → S1·S2·S3 이동 |
| 실행 가능 목록 | 테스트에서만 `create_app(registry=...)`로 주입(`tests/e2e/serve_ready_registry.py`, 통합 테스트 `ready_registry()`). 내장 Registry·어댑터 등록 경로는 변경 없음 | 브리프 |
| 산출 값 | 실제 학습 기간·행 수·예상 소요 시간 필드는 있으나 P1에서는 항상 비어 있고 화면은 안내 문구 | 파이프라인 계산(P2) 이후 채움 |
| 리비전 표시 | 저장·충돌 메시지에서 리비전 번호를 표시하지 않음(값은 응답·충돌 검사에 계속 사용) | 사용자 요청 |

### P2 시계열 실행 메모 (2026-11-19)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 구현 등록 | `builtin_registry()`는 구현이 있는 ID·버전(`ts.session_split·resample·missing·lag·scale@1.0.0`, `timeseries.forecasting.xgboost@1.0.0`)을 같은 명세로 만든 어댑터 등록으로 교체. 의존성(`pandas`·`sklearn`·`xgboost`) 없으면 `missing_dependency`. `builtin_registry(with_adapters=False)`는 명세만(모두 미구현) | ID 공간·파라미터 스키마·순서 규칙 불변(테스트). 저장된 초안 호환 |
| 모델 어댑터 | `PluginRegistry.register_model/get_model_adapter`, 계약 `validate·fit(X, y, params, seed, on_iteration)·predict·save·load`. XGBoost는 JSON 저장(피클 없음), hist, n_jobs=2, seed 42 | 전처리 어댑터와 같은 등록·의존성 점검 |
| `ts.missing` | 방법은 `ffill`(같은 설비·세션의 직전 관측, `max_gap` 이내)·`drop`(결측 표본 제외)만. 선형 보간 제거, Mock 기본값 `ffill`. 이전 초안의 `linear`는 `LEGACY_PARAM_VALUE` 오류 + 바꿀 방법(자동 변경 없음) | 선형 보간은 다음 관측(미래)을 사용 |
| 시각·행 정책 | 시간대 표기 없음 → 적힌 시각 그대로, 모두 오프셋 있음 → UTC로 바꾼 뒤 표기 제거, 섞임 → 실행 거부(`TIMESTAMP_TZ_MIXED`). 해석 불가 시각 행 제외, 같은 설비·시각 중복은 수치 평균. 문자·범주 열은 피처 제외 | `split.json.load`에 개수 기록 |
| 관측 가능 시점 | 리샘플 구간 (t-f, t] → 시각 t(closed=right, label=right, origin=epoch). lag·ffill은 같은 설비·세션 안의 과거만. 라벨은 타깃 관측 사본(채움·lag·스케일링 없음) | 미래값 변경 불변 테스트 |
| 표본·분할 | horizon마다 모델 1개(직접 예측). 타깃은 같은 세션의 t+h(리샘플 있으면 간격 정수배 필수, `HORIZON_NOT_MULTIPLE`). 경계는 전 설비 공통 고유 시각의 비율 위치. 학습·검증 표본 중 타깃 시각이 경계를 넘는 것은 제외(gap=auto: horizon, 지정 시 max(gap, horizon)). 최근 N일은 train_end 기준으로 학습 표본만 줄임 | 학습량만 바꿔도 검증·테스트 표본 동일 |
| 학습 전용 fit | `fit_scope=train_only` 단계(`ts.scale`)는 무상태 단계 뒤에만, 학습 표본 피처로 fit. 상태는 열별 center·scale JSON | 저장 파이프라인으로 새 CSV 변환 |
| 평가 | rolling origin(학습 1회, 재학습 없음). 직전값 기준 예측 = 기준 시각 타깃 값(결측 처리 후), 모델과 같은 표본. MAE·RMSE·R²(분산 0이면 null)·sMAPE(%·0/0 표본은 0). overall·horizon별·설비별. `seasonal_naive`는 `EVAL_OPTION_NOT_IMPLEMENTED` | `metrics_meta.json`에 식·집계 기록 |
| 제출 | 화면: `POST /api/datasets/{id}/runs {plan_id, plan_hash}` → `JobService.submit_approved`(승인 기록의 데이터셋·해시 대조) → 토큰 기반 `submit`과 같은 `_submit` 트랜잭션. 같은 승인 재요청은 기존 실행 반환(`duplicate`). 실행 후 승인 상태 `submitted`(+run_id), 다시 학습하려면 다시 승인 | 승인 토큰은 해시만 저장되어 복구 불가 |
| worker | 실행당 `python -m mlworkbench.runner <ws> <run_id>` 1개(`worker.lock` O_EXCL), heartbeat 2초, 취소는 DB 상태 폴링(0.5초)·XGBoost 반복 콜백. 서버: 2초마다 대조(종료된 worker → `WORKER_EXITED`, 취소 무응답 20초 → 종료), 앱 시작 시 `WORKER_NOT_STARTED`·`WORKER_LOST`, 앱 종료 시 worker 종료·`APP_SHUTDOWN` | 앱 종료 정책과 실행 기록 일치 |
| 실행 API | `GET /api/runs[?dataset_id]`, `GET /api/runs/{id}`(상태·이벤트·분할·지표), `POST .../cancel-confirmation`, `POST .../cancel {confirmation_id}`, `GET .../predictions.csv` | RunRecord에 started_at·finished_at·error_code·error_message_ko·result 추가 |
| 산출물 | `experiments/<run_id>/`: config.yaml, dataset_manifest.json, environment.json(버전·seed·플러그인·카탈로그), split.json, split_rows.csv(설비·기준 시각·타깃 시각·horizon·분할), model/pipeline.json·xgb_<h>.json, predictions/predictions.csv, metrics.json, metrics_meta.json, train.log | ARCHITECTURE §8 구성 |
| 재현성 | `mlwb experiment verify <run_id>`: 같은 seed로 임시 폴더에 재계산해 지표 비교 + 저장 예측값으로 재계산(새 실행 기록 없음) | 허용 오차 기본 1e-6 |
| 의존성 | `pip install -e ".[timeseries]"`(pandas≥2.1, numpy≥1.24, scikit-learn≥1.3, xgboost≥2.0) | pandas 2.3/3.0, xgboost 3.2/3.4에서 확인 |

### P2 S7 새 데이터 적용 메모 (2026-11-19)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 모델 식별 | 완료(SUCCEEDED) 실행의 `run_id`·`plan_hash`. 실패·취소·진행 중 실행은 409 `RUN_NOT_SUCCEEDED`. 추론 설정은 저장된 `model/pipeline.json`만 사용(현재 초안·기본값 미사용) | 학습 당시 설정 그대로 |
| 웹 API | `GET /api/models`, `GET /api/models/{run_id}`(요구사항), `POST /api/models/{run_id}/applications`(CSV 본문·`X-File-Name`, 저장 후 즉시 점검), `GET /api/applications[/{id}]`, `POST .../rebind {run_id}`(같은 입력으로 다른 모델 → 새 기록), `POST .../check`, `POST .../predict`, `GET .../predictions.csv`(SUCCEEDED만) | 입력 점검·결과 저장은 InferenceService(Application Service), FastAPI는 호출만 |
| 적용 기록 | `applications` 표 + `workspace/applications/<id>/`(input.csv, predictions.csv). 기록: 원본 run_id·plan_hash, 입력 이름·내용 sha256·크기, 인코딩·구분자, 점검 보고, 모델 파일 해시, 상태·시각·오류·결과 요약. 상태 `CHECK_FAILED`/`CHECKED` → `RUNNING` → `SUCCEEDED`/`FAILED` | 모델·파일을 바꾸면 새 기록, 이전 기록은 그대로 |
| 입력 정책 | 열은 이름으로 찾음(순서 무관). 필요한 열 = 시간·설비·타깃·학습 때 수치 입력 열. 그 밖의 열은 읽지 않고 목록으로 표시. 문자·범주 열은 학습과 같이 미사용. 시각·중복·리샘플·세션·결측 정책은 학습과 동일하며 시각 형식(표기 없음/UTC 변환)이 학습과 다르면 `TIMEZONE_POLICY_MISMATCH` | 학습과 같은 입력 구성 |
| 과거 타깃 | 기준 시각의 타깃 값과 lag에 쓰이므로 필수(`TARGET_HISTORY_MISSING`). 예측 대상 시각의 실제값은 요구하지 않음(라벨 결합 없음) | 미래 관측을 추론 조건으로 쓰지 않음 |
| 예측 표본 | 기준 시각 타깃이 있고 lag 이력이 모두 있는 기준 시각만(`drop` 결측 정책이면 모든 피처). 제외 건수를 이유별로 보고. 0개면 모델 호출 전 `NO_PREDICTABLE_ROWS`. 마지막 관측 시각에서도 이력이 충분하면 그 이후 대상 시각을 예측 | |
| 저장 파이프라인 무결성 | `pipeline.json` 누락·손상·형식, 모델 파일 누락·손상, 모델 구현·라이브러리 없음, 학습 전용 단계 수·플러그인과 저장 상태 불일치(`PIPELINE_STATE_MISMATCH`, zip 잘림 방지)를 오류로 처리. 추론은 `transform`·`predict`만 호출(fit 호출 없음, 테스트로 확인) | |
| 예측 실행 | 점검 통과(또는 이전 예측 실패) 기록만. 같은 기록 중복 요청은 하나만 RUNNING. 예측 직전 입력·모델 해시가 점검 때와 다르면 `INPUT_CHANGED`·`MODEL_CHANGED`로 실패. 앱의 분석용 프로세스 풀에서 실행(학습 worker 미사용), 결과는 임시 파일 → 교체. 앱 시작 시 RUNNING은 `APP_SHUTDOWN` 실패로 정리 | 실패가 성공 결과로 보이지 않음 |
| 결과 CSV | `model_run_id, model_plan_hash, group, origin_time, target_time, horizon, predicted`(설비·기준 시각·horizon 순). 응답에 서버 경로·승인 토큰 없음 | |
| Windows | 프로세스 확인·우선순위의 `ctypes` 호출에 `wintypes.HANDLE` 등 인자·반환 형식 명시 | 64비트 핸들 잘림 방지 |

### P2 rolling·차분·시간 특징 메모 (2026-11-20)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 구현 등록 | `ts.rolling·ts.diff·ts.calendar@1.0.0`을 기존 명세 그대로 어댑터로 교체(ready, 의존성 pandas). 스키마·ID·버전 불변 | 저장된 초안·추천 호환 |
| rolling | 같은 설비·세션에서 `shift(1)` 후 `rolling(w, min_periods=w)` → `{열}_roll{w}_{stat}`. `shift_first`는 항상 켜짐(현재 시점 제외) | 미래·현재 정보 차단, 창이 다 차기 전 결측 |
| 차분 | `{열}_diff{p}` = x(t) − 같은 설비·세션의 x(t−p) | 세션 경계 넘지 않음 |
| 시간 특징 | `cal_hour·cal_dayofweek(월=0)·cal_month·cal_is_weekend` = 기준 시각에서 계산 | 시각 해석은 읽기 정책과 동일 |
| 대상 열 | `columns` 없으면 타깃, 지정 시 수치 열만(어댑터 `validate`) | |
| S7 요구사항 | 필요한 이력 = max(lag 최대, rolling 창 최대, 차분 칸), 열별로 쓰는 피처를 '쓰는 이유'에 표시, 시간 특징은 시간 열에 표시. 이력 부족 기준 시각은 제외 건수로 보고 | 학습·추론에서 같은 피처 생성(테스트로 일치 확인) |

### P2 XLSX 메모 (2026-11-20)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 입력 어댑터 | `datasets/xlsx_table.py`(openpyxl): 점검·시트 목록·선택 시트 → 공통 표(`table.csv`, UTF-8·쉼표). 프로파일·학습·S7은 기존 CSV 경로 그대로 | 엑셀 코드는 입력 어댑터에만 |
| 등록·상태 | `.xlsx` 업로드 → `source.xlsx` 보관·ZIP 점검·시트 목록 → REGISTERED + `needs_sheet`(분석하지 않음, 재시작 시에도 대기). `POST /api/datasets/{id}/sheet {sheet}` → 정규화 → PROFILING → PROFILED/PROFILE_FAILED | 사용자가 고른 시트만 분석 |
| 매니페스트 | `source_kind=xlsx`, `selected_sheet`, `xlsx = {source_sha256, sheets, epoch, normalization{rules, header_row, rows, columns, empty_columns_dropped, blank_rows_skipped, rows_above_header, formula_cells, table_sha256}, copied_from?}`, `local_path=datasets/<id>/table.csv`, **`version` = 표 내용 해시** | 승인·실행은 같은 표에 결합. 원본 해시와 구분 |
| 시트 변경 | 초안(목표·설정)이 없는 데이터셋: 같은 데이터셋에서 시트·버전 교체(이전 프로파일 폐기, REGISTERED로). 초안이 있으면 원본 복사 → **새 데이터셋**(`copied_from`). 기존 데이터셋·승인·실행은 당시 버전 유지 | 이전 설정을 다른 시트로 실행할 수 없음 |
| 정규화 규칙 `xlsx-table-v1` | 첫 값 있는 행 = 헤더. 값 있는 열 범위. 헤더도 값도 없는 열·완전 빈 행 제외(개수 기록), 서식만 있는 셀 무시. 빈 헤더(아래 값 있음)·중복 헤더·표 범위 병합 셀·Excel 오류 값·저장 결과 없는 수식 → 실패(셀 위치·고칠 방법) | 조용히 대체하지 않음 |
| 값 변환 | 날짜 셀: 워크북 날짜 기준(1900/1904)으로 해석 → `YYYY-MM-DD HH:MM:SS`(시간대 없음 = 기존 '적힌 시각 그대로' 정책), 날짜만은 `YYYY-MM-DD`. 숫자: 저장값(표시 형식·반올림 무시). 텍스트: 그대로(앞자리 0 보존). 논리값 TRUE/FALSE. 수식: 실행하지 않고 저장된 결과(알림 `XLSX_FORMULA_CACHED`) | 같은 내용 CSV와 같은 표 |
| 거부·안전 | `.xls`·`.xlsm`·`.xlsb`·서식 파일, 암호(OLE), 손상, 숨은 매크로(`vbaProject.bin`), 항목 2만 개 초과, 압축 해제 합계 2GiB 또는 파일 크기×200+50MB 초과. 외부 링크 미사용(`keep_links=False`), 매크로 미보존 | 압축 폭탄·코드 실행 방지 |
| 전송 정책 | SafeProfile은 별칭·타입·결측률·알림 코드만(`source_kind=xlsx`). 워크북·시트 이름·헤더·셀 값·수식·경로 미포함(`hidden_field_kinds`에 sheet_names·formulas) | 테스트로 확인 |
| 실행 대조 | worker·`mlwb experiment verify`는 `local_path`의 표를 읽고, 표 해시가 승인 스냅샷의 `dataset_version`과 다르면 `DATASET_CHANGED`(CSV도 동일) | 승인 뒤 데이터 변경 차단 |
| S7 엑셀 | 업로드 → `SHEET_REQUIRED` → `POST /api/applications/{id}/sheet` → `input.csv` 정규화 → 기존 점검·추론. 기록 `input{kind, sha256(원본), sheet, sheets, table_sha256, normalization}`. 학습 때와 시트 이름이 달라도 됨. 점검 뒤 다른 시트 → 새 적용 기록. 예측 직전 원본·표 해시 대조 | |
| 의존성 | `pip install -e ".[xlsx]"`(openpyxl≥3.1). 없으면 `XLSX_DEPENDENCY_MISSING`(설치 안내) | 3.1.5 확인 |

### P2 사용자 정의 플러그인 메모 (2026-11-20)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 위치·발견 | `default_registry()` = 내장 + `<프로젝트>/plugins/<폴더>`(`MLWB_PLUGIN_DIRS`가 있으면 그것만, 빈 값이면 없음). 앱·worker·S7 예측 프로세스·CLI가 같은 함수를 쓰고, worker에는 위치를 절대경로로 넘긴다. `.`·`_`로 시작하는 폴더, plugin.json 없는 폴더는 건너뜀 | core 수정 없이 추가 |
| 형식 | `plugin.json` = PluginSpec 필드 + `entry: "파일.py:클래스"`. `status`·`reason_unavailable`·`origin`·`code_sha256`은 쓸 수 없음. 클래스는 인자 없이 생성, `transform` 필수, `validate`·`fit`·`history` 선택(`mlworkbench.plugins.sdk.UserFeature`) | |
| 허용 범위 | ID `ts.custom.<이름>`, `kind=preprocessor`, `modality=timeseries`, tasks `forecasting`, 입출력 `table.timeseries`, `fit_scope`·`source_library` 필수, stateless stage 30~59 / train_only 60 이상, locked 불가, parameter_schema는 기존 부분집합 검사 통과 | 내장 ID 덮어쓰기 차단 |
| 상태 | 명세 오류·중복 → 등록 안 함, `load_problems`(폴더 이름·코드·사유). 의존성 없음 → `missing_dependency`(코드 실행 안 함). 코드 불러오기·클래스·메서드 오류 → `invalid`(목록에 보이고 실행 불가). 사유에 경로·예외 메시지 원문을 넣지 않음(예외 이름만) | LLM 카탈로그에도 사유가 실림 |
| 신뢰 모델 | 사용자가 둔 로컬 코드를 그대로 실행(샌드박스 아님). zip·다운로드·자동 설치·LLM 생성 코드 없음 | ARCHITECTURE §7 |
| 실행 래퍼 | 라벨(`__label`) 숨김, 행 수·인덱스·기존 열·타깃·관측 열·간격·결측 정책 변경 금지, 새 피처는 feature_cols 끝·`__` 금지·중복 금지·float64 변환, 목록에 없는 열 금지, train_only 상태 JSON(NaN 불가). 위반 `PLUGIN_OUTPUT_INVALID`·`PLUGIN_STATE_INVALID`, 사용자 코드 예외 `PLUGIN_ERROR` | |
| 과거만 사용 검사 | 학습 시 stateless 사용자 단계마다 전체 고유 시각의 50%·80% 지점까지만 잘라 다시 계산, 겹치는 행의 새 피처가 같아야 함(rtol·atol 1e-9, NaN 같음). 다르면 `PLUGIN_FUTURE_LEAK`. 통과 시 train.log 기록 | 표본 검사(완전한 증명 아님) |
| 코드 결합 | `code_sha256` = plugin.json + 폴더 `*.py` 내용 해시. 승인 스냅샷 `plugin_code`, `pipeline.json`·`environment.json`(`plugin_code`·`user_plugin_code`). 제출·화면 승인 상태·worker·`mlwb experiment verify`·S7 불러오기에서 불러온 코드와 디스크 코드 모두 대조 → `PLUGIN_CODE_CHANGED`. 앱 실행 중 고친 코드는 재시작 전 확정 불가 | 버전을 올리지 않고 고쳐도 승인한 코드로만 실행 |
| S7 입력 안내 | 어댑터 `history(params, target) -> (칸 수, {열: 설명})`로 필요한 이력·열별 사용 피처 표시(내장 lag·rolling·차분도 같은 방식). 단계 이름은 Registry 표시 이름 | 사용자 ID를 core가 몰라도 됨 |
| 화면·API | StepView `origin`(builtin/user), PlanView `plugin_problems`, 상태 `invalid` = '플러그인 오류 · 실행 불가'. S2 카드 '사용자 정의' 배지, 추가 목록 '(사용자 정의)', 상단 불러오기 문제 안내. 파라미터 입력란 이름은 스키마 `title` 사용 | |

### P2 최소 대기열 메모 (2026-11-20)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| 동시 실행 | `MLWB_MAX_PARALLEL_RUNS`(기본 1, 1~4). '끝나지 않았고 worker.lock이 있는 실행' 수로 센다 | 상태기계 변경 없음(QUEUED = 대기/시작 중) |
| 시작 | `RunManager.dispatch()`가 잠금 안에서 빈 자리만큼 lock 없는 QUEUED를 제출 순서(등록 순)로 `start()`. 제출 API·2초 주기 대조·앱 시작 때 호출 | 제출 API가 직접 worker를 띄우지 않음 |
| 취소 | 대기 중(lock 없음) 취소 → `cancel_waiting()`이 같은 잠금 안에서 바로 `CANCELED`(error_code `CANCELED`, '대기 중에 취소했습니다') | 시작과 겹치면 worker가 CANCEL_REQUESTED를 보고 멈춤 |
| 재시작 | worker.json 있음 → 기존 규칙. lock만 → `FAILED(WORKER_NOT_STARTED)`. 둘 다 없는 QUEUED → 대기 유지 후 dispatch | 이전 '시작 전 실행은 실패 처리'에서 변경 |
| API | RunView `queue_position`(대기 순번, 1부터) · `queue_ahead`(먼저 돌 실행 수 = 실행 중 + 앞선 대기) · `max_parallel` | S5 상세·목록 표시 |

### S6 실험 비교 메모 (2026-11-20)

| 항목 | 결정 | 이유·영향 |
|---|---|---|
| API | `GET /api/runs/compare?run_ids=…`(2~6개, 완료만) → `{reference, runs[{run_id, conditions, protocol, protocol_key, protocol_diffs, same_protocol, comparable_horizons, metrics, steps, duration_s}], condition_diffs, metric_names, horizons, groups}`. `GET /api/runs/compare/series?run_ids=…&horizon=&group=&split=test` → `{target_time[], actual[], predicted[][], points_total, points_shown}` | 오류: `COMPARE_COUNT`(400) · `RUN_NOT_FOUND`(404) · `RUN_NOT_SUCCEEDED`(409) · `HORIZON_UNKNOWN`(400) |
| 평가 조건 | 데이터 버전·타깃·시간열·설비 열·예측 시점·분할(method·비율·gap)·지표·기준 예측·테스트 표본 집합 해시·예측 시점별 테스트 표본 수. `protocol_key` = 이 값의 짧은 해시(RunView에도) | 같은 표본·정답·지표일 때만 순위 |
| 조건 차이 | 데이터셋·전처리(표시 이름+파라미터)·모델·모델 파라미터·학습량·사용자 플러그인 코드 | 경고가 아닌 실험 변수 |
| 계산 | `app/compare.py`. 지표는 저장 metrics.json 그대로. 그래프는 같은 대상 시각 표본만 맞춰 최대 600점. 실행 폴더 `compare_key.json` 캐시 | 다시 학습·계산하지 않음 |

### 실행 이름·삭제 메모 (2026-11-20, ADD-UX-02·06·07)

| API | 동작 | 오류 |
|---|---|---|
| `PATCH /api/runs/{id}` {name} | 이름 저장(공백 정리, 빈 값=없음). RunView·비교·모델 목록에 `name` | `NAME_INVALID`·`NAME_TOO_LONG`(400) |
| `GET /api/runs/{id}/delete-preview` | {deletable, reason_ko, applications, bytes} | |
| `DELETE /api/runs/{id}` {confirmed, include_applications} | 실행·이벤트·취소 확인·적용 기록 행 + 실행·적용 폴더 삭제. 승인·확정 스냅샷 유지 | 409: `CONFIRMATION_REQUIRED`·`CONFIRM_APPLICATIONS`·`RUN_ACTIVE`·`APPLICATION_RUNNING` |
| `GET /api/datasets/{id}/delete-preview` | {deletable, reason_ko, runs, active_runs, applications, bytes} | |
| `DELETE /api/datasets/{id}` {confirmed, include_runs} | 데이터셋·분석·추천·초안·승인·실행·적용 + 데이터·실행·적용·확정 폴더 삭제 | 409: `CONFIRMATION_REQUIRED`·`CONFIRM_RUNS`·`DATASET_BUSY` |
| 공통 | 응답 `files_left`(못 지운 폴더 이름). `workspace/deletions.log` 한 줄(종류·id·개수) | |

### 비전 V1 메모 (2026-11-20, `P3_VISION_BRIEF.md`)

| 항목 | 내용 |
|---|---|
| 등록 | `POST /api/datasets`에 `.zip` → modality `vision`, `source.zip`. `POST /api/datasets/folder` {path} → 제자리 읽기(복사 없음). 오류 400: `FOLDER_REQUIRED`·`FOLDER_NOT_ABSOLUTE`·`FOLDER_NOT_FOUND`·`FOLDER_NOT_ALLOWED`. 같은 id에 다른 종류 재시도 409 `MODALITY_MISMATCH` |
| ZIP 안전 | `ZIP_INVALID`·`ZIP_TOO_MANY_FILES`(20만)·`ZIP_UNSAFE_ENTRY`(경로 탈출·링크)·`ZIP_ENCRYPTED`·`ZIP_BOMB`(압축비 200)·`ZIP_TOO_LARGE`(20GiB)·`NO_IMAGES`. 이미지 파일만 풀고 나머지는 개수만 |
| `ImageProfile` | image_count, formats, sizes(상위 5), channels, folders[{path, images, formats, role_guess, defect_type, samples}], integrity{method, checked, corrupt, …}, mask_pairs, skipped, notices. `GET /api/datasets/{id}/image-profile`(분석 전 409) |
| 미리보기 | `GET /api/datasets/{id}/image?path=` — 데이터셋 루트 안 상대 경로만(밖이면 404 `IMAGE_NOT_FOUND`), TIFF 415, 20MB 초과 413, `Cache-Control: no-store` |
| `VisionGoal` | task `anomaly_detection`, roles{폴더: train_normal·calibration_normal·test_normal·test_defect·gt_mask·ignore}, calibration{source folder·split_from_train, fraction 0.05~0.5, seed}. `GET/PUT /api/datasets/{id}/vision-goal`, 오류 있으면 422 `GOAL_INVALID`(issues). 코드: `GOAL_UNKNOWN_FOLDER`·`GOAL_TRAIN_TOO_FEW`(10장)·`GOAL_CALIBRATION_MISSING`·(경고)`GOAL_TEST_INCOMPLETE`·`GOAL_GT_WITHOUT_DEFECT`·`GOAL_CALIBRATION_FOLDER_UNUSED`·(정보)`GOAL_NO_GT` |
| SafeProfile(비전) | 역할 추정별 장수, 불량 종류 수, 형식·크기·채널, 짝 수, 손상 수, 안내 코드. 폴더·파일 이름·경로·이미지 내용은 넣지 않음(값·키 허용 목록 검사) |
