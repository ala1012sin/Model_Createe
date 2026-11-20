"""사용자 정의 플러그인 예제(ts.custom.ewm) 브라우저 검증: S2 목록에서 추가 → 확정 → 학습 → S5 → S7(이후 기간 CSV),
그리고 망가진 플러그인 폴더의 안내 표시.

실행: 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>`(실제 Registry + 프로젝트 plugins/ 폴더),
      `python tests/e2e/make_future_csv.py` 후 python tests/e2e/check_user_plugin_browser.py [스크린샷 폴더]
      불러오기 문제 안내까지 보려면 plugin.json이 망가진 폴더를 둔 둘째 위치를 함께 지정해 서버를 띄우고
      (MLWB_PLUGIN_DIRS="<프로젝트>/plugins;<둘째 위치>", Linux는 ':'), MLWB_EXPECT_PLUGIN_PROBLEM=<그 폴더 이름>.
"""

import io
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("MLWB_BASE_URL", "http://127.0.0.1:8000")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-user-plugin"
os.makedirs(SHOTS, exist_ok=True)
DATA = Path(os.environ.get("MLWB_EXAMPLE_CSV") or ROOT / "outputs" / "workbench-sensor-example" / "motor_sensor_example.csv")
FUTURE = Path(os.environ.get("MLWB_FUTURE_CSV") or ROOT / "workspace" / "e2e-data" / "motor_sensor_future.csv")
EXPECT_PROBLEM = os.environ.get("MLWB_EXPECT_PLUGIN_PROBLEM")  # 망가진 폴더 이름(지정 시 안내 표시 확인)
assert DATA.exists() and FUTURE.exists()
results: list[str] = []
NAME = "지수 가중 평균(EWM)"


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def card(page, name):
    return page.locator(".card", has=page.locator(".card-name", has_text=re.compile(f"^{re.escape(name)}$")))


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

    # 1. S1 목표 → S2: 사용자 정의 플러그인이 추가 목록에 보이고 추가·편집된다 ----------------------------
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
    if EXPECT_PROBLEM:
        banner = page.locator(".banner-warn", has_text="불러오지 못한 사용자 정의 플러그인")
        expect(banner).to_contain_text(EXPECT_PROBLEM)
        assert "/" not in banner.inner_text().split("—")[0].strip() and ":\\" not in banner.inner_text()
        page.screenshot(path=f"{SHOTS}/00_problem_banner.png", full_page=False)
        ok("불러오지 못한 플러그인 폴더 안내(폴더 이름·사유, 절대경로 없음)", banner.inner_text().splitlines()[-1])
    opt = page.locator("#add-step option", has_text=NAME)
    expect(opt).to_have_text(f"{NAME} (사용자 정의) — 실행 가능")
    page.locator("#add-step").select_option(label=f"{NAME} (사용자 정의) — 실행 가능")
    page.get_by_role("button", name="추가", exact=True).click()
    c = card(page, NAME)
    expect(c).to_have_count(1)
    expect(c.locator(".user-badge")).to_have_text("사용자 정의")
    detail = page.locator(".prep-detail")
    expect(detail).to_contain_text("ts.custom.ewm@1.0.0 · 사용자 정의 플러그인")
    detail.get_by_label(re.compile("^가중 범위 span")).fill("1")  # 범위 밖 → 서버 검증 오류가 위치와 함께
    expect(page.locator(".cards .card-issue").first).to_be_visible(timeout=5_000)
    detail.get_by_label(re.compile("^가중 범위 span")).fill("6, 24")
    cols = detail.get_by_role("group", name="대상 열")
    cols.get_by_label(re.compile("^temperature_c")).check()
    cols.get_by_label(re.compile("^motor_current_a")).check()
    expect(page.locator(".cards .field-error")).to_have_count(0, timeout=5_000)
    expect(page.locator(".cards .card-issue")).to_have_count(0, timeout=5_000)
    page.screenshot(path=f"{SHOTS}/01_s2_user_plugin.png", full_page=True)
    page.get_by_role("button", name="저장하고 학습 설정으로").click()
    expect(page.get_by_role("heading", name="학습 설정", level=1)).to_be_visible()
    plan = api.get(f"{BASE}/api/datasets/{ds_id}/plan").json()
    ewm = next(s for s in plan["steps"] if s["plugin_id"] == "ts.custom.ewm")
    assert ewm["status"] == "ready" and ewm["origin"] == "user" and ewm["params"] == {
        "spans": [6, 24], "columns": ["temperature_c", "motor_current_a"]}, ewm
    ok("S2: 추가 목록 '(사용자 정의)' 표시 → 추가·span·대상 열 편집(범위 오류 표시 후 수정)·저장",
       " → ".join(s["plugin_id"].replace("ts.", "") for s in plan["steps"]))

    # 2. S3 → S4 승인 → 학습 → S5 ---------------------------------------------------------
    page.locator("input[name=budget]").nth(1).check()
    page.get_by_label("최근 일수").fill("30")
    page.get_by_role("button", name=re.compile("확인·실행")).click()
    expect(page.locator(".conf-head")).to_contain_text(re.compile("검증 통과|승인 가능"))
    expect(page.locator(".conf-steps").first).to_contain_text(NAME)
    page.get_by_label(re.compile("^위 설정으로 승인합니다")).check()
    page.get_by_role("button", name="설정 승인").click()
    page.get_by_role("button", name="학습 시작 ▶").click()
    page.wait_for_url(re.compile(r"#/runs/run_"))
    run_id = page.url.split("#/runs/")[1]
    expect(page.locator(".run-head .status")).to_have_text("완료", timeout=180_000)
    run = api.get(f"{BASE}/api/runs/{run_id}").json()
    mae, base_mae = run["metrics"]["test"]["overall"]["model"]["mae"], run["metrics"]["test"]["overall"]["baseline"]["mae"]
    page.screenshot(path=f"{SHOTS}/02_run_done.png", full_page=True)
    ok("승인 → 실제 학습 완료(worker가 같은 플러그인 사용)", f"테스트 MAE {mae:.3f}(직전값 {base_mae:.3f})")

    # 3. S7: 같은 피처로 이후 기간 예측 --------------------------------------------------------
    page.get_by_role("link", name="이 모델로 새 데이터에 적용 →").click()
    req = page.locator(".req-table tbody")
    expect(req.locator("tr", has_text="temperature_c")).to_contain_text("EWM span [6, 24](현재 제외)")
    expect(req.locator("tr", has_text="motor_current_a")).to_contain_text("EWM span")
    expect(page.locator(".apply .conf-summary").first).to_contain_text("24칸")
    expect(page.locator(".apply")).to_contain_text(NAME)
    page.locator(".apply input[type=file]").set_input_files(str(FUTURE))
    expect(page.locator(".ap-state .status")).to_have_text("점검 통과")
    page.get_by_role("button", name="예측 실행").click()
    expect(page.locator(".ap-state .status")).to_have_text("예측 완료", timeout=60_000)
    app_id = page.url.split("#/apply/")[1]
    rec = api.get(f"{BASE}/api/applications/{app_id}").json()
    with page.expect_download() as dl:
        page.get_by_role("link", name="예측 결과 CSV 내려받기").click()
    got = pd.read_csv(dl.value.path())
    assert len(got) == rec["result"]["rows"] > 0 and set(got.model_run_id) == {run_id}
    page.screenshot(path=f"{SHOTS}/03_s7.png", full_page=True)
    ok("S7: 필요 이력 24칸·열별 EWM 표시 → 이후 기간 예측·내려받기",
       f"{len(got)}건, 이력 부족 제외 {rec['check']['excluded']['history_short']}건")

    # 4. 학습 원본 재적용 = 저장 평가 예측(같은 피처가 다시 만들어짐) -------------------------------
    page.locator(".apply input[type=file]").set_input_files(str(DATA))
    expect(page.locator(".ap-state .status")).to_have_text("점검 통과")
    app2 = page.url.split("#/apply/")[1]
    page.get_by_role("button", name="예측 실행").click()
    expect(page.locator(".ap-state .status")).to_have_text("예측 완료", timeout=60_000)
    new = pd.read_csv(io.StringIO(api.get(f"{BASE}/api/applications/{app2}/predictions.csv").text()))
    saved = pd.read_csv(io.StringIO(api.get(f"{BASE}/api/runs/{run_id}/predictions.csv").text()))
    m = saved.merge(new, on=["group", "origin_time", "target_time", "horizon"], suffixes=("", "_new"))
    diff = float((m.predicted - m.predicted_new).abs().max())
    assert len(m) >= 0.99 * len(saved) and diff < 1e-6, (len(m), len(saved), diff)
    ok("학습 원본 재적용 = 저장된 평가 예측", f"조인 {len(m)}/{len(saved)}행, 최대 차이 {diff:.1e}")

    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(f"{BASE}/#/preprocess/{ds_id}")
    expect(page.locator("ol.cards .card")).to_have_count(5)
    time.sleep(0.3)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=f"{SHOTS}/04_narrow_390.png", full_page=True)
    ok("좁은 창 390px S2(사용자 정의 배지 포함)", "가로 넘침 없음")

    app_errors = [e for e in errors if not re.search(r"status of (409|422)", e)]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음")
    browser.close()

print("\n".join(results))
