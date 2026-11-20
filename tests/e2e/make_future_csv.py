"""S7 검증용 '학습 이후 기간' 합성 CSV 만들기.

생성 기준(재현 가능):
- 원본: 학습 예제 motor_sensor_example.csv(2026-09-01 ~ 2026-10-30 23:45, 15분, MOTOR-1·2).
- 원본의 마지막 7일(2026-10-24 00:00 ~ 10-30 23:45)을 7일 뒤로 옮긴다 → 2026-10-31 00:00 ~ 2026-11-06 23:45.
  학습·평가에 쓰인 어떤 시각과도 겹치지 않는 이후 기간이다.
- 수치 열(temperature_c·ambient_temp_c·motor_current_a·vibration_mm_s)에 seed 7의 작은 정규 잡음(표준편차 0.05)을 더한다.
  빈 값은 그대로 둔다. 문자 열(device_id·operating_mode)과 열 순서는 그대로.
실행: python tests/e2e/make_future_csv.py [원본 CSV] [출력 CSV]
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "outputs" / "workbench-sensor-example" / "motor_sensor_example.csv"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "workspace" / "e2e-data" / "motor_sensor_future.csv"
NUMERIC = ["temperature_c", "ambient_temp_c", "motor_current_a", "vibration_mm_s"]

df = pd.read_csv(SRC, dtype=str, keep_default_na=False)
ts = pd.to_datetime(df["timestamp"])
start = ts.max().normalize() - pd.Timedelta(days=6)
part = df[ts >= start].copy()
part["timestamp"] = (pd.to_datetime(part["timestamp"]) + pd.Timedelta(days=7)).dt.strftime("%Y-%m-%d %H:%M")
rng = np.random.default_rng(7)
for c in NUMERIC:
    v = pd.to_numeric(part[c].replace("", np.nan), errors="coerce")
    noisy = (v + rng.normal(0, 0.05, len(v))).round(2)
    part[c] = noisy.map(lambda x: "" if pd.isna(x) else f"{x:.2f}")
OUT.parent.mkdir(parents=True, exist_ok=True)
part.to_csv(OUT, index=False)
print(f"{OUT} {len(part)}행 {part['timestamp'].iloc[0]} ~ {part['timestamp'].iloc[-1]}")
