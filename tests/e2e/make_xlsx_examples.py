"""XLSX 브라우저 검증용 예제 만들기(재현 가능: 고정 내용, 난수 없음).

- motor_sensor_example.xlsx: 학습 예제 CSV와 같은 내용. 시트 '2026_line1'(timestamp는 날짜 셀, 수치 4열은 숫자 셀,
  device_id·operating_mode는 텍스트 셀, 빈 값은 빈 셀) + 값 하나인 '메모' 시트.
- motor_sensor_future.xlsx: make_future_csv.py가 만든 이후 기간 CSV와 같은 내용. 시트 이름은 학습 때와 다른 '2026_11'
  + '메모'.
실행: python tests/e2e/make_future_csv.py && python tests/e2e/make_xlsx_examples.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from xlsx_samples import csv_to_xlsx  # noqa: E402

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "outputs" / "workbench-sensor-example" / "motor_sensor_example.csv"
FUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "workspace" / "e2e-data" / "motor_sensor_future.csv"
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "workspace" / "e2e-data"
NUMERIC = ("temperature_c", "ambient_temp_c", "motor_current_a", "vibration_mm_s")

OUT.mkdir(parents=True, exist_ok=True)
a = csv_to_xlsx(SRC, OUT / "motor_sensor_example.xlsx", sheet="2026_line1", numeric=NUMERIC)
b = csv_to_xlsx(FUT, OUT / "motor_sensor_future.xlsx", sheet="2026_11", numeric=NUMERIC)
print(a, a.stat().st_size, "bytes")
print(b, b.stat().st_size, "bytes")
