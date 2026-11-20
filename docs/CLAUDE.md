# Claude Code 지침 — ML Workbench v0.2

## 목표

개인용 **시계열·비전 ML 실험 플랫폼**을 개발한다. 데이터 업로드 시 로컬에서 구조/통계를 분석하고, **교체 가능한 LLM Provider**가 **등록된 MCP/플러그인 도구만** 사용하도록 전처리·모델 계획을 추천한다. 사용자는 계획을 편집·승인하고, 검증된 Python 학습 엔진을 실행한다.

## 작업 시작 전 필독

1. [ARCHITECTURE.md](ARCHITECTURE.md) — 책임, 상태기계, 데이터 안전성, 전체 파이프라인.
2. [CONTRACTS.md](CONTRACTS.md) — JSON schema, MCP, Provider, 플러그인, 승인 API.
3. [PROGRESS.md](PROGRESS.md) — 단계별 범위/테스트/진행 기록.
4. [README.md](../README.md) — 설치/목표, 우선 순위.

## 반드시 지킬 구현 규칙

1. **지정된 작업 범위만 진행.** 현재 작업은 [P2_XLSX_BRIEF.md](P2_XLSX_BRIEF.md)의 XLSX 입력 연결이다. 남은 체크리스트는 실제 구현 상태대로 유지한다.
2. UI·LLM 공급자·MCP Server·도메인 학습 엔진의 결합을 피한다. 새 LLM을 추가할 때 파이프라인/모델 코드를 수정하면 설계 위반.
3. DatasetProfiler가 먼저 구조를 분석한다. LLM에 전체 raw 데이터/원본 이미지/절대경로/비밀키를 기본 전송하지 않는다.
4. LLM 출력은 정형 `PlanProposal`이며 **코드가 아니다**. unknown tool, 미설치 플러그인, 위험한 파라미터를 validator가 거절한다.
5. MCP는 **기존 Registry/Application Service를 노출하는 도구 계층**이다. 모델 코드를 MCP 서버에 중복 구현하지 않는다. 호스트 백엔드가 MCP 클라이언트이자 실행 중개자이다.
6. 추천 단계 MCP 도구는 읽기 전용. `experiment.submit`과 `cancel`은 UI 승인/명시적 확인 및 서버 권한 검사 이후에만 허용.
7. 사용자 수정 → 재검증 → 승인 `plan_hash` 저장. 수정 시 이전 승인 즉시 무효화. LLM이 승인 토큰에 접근하도록 만들지 않는다.
8. `ProviderGateway`는 외부 API/OpenAI-Compatible/Mock을 지원하되, 모델별 tool calling·structured output 차이를 감지하고 JSON fallback 제공. 로컬 Ollama의 더미 key는 인증이 아님.
9. 데이터 누수 방지: train 전용 `fit`, 시간순 분할, horizon·세션 경계, 이미지 GT/마스크/normal calibration 역할 분리.
10. AU-PRO @0.05/@0.30은 pixel FPR 구간 통합이며, 이미지 정상 오판율과 다르다. GT 없으면 N/A 표시.
11. 최초에는 **합성/공개 데이터·mock provider**만 사용. 회사 코드·데이터/유료 API/회사 서버/로컬 LLM 설치는 사용자와 조직 승인 없이 하지 말 것.
12. 의존성은 최소화하고, GPU/비전/TF 패키지는 optional 또는 분리 환경. Windows 경로/프로세스·RTX GPU 동시 작업 고려.
13. 테스트를 실제 실행하고 증거를 남길 것. 단순 파일 생성과 성공 실행을 혼동하지 말 것.
14. 개발 과정에서 설계를 변경해야 한다면 이유/대안/영향을 문서에 먼저 기록. 기능 변경 시 회귀 테스트 추가.
15. 오래 걸리는 외부 네트워크 호출이나 패키지 설치는 필요성/환경을 확인하고, 숨겨진 백그라운드 장기 작업을 시작하지 말 것.

## 현재 구현 요청 (P2)

[P2_XLSX_BRIEF.md](P2_XLSX_BRIEF.md)에 따라 XLSX 등록·시트 선택부터 기존 학습과 S7 적용까지 연결한다.

## P0 구현 기록용 지침

화면 설계 이후의 P0 작업은 `IMPLEMENTATION_BRIEF.md`와 `CONTRACTS.md` §11을 함께 따른다.

`PROGRESS.md`의 P0에 한정해 다음을 구현한다.

- Python 프로젝트 구조/패키징/CLI doctor.
- `PluginSpec`, registry 등록/검색/버전/호환성.
- `ProviderConfig`, `ProviderGateway`, 테스트용 `MockProvider`.
- `PlanProposal`, `ValidationResult`, `ApprovedSnapshot` Pydantic 계약.
- MCP tools의 읽기 전용 카탈로그 인터페이스(서버 실제 구현은 범위 내 최소).
- LLM 추천 JSON에 등록되지 않은 플러그인 id가 들어온 경우 차단.
- 승인 전 `experiment.submit` 거절하는 서버측 테스트.
- SQLite/로컬 아티팩트 초기화, 비밀키·민감자료 로그 제거.
- pytest unit/integration 최소 fixture 및 실제 실행 기록.

P0 종료 시 **PROGRESS.md의 체크박스/현황/작업 로그를 갱신**하고, 다음 P1에서 할 수 있는 한 가지 작업만 제안한다. P0의 범위는 로컬 계약과 CLI 검증까지다.

## 매 작업 후 보고 템플릿

1. 변경 내용 요약
2. 수정/추가된 파일 목록
3. 테스트 명령과 실제 PASS/FAIL/미실행 결과
4. 남은 위험/알려진 한계
5. PROGRESS 갱신 내역 및 다음 작업 1개
