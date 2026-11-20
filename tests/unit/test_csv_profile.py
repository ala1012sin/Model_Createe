"""csv_profile 타입 추정 경계값 — 시각 해석 조기 중단이 판정을 바꾸지 않는지."""

from mlworkbench.datasets.csv_profile import profile_csv


def _profile(tmp_path, text: str):
    p = tmp_path / "x.csv"
    p.write_text(text, encoding="utf-8")
    return {c.name: c for c in profile_csv(p, dataset_id="ds_t", dataset_version="v", profile_version=1).columns}


def test_datetime_with_sparse_junk_stays_datetime(tmp_path):
    rows = [f"2026-01-01 {i // 60 % 24:02d}:{i % 60:02d}:00" if i % 100 != 7 else "unknown" for i in range(1000)]
    cols = _profile(tmp_path, "t\n" + "\n".join(rows) + "\n")
    assert cols["t"].dtype == "datetime" and cols["t"].parse_fail_count == 10


def test_text_with_some_dates_is_not_datetime(tmp_path):
    rows = [f"2026-01-0{1 + i % 9}" if i % 10 == 0 else f"comment {i}" for i in range(1000)]
    assert _profile(tmp_path, "t\n" + "\n".join(rows) + "\n")["t"].dtype == "text"


def test_numeric_with_few_junk_and_formats(tmp_path):
    rows = [str(i * 0.5) if i % 200 else "err" for i in range(1000)]
    cols = _profile(tmp_path, "v,d\n" + "\n".join(f"{r},2026/01/{1 + i % 28:02d}" for i, r in enumerate(rows)) + "\n")
    assert cols["v"].dtype == "numeric" and cols["v"].parse_fail_count == 5
    assert cols["d"].dtype == "datetime"


def test_iso_with_timezone(tmp_path):
    rows = [f"2026-01-01T00:{i:02d}:00Z" for i in range(30)] + [f"2026-01-01T01:{i:02d}:00+09:00" for i in range(30)]
    assert _profile(tmp_path, "t\n" + "\n".join(rows) + "\n")["t"].dtype == "datetime"


def test_all_null_column_is_empty(tmp_path):
    cols = _profile(tmp_path, "a,b\n1,\n2,NA\n3,null\n")
    assert cols["b"].dtype == "empty" and cols["b"].null_pct == 1.0


def test_small_all_unique_column_is_text_not_category(tmp_path):
    cols = _profile(tmp_path, "memo,line\n" + "\n".join(f"note-{i},L{i % 2}" for i in range(40)) + "\n")
    assert cols["memo"].dtype == "text" and cols["line"].dtype == "categorical"
