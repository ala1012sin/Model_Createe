"""테스트용 XLSX를 재현 가능한 기준으로 만든다(openpyxl, 고정 값·날짜, 난수 없음).

- csv_to_xlsx: CSV를 같은 내용의 엑셀로. 시각 열은 날짜 셀(datetime), 지정 수치 열은 숫자 셀, 나머지는 텍스트 셀.
  빈 값은 빈 셀. 첫 시트 이름은 지정(기본 '2026_line1')이고 뒤에 '메모'(값 1개) 시트를 붙인다.
- edge_workbook: 시트마다 경계 사례 하나(한글 헤더·텍스트 ID·날짜·저장된 수식 결과 / 결과 없는 수식 / 오류 값 /
  빈·중복 헤더 / 병합 / 빈 시트 / 숨김 시트 / 서식만 있는 끝 셀 / 헤더 위·가운데 빈 행과 빈 열).
- 저장된 수식 결과: openpyxl은 수식의 계산 결과를 쓰지 않으므로, 저장 뒤 시트 XML의 해당 셀에 <v>값</v>을 넣는다
  (Excel이 저장한 파일과 같은 구조).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from pathlib import Path


def _openpyxl():
    import openpyxl

    return openpyxl


def csv_to_xlsx(src: bytes | Path, out: Path, *, sheet: str = "2026_line1", time_col: str = "timestamp",
                numeric: tuple[str, ...] = (), epoch_1904: bool = False, extra_sheet: bool = True) -> Path:
    op = _openpyxl()
    text = src.read_text(encoding="utf-8") if isinstance(src, Path) else src.decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    wb = op.Workbook()
    if epoch_1904:
        from openpyxl.utils.datetime import CALENDAR_MAC_1904

        wb.epoch = CALENDAR_MAC_1904
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for r in rows[1:]:
        vals = []
        for name, v in zip(header, r):
            if v == "":
                vals.append(None)
            elif name == time_col:
                vals.append(_parse_dt(v))
            elif name in numeric:
                f = float(v)
                vals.append(int(f) if f.is_integer() and "." not in v else f)
            else:
                vals.append(v)
        ws.append(vals)
    tcol = header.index(time_col) + 1
    for row in ws.iter_rows(min_row=2, min_col=tcol, max_col=tcol):
        row[0].number_format = "yyyy-mm-dd hh:mm:ss"
    if extra_sheet:
        wb.create_sheet("메모").append(["설명"])
    wb.save(out)
    return out


def _parse_dt(v: str) -> dt.datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(v, fmt)
        except ValueError:
            pass
    raise ValueError(v)


def _set_cached_values(path: Path, sheet_index: int, values: dict[str, str]) -> None:
    """저장된 시트 XML의 수식 셀에 계산 결과(<v>)를 넣는다."""
    name = f"xl/worksheets/sheet{sheet_index}.xml"
    with zipfile.ZipFile(path) as z:
        items = {i.filename: z.read(i.filename) for i in z.infolist()}
    xml = items[name].decode("utf-8")
    for ref, val in values.items():
        pat = re.compile(rf'(<c r="{ref}"[^>]*>)(<f>[^<]*</f>)(<v\s*/>|<v></v>)?')
        xml, n = pat.subn(lambda m: f"{m.group(1)}{m.group(2)}<v>{val}</v>", xml)
        assert n == 1, ref
    items[name] = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in items.items():
            z.writestr(k, v)


EDGE_SHEETS = ["데이터", "결과없는수식", "오류값", "빈헤더", "중복헤더", "병합", "빈시트", "숨김", "서식끝", "빈행빈열"]


def edge_workbook(out: Path) -> Path:
    op = _openpyxl()
    wb = op.Workbook()
    ws = wb.active
    ws.title = "데이터"
    ws.append(["측정시각", "설비ID", "온도", "두배"])
    base = dt.datetime(2026, 3, 1, 8, 0)
    for i in range(6):
        ws.append([base + dt.timedelta(minutes=15 * i), "00123" if i % 2 == 0 else "00456", 20.5 + i, f"=C{i + 2}*2"])
    for r in range(2, 8):
        ws.cell(row=r, column=1).number_format = "yyyy-mm-dd hh:mm"
        ws.cell(row=r, column=3).number_format = "0"  # 표시 형식은 정수지만 저장값은 20.5 등
    s = wb.create_sheet("결과없는수식")
    s.append(["t", "v", "w"])
    s.append([base, 1, "=B2+1"])
    s = wb.create_sheet("오류값")
    s.append(["t", "v"])
    s.append([base, 1])
    s.append([base + dt.timedelta(minutes=15), "#DIV/0!"])
    s = wb.create_sheet("빈헤더")
    s.append(["t", None, "v"])
    s.append([base, 3, 1])
    s = wb.create_sheet("중복헤더")
    s.append(["t", "v", "v"])
    s.append([base, 1, 2])
    s = wb.create_sheet("병합")
    s.append(["t", "v"])
    s.append([base, 1])
    s.append([None, 2])
    s.merge_cells("A2:A3")
    wb.create_sheet("빈시트")
    s = wb.create_sheet("숨김")
    s.append(["t", "v"])
    s.append([base, 1])
    s.sheet_state = "hidden"
    s = wb.create_sheet("서식끝")
    s.append(["t", "v"])
    s.append([base, 1])
    s.append([base + dt.timedelta(minutes=15), 2])
    s.cell(row=5000, column=8).number_format = "0.00"  # 값 없이 서식만
    s.cell(row=5000, column=8).font = op.styles.Font(bold=True)
    s = wb.create_sheet("빈행빈열")
    s.append([])
    s.append([])
    s.append(["t", None, "v"])  # 헤더 위 빈 행 2개, 가운데 빈 열 1개
    s.append([base, None, 1])
    s.append([])
    s.append([base + dt.timedelta(minutes=15), None, 2])
    s.append([])
    wb.save(out)
    _set_cached_values(out, 1, {f"D{i + 2}": repr((20.5 + i) * 2) for i in range(6)})
    return out


def epoch_1904_workbook(out: Path) -> Path:
    op = _openpyxl()
    from openpyxl.utils.datetime import CALENDAR_MAC_1904

    wb = op.Workbook()
    wb.epoch = CALENDAR_MAC_1904
    ws = wb.active
    ws.title = "mac"
    ws.append(["t", "v"])
    for i in range(3):
        ws.append([dt.datetime(2026, 1, 2, 0, 0) + dt.timedelta(hours=i), i])
        ws.cell(row=i + 2, column=1).number_format = "yyyy-mm-dd hh:mm"
    wb.save(out)
    return out
