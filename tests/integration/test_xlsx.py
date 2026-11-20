"""P2 XLSX: 업로드 → 시트 선택 → 구조 확인 → 학습 → S7 새 엑셀 예측. 같은 내용 CSV와 결과 비교."""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("openpyxl")
pytest.importorskip("xgboost")
pd = pytest.importorskip("pandas")
from fastapi.testclient import TestClient  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_csv_dataset import sensor_csv  # noqa: E402
from test_preprocess import GOAL  # noqa: E402
from test_runs import SETTINGS, start, wait  # noqa: E402
from xlsx_samples import EDGE_SHEETS, csv_to_xlsx, edge_workbook, epoch_1904_workbook  # noqa: E402

DONE = ("PROFILED", "PROFILE_FAILED", "UPLOAD_FAILED")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False)) as c:
        c.ws, c.tmp = tmp_path / "ws", tmp_path
        yield c


def post_file(c, name: str, data: bytes) -> dict:
    r = c.post("/api/datasets", content=data, headers={"X-File-Name": quote(name)})
    assert r.status_code == 201, r.text
    return r.json()


def wait_ds(c, did, states=DONE, timeout=30) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/datasets/{did}").json()
        if d["state"] in states:
            return d
        time.sleep(0.05)
    raise AssertionError(d)


def choose(c, did, sheet) -> dict:
    r = c.post(f"/api/datasets/{did}/sheet", json={"sheet": sheet})
    assert r.status_code == 202, r.text
    return wait_ds(c, r.json()["dataset_id"])


def xlsx_bytes(tmp, maker, name="book.xlsx") -> bytes:
    return maker(tmp / name).read_bytes()


# --- 업로드·시트 목록·선택 ---------------------------------------------------------
def test_upload_lists_sheets_and_waits_for_choice(client):
    data = xlsx_bytes(client.tmp, edge_workbook)
    d = post_file(client, "센서_2026.xlsx", data)
    assert d["source_kind"] == "xlsx" and d["state"] == "REGISTERED" and d["needs_sheet"] is True
    assert [s["name"] for s in d["sheets"]] == EDGE_SHEETS
    assert next(s for s in d["sheets"] if s["name"] == "숨김")["state"] == "hidden"
    time.sleep(0.5)
    assert client.get(f"/api/datasets/{d['dataset_id']}").json()["state"] == "REGISTERED"  # 시트 선택 전 분석 안 함
    r = client.post(f"/api/datasets/{d['dataset_id']}/profile")
    assert r.status_code == 409 and r.json()["code"] == "SHEET_REQUIRED"
    assert (client.ws / "datasets" / d["dataset_id"] / "source.xlsx").exists()


def test_selected_sheet_profile_dates_text_ids_and_formulas(client):
    d = post_file(client, "센서.xlsx", xlsx_bytes(client.tmp, edge_workbook))
    p = choose(client, d["dataset_id"], "데이터")
    assert p["state"] == "PROFILED" and p["selected_sheet"] == "데이터" and p["row_count"] == 6 and p["column_count"] == 4
    assert p["table"]["formula_cells"] == 6 and p["table"]["header_row"] == 1 and p["table"]["rules"] == "xlsx-table-v1"
    prof = client.get(f"/api/datasets/{d['dataset_id']}/profile").json()
    cols = {c["name"]: c for c in prof["columns"]}
    assert cols["측정시각"]["dtype"] == "datetime" and cols["온도"]["dtype"] == "numeric" and cols["두배"]["dtype"] == "numeric"
    assert prof["time_candidates"][0]["interval"]["label"] == "15min"
    assert any(n["code"] == "XLSX_FORMULA_CACHED" for n in prof["notices"])
    table = (client.ws / "datasets" / d["dataset_id"] / "table.csv").read_text(encoding="utf-8")
    assert "00123" in table and "20.5" in table and "41.0" in table  # 앞자리 0 보존, 표시 형식(0) 아닌 저장값
    m = client.app.state.datasets.get(d["dataset_id"])
    assert m.version == m.xlsx["normalization"]["table_sha256"] != m.xlsx["source_sha256"]  # 표 해시 ≠ 원본 해시


@pytest.mark.parametrize("sheet,code,word", [
    ("결과없는수식", "FORMULA_NO_CACHED_VALUE", "C2"),
    ("오류값", "XLSX_ERROR_VALUE", "B3"),
    ("빈헤더", "HEADER_EMPTY_NAME", "B1"),
    ("중복헤더", "HEADER_DUPLICATE", "B1, C1"),
    ("병합", "XLSX_MERGED_CELLS", "A2:A3"),
    ("빈시트", "SHEET_EMPTY", "다른 시트"),
])
def test_sheet_problems_explained_with_location(client, sheet, code, word):
    d = post_file(client, "센서.xlsx", xlsx_bytes(client.tmp, edge_workbook))
    p = choose(client, d["dataset_id"], sheet)
    assert p["state"] == "PROFILE_FAILED" and p["error"]["code"] == code and word in p["error"]["message_ko"], p["error"]
    assert client.get(f"/api/datasets/{d['dataset_id']}/profile").status_code in (404, 409)
    ok = choose(client, d["dataset_id"], "데이터")  # 같은 데이터셋에서 다른 시트로 바로 다시 분석(초안 없음)
    assert ok["dataset_id"] == d["dataset_id"] and ok["state"] == "PROFILED"


def test_trailing_format_blank_rows_columns_and_hidden_sheet(client):
    d = post_file(client, "센서.xlsx", xlsx_bytes(client.tmp, edge_workbook))
    did = d["dataset_id"]
    p = choose(client, did, "서식끝")
    assert p["row_count"] == 2 and p["column_count"] == 2  # 서식만 있는 5000행·8열은 세지 않음
    p = choose(client, did, "빈행빈열")
    assert p["row_count"] == 2 and p["column_count"] == 2
    assert p["table"]["rows_above_header"] == 2 and p["table"]["blank_rows_skipped"] == 1 and p["table"]["empty_columns_dropped"] == 1
    prof = client.get(f"/api/datasets/{did}/profile").json()
    assert any(n["code"] == "XLSX_BLANK_SKIPPED" for n in prof["notices"])
    assert choose(client, did, "숨김")["state"] == "PROFILED"  # 숨김 시트도 고를 수 있음(표시 상태는 목록에 표시)


def test_1904_date_system(client):
    d = post_file(client, "mac.xlsx", xlsx_bytes(client.tmp, epoch_1904_workbook))
    p = choose(client, d["dataset_id"], "mac")
    assert p["table"]["epoch"] == "1904" and p["state"] == "PROFILED"
    table = (client.ws / "datasets" / d["dataset_id"] / "table.csv").read_text(encoding="utf-8")
    assert "2026-01-02 00:00:00" in table and "2026-01-02 02:00:00" in table  # 1900 기준이면 1462일 어긋남


@pytest.mark.parametrize("name,data,code", [
    ("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 100, "UNSUPPORTED_FILE_TYPE"),
    ("macro.xlsm", b"PK", "UNSUPPORTED_FILE_TYPE"),
    ("locked.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 100, "XLSX_ENCRYPTED_OR_XLS"),
    ("broken.xlsx", b"not a zip at all", "XLSX_INVALID"),
])
def test_unsupported_or_broken_files(client, name, data, code):
    d = post_file(client, name, data)
    d = wait_ds(client, d["dataset_id"])
    assert d["state"] == "UPLOAD_FAILED" and d["error"]["code"] == code, d["error"]
    assert not list((client.ws / "datasets" / d["dataset_id"]).glob("source.*"))


def test_zip_checks_macro_and_uncompressed_size(client, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/vbaProject.bin", b"x")
    d = wait_ds(client, post_file(client, "hidden_macro.xlsx", buf.getvalue())["dataset_id"])
    assert d["error"]["code"] == "XLSX_MACRO"
    from mlworkbench.datasets import xlsx_table
    monkeypatch.setattr(xlsx_table, "MAX_UNCOMPRESSED", 1000)
    d = wait_ds(client, post_file(client, "big.xlsx", xlsx_bytes(client.tmp, edge_workbook))["dataset_id"])
    assert d["error"]["code"] == "XLSX_TOO_LARGE_UNCOMPRESSED"


def test_missing_openpyxl_tells_how_to_install(client, monkeypatch):
    import builtins

    real = builtins.__import__
    data = xlsx_bytes(client.tmp, edge_workbook)  # 차단 전에 테스트 파일을 만든다

    def fake(name, *a, **k):
        if name == "openpyxl" or name.startswith("openpyxl."):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    d = wait_ds(client, post_file(client, "x.xlsx", data)["dataset_id"])
    assert d["error"]["code"] == "XLSX_DEPENDENCY_MISSING" and ".[xlsx]" in d["error"]["message_ko"]
    assert d["state"] == "UPLOAD_FAILED" and d["needs_sheet"] is False  # 목록에 '시트 선택 필요'가 아니라 '업로드 실패'(ADD-UX-05)


# --- 시트 변경 = 새 데이터셋(설정·승인은 원래 데이터셋에 남음) ------------------------------
def test_sheet_change_after_goal_creates_new_dataset(client):
    two = csv_to_xlsx(sensor_csv(288), client.tmp / "two.xlsx", numeric=("sensor_1",))
    import openpyxl
    wb = openpyxl.load_workbook(two)
    copy = wb.copy_worksheet(wb["2026_line1"])
    copy.title = "2026_line2"
    wb.save(two)
    d = post_file(client, "two.xlsx", two.read_bytes())
    did = d["dataset_id"]
    p = choose(client, did, "2026_line1")
    assert client.put(f"/api/datasets/{did}/goal", json=GOAL).status_code == 200
    before = client.app.state.datasets.get(did)
    r = client.post(f"/api/datasets/{did}/sheet", json={"sheet": "2026_line2"}).json()
    new = wait_ds(client, r["dataset_id"])
    assert new["dataset_id"] != did and new["selected_sheet"] == "2026_line2" and new["copied_from"] == did
    assert new["state"] == "PROFILED"
    after = client.app.state.datasets.get(did)
    assert after.selected_sheet == "2026_line1" and after.version == before.version and p["state"] == "PROFILED"
    assert client.get(f"/api/datasets/{did}/goal").json()["saved"] is True  # 원래 데이터셋의 목표 유지
    assert client.get(f"/api/datasets/{new['dataset_id']}/goal").json()["saved"] is False  # 새 시트는 목표부터 다시
    assert client.get(f"/api/datasets/{new['dataset_id']}/plan").status_code == 409


def test_llm_request_has_no_workbook_sheet_formula_or_cells(client):
    data = xlsx_bytes(client.tmp, edge_workbook)
    d = post_file(client, "비밀_공장_엑셀.xlsx", data)
    did = choose(client, d["dataset_id"], "데이터")["dataset_id"]
    goal = {"timestamp_col": "측정시각", "target_col": "온도", "group_col": "설비ID", "horizons": ["15min"]}
    assert client.put(f"/api/datasets/{did}/goal", json=goal).status_code == 200
    assert client.post(f"/api/datasets/{did}/recommendation").status_code == 200
    req = json.dumps(client.app.state.recs.last_request, ensure_ascii=False, default=str)
    for leak in ("비밀_공장_엑셀", "데이터", "측정시각", "온도", "설비ID", "00123", "C2*2", "=C", "source.xlsx", "table.csv",
                 str(client.ws)):
        assert leak not in req, leak
    sp = client.app.state.datasets.get_safe_profile(did)
    assert sp.summary["source_kind"] == "xlsx" and "sheet_names" in sp.hidden_field_kinds


# --- 같은 내용 CSV vs XLSX: 프로파일·학습·예측 일치 ----------------------------------------
def _train(c, did, settings=SETTINGS) -> dict:
    assert c.put(f"/api/datasets/{did}/goal", json=GOAL).status_code == 200
    rid = c.post(f"/api/datasets/{did}/recommendation").json()["recommendation_id"]
    plan = c.post(f"/api/datasets/{did}/plan/apply", json={"recommendation_id": rid, "expected_revision": 1}).json()
    t = c.put(f"/api/datasets/{did}/training", json={**settings, "expected_revision": plan["revision"]}).json()
    v = c.get(f"/api/datasets/{did}/confirmation").json()
    assert v["error_count"] == 0, v["issues"]
    a = c.post(f"/api/datasets/{did}/approve", json={"expected_revision": v["revision"], "confirmed": True}).json()
    run = start(c, did, a["approval"]).json()
    done = wait(c, run["run_id"])
    assert done["state"] == "SUCCEEDED", (done["error_code"], done["error_message_ko"])
    assert t["revision"]
    return done


def test_csv_and_xlsx_same_profile_split_predictions_and_metrics(client):
    raw = sensor_csv(2880)
    c_ds = post_file(client, "sensor_A.csv", raw)
    x_ds = post_file(client, "sensor_A.xlsx", csv_to_xlsx(raw, client.tmp / "a.xlsx", numeric=("sensor_1",)).read_bytes())
    cp = wait_ds(client, c_ds["dataset_id"])
    xp = choose(client, x_ds["dataset_id"], "2026_line1")
    assert (cp["row_count"], cp["column_count"]) == (xp["row_count"], xp["column_count"])
    pc = client.get(f"/api/datasets/{cp['dataset_id']}/profile").json()
    px = client.get(f"/api/datasets/{xp['dataset_id']}/profile").json()
    for a, b in zip(pc["columns"], px["columns"]):
        assert (a["name"], a["dtype"], a["null_count"]) == (b["name"], b["dtype"], b["null_count"])
    ta, tb = pc["time_candidates"][0], px["time_candidates"][0]
    assert (ta["interval"]["label"], ta["parse_rate"], ta["start"], ta["end"]) == (tb["interval"]["label"], tb["parse_rate"], tb["start"], tb["end"])
    rc, rx = _train(client, cp["dataset_id"]), _train(client, xp["dataset_id"])
    for k in ("train_end", "val_end", "train_samples", "val_samples", "test_samples", "train_period", "test_period"):
        assert rc["split"][k] == rx["split"][k], k
    for k in ("mae", "rmse"):
        assert abs(rc["metrics"]["test"]["overall"]["model"][k] - rx["metrics"]["test"]["overall"]["model"][k]) < 1e-9
    pa = pd.read_csv(client.ws / "experiments" / rc["run_id"] / "predictions" / "predictions.csv")
    pb = pd.read_csv(client.ws / "experiments" / rx["run_id"] / "predictions" / "predictions.csv")
    pd.testing.assert_frame_equal(pa, pb)
    # 학습 실행은 승인 당시 표(table.csv 해시)에 묶인다 → 표를 바꾸면 실행 거부
    snap = client.app.state.plans.get_snapshot(rx["plan_id"])
    assert snap.dataset_version == client.app.state.datasets.get(xp["dataset_id"]).version

    # --- S7: 새 엑셀(이후 기간, 다른 시트 이름) → 시트 선택 → 점검 → 예측, 같은 내용 CSV와 결과 일치
    df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    ts = pd.to_datetime(df.timestamp)
    fut = df[ts >= ts.max() - pd.Timedelta(days=2)].copy()
    fut["timestamp"] = (pd.to_datetime(fut.timestamp) + pd.Timedelta(days=30)).dt.strftime("%Y-%m-%d %H:%M:%S")
    fut_csv = fut.to_csv(index=False).encode()
    fut_x = csv_to_xlsx(fut_csv, client.tmp / "f.xlsx", sheet="새기간", numeric=("sensor_1",)).read_bytes()
    run_id = rx["run_id"]
    a = client.post(f"/api/models/{run_id}/applications", content=fut_x, headers={"X-File-Name": "future.xlsx"}).json()
    assert a["state"] == "SHEET_REQUIRED" and [s["name"] for s in a["input"]["sheets"]] == ["새기간", "메모"]
    assert client.post(f"/api/applications/{a['application_id']}/predict").status_code == 409
    a = client.post(f"/api/applications/{a['application_id']}/sheet", json={"sheet": "새기간"}).json()
    assert a["state"] == "CHECKED" and a["input"]["sheet"] == "새기간" and a["input"]["table_sha256"] != a["input"]["sha256"]
    b = client.post(f"/api/models/{run_id}/applications", content=fut_csv, headers={"X-File-Name": "future.csv"}).json()
    assert b["state"] == "CHECKED"
    for app in (a, b):
        client.post(f"/api/applications/{app['application_id']}/predict")
    end = time.time() + 30
    while time.time() < end and any(client.get(f"/api/applications/{x['application_id']}").json()["state"] == "RUNNING"
                                    for x in (a, b)):
        time.sleep(0.1)
    ra = pd.read_csv(io.StringIO(client.get(f"/api/applications/{a['application_id']}/predictions.csv").text))
    rb = pd.read_csv(io.StringIO(client.get(f"/api/applications/{b['application_id']}/predictions.csv").text))
    pd.testing.assert_frame_equal(ra, rb)
    assert (pd.to_datetime(ra.target_time) > pd.to_datetime(fut.timestamp).max()).any()
    # 다른 시트로 바꾸면 새 적용 기록, 이전 기록은 그대로
    other = client.post(f"/api/applications/{a['application_id']}/sheet", json={"sheet": "메모"}).json()
    assert other["application_id"] != a["application_id"] and other["state"] == "CHECK_FAILED"
    assert client.get(f"/api/applications/{a['application_id']}").json()["state"] == "SUCCEEDED"


def test_restart_does_not_profile_xlsx_without_sheet(tmp_path):
    ws = tmp_path / "ws"
    with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c:
        c.tmp = tmp_path
        did = post_file(c, "a.xlsx", xlsx_bytes(tmp_path, edge_workbook))["dataset_id"]
    with TestClient(create_app(ws, serve_frontend=False, profile_in_process=False)) as c2:
        time.sleep(0.3)
        d = c2.get(f"/api/datasets/{did}").json()
        assert d["state"] == "REGISTERED" and d["needs_sheet"] is True
