# 사용자 정의 전처리 플러그인

이 폴더의 하위 폴더 하나가 플러그인 하나다. 앱·학습 프로세스·`mlwb` 명령이 시작할 때 자동으로 찾아 등록한다
(코어 코드 수정 없음). 설계: `docs/P2_PLUGIN_BRIEF.md`, 계약: `docs/CONTRACTS.md` "P2 사용자 정의 플러그인 메모".

```text
plugins/<폴더>/
  plugin.json   # 명세(PluginSpec 필드) + "entry": "plugin.py:클래스"
  plugin.py     # transform(필수)·validate·fit·history
```

- ID는 `ts.custom.<이름>`, 시계열 전처리(`kind=preprocessor`, `modality=timeseries`)만 지원한다.
- **신뢰된 로컬 코드**로 그대로 실행된다(샌드박스 아님). 직접 작성했거나 검토한 코드만 둔다.
- 코드를 고치면 코드 해시가 바뀌어, 이전 확정(승인)은 제출할 수 없고 이전 학습 모델은 S7에서 쓸 수 없다. 다시 확정·학습한다.
- 실행할 때 자동 검사: 라벨(미래 정답) 숨김, 행·기존 열 변경 금지, 새 피처는 수치, 앞부분만 잘라 다시 계산해 같은 값인지(과거만 사용).
- 다른 위치를 쓰려면 환경변수 `MLWB_PLUGIN_DIRS`(여러 개는 Windows `;`, Linux `:`), 끄려면 빈 값으로 둔다.
- 불러오기 문제는 `mlwb doctor`와 S2 화면 위쪽 안내에 폴더 이름과 함께 표시된다.

예제: `ts_custom_ewm/` — 같은 설비·세션 안에서 현재 시점을 뺀 과거 값의 지수 가중 평균(EWM).
