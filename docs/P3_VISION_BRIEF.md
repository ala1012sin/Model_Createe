# 비전 MVP (P1 이미지 → P3) — 설계 기록 (2026-11-20, Claude 작성)

> 사용자 지시: "이제 사용성은 두고 개발 진전을 하자" → 다음 단계로 "비전 MVP 시작 (P1 이미지 → P3)" 선택.
> 근거: REQUIREMENTS DATA-03·TRAIN-01·EVAL-01·APPLY-01, SCREEN_DESIGN S1-V·S3(비전)·S7(이미지)·§12 따라가기, ARCHITECTURE §7(비전),
> CLAUDE.md 규칙 9·10(역할 분리, AU-PRO는 pixel FPR 적분·GT 없으면 N/A), 11(공개·합성 데이터만), 12(GPU·비전 패키지는 optional).
> 별도 지시서가 없어 규칙 14에 따라 구현 전에 기록한다.

## 단계(한 번에 하나씩, 단계마다 테스트·브라우저·PC 반영)

| 단계 | 범위 | 새 의존성 |
|---|---|---|
| V1 (완료) | 이미지 ZIP 업로드·로컬 폴더 등록 → 보안 검사 → 폴더 트리·수량·형식·크기·손상 표본 검사·GT 마스크 짝 → 예시 이미지(정상·불량) → 폴더 역할 확정(정상 학습/보정 정상/정상 평가/불량 평가/GT 마스크/제외)·보정 분리·태스크 = 비전 목표 저장 | 없음(이미지 머리글을 직접 읽음. Pillow가 있으면 손상 검사에만 사용) |
| **V2 (이번)** | 이상탐지 지표(이미지 AUROC·불량 검출률·정상 오판율, 픽셀 AU-PRO@0.05/0.30 — 합성 정답과 대조), 분할·보정 규칙(임계값) | numpy |
| V3 | PatchCore 어댑터(정상 학습만, 메모리·코어셋·anomaly map), S2 resize/normalize, S3 학습 이미지 수, worker 실행·저장 | `vision` extra: torch·torchvision (설치는 사용자 승인 후, PC는 CPU 소량 fixture만) |
| V4 | S5 결과(지표·맵 미리보기), S6 비교(비전 지표), S7 새 이미지 점수·판정·CSV | — |

## V1 설계

| 항목 | 결정 | 이유 |
|---|---|---|
| 등록 경로 | ① `.zip` 업로드 → `datasets/<id>/source.zip` 보관, 검사 후 `images/`에 풀기 ② 로컬 폴더 등록 `POST /api/datasets/folder {path}` → 제자리 읽기 전용(복사 안 함), 경로는 매니페스트 `local_path`(로컬 전용) | 큰 공개 데이터셋은 복사하지 않음. 원본 수정 금지 |
| ZIP 보안 | 항목 ≤ 200,000, 압축 해제 합계 ≤ 20GiB, 항목별 압축률 ≤ 200, 절대경로·`..`·드라이브 문자·심볼릭 링크 거부(사유와 개수), `__MACOSX`·`.DS_Store`·숨김 파일 무시 | 경로 순회·압축 폭탄 |
| 폴더 등록 보안 | 존재하는 폴더, 심볼릭 링크 따라가지 않음, 이미지 확장자만 읽음, 파일 수 ≤ 200,000. 응답·LLM에 절대경로 없음 | |
| 이미지 형식 | PNG·JPEG·BMP·TIFF(머리글로 크기·채널 수 읽기). 미리보기는 브라우저가 그리는 PNG·JPEG·BMP만(TIFF는 '미리보기 불가 형식') | 의존성 없이 |
| 손상 검사 | 폴더마다 고르게 최대 200장 표본: PNG 청크 CRC·IEND, JPEG SOI/EOI, BMP 크기 일치. Pillow가 있으면 `verify()`도. 결과 "손상 n / 표본 m장" | 전체 검사는 학습 단계에서 |
| 폴더 역할 추정 | 이미지가 있는 폴더마다: `ground_truth/gt/mask(s)` 포함 → GT 마스크, `train` + `good/ok/normal` → 정상 학습, `val(idation)` + 정상 이름 → 보정 정상, `test` + 정상 이름 → 정상 평가, `test` + 그 밖 → 불량 평가(불량 종류 = 폴더 이름), 그 밖 → 확인 필요(제외). **추정일 뿐 사용자가 확정** | MVTec AD 형식 우선, 다른 형식은 사용자가 고름 |
| GT 짝 | 불량 평가 이미지 `test/<종류>/<이름>`에 대해 `ground_truth/<종류>/<이름>_mask.*` 또는 `<이름>.*`. 짝 맞춤 n/m | AU-PRO 가능 여부(V2) |
| 버전 | 이미지·마스크 파일의 (상대 경로, 크기, 내용 해시) 정렬 해시 | 승인·실행 결합 |
| 비전 목표(`VisionGoal`) | `roles: {폴더 상대경로: train_normal|calibration_normal|test_normal|test_defect|gt_mask|ignore}`, `calibration: {source: folder|split_from_train, fraction 0.05~0.5, seed}`, `task: anomaly_detection`(세그멘테이션은 라벨 필요 — 이번엔 선택 불가 안내). 검증: 정상 학습 ≥ 10장, 평가에 정상·불량이 모두 있어야 AUROC(없으면 경고), GT 없으면 AU-PRO N/A 경고, 같은 폴더 중복 역할 불가 | 규칙 9 역할 분리 |
| LLM 전송(SafeProfile) | 역할별 장수·형식·크기 분포·GT 유무·불량 종류 **개수**만. 폴더 이름·파일 이름·경로·픽셀 없음(`hidden_field_kinds`에 folder_names·file_names·pixels) | 규칙 3 |
| 화면 | 업로드 영역에 ZIP·'로컬 폴더 등록', 목록에 '이미지' 종류, 구조 패널 S1-V(요약·폴더 트리·역할 선택·예시 2장·보정·태스크·목표 저장) | SCREEN_DESIGN S1-V |
| 미리보기 API | `GET /api/datasets/{id}/image?path=<상대경로>` — 등록 폴더 안 이미지 파일만(경로 정규화 후 범위 확인), 20MB 이하 | 로컬 화면 전용 |
| 테스트 데이터 | 순수 Python으로 만드는 합성 MVTec 형식 PNG(정상·불량·마스크) — 외부 다운로드 없음 | 규칙 11 |

## V2 설계 — 이상탐지 지표 (2026-11-20, 구현 전 기록)

> 사용자 지시: "비전 V2만 진행. 기존 평가 기능 재사용, image AUROC·NG recall·정상 이미지 오판율·AU-PRO@0.05/0.30. 임계값은 정상 보정 데이터로 정하고 테스트로 조정하지 말 것. 계산 단위·집계를 문서에 명시, 계산 불가는 이유와 함께 N/A. 합성 정답으로 검증. 모델 학습·화면 확장 없음."
> 근거: ARCHITECTURE §7 평가·AU-PRO, CONTRACTS §8(AU-PRO GT 없음 → `null` + `missing_gt_masks`, 0점 아님), SCREEN_DESIGN S3(비전)·S6, CLAUDE.md 규칙 9·10.

**재사용**: 계약 `VisionAnomalyEvaluation`(지표 이름 `auroc`·`ng_recall`·`normal_fpr`·`aupro_0.05`·`aupro_0.30`)·`ThresholdRule`(`calibration_normal_max`·`calibration_quantile`·`fixed`), `pipelines/metrics.py`의 계산 불가 = `null` 규칙·유한값 정리(`_clean`)·`metrics_meta.json`의 `definitions`/`aggregation` 형식. 새 모듈 `pipelines/vision_metrics.py`는 모델과 무관한 순수 함수(입력 = 점수·맵·마스크 배열) — V3 PatchCore worker가 그대로 호출. 이미지·마스크 파일 읽기는 V3(모델 출력과 같은 해상도로 맞추는 단계와 함께).

**새 의존성 없음**: numpy(시계열 extra에 이미 있음). 연결 영역 라벨링은 scipy가 있으면 `scipy.ndimage.label`(scikit-learn 의존성으로 이미 설치됨), 없으면 순수 Python 대체 구현 — 두 구현 결과 일치를 테스트.

| 지표 | 계산 단위 | 집계 | 계산 불가(N/A) 이유 코드 |
|---|---|---|---|
| 임계값 | 보정 정상 이미지의 이미지 점수 | `calibration_normal_max` = 최댓값, `calibration_quantile` = q 분위(관측 점수 중 하나: numpy `method="higher"`), `fixed` = 지정값. **함수 입력에 테스트 데이터가 없음**(구조적으로 테스트로 조정 불가). 보정·테스트 이미지 id가 겹치면 오류 | `no_calibration_scores`(보정 0장), `non_finite_score` |
| 판정 규칙 | 이미지 | 점수 **> 임계값**이면 불량(같으면 정상) — 최댓값 규칙에서 보정 정상은 모두 정상 판정 | — |
| `auroc` (image AUROC) | 테스트 이미지 1장 = 표본 1개, 양성 = 불량 | 정상·불량 전체를 한 번에. Mann-Whitney 순위 통계, 동점은 평균 순위(= 0.5 기여). 임계값과 무관 | `empty_input`, `single_class`(정상 또는 불량만), `non_finite_score` |
| `ng_recall` (불량 검출률) | 테스트 불량 이미지 | 불량 판정 수 / 불량 이미지 수 | `empty_input`, `no_defect_images`, `threshold_unavailable` |
| `normal_fpr` (정상 이미지 오판율) | 테스트 **정상** 이미지(보정 이미지 제외) | 불량 판정 수 / 정상 이미지 수 | `empty_input`, `no_normal_images`, `threshold_unavailable` |
| `aupro_0.05` / `aupro_0.30` | 픽셀 FPR: 모든 테스트 이미지(정상 포함)의 GT=0 픽셀. PRO: GT 연결 영역(8-이웃) 1개 = 1단위, 영역마다 검출 픽셀 비율 | 픽셀 임계값 격자 = 모든 고유 맵 값(정확 곡선, 높은 값부터). 곡선마다 (pixel FPR, 영역 평균 PRO), 시작점 (0,0). 0~상한 구간 사다리꼴 적분, 상한에서 선형 보간, **상한으로 나눠 0~1 정규화**. 이미지 정상 오판율과 다른 지표 | `empty_input`, `missing_gt_masks`(불량 이미지 중 하나라도 마스크 없음 — 일부만으로 계산하지 않음, 개수 기록), `missing_anomaly_maps`, `no_defect_regions`(마스크가 모두 비어 있음), `no_normal_pixels`, `non_finite_score` |

- 정상 테스트 이미지는 마스크가 없어도 GT = 전부 0으로 본다(정의상 결함 없음). 마스크는 0보다 크면 결함 픽셀. 맵과 마스크 크기가 다르면 계산하지 않고 입력 오류(크기 맞춤은 V3 책임).
- 결과 = `metrics`(값 또는 `null`) + `not_available`{지표: {code, message_ko}} + `threshold`{method, value, calibration_count} + `counts`(정상·불량 테스트 수, TP·FN·FP·TN, 영역 수, 마스크 없는 불량 수). `metrics_meta`에 정의·계산 단위·집계·격자·보간·정규화를 기록.
- 검증(합성 정답): AUROC 손 계산·완전 분리 1·역전 0·전부 동점 0.5·scikit-learn과 대조 / 임계값 규칙 3종·경계값(점수 = 임계값 → 정상)·테스트 점수를 바꿔도 임계값 불변·보정 이미지가 정상 오판율에 안 섞임 / AU-PRO 완전 맵 1·역전 맵 0·상수 맵 = 상한/2·손 계산 사례·영역 크기와 무관한 영역 평균(픽셀 TPR과 다름)·대각선 연결·느린 참조 구현(임계값마다 직접 계산)과 무작위 대조·scipy/순수 Python 라벨링 일치 / 이미지 정상 오판율 0인데 픽셀 FPR > 0인 사례 / N/A 사유 전부.

## 진행

- V1 완료(2026-11-20): pytest 344 passed(작업 공간)·343+1 skipped(PC), 브라우저 비전 6/6. 기록은 `PROGRESS.md` 작업 로그.
- 검증 중 바뀐 결정: 폴더 경로 입력 오류는 400(입력 오류)로 응답한다(초안은 409).
- V2 완료(2026-11-20): `pipelines/vision_metrics.py` + `tests/unit/test_vision_metrics.py`(31). pytest 375 passed(작업 공간). 학습·화면 변경 없음 — S3·S5·S6 연결은 V3·V4.
- 검증 중 바뀐 결정: 판정 규칙(점수 > 임계값)을 `predict_defect` 한 곳에만 두고 묶음 평가도 이 함수를 쓴다(변이 검사에서 중복 정의가 테스트를 피해 가는 것을 발견).
- 다음: V3 PatchCore(torch 설치는 사용자 승인 후).
