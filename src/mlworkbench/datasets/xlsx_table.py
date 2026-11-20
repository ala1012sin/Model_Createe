"""XLSX 입력 어댑터: 워크북 점검·시트 목록·선택한 시트를 공통 표(CSV)로 정규화.

정규화 규칙 `xlsx-table-v1` (CONTRACTS §11 "P2 XLSX 메모"):
- 첫 번째 '값이 있는' 행을 헤더로 쓴다. 헤더 위의 빈 행은 건너뛴다.
- 열 범위: 헤더 또는 데이터에 값이 있는 가장 왼쪽 ~ 가장 오른쪽 열. 범위 안에서 헤더도 값도 없는 열은 제외(개수 기록).
  헤더가 빈 칸인데 아래에 값이 있으면 오류(위치·수정 방법), 헤더가 중복이면 오류.
- 데이터 행: 헤더 아래에서 값이 하나라도 있는 행. 완전히 빈 행은 제외(가운데·끝 따로 개수 기록).
  서식만 있고 값이 없는 셀은 행·열 수에 넣지 않는다.
- 병합 셀이 표 범위(헤더~마지막 데이터 행)에 있으면 오류: 병합을 해제하고 값을 채우도록 안내.
- 값: 날짜·시각 셀은 워크북의 날짜 기준(1900/1904)으로 라이브러리가 해석한 값을 'YYYY-MM-DD HH:MM:SS'(시간대 없음)로,
  날짜만은 'YYYY-MM-DD'. 숫자는 저장된 값 그대로(표시 형식·반올림 무시). 텍스트는 그대로(앞자리 0 보존).
  논리값 TRUE/FALSE. 빈 셀은 빈 문자열.
- 수식은 실행하지 않는다. 파일에 저장된 계산 결과를 쓴다. 표 범위의 수식에 저장 결과가 없거나 Excel 오류 값(#DIV/0! 등)이
  있으면 위치와 함께 오류(빈 값·0으로 바꾸지 않음).
- 외부 링크는 읽지 않는다(keep_links=False). 매크로·구형 XLS·암호 파일은 지원하지 않는다.
- ZIP 점검: 항목 수와 압축 해제 크기 합계를 미리 확인(압축 폭탄 방지).
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import warnings
import zipfile
from pathlib import Path
from typing import Any

RULES = "xlsx-table-v1"
MAX_ENTRIES = 20_000
MAX_UNCOMPRESSED = 2 * 1024**3
MAX_RATIO = 200  # 압축 해제 크기 / 파일 크기
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_REFS = 5  # 오류 메시지에 보여 줄 셀 위치 수


class XlsxError(Exception):
    def __init__(self, code: str, message_ko: str, **detail: Any):
        super().__init__(message_ko)
        self.code, self.message_ko, self.detail = code, message_ko, detail


def require_openpyxl():
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise XlsxError("XLSX_DEPENDENCY_MISSING",
                        "엑셀 파일을 읽는 구성요소가 설치되어 있지 않습니다. "
                        "pip install -e \".[xlsx]\" 로 설치한 뒤 다시 시도해 주세요") from None
    import openpyxl

    return openpyxl


def unsupported_message(suffix: str) -> str | None:
    s = suffix.lower()
    if s == ".xls":
        return "구형 엑셀(.xls)은 지원하지 않습니다. Excel에서 '다른 이름으로 저장 → Excel 통합 문서(.xlsx)'로 저장해 주세요"
    if s in (".xlsm", ".xlsb", ".xltm", ".xltx"):
        return "매크로·바이너리·서식 파일은 지원하지 않습니다. 매크로 없는 Excel 통합 문서(.xlsx)로 저장해 주세요"
    return None


def check_zip(path: Path) -> None:
    with path.open("rb") as f:
        head = f.read(8)
    if head == _OLE:
        raise XlsxError("XLSX_ENCRYPTED_OR_XLS",
                        "암호가 걸렸거나 구형 형식의 엑셀 파일입니다. 암호를 해제하고 .xlsx로 다시 저장해 주세요")
    if not zipfile.is_zipfile(path):
        raise XlsxError("XLSX_INVALID", "엑셀(.xlsx) 파일 형식이 아닙니다. 파일이 손상되지 않았는지 확인해 주세요")
    size = path.stat().st_size
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise XlsxError("XLSX_TOO_COMPLEX", "엑셀 파일 안의 항목이 너무 많습니다")
        total = sum(i.file_size for i in infos)
        if total > MAX_UNCOMPRESSED or total > max(size, 1) * MAX_RATIO + 50 * 1024**2:
            raise XlsxError("XLSX_TOO_LARGE_UNCOMPRESSED",
                            "압축을 풀면 너무 커지는 엑셀 파일입니다. 필요한 시트만 남겨 저장해 주세요")
        names = {i.filename for i in infos}
        if "xl/workbook.xml" not in names:
            raise XlsxError("XLSX_INVALID", "엑셀(.xlsx) 통합 문서가 아닙니다")
        if "xl/vbaProject.bin" in names:
            raise XlsxError("XLSX_MACRO", "매크로가 들어 있는 파일은 지원하지 않습니다. 매크로 없이 .xlsx로 저장해 주세요")


def _load(path: Path, *, read_only: bool, data_only: bool):
    openpyxl = require_openpyxl()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # 확장 기능 미지원 경고 등
        try:
            return openpyxl.load_workbook(path, read_only=read_only, data_only=data_only, keep_links=False,
                                          keep_vba=False)
        except Exception:  # noqa: BLE001 - 손상 파일 등 라이브러리별 예외
            raise XlsxError("XLSX_INVALID", "엑셀 파일을 열 수 없습니다. Excel에서 열리는지 확인하고 다시 저장해 주세요") from None


def inspect(path: Path) -> dict[str, Any]:
    """워크북 점검 + 시트 목록(이름·표시 상태·대략 크기)."""
    check_zip(path)
    wb = _load(path, read_only=True, data_only=True)
    try:
        sheets = []
        for ws in wb.worksheets:
            sheets.append({"name": ws.title, "state": ws.sheet_state,
                           "rows_estimate": ws.max_row or 0, "cols_estimate": ws.max_column or 0})
        return {"sheets": sheets, "epoch": "1904" if _is_1904(wb) else "1900"}
    finally:
        wb.close()


def _is_1904(wb) -> bool:
    try:
        from openpyxl.utils.datetime import CALENDAR_MAC_1904

        return wb.epoch == CALENDAR_MAC_1904
    except Exception:  # noqa: BLE001
        return False


def _ref(row: int, col: int) -> str:
    from openpyxl.utils import get_column_letter

    return f"{get_column_letter(col)}{row}"


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, dt.datetime):
        if v.tzinfo is not None:
            v = v.replace(tzinfo=None)
        return v.strftime("%Y-%m-%d %H:%M:%S" + (".%f" if v.microsecond else ""))
    if isinstance(v, dt.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, dt.time):
        return v.strftime("%H:%M:%S")
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def sheet_to_csv(path: Path, sheet: str, out: Path) -> dict[str, Any]:
    """선택한 시트를 규칙대로 CSV(UTF-8, 쉼표)로 쓴다. 성공하면 정규화 보고를 돌려준다."""
    check_zip(path)
    wb = _load(path, read_only=False, data_only=True)
    try:
        if sheet not in wb.sheetnames:
            raise XlsxError("SHEET_NOT_FOUND", "선택한 시트가 파일에 없습니다")
        ws = wb[sheet]
        epoch = "1904" if _is_1904(wb) else "1900"
        cells: dict[tuple[int, int], Any] = {}
        errors: list[str] = []
        for row in ws.iter_rows():
            for c in row:
                if c.value is None:
                    continue
                if c.data_type == "e":
                    errors.append(c.coordinate)
                    continue
                if isinstance(c.value, str) and c.value.strip() == "":
                    continue
                cells[(c.row, c.column)] = c.value
        merged = [str(r) for r in ws.merged_cells.ranges]
        sheet_state = ws.sheet_state
    finally:
        wb.close()
    # 수식 위치(저장 결과 유무 확인용). 수식 문자열은 어디에도 저장하지 않는다.
    formulas: list[tuple[int, int]] = []
    wbf = _load(path, read_only=True, data_only=False)
    try:
        for row in wbf[sheet].iter_rows():
            for c in row:
                v = getattr(c, "value", None)
                if isinstance(v, str) and v.startswith("=") or getattr(c, "data_type", None) == "f":
                    formulas.append((c.row, c.column))
    finally:
        wbf.close()

    if not cells and not errors and not formulas:
        raise XlsxError("SHEET_EMPTY", "선택한 시트에 값이 없습니다. 다른 시트를 골라 주세요")
    rows_with = sorted({r for r, _ in cells} | {r for r, _ in formulas})
    header_row = rows_with[0]
    last_row = rows_with[-1]
    cols_with = sorted({c for _, c in cells} | {c for _, c in formulas})
    first_col, last_col = cols_with[0], cols_with[-1]

    in_table = [e for e in errors if _in_range(e, header_row, last_row, first_col, last_col)]
    if in_table:
        raise XlsxError("XLSX_ERROR_VALUE",
                        f"Excel 오류 값(#DIV/0!, #N/A 등)이 있습니다: {', '.join(in_table[:MAX_REFS])}"
                        + (f" 외 {len(in_table) - MAX_REFS}곳" if len(in_table) > MAX_REFS else "")
                        + ". Excel에서 수식을 고치고 다시 저장해 주세요")
    no_cache = [(r, c) for r, c in formulas if (r, c) not in cells]
    if no_cache:
        refs = [_ref(r, c) for r, c in no_cache]
        raise XlsxError("FORMULA_NO_CACHED_VALUE",
                        f"수식의 계산 결과가 파일에 저장되어 있지 않습니다: {', '.join(refs[:MAX_REFS])}"
                        + (f" 외 {len(refs) - MAX_REFS}곳" if len(refs) > MAX_REFS else "")
                        + ". Excel에서 파일을 열어 다시 계산(F9)한 뒤 저장해 주세요")
    bad_merge = [m for m in merged if _range_overlaps(m, header_row, last_row, first_col, last_col)]
    if bad_merge:
        raise XlsxError("XLSX_MERGED_CELLS",
                        f"표 안에 병합된 셀이 있습니다: {', '.join(bad_merge[:MAX_REFS])}. "
                        "병합을 해제하고 각 칸에 값을 채운 뒤 다시 저장해 주세요")

    columns, dropped_cols = [], 0
    header_names: dict[str, list[str]] = {}
    for col in range(first_col, last_col + 1):
        h = cells.get((header_row, col))
        has_data = any((r, col) in cells for r in range(header_row + 1, last_row + 1))
        if _empty(h):
            if has_data:
                raise XlsxError("HEADER_EMPTY_NAME", f"헤더(첫 줄)가 비어 있는 열이 있습니다: {_ref(header_row, col)}. "
                                                     "열 이름을 입력하고 다시 저장해 주세요")
            dropped_cols += 1
            continue
        name = _text(h).strip()
        header_names.setdefault(name, []).append(_ref(header_row, col))
        columns.append((col, name))
    dups = {n: refs for n, refs in header_names.items() if len(refs) > 1}
    if dups:
        n, refs = next(iter(dups.items()))
        raise XlsxError("HEADER_DUPLICATE", f"같은 열 이름이 여러 번 있습니다: '{n}' ({', '.join(refs)}). "
                                            "열 이름을 서로 다르게 바꿔 주세요")
    data_rows = [r for r in range(header_row + 1, last_row + 1) if any((r, c) in cells for c, _ in columns)]
    blank_middle = (last_row - header_row) - len(data_rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    h = hashlib.sha256()
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow([n for _, n in columns])
        for r in data_rows:
            w.writerow([_text(cells.get((r, c))) for c, _ in columns])
    with tmp.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    tmp.replace(out)
    return {
        "rules": RULES, "sheet": sheet, "sheet_state": sheet_state, "epoch": epoch,
        "header_row": header_row, "first_column": _ref(header_row, first_col)[:-len(str(header_row))],
        "rows": len(data_rows), "columns": len(columns), "empty_columns_dropped": dropped_cols,
        "blank_rows_skipped": blank_middle, "rows_above_header": header_row - 1,
        "formula_cells": len(formulas), "table_sha256": "sha256:" + h.hexdigest(),
    }


def _in_range(coord: str, r0: int, r1: int, c0: int, c1: int) -> bool:
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

    col, row = coordinate_from_string(coord)
    return r0 <= row <= r1 and c0 <= column_index_from_string(col) <= c1


def _range_overlaps(rng: str, r0: int, r1: int, c0: int, c1: int) -> bool:
    from openpyxl.utils.cell import range_boundaries

    a, b, c, d = range_boundaries(rng)  # min_col, min_row, max_col, max_row
    return not (d < r0 or b > r1 or c < c0 or a > c1)
