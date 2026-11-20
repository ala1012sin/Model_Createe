"""추가 요구사항 1차(ADD-UX-03·04·08·09·10·11) 브라우저 검증. 05는 통합 테스트(test_xlsx)에서 확인.

실행: 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>`(실제 Registry) 후
      python tests/e2e/check_ux_browser.py [스크린샷 폴더]
앱 창(pywebview) 버튼은 일반 브라우저에 없으므로, 창 제어 API를 흉내 낸 객체를 페이지에 넣어 닫기 확인을 본다.
"""

import os
import re
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from test_csv_dataset import sensor_csv  # noqa: E402

BASE = os.environ.get("MLWB_BASE_URL", "http://127.0.0.1:8000")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-ux"
os.makedirs(SHOTS, exist_ok=True)
D = tempfile.mkdtemp(prefix="mlwb-ux-")
Path(D, "sensor_ux.csv").write_bytes(sensor_csv(2880))
Path(D, "broken.csv").write_bytes(b"time,,temp\n2026-01-01 00:00,1,2\n")
results: list[str] = []
FAKE_WINDOW = """
window.__closed = 0;
window.pywebview = { api: {
  minimize: async () => {}, toggle_maximized: async () => false,
  close: async () => { window.__closed += 1; } } };
window.addEventListener('DOMContentLoaded', () => window.dispatchEvent(new Event('pywebviewready')));
"""


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def wait_state(page, name, text):
    expect(page.locator(".ds-row", has_text=name).locator(".status").first).to_have_text(text, timeout=60_000)


with sync_playwright() as p:
    # 헤드리스 Chromium은 기본으로 스크롤바를 숨겨(겹침) 폭 변화가 재현되지 않는다 → 실제 앱 창처럼 스크롤바를 보이게
    browser = p.chromium.launch(ignore_default_args=["--hide-scrollbars"])
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2, locale="ko-KR")
    ctx.add_init_script(FAKE_WINDOW)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    nav = page.get_by_role("navigation", name="주 메뉴")
    api = ctx.request

    # ADD-UX-08: 내용 길이가 달라도(분석 실패 ↔ 완료) 오른쪽 박스 위치·폭이 같다 ---------------------
    page.set_viewport_size({"width": 1440, "height": 640})  # 완료 데이터셋만 세로 스크롤이 생기는 높이
    page.goto(BASE)
    page.set_input_files("input[type=file]", f"{D}/broken.csv")
    wait_state(page, "broken.csv", "분석 실패")
    page.set_input_files("input[type=file]", f"{D}/sensor_ux.csv")
    wait_state(page, "sensor_ux.csv", "완료")
    boxes = {}
    for name in ("broken.csv", "sensor_ux.csv", "broken.csv"):
        page.locator(".ds-row", has_text=name).locator(".ds-select").click()
        page.wait_for_timeout(300)
        b = page.locator(".col-right").bounding_box()
        boxes.setdefault(name, []).append((round(b["x"]), round(b["width"])))
    page.locator(".ds-row", has_text="sensor_ux.csv").locator(".ds-select").click()
    page.wait_for_timeout(300)
    tall = page.evaluate("document.documentElement.scrollHeight > window.innerHeight")
    bar = page.evaluate("window.innerWidth - document.documentElement.clientWidth")
    assert bar > 0, bar  # 긴 화면에서 스크롤바가 실제로 폭을 차지(앱 창과 같은 조건)
    page.locator(".ds-row", has_text="broken.csv").locator(".ds-select").click()
    page.wait_for_timeout(300)
    short = page.evaluate("document.documentElement.scrollHeight <= window.innerHeight")
    assert tall and short, (tall, short)  # 스크롤이 생기는 경우와 안 생기는 경우를 실제로 비교
    assert len({v for vs in boxes.values() for v in vs}) == 1, boxes
    page.set_viewport_size({"width": 1440, "height": 900})
    ok("데이터셋을 바꿔도 오른쪽 박스 위치·폭 고정(ADD-UX-08)", f"x·폭 {boxes['sensor_ux.csv'][0]}, 스크롤 있음·없음 둘 다 같음")

    # ADD-UX-11·09: 예측 구간 숫자+단위 → 목표 저장 → 알림 + 전처리 자동 이동 -------------------------
    page.locator(".ds-row", has_text="sensor_ux.csv").locator(".ds-select").click()
    form = page.locator("form.goal")
    form.get_by_label("예측 타깃").select_option("sensor_1")
    form.get_by_label("설비 구분 열 (선택)").select_option("device_id")
    form.get_by_role("button", name="15분").click()
    hz = form.get_by_label("예측 구간", exact=True)
    hz.fill("2")
    form.get_by_label("예측 구간 단위").select_option(label="시간")
    form.get_by_role("button", name="추가", exact=True).click()
    expect(form.locator(".chip")).to_have_count(2)
    expect(form.locator(".chip").nth(1)).to_contain_text("2시간 뒤")
    assert hz.get_attribute("type") == "number"
    ds_id = page.url.split("#/datasets/")[1]
    form.get_by_role("button", name="목표 저장").click()
    expect(page.locator(".toast")).to_contain_text("목표를 저장했습니다 — 전처리로 이동합니다")
    page.wait_for_url(re.compile(r"#/preprocess/"))
    expect(page.get_by_role("heading", name="전처리", level=1)).to_be_visible()
    page.screenshot(path=f"{SHOTS}/01_toast_preprocess.png")
    goal = api.get(f"{BASE}/api/datasets/{ds_id}/goal").json()["goal"]
    assert goal["horizons"] == ["15min", "2h"], goal
    ok("예측 구간 숫자+단위(2 + 시간 → 2h) · 목표 저장 → 알림 + 전처리 자동 이동(ADD-UX-09·11)", str(goal["horizons"]))

    # ADD-UX-11: 전처리 시간 파라미터 숫자+단위 ------------------------------------------------
    expect(page.locator("ol.cards .card")).to_have_count(4, timeout=15_000)
    page.locator(".card", has=page.locator(".card-name", has_text=re.compile("^세션 분리$"))).locator(".card-main").click()
    detail = page.locator(".prep-detail")
    gap = detail.get_by_label(re.compile("^세션을 나누는 간격 gap"))
    assert gap.get_attribute("type") == "number"
    before = (gap.input_value(), detail.get_by_label("세션을 나누는 간격 단위").input_value())
    gap.fill("1")
    detail.get_by_label("세션을 나누는 간격 단위").select_option(label="시간")
    expect(page.locator(".cards .field-error")).to_have_count(0, timeout=5_000)
    page.get_by_role("button", name="초안 저장").click()
    expect(page.locator(".save-bar [role=status]")).to_contain_text("초안을 저장했습니다")
    plan = api.get(f"{BASE}/api/datasets/{ds_id}/plan").json()
    gap_v = next(s for s in plan["steps"] if s["plugin_id"] == "ts.session_split")["params"]["gap"]
    assert gap_v == "1h", gap_v
    page.screenshot(path=f"{SHOTS}/02_duration_param.png")
    ok("세션 분리 간격 숫자+단위(추천값 표시 → 1 + 시간 → 1h 저장)", f"처음 {before[0]}{before[1]} → {gap_v}")

    # ADD-UX-10: 뒤로가기 ------------------------------------------------------------------
    back = page.locator(".back-link")
    expect(back).to_have_text("← 데이터셋·목표")
    page.screenshot(path=f"{SHOTS}/02b_before_train.png", full_page=True)
    page.get_by_role("button", name=re.compile("학습 설정으로$")).click()
    expect(page.get_by_role("heading", name="학습 설정", level=1)).to_be_visible()
    expect(page.locator(".back-link")).to_have_text("← 전처리")

    # ADD-UX-04: 모델 파라미터 기본값 -----------------------------------------------------------
    depth = page.get_by_label(re.compile("^최대 깊이"))
    first_depth = depth.input_value()  # 추천이 채운 값일 수 있음
    depth.fill("")
    expect(depth).to_have_attribute("placeholder", "기본값 6")
    expect(page.locator(".num-default", has=depth)).to_contain_text("비워 두면 기본값 6")
    depth.fill("8")
    reset = page.get_by_role("button", name="기본값(6)으로")
    expect(reset).to_be_visible()
    reset.click()
    expect(depth).to_have_value("")
    lr = page.get_by_label(re.compile("^학습률"))
    expect(lr).to_have_attribute("placeholder", "기본값 0.1")
    page.screenshot(path=f"{SHOTS}/03_param_defaults.png")
    ok("모델 파라미터 기본값 표시·되돌리기(ADD-UX-04)", f"추천값 {first_depth or '없음'} → 비우면 '기본값 6' 안내, 8 → '기본값(6)으로' → 비움")

    page.locator(".back-link").click()
    expect(page.get_by_role("heading", name="전처리", level=1)).to_be_visible()
    page.locator(".back-link").click()
    expect(page.locator("form.goal")).to_be_visible()
    page.goto(f"{BASE}/#/runs")
    expect(page.locator(".back-link")).to_have_count(0)
    page.goto(f"{BASE}/#/apply")
    expect(page.locator(".back-link")).to_have_text("← 실행·결과")
    ok("뒤로가기 링크: 학습 설정 → 전처리 → 데이터셋·목표, 새 데이터 적용 → 실행·결과(ADD-UX-10)")

    # ADD-UX-03: 창 닫기 확인 --------------------------------------------------------------
    page.goto(BASE)
    close_btn = page.get_by_role("button", name="창 닫기")
    expect(close_btn).to_be_visible()
    close_btn.click()
    dlg = page.get_by_role("alertdialog", name="앱을 종료합니다")
    expect(dlg).to_contain_text("진행 중인 학습은 없습니다")
    page.screenshot(path=f"{SHOTS}/04_close_confirm.png")
    dlg.get_by_role("button", name="취소").click()
    expect(dlg).to_have_count(0)
    assert page.evaluate("window.__closed") == 0
    close_btn.click()
    page.get_by_role("alertdialog", name="앱을 종료합니다").get_by_role("button", name="종료").click()
    page.wait_for_function("window.__closed === 1")
    ok("창 닫기(×) → '앱을 종료합니다' 확인, 취소하면 그대로·종료하면 창 닫기 호출(ADD-UX-03)")

    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(f"{BASE}/#/train/{ds_id}")
    expect(page.get_by_role("heading", name="학습 설정", level=1)).to_be_visible()
    time.sleep(0.3)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=f"{SHOTS}/05_narrow_390.png", full_page=True)
    ok("좁은 창 390px 학습 설정(기본값 안내)", "가로 넘침 없음")

    app_errors = [e for e in errors if not re.search(r"status of (409|422)", e)]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음")
    browser.close()

print("\n".join(results))
