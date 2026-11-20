"""P1 CSV 등록·프로파일 (docs/P1_CSV_BRIEF.md). 합성 CSV만 사용한다."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta

import pytest

from mlworkbench.app.services import DatasetService
from mlworkbench.core.contracts import DatasetState
from mlworkbench.core.errors import StateTransitionError
from mlworkbench.core.registry import builtin_registry
from mlworkbench.core.security import find_sensitive
from mlworkbench.mcp.catalog import ToolFacade
from mlworkbench.storage.database import Workspace


def sensor_csv(rows: int = 288, *, step_min: int = 5, devices=("D01", "D02"), encoding="utf-8",
               header=("timestamp", "device_id", "sensor_1", "mode", "memo")) -> bytes:
    t0 = datetime(2026, 1, 3)
    lines = [",".join(header)]
    for i in range(rows):
        ts = (t0 + timedelta(minutes=step_min * i)).strftime("%Y-%m-%d %H:%M:%S")
        for d in devices:
            val = "" if i % 33 == 0 else f"{20 + (i % 17) * 0.5:.2f}"
            lines.append(f"{ts},{d},{val},{['run', 'idle', 'stop'][i % 3]},note-{d}-{i}")
    return ("\n".join(lines) + "\n").encode(encoding)


@pytest.fixture
def svc(tmp_path) -> DatasetService:
    return DatasetService(Workspace(tmp_path / "workspace").init())


def write(tmp_path, name: str, data: bytes):
    p = tmp_path / "inbox" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(data)
    return p


def files_in(svc: DatasetService, dataset_id: str) -> set[str]:
    d = svc.ws.root / "datasets" / dataset_id
    return {p.name for p in d.iterdir()} if d.exists() else set()


# --- 정상 경로 -------------------------------------------------------------
def test_register_and_profile_synthetic_csv(svc, tmp_path):
    m = svc.register_and_profile(write(tmp_path, "sensor_A.csv", sensor_csv()))
    assert m.state == DatasetState.PROFILED and m.error is None
    assert m.version.startswith("sha256:") and m.size_bytes > 0
    assert m.local_path == f"datasets/{m.dataset_id}/source.csv" and m.name == "sensor_A.csv"
    lp = svc.get_local_profile(m.dataset_id)
    assert lp.row_count == 576 == m.row_count and lp.column_count == 5
    types = {c.name: c.dtype for c in lp.columns}
    assert types == {"timestamp": "datetime", "device_id": "categorical", "sensor_1": "numeric",
                     "mode": "categorical", "memo": "text"}
    s1 = next(c for c in lp.columns if c.name == "sensor_1")
    assert s1.null_count == 18 and s1.null_pct == pytest.approx(18 / 576)
    assert [c.alias for c in lp.columns] == [f"column_{i}" for i in range(1, 6)]
    [tc] = lp.time_candidates
    assert tc.alias == "column_1" and tc.parse_rate == 1.0
    assert tc.interval.status == "estimated" and tc.interval.label == "5min" and tc.interval.seconds == 300
    assert tc.duplicate_timestamps == 288  # 설비 2대가 같은 시각 공유
    assert lp.duplicate_rows == 0 and lp.encoding == "utf-8" and lp.delimiter == ","
    assert files_in(svc, m.dataset_id) == {"source.csv"}  # 부분 파일 없음


def test_profile_survives_restart_and_reread(svc, tmp_path):
    m = svc.register_and_profile(write(tmp_path, "a.csv", sensor_csv(50)))
    again = DatasetService(Workspace(svc.ws.root).init())
    assert again.get_local_profile(m.dataset_id) == svc.get_local_profile(m.dataset_id)
    assert again.list()[0].dataset_id == m.dataset_id


def test_same_bytes_same_hash_changed_bytes_new_hash(svc, tmp_path):
    data = sensor_csv(30)
    a = svc.register_csv(write(tmp_path, "a.csv", data))
    b = svc.register_csv(io.BytesIO(data), display_name="other_name.csv")
    c = svc.register_csv(io.BytesIO(data.replace(b"20.50", b"20.51", 1)), display_name="a.csv")
    assert a.version == b.version != c.version
    assert a.dataset_id != b.dataset_id


def test_reprofile_increments_version(svc, tmp_path):
    m = svc.register_and_profile(write(tmp_path, "a.csv", sensor_csv(20)))
    m2 = svc.profile(m.dataset_id)
    assert m2.state == DatasetState.PROFILED and m2.profile_version == m.profile_version + 1
    assert svc.get_local_profile(m.dataset_id).profile_version == m2.profile_version


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp949"])
def test_korean_header(svc, tmp_path, encoding):
    data = sensor_csv(40, header=("측정시각", "설비", "온도", "상태", "비고"), encoding=encoding)
    m = svc.register_and_profile(write(tmp_path, "한글_센서.csv", data))
    assert m.state == DatasetState.PROFILED, m.error
    lp = svc.get_local_profile(m.dataset_id)
    assert [c.name for c in lp.columns] == ["측정시각", "설비", "온도", "상태", "비고"]
    assert lp.time_candidates[0].name == "측정시각" and lp.time_candidates[0].alias == "column_1"
    assert lp.encoding == ("cp949" if encoding == "cp949" else "utf-8")
    assert m.name == "한글_센서.csv"


def test_csv_without_time_candidate_profiles_with_notice(svc, tmp_path):
    data = "lot,weight,grade\n" + "\n".join(f"L{i},{10 + i * 0.1:.1f},{'AB'[i % 2]}" for i in range(30)) + "\n"
    m = svc.register_and_profile(write(tmp_path, "lots.csv", data.encode()))
    lp = svc.get_local_profile(m.dataset_id)
    assert m.state == DatasetState.PROFILED and lp.time_candidates == []
    assert "NO_TIME_CANDIDATE" in {n.code for n in lp.notices}
    assert svc.get_safe_profile(m.dataset_id).summary["time_candidates"] == []


@pytest.mark.parametrize("delim", [";", "\t", "|"])
def test_delimiter_detection(svc, tmp_path, delim):
    data = sensor_csv(20).decode().replace(",", delim).encode()
    m = svc.register_and_profile(write(tmp_path, "d.tsv" if delim == "\t" else "d.csv", data))
    lp = svc.get_local_profile(m.dataset_id)
    assert lp.delimiter == delim and lp.column_count == 5


def test_interval_insufficient_and_irregular_and_backward(svc, tmp_path):
    two = "time,v\n2026-01-01 00:00,1\n2026-01-01 00:05,2\n"
    lp = svc.get_local_profile(svc.register_and_profile(write(tmp_path, "two.csv", two.encode())).dataset_id)
    assert lp.time_candidates[0].interval.status == "insufficient" and lp.time_candidates[0].interval.reason_ko
    irregular = "time,v\n" + "\n".join(f"2026-01-01 00:{m:02d}:00,{m}" for m in (0, 1, 3, 7, 15, 31, 40, 52)) + "\n"
    lp = svc.get_local_profile(svc.register_and_profile(write(tmp_path, "irr.csv", irregular.encode())).dataset_id)
    assert lp.time_candidates[0].interval.status == "irregular"
    back = "time,v\n2026-01-01 00:00,1\n2026-01-01 00:10,2\n2026-01-01 00:05,3\n2026-01-01 00:15,4\n"
    lp = svc.get_local_profile(svc.register_and_profile(write(tmp_path, "back.csv", back.encode())).dataset_id)
    assert lp.time_candidates[0].backward_steps == 1 and "TIME_BACKWARD" in {n.code for n in lp.notices}


def test_duplicates_and_few_malformed_rows(svc, tmp_path):
    body = "\n".join(f"2026-01-01 00:{i:02d}:00,{i}" for i in range(200))
    data = f"time,v\n{body}\n2026-01-01 00:00:00,0\n2026-01-01 00:05:00,5,EXTRA\n\n"
    m = svc.register_and_profile(write(tmp_path, "dup.csv", data.encode()))
    lp = svc.get_local_profile(m.dataset_id)
    assert m.state == DatasetState.PROFILED and lp.row_count == 201
    assert lp.duplicate_rows == 1 and lp.malformed_rows == 1 and lp.blank_lines == 1
    assert {"DUPLICATE_ROWS", "MALFORMED_ROWS_SKIPPED"} <= {n.code for n in lp.notices}


def test_file_like_upload_uses_basename_only(svc):
    m = svc.register_and_profile(io.BytesIO(sensor_csv(10)), display_name=r"C:\Users\kim\Desktop\센서.csv")
    assert m.name == "센서.csv" and m.state == DatasetState.PROFILED


# --- 실패 경로 -------------------------------------------------------------
FAILS = [
    ("empty.csv", b"", "UPLOAD_FAILED", "EMPTY_FILE"),
    ("blank.csv", b"\n\n  \n", "PROFILE_FAILED", "EMPTY_FILE"),
    ("header_only.csv", b"time,v\n", "PROFILE_FAILED", "NO_DATA_ROWS"),
    ("bad_enc.csv", b"time,v\n\x80\xff\xfe,1\n", "PROFILE_FAILED", "ENCODING_UNSUPPORTED"),
    ("empty_name.csv", b"time,,v\n1,2,3\n", "PROFILE_FAILED", "HEADER_EMPTY_NAME"),
    ("dup_name.csv", b"a,a,b\n1,2,3\n", "PROFILE_FAILED", "HEADER_DUPLICATE"),
    ("no_header.csv", b"1,2,3\n4,5,6\n", "PROFILE_FAILED", "HEADER_LOOKS_LIKE_DATA"),
    ("quote.csv", b'a,b\n1,"unterminated\n2,3\n', "PROFILE_FAILED", "CSV_PARSE_ERROR"),
    ("ragged.csv", b"a,b\n1,2\n1,2,3\n4\n5,6,7,8\n9,10\n", "PROFILE_FAILED", "TOO_MANY_MALFORMED_ROWS"),
    ("data.xls", b"\xd0\xcf\x11\xe0", "UPLOAD_FAILED", "UNSUPPORTED_FILE_TYPE"),  # 구형 XLS(.xlsx는 P2에서 지원)
    ("data.xlsx", b"PK\x03\x04", "UPLOAD_FAILED", "XLSX_INVALID"),
]


@pytest.mark.parametrize("name,data,state,code", FAILS, ids=[f[3] + ":" + f[0] for f in FAILS])
def test_failure_states_and_reasons(svc, tmp_path, name, data, state, code):
    m = svc.register_and_profile(write(tmp_path, name, data))
    got = svc.get(m.dataset_id)
    assert got.state.value == state and got.error.code == code
    assert got.error.message_ko and any("가" <= ch <= "힣" for ch in got.error.message_ko)
    assert got.row_count is None and got.columns == []
    assert svc.get_local_profile(m.dataset_id) is None
    with pytest.raises(StateTransitionError):
        svc.get_safe_profile(m.dataset_id)
    assert ".upload.part" not in files_in(svc, m.dataset_id)
    if state == "UPLOAD_FAILED":
        assert got.version is None and got.local_path is None and files_in(svc, m.dataset_id) == set()


def test_missing_source_and_too_large(tmp_path):
    svc = DatasetService(Workspace(tmp_path / "ws").init(), max_upload_bytes=100)
    m = svc.register_csv(tmp_path / "nope.csv")
    assert m.state == DatasetState.UPLOAD_FAILED and m.error.code == "SOURCE_NOT_FOUND"
    m = svc.register_csv(io.BytesIO(sensor_csv(20)), display_name="big.csv")
    assert m.state == DatasetState.UPLOAD_FAILED and m.error.code == "FILE_TOO_LARGE"
    assert files_in(svc, m.dataset_id) == set()


def test_retry_after_profile_failure_with_fixed_file(svc, tmp_path):
    bad = svc.register_and_profile(write(tmp_path, "s.csv", b"time,,v\n1,2,3\n"))
    assert bad.state == DatasetState.PROFILE_FAILED
    again = svc.profile(bad.dataset_id)  # 같은 파일 재분석은 같은 이유로 실패
    assert again.state == DatasetState.PROFILE_FAILED and again.error.code == "HEADER_EMPTY_NAME"
    fixed = svc.register_and_profile(write(tmp_path, "s_fixed.csv", sensor_csv(20)), retry_of=bad.dataset_id)
    assert fixed.dataset_id == bad.dataset_id and fixed.state == DatasetState.PROFILED and fixed.error is None
    assert fixed.name == "s_fixed.csv" and fixed.version != bad.version
    assert svc.get_local_profile(fixed.dataset_id).row_count == 40


def test_retry_after_upload_failure(svc, tmp_path):
    bad = svc.register_csv(write(tmp_path, "e.csv", b""))
    ok = svc.register_and_profile(write(tmp_path, "e.csv", sensor_csv(5)), retry_of=bad.dataset_id)
    assert ok.state == DatasetState.PROFILED and ok.dataset_id == bad.dataset_id


def test_retry_not_allowed_on_success(svc, tmp_path):
    m = svc.register_and_profile(write(tmp_path, "a.csv", sensor_csv(5)))
    with pytest.raises(StateTransitionError):
        svc.register_csv(write(tmp_path, "b.csv", sensor_csv(6)), retry_of=m.dataset_id)


def test_changed_source_leaves_no_success_record(svc, tmp_path):
    m = svc.register_and_profile(write(tmp_path, "a.csv", sensor_csv(10)))
    (svc.ws.root / m.local_path).write_bytes(sensor_csv(11))  # 등록 후 원본 변조
    after = svc.profile(m.dataset_id)
    assert after.state == DatasetState.PROFILE_FAILED and after.error.code == "SOURCE_CHANGED"
    assert after.row_count is None and svc.get_local_profile(m.dataset_id) is None


# --- 전송 요약 누수 방지 -----------------------------------------------------
LEAKY_HEADERS = [r"C:\Users\kim\secret_dir", "api_key=sk-abcdefghijklmnopqrstuvwx", "/home/kim/data", "고객명", "measured_at"]
SECRET_VALUES = ["홍길동-UNIQUE-0001", "SN-ZX81-7731", "kim@example.com", "Bearer abcdefghijklmnopqrstu"]


def leaky_csv() -> bytes:
    import csv as _csv

    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(LEAKY_HEADERS)
    for i in range(60):
        w.writerow([f"{i * 1.5:.1f}", f"{100 + i}", f"{i % 4}", f"{SECRET_VALUES[i % 4]}-{i}",
                    f"2026-02-0{1 + i // 30} 0{i % 10}:{i % 6}0:00"])
    return buf.getvalue().encode("utf-8")


def test_safe_profile_has_no_raw_headers_values_or_paths(svc, tmp_path):
    src = write(tmp_path, "기밀_고객_목록.csv", leaky_csv())
    m = svc.register_and_profile(src)
    assert m.state == DatasetState.PROFILED, m.error
    sp = svc.get_safe_profile(m.dataset_id)
    text = json.dumps(sp.model_dump(mode="json"), ensure_ascii=False)
    forbidden = LEAKY_HEADERS + SECRET_VALUES + ["기밀_고객_목록", str(tmp_path), str(svc.ws.root), "source.csv",
                                                 m.dataset_id + "/", "datasets/", "2026-02-01"]
    for f in forbidden:
        assert f not in text, f
    assert find_sensitive(sp.model_dump(mode="json")) == []
    cols = sp.summary["columns"]
    assert [c["alias"] for c in cols] == [f"column_{i}" for i in range(1, 6)]
    assert set(cols[0]) == {"alias", "dtype", "null_pct", "distinct_count", "distinct_capped"}  # min/max 없음
    assert [t["alias"] for t in sp.summary["time_candidates"]] == ["column_5"]
    # 로컬 UI용 실제 이름은 별도로 유지
    lp = svc.get_local_profile(m.dataset_id)
    assert lp.alias_map()["column_4"] == "고객명" and lp.time_candidates[0].name == "measured_at"


def test_mcp_profile_tool_uses_safe_profile(svc, tmp_path):
    m = svc.register_and_profile(write(tmp_path, "기밀.csv", leaky_csv()))
    out = ToolFacade(builtin_registry(), datasets=svc).call("dataset.get_profile", {"dataset_id": m.dataset_id},
                                                            context="recommendation")
    text = json.dumps(out, ensure_ascii=False)
    assert all(v not in text for v in LEAKY_HEADERS + SECRET_VALUES) and "기밀" not in text


def test_profile_errors_do_not_leak_into_safe_profile_or_cli(svc, tmp_path, capsys):
    from mlworkbench.cli import main

    m = svc.register_and_profile(write(tmp_path, "q.csv", b'a,b\n1,"x\n'))
    assert m.error.code == "CSV_PARSE_ERROR"
    rc = main(["datasets", "show", m.dataset_id, "--workspace", str(svc.ws.root)])
    out = capsys.readouterr().out
    assert rc == 1 and "CSV_PARSE_ERROR" in out and str(tmp_path) not in out


def test_cli_add_list_show(tmp_path, capsys):
    from mlworkbench.cli import main

    ws = str(tmp_path / "w")
    f = write(tmp_path, "센서.csv", sensor_csv(30))
    assert main(["datasets", "add", str(f), "--workspace", ws, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "PROFILED" and "local_path" not in out
    assert main(["datasets", "show", out["dataset_id"], "--workspace", ws]) == 0
    text = capsys.readouterr().out
    assert "시간열 후보 timestamp(column_1)" in text and "5min" in text and str(tmp_path) not in text
    assert main(["datasets", "show", out["dataset_id"], "--workspace", ws, "--safe"]) == 0
    safe = capsys.readouterr().out
    assert '"timestamp"' not in safe and "센서" not in safe
    assert main(["datasets", "list", "--workspace", ws]) == 0


def test_headers_named_like_summary_keys_do_not_break(svc, tmp_path):
    data = "rows,alias,dtype,numeric\n" + "\n".join(f"{i},{i * 2},{i % 3},x{i % 2}" for i in range(20)) + "\n"
    m = svc.register_and_profile(write(tmp_path, "keys.csv", data.encode()))
    sp = svc.get_safe_profile(m.dataset_id)
    assert [c["alias"] for c in sp.summary["columns"]] == ["column_1", "column_2", "column_3", "column_4"]


def test_leak_guard_blocks_unexpected_value(svc, tmp_path):
    from mlworkbench.core.errors import PolicyError
    from mlworkbench.datasets.safe_profile import assert_no_leak

    m = svc.register_and_profile(write(tmp_path, "a.csv", sensor_csv(5)))
    sp = svc.get_safe_profile(m.dataset_id)
    sp.summary["columns"][0]["alias"] = "timestamp"  # 원래 헤더가 섞인 상황 재현
    with pytest.raises(PolicyError):
        assert_no_leak(sp, svc.get_local_profile(m.dataset_id))
