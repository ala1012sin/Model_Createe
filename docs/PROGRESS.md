# PROGRESS — 구현 로드맵 및 작업 이력

> **현재: P0 완료 · P1 진행 중(CSV·XLSX 기반 S1~S4 완료) · P2 진행 중(학습·S7·Windows 확인·rolling/차분/시간 특징·XLSX·사용자 정의 플러그인 예제·최소 대기열 완료) · S6 실험 비교(시계열)·추가 요구사항 1·2차 완료 / 다음: 추가 요구사항 3차(자동 설치)**  
> 2026-11-20 / 개인용 React + FastAPI / 시계열 + 비전 / 외부 API 초기 사용, 로컬 LLM 교체 지원

## 구현 우선순위

P0 구조·계약 → P1 업로드/프로파일/LLM 추천 → P2 시계열 학습 MVP → P3 비전 학습 MVP → P4 모델 확장 → P5 최적화·실험 비교 → P6 안정화/배포.

**품질 원칙:** 작동하는 세로 단면을 한 단계씩 구현한다. GPU가 필요한 기능을 구현하기 전까지는 mock/CPU 테스트로 구조를 검증한다. 테스트를 안 했다면 완료 표시하지 않는다.

## P0. 프로젝트 골격 / 핵심 계약 (최우선)

화면 검토 이후의 구현 지시와 완료 검수 기준은 `IMPLEMENTATION_BRIEF.md`, 계약 결정은 `CONTRACTS.md` §11을 따른다.

- [x] Python 패키지 `src/mlworkbench`, `pyproject.toml`, `.gitignore`, `.env.example` 생성.
- [x] Pydantic 계약 정의: DatasetManifest, SafeProfile, PluginSpec, ProviderConfig/Capabilities, PlanProposal, ValidationResult, ApprovedSnapshot, RunRecord.
- [x] 플러그인 Registry: 등록·조회·버전/의존성·task compatibility·미구현 처리.
- [x] 공급자 인터페이스: MockProvider 및 외부/로컬 Provider 스텁, Factory 설정 스키마.
- [x] MCP Server/Client **인터페이스만** 작성: 도구 등록 목록과 read-only allowlist, MCP 없이도 Application Service 재사용 가능.
- [x] Validation Engine: 미등록 tool, 비호환 modality/task, 잘못된 스키마와 위험한 파라미터 차단.
- [x] 계획 상태 관리/승인 해시 계약 + 파일/SQLite 저장소 초기화.
- [x] CLI 명령: `plugins list`, `providers list`, `plan validate`, `doctor` (명령 이름은 문서화하고 실제 구현에 고정).
- [x] unit tests: Registry 등록/중복/버전, Provider factory, JSON schema, 권한/승인, 플러그인 없음, 비밀키 출력 차단.

**P0 완료 기준:** MockProvider와 Registry로 정형 추천 샘플을 검증하고, 승인 없이 `submit`을 거절하며, `pytest`가 실제로 통과. 아직 실제 외부 API/GPU 학습 필요 없음.

## P1. 데이터 프로파일 + LLM/MCP 추천 세로 단면

`P1_CSV_BRIEF.md`의 CSV 등록·프로파일링과 `P1_REACT_BRIEF.md`의 React S1-T 화면·FastAPI 연결을 완료했다.
`P1_PREPROCESS_BRIEF.md`의 S2 전처리 화면과 오프라인 Mock 추천을 완료했다. `P1_TRAIN_SETTINGS_BRIEF.md`의 S3 시계열 학습 설정(초안 편집·검증·저장)과 `P1_CONFIRMATION_BRIEF.md`의 S4 계획 요약·최종 검증·승인을 완료했다.

- [x] CSV 로더: dtype, 누락/중복, 시간열 후보, 피처 요약. (2026-11-19, 등록·상태·실패 코드·로컬/전송 프로파일 포함)
- [x] React S1-T: CSV 업로드·목록·분석 상태와 피처 구조 표시, FastAPI 연동 및 브라우저 확인. (2026-11-19, 시계열 목표 저장 포함)
- [x] S2 전처리: 저장 목표 → 별칭 요청 Mock 추천 → 카드(이유·구현 상태) 체크·순서·파라미터 편집 → 초안 검증·저장·복원, 브라우저 확인. (2026-11-19, 미구현 플러그인은 계획 미리보기·실행 불가)
- [x] S3 학습 설정: 모델·학습 데이터량(전체/최근 N일)·시간순 분할·평가 지표 편집 → 입력 근처 오류·초안 검증·저장·복원(S1 목표·S2 전처리 보존), 상단 검색 Enter 규칙, 브라우저 확인. (2026-11-19, 승인·실행은 S4)
- [x] S4 확인·승인: 저장 초안 요약(데이터셋·목표·전처리·모델·학습량·분할·평가) → 서버 최종 검증(오류/경고, 고칠 화면 링크) → 명시적 확인 + 예상 리비전으로 `PlanService.approve` → 승인 상태·`plan_hash` 표시, 실행 설정 변경 시 재승인 안내. 학습 시작은 비활성(P2). (2026-11-19)
- [x] XLSX 로더: 시트 목록·선택·컬럼 프로파일; 수식/유형/빈 셀 처리 정책 정의. (2026-11-20, `P2_XLSX_BRIEF.md`: 선택 시트를 규칙 `xlsx-table-v1`로 표 정규화 → 기존 CSV 프로파일·학습·S7 경로)
- [x] 이미지 폴더/ZIP 로더: 트리, 이미지 크기/수량, 라벨/GT 후보, 경로 보안. (2026-11-20 비전 V1, `P3_VISION_BRIEF.md` — 합성 MVTec 형식으로 검증, 공개 데이터 fixture는 P3)
- [ ] DatasetManifest + SafeProfile 생성·로컬 저장·전송 요약 미리보기.
- [ ] 목표 확인 UI: modality/task, target, timestamp/group, horizon, label/GT mapping.
- [ ] MCP read-only tools 연결: `dataset.get_profile`, `catalog.list_preprocessors`, `catalog.list_models`, `pipeline.validate`.
- [ ] Provider Gateway 구현: OpenAI(API Key env), OpenAI-compatible(base URL/model), Mock; health-check 및 오류 fallback.
- [ ] 공급자별 structured JSON 출력 → Pydantic 검증/사용자 친화 오류, 미지원 모델은 JSON-only fallback.
- [ ] 추천 UI: 전처리/모델 카드, 이유·주의사항, 추가·삭제·재정렬, 파라미터 편집.
- [ ] 승인 게이트와 `plan_hash` 불변 승인 저장. 승인 전 어떤 학습도 실행되지 않음.
- [ ] 공급자 전환 통합 테스트: 같은 synthetic metadata로 OpenAI 호환 mock endpoint와 MockProvider가 schema 통과.
- [ ] 보안 테스트: 민감 raw 데이터 차단, 의심스러운 컬럼명/폴더명, LLM의 미등록 tool/실행 시도 거부.

**P1 완료 기준:** 파일 업로드 → 구조 보여주기 → 추천안 생성(Mock 포함) → 편집/검증/승인 → YAML 저장까지 UI에서 사용 가능. 실제 학습은 이후 단계.

## P2. 시계열 학습 MVP

최근 작업은 [P2_QUEUE_BRIEF.md](P2_QUEUE_BRIEF.md)(최소 대기열)·[P2_PLUGIN_BRIEF.md](P2_PLUGIN_BRIEF.md)(사용자 정의 전처리 플러그인 예제) — 둘 다 Claude가 구현 전에 작성한 설계 기록이다. 이전: [P2_XLSX_BRIEF.md](P2_XLSX_BRIEF.md). P1의 남은 항목은 위 체크리스트에 유지한다.

- [x] 시계열 타깃/시간/설비 지정과 time-based split(기본 70/15/15). (2026-11-19, 설비 공통 시각 경계·라벨 purge)
- [x] 미래 horizon label 생성과 session boundary leakage 방지. (2026-11-19, 직접 예측·세션 안에서만 타깃)
- [x] 전처리: session split, resample, missing, lag, rolling, diff, 시간 피처, scaler(train-fit). (2026-11-19 session split·resample·missing·lag·scaler, 2026-11-20 rolling·차분·시간 특징. 모두 같은 설비·세션 안의 과거 관측만 사용, S7 재적용 시 같은 피처 확인)
- [x] 사용자 정의 `ts.custom_feature` 플러그인 1개 추가 및 자동 노출. (2026-11-20, `plugins/ts_custom_ewm` = `ts.custom.ewm` 지수 가중 평균. core 수정 없이 Registry·S2·LLM 카탈로그·학습·S7 연결, 코드 해시로 승인·저장 모델 결합)
- [x] XGBoost 예측 학습 어댑터/평가기(MAE/RMSE, naive baseline). (2026-11-19, R²·sMAPE 포함, horizon별·설비별)
- [x] `experiment.submit` 승인값 검증 후 background subprocess 큐, 로그/중지 상태. (2026-11-19 승인 검증·실행당 subprocess 1개·로그·취소·실패/재시작 정리, 2026-11-20 대기열: 동시 학습 기본 1개·제출 순서·대기 중 취소 즉시·재시작 후 대기 유지)
- [x] CSV/XLSX → 승인된 계획 → XGBoost 학습 → 예측/지표 저장, 동일 설정 CLI 재실행. (2026-11-19 CSV·`mlwb experiment verify`, 2026-11-20 XLSX: 같은 내용 CSV와 분할·지표·예측 일치)
- [x] time leakage·split·scaler-fit 테스트와 CPU 통합 테스트. (2026-11-19)
- [x] 학습량만 변경한 실행의 검증·테스트 구간 고정 확인. 실제 작업 이벤트를 S5에 표시. (2026-11-19)
- [x] S6 결과에서 선택한 모델을 저장된 전처리와 함께 불러오고, S7에서 새 CSV의 입력 조건 점검·예측·다운로드. (2026-11-19, S5 완료 실행 → S7. 2026-11-20 S6 비교 화면 추가 — 비교 패널에서도 '새 데이터 적용 →')

**P2 완료 기준:** 외부 LLM 연결 여부와 무관하게 공개/합성 데이터로 학습·평가·저장에 성공하고, 선택한 모델로 새 CSV를 예측해 내려받는다.

## P3. 비전 학습 MVP

- [ ] 이미지 manifest/mask mapping/손상 파일 테스트, MVTec AD 또는 VisA 공개 fixture. (V1: manifest·불량-마스크 짝·손상 파일 테스트는 합성 이미지로 완료. 공개 fixture 내려받기는 미실행 — 네트워크·용량 확인 후)
- [ ] `vision.resize`, `vision.normalize`, ROI 선택, mask interpolation 검증.
- [ ] PatchCore adapter: 정상 train 기반 메모리 생성/추론/anomaly map.
- [ ] 이상탐지 image AUROC, NG recall, 정상 이미지 FPR 계산.
- [ ] GT 있을 때 pixel AU-PRO @0.05/@0.30; GT 없을 때 N/A 및 이유.
- [ ] 정상 train/calibration/validation/test 완전 분리, 운영 이미지 threshold 기록.
- [ ] UI 이미지/GT/맵 시각화, 학습 결과 저장/비교.
- [ ] 이미지 수가 적은 fixture로 end-to-end smoke test; 참조 AU-PRO 구현/합성 정답과 비교.
- [ ] 정상 학습 이미지 수 선택을 적용하고, 저장된 모델과 판정 임계값으로 S7에서 새 이미지의 점수·판정을 내려받는다.

**P3 완료 기준:** 공개 데이터에서 PatchCore 학습·평가와 GT 유무에 따른 지표 처리를 확인하고, 저장 모델로 새 이미지의 점수·판정을 내려받는다.

## P4. 익숙한 모델·전처리 플러그인 확장

### 시계열
- [ ] LightGBM.
- [ ] LSTM.
- [ ] GRU-LSTM.
- [ ] CNN1D.
- [ ] RCNN (구현체/계층/입력 윈도우 사양 먼저 확정).
- [ ] 모델별 독립 환경/입력 tensor·예측 시점 호환성 검사.

### 비전
- [ ] Dinomaly.
- [ ] PatchCore+Dinomaly ensemble (맵/점수 정렬).
- [ ] YOLO11-seg (supervised label validation, class/mAP 지표).
- [ ] SuperAD 후보 (공식 코드/라이선스/모델 입력 포맷 확인 후 실제 구현).
- [ ] EfficientAD 후보.
- [ ] VisA PCB, BTAD, MVTec AD 1/2용 dataset importer.

**P4 완료 기준:** 대표 fixture의 단위/연동 테스트 + 미설치 플러그인은 실행 불가 표시. 성능 우위는 독립 실험 결과가 나올 때만 보고.

## P5. 실험 운영·추천 고도화

- [ ] Optuna 탐색/seed 반복/walk-forward CV.
- [x] 동일 조건 실험 비교, 프로토콜이 다르면 경고. (2026-11-20 시계열 — P2 다음 순서로 앞당김, `P2_COMPARE_BRIEF.md`. 비전 지표 비교는 P3 이후)
- [ ] YAML/JSON 저장·불러오기·설정 복제·run export.
- [ ] CPU/GPU 작업 큐/메모리 관측/학습 취소/실패 복구.
- [ ] 실험 결과만 보내는 LLM 차기 실험 추천(승인 후에만 새 run).
- [ ] 추천 품질 평가 fixture/공급자 비교 화면(안정성·비용·응답시간).
- [ ] MLflow 연동(선택).

## P6. 완성도·보안·문서화

- [ ] 플러그인 작성 가이드 및 예제 2종(시계열/비전).
- [ ] API 키/원시 자료 유출 없는지 확인, 외부 API 전송 승인 흐름 검증.
- [ ] Windows 환경 실행/경로/worker 종료 테스트.
- [ ] Ollama/vLLM 샘플 연결 (가용하면), 사내 원격 접근 시 인증·방화벽 점검.
- [ ] README 사용법·문제 해결·테스트 증거·스크린샷.
- [ ] 실제 사용자 워크플로에서 각 단계 클릭 수·재사용성 개선.

## 핵심 회귀 테스트 (매 변경마다 확인)

- [ ] OpenAI·Ollama·Mock adapter 모두 공통 PlanProposal 검증; 공급자 변경이 학습 엔진 코드 수정으로 이어지지 않음.
- [ ] 승인 전/승인 변경 후 실행 불가, LLM이 승인 토큰 생성 불가.
- [ ] LLM이 미등록 모델/미설치 플러그인/임의 코드를 추천해도 실행 차단.
- [ ] 학습·검증·테스트 누수/예측 시계열 horizon 세션 경계 검사.
- [ ] 정상 이미지 FPR vs pixel FPR 완전히 구별, GT 없는 AU-PRO는 N/A.
- [ ] Provider 키/프롬프트 raw 데이터/회사 비밀 자료가 git·DB·로그에 유출되지 않음.
- [ ] 모든 run에 `config.yaml`, `environment.json`, `dataset_manifest.json`, `metrics.json`, `metrics_meta.json`, log 생성.
- [ ] GPU 없어도 P0~P2 핵심 단위 테스트 가능.

## 진행 요약

| Phase | 현황 | 증빙 |
|---|---|---|
| P0 계약·골격 | **완료** | pytest 71 passed (Linux Python 3.10.12 및 Windows Python 3.13.17) · CLI 실행 기록은 아래 작업 로그 |
| P1 업로드+LLM/MCP 추천 | 진행 중 | CSV·S1-T·S2·S3·S4 완료: pytest 175 passed, 브라우저 S1 12·S2 13·S3 10·S4 10항목 PASS(작업 공간 Chromium) |
| P2 시계열 MVP | 진행 중 | 첫 학습 실행·S7 완료: pytest 232 passed(작업 공간·PC), 브라우저 P2 8·S7 10항목 PASS, 예제 CSV 실제 XGBoost 학습·재현 차이 0, 이후 기간 CSV 예측·내려받기. rolling·차분·시간 특징 추가 후 pytest 238 passed(작업 공간·PC), 브라우저 피처 6항목 PASS. XLSX 추가 후 pytest 260 passed(작업 공간·PC), 브라우저 XLSX 8항목 PASS. 사용자 정의 플러그인 예제 후 pytest 308 passed(작업 공간·PC), 브라우저 플러그인 7항목 PASS. 대기열 후 pytest 312 passed(작업 공간·PC), 브라우저 대기열 5항목 PASS. P2 체크리스트 완료 — 다음: S6 실험 비교(P5 항목 앞당김), 추가 요구사항 ADD-UX |
| P3 비전 MVP | 진행 중 | V1 등록·구조·역할(2026-11-20): pytest 344 passed(작업 공간)·343 passed+1 skipped(PC), 브라우저 비전 6항목 PASS. V2 지표: pytest 375 passed(작업 공간), PC 테스트 미실행. 다음 V3 PatchCore |
| P4 모델 확장 | 미착수 | — |
| P5 운영·실험 최적화 | 일부 | S6 실험 비교(시계열) 2026-11-20: pytest 317 passed(작업 공간·PC), 브라우저 비교 7항목 PASS |
| P6 안정화 | 미착수 | — |

## 작업 로그 — Claude가 매 단계 직접 작성

```markdown
### YYYY-MM-DD — P0/작업명
- 목적:
- 변경 파일:
- 실제 구현 내용:
- 실행 명령/테스트 결과: (PASS/FAIL/미실행, 로그)
- 설계 변경 및 이유:
- 남은 문제:
- 다음 작업 한 가지:
```

### 2026-11-19 — P0/프로젝트 골격·검증·승인 계약·CLI·테스트

- 목적: `IMPLEMENTATION_BRIEF.md` P0 범위 구현. 계약 기준은 `CONTRACTS.md` §11.
- 변경 파일:
  - 추가: `pyproject.toml`, `.gitignore`, `.env.example`, `configs/providers.yaml`(기본 활성 = Mock)
  - 추가: `src/mlworkbench/` — `cli.py`, `core/{contracts,validation,registry,schema_check,hashing,security,errors}.py`, `plugins/builtin.py`, `providers/{config,base,mock,stubs,gateway}.py`, `mcp/catalog.py`, `app/services.py`, `storage/database.py`
  - 추가: `tests/{conftest.py, unit/*(6), integration/*(2), fixtures/*_manifest.yaml}`
  - 수정: `examples/timeseries_plan.yaml`, `examples/vision_plan.yaml`(user_goal·train_budget·typed evaluation), `CONTRACTS.md` §2 의사코드·§3 예시·§11 P0 구현 메모
- 실제 구현 내용:
  - 계약: DatasetManifest(D-05 상태)·SafeProfile·PluginSpec(한글 메타·status·순서 규칙)·ProviderConfig/Capabilities·DraftPlan/PlanProposal/ExecutablePlan 경계(D-10)·`user_goal`·`train_budget`/`split_policy`/`evaluation` 분리(D-01)·ValidationIssue(D-06)·ApprovedSnapshot·RunState/RunEvent/RunStatus(D-04)·RunRecord·UserPreset(D-02 타입만).
  - 검증기: draft(빈 필드 허용, info 안내)/final(차단). 미등록·버전 불일치·미구현·종류/모달리티/태스크 비호환·파라미터 스키마(범위·타입·미정의 키·필수)·순서/고정 단계·학습량/분할/평가 호환·보정 분리·GT 없는 AU-PRO N/A 경고·데이터셋 상태/컬럼 확인.
  - 승인: 서버가 데이터 버전·정확한 플러그인 버전·실행 설정을 한 해시로 묶어 스냅샷 저장(`workspace/plans/<plan_id>/approved_snapshot.yaml`). approval_id는 해시만 저장. 실행 설정 변경 시 승인 무효화, 설명 수정은 유지.
  - 제출: 승인 없음/위조/무효화/데이터 변경/플러그인 불가/스냅샷 변조 거절, 재전송 시 같은 run 반환. 이벤트 seq 단조 증가, 상태 전이 검사, 취소는 1회용 확인 ID → CANCEL_REQUESTED(실제 종료와 분리).
  - Provider: Mock(결정적, ready 카탈로그만 사용), OpenAI/OpenAI 호환 스텁(외부 호출 없음), Gateway(외부 전송 동의·절대경로/비밀값 차단·요약 크기 한도·JSON fallback·1회 재시도).
  - MCP: 도구 11종 정의, 추천 컨텍스트 read-only allowlist, Facade로 Application Service 위임(전송 계층 없음).
  - 보안: 로그 필터·CLI 출력에서 키 값 마스킹, 키는 환경변수 이름과 설정 여부만 표시.
- 실행 명령/테스트 결과 (사용자 PC 연결 폴더, Python 3.10.12, 가상환경은 프로젝트 밖):
  - `pip install -e ".[dev]"` → pydantic 2.14.3, PyYAML 6.0.3, pytest 9.2.0 설치
  - `python -m pytest` → **PASS 71 passed** (unit 55 / integration 16)
  - `mlwb doctor` → **정상(exit 0)**. 최초 실행은 FAIL(`sqlite3 disk I/O error`) → 원인: 연결 폴더에서 파일 삭제가 막혀 SQLite 기본 저널(DELETE) 실패 → `journal_mode=TRUNCATE`로 수정·회귀 테스트 추가 후 재실행 PASS
  - `mlwb plugins list` → 14개, 모두 "불가(P2/P3/P4에서 구현 예정)"
  - `mlwb providers list` → 활성 offline_test(mock), 외부 전송 허용 False, 키 미설정 표시
  - `mlwb plan validate examples/timeseries_plan.yaml --mode final --dataset tests/fixtures/ts_manifest.yaml` → exit 1, PLUGIN_NOT_READY 5건(의도대로 차단)
  - 같은 명령 `--assume-ready` → exit 0, plan_hash `sha256:085775ca…`(승인 권한 아님 표시)
  - `examples/vision_plan.yaml --assume-ready` → exit 0, 경고 PIXEL_METRIC_NA(GT 없음)
  - 연결 폴더 `workspace/`에서 승인→제출 스모크: QUEUED, 재전송 duplicate=True·같은 run_id, 이벤트 last_seq=2
  - 컨테이너(Python 3.13) 교차 실행도 71 passed
- 설계 변경 및 이유: `CONTRACTS.md` §11 "P0 구현 메모" 표에 기록(status 필드, generate_raw, 승인 1회성, SQLite TRUNCATE 등).
- 남은 문제:
  - 최초 검증은 연결 폴더를 마운트한 Linux 환경에서 진행했다. Windows P0 재검증은 아래 기록 참조. P6 Windows 테스트 항목 유지.
  - 내장 플러그인은 메타데이터만 있어 실제 제출은 테스트용 ready 레지스트리로만 검증됨. 실제 실행 가능 표시는 P2/P3 구현 시.
  - 실제 업로드·프로파일러, Provider 연결 테스트, MCP stdio 전송, 미리보기 해시(D-09), 프리셋 저장 서비스, 자원 확인, worker 없음(후속 단계).
  - `workspace/`에 스모크 데이터와 SQLite 점검용 `_probe_*.sqlite` 파일이 남아 있음(깃 제외 폴더, 삭제해도 무방).
- 다음 작업 한 가지 (P1): **CSV 프로파일러** — 합성 CSV로 `DatasetManifest`(UPLOADING→REGISTERED→PROFILING→PROFILED, 내용 해시 version)와 `SafeProfile`(컬럼 타입·결측률·시간열 후보·간격 추정)을 만들고, 절대경로·셀 값이 SafeProfile에 없음을 테스트로 확인.

### 2026-11-19 — P0/Windows 재검증

- Windows 네이티브 CPython 3.13.17. PyYAML과 Pydantic을 프로젝트 `workspace/` 아래 검증용 폴더에 설치하고, 해당 명령의 `PYTHONPATH`로만 사용했다.
- `python -B -m pytest -p no:cacheprovider --basetemp=workspace/p0-windows-review-native-tests` → **PASS 71 passed** (2.14초). 제한 환경에서는 `asyncio`의 로컬 소켓 초기화에서 대기해 기본 Windows 환경으로 다시 실행했다.
- `python -B -m mlworkbench doctor --workspace workspace/p0-windows-review-doctor` → **정상(exit 0)**. 새 검증용 작업 공간과 SQLite 초기화, Mock 설정을 확인했다.

### 2026-11-19 — P1/CSV 등록·로컬 프로파일링

- 목적: `P1_CSV_BRIEF.md`. S1 첫 화면이 호출할 CSV 등록·분석 서비스와 전송용 요약.
- 변경 파일:
  - 추가: `src/mlworkbench/datasets/{__init__,csv_profile,safe_profile}.py`, `tests/integration/test_csv_dataset.py`(35), `tests/unit/test_csv_profile.py`(5)
  - 수정: `core/contracts.py`(DatasetError·LocalProfile 등, 상태 전이 1개 추가), `app/services.py`(DatasetService: `register_csv`·`profile`·`register_and_profile`·`list`·`get_local_profile`·`get_safe_profile`), `storage/database.py`(profiles 테이블), `mcp/catalog.py`(dataset.get_profile → 별칭 요약), `cli.py`(`datasets add/profile/list/show [--safe]`), `CONTRACTS.md` §11 "P1 CSV 구현 메모"
- 실제 구현 내용: 서버 관리 경로 저장·sha256 버전·부분 파일 방지, UPLOADING→REGISTERED→PROFILING→PROFILED 및 실패 상태와 한글 오류 코드 16종, 행·열·타입·결측률·고유값·중복/제외 행·시간열 후보·간격 추정, 로컬 프로파일 재조회, 별칭 기반 SafeProfile과 누수 검사, 실패 데이터셋에 수정 파일 재등록.
- 실행 명령/테스트 결과 (Ubuntu 22.04.5 / Linux 6.8, Python 3.10.12, 연결 폴더를 마운트한 환경):
  - `python -m pytest -p no:cacheprovider` → **PASS 111 passed** (P1 CSV 40 = 통합 35 + 단위 5, P0 회귀 71 그대로 통과)
  - 교차 확인: 작업 공간 Python 3.13.18에서 CSV 통합 포함 106 passed(시각 해석 최적화 이전 코드). Windows 네이티브는 이번 변경분 미실행
  - `mlwb datasets add 합성_센서.csv` (프로젝트 `workspace/`) → 완료, 행 1,728 · 열 5, 시간열 후보 timestamp(column_1) 5min
  - `mlwb datasets show <id> --safe` → column_N 별칭·타입·결측률만, 원래 헤더·파일명 없음
  - `mlwb datasets add broken.csv` → 분석 실패 `HEADER_EMPTY_NAME`(exit 1) → `--retry-of`로 정상 파일 재등록 → 완료
  - `mlwb doctor` → 정상
  - 성능: 합성 20만 행(9.9MB) 등록+분석 최초 62.9초 → 날짜가 아닌 열의 시각 해석을 조기 중단하도록 수정 후 **2.0초**(판정 동일, 회귀 테스트 추가)
- 설계 변경 및 이유: `CONTRACTS.md` §11 "P1 CSV 구현 메모".
- 남은 문제:
  - 추천 요청의 목표 컬럼명 별칭화는 아직(추천 연결 작업에서).
  - 설비 구분 열 후보·단위 추정은 미구현(S1-T에서 사용자 선택으로 우선 대응).
  - 수백만 행 파일과 더 넓은 데이터의 시간·메모리는 미측정. 108만 행 실측은 아래 검수 기록 참조. 고유값 상한 1만, 중복 검사 500만 행까지.
  - `workspace/`에 CLI 시연 데이터셋 2개가 남아 있음(깃 제외).
- 다음 작업 한 가지 (P1): **React S1-T** — 왼쪽 업로드·목록·상태, 오른쪽 시계열 요약을 FastAPI를 통해 `DatasetService`에 연결. 범위와 브라우저 검증 기준은 `P1_REACT_BRIEF.md`.

### 2026-11-19 — P1/CSV Windows 검수

- Windows 네이티브 CPython 3.13.17에서 `python -B -m pytest -p no:cacheprovider --basetemp=workspace/p1-csv-windows-review-tests` → **PASS 111 passed** (7.28초).
- 합성 CSV 1회 실측. 5분 간격 × 설비 42대 × 90일, 1,088,640행·5열·43,950,737바이트. `DatasetService.register_and_profile` 호출은 **10.18초**, 측정 Python 프로세스의 최대 작업 집합은 **167.6 MiB**. CSV 생성, HTTP 업로드, 브라우저 렌더링은 호출 시간에 포함되지 않는다. 합성 입력과 작업 공간은 `workspace/p1-csv-benchmark-4aape_d9`에 있다.

### 2026-11-19 — P1/React 데이터셋 첫 화면 + FastAPI

- 목적: `P1_REACT_BRIEF.md`. DATA-01·DATA-02 CSV 경로를 브라우저에서 사용.
- 변경 파일:
  - 추가: `src/mlworkbench/api/{__init__,app,schemas}.py`, `frontend/`(Vite+React+TS: `src/{App,main,api,types,format}.tsx/ts`, `components/{SideNav,DatasetsPage,UploadPanel,DatasetList,StructurePanel,GoalForm,Icons}.tsx`, `styles.css`), `tests/integration/test_api.py`(12), `tests/e2e/check_s1_browser.py`
  - 수정: `app/services.py`(list_entries·state_since·recover_interrupted·rowid 정렬, PlanService.latest_draft_for_dataset·save_goal), `core/validation.py`(설비 구분=시간열 금지, 구간 중복), `datasets/csv_profile.py`(범주/텍스트 규칙), `cli.py`(`mlwb serve`, doctor 웹 의존성 안내 FastAPI·uvicorn), `pyproject.toml`(`web` extra, dev에 httpx·fastapi·uvicorn), `README.md`, `CONTRACTS.md` §11 "P1 React·FastAPI 구현 메모"
- 실제 구현 내용: 왼쪽 세로 메뉴(데이터셋 현재, 나머지 5개 연결 예정·좁은 창 아이콘+포커스 툴팁), 업로드·드래그앤드롭·목록·상태(업로드 % / 분석 중 경과 / 완료 / 실패 원인)·수정 파일 재등록, 구조 패널(행×열·시간축·피처 표·알림), 목표(시간열·타깃·설비 구분 선택·예측 구간) 저장·재조회. 분석은 별도 프로세스, 업로드는 스트림.
- 실행 명령/테스트 결과:
  - 사용자 PC 연결 폴더(Ubuntu 22.04.5, Python 3.10.12, Node 22.23.4): `python -m pytest -p no:cacheprovider` → **PASS 124 passed**(P0 71 + CSV 41 + API 12). `npx tsc -b` 통과, `npx vite build --emptyOutDir=false` 성공(이 환경은 연결 폴더 삭제가 막혀 Vite의 dist 비우기만 실패, 일반 Windows에서는 `npm run build` 그대로 사용). `mlwb serve` → health·화면 HTML·curl 업로드 → 별도 프로세스 분석 PROFILED(1,728행, 5min) 확인.
  - 교차: 작업 공간 Python 3.13.18·Node 22.23.5에서 pytest 124 passed, `npm run build` 성공.
  - **브라우저 검증**(Playwright + Chromium, 작업 공간에서 `mlwb` 앱을 127.0.0.1:8000에 띄우고 빈 워크스페이스로 실행, 스크립트 `tests/e2e/check_s1_browser.py`) — 10항목 PASS:
    1. 빈 상태·세로 메뉴: 현재=데이터셋(aria-current), 후속 5개 aria-disabled + "연결 예정"
    2. 합성 CSV 두 개(576행 / 한글 헤더 40행) 업로드 → 목록 선택 전환 시 행 수·피처명 변경 확인
    3. 목표 저장: 타깃 미선택 시 "예측 타깃을 선택하세요" → 선택 후 리비전 1 저장
    4. 새로고침 후 등록 결과·목표 재조회
    5. 헤더 빈 이름 CSV → "이름이 비어 있는 열이 있습니다…" → 수정 파일(100행)로 같은 ID 재등록
    6. 키보드: Tab으로 "CSV 파일 선택" 도달·Enter로 선택 창, 목록 ↑↓/Home/End 이동·선택
    7. 큰 CSV 80만 행(40.2MB) 분석 중 다른 데이터셋 선택·"분석 중 0:02" 경과 표시, 목록 API 최대 9ms, 업로드~완료 7~8초
    8. 820px: 메뉴 64px 아이콘, 업로드·목록이 구조 패널 위, 키보드 포커스 시 "전처리 연결 예정" 툴팁
    9. 390px: 동일 + 피처 표 가로 스크롤(이름 열 고정)
    10. 콘솔 오류 없음(의도한 목표 검증 422 응답 로그 제외)
  - 검증 중 발견·수정: ① 스레드 분석 시 80만 행에서 목록 응답 1.39초 지연 → 분석을 별도 프로세스로 분리(최대 9ms) ② 좁은 창 메뉴 이름이 `display:none`으로 보조기술에서 사라짐 → 시각적 숨김+툴팁 ③ 긴 화면에서 메뉴 배경 끊김 ④ 고정 이름 열 경계 없음 ⑤ 모두 고유한 소규모 메모 열이 범주로 판정 → 규칙 보정·회귀 테스트 ⑥ 같은 초 등록 순서 불안정 → rowid 정렬
- 설계 변경 및 이유: `CONTRACTS.md` §11 "P1 React·FastAPI 구현 메모".
- 남은 문제:
  - 최초 브라우저 검증은 작업 공간 Chromium에서 실행했다. Windows 네이티브 확인은 아래 재검증 기록 참조.
  - 목표 저장은 시계열만. 추천 요청의 컬럼명 별칭화는 여전히 추천 연결 작업에서.
  - 데이터셋 삭제·검색/필터·타입 수정은 아직 없음(SCREEN_DESIGN S1에는 있음).
  - `workspace/`에 PC 쪽 스모크 데이터셋과 전송용 `_transfer_repo.tgz`가 남아 있음(깃 제외, 삭제 가능).
- 다음 작업 한 가지 (P1): **S2 전처리 화면의 Mock 추천 연결** — 저장된 목표와 별칭 SafeProfile로 MockProvider 추천을 받아 전처리 카드(무엇을·왜)·체크·순서·파라미터 편집을 초안 검증(`pipeline.validate`)과 연결.

### 2026-11-19 — P1/S1-T Windows 재검증

- Windows 네이티브 CPython 3.13.17, Node 26.13.0에서 `npm ci` 후 `npm run build` **PASS**, `python -B -m pytest -p no:cacheprovider --basetemp=workspace/s1-windows-review/tests` **PASS 124개**(18.43초, Starlette 테스트 클라이언트의 의존성 사용 중단 예정 경고 1건).
- Windows에서 `python -B -m mlworkbench serve` 실행 후 설치된 Chrome을 Playwright로 제어해 S1 브라우저 검증 **10개 PASS**. 목록 전환, 목표 복원, 같은 ID 재등록, 키보드 조작, 820px·390px 화면과 별도 프로세스 분석을 확인했다. 합성 CSV 80만 행은 업로드부터 분석 완료까지 7.5초, 분석 중 측정한 목록 응답은 최대 14ms였다. 스크린샷은 `workspace/s1-windows-review/screenshots/`에 저장했다.
- 설치 확인에서 현재 `pyproject.toml`은 `dev = ["pytest>=7.4"]`만 선언하고 있어 README의 웹 설치 안내와 차이가 있었다. 재검증은 별도 폴더에 웹·테스트 의존성을 직접 설치해 실행했다. 선언과 안내를 맞추는 작업은 `P1_PREPROCESS_BRIEF.md`에 포함했다.

### 2026-11-19 — P1/S2 전처리 화면 + Mock 추천 (별칭 요청)

- 목적: `P1_PREPROCESS_BRIEF.md`. 저장한 시계열 목표로 오프라인 Mock 추천을 받아 전처리 항목·순서·파라미터를 편집·검증·저장.
- 변경 파일:
  - 추가: `src/mlworkbench/app/recommend_service.py`, `frontend/src/components/{PreprocessPage,ParamEditor}.tsx`, `tests/integration/test_preprocess.py`(18), `tests/e2e/check_s2_browser.py`
  - 수정: `core/contracts.py`(DraftPlan `excluded_steps`·`recommendation_id`·source `goal`), `app/services.py`(PlanService `save_steps`·`apply_recommendation`·`validate_plan`, `REVISION_CONFLICT`), `api/{app,schemas}.py`(plan·recommendation·apply·validate API), `providers/{mock,gateway}.py`(미리보기 후보), `plugins/builtin.py`(`x-column`), `storage/database.py`(recommendations 표), `frontend/src/{App,api,types,styles.css}`, `components/{SideNav,DatasetsPage,StructurePanel,GoalForm}.tsx`, `tests/unit/test_contracts.py`, `tests/e2e/check_s1_browser.py`, `pyproject.toml`(`web`·`dev` extra), `README.md`, `CONTRACTS.md` §11 "P1 S2 전처리·Mock 추천 구현 메모"
- 실제 구현 내용: 목표 저장 후 전처리 메뉴 열림, 진입 시 자동 Mock 추천(목표만 저장된 초안일 때), 카드별 한글 이름·하는 일·추천 이유(일반 텍스트)·구현 상태, 체크/제외 보관, ▲▼(고정 단계 비활성), 스키마 기반 파라미터 편집, 0.5초 뒤 초안 검증과 항목별 메시지, 저장·새로고침 복원, 다시 추천 시 교체 선택, 수동 모드, 추천 실패 안내, 리비전 충돌 안내.
- 실행 명령/테스트 결과:
  - 사용자 PC 연결 폴더(Ubuntu 22.04.5, Python 3.10.12, Node 22.23.4): `python -m pytest -p no:cacheprovider` → **PASS 142 passed**(P0 71 + CSV 41 + API 12 + S2 18). `npx tsc -b` 통과, `npx vite build --emptyOutDir=false` 성공.
  - 설치 선언 확인(각각 새 가상환경, 프로젝트 사본): `pip install ".[web]"` → fastapi·uvicorn 설치, 테스트 의존성 없음, `mlwb serve` 기동·CSV 업로드→PROFILED 확인 / `pip install -e ".[dev]"` → pytest·httpx 포함, **142 passed**.
  - 교차: 작업 공간 Python 3.13.18에서 142 passed, `npm run build` 성공.
  - **브라우저 검증**(작업 공간 Chromium/Playwright, 빈 워크스페이스, `tests/e2e/check_s2_browser.py`) — 13항목 PASS: 목표 저장 전 전처리 잠김→저장 후 진입 / 자동 Mock 추천·이유·"계획 미리보기 · 실행 불가" / 체크 해제·추가·파라미터(lags 0 → "1 이상" 오류 → 수정)·순서 오류 표시→저장(리비전 3) / 새로고침·메뉴 이동 후 복원 / 다른 탭 저장으로 리비전 충돌→최신 불러오기 / 다시 추천 시 유지·바꾸기 선택 / 추천 실패(422) 한글 원인·코드·기존 초안 유지 / 수동 모드 저장·새로고침 후 자동 추천 없음 / 목표 없는 데이터셋 안내 / 키보드(Space·Enter) / 820px·390px / 콘솔 오류 없음.
  - S1 회귀(`check_s1_browser.py`) — 12항목 PASS(전처리 메뉴 잠금 문구 변경 반영, 상단 바 검색·앱 창 제어 버튼 항목 추가).
  - 추천 요청 검사: 경로·키처럼 보이는 헤더와 고유 셀 값을 넣은 CSV로 요청 전체를 직렬화해 원래 헤더·셀 값·파일명·작업 공간 경로가 없음을 확인, 목표는 `column_5/column_1/column_4`로 전송되고 화면에는 원래 이름으로 복원.
- 검증 중 발견·수정: 수동 모드로 바꿔도 추천 출처가 남던 문제(서버에서 `recommendation_id` 해제), 상세 패널의 미구현 메시지 중복.
- **작업 충돌 기록:** 05:25~05:27 다른 작업(상단 바 `WorkbenchBar`, 데이터셋 이름 검색, pywebview 실행기)이 같은 폴더에서 진행되는 동안 05:29 S2 동기화가 그 작업의 `App.tsx`·`DatasetsPage.tsx`·`styles.css` 변경을 덮어썼다(사이드 메뉴 로고 제거 포함). 05:26 빌드 결과를 `workspace/_recovery_dist_0526/`에 백업한 뒤 번들에서 변경을 확인해 S2 코드와 병합했다. 병합한 CSS에서 S2 부분을 뺀 빌드가 05:26 빌드 CSS와 규칙 단위로 일치함을 확인했고, 검색·창 제어 동작은 S1 브라우저 검증에 항목으로 추가했다. `WorkbenchBar.tsx`·`DatasetList.tsx`·실행기·README·SCREEN_DESIGN은 영향 없음. 이후 쓰기 전에는 대상 파일 checksum과 폴더 변경 시각을 확인했다.
- 남은 문제:
  - 최초 브라우저 검증은 작업 공간 Chromium에서 실행했다. Windows Chrome·WebView2 확인은 아래 기록 참조.
  - 전처리를 실제 데이터에 적용하는 미리보기(D-09), 외부 공급자 호출, 학습 설정·승인·실행은 후속.
  - Mock 추천은 데이터 프로파일과 무관한 고정 규칙(설명용 이유). 모델 추천(XGBoost)은 초안에만 저장되고 S3에서 표시.
  - `workspace/`에 검증용 `_recovery_dist_0526/`, `_transfer_repo.tgz`, `_md5_device.txt`가 있다(깃 제외, 확인 후 삭제 가능).
- 다음 작업 한 가지 (P1): **S3 학습 설정** — 저장된 초안의 모델 후보(XGBoost)·학습량(전체/최근 N일)·시간순 분할·평가 지표를 편집하고 초안 검증과 연결(승인·실행은 S4 이후).

### 2026-11-19 — P1/S2 병합본 Windows 검수

- Windows 네이티브 CPython 3.13.17에서 `python -B -m pytest -p no:cacheprovider --basetemp=workspace/s1-windows-review/s2-tests` **142개 통과**, 프런트엔드 `npm run build` 통과. `pyproject.toml`의 `web`·`dev` 설치 설정도 현재 작업 폴더에서 확인했다.
- 설치된 Windows Chrome에서 S1 **12개 통과**. S2는 처음에 390px에서 전처리 목록의 그리드가 가로로 넘쳤다. 목록 열이 줄어들 수 있게 CSS를 수정하고 빌드한 뒤 S2 **13개 통과**. 작업 폴더와 검증 빌드의 소스 해시를 대조한 뒤 `frontend/dist/`를 갱신했다.
- 별도 테스트 작업 공간의 WebView2에서 S2 제목, 전처리 카드 4개, 상단 검색, 창 버튼 3개와 창 제어 브리지를 확인했다. 전체 S2 편집 흐름은 Chrome에서 검증했다.
- 상단 검색으로 데이터셋 화면에 접근하는 방향을 유지한다. 다른 화면에서는 Enter로 이동을 확정하고, 미저장 편집을 처리하도록 S3 지시서에 추가했다.

### 2026-11-19 — 앱 창 실행기: 창을 닫으면 서버도 종료

- 계기: Windows 앱 창에서 전처리 화면이 "대상을 찾을 수 없습니다"로 실패. 실행기가 S2 이전(05:20)에 띄운 서버를 계속 재사용해 새 API(`/plan`)가 없었다(앱 DB에 `recommendations` 표 없음으로 확인).
- 변경: `scripts/open_workbench.py` — 서버 PID를 `workspace/edge-app/app-instance.json`에 기록하고, 창이 닫히면(오류 종료 포함) 서버와 하위 분석 프로세스를 트리째 종료(Windows `taskkill /T /F`, 그 외 프로세스 그룹 종료). 다음 실행 시 남은 서버(기록된 PID, 또는 기록 없이 포트를 쓰는 이전 실행기 서버)를 먼저 정리하고 새로 띄움. 창이 열려 있으면 두 번째 실행은 알림 후 종료. 추가: `tests/integration/test_launcher.py`(가짜 webview로 실행기 프로세스 로직 실행).
- 결과: 작업 공간(Python 3.13)·PC 쪽 Linux 환경(Python 3.10)에서 `test_launcher.py` 3개 PASS(창 닫기 → 서버·분석 프로세스·포트·상태 파일 정리 / 실행기 강제 종료 후 재실행 시 남은 서버 교체 / 두 번째 실행이 첫 창 서버 유지), 전체 pytest 145 passed.
- 미확인: Windows 경로(`tasklist`·`taskkill`·`netstat`)와 실제 pywebview 창은 미실행. Windows에서 `python -m pytest tests/integration/test_launcher.py`로 같은 시나리오를 확인할 수 있다.

### 2026-11-19 — P1/S3 학습 설정 (모델·학습량·분할·평가)

- 범위: `P1_TRAIN_SETTINGS_BRIEF.md`. 저장된 초안의 모델·학습 데이터량·분할·평가를 편집·검증·저장. 승인·실행·실제 기간 계산·비전 설정·프리셋은 범위 밖.
- 변경 파일
  - 수정: `core/validation.py`(pydantic 오류 한국어 메시지·union 태그 제거 위치, `EVALUATION_GROUP_MISSING`), `app/services.py`(PlanService `training_candidate`·`training_issues`·`save_training`, 분할 비율 % 문구), `api/{app,schemas}.py`(`GET/PUT /api/datasets/{id}/training`, `POST .../training/validate`), `frontend/src/{App,api,types,styles.css}`, `components/{PreprocessPage,WorkbenchBar}.tsx`, `tests/e2e/check_s1_browser.py`, `README.md`, `CONTRACTS.md` §11 "P1 S3 학습 설정 구현 메모"
  - 추가: `frontend/src/components/TrainingPage.tsx`, `tests/integration/test_training.py`(13), `tests/e2e/check_s3_browser.py`(10)
- 화면: 모델(추천 표시·구현 상태·파라미터 편집, XGBoost는 계획 미리보기), 학습 데이터량(전체/최근 N일 + 개념도 — 실제 기간·행 수는 표시하지 않음), 분할·평가(학습/검증/테스트 %, 합계 표시, 최대 horizon 기준 자동 gap 안내, MAE·RMSE·R²·sMAPE, 기준 모델 직전값/사용 안 함, horizon별·그룹별 집계). 입력 중 검증(지연), 오류는 해당 입력 아래 표시, 저장 시 리비전 충돌이면 다시 불러오기 안내. "다음: 확인·실행"은 연결 예정(비활성).
- 상단 검색: 데이터셋 화면은 입력 즉시 필터. 전처리·학습 설정 화면은 입력해도 화면 유지, Enter로 데이터셋 화면 이동. 저장하지 않은 편집이 있으면 "저장하고 이동/버리고 이동/취소". 창 드래그·창 버튼 동작은 그대로(WorkbenchBar에는 선택 prop `onSubmit`만 추가).
- 테스트(작업 공간, Python 3.13 · Chromium)
  - `python -m pytest -p no:cacheprovider` → **158 passed**(기존 145 + S3 13).
  - 브라우저: S1 **12/12**, S2 **13/13**, S3 **10/10** PASS. S3 항목에 예제 CSV(`motor_sensor_example.csv`, 타깃 temperature_c)로 S1 목표 → S2 전처리 저장 → S3 **최근 30일·시간순 70/15/15·MAE·RMSE 저장 후 새로고침해 전처리와 학습 설정이 함께 복원**, 다른 비율(60/20/20) 저장·복원, 합계 ≠ 100%·일수 0·지표 0개·잘못된 파라미터 오류 위치, 검색 입력만으로는 이동 안 함 / Enter 이동 / 미저장 시 확인 대화상자 포함.
  - S1 브라우저 검증에서 파일 선택 대기 시간 초과가 1회 발생, 재실행 2회 모두 통과(간헐적, 코드 변경 없음).
- 사용자 PC 연결 폴더(Ubuntu 22.04.5, Python 3.10.12, Node 22): 반영 직전 수정 대상 14개 파일의 checksum이 작업 시작 기준과 같은지 확인(사용자가 05:45에 수정한 `styles.css`의 `.prep-list` 규칙은 병합). 반영 후 소스 전체 해시가 작업 공간과 일치. `python -m pytest -p no:cacheprovider` → **PASS 158 passed**, `npx tsc -b` 통과, `npx vite build --emptyOutDir=false` 성공. 브라우저 검증은 PC에서 미실행.
- 알려진 한계
  - 내장 모델이 모두 미구현이라 학습 설정 초안도 계획 미리보기 상태(승인 불가). ready 레지스트리로는 승인 스냅샷에 설정이 들어가는 것을 통합 테스트로 확인.
  - 최근 N일의 실제 기간·행 수, 분할 경계 날짜는 파이프라인 계산 단계(P2)에서 표시.
  - Windows Chrome·WebView2에서 S3 검증 미실행.
- 다음 작업 한 가지 (P1): **S4 확인·실행 화면** — 저장된 초안의 최종 검증 결과와 계획 요약을 보여 주고, 승인(`plan_hash` 저장)·수정 시 승인 무효화를 연결(실제 학습 제출은 P2).

### 2026-11-19 — 저장 메시지의 리비전 번호 표시 제거

- 사용자 요청: 저장할 때마다 보이던 "(리비전 N)"이 불필요. 목표·전처리·학습 설정 저장 메시지와 충돌 안내 문구에서 번호를 뺐다(`GoalForm`·`PreprocessPage`·`TrainingPage`, `PlanService`의 `REVISION_CONFLICT` 메시지). 리비전 값은 API 응답과 충돌 검사(`expected_revision`)에 그대로 쓴다. 브라우저 검증 S1·S2의 문구 기대값 수정.

### 2026-11-19 — P1/S4 계획 요약·최종 검증·승인

- 범위: `P1_CONFIRMATION_BRIEF.md`. 학습 제출·실제 기간 계산·YAML 내보내기·추천 대비 변경 내역은 범위 밖.
- 시작 전 병합: 다른 작업(ADD-1·ADD-2)이 바꾼 `core/{contracts,registry,schema_check,validation}.py`, `plugins/preprocessor.py`, `tests/unit/test_preprocessor_adapter.py`를 작업 공간으로 가져와 그 위에서 작업(해당 파일은 이번에 수정하지 않음). 반영 직전 수정 대상 13개 파일의 checksum이 작업 시작 시점과 같음을 다시 확인.
- 변경 파일
  - 수정: `app/services.py`(PlanService `confirmation`·`approve_for_dataset`·`issue_step`·`_approval_state`), `api/{app,schemas}.py`(`GET /api/datasets/{id}/confirmation`, `POST .../approve`), `frontend/src/{App,api,types,styles.css}`, `components/{TrainingPage,PreprocessPage,GoalForm}.tsx`, `tests/e2e/check_s{1,2,3}_browser.py`, `README.md`, `CONTRACTS.md` §11 "P1 S4 확인·승인 구현 메모"
  - 추가: `frontend/src/components/ConfirmPage.tsx`, `tests/integration/test_confirmation.py`(10), `tests/e2e/check_s4_browser.py`(10), `tests/e2e/serve_ready_registry.py`(테스트 전용 실행 가능 목록 서버)
- 화면: 주소 `#/confirm/<id>`, 메뉴 현재 위치는 학습 설정. S3 하단 "다음: 확인·실행"(미저장 시 "저장하고 확인·실행으로") 연결. 상단 배너(차단 오류 N건 / 경고 N건 승인 가능 / 검증 통과), 이전 승인 무효화 안내, 확인할 항목 목록(오류·경고 + "전처리에서 고치기 →" 등 S1·S2·S3 링크), 계획 요약, 확인 체크 + **설정 승인**(오류 0건·체크 시에만), 학습 시작 ▶은 비활성(P2).
- 서버: 내장 Registry는 그대로이며 미구현 후보는 최종 검증에서 승인 차단(422 `VALIDATION_FAILED`, 항목별 고칠 화면 포함). 확인 체크 없으면 400 `CONFIRMATION_REQUIRED`, 리비전 불일치 409 `REVISION_CONFLICT`. 같은 실행 설정이 이미 승인되어 있으면 새 승인을 만들지 않음. 응답에는 승인 상태·`plan_id`·`plan_hash`·시각만 있고 승인 토큰은 없음. 실행 기록은 만들지 않음.
- 테스트(작업 공간, Python 3.13 · Chromium)
  - `python -m pytest -p no:cacheprovider` → **175 passed**(ADD 포함 165 + S4 10). S4 통합: 요약 내용, 미구현 승인 차단·고칠 화면, 경로→화면 매핑, 정상 승인·해시 재조회·토큰 비노출(응답·로그), 확인 체크 필수, 잘못된 리비전 거부, 실행 설정 변경 후 무효화·재승인, 설명만 바뀌면 승인 유지, 경고만 있을 때 승인, 목표 없을 때 진입 거부. 모든 경우 실행 기록 0건.
  - 브라우저(예제 CSV `motor_sensor_example.csv`): S1 **12/12**, S2 **13/13**, S3 **10/10**, S4 **10/10** PASS. S4 1부(내장 레지스트리): S3 저장 → S4 요약(최근 30일·70/15/15·MAE·RMSE) → 미구현 후보 오류 5건으로 체크·승인 비활성 → 오류 링크로 S2·S3 이동 후 복귀, 820·390px 가로 넘침 없음. 2부(테스트 전용 실행 가능 목록 서버): 체크 → 승인 → `plan_hash` 표시·새로고침 후 동일 → 학습량 변경 → 무효화 안내 → 재승인(새 해시) → 다른 탭 저장 후 승인 시 충돌 안내·최신 불러오기, 확인·승인 응답 10건에 토큰 없음. 검증 후 두 작업 공간 모두 runs 0건.
  - 390px에서 긴 플러그인 ID가 든 오류 문구가 가로로 넘쳐 `overflow-wrap`을 추가해 수정.
- 사용자 PC 연결 폴더(Ubuntu 22.04.5, Python 3.10.12, Node 22): 반영 후 소스 전체 해시가 작업 공간과 일치. `python -m pytest -p no:cacheprovider` → **PASS 175 passed**, `npx tsc -b` 통과, `npx vite build --emptyOutDir=false` 성공. 브라우저 검증은 PC에서 미실행.
- 알려진 한계
  - 화면 응답에 승인 토큰을 넣지 않으므로, P2의 학습 시작은 서버 안에서 승인 확인과 제출을 함께 처리하는 경로가 필요하다(CONTRACTS §11 메모).
  - 실제 학습 기간·행 수·예상 소요 시간, 추천 대비 변경 내역, YAML 저장은 미구현(값이 없으면 안내 문구).
  - Windows Chrome·WebView2에서 S4 검증 미실행.
- 다음 작업 한 가지: **P2 학습 제출 연결** — S4 "학습 시작"에서 서버가 활성 승인과 `plan_hash`를 재확인하고 `JobService.submit`으로 실행 기록을 만드는 경로(승인 토큰은 서버 내부에서만 사용)와 S5 상태 화면의 최소 연결.

## Claude 현재 작업 지시

> 프로젝트 루트의 `CLAUDE.md`와 `docs/P2_XLSX_BRIEF.md`를 읽고 XLSX 등록·시트 선택부터 기존 시계열 학습과 S7 적용까지 연결해 줘. 기존 CSV·모델·추론 경로를 재사용하고 `docs/PROGRESS.md`에 테스트와 실행 결과를 남겨 줘.

### 2026-11-19 — 문서 위치 정리 및 P2 구현 지시 작성

- 설계·계약·요구사항·화면 설계·단계별 지시와 이 진행 기록을 `docs/`로 옮겼다. 기존 작업 이력을 유지하고 루트 README와 문서 안내 링크를 갱신했다. 루트 `CLAUDE.md`는 `docs/CLAUDE.md`의 작업 규칙과 현재 지시서로 안내한다.
- `P2_TIMESERIES_BRIEF.md`에 실제 전처리·분할·CPU XGBoost·동일 샘플 baseline 평가·저장과 S5 연결의 범위 및 검수 기준을 작성했다. ADD-2 어댑터 재사용, 승인 토큰이 해시만 저장되는 기존 구조, causal 전처리·분할 누수와 Windows worker 상태 처리를 포함했다.
- 이번 작업은 문서 이동·참조 정리와 구현 지시 작성이다. P2 구현과 학습 실행 증거는 다음 작업에서 기록한다.
- 문서 검증 결과. `docs/`의 기존 문서 12개와 새 지시서 1개, 로컬 Markdown 링크 27개가 존재함을 확인했다. 루트 Markdown은 `README.md`와 진입용 `CLAUDE.md` 두 개다.

### 2026-11-19 — P2/첫 시계열 학습 실행 (CSV → 전처리 → 분할 → XGBoost CPU → 평가·저장 → S5)

- 범위: `P2_TIMESERIES_BRIEF.md`. 새 CSV 적용(S7)·rolling/diff/시간 피처·대기열·XLSX는 범위 밖(남은 항목).
- 설치(사용자 승인): 작업 공간에 xgboost 3.4.1, PC 쪽 Linux 검증 환경(`~/.venv-mlwb`)에 numpy 2.2.6·pandas 2.3.3·scikit-learn 1.7.2·xgboost 3.2.0. Windows `.venv`는 건드리지 않음. `pyproject.toml`에 `timeseries` optional extra 추가.
- 시작 전·반영 직전 병합: 다른 작업이 바꾼 `core/contracts.py`·`tests/integration/test_csv_dataset.py`·`configs/providers.yaml`(문서 경로 주석 `docs/`)을 받아 병합. 덮어쓴 파일은 모두 S4 반영본·ADD 반영본과 같은 상태임을 checksum으로 확인.
- 변경 파일
  - 추가: `pipelines/{__init__,ts_frame,timeseries,metrics}.py`, `plugins/{timeseries,models}.py`, `runner/{__init__,__main__,worker,manager}.py`, `frontend/src/components/RunPages.tsx`, `tests/unit/test_timeseries_pipeline.py`(15), `tests/integration/test_runs.py`(9), `tests/e2e/check_p2_browser.py`(8)
  - 수정: `core/registry.py`(모델 어댑터 등록·내장 ID 어댑터 교체), `core/validation.py`(LEGACY_PARAM_VALUE·HORIZON_NOT_MULTIPLE·EVAL_OPTION_NOT_IMPLEMENTED), `core/contracts.py`(RunRecord 실행 필드), `plugins/builtin.py`(`ts.missing` 선형 보간 제거), `providers/mock.py`(기본 `ffill`), `app/services.py`(`submit_approved`·`_submit` 공통화·실행 기록·`submitted` 승인 상태), `api/{app,schemas}.py`(실행 API·worker 관리), `cli.py`(`mlwb experiment verify`), `frontend/src/{App,api,types,styles.css}`, `components/{ConfirmPage,ParamEditor}.tsx`, 기존 테스트(미리보기 흐름은 명세만 있는 카탈로그 주입), `pyproject.toml`, `README.md`, `CONTRACTS.md` §11 "P2 시계열 실행 메모"
- 구현 요약: 구현이 있는 `ts.session_split·resample·missing·lag·scale`과 `timeseries.forecasting.xgboost`는 같은 ID·버전 명세로 만든 어댑터 등록으로 교체되며 의존성이 있을 때만 ready. S4 학습 시작 → 서버가 계획 ID·해시를 승인 기록과 대조해 기존 제출 트랜잭션으로 실행 등록 → 실행당 worker 프로세스 1개(잠금 파일) → prepare·preprocess·train·evaluate·save 이벤트 → `workspace/experiments/<run_id>/`에 config·dataset_manifest·environment·split(json·행 목록)·model(pipeline.json·xgb_<h>.json)·predictions·metrics·metrics_meta·train.log 저장. S5에서 단계·진행률·경과·이벤트·오류·취소와 테스트 지표(모델/직전값, horizon별·설비별)·실제 분할 기간·예측 CSV 내려받기.
- 테스트(작업 공간, Python 3.13 · pandas 3.0.5 · xgboost 3.4.1 · Chromium)
  - `python -m pytest -p no:cacheprovider` → **205 passed**. 누수·경계: 미래값을 바꿔도 과거 피처·라벨 동일, 리샘플 (t-f, t] 라벨 t, 그룹·세션 경계를 넘는 lag·타깃 없음, ffill은 과거만·max_gap, 분할 경계를 넘는 라벨 제외·같은 시각 같은 분할, 스케일러 fit=학습 표본 평균·테스트 구간 변경에 불변, 학습량만 바꾼 두 실행의 검증·테스트 표본 동일. 지표 정의·계산 불가(R² 분산 0, sMAPE 0/0). 같은 seed 재실행 지표 차이 0, 저장 모델 재로딩 예측 차이 < 1e-9. 실행: 정상 완료·산출물, 동시 4회+재요청 → 실행 1건·worker 1개, 임의 계획 ID·다른 해시·다른 데이터셋·승인 후 설정 변경 거부, 파일 손상 시 FAILED(NO_VALID_ROWS), worker 강제 종료 → FAILED(WORKER_EXITED), 확인 없는 취소 거부·확인 후 CANCELED·프로세스 종료, 앱 종료 → worker 종료·FAILED(APP_SHUTDOWN), 재시작 시 시작 못 한 실행 FAILED(WORKER_NOT_STARTED)·heartbeat 멈춘 실행/죽은 PID FAILED(WORKER_LOST). 모든 승인·실행 응답에 승인 토큰 없음.
  - 브라우저(예제 `motor_sensor_example.csv`, 실제 내장 Registry): **P2 8/8** — 15분·30분, 최근 30일, 70/15/15, MAE·RMSE·직전값, 설비별 → 승인 → 학습 시작 → 완료(약 2.3초) → 테스트 MAE 0.439(직전값 1.252), RMSE 0.808(1.804), 예측 CSV 6,900행 / 같은 승인 재요청 → 같은 실행 / 학습량 '전체'로 바꾼 두 번째 실행: 검증·테스트 구간·표본(3,450) 동일, 학습 표본 11,514 → 16,122 / 실행 목록 / 트리 5,000개 학습 중 데이터셋 목록 응답 최대 11ms·확인 후 취소됨 / 820·390px. 기존 S1 12·S2 13·S3 10·S4 10 PASS(S2·S3·S4 1부는 명세만 있는 카탈로그 서버, S1·P2는 실제 내장 Registry).
  - 재현성: `mlwb experiment verify <run_id>`(같은 seed 42로 임시 폴더에 재계산, 새 실행 기록 없음) → 검증·테스트 MAE·RMSE 재실행 차이 0, 저장 예측값으로 재계산한 차이 최대 1.1e-16(허용 오차 1e-6).
- 사용자 PC 연결 폴더(Ubuntu 22.04.5, Python 3.10.12, pandas 2.3.3, xgboost 3.2.0): 반영 후 소스 전체 해시가 작업 공간과 일치. pytest **205 passed**(unit 89 + integration 107 + 실제 worker 실행 9, 도구 시간 제한으로 나눠 실행), `npx tsc -b` 통과, `npx vite build --emptyOutDir=false` 성공. 브라우저 검증은 PC에서 미실행.
- 알려진 한계
  - Windows 경로(worker `CREATE_NO_WINDOW`·낮은 우선순위·`TerminateProcess`, PID 확인 `OpenProcess`)와 pywebview 창 종료 연동은 미실행. Windows에서 `python -m pytest tests/integration/test_runs.py`로 같은 시나리오를 확인할 수 있다(테스트의 프로세스 확인·강제 종료는 Windows·POSIX 공통 경로 사용).
  - 동시 실행 수 제한(대기열) 없음. CPU worker는 n_jobs=2·낮은 우선순위.
  - 문자·범주 열은 피처로 쓰지 않음(기록됨). 리샘플 없이 불규칙 간격이면 t+h와 정확히 같은 시각의 관측이 있을 때만 표본.
  - 이전 초안의 `ts.missing` 선형 보간은 S2·S4에서 '사용할 수 없음' 오류와 바꿀 방법을 표시(자동 변경하지 않음).
- 다음 작업 한 가지 (P2): **S7 새 CSV 예측·내려받기** — 완료된 실행의 저장 파이프라인(`FittedTimeseries.load`)으로 새 CSV의 입력 조건(열·간격·시각 형식)을 점검하고 예측해 CSV로 내려받기.

### 2026-11-19 — P2/S7 새 CSV 예측·내려받기 (저장 모델로 추론, 학습 없음)

- 범위: `P2_INFERENCE_BRIEF.md`. 완료 실행 선택 → 새 CSV 입력 점검 → 저장 모델로 예측 → CSV 내려받기. MCP 노출·열 매핑 UI·XLSX는 범위 밖.
- 반영 직전 확인: 수정 대상 코드 파일이 모두 P2 반영본과 같았음(다른 작업의 코드 변경 없음). 문서는 사용자가 07:39에 갱신한 현재 내용 위에 추가.
- 변경 파일
  - 추가: `app/inference_service.py`(InferenceService·`predict_job`), `frontend/src/components/ApplyPage.tsx`, `tests/integration/test_inference.py`(17), `tests/e2e/check_s7_browser.py`(10), `tests/e2e/make_future_csv.py`
  - 수정: `pipelines/timeseries.py`(`FittedTimeseries`: 엄격한 불러오기·`requirements`·`prepare`/`predict` 분리, pipeline.json에 시각 정책 저장), `storage/database.py`(applications 표), `api/app.py`(모델·적용 API, 예측 작업은 기존 분석용 프로세스 풀), `runner/{manager,worker}.py`(Windows `ctypes` 64비트 핸들 형식 명시), `frontend/src/{App,api,types,styles.css}`, `components/RunPages.tsx`(S5 → "이 모델로 새 데이터에 적용"), `tests/unit/test_timeseries_pipeline.py`(+10), `tests/e2e/check_s1_browser.py`(새 데이터 적용 메뉴 열림, 파일 선택 키 입력 재시도), `README.md`, `CONTRACTS.md` §11 "P2 S7 새 데이터 적용 메모"
- 화면(S7, `#/apply`, `#/apply/<run_id>`, `#/apply/<application_id>`): ① 완료 모델 선택(데이터셋·모델·horizon·완료 시각·run·테스트 MAE, 없으면 안내) ② 필요한 입력(열·역할·쓰는 이유 — 과거 타깃은 기준 시각 값·lag에 필요, 예측 대상 시각 실제값은 불필요 / 전처리 / 과거 이력 8칸 / 시각 형식 / 다른 열 정책) ③ 새 CSV 점검(읽은·사용 행, 기간·설비, 예측 가능 표본과 horizon별, 이력 부족·기준 시각 타깃 없음 제외 건수, 읽지 않은 열, 마지막 관측 이후 시각) ④ 예측(진행 상태·실패 이유, 건수·설비·기간·horizon, 결과 일부 20행, CSV 내려받기) + 최근 적용 기록. 모델을 바꾸면 이전 점검과 구분해 경고하고 같은 파일로 새 기록을 만들어 재점검. 새로고침해도 적용 기록으로 복원.
- 계약 요약: 적용 기록 = 원본 실행(run_id·plan_hash) × 입력 내용 해시. 예측 직전 입력·모델 파일 해시를 점검 때와 대조. 결과 열 `model_run_id, model_plan_hash, group, origin_time, target_time, horizon, predicted`. 원본 실행 폴더·입력 파일은 바꾸지 않음. 실패 기록은 내려받기 불가. 상세는 CONTRACTS 메모.
- 이후 기간 합성 CSV(`tests/e2e/make_future_csv.py`): 학습 예제의 마지막 7일(2026-10-24 ~ 10-30)을 7일 뒤로 옮겨 2026-10-31 00:00 ~ 11-06 23:45(학습·평가와 겹치지 않음), 수치 열에 seed 7 정규 잡음(표준편차 0.05), 1,344행. 작업 공간과 PC에서 같은 파일(md5 `f17db898…`) 생성 확인, PC에는 `workspace/e2e-data/motor_sensor_future.csv`.
- 테스트(작업 공간, Python 3.13 · pandas 3.0.5 · xgboost 3.4.1 · Chromium)
  - `python -m pytest -p no:cacheprovider` → **232 passed**(P2 205 + S7 통합 17 + 저장 파이프라인 무결성 단위 10). 같은 원본 재적용 = 저장 평가 예측(조인 비교, 최대 차이 < 1e-9, 평가 표본 90% 이상 조인·평가 밖 표본 추가 확인), 이후 기간 예측(마지막 관측 이후 대상 포함), 열 순서·추가 열(결과 동일, 읽지 않은 열 표시), 빠진 타깃·설비 열, 과거 타깃 없음, 이력 부족 0표본, 시각 형식 충돌(오프셋·혼합), CSV 아님·빈 파일, 점검 후 입력·모델 변경 → 예측 실패, 손상 모델 → 점검 실패 → 복구 후 재점검, 모델 변경 → 새 기록·이전 기록 유지, 모든 fit 호출을 막아도 추론 성공, 중복 예측 요청, 재시작 시 RUNNING 적용 정리(내려받기 불가), 실패·취소 실행 선택 불가, 원본 실행 폴더 해시·학습 실행 수 불변. 단위: 상태 개수·플러그인 불일치(`PIPELINE_STATE_MISMATCH`, zip 잘림 방지), 모델 파일 누락·손상, pipeline.json 누락·손상·형식, xgboost 없음 → `PLUGIN_UNAVAILABLE`, 저장 스케일러로만 변환(새 데이터로 다시 맞추지 않음).
  - 브라우저(실제 내장 Registry, 서버는 별도 프로세스 풀로 예측): **S7 10/10** — 모델 없을 때 안내 / S5 완료 실행 → '이 모델로 새 데이터에 적용' → 필요한 열·lag·이력 8칸 / 타깃 열 없는 파일 → "필요한 열이 없습니다: temperature_c", 예측 비활성 / 이후 기간 CSV → 점검 통과 → 예측 완료 2,656건(15·30분 각 1,328, 설비 2, 이력 부족 32 제외, 마지막 관측 이후 6건) → 내려받은 CSV 열·run_id·plan_hash·행 수 확인 / 새로고침 후 복원 / 학습 원본 재적용 → 저장 평가 예측과 조인 6,900/6,900행, 최대 차이 0 / 두 번째 모델로 변경 → 경고·재점검 → 새 기록, 이전 기록 유지 / 학습 실행 수 불변 / 820·390px / 콘솔 오류 없음. 회귀: S1 12(3회 연속), S2 13, S3 10, S4 10, P2 8 PASS.
- 사용자 PC 연결 폴더(Ubuntu 22.04.5, Python 3.10.12, pandas 2.3.3, xgboost 3.2.0): 반영 후 소스 전체 해시가 작업 공간과 일치. pytest **232 passed**(unit+S7 116, 기타 integration 107, 실제 worker 9 — 도구 시간 제한으로 나눠 실행), `npx tsc -b` 통과, `npx vite build --emptyOutDir=false` 성공. 브라우저 검증은 PC에서 미실행.
- Windows 확인: **미실행**(이 세션의 PC 쪽 셸은 Linux 가상 환경). `ctypes` 호출은 `OpenProcess`·`GetExitCodeProcess`·`CloseHandle`·`GetCurrentProcess`·`SetPriorityClass`의 인자·반환 형식을 `wintypes.HANDLE` 등으로 명시해 64비트 핸들 잘림을 막았다(Linux에서는 이 경로가 실행되지 않아 코드 검토만). Windows에서 확인할 명령: `pip install -e ".[dev,timeseries]"` 후 `python -m pytest tests/integration/test_runs.py tests/integration/test_inference.py`, 그리고 `mlwb serve` + `python tests/e2e/make_future_csv.py` + `python tests/e2e/check_s7_browser.py`(Chrome), 앱 창(WebView2)에서 S5 → S7 이동.
- 알려진 한계
  - 열 이름이 학습 때와 다르면 매핑하지 않고 부족한 이름과 필요한 이름을 보여 준다(매핑 UI는 후속).
  - 이력 부족 행은 예측에서 제외한다(학습 때 XGBoost는 결측 lag도 받았지만, 추론 결과의 신뢰를 위해 이력이 모두 있는 기준 시각만 예측).
  - 예측 작업은 분석용 프로세스 풀(최대 2개)을 같이 쓴다. 대용량 입력의 메모리·시간은 미측정.
  - S6 결과 비교 화면은 아직 없어서, 모델은 S5 실행 상세 또는 S7 목록에서 고른다.
- 다음 작업 한 가지 (P2): **Windows 실행 확인** — Windows Python에서 `test_runs.py`·`test_inference.py`와 Chrome·WebView2의 S5 → S7 흐름을 실행해 worker·프로세스 핸들·창 종료 연동을 확인(이 세션에서는 실행 불가).

### 2026-11-20 — Windows S7 실행 검수 기록

- 프로젝트 Windows `.venv`에 `pip install -e ".[dev,timeseries]" playwright` 설치 후 확인했다. Python 3.13.17, NumPy 2.5.5, pandas 3.1.1, scikit-learn 1.9.1, XGBoost 3.4.1, pywebview 6.2.1. 검증 데이터와 빌드는 `workspace/windows-s7-review-20261120/`에 뒀다.
- `test_runs.py`와 `test_inference.py`의 **26개 통과**. 실제 Windows worker 학습·중복 요청·강제 종료·취소·앱 종료·재시작, 추론 재현·입력 점검·학습 없는 추론을 포함한다. 프런트엔드 타입 검사와 Windows 빌드 통과. 전체 232개 테스트는 이번 검수에서 다시 실행하지 않았다.
- 추가로 실행한 런처 테스트 3개는 처음에 준비 대기 만료로 실패했다. 이 Windows 가상환경에서는 `Popen.pid`가 중간 실행 프로세스이고 실제 Python `os.getpid()`와 다르다. `tests/integration/test_launcher.py`에서 시작 시 실제 인터프리터 PID를 받아 준비 확인과 강제 종료 대상으로 사용하도록 보완했다. 한글 출력은 자식과 부모 모두 UTF-8로 맞췄다. 보완 후 `test_launcher.py` **3개 통과**(22.69초). 초기 실패가 남긴 테스트 서버는 명령줄에서 이번 검증 폴더 소유임을 확인하고 하위 프로세스와 함께 종료했다.
- 설치된 Windows Chrome에서 S7 **10/10 통과**. 새 기간 CSV 1,344행 → 예측 2,656건, 마지막 관측 이후 6건. 원본 재적용은 저장된 평가 예측 6,900행과 조인했을 때 최대 차이 0. 입력 오류·모델 변경·적용 기록 복원·다운로드·820/390px 화면도 확인했다. 스크린샷은 `workspace/windows-s7-review-20261120/screenshots/`에 있다.
- 실제 WebView2 창에서 S5 완료 실행 → S7로 이동해 필요한 입력·과거 이력과 기존 적용 결과 2,656건·다운로드 링크를 확인했다. 검증 창을 닫은 뒤 실행기가 정상 종료하고 서버 상태 파일이 제거되는 것을 확인했다. 새 파일 업로드부터 다운로드까지의 전체 S7 검증은 Chrome에서 수행했다.

### 2026-11-20 — P2/rolling·차분·시간 특징 실제 구현

- 범위: 사용자 지시(대화). 작업 순서는 ① rolling·차분·시간 특징 ② XLSX ③ 사용자 정의 플러그인 ④ 최소 대기열 ⑤ S6 실험 비교 — 이번은 ①.
- 시작 전 확인: PC 코드와 작업 공간을 파일별로 대조. 바뀐 파일은 Windows 검수 작업이 고친 `tests/integration/test_launcher.py` 하나(실제 인터프리터 PID 사용·UTF-8)였고 그대로 받아 포함. 반영 직전 수정 대상 6개 파일이 오전 확인과 같음을 다시 확인.
- 변경 파일
  - 수정: `plugins/timeseries.py`(`Rolling`·`Diff`·`Calendar` 어댑터, 열 검사 공통화), `pipelines/timeseries.py`(S7 요구사항: 열별로 쓰는 피처와 필요한 이력 = max(lag, rolling 창, 차분 칸)), `tests/unit/test_timeseries_pipeline.py`(+5), `tests/integration/test_inference.py`(+1), `tests/unit/test_security_cli.py`·`test_validation.py`(예제 계획이 이제 실행 가능 → 미구현 예시를 LightGBM으로), `CONTRACTS.md` §11, `README.md`
  - 추가: `tests/e2e/check_p2_features_browser.py`(6)
  - 카탈로그 ID·버전·파라미터 스키마는 바꾸지 않음(저장된 초안 호환). 화면 코드 변경 없음(S2 카드·파라미터 편집이 그대로 '실행 가능'으로 표시).
- 규칙(관측 가능 시점)
  - rolling: 같은 설비·세션 안에서 한 칸 민 값(현재 시점 제외)의 직전 w칸 통계(평균·표준편차·최소·최대), w칸이 다 찬 뒤부터 값. 필요한 이력 w칸(lag w와 같음).
  - 차분: 같은 설비·세션 안에서 x(t) − x(t−p). x(t)는 기준 시각의 관측값. 필요한 이력 p칸.
  - 시간 특징: 기준 시각 자체의 시·요일·월·주말 여부. 시각 해석은 데이터 읽기 정책(적힌 시각 그대로/UTC 변환)과 같음.
  - 대상 열 기본값은 타깃, 지정 시 수치 열만(문자·범주 열은 검증 오류).
- 테스트(작업 공간, Python 3.13 · pandas 3.0.5 · xgboost 3.4.1 · Chromium)
  - `python -m pytest -p no:cacheprovider` → **238 passed**. 단위: rolling이 현재 값을 빼고 직전 w칸만 쓰며 설비·세션 경계에서 다시 시작(수작업 계산과 일치), 차분·시간 특징 값(토요일 23:30 → 일요일 00:30 경계 포함), 미래값을 바꿔도 과거 기준 시각의 모든 새 피처 불변(경계 다음 칸 rolling도 불변), 수치 아닌 열 거부, **학습 때 만든 피처와 S7에서 저장 파이프라인으로 다시 만든 피처가 같은 키에서 일치**(NaN 포함), 저장 평가 예측과 S7 예측 차이 < 1e-9, S7 요구사항(이력 4칸·열별 rolling·시간 특징 표시)과 세션 첫 4칸만 제외. 통합: API로 S2에 rolling·차분·시간 특징 추가 → 실제 worker 학습 성공 → S7 재적용이 저장 평가 예측과 일치.
  - 브라우저(예제 CSV, 실제 내장 Registry): **피처 6/6** — S2에서 세 항목을 '실행 가능'으로 추가하고 파라미터 편집(rolling 창 4·8, 평균·표준편차, temperature_c·motor_current_a / 차분 1칸 / 시·요일·주말) → S3 최근 30일 → S4 승인 → 학습 완료(테스트 MAE 0.502, 직전값 1.252), S5에 전처리 기록 표시 → S7 요구사항에 이력 8칸·열별 사용 피처 → 이후 기간 CSV 2,656건 예측·내려받기(이력 부족 32건 제외) → 학습 원본 재적용 6,900/6,900행 차이 0 → 390px. 회귀: S1 12, S2 13, S3 10, S4 10, P2 8, S7 10 PASS.
  - 검증 중 수정: S7 필요 이력을 처음에 'rolling 창+1'로 안내했으나 실제 제외 건수가 lag 8과 같아 확인해 보니 현재 제외 직전 w칸 = 이전 w칸 관측이므로 'w칸'이 맞음 → 문구와 테스트 수정.
  - 참고: 예제에서 세 피처를 추가한 실행의 테스트 MAE(0.502)는 lag만 쓴 실행(0.439)보다 높았다. 성능 우위는 완료 조건이 아니며 같은 조건 비교는 S6에서 다룬다.
- 사용자 PC 연결 폴더(Ubuntu 22.04.5, Python 3.10.12, pandas 2.3.3, xgboost 3.2.0): 반영 후 소스 전체 해시가 작업 공간과 일치. pytest **238 passed**(unit+S7 122, 기타 integration 107, 실제 worker 9). 화면 코드 변경이 없어 프런트엔드 빌드는 다시 하지 않음. Windows는 이번 변경분 미실행(`python -m pytest tests/unit/test_timeseries_pipeline.py tests/integration/test_inference.py`로 확인 가능).
- 다음 작업 한 가지 (P2): **XLSX 업로드** — 엑셀 파일을 CSV 변환 없이 등록하고 시트 선택 → 프로파일 → 목표·전처리·학습·S7까지 같은 경로로 연결.

### 2026-11-20 — P2/XLSX 등록·시트 선택·학습·새 엑셀 적용

- 범위: `P2_XLSX_BRIEF.md`. 엑셀 업로드 → 시트 선택 → 구조 확인 → 기존 시계열 학습 → S7 새 엑셀(시트 선택) 예측·CSV 내려받기. XLSX 결과 내려받기(엑셀 형식)·열 매핑 UI는 범위 밖.
- 설치(사용자 승인): PC 쪽 Linux 검증 환경에 openpyxl 3.1.5(작업 공간은 이미 3.1.5). Windows `.venv`는 건드리지 않음. `pyproject.toml`에 `xlsx` optional extra, `mlwb doctor`에 openpyxl 확인 추가.
- 반영 직전 확인: 수정 대상 28개 중 기존 파일은 모두 오전 반영본과 같은 상태(다른 작업의 변경 없음)를 파일별 checksum으로 확인.
- 변경 파일
  - 추가: `datasets/xlsx_table.py`(점검·시트 목록·정규화), `frontend/src/components/SheetPicker.tsx`, `tests/integration/test_xlsx.py`(20), `tests/integration/xlsx_samples.py`(테스트 워크북 생성), `tests/e2e/check_xlsx_browser.py`(8), `tests/e2e/make_xlsx_examples.py`
  - 수정: `core/contracts.py`(DatasetManifest `xlsx` 출처 정보), `app/services.py`(등록 분기·시트 선택·정규화 후 프로파일·SafeProfile), `app/inference_service.py`(S7 엑셀·시트 선택), `api/{app,schemas}.py`(`POST /api/datasets/{id}/sheet`, `POST /api/applications/{id}/sheet`, 시트 정보 응답), `datasets/safe_profile.py`(source_kind xlsx, 숨김 항목에 시트 이름·수식), `runner/worker.py`·`cli.py`(분석한 표 `local_path`를 읽고 승인 버전 해시와 대조), `frontend/src/{api,types,styles.css}`, `components/{UploadPanel,DatasetsPage,DatasetList,StructurePanel,ApplyPage}.tsx`, 기존 테스트(.xlsx 지원 반영, 승인 후 데이터 변경 → `DATASET_CHANGED`), `pyproject.toml`, `README.md`, `CONTRACTS.md` §11 "P2 XLSX 메모"
- 정책 요약(상세는 CONTRACTS): 원본 `source.xlsx` 보관 → 시트 목록(이름·표시 상태·대략 크기) → 사용자가 고른 시트만 규칙 `xlsx-table-v1`로 `table.csv` 정규화 → 기존 CSV 프로파일러. 데이터 버전 = 정규화 표의 내용 해시(원본 해시·시트·규칙은 따로 기록). 첫 값 있는 행 = 헤더, 완전 빈 행·열 제외(개수 기록), 서식만 있는 셀 무시, 빈·중복 헤더·병합 셀·Excel 오류 값·저장 결과 없는 수식은 위치와 고칠 방법을 알려 주고 실패. 날짜 셀은 1900/1904 기준을 라이브러리로 해석해 시간대 없는 시각 문자열, 숫자는 표시 형식이 아닌 저장값, 텍스트 ID 앞자리 0 보존. 수식은 실행하지 않고 저장된 결과 사용(화면에 안내). `.xls`·`.xlsm`·암호·매크로·압축 해제 크기 초과는 형식별 안내. 외부 링크 미사용.
- 시트 변경: 목표·설정(초안)이 없으면 같은 데이터셋의 시트를 바꾸고 다시 분석, 초안이 있으면 원본을 복사해 **새 데이터셋**으로 등록(기존 데이터셋의 시트·버전·목표·승인·실행 결과는 그대로). worker는 승인한 데이터 버전(표 해시)과 현재 표를 대조해 다르면 `DATASET_CHANGED`로 실행하지 않음.
- 테스트용 워크북(재현 기준): `tests/integration/xlsx_samples.py` — 고정 값·날짜, 난수 없음. `edge_workbook`은 시트별 경계 사례(한글 헤더·텍스트 ID '00123'·날짜·표시 형식 '0'인 20.5·저장된 수식 결과 / 결과 없는 수식 / #DIV/0! / 빈 헤더 / 중복 헤더 / 병합 A2:A3 / 빈 시트 / 숨김 시트 / 5000행에 서식만 있는 셀 / 헤더 위 빈 행 2·가운데 빈 행 1·빈 열 1), `epoch_1904_workbook`, `csv_to_xlsx`(CSV와 같은 내용, 날짜·숫자·텍스트 셀). 저장된 수식 결과는 저장 뒤 시트 XML에 `<v>` 값을 넣어 만든다. 브라우저용 예제는 `make_xlsx_examples.py`(학습 예제 → 시트 `2026_line1`, 이후 기간 → 시트 `2026_11`). 워크북 파일 바이트는 생성 시각 메타데이터 때문에 매번 다르지만, 정규화한 표의 해시는 작업 공간과 PC에서 같음(`7f12608b…`, `3c8145f1…`).
- 테스트(작업 공간, Python 3.13 · pandas 3.0.5 · openpyxl 3.1.5 · Chromium)
  - `python -m pytest -p no:cacheprovider` → **260 passed**. XLSX 통합 20: 시트 목록·선택 전 분석 안 함·재시작 후에도 대기, 날짜·텍스트 ID·저장된 수식 결과·표시 형식 무시, 표 해시 ≠ 원본 해시, 문제 시트 6종의 코드·셀 위치 안내와 같은 데이터셋에서 다른 시트로 재분석, 서식만 있는 끝 셀·빈 행·빈 열·숨김 시트, 1904 날짜 기준, .xls·.xlsm·암호(OLE)·손상·숨은 매크로·압축 해제 크기 초과, openpyxl 없음 → 설치 안내, 목표 저장 후 시트 변경 → 새 데이터셋(기존 목표 유지, 새 데이터셋은 목표부터), LLM 추천 요청에 워크북·시트 이름·헤더·셀 값·수식·경로 없음, **같은 내용 CSV와 XLSX: 열별 타입·결측 수·시간 범위 동일 → 같은 설정 학습의 분할·지표(차이 < 1e-9)·평가 예측 동일 → S7 새 엑셀(다른 시트 이름, 시트 선택)과 같은 내용 CSV 적용 결과 동일**, S7 점검 뒤 다른 시트 → 새 적용 기록.
  - 브라우저(실제 내장 Registry): **XLSX 8/8** — 업로드 → '시트 선택 필요' → 2개 중 2026_line1 → 11,520행 × 7열·시간열 timestamp, 같은 내용 CSV와 구조 일치 → S1 목표(15·30분) → S2 → S3 최근 30일·70/15/15·MAE·RMSE → S4 승인 → S5 완료(테스트 MAE 0.439) → 같은 설정 CSV 학습과 분할·지표·예측 6,900행 차이 0 → S7 새 엑셀 시트 2026_11 선택·점검·예측·CSV 내려받기 2,656건(같은 내용 CSV 적용과 동일) → 학습 후 '다른 시트'(메모) → 새 데이터셋(헤더만 있어 '데이터 행이 없습니다'), 원래 데이터셋 목표·시트·실행 유지 → 390px 시트 선택 화면. 회귀: S1 12, S2 13, S3 10, S4 10, P2 8, S7 10, 피처 6 PASS.
  - 검증 중 수정: 같은 데이터셋에서 시트를 바꾼 직후 이전 '분석 실패' 상태가 잠깐 보이던 문제(시트 선택 시 '등록됨'으로 되돌림), XLSX 구조 화면에 내부 표의 인코딩·구분자가 보이던 것(엑셀은 '형식: 엑셀 시트'로 표시).
- 사용자 PC 연결 폴더(Ubuntu 22.04.5, Python 3.10.12, pandas 2.3.3, xgboost 3.2.0, openpyxl 3.1.5): 반영 후 소스 전체 해시가 작업 공간과 일치. pytest **260 passed**(unit+S7+XLSX 142, 기타 integration 108, 실제 worker 10), `npx tsc -b` 통과, `npx vite build --emptyOutDir=false` 성공. `workspace/e2e-data/`에 XLSX 예제 생성. 브라우저 검증은 PC에서 미실행, Windows는 이번 변경분 미실행(`pip install -e ".[dev,timeseries,xlsx]"` 후 `python -m pytest tests/integration/test_xlsx.py`).
- 알려진 한계: 표 하나가 시트 맨 위 영역에 있다고 가정(시트 안 여러 표·피벗·차트 데이터 미지원). 시트 전체를 메모리로 읽어 수백만 셀 이상의 큰 시트는 느릴 수 있음(11,520행 × 7열 정규화 약 1.4초). 수식 결과의 최신성은 보장하지 않음(안내만). 결과 내려받기는 CSV만.
- 다음 작업 한 가지 (P2): **사용자 정의 전처리 플러그인 예제** — 예제 하나를 추가해 Registry·S2 화면·추천 후보·실행·S7이 같은 경로로 연결되는지 검증(이후 최소 대기열).

### 2026-11-20 — P2/사용자 정의 전처리 플러그인 예제(ts.custom.ewm)

- 범위: 사용자 지시(대화) "고고 그리고 검증도 일단 너가 해줘야돼". 별도 지시서가 없어 규칙 14에 따라 구현 전에 설계·대안·영향을 `docs/P2_PLUGIN_BRIEF.md`에 먼저 기록했다. 이번에는 독립 검토(GPT) 없이 Claude가 구현과 검증을 함께 했다 — 아래 '검증 방식' 참고.
- 반영 직전 확인: 수정 대상 기존 15개 파일을 PC에서 받아 작업 공간과 비교, 차이가 이번 수정분뿐임(다른 작업의 변경 없음)을 확인한 뒤 반영. 반영 후 25개 파일 sha256 일치.
- 변경 파일
  - 추가: `plugins/README.md`, `plugins/ts_custom_ewm/{plugin.json,plugin.py}`(예제), `src/mlworkbench/plugins/{user_loader,user_guard,sdk}.py`, `tests/unit/test_user_plugins.py`(43), `tests/integration/test_user_plugin_flow.py`(5), `tests/e2e/check_user_plugin_browser.py`(7), `docs/P2_PLUGIN_BRIEF.md`
  - 수정: `core/contracts.py`(PluginSpec `origin`·`code_sha256`, 상태 `invalid`, ApprovedSnapshot `plugin_code`), `core/registry.py`(`default_registry()`=내장+사용자, `load_problems`, `current_code_hash`, 카탈로그 버전에 코드 해시), `pipelines/timeseries.py`(사용자 플러그인 과거만 사용 검사 호출, pipeline.json·environment.json에 코드 해시, S7 불러오기 시 코드 대조, 입력 요구사항을 각 어댑터의 `history()`로 일반화·단계 이름은 Registry 표시 이름), `plugins/timeseries.py`(lag·rolling·차분 `history()`), `app/services.py`(승인 시 코드 해시 기록·불러온 뒤 바뀐 코드면 확정 거절, 제출 시 대조, 화면 승인 상태 무효 표시), `runner/{worker,manager}.py`(worker 시작 시 대조, 플러그인 위치를 절대경로로 전달), `app/inference_service.py`·`api/app.py`·`cli.py`(`default_registry`, doctor에 사용자 플러그인·불러오기 문제), `api/schemas.py`(StepView `origin`, PlanView `plugin_problems`), 프론트 `types.ts`·`PreprocessPage.tsx`(“사용자 정의” 배지·추가 목록 표시·불러오기 문제 안내·`invalid` 상태)·`ParamEditor.tsx`(스키마 `title`을 입력란 이름으로), `tests/conftest.py`(기본은 사용자 플러그인 없음 — 예제가 다른 테스트 카탈로그에 섞이지 않게), `README.md`, `CONTRACTS.md` §11 메모, 루트 `CLAUDE.md` 현재 작업 안내
- 정책 요약(상세는 CONTRACTS "P2 사용자 정의 플러그인 메모"): `<프로젝트>/plugins/<폴더>/plugin.json + .py`(또는 `MLWB_PLUGIN_DIRS`, 빈 값이면 끔). ID `ts.custom.*`, 시계열 전처리만. 명세를 먼저 검증하고 의존성이 없으면 코드를 불러오지 않음(`missing_dependency`). 명세 오류·ID 충돌은 등록하지 않고 폴더 이름·사유만 안내, 코드 불러오기 실패는 `invalid`(보이지만 실행 불가). 신뢰된 로컬 코드(샌드박스 아님). 실행 래퍼가 라벨 숨김·행/기존 열 변경 금지·새 피처 수치·train_only 상태 JSON 저장 가능·**과거만 사용 검사**(앞 50%·80% 시각까지만 잘라 다시 계산해 같은 값인지)를 매번 확인. 코드 해시를 승인·실행 산출물에 기록하고 제출·worker·S7에서 대조(`PLUGIN_CODE_CHANGED`).
- 예제 `ts.custom.ewm`: 같은 설비·세션 안에서 한 칸 민(현재 제외) 값의 지수 가중 평균, span개 이상 이전 관측이 있어야 값. 파라미터 spans(2~1000), columns(수치 열, 기본 타깃).
- 검증 방식(독립 검토 대신): ① 실패해야 하는 경우를 먼저 테스트로 만듦 — 미래 값 사용 3종(다음 값, 전체 평균, 가운데 정렬 rolling)이 실제로 `PLUGIN_FUTURE_LEAK`로 잡히는지, 출력 규칙 위반 10종, 명세 오류 14종·코드 오류 7종 ② 학습 평가 예측과 S7 재적용 결과를 행 단위로 조인해 같은 피처가 다시 만들어지는지(차이 0) ③ 수정 전 PC 파일과 diff를 떠서 의도하지 않은 변경이 없는지 ④ 기존 브라우저 점검을 예제 플러그인이 있는 실제 서버로 다시 실행해 카탈로그 추가가 기존 화면을 깨지 않는지.
- 테스트(작업 공간, Python 3.13 · pandas 3.0.5 · Chromium)
  - `python -m pytest -p no:cacheprovider` → **308 passed**(기존 260 + 단위 43 + 통합 5). 단위: 예제 발견·ready·카탈로그 버전 변화, 환경변수 끄기·여러 위치·기본 위치, 명세 오류 14종(내장 ID 공간 `ts.ewm`·`ts.lag`, model·vision, status·code_sha256 직접 기재, entry 형식·누락, fit_scope 누락, stage 10, locked, 버전 형식, 모르는 필드, 잘못된 스키마) → 등록 안 함·절대경로 없음, 깨진 JSON, 코드 오류 7종(import 예외 메시지의 경로 미노출, `sys.exit` 무해, transform 없음, 클래스 없음, 생성자 인자, 폴더 밖 entry, 다른 파일) → `invalid`, 의존성 없음 → 코드를 실행하지 않음, ID·버전 중복, 코드 수정 → 해시 변화 감지, EWM 값 수작업 대조·세션 경계·현재 값 미포함, 라벨 숨김, 미래 사용 3종 탐지, 출력 규칙 10종, 실행 오류·NaN 상태. 통합: S2 카탈로그 `origin=user`·LLM 읽기 전용 카탈로그 포함(민감정보 검사 통과), 예제를 쓴 제안 검증 통과·미등록 `ts.custom.future`·범위 밖 span 오류, 실제 worker 학습 → pipeline.json 피처·코드 해시·승인 스냅샷·train.log '과거만 사용 검사 통과', S7 요구사항(12칸·EWM 표시) → 재적용 예측 = 저장 평가 예측(차이 < 1e-9), 승인 뒤 코드 수정 → 제출 거절·화면 승인 무효·재시작 전 확정 거절 → 재시작 후 이전 모델 S7 점검 실패(`PLUGIN_CODE_CHANGED`) → 다시 확정하면 학습 성공, 플러그인 폴더 제거·망가진 폴더 → 카드 '등록되지 않은 도구'·확정 오류·S7 `PLUGIN_UNAVAILABLE`·안내에 절대경로 없음.
  - 브라우저(실제 Registry + 예제, 망가진 폴더 하나를 둘째 위치에 둠): **사용자 플러그인 7/7** — 불러오기 문제 안내(폴더 이름·사유) → 추가 목록 '지수 가중 평균(EWM) (사용자 정의) — 실행 가능' → 카드 배지·상세 'ts.custom.ewm@1.0.0 · 사용자 정의 플러그인' → span 1 입력 시 오류 표시 후 6, 24·대상 열 2개 → 저장 → S3 최근 30일 → S4 승인 → 학습 완료(테스트 MAE 0.421, 직전값 1.252) → S7 필요 이력 24칸·열별 'EWM span [6, 24](현재 제외)' → 이후 기간 2,592건 예측·내려받기 → 학습 원본 재적용 6,900/6,900행 차이 0 → 390px 넘침 없음 → 콘솔 오류 없음. 회귀(예제 플러그인이 있는 서버): S2 13(preview 서버), 피처 6, S7 10 PASS. `tsc -b`·`vite build` 통과, `mlwb doctor`·`mlwb plugins list`에 사용자 플러그인·불러오기 문제 표시 확인.
- 사용자 PC 연결 폴더(Ubuntu 22.04.5, Python 3.10.12, pandas 2.3.3): pytest **308 passed**(unit+S7+XLSX+플러그인 190, 기타 integration 118 — 시간 제한 때문에 두 번에 나눠 실행), `npx tsc -b`·`npx vite build --emptyOutDir=false` 성공, `mlwb doctor` → 사용자 정의 1개(실행 가능 1개). 브라우저 검증은 PC에서 미실행. **Windows는 이번 변경분 미실행**(확인 명령: `python -m pytest tests/unit/test_user_plugins.py tests/integration/test_user_plugin_flow.py`, `mlwb doctor`).
- 알려진 한계: 사용자 코드는 샌드박스 없이 앱·worker 프로세스에서 실행(신뢰된 로컬 코드 전제). 과거만 사용 검사는 두 지점에서 잘라 비교하는 표본 검사라 특정 시각에만 미래를 쓰는 코드는 놓칠 수 있다. 앱 실행 중 코드를 고치면 앱을 다시 시작해야 새 코드로 확정할 수 있다. 같은 ID의 여러 버전을 동시에 두면 S2 목록에 둘 다 보이고 화면 키가 겹칠 수 있다. 모델·비전 플러그인, 설치·업로드 UI는 범위 밖.
- 다음 작업 한 가지 (P2): **최소 대기열** — 동시 학습 1개 제한, 나머지는 QUEUED로 대기 후 순서대로 시작(취소·재시작 복구 포함).

### 2026-11-20 — Windows 앱 수동 확인(사용자) · 요청 기록 · 대기열 작업 중

- 사용자 확인(Windows 앱, 사용자 보고 — Claude가 직접 실행한 것 아님):
  - 사용자 정의 플러그인 예제(EWM)를 넣은 계획으로 S2 → 확정 → 학습 완료 → S7 다른 데이터(CSV) 예측까지 정상.
  - 엑셀(.xlsx)은 S1 등록·S7 적용 모두 `XLSX_DEPENDENCY_MISSING`(Windows `.venv`에 openpyxl 없음). 기능 오류가 아니라 설치 누락 — 안내 문구는 의도대로 표시됨.
  - 플러그인 확인 3번(코드 수정 → 제출 거절 · 재시작 후 S7 점검 실패 · 되돌리면 복구)은 아직 안 함.
- 사용자 요청(다음 작업 후보, 순서: 최소 대기열 → 아래 항목):
  1. **선택 패키지 자동 설치**: 앱 실행(cmd 실행기) 때 빠진 선택 의존성(openpyxl 등)을 자동 설치. 설계 시 확인할 것 — 규칙 15(설치 전 필요성·환경 확인, 숨은 장기 작업 금지): 설치 대상·버전 고정, 진행 표시, 네트워크 없음·회사 프록시 실패 시 안내, Windows `.venv`에만 설치.
  2. **데이터셋 삭제**: 사용자 결정 = 학습 실행이 있으면 확인 창에 '학습 실행 N개·모델·예측 결과도 함께 삭제'를 보여 주고 체크해야 삭제. 진행 중인 학습이 있으면 삭제 불가.
  3. **창 닫기 확인**: 앱 창 닫기(×) 시 '종료합니다' 확인, 진행 중·대기 중 학습이 있으면 중단된다는 안내(Alt+F4 등 창 밖 종료는 별도 확인).
  4. **모델 파라미터 기본값 표시**: S3 XGBoost 파라미터 빈칸 대신 기본값(트리 300 · 깊이 6 · 학습률 0.1) 표시·되돌리기.
  5. (발견한 버그) 업로드 실패한 엑셀 데이터셋이 목록에서 '업로드 실패'가 아니라 '시트 선택 필요'로 표시됨 — `needs_sheet`가 상태를 보지 않음.
  6. **실행 이름**: 학습 실행마다 사용자가 이름(메모)을 붙이고 수정할 수 있게. S5 목록이 run_id·데이터셋·모델만으로는 구분이 안 됨 — 목록에 이름과 함께 전처리 요약(예: lag·EWM 포함)·예측 시점도 보이면 좋음. S7 모델 선택에도 같은 이름 표시.
  7. **실행 삭제**: S5 목록·상세에서 실행 삭제(확인 후 모델·예측·로그 폴더 함께 삭제). 진행 중·대기 중 실행은 삭제 대신 취소. 이 모델로 만든 S7 적용 기록 처리 방식(함께 삭제/남김)은 설계 때 정함.
  8. **화면 레이아웃 고정**: 데이터셋을 바꾸면(예: 분석 실패 엑셀 ↔ 완료 CSV) 오른쪽 내용 길이에 따라 세로 스크롤바가 생기거나 사라져 박스 위치·폭이 흔들림. 스크롤바 자리를 항상 확보(`scrollbar-gutter: stable` 등)해 위치를 고정. (원인 추정 — 두 화면 캡처의 스크롤바 유무 차이, 수정 시 확인)
  9. **목표 저장 후 자동 이동**: S1 '목표 저장'을 누르면 '저장했습니다' 알림(잠깐 뜨는 팝업)을 보여 주고 전처리(S2)로 자동 이동. 지금은 저장 문구 옆 '전처리로 이동'을 한 번 더 눌러야 함. 같은 흐름이 S2→S3('저장하고 학습 설정으로')와 맞는지 함께 정리.
- 사용자 확인 추가(Windows, 사용자 보고): 플러그인 3번 — `plugin.py` 끝에 한 줄 추가 후 앱 재시작 → EWM으로 학습한 모델(run_0ac7594cc1a7)은 S7 모델 선택 단계에서 '학습 이후 사용자 플러그인 코드가 바뀌어 같은 피처를 만들 수 없습니다: ts.custom.ewm@1.0.0'으로 차단(파일 선택 비활성), EWM 없는 모델(run_4b414a25187b)은 정상. 되돌린 뒤(끝 줄바꿈까지 원본과 같게 — 처음엔 줄바꿈이 빠져 계속 차단됨, 해시 39ba77cd… 일치 확인) 재시작 → run_0ac7594cc1a7 입력 요구사항 정상(EWM span·필요 이력 표시). 플러그인 수동 확인 1·3번 완료, 2번(승인 후 코드 수정 → 제출 거절)은 자동 테스트로만 확인.
- 사용자 확인 추가(Windows, 사용자 보고): openpyxl 설치 후 엑셀 등록·시트 선택(motor_sensor_future.xlsx · 2026_11, 1,344행 × 7열)·구조 확인 정상.
- 위 1~9는 `REQUIREMENTS.md` §5 추가 요구사항 ADD-UX-01~09로 정의함(사용자 지시: 계획한 작업을 마친 뒤 한꺼번에 적용).

### 2026-11-20 — P2/최소 대기열

- 범위: 사용자 우선순위 ④, "대기열 먼저". 설계를 구현 전에 `docs/P2_QUEUE_BRIEF.md`에 기록.
- 반영 직전 확인: 수정 대상 중 이전 반영에 없던 `RunPages.tsx`·`test_runs.py`를 PC에서 받아 비교 — 차이는 이번 수정분뿐. 나머지는 직전 반영본 sha256과 일치. 반영 후 sha256 일치.
- 변경 파일: `runner/manager.py`(`dispatch`·`cancel_waiting`·`queue_state`·`max_parallel_runs`, 재시작 시 대기 유지), `api/app.py`(제출·주기 대조·앱 시작 때 dispatch, 대기 취소 즉시, RunView 대기 순번), `api/schemas.py`(`queue_position`·`queue_ahead`·`max_parallel`), `app/services.py`(실행 목록 정렬에 등록 순서 보조 키), 프론트 `types.ts`·`RunPages.tsx`(S5 '대기 중 · 앞에 N개' 안내·대기 취소 문구, 목록 'N번째'), 테스트 `test_queue.py`(4, 신규)·`test_runs.py`(재시작 테스트를 대기열 정책으로 수정), `tests/e2e/check_queue_browser.py`(5, 신규)
- 정책: 동시 학습 기본 1개(`MLWB_MAX_PARALLEL_RUNS` 1~4, 잘못된 값은 1). worker가 있는 미종료 실행 수가 자리보다 적을 때만 대기 실행을 제출 순서대로 시작. 대기 중 취소는 worker 없이 즉시 `CANCELED`('대기 중에 취소했습니다'). 앱 종료 시 실행 중 학습은 기존처럼 `APP_SHUTDOWN`, 대기 실행은 유지 → 다시 켜면 이어서 시작. 시작 도중 종료(잠금만 있음)는 `WORKER_NOT_STARTED`. 실행 전 검사(계획 해시·데이터 버전·플러그인 코드)는 worker에서 그대로.
- 정책 변경(회귀 테스트 수정): 이전에는 worker 없이 남은 `QUEUED` 실행을 재시작 때 `WORKER_NOT_STARTED`로 실패 처리 → 이제 대기열에 유지하고 이어서 시작. `test_app_shutdown_stops_worker_and_restart_recovers`가 두 경우(대기 → 재시작 후 완료, 잠금만 → 실패)를 모두 확인.
- 검증 중 발견·수정: 같은 초에 제출한 실행들이 `created_at`(초 단위) 동률에서 run_id(무작위) 순으로 정렬되어 **제출 순서가 바뀌는 버그**(PC에서 3회 중 2회 재현: 3번째가 2번째보다 먼저 시작). 실행 목록을 `created_at DESC, rowid DESC`로 정렬하고 대기열은 그 역순(= 등록 순)을 쓰도록 고침 → PC 3회 반복 통과. 작업 공간에서는 우연히 통과하던 문제라, 순서를 직접 확인하는 단언을 테스트에 추가.
- 테스트: 작업 공간 pytest **312 passed**(기존 308 + 대기열 4). 브라우저(실제 Registry) **대기열 5/5** — 학습 중 두 번째 제출 → S5 '대기 중 · 앞에 1개 실행'·'동시에 1개씩' → 실행 목록 '대기 1번째'·앞 실행 '실행 중' → 화면에서 대기 실행 취소 → 즉시 '취소됨'·'대기 중에 취소했습니다'(잠금 파일 없음) → 세 번째 제출 후 앞 실행 취소 → 자동 시작 → 완료(앞 종료 ≤ 시작) → 콘솔 오류 없음. PC(Ubuntu, Python 3.10): pytest **312 passed**(시간 제한으로 세 묶음 실행, 312개 수집), `tsc -b`·`vite build` 성공. Windows는 미실행(확인: 학습 중에 다른 확정 계획으로 학습 시작 → S5 대기 안내).
- 알려진 한계: 같은 작업 공간을 두 앱이 동시에 쓰는 경우는 지원하지 않음. 순서 바꾸기·우선순위·GPU 자원 감지 없음. 대기 중에는 진행률·예상 시작 시각을 보여 주지 않음.
- 다음 작업 한 가지 (계획 순서 ⑤): **S6 실험 비교** — 같은 데이터·분할·평가 기준의 실행을 나란히 비교하고 기준이 다르면 경고(REQUIREMENTS RESULT-01). 이후 추가 요구사항 ADD-UX-01~11.

### 2026-11-20 — S6 실험 비교(시계열)

- 범위: 사용자 우선순위 ⑤ "일단은 진행해보자". 설계를 구현 전에 `docs/P2_COMPARE_BRIEF.md`에 기록(근거 RESULT-01, SCREEN_DESIGN §9, ARCHITECTURE §8). 실행 이름·삭제(ADD-UX-06·07), 설정 복제해 재실험, 비전 지표, MCP `experiment.compare`는 범위 밖.
- 반영 직전 확인: 직전 반영본 sha256과 PC 파일 일치, 이전 반영에 없던 `api.ts`·`styles.css`는 PC에서 받아 비교(차이는 이번 수정분뿐). 반영 후 sha256 일치.
- 변경 파일: 추가 `app/compare.py`(평가 조건·조건 차이·그래프 자료·캐시), `frontend/src/components/ComparePanel.tsx`, `tests/integration/test_compare.py`(5), `tests/e2e/check_compare_browser.py`(7). 수정 `api/app.py`(`GET /api/runs/compare`, `GET /api/runs/compare/series`, RunView `steps`·`duration_s`·`protocol_key`), `api/schemas.py`, 프론트 `types.ts`·`api.ts`·`RunPages.tsx`(목록 = 비교 표)·`styles.css`.
- 정책: **평가 조건**(데이터 버전·타깃·시간열·설비 열·예측 시점·분할 비율·gap·지표·기준 예측·**실제 테스트 표본 집합**(예측 파일의 테스트 행 (설비, 기준 시각) 해시, 예측 시점별 표본 수))이 기준(처음 고른 실행)과 다르면 ⚠ 경고하고 최고값 표시에서 뺀다. 예측 시점별 행은 그 시점의 표본 집합이 같으면 비교. **조건 차이**(전처리·모델·파라미터·학습량·플러그인 코드)는 다른 항목만 표로. 지표는 저장된 metrics.json 그대로(다시 계산하지 않음), 비교 표는 유효숫자 4자리. 그래프는 테스트 구간에서 선택한 실행 모두에 있는 표본만, 최대 600점 균등 추출, SVG(외부 라이브러리 없음), 색은 선택 순서 고정 6색(dataviz 검증 스크립트 통과, 대비 경고 → 범례·표 함께 표시). 평가 조건은 실행 폴더 `compare_key.json`에 캐시(입력 파일 크기·시각이 바뀌면 다시 계산).
- 화면: 실행·결과 목록에 체크(완료만, 최대 6) · 데이터셋 필터 · '기준과 같은 평가 조건만' · 전처리·예측 시점·소요 열 · '기준'·'⚠ 평가 조건 다름' 표시. 2개 이상 고르면 아래에 비교 패널(실행별 상세·새 데이터 적용 링크, 경고, 조건 차이, 테스트 지표 전체·예측 시점별·설비별, 실제 vs 예측 그래프와 마우스 값).
- 테스트: 작업 공간 pytest **317 passed**(312 + 비교 5: 목록 키·전처리·소요, 학습량·전처리만 다른 실행 = 같은 평가 조건·조건 차이 2개·저장 지표 일치, 분할 다른 실행 = 분할·테스트 표본·표본 수 경고·조건 차이엔 없음, 그래프 자료 = 저장 예측과 같은 값·표본 수, 오류·캐시). 브라우저(실제 Registry, 합성 CSV로 학습 3개) **비교 7/7** — 준비 → 체크 2개 → 비교 패널·기준 → 조건 차이 '학습량'만·지표 3행(최저 MAE 굵게 = 저장 지표)·그래프 선 3개·마우스 값·설비 바꾸기·설비별 5행 → 분할 다른 실행 '⚠ 평가 조건 다름'·경고(분할·테스트 표본)·최고값 제외 → '같은 평가 조건만' 2개 → 390px → 콘솔 오류 없음. 회귀: P2 8, 대기열 5 PASS(목록 표 변경 영향 확인). PC(Ubuntu, Python 3.10): pytest **317 passed**(179 + 133 + 5), `tsc -b`·`vite build` 성공. Windows는 미실행.
- 검증 중 수정: 그래프 마우스 값이 화면 밖 좌표에서 안 뜨는 점검 순서 보완, 비교 표 3자리 반올림으로 근소한 차이가 같아 보여 유효숫자 4자리로, 경고에 표본 해시 대신 '테스트 표본(…)이 다름', 목록 표 열 줄바꿈(모델·상태가 글자 단위로 끊김) 정리.
- 알려진 한계: 비교는 완료 실행만, 최대 6개. 평가 조건 판단은 저장 파일 기준(오래된 실행 폴더를 손으로 고치면 캐시가 다시 계산됨). 기준은 처음 고른 실행으로 고정(바꾸려면 선택 해제 후 다시). 실행 이름이 없어 run_id로 구분(ADD-UX-06에서 해결 예정).
- 다음 작업 한 가지: 계획한 P2 작업이 끝났으므로 **추가 요구사항 ADD-UX-01~11** 적용(사용자 지시: 계획을 마친 뒤 한꺼번에).

### 2026-11-20 — 추가 요구사항 1차(ADD-UX-03·04·05·08·09·10·11)

- 범위: 사용자 지시 "수정 먼저 일단 하고 다음 계획 생각하자". 설계를 구현 전에 `docs/ADD_UX_BRIEF.md`에 기록. 세 묶음 중 1차(화면만 고치는 것).
- 반영 직전 확인: 대상 기존 23개 파일을 PC에서 받아 비교 — 차이는 이번 수정분뿐. 반영 후 28개 sha256 일치.
- 변경
  - 05 엑셀 상태 버그: `needs_sheet`를 등록(REGISTERED) 상태로 한정(`services.py`·`schemas.py`) → 업로드 실패한 엑셀은 '업로드 실패'.
  - 08 레이아웃: `html { scrollbar-gutter: stable }`. 원인 확인: 헤드리스 Chromium에서 스크롤바를 실제로 보이게 하고 재현 — 수정 전 오른쪽 박스 폭 801(짧은 화면) ↔ 790(스크롤 생김), 수정 후 790 ↔ 790.
  - 09 목표 저장 → 오류가 없으면 알림 '목표를 저장했습니다 — 전처리로 이동합니다' + 전처리로 자동 이동(오류가 남으면 그 자리). 앱 공통 알림(`toast.tsx`).
  - 10 뒤로가기: 전처리 '← 데이터셋·목표', 학습 설정 '← 전처리', 확인·실행 '← 학습 설정', 실행 상세 '← 실행·결과 목록', 새 데이터 적용 '← 실행 상세/실행·결과'.
  - 11 시간 입력: 시간 간격 파라미터(세션 분리·리샘플·결측 최대 간격 등)와 S1 예측 구간 직접 입력을 숫자 + 단위(초·분·시간·일, 기본 분)로(`DurationInput.tsx`). 저장 값 형식(예: 60min) 그대로.
  - 04 모델 파라미터 기본값: XGBoost 스키마에 `default`(300·6·0.1 = 어댑터 기본값, `test_model_defaults.py`로 고정). 빈칸은 '기본값 N' 표시·'비워 두면 기본값 N', 값을 바꾸면 '기본값(N)으로' 버튼.
  - 03 창 닫기(×): '앱을 종료합니다' 확인 창(실행 중 학습은 중단, 대기 학습은 다음에 이어서 시작 안내) → [종료]/[취소]. Alt+F4 등 창 밖 종료는 범위 밖.
- 테스트: 작업 공간 pytest **318 passed**(+ 기본값 고정 1, 엑셀 업로드 실패 시 needs_sheet=False 단언 추가). 브라우저 **UX 8/8**(`check_ux_browser.py`: 08 스크롤바 실제 표시 조건에서 박스 위치·폭 동일, 09·11 예측 구간 2+시간→2h·알림·자동 이동, 11 세션 간격 30min 표시→1+시간→1h 저장, 10 뒤로가기 링크 경로, 04 기본값 안내·되돌리기, 03 닫기 확인 취소/종료, 390px, 콘솔 오류 없음). 회귀(목표 저장 후 자동 이동·뒤로가기 링크 이름 중복·닫기 확인에 맞춰 기존 점검 스크립트 9개 수정) — S1 12, S2 13, S3 10, S4 10, P2 8, 피처 6, 사용자 플러그인 6, XLSX 8, S7 10, 대기열 5, 비교 7 모두 PASS. PC(Ubuntu, Python 3.10): pytest **318 passed**(148 + 37 + 133), `tsc -b`·`vite build` 성공. Windows는 미실행 — 특히 08(앱 창 스크롤바)·03(실제 창 닫힘)은 Windows 앱에서 확인 필요.
- 알려진 한계: 확인·실행·학습 설정 화면에는 아래쪽 '← 학습 설정/← 전처리' 버튼이 이미 있어 위쪽 뒤로가기 링크와 이름이 같음(둘 다 같은 곳으로 이동). 알림은 3초 뒤 사라짐.
- 다음 작업 한 가지: **추가 요구사항 2차** — 06 실행 이름, 07 실행 삭제, 02 데이터셋 삭제(시작 전에 '실행 삭제 시 그 모델로 만든 새 데이터 적용 기록 처리'를 사용자에게 확인).

### 2026-11-20 — 추가 요구사항 2차(ADD-UX-06 실행 이름, 07 실행 삭제, 02 데이터셋 삭제)

- 범위: 사용자 지시 "다음 개발을 하자 ㄱㄱ 그거 적용해줘". 사용자 결정(질문 1회): 실행 삭제 시 그 모델로 만든 새 데이터 적용 기록도 함께 삭제(확인 체크), 데이터셋 삭제도 같은 규칙. 설계는 `ADD_UX_BRIEF.md` 2차 절.
- 반영 직전 확인: 이번 대상 중 직전 반영본(sync_ux1·sync_cmp·sync_plugin)과 PC 파일 sha256 일치, 이전에 반영하지 않은 `StructurePanel.tsx`·`DatasetsPage.tsx`는 PC에서 받아 비교(차이 없음). 반영 후 17개 sha256 일치.
- 변경 파일: 추가 `app/deletion.py`, `components/DeleteDialog.tsx`, `tests/integration/test_delete.py`(5), `tests/e2e/check_delete_browser.py`(7). 수정 `core/contracts.py`(RunRecord `name`), `app/services.py`(`rename`), `api/app.py`(`PATCH /api/runs/{id}`, `GET/DELETE` 실행·데이터셋 삭제 미리보기·삭제, 비교·RunView에 이름), `api/schemas.py`, `app/inference_service.py`(모델 목록 이름), 프론트 `api.ts`·`types.ts`·`styles.css`·`RunPages.tsx`(목록 이름·삭제 열, 상세 이름 붙이기·삭제…)·`ComparePanel.tsx`(이름 표시)·`ApplyPage.tsx`(모델 목록 이름)·`StructurePanel.tsx`·`DatasetsPage.tsx`(데이터셋 삭제…).
- 정책 요약: 끝난 실행만 삭제, 진행·대기 중 학습·업로드·분석 중·예측 중이면 막음. 함께 지워질 것(적용 기록·실행)이 있으면 확인 체크 전엔 삭제 버튼 비활성. 기록을 먼저 지우고 파일을 지움(못 지운 폴더는 알림). 실행 삭제 시 승인·확정 스냅샷은 남겨 같은 설정으로 다시 확정 가능, 데이터셋 삭제는 모두 지움. `workspace/deletions.log`에 개수만.
- 검증 중 수정: 완료된 데이터셋 화면은 별도 머리글을 써서 '데이터셋 삭제…' 버튼이 빠졌던 것, 실행 목록에 숨김 글자 열('관리')을 추가하자 390px에서 페이지 가로 넘침(표 밖으로 위치) → `.table-wrap { position: relative }`.
- 테스트: 작업 공간 pytest **323 passed**(318 + 삭제·이름 5: 이름 정리·제어 문자·60자·비교·모델 목록, 적용 기록 있는 실행 삭제(확인 없음·체크 없음 거절 → 삭제 후 폴더·기록 없음·로그·다시 확정·학습 가능), 학습 중 실행·데이터셋 삭제 거절 → 취소 후 삭제, 실행 있는 데이터셋 삭제(관련 표·폴더 모두 없음, 다른 데이터셋 그대로), 실행 없는 데이터셋 삭제). 브라우저 **삭제·이름 7/7** — 이름 붙이기(상세·목록·비교·S7 표시, 잘못된 이름 안내) → 목록에서 실행 삭제(체크 전 비활성·적용 기록 함께) → 상세 삭제…(체크 없음·취소) → 데이터셋 삭제(실행 함께 체크 → 목록에서 사라짐, 다른 데이터셋·실행 유지) → 390px → 콘솔 오류 없음. 회귀: S1 12, XLSX 8, S7 10, 대기열 5, P2 8, 비교 7, UX 8 PASS(UX는 7개 점검을 동시에 돌린 부하에서 5초 대기 초과로 1회 실패 → 단독 재실행 통과). PC(Ubuntu, Python 3.10): pytest **323 passed**(153 + 37 + 133), `tsc -b`·`vite build` 성공. Windows는 미실행(특히 Windows 파일 잠금 시 삭제 동작).
- 추가(사용자 확인 '이름 뭐 없는데'): 확인·실행 화면에 '실행 이름 (선택)' 입력 → 학습 시작 요청 `name`(잘못된 이름이면 실행을 만들기 전에 거절, 같은 승인 재요청은 이름 유지). 비우면 이름 없음(run_id로 표시), 나중에 실행 상세에서 붙이거나 바꿈. 테스트: 통합 +1(324), 브라우저 삭제·이름 8/8, S4 10·P2 8 회귀 PASS, PC 반영 후 unit+삭제 154 passed·빌드 성공.
- 알려진 한계: 휴지통·되돌리기 없음(확인 창과 로그로 대신). 삭제는 한 번에 하나씩. 다른 앱 인스턴스가 같은 작업 공간을 쓰는 경우는 지원하지 않음.
- 다음 작업 한 가지: **추가 요구사항 3차 — ADD-UX-01 실행기 선택 패키지 자동 설치**(Windows 실행기·네트워크가 걸려 있어 사용자 Windows 확인 필요).

### 2026-11-20 — P3 비전 V1: 이미지 ZIP·폴더 등록, 구조 분석, 폴더 역할·보정·비전 목표

- 범위: 사용자 선택 "비전 MVP 시작 (P1 이미지 → P3)". 설계를 구현 전에 `docs/P3_VISION_BRIEF.md`에 기록(V1~V4 단계, V1 설계 표). 지표(V2)·PatchCore 학습(V3, torch 설치 승인 필요)·S5/S6/S7 비전(V4)은 범위 밖.
- 구현: ZIP 업로드(`source.zip` 보관, 이미지 파일만 풀기 — 경로 탈출·링크·암호·압축 폭탄·파일 수·총 용량 차단) / 이미지 폴더 제자리 등록(복사 없음, 상대 경로·드라이브 최상위·작업 공간 거절, 데이터셋 삭제 시 사용자 폴더는 그대로) / 의존성 없는 헤더 판독(PNG·JPEG·BMP·TIFF 크기·채널), 표본 200장 구조 검사(Pillow 있으면 `verify()`) / 폴더 이름으로 역할 추정(정상 학습·보정 정상·정상 평가·불량 평가·GT 마스크·제외), 불량-마스크 짝 수 / 비전 목표(`VisionGoal.roles`·`calibration` 폴더 또는 학습에서 떼어 냄 5~50%) 검증·저장 / SafeProfile은 개수·형식·크기만(폴더·파일 이름·경로 없음) / 미리보기 API(`no-store`, 20MB 초과·TIFF 거절, 경로 탈출 404).
- 화면(S1-V): 요약(장수·GT·형식·크기·손상·짝·건너뛴 파일), 안내(정상 학습 없음·손상·크기 혼재·마스크 일부/없음 → AU-PRO N/A), 폴더 표(역할 선택, 바꾼 행 강조), 예시 정상·불량(다른 예시 보기), 태스크(정상 기반 이상탐지, 세그멘테이션은 준비 중), 보정 방식, 역할별 장수 즉시 계산, 목표 저장. 업로드 칸에 '이미지 폴더 등록'.
- 검증 중 수정: 데이터셋을 바꾼 직후 첫 렌더가 이전 데이터셋 예시 경로로 새 id 이미지를 요청해 404 3건 → 프로필 소속 확인 + 데이터셋별 컴포넌트 재생성. 폴더 경로 입력 오류가 409(상태 충돌)로 나가던 것 → 400. ZIP에서 건너뛴 파일 수가 요약에 빠지던 것. 업로드 버튼 문구 '파일 선택 (CSV·엑셀·ZIP)'(S1 점검 기대값 수정, 새 버튼으로 Tab 순서 한 칸 추가).
- 테스트: 작업 공간 pytest **344 passed**(323 + 단위 12 + 통합 8 + 기존 1 조정): 헤더·역할 추정·ZIP 보안 7종·손상·형식, ZIP 프로필·미리보기 안전·폴더 제자리(삭제 시 폴더 유지)·비전 목표 오류/경고·SafeProfile에 이름 없음·다른 종류로 재시도 거절. 브라우저 **비전 6/6**(`check_vision_browser.py`): ZIP → 39장·GT 7·짝 7/7·역할 추정·예시 2장 로드 → 역할 수정·보정 → 저장·새로고침 복원 → 정상 학습 0장 오류 → 폴더 등록 22장·GT 없음 안내·절대경로 미표시·상대 경로 오류 → 390px → 의도한 오류 응답 2건 외 4xx/5xx·콘솔 오류 없음. 회귀 S1·XLSX·삭제 PASS. PC(Ubuntu, Python 3.10): 반영 전 대상 13개 PC본과 비교(우리 변경 외 차이 없음), 반영 후 20개 sha256 일치, pytest **343 passed + 1 skipped**(Pillow 없음 → 구조 검사로 대체, 159+20+110+54), `tsc -b`·`vite build` 성공. Windows는 미실행.
- 알려진 한계: 공개 데이터셋(MVTec/VisA) 미검증 — 합성 이미지만. 손상 검사는 표본 200장(Pillow 없으면 구조 검사만). TIFF는 미리보기 불가. 폴더 등록은 앱과 같은 PC의 경로만. 등록 후 폴더 내용이 바뀌면 다시 분석해야 버전이 바뀜. 전처리·학습 화면은 아직 시계열 전용.
- 다음 작업 한 가지: **비전 V2 — 이상탐지 지표(image AUROC·NG recall·정상 FPR, AU-PRO@0.05/0.30 픽셀 FPR 적분·GT 없으면 N/A)를 합성 정답과 비교하는 테스트로 구현**.

### 2026-11-20 — P3 비전 V2: 이상탐지 지표(학습·화면 변경 없음)

- 범위: 사용자 지시 "비전 V2만. 기존 평가 기능 재사용, image AUROC·NG recall·정상 이미지 오판율·AU-PRO@0.05/0.30, 임계값은 정상 보정 데이터로만, 계산 단위·집계 문서화, 계산 불가는 이유와 함께 N/A, 합성 정답으로 검증". 설계는 구현 전에 `P3_VISION_BRIEF.md` 'V2 설계'에 기록(지표별 계산 단위·집계·N/A 코드 표).
- 구현: `pipelines/vision_metrics.py`(모델과 무관한 순수 함수, 입력 = 점수·맵·마스크 배열). 재사용: 계약 `VisionAnomalyEvaluation`·`ThresholdRule`, `metrics.py`의 `_clean`·계산 불가 = null·`metrics_meta` 형식. 임계값 `decide_threshold`는 보정 점수만 받음(테스트 인자 없음), 판정 = 점수 > 임계값(`predict_defect` 한 곳에서 정의). 보정·테스트 id 겹침·id 중복·맵/마스크 크기 불일치는 입력 오류. AU-PRO = 모든 테스트 이미지 픽셀로 정확 곡선(고유 맵 값 격자), GT 연결 영역 8-이웃 평균, 상한에서 선형 보간·상한으로 정규화. N/A 코드: empty_input·single_class·no_defect_images·no_normal_images·no_calibration_scores·threshold_unavailable·non_finite_score·missing_gt_masks(일부 누락도 일부만으로 계산하지 않음)·missing_anomaly_maps·no_defect_regions·no_normal_pixels. 새 의존성 없음(scipy 있으면 라벨링에 사용, 없으면 순수 Python).
- 테스트: 단위 31(손 계산 AUROC·완전/역전/동점·scikit-learn 대조, 임계값 3규칙·경계값·테스트 불변·보정 미포함, AU-PRO 손 계산 곡선·완전 1·역전 0·상수 = 상한/2·영역 평균·8-이웃·느린 참조 구현과 무작위 25회 대조·scipy/Python 라벨 일치, 이미지 FPR 0이지만 픽셀 FPR > 0, 합성 데이터 끝까지, N/A 전 사유). 변이 검사 9종 중 8종 검출 → 미검출 1종(판정 규칙 중복 정의)을 한 곳으로 합친 뒤 검출. scipy를 막은 상태 29 passed + 2 skipped. 작업 공간 전체 pytest **375 passed**. PC 반영 3개 파일 sha256 일치. **PC pytest·Windows는 미실행**(토큰 부족으로 중단).
- 알려진 한계: 마스크·맵 파일 읽기와 크기 맞춤은 V3. S3·S5·S6 연결 없음. 큰 마스크에서 순수 Python 라벨링은 느릴 수 있음(scipy 있으면 해당 없음).
- 다음 작업 한 가지: **PC에서 `pytest tests/unit/test_vision_metrics.py` 실행 확인** 후 V3 PatchCore(torch 설치 승인 필요).

## 전처리 확장(ADD)

### 2026-11-19 — ADD-1 현황 파악 및 ADD-2 최소 Registry/Adapter

- [x] **ADD-1 현황 파악.** `datasets/csv_profile.py`·`safe_profile.py`에 로컬 CSV 프로파일과 전송용 별칭 요약이 있다. `core/registry.py`·`plugins/builtin.py`는 ID·버전 기반 카탈로그를 제공하고, `core/validation.py`·`schema_check.py`는 파라미터·순서를 검증한다. `app/recommend_service.py`는 실제 추천과 로컬 Mock을 구분하며, `mcp/catalog.py`는 같은 Registry와 검증 경로를 노출한다. React S2는 전처리 편집과 초안 검증을, S3는 학습 설정 저장을 지원한다. `JobService`는 승인 후 작업 등록 및 상태·이벤트를 관리한다. 실제 전처리 실행기와 전·후 미리보기는 조사 시점에 구현되어 있지 않았다.
- **통합 계획과 충돌 위험.** 기존 Registry의 ID·버전 공간과 검증기를 사용한다. 기존 내장 명세와 테스트용 ready 명세의 의미는 유지한다. 새 어댑터 등록 경로에서만 구현체와 의존성을 점검하고, 기존 ID를 실제 구현으로 전환할 때는 같은 ID·버전의 명세 등록을 어댑터 등록으로 교체한다. ADD-2에서는 추천·MCP 출력 형식과 웹 API, 학습 작업 흐름을 유지한다.
- [x] **ADD-2 최소 Registry/Adapter.** `plugins/preprocessor.py`에 `validate`·`fit`·`transform`·`preview` 계약을 정의했다. 기존 `PluginSpec`을 라이브러리 출처, fit 범위, 학습 전용·마스크 지원, 경고, 비용 힌트로 확장했다. 같은 Registry에 신뢰된 로컬 구현체를 명시적으로 등록하고 정확한 버전으로 조회한다. 준비 상태로 선언한 구현체의 최상위 Python 모듈은 import 없이 탐색하고, 누락되면 `missing_dependency`로 표시하여 ready 조회와 구현체 조회에서 제외한다. 등록 시 파라미터 스키마를 확인하고, 기존 최종 검증이 일반 파라미터 검사에 통과한 어댑터의 `validate`를 호출한다. 등록·검증에서 `fit`, `transform`, `preview`는 호출하지 않는다.
- **변경 파일:** `src/mlworkbench/core/{contracts,registry,schema_check,validation}.py`, `src/mlworkbench/plugins/preprocessor.py`, `tests/unit/test_preprocessor_adapter.py`.
- **테스트:** Windows Python 3.13.17에서 어댑터·Registry·계획 검증 단위 테스트 **30개 통과**. 초기 전체 실행은 자식 서버의 테스트 의존성 누락으로 기존 런처 테스트 3개가 실패했고, 가상환경 재시도에서는 해당 검사에서 준비 대기가 만료됐다. 최종적으로 기존 의존성과 동일 소스를 격리된 사본에 배치하고 기본 Windows Python에서 `python -B -m pytest -p no:cacheprovider --basetemp=run`을 실행해 **165개 통과**(42.77초, Starlette 사용 중단 예정 경고 1건). ADD 변경 파일 6개의 해시가 검증 사본과 일치함을 확인했다.
- **범위:** 라이브러리 전처리 구현, 새 추천/MCP 도구, 편집 UI 변경, 실제 미리보기·학습, 플러그인 자동 탐색은 후속 ADD 항목에 해당한다. 외부 다운로드와 패키지 설치는 이번 작업에서 하지 않았다.
