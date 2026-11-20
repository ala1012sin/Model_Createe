"""XLSX 브라우저 검증: 업로드 → 시트 선택 → S1~S5 학습 → S7 새 엑셀(시트 선택) 예측 → 같은 내용 CSV와 비교.

준비: python tests/e2e/make_future_csv.py && python tests/e2e/make_xlsx_examples.py
실행: 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>` 후 python tests/e2e/check_xlsx_browser.py [스크린샷 폴더]
"""

import io
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("MLWB_BASE_URL", "http://127.0.0.1:8000")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-xlsx"
os.makedirs(SHOTS, exist_ok=True)
DATA = ROOT / "workspace" / "e2e-data"
CSV = Path(os.environ.get("MLWB_EXAMPLE_CSV") or ROOT / "outputs" / "workbench-sensor-example" / "motor_sensor_example.csv")
FUT_CSV = Path(os.environ.get("MLWB_FUTURE_CSV") or DATA / "motor_sensor_future.csv")
XLSX = Path(os.environ.get("MLWB_EXAMPLE_XLSX") or DATA / "motor_sensor_example.xlsx")
FUT_XLSX = Path(os.environ.get("MLWB_FUTURE_XLSX") or DATA / "motor_sensor_future.xlsx")
for f in (CSV, FUT_CSV, XLSX, FUT_XLSX):
    assert f.exists(), f
GOAL = {"timestamp_col": "timestamp", "target_col": "temperature_c", "group_col": "device_id", "horizons": ["15min", "30min"]}
results: list[str] = []


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def wait_api(api, url, pred, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        v = api.get(url).json()
        if pred(v):
            return v
        time.sleep(0.3)
    raise AssertionError(url)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 950}, device_scale_factor=2, locale="ko-KR",
                              accept_downloads=True)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    nav = page.get_by_role("navigation", name="주 메뉴")
    api = ctx.request

    # 1. XLSX 업로드 → 시트 선택 → 구조 ------------------------------------------------------
    page.goto(BASE)
    expect(page.locator(".dropzone")).to_contain_text("엑셀(.xlsx)")
    page.set_input_files("input[type=file]", str(XLSX))
    row = page.locator(".ds-row").first
    expect(row.locator(".status")).to_have_text("시트 선택 필요", timeout=30_000)
    panel = page.locator(".col-right")
    picker = panel.locator(".sheet-picker")
    expect(picker.locator(".sheet-opt")).to_have_count(2)
    expect(picker).to_contain_text("2026_line1")
    expect(picker).to_contain_text("메모")
    page.screenshot(path=f"{SHOTS}/01_sheet_picker.png", full_page=True)
    picker.locator(".sheet-opt", has_text="2026_line1").locator("input").check()
    picker.get_by_role("button", name="이 시트로 분석").click()
    expect(row.locator(".status")).to_have_text("완료", timeout=60_000)
    expect(row).to_contain_text("· 2026_line1")
    expect(panel.locator(".sheet-bar")).to_contain_text("시트 2026_line1")
    expect(panel.locator(".shape")).to_contain_text("11,520")
    expect(panel.locator(".shape")).to_contain_text("7")
    expect(panel.locator(".axis")).to_contain_text("timestamp")
    xid = page.url.split("#/datasets/")[1]
    page.screenshot(path=f"{SHOTS}/02_profiled.png", full_page=True)
    ok("XLSX 업로드 → 시트 선택(2개 중 2026_line1) → 구조", "11,520행 × 7열, 시간열 timestamp")

    # 같은 내용 CSV와 프로파일 비교(API)
    c = api.post(f"{BASE}/api/datasets", data=CSV.read_bytes(), headers={"X-File-Name": quote(CSV.name)}).json()
    cid = c["dataset_id"]
    wait_api(api, f"{BASE}/api/datasets/{cid}", lambda v: v["state"] == "PROFILED")
    pc, px = api.get(f"{BASE}/api/datasets/{cid}/profile").json(), api.get(f"{BASE}/api/datasets/{xid}/profile").json()
    assert (pc["row_count"], pc["column_count"]) == (px["row_count"], px["column_count"])
    for a, b in zip(pc["columns"], px["columns"]):
        assert (a["name"], a["dtype"], a["null_count"]) == (b["name"], b["dtype"], b["null_count"]), (a["name"], b["name"])
    ta, tb = pc["time_candidates"][0], px["time_candidates"][0]
    assert ta["interval"]["label"] == tb["interval"]["label"] == "15min" and ta["start"][:16] == tb["start"][:16]
    ok("같은 내용 CSV와 구조 일치", "행·열·열별 타입·결측 수·시간 간격·시작 시각")

    # 2. S1 목표 → S2 → S3 최근 30일·70/15/15·MAE·RMSE → S4 승인 → S5 -----------------------------
    page.locator(".ds-select", has_text="2026_line1").click()
    form = page.locator("form.goal")
    form.get_by_label("예측 타깃").select_option("temperature_c")
    form.get_by_label("설비 구분 열 (선택)").select_option("device_id")
    form.get_by_role("button", name="15분").click()
    form.get_by_role("button", name="30분").click()
    form.get_by_role("button", name="목표 저장").click()
    expect(page.locator(".toast")).to_contain_text("목표를 저장했습니다")  # 저장 후 전처리로 자동 이동(ADD-UX-09)
    page.wait_for_url(re.compile(r"#/preprocess/"))
    nav.get_by_role("link", name=re.compile("^전처리")).click()
    expect(page.locator("ol.cards .card")).to_have_count(4, timeout=15_000)
    page.get_by_role("button", name=re.compile("학습 설정으로$")).click()
    page.locator("input[name=budget]").nth(1).check()
    page.get_by_label("최근 일수").fill("30")
    page.get_by_role("button", name=re.compile("확인·실행")).click()
    expect(page.locator(".conf-summary")).to_contain_text("motor_sensor_example.xlsx")
    page.get_by_label(re.compile("^위 설정으로 승인합니다")).check()
    page.get_by_role("button", name="설정 승인").click()
    page.get_by_role("button", name="학습 시작 ▶").click()
    page.wait_for_url(re.compile(r"#/runs/run_"))
    xrun = page.url.split("#/runs/")[1]
    expect(page.locator(".run-head .status")).to_have_text("완료", timeout=180_000)
    rx = api.get(f"{BASE}/api/runs/{xrun}").json()
    ok("S1 목표 → S2 → S3(최근 30일·70/15/15·MAE·RMSE) → S4 승인 → S5 완료",
       f"테스트 MAE {rx['metrics']['test']['overall']['model']['mae']:.3f}")

    # 같은 설정으로 CSV 학습(API) → 분할·지표·예측 비교
    assert api.put(f"{BASE}/api/datasets/{cid}/goal", data=GOAL).ok
    rid = api.post(f"{BASE}/api/datasets/{cid}/recommendation").json()["recommendation_id"]
    plan = api.post(f"{BASE}/api/datasets/{cid}/plan/apply", data={"recommendation_id": rid, "expected_revision": 1}).json()
    tx = api.get(f"{BASE}/api/datasets/{xid}/training").json()
    t = api.put(f"{BASE}/api/datasets/{cid}/training", data={k: tx[k] for k in ("model", "train_budget", "split_policy", "evaluation")}
                | {"expected_revision": plan["revision"]})
    assert t.ok, t.text()
    v = api.get(f"{BASE}/api/datasets/{cid}/confirmation").json()
    ap = api.post(f"{BASE}/api/datasets/{cid}/approve", data={"expected_revision": v["revision"], "confirmed": True}).json()
    crun = api.post(f"{BASE}/api/datasets/{cid}/runs", data={"plan_id": ap["approval"]["plan_id"],
                                                              "plan_hash": ap["approval"]["plan_hash"]}).json()["run_id"]
    rc = wait_api(api, f"{BASE}/api/runs/{crun}", lambda r: r["state"] in ("SUCCEEDED", "FAILED"), timeout=180)
    assert rc["state"] == "SUCCEEDED"
    for k in ("train_end", "val_end", "train_samples", "test_samples", "train_period"):
        assert rc["split"][k] == rx["split"][k], k
    dm = max(abs(rc["metrics"]["test"]["overall"]["model"][m] - rx["metrics"]["test"]["overall"]["model"][m]) for m in ("mae", "rmse"))
    pa = pd.read_csv(io.StringIO(api.get(f"{BASE}/api/runs/{crun}/predictions.csv").text()))
    pb = pd.read_csv(io.StringIO(api.get(f"{BASE}/api/runs/{xrun}/predictions.csv").text()))
    dp = float((pa.predicted - pb.predicted).abs().max())
    assert dm < 1e-9 and len(pa) == len(pb) and dp < 1e-9, (dm, dp)
    ok("같은 내용 CSV로 같은 설정 학습 → 분할·지표·예측 일치", f"지표 차이 {dm:.1e}, 예측 {len(pa)}행 최대 차이 {dp:.1e}")

    # 3. S7: 새 엑셀(시트 이름 다름) → 시트 선택 → 점검 → 예측 → 다운로드, 같은 내용 CSV와 비교 ------------------
    page.get_by_role("link", name="이 모델로 새 데이터에 적용 →").click()
    page.locator(".apply input[type=file]").set_input_files(str(FUT_XLSX))
    expect(page.locator(".ap-state .status")).to_have_text("시트 선택 필요")
    sp = page.locator(".apply .sheet-picker")
    expect(sp.locator(".sheet-opt")).to_have_count(2)
    expect(page.get_by_role("button", name="예측 실행")).to_be_disabled()
    sp.locator(".sheet-opt", has_text="2026_11").locator("input").check()
    sp.get_by_role("button", name="이 시트로 점검").click()
    expect(page.locator(".ap-state .status")).to_have_text("점검 통과")
    expect(page.locator(".ap-state")).to_contain_text("시트 2026_11")
    expect(page.locator(".ap-check")).to_contain_text("2026-10-31 00:00 ~ 2026-11-06 23:45")
    page.get_by_role("button", name="예측 실행").click()
    expect(page.locator(".ap-state .status")).to_have_text("예측 완료", timeout=60_000)
    app_x = page.url.split("#/apply/")[1]
    with page.expect_download() as dl:
        page.get_by_role("link", name="예측 결과 CSV 내려받기").click()
    gx = pd.read_csv(dl.value.path())
    rec = api.get(f"{BASE}/api/applications/{app_x}").json()
    assert rec["input"]["kind"] == "xlsx" and rec["input"]["sheet"] == "2026_11" and rec["input"]["table_sha256"] != rec["input"]["sha256"]
    assert set(gx.model_run_id) == {xrun} and list(gx.columns)[:2] == ["model_run_id", "model_plan_hash"]
    page.screenshot(path=f"{SHOTS}/03_s7_xlsx.png", full_page=True)
    ac = api.post(f"{BASE}/api/models/{xrun}/applications", data=FUT_CSV.read_bytes(),
                  headers={"X-File-Name": FUT_CSV.name}).json()
    assert ac["state"] == "CHECKED"
    api.post(f"{BASE}/api/applications/{ac['application_id']}/predict")
    wait_api(api, f"{BASE}/api/applications/{ac['application_id']}", lambda r: r["state"] == "SUCCEEDED", timeout=60)
    gc = pd.read_csv(io.StringIO(api.get(f"{BASE}/api/applications/{ac['application_id']}/predictions.csv").text()))
    pd.testing.assert_frame_equal(gx, gc)
    ok("S7 새 엑셀(시트 2026_11) 시트 선택 → 점검 → 예측 → CSV 다운로드", f"{len(gx)}건, 같은 내용 CSV 적용 결과와 동일")

    # 4. 다른 시트로 바꾸면 새 데이터셋, 원래 데이터셋의 목표·실행 유지 ------------------------------------------
    page.goto(f"{BASE}/#/datasets/{xid}")
    panel.locator(".sheet-bar").get_by_role("button", name="다른 시트").click()
    expect(panel.locator(".sheet-picker")).to_contain_text("새 데이터셋으로 등록")
    panel.locator(".sheet-opt", has_text="메모").locator("input").check()
    panel.get_by_role("button", name="이 시트로 분석").click()
    page.wait_for_url(re.compile(r"#/datasets/(?!" + xid + ")ds_"))
    new_id = page.url.split("#/datasets/")[1]
    nd = wait_api(api, f"{BASE}/api/datasets/{new_id}", lambda d: d["state"] in ("PROFILED", "PROFILE_FAILED"))
    assert nd["selected_sheet"] == "메모" and nd["copied_from"] == xid
    assert api.get(f"{BASE}/api/datasets/{xid}/goal").json()["saved"] is True
    assert api.get(f"{BASE}/api/datasets/{xid}").json()["selected_sheet"] == "2026_line1"
    assert api.get(f"{BASE}/api/runs/{xrun}").json()["state"] == "SUCCEEDED"
    ok("학습 후 다른 시트 선택 → 새 데이터셋", f"원래 데이터셋 목표·시트·실행 유지, 새 데이터셋 {nd['state']}")

    # 5. 좁은 창: 시트 선택 화면 --------------------------------------------------------------------
    page.set_viewport_size({"width": 390, "height": 900})
    page.set_input_files("input[type=file]", str(FUT_XLSX))
    expect(page.locator(".ds-row .status", has_text="시트 선택 필요").first).to_be_visible(timeout=30_000)
    page.locator(".ds-row", has_text="시트 선택 필요").first.locator(".ds-select").click()
    expect(page.locator(".col-right .sheet-picker")).to_be_visible()
    time.sleep(0.3)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=f"{SHOTS}/04_narrow_390.png", full_page=True)
    ok("좁은 창 390px 시트 선택", "가로 넘침 없음")

    app_errors = [e for e in errors if not re.search(r"status of (409|422)", e)]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음")
    browser.close()

print("\n".join(results))
