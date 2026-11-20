"""추가 요구사항 2차(ADD-UX-06 실행 이름, 07 실행 삭제, 02 데이터셋 삭제) 브라우저 검증.

실행: 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>`(실제 Registry) 후
      python tests/e2e/check_delete_browser.py [스크린샷 폴더]
준비(업로드·목표·추천·학습 설정·승인·학습·새 데이터 적용)는 API로 하고, 이름·삭제는 화면에서 확인한다.
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
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-delete"
os.makedirs(SHOTS, exist_ok=True)
GOAL = {"timestamp_col": "timestamp", "target_col": "sensor_1", "group_col": "device_id", "horizons": ["15min", "1h"]}
SET = {"model": {"plugin_id": "timeseries.forecasting.xgboost", "plugin_version": "1.0.0", "params": {"n_estimators": 40}},
       "train_budget": {"mode": "all"},
       "split_policy": {"method": "chronological", "train": 0.7, "val": 0.15, "test": 0.15, "gap": "auto"},
       "evaluation": {"kind": "timeseries", "metrics": ["mae", "rmse"], "baseline": "naive_last_value",
                      "per_group": True, "per_horizon": True}}
results: list[str] = []


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def dataset(api, name):
    data = sensor_csv(2880)
    d = api.post(f"{BASE}/api/datasets", data=data, headers={"X-File-Name": quote(name)}).json()
    did = d["dataset_id"]
    for _ in range(600):
        if api.get(f"{BASE}/api/datasets/{did}").json()["state"] == "PROFILED":
            break
        time.sleep(0.05)
    assert api.put(f"{BASE}/api/datasets/{did}/goal", data=GOAL).ok
    rid = api.post(f"{BASE}/api/datasets/{did}/recommendation").json()["recommendation_id"]
    assert api.post(f"{BASE}/api/datasets/{did}/plan/apply", data={"recommendation_id": rid, "expected_revision": 1}).ok
    return did, data


def train(api, did, settings=SET):
    plan = api.get(f"{BASE}/api/datasets/{did}/plan").json()
    assert api.put(f"{BASE}/api/datasets/{did}/training", data={**settings, "expected_revision": plan["revision"]}).ok
    v = api.get(f"{BASE}/api/datasets/{did}/confirmation").json()
    a = api.post(f"{BASE}/api/datasets/{did}/approve", data={"expected_revision": v["revision"], "confirmed": True}).json()
    run = api.post(f"{BASE}/api/datasets/{did}/runs",
                   data={"plan_id": a["approval"]["plan_id"], "plan_hash": a["approval"]["plan_hash"]}).json()
    for _ in range(600):
        s = api.get(f"{BASE}/api/runs/{run['run_id']}").json()
        if s["state"] in ("SUCCEEDED", "FAILED", "CANCELED"):
            assert s["state"] == "SUCCEEDED", s
            return run["run_id"]
        time.sleep(0.3)
    raise AssertionError("timeout")


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 950}, device_scale_factor=2, locale="ko-KR")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    api = ctx.request

    did1, data1 = dataset(api, "keep_me.csv")
    r1 = train(api, did1)
    r2 = train(api, did1, {**SET, "train_budget": {"mode": "recent_days", "days": 5}})
    app = api.post(f"{BASE}/api/models/{r2}/applications", data=data1, headers={"X-File-Name": "again.csv"}).json()
    assert app["state"] == "CHECKED", app
    assert api.post(f"{BASE}/api/applications/{app['application_id']}/predict").ok
    did2, _ = dataset(api, "remove_me.csv")
    r3 = train(api, did2)
    ok("준비: 데이터셋 2개 · 학습 3개 · 새 데이터 적용 1개(r2)", f"{r1}, {r2}, {r3}")

    # 06 확인·실행 화면에서 시작할 때 이름 붙이기 ------------------------------------------------
    plan = api.get(f"{BASE}/api/datasets/{did1}/plan").json()
    assert api.put(f"{BASE}/api/datasets/{did1}/training",
                   data={**SET, "train_budget": {"mode": "recent_days", "days": 3}, "expected_revision": plan["revision"]}).ok
    page.goto(f"{BASE}/#/confirm/{did1}")
    page.get_by_label(re.compile("^위 설정으로 승인합니다")).check()
    page.get_by_role("button", name="설정 승인").click()
    name_box = page.get_by_label("실행 이름 (선택)")
    expect(name_box).to_be_enabled()
    name_box.fill("최근 3일 실험")
    page.screenshot(path=f"{SHOTS}/00_confirm_name.png")
    page.get_by_role("button", name="학습 시작 ▶").click()
    page.wait_for_url(re.compile(r"#/runs/run_"))
    expect(page.locator("h1.page-title")).to_contain_text("최근 3일 실험 —")
    r0 = page.url.split("#/runs/")[1]
    expect(page.locator(".run-head .status")).to_have_text("완료", timeout=120_000)
    ok("확인·실행에서 '실행 이름 (선택)' 입력 후 학습 시작 → 상세 제목에 이름", r0)

    # 06 실행 이름 ----------------------------------------------------------------------
    page.goto(f"{BASE}/#/runs/{r1}")
    expect(page.locator(".run-head .status")).to_have_text("완료")
    page.get_by_role("button", name="이름 붙이기").click()
    page.get_by_label("실행 이름").fill("lag 기본 · 전체")
    page.get_by_role("button", name="이름 저장").click()
    expect(page.locator("h1.page-title")).to_contain_text("lag 기본 · 전체 —")
    expect(page.get_by_role("button", name="이름 바꾸기")).to_be_visible()
    page.goto(f"{BASE}/#/runs")
    row1 = page.locator(".run-table tbody tr", has_text=r1)
    expect(row1.locator(".run-name")).to_have_text("lag 기본 · 전체")
    row1.get_by_role("checkbox").check()
    page.locator(".run-table tbody tr", has_text=r2).get_by_role("checkbox").check()
    expect(page.locator(".compare .cmp-diff thead")).to_contain_text("lag 기본 · 전체")
    page.goto(f"{BASE}/#/apply")
    expect(page.locator(".model-opt", has_text=r1)).to_contain_text("lag 기본 · 전체")
    page.goto(f"{BASE}/#/runs/{r1}")
    page.get_by_role("button", name="이름 바꾸기").click()
    page.get_by_label("실행 이름").fill("줄\t바꿈")
    page.get_by_role("button", name="이름 저장").click()
    expect(page.locator(".run-name-row .field-error")).to_contain_text("제어 문자")
    page.get_by_role("button", name="취소").click()
    page.screenshot(path=f"{SHOTS}/01_named.png", full_page=True)
    ok("실행 이름 붙이기 → 상세 제목·목록·비교·새 데이터 적용 모델 목록에 표시, 잘못된 이름 안내")

    # 07 실행 삭제(적용 기록 함께) ----------------------------------------------------------
    page.goto(f"{BASE}/#/runs")
    page.locator(".run-table tbody tr", has_text=r2).get_by_role("button", name=re.compile("삭제$")).click()
    dlg = page.get_by_role("alertdialog", name="실행 삭제")
    agree = dlg.get_by_label(re.compile("새 데이터 적용 기록 1개"))
    expect(agree).to_be_visible()
    del_btn = dlg.get_by_role("button", name="삭제", exact=True)
    expect(del_btn).to_be_disabled()
    page.screenshot(path=f"{SHOTS}/02_run_delete_dialog.png")
    agree.check()
    del_btn.click()
    expect(page.locator(".toast", has_text="실행을 삭제했습니다")).to_be_visible()
    expect(page.locator(".run-table tbody tr", has_text=r2)).to_have_count(0)
    assert api.get(f"{BASE}/api/applications/{app['application_id']}").status == 404
    ok("목록에서 실행 삭제 → 적용 기록 함께 삭제 체크 전엔 버튼 비활성 → 삭제·목록에서 사라짐")

    page.goto(f"{BASE}/#/runs/{r1}")
    page.get_by_role("button", name="삭제…").click()
    dlg = page.get_by_role("alertdialog", name="실행 삭제")
    expect(dlg.get_by_role("checkbox")).to_have_count(0)  # 적용 기록 없으면 체크 없이 삭제
    dlg.get_by_role("button", name="취소").click()
    expect(dlg).to_have_count(0)
    ok("상세 화면 삭제… → 적용 기록 없으면 체크 없이 삭제 가능, 취소하면 그대로")

    # 02 데이터셋 삭제(실행·모델 함께) --------------------------------------------------------
    page.goto(f"{BASE}/#/datasets/{did2}")
    page.get_by_role("button", name="데이터셋 삭제…").click()
    dlg = page.get_by_role("alertdialog", name="데이터셋 삭제")
    agree = dlg.get_by_label(re.compile("학습 실행 1개와 모델·예측 결과"))
    expect(dlg.get_by_role("button", name="삭제", exact=True)).to_be_disabled()
    page.screenshot(path=f"{SHOTS}/03_dataset_delete_dialog.png")
    agree.check()
    dlg.get_by_role("button", name="삭제", exact=True).click()
    expect(page.locator(".toast", has_text="데이터셋을 삭제했습니다")).to_be_visible()
    expect(page.locator(".ds-row", has_text="remove_me.csv")).to_have_count(0)
    expect(page.locator(".ds-row", has_text="keep_me.csv")).to_have_count(1)
    assert api.get(f"{BASE}/api/runs/{r3}").status == 404
    assert api.get(f"{BASE}/api/runs/{r1}").json()["state"] == "SUCCEEDED"
    page.screenshot(path=f"{SHOTS}/04_after_dataset_delete.png")
    ok("데이터셋 삭제 → 실행 함께 삭제 체크 → 목록에서 사라짐, 다른 데이터셋·실행은 그대로")

    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(f"{BASE}/#/runs/{r1}")
    page.get_by_role("button", name="삭제…").click()
    time.sleep(0.3)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=f"{SHOTS}/05_narrow_390.png")
    ok("좁은 창 390px 삭제 확인 창", "가로 넘침 없음")

    app_errors = [e for e in errors if not re.search(r"status of (400|409|422)", e)]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음")
    browser.close()

print("\n".join(results))
