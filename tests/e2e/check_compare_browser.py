"""S6 실험 비교 브라우저 검증: 같은 데이터로 학습량만 다른 실행 2개 + 분할이 다른 실행 1개 →
목록 체크 → 조건 차이·지표·실제 vs 예측 그래프 → 평가 조건 다른 실행 경고 → '같은 평가 조건만' 필터.

실행: 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>`(실제 Registry) 후
      python tests/e2e/check_compare_browser.py [스크린샷 폴더]
준비(업로드·목표·추천·학습 설정·승인·학습)는 API로 하고, 비교 화면은 브라우저로 확인한다.
"""

import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from test_csv_dataset import sensor_csv  # noqa: E402

BASE = os.environ.get("MLWB_BASE_URL", "http://127.0.0.1:8000")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-compare"
os.makedirs(SHOTS, exist_ok=True)
GOAL = {"timestamp_col": "timestamp", "target_col": "sensor_1", "group_col": "device_id", "horizons": ["15min", "1h"]}
BASE_SET = {"model": {"plugin_id": "timeseries.forecasting.xgboost", "plugin_version": "1.0.0",
                      "params": {"n_estimators": 60, "max_depth": 4}},
            "train_budget": {"mode": "recent_days", "days": 5},
            "split_policy": {"method": "chronological", "train": 0.7, "val": 0.15, "test": 0.15, "gap": "auto"},
            "evaluation": {"kind": "timeseries", "metrics": ["mae", "rmse"], "baseline": "naive_last_value",
                           "per_group": True, "per_horizon": True}}
results: list[str] = []


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def train(api, did, settings):
    plan = api.get(f"{BASE}/api/datasets/{did}/plan").json()
    t = api.put(f"{BASE}/api/datasets/{did}/training", data={**settings, "expected_revision": plan["revision"]})
    assert t.ok, t.text()
    v = api.get(f"{BASE}/api/datasets/{did}/confirmation").json()
    a = api.post(f"{BASE}/api/datasets/{did}/approve", data={"expected_revision": v["revision"], "confirmed": True}).json()
    run = api.post(f"{BASE}/api/datasets/{did}/runs",
                   data={"plan_id": a["approval"]["plan_id"], "plan_hash": a["approval"]["plan_hash"]}).json()
    for _ in range(600):
        s = api.get(f"{BASE}/api/runs/{run['run_id']}").json()
        if s["state"] in ("SUCCEEDED", "FAILED", "CANCELED"):
            assert s["state"] == "SUCCEEDED", s
            return s
        time.sleep(0.3)
    raise AssertionError("timeout")


def row(page, run_id):
    return page.locator(".run-table tbody tr", has_text=run_id)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 950}, device_scale_factor=2, locale="ko-KR")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    api = ctx.request

    d = api.post(f"{BASE}/api/datasets", data=sensor_csv(2880), headers={"X-File-Name": quote("sensor_cmp.csv")}).json()
    did = d["dataset_id"]
    for _ in range(600):
        if api.get(f"{BASE}/api/datasets/{did}").json()["state"] == "PROFILED":
            break
        time.sleep(0.05)
    assert api.put(f"{BASE}/api/datasets/{did}/goal", data=GOAL).ok
    rid = api.post(f"{BASE}/api/datasets/{did}/recommendation").json()["recommendation_id"]
    assert api.post(f"{BASE}/api/datasets/{did}/plan/apply", data={"recommendation_id": rid, "expected_revision": 1}).ok
    a = train(api, did, BASE_SET)
    b = train(api, did, {**BASE_SET, "train_budget": {"mode": "all"}})
    c = train(api, did, {**BASE_SET, "split_policy": {**BASE_SET["split_policy"], "train": 0.6, "val": 0.2, "test": 0.2}})
    ok("준비: 같은 데이터로 학습 3개(최근 5일 / 전체 / 분할 60·20·20)", ", ".join(x["run_id"] for x in (a, b, c)))

    # 1. 목록: 전처리·예측·소요 열, 체크 → 기준 ----------------------------------------------
    page.goto(f"{BASE}/#/runs")
    expect(page.locator(".run-table tbody tr")).to_have_count(3)
    expect(row(page, a["run_id"])).to_contain_text("lag 피처")
    expect(row(page, a["run_id"])).to_contain_text("15분, 1시간")
    row(page, a["run_id"]).get_by_role("checkbox").check()
    expect(row(page, a["run_id"])).to_contain_text("기준")
    expect(page.locator(".compare")).to_have_count(0)  # 하나만 고르면 비교 없음
    row(page, b["run_id"]).get_by_role("checkbox").check()
    cmp_ = page.locator(".compare")
    expect(cmp_).to_contain_text("실험 비교 · 2개")
    ok("목록에서 체크 2개 → 비교 패널, 첫 선택 = 기준", "전처리·예측 시점·소요 열 표시")

    # 2. 조건 차이·지표·그래프 ----------------------------------------------------------------
    diff = cmp_.locator(".cmp-diff tbody tr")
    expect(diff).to_have_count(1)
    expect(diff.first).to_contain_text("학습량")
    expect(diff.first).to_contain_text("최근 5일")
    expect(diff.first).to_contain_text("전체")
    expect(cmp_.locator(".banner-warn")).to_have_count(0)
    mt = cmp_.locator(".cmp-metrics tbody tr")
    expect(mt).to_have_count(3)  # 전체 + 15분 + 1시간
    expect(mt.first.locator("strong")).to_have_count(1)  # 같은 조건 중 최고 하나 굵게
    want = min(a["metrics"]["test"]["overall"]["model"]["mae"], b["metrics"]["test"]["overall"]["model"]["mae"])
    assert abs(float(mt.first.locator("strong").inner_text()) - want) < 1e-3
    chart = cmp_.locator(".cmp-chart svg")
    expect(chart.locator("path.line-pred")).to_have_count(2)
    expect(chart.locator("path.line-actual")).to_have_count(1)
    chart.scroll_into_view_if_needed()
    box = chart.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5)
    expect(cmp_.locator(".cmp-tip")).to_contain_text("실제")
    cmp_.get_by_label("그래프 설비").select_option("D02")
    expect(cmp_.locator(".cmp-controls").last).to_contain_text("점")
    cmp_.get_by_label("설비별도 보기").check()
    expect(mt).to_have_count(5)
    page.screenshot(path=f"{SHOTS}/01_same_protocol.png", full_page=True)
    ok("조건 차이 = 학습량만, 지표(전체·예측 시점별·설비별, 최고값 굵게) = 저장 지표, 실제 vs 예측 그래프·마우스 값", f"최저 MAE {want:.3f}")

    # 3. 평가 조건이 다른 실행 ---------------------------------------------------------------
    expect(row(page, c["run_id"])).to_contain_text("⚠ 평가 조건 다름")
    expect(row(page, b["run_id"]).locator(".cmp-flag")).to_have_count(0)
    row(page, c["run_id"]).get_by_role("checkbox").check()
    expect(cmp_).to_contain_text("실험 비교 · 3개")
    warn = cmp_.locator(".banner-warn")
    expect(warn).to_contain_text("평가 조건이 기준과 다른 실행")
    expect(warn).to_contain_text("분할 비율·gap")
    expect(warn).to_contain_text("테스트 표본")
    expect(mt.first.locator("td").nth(2)).to_contain_text("⚠")
    page.screenshot(path=f"{SHOTS}/02_protocol_warning.png", full_page=True)
    ok("분할이 다른 실행: 목록 '⚠ 평가 조건 다름'·비교 경고(분할·테스트 표본)·최고값 표시에서 제외")

    page.get_by_label(re.compile("기준과 같은 평가 조건만")).check()
    expect(page.locator(".run-table tbody tr")).to_have_count(2)
    expect(row(page, c["run_id"])).to_have_count(0)
    ok("'기준과 같은 평가 조건만' 필터 → 2개")

    page.set_viewport_size({"width": 390, "height": 900})
    time.sleep(0.3)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=f"{SHOTS}/03_narrow_390.png", full_page=True)
    ok("좁은 창 390px", "가로 넘침 없음(표는 표 안에서 가로 스크롤)")

    app_errors = [e for e in errors if not re.search(r"status of (409|422)", e)]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음")
    browser.close()

print("\n".join(results))
