"""P2 첫 시계열 학습 브라우저 검증 (Playwright/Chromium, 실제 XGBoost CPU 학습).

사전 준비: pip install -e ".[dev,timeseries]" playwright && python -m playwright install chromium
실행: 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>`(실제 내장 Registry) 실행 후
      python tests/e2e/check_p2_browser.py [스크린샷 폴더]   (기본 ./workspace/e2e-shots-p2)
예제 outputs/workbench-sensor-example/motor_sensor_example.csv (또는 MLWB_EXAMPLE_CSV)가 필요하다.
"""

import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("MLWB_BASE_URL", "http://127.0.0.1:8000")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-p2"
os.makedirs(SHOTS, exist_ok=True)
DATA = Path(os.environ.get("MLWB_EXAMPLE_CSV") or ROOT / "outputs" / "workbench-sensor-example" / "motor_sensor_example.csv")
assert DATA.exists(), f"예제 CSV가 없습니다: {DATA}"
results: list[str] = []


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def to_confirm_and_start(page) -> str:
    """S3에서 '저장하고 확인·실행으로' → S4 승인 → 학습 시작. run_id 반환."""
    page.get_by_role("button", name=re.compile("확인·실행")).click()
    expect(page.get_by_role("heading", name="확인·실행", level=1)).to_be_visible()
    expect(page.locator(".conf-head")).to_contain_text(re.compile("검증 통과|승인 가능"))
    page.get_by_label(re.compile("^위 설정으로 승인합니다")).check()
    page.get_by_role("button", name="설정 승인").click()
    expect(page.locator(".conf-approval")).to_contain_text("승인됨")
    start = page.get_by_role("button", name="학습 시작 ▶")
    expect(start).to_be_enabled()
    start.click()
    page.wait_for_url(re.compile(r"#/runs/run_"))
    return page.url.split("#/runs/")[1]


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 950}, device_scale_factor=2, locale="ko-KR")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    nav = page.get_by_role("navigation", name="주 메뉴")
    api = ctx.request

    # 1. S1~S3: 15분·30분, 최근 30일, 70/15/15, MAE·RMSE·직전값, 설비별 -------------------
    page.goto(BASE)
    page.set_input_files("input[type=file]", str(DATA))
    expect(page.locator(".ds-row .status").first).to_have_text("완료", timeout=60_000)
    page.locator(".ds-select").first.click()
    form = page.locator("form.goal")
    form.get_by_label("예측 타깃").select_option("temperature_c")
    form.get_by_label("설비 구분 열 (선택)").select_option("device_id")
    form.get_by_role("button", name="15분").click()
    form.get_by_role("button", name="30분").click()
    form.get_by_role("button", name="목표 저장").click()
    expect(page.locator(".toast")).to_contain_text("목표를 저장했습니다")  # 저장 후 전처리로 자동 이동(ADD-UX-09)
    page.wait_for_url(re.compile(r"#/preprocess/"))
    ds_id = page.url.split("#/preprocess/")[1]
    nav.get_by_role("link", name=re.compile("^전처리")).click()
    expect(page.locator("ol.cards .card")).to_have_count(4, timeout=15_000)
    expect(page.locator("ol.cards")).not_to_contain_text("실행 불가")
    expect(page.locator("ol.cards")).to_contain_text("결측 처리")
    page.get_by_role("button", name=re.compile("학습 설정으로$")).click()
    expect(page.get_by_role("heading", name="학습 설정", level=1)).to_be_visible()
    expect(page.locator(".model-opt").first.locator(".status")).to_have_text("실행 가능")
    expect(page.locator(".preview-note")).to_have_count(0)
    page.locator("input[name=budget]").nth(1).check()
    page.get_by_label("최근 일수").fill("30")
    page.locator(".checks label", has_text="설비별").locator("input").check()
    ok("S1 목표(15분·30분) → S2 추천(실행 가능) → S3 최근 30일·70/15/15·MAE·RMSE·직전값·설비별")

    # 2. S4 승인 → 학습 시작 → S5 완료 ---------------------------------------------
    run1 = to_confirm_and_start(page)
    expect(page.locator(".stepper")).to_be_visible()
    t0 = time.time()
    expect(page.locator(".run-head .status")).to_have_text("완료", timeout=180_000)
    secs = time.time() - t0
    expect(page.locator(".stepper li.is-done")).to_have_count(6)
    table = page.locator(".metric-table").first
    for row in ("전체", "15분 뒤", "30분 뒤", "설비 MOTOR-1", "설비 MOTOR-2"):
        expect(table.locator("tbody th", has_text=row)).to_have_count(1)
    expect(table.locator("thead")).to_contain_text("MAE (모델 / 직전값)")
    mae = table.locator("tbody tr").first.locator("td").nth(1).locator("strong").inner_text()
    assert re.match(r"^\d+\.\d+$", mae), mae
    expect(page.locator("#split-title + dl")).to_contain_text("(최근 30일)")
    log = page.locator(".event-log")
    for txt in ("대기열 등록", "데이터 준비 시작", "전처리·분할 완료", "학습 시작", "평가 시작", "결과 저장 완료", "완료"):
        expect(log).to_contain_text(txt)
    page.screenshot(path=f"{SHOTS}/01_run_done.png", full_page=True)
    href = page.get_by_role("link", name="예측값 CSV 내려받기").get_attribute("href")
    csv = api.get(BASE + href)
    assert csv.status == 200 and csv.text().startswith("split,group,origin_time,target_time,horizon,actual,predicted,baseline")
    ok("S4 승인 → 학습 시작 → S5 단계·이벤트 → 완료", f"{secs:.1f}초, 테스트 MAE {mae}, 예측 CSV {len(csv.text().splitlines()) - 1}행")

    # 3. 같은 승인 재요청은 같은 실행 -------------------------------------------------
    conf = api.get(f"{BASE}/api/datasets/{ds_id}/confirmation").json()
    assert conf["approval"]["status"] == "submitted" and conf["approval"]["run_id"] == run1
    again = api.post(f"{BASE}/api/datasets/{ds_id}/runs",
                     data={"plan_id": conf["approval"]["plan_id"], "plan_hash": conf["approval"]["plan_hash"]})
    assert again.ok and again.json()["run_id"] == run1 and again.json()["duplicate"] is True
    ok("같은 승인으로 다시 요청 → 기존 실행 반환", "S4 상태 '학습 시작함' + 결과 링크")

    # 4. 학습량만 '전체'로 바꾼 두 번째 실행: 검증·테스트 표본 동일 ---------------------------
    page.goto(f"{BASE}/#/train/{ds_id}")
    page.locator("input[name=budget]").nth(0).check()
    run2 = to_confirm_and_start(page)
    expect(page.locator(".run-head .status")).to_have_text("완료", timeout=180_000)
    a, b = api.get(f"{BASE}/api/runs/{run1}").json(), api.get(f"{BASE}/api/runs/{run2}").json()
    for k in ("val_period", "test_period", "val_samples", "test_samples", "train_end", "val_end"):
        assert a["split"][k] == b["split"][k], k
    assert b["split"]["train_samples"] > a["split"]["train_samples"]
    ok("학습량만 변경(최근 30일 → 전체)", f"검증·테스트 구간·표본 동일({a['split']['test_samples']}), 학습 표본 {a['split']['train_samples']} → {b['split']['train_samples']}")

    # 5. 목록 ------------------------------------------------------------------------
    nav.get_by_role("link", name="실행·결과", exact=True).click()
    rows = page.locator(".run-table tbody tr")
    expect(rows).to_have_count(2)
    expect(rows.first).to_contain_text("전체")
    expect(rows.nth(1)).to_contain_text("최근 30일")
    expect(rows.first.locator(".status")).to_have_text("완료")
    page.screenshot(path=f"{SHOTS}/02_runs_list.png", full_page=True)
    ok("실행·결과 목록", "2개 실행, 테스트 MAE·RMSE·직전값 대비")

    # 6. 긴 학습 중 앱 응답 + 취소 ---------------------------------------------------------
    page.goto(f"{BASE}/#/train/{ds_id}")
    page.get_by_label(re.compile("^트리 개수")).fill("5000")
    page.get_by_label(re.compile("^최대 깊이")).fill("10")
    run3 = to_confirm_and_start(page)
    expect(page.locator(".stepper li.is-current")).to_have_text(re.compile("학습"), timeout=60_000)
    lat = []
    for _ in range(5):
        s = time.time()
        assert api.get(f"{BASE}/api/datasets").ok
        lat.append(time.time() - s)
    assert max(lat) < 1.5, lat
    expect(page.locator(".run-progress .bar")).to_be_visible(timeout=10_000)
    page.get_by_role("button", name="취소…").click()
    expect(page.get_by_role("alertdialog")).to_contain_text("저장된 중간 결과는 없습니다")
    page.get_by_role("button", name="학습 취소").click()
    expect(page.locator(".run-head .status")).to_have_text("취소됨", timeout=30_000)
    ok("학습 중 앱 응답·취소", f"데이터셋 목록 응답 최대 {max(lat) * 1000:.0f}ms, 확인 후 '취소됨'")

    # 7. 좁은 창 -----------------------------------------------------------------------
    page.goto(f"{BASE}/#/runs/{run1}")
    expect(page.locator(".metric-table").first).to_be_visible()
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
