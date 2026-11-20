"""최소 대기열 브라우저 검증: 학습 중에 두 번째 실행 제출 → S5 '대기 중 · 앞에 1개'·목록 순번 → 대기 실행 취소(즉시)
→ 세 번째 제출 → 앞 실행 취소 후 자동 시작·완료.

실행: 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>`(실제 Registry) 후
      python tests/e2e/check_queue_browser.py [스크린샷 폴더]
준비(업로드·목표·추천·학습 설정·승인)는 API로 하고, 대기열 표시·취소는 화면에서 확인한다.
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
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-queue"
os.makedirs(SHOTS, exist_ok=True)
GOAL = {"timestamp_col": "timestamp", "target_col": "sensor_1", "group_col": "device_id", "horizons": ["15min", "1h"]}
MODEL = {"plugin_id": "timeseries.forecasting.xgboost", "plugin_version": "1.0.0"}
COMMON = {"train_budget": {"mode": "all"},
          "split_policy": {"method": "chronological", "train": 0.7, "val": 0.15, "test": 0.15, "gap": "auto"},
          "evaluation": {"kind": "timeseries", "metrics": ["mae", "rmse"], "baseline": "naive_last_value",
                         "per_group": True, "per_horizon": True}}
SLOW = {**COMMON, "model": {**MODEL, "params": {"n_estimators": 5000, "max_depth": 12, "learning_rate": 0.001}}}
FAST = {**COMMON, "model": {**MODEL, "params": {"n_estimators": 40, "max_depth": 4}}}
results: list[str] = []


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def approved_run(api, name, settings, rows):
    d = api.post(f"{BASE}/api/datasets", data=sensor_csv(rows), headers={"X-File-Name": quote(name)}).json()
    did = d["dataset_id"]
    for _ in range(600):
        if api.get(f"{BASE}/api/datasets/{did}").json()["state"] == "PROFILED":
            break
        time.sleep(0.05)
    assert api.put(f"{BASE}/api/datasets/{did}/goal", data=GOAL).ok
    rid = api.post(f"{BASE}/api/datasets/{did}/recommendation").json()["recommendation_id"]
    plan = api.post(f"{BASE}/api/datasets/{did}/plan/apply", data={"recommendation_id": rid, "expected_revision": 1}).json()
    t = api.put(f"{BASE}/api/datasets/{did}/training", data={**settings, "expected_revision": plan["revision"]})
    assert t.ok, t.text()
    v = api.get(f"{BASE}/api/datasets/{did}/confirmation").json()
    a = api.post(f"{BASE}/api/datasets/{did}/approve", data={"expected_revision": v["revision"], "confirmed": True}).json()
    return did, a["approval"]


def submit(api, did, appr):
    r = api.post(f"{BASE}/api/datasets/{did}/runs", data={"plan_id": appr["plan_id"], "plan_hash": appr["plan_hash"]})
    assert r.ok, r.text()
    return r.json()


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 950}, device_scale_factor=2, locale="ko-KR")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    api = ctx.request

    # 1. 오래 걸리는 학습 1개 진행 중 + 두 번째 제출 → 대기 ----------------------------------
    did1, a1 = approved_run(api, "queue_slow.csv", SLOW, 5760)
    r1 = submit(api, did1, a1)
    did2, a2 = approved_run(api, "queue_second.csv", FAST, 2880)
    r2 = submit(api, did2, a2)
    assert r2["state"] == "QUEUED" and r2["queue_position"] == 1, r2
    page.goto(f"{BASE}/#/runs/{r2['run_id']}")
    note = page.locator(".queue-note")
    expect(note).to_contain_text("대기 중")
    expect(note).to_contain_text("앞에 1개 실행")
    expect(note).to_contain_text("동시에 1개씩")
    page.screenshot(path=f"{SHOTS}/01_waiting.png", full_page=True)
    ok("학습 중 두 번째 제출 → S5 '대기 중 · 앞에 1개'", r2["run_id"])

    page.goto(f"{BASE}/#/runs")
    row = page.locator("tr", has_text=r2["run_id"])
    expect(row).to_contain_text("대기")
    expect(row).to_contain_text("1번째")
    expect(page.locator("tr", has_text=r1["run_id"])).to_contain_text("실행 중")
    page.screenshot(path=f"{SHOTS}/02_list.png", full_page=True)
    ok("실행 목록에 대기 순번 표시", "1번째")

    # 2. 대기 실행 취소(화면) → 즉시 취소 ------------------------------------------------------
    page.goto(f"{BASE}/#/runs/{r2['run_id']}")
    page.get_by_role("button", name="취소…").click()
    expect(page.locator("#cancel-title")).to_contain_text("대기 중인 학습을 취소합니다")
    page.get_by_role("button", name="학습 취소").click()
    expect(page.locator(".run-head .status")).to_have_text("취소됨", timeout=5_000)
    expect(page.locator(".banner", has_text="대기 중에 취소했습니다")).to_be_visible()
    assert not (Path(os.environ["MLWB_E2E_WS"]) / "experiments" / r2["run_id"] / "worker.lock").exists() \
        if os.environ.get("MLWB_E2E_WS") else True
    page.screenshot(path=f"{SHOTS}/03_canceled_waiting.png", full_page=True)
    ok("대기 실행 취소 → 즉시 '대기 중에 취소했습니다'(학습 프로세스 없음)")

    # 3. 세 번째 제출 → 앞 실행 취소 → 자동 시작·완료 --------------------------------------------
    did3, a3 = approved_run(api, "queue_third.csv", FAST, 2880)
    r3 = submit(api, did3, a3)
    assert r3["state"] == "QUEUED" and r3["queue_position"] == 1, r3
    page.goto(f"{BASE}/#/runs/{r1['run_id']}")
    page.get_by_role("button", name="취소…").click()
    page.get_by_role("button", name="학습 취소").click()
    expect(page.locator(".run-head .status")).to_have_text("취소됨", timeout=60_000)
    page.goto(f"{BASE}/#/runs/{r3['run_id']}")
    expect(page.locator(".run-head .status")).to_have_text("완료", timeout=120_000)
    v1 = api.get(f"{BASE}/api/runs/{r1['run_id']}").json()
    v3 = api.get(f"{BASE}/api/runs/{r3['run_id']}").json()
    assert v3["started_at"] >= v1["finished_at"], (v1["finished_at"], v3["started_at"])
    expect(page.locator(".queue-note")).to_have_count(0)
    page.screenshot(path=f"{SHOTS}/04_auto_started.png", full_page=True)
    ok("앞 실행이 끝나자 대기 실행 자동 시작 → 완료", f"앞 종료 {v1['finished_at']} ≤ 시작 {v3['started_at']}")

    app_errors = [e for e in errors if not re.search(r"status of (409|422)", e)]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음")
    browser.close()

print("\n".join(results))
