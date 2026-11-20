"""CSV 로컬 프로파일러 (P1). 표준 라이브러리 csv만 사용한다.

처리 기준
- 인코딩: UTF-8(BOM 허용) → CP949(한글 Windows/Excel) 순서로 시도. 둘 다 실패하면 ENCODING_UNSUPPORTED.
- 구분자: 앞부분 표본으로 , ; 탭 | 중 추정. 실패 시 쉼표.
- 첫 행은 헤더. 빈 이름·중복 이름·전부 숫자인 헤더(헤더 누락 의심)는 실패.
- 빈 줄은 건너뛰고 개수만 센다.
- 열 개수가 헤더와 다른 행은 통계에서 제외하고 경고. 1%를 넘으면(최소 1행 초과 시) 실패.
- 따옴표가 닫히지 않는 등 해석 불가 → CSV_PARSE_ERROR(줄 번호).
- 데이터 행이 없으면 NO_DATA_ROWS.
- 타입 추정: 결측 제외 값의 98% 이상이 숫자면 numeric, 시각이면 datetime,
  그 외 고유 비율 ≤ 50%이거나 (고유값 ≤ 50이고 고유 비율 < 90%)면 categorical, 나머지 text. 전부 결측이면 empty.
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from mlworkbench.core.contracts import (
    ColumnProfile,
    IntervalEstimate,
    LocalProfile,
    NumericSummary,
    ProfileNotice,
    TimeCandidate,
)

NULL_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-"}
TYPE_THRESHOLD = 0.98
MALFORMED_MAX_RATIO = 0.01
DISTINCT_CAP = 10_000
DUPLICATE_CHECK_MAX_ROWS = 5_000_000
TIMESTAMP_SAMPLE_MAX = 2_000_000
_DT_PREFIX = re.compile(r"^\d{4}[-/.]\d")  # 지원 형식은 모두 연도 4자리 + 구분자로 시작
DT_GIVE_UP_AFTER = 200  # 이만큼 시도한 뒤 실패율이 5%를 넘으면 그 열은 시각 해석 중단(98% 기준 도달 불가)
_TIME_NAME_HINT = re.compile(r"(time|date|timestamp|datetime|dt|ts|시간|시각|일시|날짜|일자)", re.I)

DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d",
]


class ProfileFailure(Exception):
    """프로파일 실패. message_ko는 사용자용, detail은 로컬 전용."""

    def __init__(self, code: str, message_ko: str, **detail: Any):
        super().__init__(code)
        self.code, self.message_ko, self.detail = code, message_ko, detail


# ---------------------------------------------------------------------------
def detect_encoding(path: Path) -> str:
    for enc in ("utf-8-sig", "cp949"):
        dec = codecs.getincrementaldecoder(enc)(errors="strict")
        try:
            with path.open("rb") as f:
                while chunk := f.read(1 << 20):
                    dec.decode(chunk)
                dec.decode(b"", final=True)
            return enc
        except UnicodeDecodeError:
            continue
    raise ProfileFailure("ENCODING_UNSUPPORTED", "파일 인코딩을 읽을 수 없습니다. UTF-8 또는 CP949(한글 Excel)로 저장해 주세요")


def detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _is_null(v: str) -> bool:
    return v.strip().lower() in NULL_TOKENS


def _to_float(v: str) -> float | None:
    try:
        x = float(v.strip())
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def _parse_dt(v: str, fmt_hint: list[str]) -> tuple[datetime | None, str | None]:
    s = v.strip()
    if not _DT_PREFIX.match(s):
        return None, None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    for fmt in fmt_hint:
        try:
            return datetime.strptime(s, fmt), fmt
        except ValueError:
            pass
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            d = datetime.fromisoformat(s)
            return d, "iso8601"
    except ValueError:
        pass
    for fmt in DATETIME_FORMATS:
        if fmt in fmt_hint:
            continue
        try:
            return datetime.strptime(s, fmt), fmt
        except ValueError:
            pass
    return None, None


def _epoch(d: datetime) -> float:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)  # 시간대 표기가 없으면 상대 간격만 의미 있음
    return d.timestamp()


def _label(seconds: float) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60), ("s", 1)):
        if seconds >= size and abs(seconds / size - round(seconds / size)) < 1e-9:
            return f"{int(round(seconds / size))}{unit}"
    return f"{seconds:g}s"


class _Col:
    def __init__(self, name: str):
        self.name = name
        self.nulls = 0
        self.nonnull = 0
        self.num_ok = 0
        self.num_min = math.inf
        self.num_max = -math.inf
        self.num_sum = 0.0
        self.dt_ok = 0
        self.dt_fmts: list[str] = []
        self.dt_counter: Counter[str] = Counter()
        self.distinct: set[str] = set()
        self.capped = False
        self.ts: list[float] = []  # 해석된 시각(epoch 초), 간격 추정용
        self.dt_tried = 0
        self.dt_disabled = False

    def add(self, raw: str) -> None:
        if _is_null(raw):
            self.nulls += 1
            return
        self.nonnull += 1
        v = raw.strip()
        if not self.capped:
            self.distinct.add(v)
            if len(self.distinct) > DISTINCT_CAP:
                self.capped = True
        x = _to_float(v)
        if x is not None:
            self.num_ok += 1
            self.num_min = min(self.num_min, x)
            self.num_max = max(self.num_max, x)
            self.num_sum += x
            return
        # 숫자가 아닌 값만 시각 해석 시도 (숫자열은 epoch 등 모호하므로 시각으로 보지 않음)
        if self.dt_disabled:
            return
        self.dt_tried += 1
        d, fmt = _parse_dt(v, self.dt_fmts)
        if d is None and self.dt_tried >= DT_GIVE_UP_AFTER and (self.dt_tried - self.dt_ok) / self.dt_tried > 0.05:
            self.dt_disabled = True
        if d is not None:
            self.dt_ok += 1
            self.dt_counter[fmt] += 1
            if fmt not in self.dt_fmts and fmt != "iso8601":
                self.dt_fmts.insert(0, fmt)
            if len(self.ts) < TIMESTAMP_SAMPLE_MAX:
                self.ts.append(_epoch(d))

    def dtype(self) -> str:
        if self.nonnull == 0:
            return "empty"
        if self.num_ok / self.nonnull >= TYPE_THRESHOLD:
            return "numeric"
        if self.dt_ok / self.nonnull >= TYPE_THRESHOLD:
            return "datetime"
        n = len(self.distinct)
        ratio = n / self.nonnull
        # 거의 모든 값이 서로 다르면(메모·ID 등) 고유값 수가 적어도 텍스트로 본다
        if not self.capped and (ratio <= 0.5 or (n <= 50 and ratio < 0.9)):
            return "categorical"
        return "text"


def profile_csv(path: Path, *, dataset_id: str, dataset_version: str, profile_version: int) -> LocalProfile:
    encoding = detect_encoding(path)
    with path.open("r", encoding=encoding, newline="") as f:
        sample = f.read(64 * 1024)
    if not sample.strip():
        raise ProfileFailure("EMPTY_FILE", "파일에 내용이 없습니다")
    delimiter = detect_delimiter(sample)

    notices: list[ProfileNotice] = []
    with path.open("r", encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter=delimiter, strict=True)
        try:
            header = next((r for r in reader if any(c.strip() for c in r)), None)
        except csv.Error:
            raise ProfileFailure("CSV_PARSE_ERROR", f"{reader.line_num}번째 줄을 해석할 수 없습니다(따옴표·구분자 확인)",
                                 line=reader.line_num)
        if header is None:
            raise ProfileFailure("HEADER_MISSING", "헤더 행을 찾을 수 없습니다")
        names = [h.strip() for h in header]
        if any(not n for n in names):
            raise ProfileFailure("HEADER_EMPTY_NAME", "이름이 비어 있는 열이 있습니다. 첫 행에 모든 열 이름을 넣어 주세요",
                                 positions=[i + 1 for i, n in enumerate(names) if not n])
        dup = [n for n, c in Counter(names).items() if c > 1]
        if dup:
            raise ProfileFailure("HEADER_DUPLICATE", f"같은 이름의 열이 {len(dup)}종 있습니다. 열 이름을 서로 다르게 바꿔 주세요",
                                 names=dup)
        if all(_to_float(n) is not None for n in names):
            raise ProfileFailure("HEADER_LOOKS_LIKE_DATA", "첫 행이 숫자로만 되어 있어 헤더가 없는 파일로 보입니다. 열 이름 행을 추가해 주세요")

        cols = [_Col(n) for n in names]
        width = len(names)
        rows = blank = malformed = 0
        malformed_lines: list[int] = []
        seen: set[bytes] | None = set()
        duplicates = 0
        try:
            for rec in reader:
                if not any(c.strip() for c in rec):
                    blank += 1
                    continue
                if len(rec) != width:
                    malformed += 1
                    if len(malformed_lines) < 5:
                        malformed_lines.append(reader.line_num)
                    continue
                rows += 1
                for c, v in zip(cols, rec):
                    c.add(v)
                if seen is not None:
                    seen_key = hashlib.blake2b("\x1f".join(rec).encode("utf-8"), digest_size=12).digest()
                    if seen_key in seen:
                        duplicates += 1
                    else:
                        seen.add(seen_key)
                    if rows > DUPLICATE_CHECK_MAX_ROWS:
                        seen = None
                        notices.append(ProfileNotice(code="DUPLICATE_CHECK_SKIPPED", severity="info",
                                                     message_ko="행이 많아 중복 행 검사를 생략했습니다"))
        except csv.Error:
            raise ProfileFailure("CSV_PARSE_ERROR", f"{reader.line_num}번째 줄을 해석할 수 없습니다(따옴표·구분자 확인)",
                                 line=reader.line_num)

    if rows == 0:
        raise ProfileFailure("NO_DATA_ROWS", "헤더만 있고 데이터 행이 없습니다" if not malformed else
                             "열 개수가 헤더와 맞는 데이터 행이 없습니다", malformed_rows=malformed)
    if malformed > max(1, rows * MALFORMED_MAX_RATIO):
        raise ProfileFailure("TOO_MANY_MALFORMED_ROWS",
                             f"열 개수가 헤더와 다른 행이 {malformed}개로 너무 많습니다(첫 위치: {malformed_lines[0]}번째 줄). 구분자나 따옴표를 확인해 주세요",
                             malformed_rows=malformed, lines=malformed_lines)
    if malformed:
        notices.append(ProfileNotice(code="MALFORMED_ROWS_SKIPPED", severity="warning",
                                     message_ko=f"열 개수가 맞지 않는 {malformed}개 행을 분석에서 제외했습니다",
                                     detail={"lines": malformed_lines}))
    if width == 1:
        notices.append(ProfileNotice(code="SINGLE_COLUMN", severity="warning",
                                     message_ko="열이 1개로 인식되었습니다. 구분자가 맞는지 확인해 주세요"))
    if seen is not None and duplicates:
        notices.append(ProfileNotice(code="DUPLICATE_ROWS", severity="info", message_ko=f"완전히 같은 행이 {duplicates}개 있습니다"))

    profiles: list[ColumnProfile] = []
    candidates: list[TimeCandidate] = []
    for i, c in enumerate(cols):
        alias = f"column_{i + 1}"
        dtype = c.dtype()
        parse_fail = 0
        if dtype == "numeric":
            parse_fail = c.nonnull - c.num_ok
        elif dtype == "datetime":
            parse_fail = c.nonnull - c.dt_ok
        numeric = (NumericSummary(min=c.num_min, max=c.num_max, mean=c.num_sum / c.num_ok)
                   if dtype == "numeric" and c.num_ok else None)
        profiles.append(ColumnProfile(
            alias=alias, name=c.name, dtype=dtype, null_count=c.nulls, null_pct=round(c.nulls / rows, 6),
            distinct_count=len(c.distinct), distinct_capped=c.capped, parse_fail_count=parse_fail, numeric=numeric))
        if dtype == "datetime":
            candidates.append(_time_candidate(alias, c, c.ts))
        if rows and c.nulls / rows >= 0.5 and dtype != "empty":
            notices.append(ProfileNotice(code="HIGH_NULL_COLUMN", severity="info",
                                         message_ko=f"결측이 절반 이상인 열이 있습니다", detail={"column": c.name}))

    # 이름 힌트·해석률 순 정렬
    candidates.sort(key=lambda t: (-t.parse_rate, not _TIME_NAME_HINT.search(t.name)))
    if not candidates:
        notices.append(ProfileNotice(code="NO_TIME_CANDIDATE", severity="info",
                                     message_ko="시간열 후보를 찾지 못했습니다. 시계열 예측에 쓰려면 타입 수정에서 시간열을 지정해 주세요"))
    for t in candidates:
        if t.backward_steps:
            notices.append(ProfileNotice(code="TIME_BACKWARD", severity="warning",
                                         message_ko=f"시간 순서가 거꾸로인 곳이 {t.backward_steps}곳 있습니다(학습 전 자동 정렬)",
                                         detail={"column": t.name}))
            break

    return LocalProfile(
        dataset_id=dataset_id, dataset_version=dataset_version, profile_version=profile_version,
        encoding="utf-8" if encoding == "utf-8-sig" else encoding, delimiter=delimiter,
        row_count=rows, column_count=width, blank_lines=blank, malformed_rows=malformed,
        malformed_line_samples=malformed_lines, duplicate_rows=duplicates if seen is not None else None,
        columns=profiles, time_candidates=candidates, notices=notices,
    )


def _time_candidate(alias: str, c: _Col, values: list[float]) -> TimeCandidate:
    fmt = c.dt_counter.most_common(1)[0][0] if c.dt_counter else "unknown"
    backward = sum(1 for a, b in zip(values, values[1:]) if b < a)
    uniq = sorted(set(values))
    dup_ts = len(values) - len(uniq)
    diffs = [b - a for a, b in zip(uniq, uniq[1:])]
    if len(diffs) < 2:
        interval = IntervalEstimate(status="insufficient", reason_ko="서로 다른 시각이 3개 미만이라 간격을 추정할 수 없습니다")
    else:
        rounded = Counter(round(d, 3) for d in diffs)
        sec, n = rounded.most_common(1)[0]
        share = n / len(diffs)
        if share < 0.5:
            interval = IntervalEstimate(status="irregular", seconds=float(median(diffs)), share=round(share, 4),
                                        label=_label(float(median(diffs))),
                                        reason_ko="간격이 일정하지 않습니다(중앙값 기준 표시). 리샘플링을 고려하세요")
        else:
            interval = IntervalEstimate(status="estimated", seconds=float(sec), label=_label(float(sec)), share=round(share, 4))

    def iso(x: float) -> str:
        return datetime.fromtimestamp(x, tz=timezone.utc).replace(tzinfo=None).isoformat()

    return TimeCandidate(
        alias=alias, name=c.name, parse_rate=round(c.dt_ok / c.nonnull, 6) if c.nonnull else 0.0, format=fmt,
        backward_steps=backward, duplicate_timestamps=dup_ts,
        start=iso(uniq[0]) if uniq else None, end=iso(uniq[-1]) if uniq else None, interval=interval,
    )
