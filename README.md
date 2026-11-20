# ML Workbench — LLM + MCP 설계 패키지 (v0.2)

**상태:** P0와 CSV 기반 S1~S4 화면 구현 완료. 다음 구현은 P2 실제 전처리·시계열 학습이다. P1의 XLSX·이미지·외부 공급자 등 남은 항목은 `docs/PROGRESS.md`에서 관리한다.  
**대상:** 개인용 시계열·비전 AI 실험 도구 / Claude Code로 단계별 구현.  
**업데이트:** 2026-11-19

## 이번 개정의 핵심

기존 설계의 `데이터 → 수동 전처리/모델 선택 → 학습`을 다음 흐름으로 확장한다.

**데이터 업로드 → 구조/통계 분석 → LLM이 MCP 도구 목록을 참고해 전처리·모델 추천 → 사용자가 편집·승인 → 검증된 학습 엔진 실행 → 지표/실험 기록 → (선택) LLM이 다음 실험 추천**

- LLM 교체: **외부 API → Ollama/vLLM 로컬 API** 전환을 UI 설정으로 처리한다. `ProviderAdapter`와 기능 감지 계층을 공유하고, 학습 엔진 및 MCP 서버는 변경하지 않는다.
- 추천은 LLM이 하되 **실행 권한과 검증은 프로그램**이 관리한다. MCP 도구의 정의/스키마는 플러그인 레지스트리에서 만들어진다.
- 민감한 회사 데이터는 승인 없이 외부 LLM으로 보내지 않는다. 기본은 **구조·집계 통계만 전송**. 로컬 모델도 동일한 권한 정책을 따른다.
- 시계열과 비전은 개별 파이프라인을 사용하고, 데이터 관리·실험 실행·LLM 추천·MCP 카탈로그·결과 기록은 공통이다.

## 파일 안내

| 문서 | 용도 |
|---|---|
| [아키텍처](docs/ARCHITECTURE.md) | 전체 구조, 데이터 흐름, 공급자 교체, 플러그인·학습 설계 |
| [계약](docs/CONTRACTS.md) | LLM 추천 JSON, Provider/MCP/플러그인 인터페이스, 안전한 실행 규칙 |
| [진행 기록](docs/PROGRESS.md) | P0~P6 단계별 체크리스트/테스트/진행 상태 |
| [요구사항](docs/REQUIREMENTS.md) | 한글 워크벤치 화면 요구사항 |
| [화면 설계](docs/SCREEN_DESIGN.md) | 화면 흐름, 상태별 동작, 와이어프레임 |
| [P2 현재 구현 지시](docs/P2_XLSX_BRIEF.md) | XLSX 업로드·시트 선택·기존 학습과 S7 적용 |
| [P2 새 CSV 지시](docs/P2_INFERENCE_BRIEF.md) | 저장 모델로 새 CSV 입력 점검·예측·다운로드 |
| [P2 첫 학습 지시](docs/P2_TIMESERIES_BRIEF.md) | 실제 전처리·분할·XGBoost 학습·평가·저장 |
| [Claude 작업 규칙](docs/CLAUDE.md) | 작업 규칙과 현재 구현 범위 |
| [P0 구현 지시](docs/IMPLEMENTATION_BRIEF.md) | 프로젝트 골격 구현 범위와 검수 기준 |
| [P1 CSV 지시](docs/P1_CSV_BRIEF.md) | CSV 등록·프로파일링 |
| [P1 React 지시](docs/P1_REACT_BRIEF.md) | 데이터셋 첫 화면과 FastAPI 연결 |
| [P1 전처리 지시](docs/P1_PREPROCESS_BRIEF.md) | 전처리 화면과 오프라인 Mock 추천 |
| [P1 학습 설정 지시](docs/P1_TRAIN_SETTINGS_BRIEF.md) | 시계열 학습 설정과 초안 저장 |
| [P1 확인·승인 지시](docs/P1_CONFIRMATION_BRIEF.md) | 설정 요약과 최종 검증·승인 |
| `examples/providers.yaml` | 외부 API ↔ 로컬 LLM 설정 예시 (비밀키는 환경변수) |
| `examples/timeseries_plan.yaml` | 시계열 학습 계획 예시 |
| `examples/vision_plan.yaml` | 비전 이상탐지 학습 계획 예시 |

## 설치·실행 (P0)

```bash
python -m venv .venv            # Windows: py -3.10 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

mlwb doctor                     # 환경·workspace/·레지스트리·Provider 설정 점검
mlwb plugins list               # 플러그인 카탈로그 (P0는 모두 '미구현')
mlwb providers list             # LLM 공급자 (기본 활성 = Mock)
mlwb plan validate examples/timeseries_plan.yaml --mode final --dataset tests/fixtures/ts_manifest.yaml
python -m pytest
```

- 설정: `configs/providers.yaml`(기본 Mock). 키 값은 `.env`/환경변수에만 둔다(`.env.example` 참고).
- 워크스페이스: 기본 `./workspace`, 바꾸려면 `--workspace` 또는 `MLWB_WORKSPACE`.
- `plan validate --assume-ready`는 미구현 플러그인을 실행 가능으로 가정하는 **구조 검증 전용** 옵션이다.

## 웹 화면 실행 (P1: 데이터셋·전처리 화면)

설치 옵션(`pyproject.toml`에 선언, 각각 새 가상환경에서 설치·실행 확인):

| 옵션 | 포함 | 용도 |
|---|---|---|
| `pip install -e ".[web]"` | 기본 + FastAPI·uvicorn | `mlwb serve`로 화면 사용 |
| `pip install -e ".[dev]"` | 기본 + FastAPI·uvicorn + pytest·httpx | 개발·전체 테스트(`python -m pytest`) |
| `pip install -e ".[timeseries]"` | pandas·numpy·scikit-learn·xgboost | 시계열 실제 학습(P2). 없으면 해당 플러그인이 '의존성 없음'으로 표시되고 학습 테스트는 건너뜀 |
| `pip install -e ".[xlsx]"` | openpyxl | 엑셀(.xlsx) 등록·새 데이터 적용. 없으면 엑셀 업로드 시 설치 안내 |

```bash
pip install -e ".[dev,timeseries,xlsx]"   # 개발용 + 시계열 학습 + 엑셀 (화면만 쓰려면 ".[web,timeseries,xlsx]")
cd frontend && npm install && npm run build && cd ..   # Node 18 이상
mlwb serve                       # http://127.0.0.1:8000 (로컬 전용, 다른 주소로는 실행 거부)
```

- 화면 개발 중에는 터미널 두 개를 쓴다: `mlwb serve`(API, 8000) + `cd frontend && npm run dev`(화면, http://127.0.0.1:5173, `/api`는 8000으로 전달).
- 데이터셋 목표 저장 후 전처리·학습 설정·확인·승인 화면으로 진행한다. 시계열 의존성이 설치되어 있으면 XGBoost CPU 학습을 실행하고 결과를 저장한다. 완료 모델은 새 데이터 적용 화면에서 새 CSV를 예측하고 결과를 내려받을 수 있다.
- 전처리 화면은 오프라인 Mock 추천(원래 열 이름 대신 `column_N` 별칭으로 요청)을 받아 항목 체크·순서·파라미터를 편집해 저장한다. 구현 및 의존성이 준비된 전처리를 실행하고, 구현 전인 항목은 "계획 미리보기 · 실행 불가"로 표시한다.
- CSV 분석은 별도 Python 프로세스에서 실행되므로 큰 파일을 분석하는 동안에도 목록·상태를 계속 볼 수 있다. 앱이 업로드·분석 도중 종료되면 다음 실행 때 해당 항목이 실패(INTERRUPTED)로 바뀌어 다시 시도할 수 있다.
- 브라우저 검증 스크립트(Playwright 필요, 예제 CSV는 `MLWB_EXAMPLE_CSV`로 지정 가능). 각 스크립트마다 빈 워크스페이스로 서버를 새로 띄운다.
  - S1·P2(실제 학습): `mlwb serve --workspace <빈 폴더>` → `python tests/e2e/check_s1_browser.py` / `python tests/e2e/check_p2_browser.py`
  - S7(새 데이터 적용): `python tests/e2e/make_future_csv.py`(학습 이후 기간 합성 CSV) 후 `mlwb serve --workspace <빈 폴더>` → `python tests/e2e/check_s7_browser.py`
  - rolling·차분·시간 특징(S2 추가 → 학습 → S7): 위와 같이 준비 후 `python tests/e2e/check_p2_features_browser.py`
  - XLSX(업로드·시트 선택 → 학습 → S7 새 엑셀): `python tests/e2e/make_future_csv.py && python tests/e2e/make_xlsx_examples.py` 후 `python tests/e2e/check_xlsx_browser.py`
  - 사용자 정의 플러그인(S2 추가 → 학습 → S7): `make_future_csv.py` 후 `mlwb serve --workspace <빈 폴더>` → `python tests/e2e/check_user_plugin_browser.py`
  - 비전 V1(이미지 ZIP·폴더 등록 → 구조·역할·예시 → 비전 목표): `mlwb serve --workspace <빈 폴더>` → `python tests/e2e/check_vision_browser.py` (합성 이미지를 임시 폴더에 만듦, 외부 다운로드 없음)
  - 실행 이름·실행/데이터셋 삭제: `mlwb serve --workspace <빈 폴더>` → `python tests/e2e/check_delete_browser.py`
  - 추가 요구사항 1차 화면(알림·자동 이동·뒤로가기·숫자+단위·기본값·닫기 확인·레이아웃 고정): `mlwb serve --workspace <빈 폴더>` → `python tests/e2e/check_ux_browser.py`
  - S6 실험 비교(학습 3개 → 체크 → 조건 차이·지표·그래프·평가 조건 경고): `mlwb serve --workspace <빈 폴더>` → `python tests/e2e/check_compare_browser.py`
  - 최소 대기열(학습 중 두 번째 제출 → 대기 → 취소·자동 시작): `mlwb serve --workspace <빈 폴더>` → `python tests/e2e/check_queue_browser.py` (동시 학습 수는 `MLWB_MAX_PARALLEL_RUNS`, 기본 1)
  - S2·S3(계획 미리보기 화면): `python tests/e2e/serve_ready_registry.py <빈 폴더> 8000 preview` → `check_s2_browser.py` / `check_s3_browser.py`
  - S4: 위 preview 서버(8000)와 `python tests/e2e/serve_ready_registry.py <빈 폴더> 8001 ready` → `check_s4_browser.py`(8001이 없으면 승인 흐름은 건너뜀)
- 학습 재현성 확인: `mlwb experiment verify <run_id> --workspace <폴더>` — 같은 seed로 다시 계산해 저장 지표와 비교(새 실행 기록을 만들지 않음).

### Windows에서 앱 창으로 열기

`Open-Workbench.cmd`를 두 번 클릭하면 로컬 서버가 시작되고 WebView2 기반의 워크벤치 창이 열린다. 상단에서 데이터셋 이름을 검색하고, 워크벤치 이름 주변이나 검색창 오른쪽 빈 공간을 끌어 창을 옮길 수 있다. 데이터는 기존 `workspace/edge-app/`에 저장한다. 서버는 창과 함께 켜지고 꺼진다. 창을 닫으면 서버와 분석용 하위 프로세스가 함께 종료되고, 다시 실행하면 현재 코드로 서버를 새로 띄운다. 이전 실행이 남긴 서버(실행기가 강제 종료된 경우, 이전 버전 실행기가 띄운 서버)는 다음 실행 때 먼저 정리한다. 창이 이미 열려 있으면 두 번째 실행은 알림만 띄운다.

새로 준비하는 경우 Windows WebView2 런타임이 필요하다. 프로젝트 루트에서 `python -m venv .venv`와 `.venv\Scripts\python.exe -m pip install pydantic PyYAML fastapi uvicorn "pywebview>=6.2,<7"`을 실행하고, `frontend`에서 `npm ci`와 `npm run build`를 실행한다.

## Claude Code에서 이어서 작업

현재 작업은 [XLSX 구현 지시서](docs/P2_XLSX_BRIEF.md)에 있다. 루트의 [CLAUDE.md](CLAUDE.md)를 통해 작업 규칙과 진행 기록에 접근한다. 기본 테스트에는 합성·공개 데이터와 Mock 공급자를 사용한다.

한글 화면은 React + TypeScript로 개발하고 FastAPI를 통해 Python 서비스에 연결한다. 레이아웃과 화면 상태는 [화면 설계서](docs/SCREEN_DESIGN.md)에 있다. 설계·계약·진행 기록과 구현 지시서는 `docs/`에 모으고 루트에는 README와 Claude 진입 문서를 둔다.

## MVP 성공 기준

- CSV/XLSX(시트 선택)/이미지 폴더를 등록하고, 로컬 프로파일러가 컬럼·폴더/라벨 구조를 설명한다.
- 공급자를 바꿔도 동일한 정형 JSON 스키마로 전처리/모델 추천을 받는다. LLM 연결 없이 수동 설정도 가능하다.
- LLM은 등록된 **실제 구현된** 도구만 추천하며, 미설치/비호환 도구는 실행할 수 없다.
- 사용자가 추천안을 편집하고 최종 승인한 뒤에만 GPU 학습이 실행된다.
- 시계열 XGBoost 1개, 비전 PatchCore 1개가 공개 데이터로 end-to-end 수행되며, 결과/평가 프로토콜이 저장된다.
- 새 전처리 플러그인을 추가해도 LLM 코어·UI·기존 모델 소스의 수정 없이 레지스트리에 노출된다.

## 구현 시 참고할 공식 문서

- MCP 도구 스펙: https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- Ollama OpenAI 호환 API: https://docs.ollama.com/api/openai-compatibility
- Ollama 정형 출력: https://docs.ollama.com/capabilities/structured-outputs
- vLLM OpenAI 호환 서버: https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/

> 프로토콜/라이브러리는 업데이트되므로 구현 시 설치 버전과 최신 공식 설명을 대조할 것. OpenAI-호환이라는 이름이 모든 공급자 기능의 완전한 동일성을 보장하지는 않는다.

## 사용자 정의 전처리 플러그인

`plugins/<폴더>/`에 `plugin.json`과 `.py`를 두면 앱·학습·`mlwb` 명령이 자동으로 찾아 S2 전처리 목록과 추천 카탈로그에 보여 준다(core 수정 없음).
예제는 `plugins/ts_custom_ewm/`(지수 가중 평균). 작성 규칙은 [plugins/README.md](plugins/README.md), 설계는 [docs/P2_PLUGIN_BRIEF.md](docs/P2_PLUGIN_BRIEF.md).
플러그인 코드는 신뢰된 로컬 코드로 실행되므로 직접 작성·검토한 코드만 둔다. `mlwb doctor`로 불러오기 상태를 확인할 수 있다.
