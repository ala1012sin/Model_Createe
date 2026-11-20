"""S7 새 데이터 적용 브라우저 검증 (Playwright/Chromium, 실제 XGBoost 모델로 추론).

사전 준비: pip install -e ".[dev,timeseries]" playwright && python -m playwright install chromium
실행: 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>`(실제 내장 Registry) 실행 후
      python tests/e2e/make_future_csv.py          # 이후 기간 합성 CSV 생성(생성 기준은 그 파일 설명)
      python tests/e2e/check_s7_browser.py [스크린샷 폴더]
예제 CSV는 MLWB_EXAMPLE_CSV, 이후 기간 CSV는 MLWB_FUTURE_CSV로 바꿀 수 있다.
학습(설정·승인)은 API로 준비하고, S5 → S7 → 점검 → 예측 → 내려받기는 화면으로 확인한다.
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
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-s7"
os.makedirs(SHOTS, exist_ok=True)
DATA = Path(os.environ.get("MLWB_EXAMPLE_CSV") or ROOT / "outputs" / "workbench-sensor-example" / "motor_sensor_example.csv")
FUTURE = Path(os.environ.get("MLWB_FUTURE_CSV") or ROOT / "workspace" / "e2e-data" / "motor_sensor_future.csv")
assert DATA.exists() and FUTURE.exists(), "예제 CSV와 이후 기간 CSV가 필요합니다(make_future_csv.py)"
TMP = Path(SHOTS) / "_inputs"
TMP.mkdir(exist_ok=True)
results: list[str] = []
GOAL = {"timestamp_col": "timestamp", "target_col": "temperature_c", "group_col": "device_id", "horizons": ["15min", "30min"]}
TRAIN = {"model": {"plugin_id": "timeseries.forecasting.xgboost", "plugin_version": "1.0.0",
                   "params": {"n_estimators": 300, "max_depth": 6}},
         "split_policy": {"method": "chronological", "train": 0.7, "val": 0.15, "test": 0.15, "gap": "auto"},
         "evaluation": {"kind": "timeseries", "metrics": ["mae", "rmse"], "baseline": "naive_last_value",
                        "per_group": True, "per_horizon": True}}


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def train(api, budget) -> str:
    """API로 S1~S4를 거쳐 학습을 완료하고 run_id를 돌려준다."""
    r = api.post(f"{BASE}/api/datasets", data=DATA.read_bytes(), headers={"X-File-Name": quote(DATA.name)}).json()
    did = r["dataset_id"]
    for _ in range(300):
        if api.get(f"{BASE}/api/datasets/{did}").json()["state"] == "PROFILED":
            break
        time.sleep(0.2)
    assert api.put(f"{BASE}/api/datasets/{did}/goal", data=GOAL).ok
    rid = api.post(f"{BASE}/api/datasets/{did}/recommendation").json()["recommendation_id"]
    plan = api.post(f"{BASE}/api/datasets/{did}/plan/apply", data={"recommendation_id": rid, "expected_revision": 1}).json()
    t = api.put(f"{BASE}/api/datasets/{did}/training", data={**TRAIN, "train_budget": budget, "expected_revision": plan["revision"]})
    assert t.ok, t.text()
    v = api.get(f"{BASE}/api/datasets/{did}/confirmation").json()
    a = api.post(f"{BASE}/api/datasets/{did}/approve", data={"expected_revision": v["revision"], "confirmed": True}).json()
    run = api.post(f"{BASE}/api/datasets/{did}/runs", data={"plan_id": a["approval"]["plan_id"],
                                                            "plan_hash": a["approval"]["plan_hash"]}).json()
    for _ in range(600):
        s = api.get(f"{BASE}/api/runs/{run['run_id']}").json()
        if s["state"] in ("SUCCEEDED", "FAILED", "CANCELED"):
            assert s["state"] == "SUCCEEDED", s
            return run["run_id"]
        time.sleep(0.3)
    raise AssertionError("학습 시간 초과")


def upload(page, path: Path) -> None:
    page.locator(".apply input[type=file]").set_input_files(str(path))


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

    # 0. 모델이 없을 때 --------------------------------------------------------------
    page.goto(BASE)
    nav.get_by_role("link", name="새 데이터 적용", exact=True).click()
    expect(page.get_by_role("heading", name="적용할 수 있는 모델이 없습니다")).to_be_visible()
    ok("완료 모델이 없을 때 안내", "실행·결과로 이동 버튼")

    # 1. 학습 준비(API) → S5 → S7 ------------------------------------------------------
    run1 = train(api, {"mode": "recent_days", "days": 30})
    page.goto(f"{BASE}/#/runs/{run1}")
    expect(page.locator(".run-head .status")).to_have_text("완료")
    page.get_by_role("link", name="이 모델로 새 데이터에 적용 →").click()
    expect(page.get_by_role("heading", name="새 데이터 적용", level=1)).to_be_visible()
    assert f"#/apply/{run1}" in page.url
    expect(nav.locator('[aria-current="page"]')).to_contain_text("새 데이터 적용")
    expect(nav.get_by_role("link", name="학습 설정", exact=True)).to_have_attribute("href", re.compile("^#/train/ds_"))
    expect(page.locator(".model-opt.is-selected")).to_contain_text(run1)
    req = page.locator(".req-table tbody")
    for col in ("timestamp", "device_id", "temperature_c", "ambient_temp_c", "motor_current_a", "vibration_mm_s"):
        expect(req).to_contain_text(col)
    expect(req.locator("tr", has_text="temperature_c")).to_contain_text("lag")
    expect(page.locator("#ap-req ~ dl, .conf-summary").first).to_contain_text("8칸")
    page.screenshot(path=f"{SHOTS}/01_requirements.png", full_page=True)
    ok("S5 완료 실행 → '이 모델로 새 데이터에 적용' → S7", "필요한 열·과거 타깃(lag)·이력 8칸·시각 형식 표시")

    # 2. 입력 점검 실패: 타깃 열 없음 -------------------------------------------------------
    bad = pd.read_csv(FUTURE, dtype=str, keep_default_na=False).drop(columns=["temperature_c"])
    bad_path = TMP / "future_no_target.csv"
    bad.to_csv(bad_path, index=False)
    upload(page, bad_path)
    expect(page.locator(".ap-state .status")).to_have_text("점검 실패")
    expect(page.locator(".ap-state .field-error")).to_contain_text("필요한 열이 없습니다: temperature_c")
    expect(page.get_by_role("button", name="예측 실행")).to_be_disabled()
    ok("빠진 열 점검", "'필요한 열이 없습니다: temperature_c', 예측 실행 비활성")

    # 3. 이후 기간 CSV: 점검 → 예측 → 결과 → 내려받기 ------------------------------------------
    upload(page, FUTURE)
    expect(page.locator(".ap-state .status")).to_have_text("점검 통과")
    app1 = page.url.split("#/apply/")[1]
    assert app1.startswith("app_")
    chk = page.locator(".ap-check")
    expect(chk).to_contain_text("2026-10-31 00:00 ~ 2026-11-06 23:45")
    expect(chk).to_contain_text("MOTOR-1, MOTOR-2")
    expect(chk).to_contain_text("마지막 관측 이후 시각")
    expect(chk).to_contain_text("이력 부족")
    expect(chk).to_contain_text("operating_mode")
    page.get_by_role("button", name="예측 실행").click()
    expect(page.locator(".ap-state .status")).to_have_text("예측 완료", timeout=60_000)
    rec = api.get(f"{BASE}/api/applications/{app1}").json()
    res = rec["result"]
    expect(page.locator("#ap-run ~ dl").first).to_contain_text(f"{res['rows']:,}건")
    expect(page.locator(".apply .metric-table tbody tr")).to_have_count(20)
    page.screenshot(path=f"{SHOTS}/02_predicted.png", full_page=True)
    with page.expect_download() as dl:
        page.get_by_role("link", name="예측 결과 CSV 내려받기").click()
    got = pd.read_csv(dl.value.path())
    assert list(got.columns) == ["model_run_id", "model_plan_hash", "group", "origin_time", "target_time", "horizon", "predicted"]
    assert len(got) == res["rows"] and set(got.model_run_id) == {run1} and set(got.model_plan_hash) == {rec["plan_hash"]}
    assert set(got.group) == {"MOTOR-1", "MOTOR-2"} and set(got.horizon) == {"15min", "30min"}
    assert (pd.to_datetime(got.target_time) > pd.Timestamp("2026-11-06 23:45")).sum() == res["beyond_last_observation"] > 0
    assert got.predicted.between(-50, 150).all()
    ok("이후 기간 CSV 점검 → 예측 → 내려받기",
       f"{res['rows']}건, 설비 2, horizon 15·30분, 마지막 관측 이후 {res['beyond_last_observation']}건, 메타데이터 열 확인")

    # 4. 새로고침 후 복원 -------------------------------------------------------------------
    page.reload()
    expect(page.locator(".ap-state .status")).to_have_text("예측 완료")
    expect(page.get_by_role("link", name="예측 결과 CSV 내려받기")).to_be_visible()
    expect(page.locator(".model-opt.is-selected")).to_contain_text(run1)
    ok("새로고침 후 적용 기록 복원", "입력 파일·모델·결과·내려받기")

    # 5. 같은 원본을 다시 적용 → 저장 평가 예측과 조인 비교 ----------------------------------------
    upload(page, DATA)
    expect(page.locator(".ap-state .status")).to_have_text("점검 통과")
    app2 = page.url.split("#/apply/")[1]
    page.get_by_role("button", name="예측 실행").click()
    expect(page.locator(".ap-state .status")).to_have_text("예측 완료", timeout=60_000)
    new = pd.read_csv(io.StringIO(api.get(f"{BASE}/api/applications/{app2}/predictions.csv").text()))
    saved = pd.read_csv(io.StringIO(api.get(f"{BASE}/api/runs/{run1}/predictions.csv").text()))
    m = saved.merge(new, on=["group", "origin_time", "target_time", "horizon"], suffixes=("", "_new"))
    diff = float((m.predicted - m.predicted_new).abs().max())
    assert len(m) >= 0.99 * len(saved) and diff < 1e-6, (len(m), len(saved), diff)
    ok("학습 원본 재적용 = 저장된 평가 예측", f"조인 {len(m)}/{len(saved)}행, 최대 차이 {diff:.1e}")

    # 6. 모델 변경 → 같은 파일 재점검(새 기록), 이전 기록 유지 -------------------------------------
    run2 = train(api, {"mode": "all"})
    page.reload()
    page.locator(".model-opt", has_text=run2).locator("input").check()
    expect(page.locator(".banner-warn")).to_contain_text("다른 모델")
    expect(page.locator(".apply button:has-text('예측 실행'):enabled")).to_have_count(0)
    expect(page.get_by_role("link", name="예측 결과 CSV 내려받기")).to_have_count(0)
    page.get_by_role("button", name="이 파일로 다시 점검").click()
    expect(page.locator(".ap-state .status")).to_have_text("점검 통과")
    app3 = page.url.split("#/apply/")[1]
    assert app3 not in (app1, app2)
    hist = page.locator(".apply .run-table").last
    for a in (app1, app2, app3):
        expect(hist).to_contain_text(a)
    old = api.get(f"{BASE}/api/applications/{app1}").json()
    assert old["state"] == "SUCCEEDED" and old["run_id"] == run1
    ok("모델 변경 시 이전 점검·결과와 구분", "경고 → '이 파일로 다시 점검' → 새 적용 기록, 이전 기록 유지")

    # 7. 원본 실행 불변·학습 실행 수 ---------------------------------------------------------
    runs = api.get(f"{BASE}/api/runs").json()["items"]
    assert len(runs) == 2
    ok("적용으로 학습 실행이 늘지 않음", "실행 2건(학습 2회분)")

    # 8. 좁은 창 ---------------------------------------------------------------------------
    page.goto(f"{BASE}/#/apply/{app1}")
    expect(page.locator(".ap-state .status")).to_have_text("예측 완료")
    for w, h, tag in ((820, 1000, "03_narrow_820"), (390, 900, "04_narrow_390")):
        page.set_viewport_size({"width": w, "height": h})
        time.sleep(0.3)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), w
        page.screenshot(path=f"{SHOTS}/{tag}.png", full_page=True)
    ok("좁은 창 820·390px", "가로 넘침 없음(표는 자체 스크롤)")

    app_errors = [e for e in errors if not re.search(r"status of (409|422)", e)]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음")
    browser.close()

print("\n".join(results))
