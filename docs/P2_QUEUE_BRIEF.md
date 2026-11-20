# P2 최소 대기열 — 설계 기록 (2026-11-20, Claude 작성)

> 사용자 지시(대화): 우선순위 ④ 최소 대기열, "대기열 먼저"(창 닫기 확인·모델 파라미터 기본값·데이터셋 삭제는 그다음).
> 별도 지시서가 없어 규칙 14에 따라 구현 전에 설계를 먼저 기록한다. 근거: PROGRESS P2 "`experiment.submit` 승인값 검증 후
> background subprocess 큐", ARCHITECTURE §8 "동시 GPU 학습 기본 1개, 대기/취소/실패 복구".

## 완료 범위

- 동시에 도는 학습 worker는 기본 **1개**(`MLWB_MAX_PARALLEL_RUNS`, 1~4). 나머지 제출은 `QUEUED`로 기다렸다가 제출 순서대로 시작한다.
- 대기 중인 실행 취소 → worker 없이 바로 `CANCELED`.
- 앱을 껐다 켜도 대기 중인 실행은 그대로 대기열에 남고, 앞 실행이 끝나면 이어서 시작한다.
- S5: 대기 중이면 "대기 중 · 앞에 N개" 표시.

범위 밖: 우선순위 변경·순서 바꾸기, GPU 자원 감지, 여러 앱 인스턴스 간 분산(같은 작업 공간을 두 앱이 동시에 쓰는 것은 지원하지 않음).

## 설계

| 항목 | 결정 | 이유·대안 |
|---|---|---|
| 상태 | 새 상태를 만들지 않고 `QUEUED` = 대기(worker.lock 없음) / 시작 중(lock 있음) | 상태기계·기존 화면 유지 |
| 디스패치 | `RunManager.dispatch()`: 잠금 안에서 '끝나지 않았고 worker.lock이 있는 실행' 수를 세고, 남는 자리만큼 lock 없는 `QUEUED`를 `created_at` 순으로 `start()` | 제출 API·주기 대조(2초)·앱 시작 때 호출. 제출 API가 직접 worker를 띄우던 것을 대체 |
| 취소 | 대기 중(lock 없음) 실행은 취소 요청 즉시 같은 잠금 안에서 `CANCELED`. 이미 시작된 실행은 기존처럼 worker가 응답 | 시작과 취소가 겹쳐도 worker가 `CANCEL_REQUESTED`를 보고 멈춘다 |
| 재시작 복구 | worker.json 있음 → 기존 규칙(실행 중/중단). lock만 있고 worker.json 없음(시작 도중 종료) → `FAILED(WORKER_NOT_STARTED)`. 둘 다 없음 → **대기 유지** | 기존: 시작 전 실행도 실패 처리 → 대기열 도입으로 변경(회귀 테스트 수정) |
| 앱 종료 | 실행 중 worker는 기존처럼 종료·`APP_SHUTDOWN`. 대기 실행은 그대로 | |
| 실행 전 검사 | worker 시작 시 기존 검사(계획 해시·데이터 버전·사용자 플러그인 코드) 그대로 | 대기 중 데이터·코드가 바뀌어도 실행 안 됨 |
| 화면 | RunView `queue_position`(대기 중일 때 1부터, 앞에 기다리는 수 = position−1 + 실행 중 수), `max_parallel` | |
