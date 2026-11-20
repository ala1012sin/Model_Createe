"""시계열 실행용 표 구조와 CSV 읽기 정책.

정책 (CONTRACTS §11 "P2 시계열 실행 메모"와 같은 내용):
- 시각: 시간대 표기가 없으면 적힌 벽시계 시각 그대로 쓴다(변환 없음). 모든 값에 오프셋(Z, +09:00)이 있으면
  UTC로 바꾼 뒤 표기를 뗀다. 표기가 섞여 있으면 실행을 거부한다. 해석할 수 없는 시각의 행은 제외하고 개수를 기록한다.
- 같은 설비·같은 시각이 여러 행이면 수치 열을 평균해 한 행으로 합친다(개수 기록).
- 입력 피처: 타깃과 다른 수치 열의 '기준 시각 값'. 문자·범주 열은 이번 단계에서 쓰지 않는다(기록).
- 그룹 열 값이 비어 있으면 "(빈 값)" 그룹으로 둔다.
- 라벨(`__label`)은 타깃의 관측값 사본이다. 결측 채움·lag·스케일링을 적용하지 않는다(미래 정답을 채운 값으로 학습하지 않음).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

GROUP, TIME, SESSION, LABEL = "__group", "__time", "__session", "__label"
NO_GROUP = "__all__"
NULL_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-"}
_TZ = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")
_DURATION = re.compile(r"^(\d+)(s|min|h|d)$")
_UNIT = {"s": "s", "min": "min", "h": "h", "d": "D"}


class PipelineError(Exception):
    """사용자에게 보여 줄 실행 오류. 메시지에 경로·셀 값을 넣지 않는다."""

    def __init__(self, code: str, message_ko: str):
        super().__init__(message_ko)
        self.code, self.message_ko = code, message_ko


def duration(text: str) -> pd.Timedelta:
    m = _DURATION.match(text or "")
    if not m:
        raise PipelineError("INVALID_DURATION", f"시간 간격 형식이 올바르지 않습니다: {text}")
    return pd.Timedelta(int(m.group(1)), unit=_UNIT[m.group(2)])


@dataclass
class TsFrame:
    """그룹·시간 순으로 정렬된 시계열 표와 열 역할."""

    df: pd.DataFrame
    target: str
    value_cols: list[str]  # 관측 수치 열(타깃 포함)
    feature_cols: list[str]  # 모델 입력 열
    freq: pd.Timedelta | None = None  # 리샘플 후 일정 간격
    notes: list[str] = field(default_factory=list)
    drop_missing: bool = False  # 결측 처리 'drop': 결측이 있는 표본을 제외

    def copy(self) -> "TsFrame":
        return TsFrame(self.df.copy(), self.target, list(self.value_cols), list(self.feature_cols), self.freq,
                       list(self.notes), self.drop_missing)

    def sessions(self):
        """(그룹, 세션)별 행 묶음. 세션 분리를 하지 않았으면 그룹 전체가 한 세션."""
        return self.df.groupby([GROUP, SESSION], sort=False, group_keys=False)


def _parse_time(raw: pd.Series) -> tuple[pd.Series, str]:
    s = raw.astype("string").str.strip()
    # 날짜 부분의 / . 구분자를 - 로 맞춘다(소수 초의 . 은 그대로)
    s = s.str.replace(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})", r"\1-\2-\3", regex=True)
    has_tz = s.str.contains(_TZ, na=False)
    nonnull = s.notna()
    if has_tz.any() and (has_tz | ~nonnull).sum() != len(s):
        raise PipelineError("TIMESTAMP_TZ_MIXED", "시간대 표기가 있는 시각과 없는 시각이 섞여 있습니다. 한 가지 형식으로 맞춰 주세요")
    if has_tz.any():
        t = pd.to_datetime(s.str.replace(r"Z$", "+00:00", regex=True), errors="coerce", utc=True, format="ISO8601")
        return t.dt.tz_convert("UTC").dt.tz_localize(None), "utc_converted"
    t = pd.to_datetime(s, errors="coerce", format="ISO8601")
    return t, "naive_as_written"


def _numeric(raw: pd.Series) -> pd.Series:
    s = raw.astype("string").str.strip()
    s = s.mask(s.str.lower().isin(NULL_TOKENS))
    out = pd.to_numeric(s, errors="coerce").astype("float64")
    return out.where(np.isfinite(out))


def load_frame(path: Path, *, encoding: str, delimiter: str, timestamp_col: str, target_col: str,
               group_col: str | None, numeric_cols: list[str]) -> tuple[TsFrame, dict[str, Any]]:
    """CSV를 읽어 정렬된 TsFrame과 읽기 보고(행 수·제외 사유)를 돌려준다."""
    raw = pd.read_csv(path, encoding=encoding, sep=delimiter, dtype=str, keep_default_na=False, na_filter=False)
    for col in [timestamp_col, target_col] + ([group_col] if group_col else []):
        if col not in raw.columns:
            raise PipelineError("COLUMN_MISSING", "목표에 지정한 열이 파일에 없습니다. 데이터셋을 다시 분석해 주세요")
    report: dict[str, Any] = {"rows_read": int(len(raw))}
    t, tz_policy = _parse_time(raw[timestamp_col])
    report["timezone_policy"] = tz_policy
    values = [target_col] + [c for c in numeric_cols if c not in (target_col, timestamp_col, group_col)]
    df = pd.DataFrame({TIME: t})
    df[GROUP] = (raw[group_col].astype("string").str.strip().replace("", "(빈 값)").fillna("(빈 값)")
                 if group_col else NO_GROUP)
    for c in values:
        df[c] = _numeric(raw[c])
    if df[target_col].notna().sum() == 0:
        raise PipelineError("TARGET_NOT_NUMERIC", "타깃 열에서 숫자로 읽을 수 있는 값이 없습니다")
    bad_time = df[TIME].isna()
    report["rows_time_invalid"] = int(bad_time.sum())
    df = df[~bad_time]
    before = len(df)
    df = df.groupby([GROUP, TIME], sort=True, as_index=False)[values].mean()
    report["rows_duplicate_time_merged"] = int(before - len(df))
    report["excluded_columns"] = [c for c in raw.columns if c not in values + [timestamp_col] + ([group_col] if group_col else [])]
    df[SESSION] = 0
    df[LABEL] = df[target_col]
    df = df.sort_values([GROUP, TIME], kind="mergesort").reset_index(drop=True)
    report["rows_used"] = int(len(df))
    report["groups"] = sorted(df[GROUP].unique().tolist())
    if len(df) == 0:
        raise PipelineError("NO_VALID_ROWS", "시각을 해석할 수 있는 행이 없습니다")
    return TsFrame(df=df, target=target_col, value_cols=values, feature_cols=list(values)), report
