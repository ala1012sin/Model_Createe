"""S1 데이터셋 화면 브라우저 검증 스크립트 (Playwright/Chromium).

사전 준비: pip install playwright && python -m playwright install chromium
실행 순서:
  1) 빈 워크스페이스로 서버 실행:  mlwb serve --workspace <빈 폴더>
  2) python tests/e2e/check_s1_browser.py [스크린샷 폴더]   (기본 ./workspace/e2e-shots)
합성 CSV(576행, 40행, 오류 1개, 수정본 100행, 80만 행)는 임시 폴더에 생성한다.
pytest 기본 수집 대상이 아니다(파일명이 test_로 시작하지 않음).
"""

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
from test_csv_dataset import sensor_csv  # noqa: E402

BASE = os.environ.get("MLWB_BASE_URL", "http://127.0.0.1:8000")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots"
os.makedirs(SHOTS, exist_ok=True)
D = tempfile.mkdtemp(prefix="mlwb-e2e-")
for name, data in {
    "sensor_A.csv": sensor_csv(288),
    "line2_log.csv": sensor_csv(40, devices=("L2",), header=("측정시각", "라인", "온도", "상태", "비고")),
    "broken_header.csv": b"time,,temp\n2026-01-01 00:00,1,2\n",
    "broken_header_fixed.csv": sensor_csv(100, devices=("D9",)),
    "big_sensor.csv": sensor_csv(400_000),
}.items():
    Path(D, name).write_bytes(data)
results: list[tuple[str, str]] = []


def ok(name: str, detail: str = "") -> None:
    results.append((name, "PASS" + (f" — {detail}" if detail else "")))
    print("PASS", name, detail, flush=True)


def row(page, name):
    return page.locator(".ds-row", has=page.locator(".ds-name", has_text=name))


def select(page, name):
    row(page, name).locator(".ds-select").click()


def wait_state(page, name, text, timeout=60_000):
    expect(row(page, name).locator(".status")).to_contain_text(text, timeout=timeout)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2, locale="ko-KR")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))

    # 1. 빈 상태 + 세로 메뉴 ---------------------------------------------------
    page.goto(BASE)
    expect(page.get_by_role("heading", name="데이터셋", level=1)).to_be_visible()
    expect(page.get_by_text("등록된 데이터셋이 없습니다.")).to_be_visible()
    nav = page.get_by_role("navigation", name="주 메뉴")
    cur = nav.locator('[aria-current="page"]')
    expect(cur).to_have_count(1)
    expect(cur).to_contain_text("데이터셋")
    disabled = nav.locator('[aria-disabled="true"]')
    expect(disabled).to_have_count(3)
    # 전처리·학습 설정은 목표를 저장하면 열리는 메뉴(P1 S2·S3), 실행·결과·새 데이터 적용은 항상 열림(P2), 설정은 연결 예정
    expect(nav.get_by_role("link", name="실행·결과", exact=True)).to_have_attribute("href", "#/runs")
    expect(nav.get_by_role("link", name="새 데이터 적용", exact=True)).to_have_attribute("href", "#/apply")
    for lbl in ["전처리", "학습 설정"]:
        expect(nav.get_by_role("link", name=f"{lbl} 목표 저장 후 사용", exact=True)).to_have_attribute("aria-disabled", "true")
    for lbl in ["설정"]:
        expect(nav.get_by_role("link", name=f"{lbl} 연결 예정", exact=True)).to_have_attribute("aria-disabled", "true")
    page.screenshot(path=f"{SHOTS}/01_empty_wide.png")
    ok("빈 상태·세로 메뉴(현재=데이터셋, 전처리·학습 설정=목표 저장 후 사용, 실행·결과·새 데이터 적용 열림, 설정 연결 예정)")

    # 2. CSV 두 개 업로드 → 목록 전환 시 오른쪽 구조 변경 -------------------------
    page.set_input_files("input[type=file]", [f"{D}/sensor_A.csv", f"{D}/line2_log.csv"])
    wait_state(page, "sensor_A.csv", "완료")
    wait_state(page, "line2_log.csv", "완료")
    select(page, "sensor_A.csv")
    panel = page.locator(".col-right")
    expect(panel.locator(".shape")).to_have_attribute("aria-label", "576행 5열")
    expect(panel.locator(".feat-name").first).to_have_text("timestamp")
    expect(panel.locator(".axis")).to_contain_text("5분 간격")
    names_a = panel.locator(".feat-name").all_inner_texts()
    select(page, "line2_log.csv")
    expect(panel.locator(".shape")).to_have_attribute("aria-label", "40행 5열")
    expect(panel.locator(".feat-name").first).to_have_text("측정시각")
    names_b = panel.locator(".feat-name").all_inner_texts()
    assert names_a != names_b
    page.screenshot(path=f"{SHOTS}/02_two_csv_wide.png", full_page=True)
    ok("두 CSV 전환", f"sensor_A 576행 {names_a} / line2_log 40행 {names_b}")

    # 2-1. 상단 바 검색(데이터셋 이름) ------------------------------------------------
    search = page.get_by_role("searchbox", name="데이터셋 이름 검색")
    search.fill("LINE2")
    expect(page.locator(".ds-row")).to_have_count(1)
    expect(page.locator(".ds-name")).to_have_text("line2_log.csv")
    search.fill("없는이름")
    expect(page.locator(".ds-row")).to_have_count(0)
    expect(page.get_by_role("status").filter(has_text="검색 결과가 없습니다.")).to_be_visible()
    search.fill("")
    expect(page.locator(".ds-row")).to_have_count(2)
    expect(page.get_by_role("group", name="창 제어")).to_have_count(0)  # 일반 브라우저에는 창 버튼 없음
    ok("상단 바 데이터셋 이름 검색", "대소문자 무시 필터·결과 없음 안내, 일반 브라우저에서는 창 버튼 숨김")

    # 3. 목표 저장 ----------------------------------------------------------
    select(page, "sensor_A.csv")
    form = page.locator("form.goal")
    form.get_by_role("button", name="목표 저장").click()
    expect(form.locator(".field-error").first).to_be_visible()
    first_err = form.locator(".field-error").first.inner_text()
    form.get_by_label("예측 타깃").select_option("sensor_1")
    form.get_by_label("설비 구분 열 (선택)").select_option("device_id")
    form.get_by_role("button", name="15분").click()
    form.get_by_label("예측 구간", exact=True).fill("1")  # 숫자 + 단위 선택(ADD-UX-11)
    form.get_by_label("예측 구간 단위").select_option("h")
    form.get_by_label("예측 구간", exact=True).press("Enter")
    form.get_by_role("button", name="목표 저장").click()
    expect(page.locator(".toast")).to_contain_text("목표를 저장했습니다")  # 저장 후 전처리로 자동 이동(ADD-UX-09)
    page.wait_for_url(re.compile(r"#/preprocess/"))
    ok("목표 저장", f"미입력 시 오류 '{first_err}' → 저장")

    expect(page.get_by_role("heading", name="전처리", level=1)).to_be_visible()
    expect(page.locator(".back-link")).to_have_text("← 데이터셋·목표")
    page.locator(".back-link").click()  # 뒤로가기(ADD-UX-10)
    expect(page.locator("form.goal")).to_be_visible()

    # 4. 새로고침 후 재조회 -------------------------------------------------------
    page.reload()
    wait_state(page, "sensor_A.csv", "완료")
    wait_state(page, "line2_log.csv", "완료")
    select(page, "sensor_A.csv")
    expect(panel.locator(".shape")).to_have_attribute("aria-label", "576행 5열")
    expect(form.get_by_label("예측 타깃")).to_have_value("sensor_1")
    expect(form.locator(".chip")).to_have_count(2)
    expect(form.get_by_role("status")).to_contain_text("저장된 목표를 불러왔습니다")
    ok("새로고침 후 등록 결과·목표 재조회")

    # 5. 잘못된 CSV → 한글 오류 → 수정 파일로 재등록(같은 ID) -----------------------
    page.set_input_files("input[type=file]", f"{D}/broken_header.csv")
    wait_state(page, "broken_header.csv", "분석 실패")
    r = row(page, "broken_header.csv")
    expect(r.locator(".ds-error")).to_have_text("이름이 비어 있는 열이 있습니다. 첫 행에 모든 열 이름을 넣어 주세요")
    bad_id = [i for i in page.request.get(f"{BASE}/api/datasets").json()["items"] if i["name"] == "broken_header.csv"][0]["dataset_id"]
    select(page, "broken_header.csv")
    expect(panel.locator(".fail-box")).to_contain_text("이름이 비어 있는 열")
    page.screenshot(path=f"{SHOTS}/03_failed_wide.png", full_page=True)
    with page.expect_file_chooser() as fc:
        r.get_by_role("button", name="수정한 파일로 다시 등록").click()
    fc.value.set_files(f"{D}/broken_header_fixed.csv")
    wait_state(page, "broken_header_fixed.csv", "완료")
    fixed = [i for i in page.request.get(f"{BASE}/api/datasets").json()["items"] if i["dataset_id"] == bad_id][0]
    assert fixed["state"] == "PROFILED" and fixed["name"] == "broken_header_fixed.csv" and fixed["row_count"] == 100
    expect(page.locator(".ds-row")).to_have_count(3)
    ok("실패 → 한글 원인 → 수정 파일 재등록", f"같은 ID {bad_id}, 100행")

    # 6. 키보드 조작 ------------------------------------------------------------
    page.reload()
    wait_state(page, "sensor_A.csv", "완료")
    page.keyboard.press("Tab")
    focused = []
    for _ in range(12):
        focused.append(page.evaluate("document.activeElement.innerText || document.activeElement.getAttribute('aria-label') || ''")[:20])
        if "파일 선택 (CSV·엑셀·ZIP)" in focused[-1]:
            break
        page.keyboard.press("Tab")
    assert "파일 선택 (CSV·엑셀·ZIP)" in focused[-1], focused
    chooser = None
    for _ in range(3):  # 헤드리스 Chromium에서 키 입력이 파일 대화상자를 가끔 놓쳐 재시도(키보드로 여는 동작 자체를 검증)
        try:
            with page.expect_file_chooser(timeout=5_000) as fc:
                page.keyboard.press("Enter")
            chooser = fc.value
            break
        except Exception:  # noqa: BLE001
            assert "파일 선택 (CSV·엑셀·ZIP)" in (page.evaluate("document.activeElement.innerText || ''") or ""), "포커스 이동"
    assert chooser is not None, "Enter로 파일 선택 창이 열리지 않음"
    chooser.set_files([])
    page.keyboard.press("Tab")  # 이미지 폴더 등록 버튼(비전 V1에서 추가된 탭 정지점)
    assert "이미지 폴더 등록" in (page.evaluate("document.activeElement.innerText || ''") or "")
    page.keyboard.press("Tab")  # 목록의 선택 항목(로빙 탭)
    first = page.evaluate("document.activeElement.querySelector('.ds-name')?.innerText")
    page.keyboard.press("ArrowDown")
    second = page.evaluate("document.activeElement.querySelector('.ds-name')?.innerText")
    expect(page.locator('.ds-select[aria-current="true"] .ds-name')).to_have_text(second)
    page.keyboard.press("End")
    last = page.evaluate("document.activeElement.querySelector('.ds-name')?.innerText")
    page.keyboard.press("Home")
    expect(page.locator('.ds-select[aria-current="true"] .ds-name')).to_have_text(first)
    ok("키보드: Tab→파일 선택(Enter로 선택 창), 목록 ↑↓/Home/End", f"{first} → {second} → {last} → {first}")

    # 7. 큰 CSV 분석 중에도 목록·상태 조회·다른 데이터셋 선택 ----------------------------
    t_up = time.time()
    page.set_input_files("input[type=file]", f"{D}/big_sensor.csv")
    wait_state(page, "big_sensor.csv", "분석", timeout=120_000)
    t_seen = time.time()
    lat, states = [], []
    select(page, "line2_log.csv")
    expect(panel.locator(".shape")).to_have_attribute("aria-label", "40행 5열")
    select(page, "big_sensor.csv")
    expect(panel.locator(".state-line")).to_contain_text("경과")
    page.screenshot(path=f"{SHOTS}/04_big_profiling_wide.png")
    for _ in range(6):
        t = time.time()
        items = page.request.get(f"{BASE}/api/datasets").json()["items"]
        lat.append(time.time() - t)
        states.append([i["state"] for i in items if i["name"] == "big_sensor.csv"][0])
        time.sleep(0.4)
    elapsed_text = row(page, "big_sensor.csv").locator(".status").inner_text()
    wait_state(page, "big_sensor.csv", "완료", timeout=300_000)
    total = time.time() - t_up
    expect(panel.locator(".shape")).to_have_attribute("aria-label", "800000행 5열")
    assert "PROFILING" in states and max(lat) < 0.5, (states, lat)
    ok("큰 CSV(80만 행, 40.2MB) 분석 중 조작",
       f"분석 중 목록 API 최대 {max(lat)*1000:.0f}ms, 상태 표시 '{elapsed_text}', 다른 데이터셋 선택 가능, 업로드~완료 {total:.1f}s")

    # 8. 좁은 창 ---------------------------------------------------------------
    for w, h, tag in ((820, 1000, "05_narrow_820"), (390, 844, "06_narrow_390")):
        page.set_viewport_size({"width": w, "height": h})
        select(page, "sensor_A.csv")
        nav_w = nav.bounding_box()["width"]
        assert nav_w <= 70, nav_w
        # 이름은 보조기술에 유지
        expect(nav.get_by_role("link", name="데이터셋")).to_have_attribute("aria-current", "page")
        expect(nav.get_by_role("link", name="설정 연결 예정")).to_have_attribute("aria-disabled", "true")
        # 업로드·목록이 구조 패널보다 위
        assert page.locator(".col-left").bounding_box()["y"] < page.locator(".col-right").bounding_box()["y"]
        wrap = page.locator(".table-wrap")
        sw, cw = wrap.evaluate("e => [e.scrollWidth, e.clientWidth]")
        note = f"메뉴 폭 {nav_w:.0f}px, 표 {'가로 스크롤' if sw > cw else '스크롤 불필요'}({sw}/{cw})"
        if sw > cw:
            wrap.evaluate("e => e.scrollLeft = e.scrollWidth")
            assert wrap.locator(".feat-name").first.is_visible()  # 이름 열 고정
        page.screenshot(path=f"{SHOTS}/{tag}.png", full_page=True)
        # 키보드 포커스 시 메뉴 이름 툴팁(이름·연결 예정 표시)
        page.mouse.move(w - 5, h - 5)
        nav.get_by_role("link", name="데이터셋").focus()
        for _ in range(5):
            page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.getAttribute('title')") == "설정 (연결 예정)"
        tip = nav.get_by_role("link", name="설정 연결 예정").locator(".nav-text")
        box = tip.bounding_box()
        assert box and box["width"] > 40, box
        page.screenshot(path=f"{SHOTS}/{tag}_tooltip.png", clip={"x": 0, "y": 0, "width": min(w, 400), "height": 260})
        note += f", 키보드 포커스 툴팁 '{tip.inner_text().replace(chr(10), ' ')}'"
        ok(f"좁은 창 {w}px", note)

    # 9. Windows 앱 창(pywebview) 창 제어 버튼 — API를 흉내 낸 새 페이지에서 확인 ----------
    win = ctx.new_page()
    win.add_init_script("""
      window.__calls = [];
      window.pywebview = { api: {
        minimize: async () => window.__calls.push('minimize'),
        toggle_maximized: async () => { window.__calls.push('toggle'); return true; },
        close: async () => window.__calls.push('close') } };
    """)
    win.goto(BASE)
    controls = win.get_by_role("group", name="창 제어")
    expect(controls).to_be_visible()
    controls.get_by_role("button", name="최소화").click()
    controls.get_by_role("button", name="최대화").click()
    expect(controls.get_by_role("button", name="이전 크기로")).to_be_visible()
    controls.get_by_role("button", name="창 닫기").click()
    win.get_by_role("alertdialog", name="앱을 종료합니다").get_by_role("button", name="종료").click()  # 종료 확인(ADD-UX-03)
    win.wait_for_function("window.__calls.length === 3")
    assert win.evaluate("window.__calls") == ["minimize", "toggle", "close"]
    win.close()
    ok("앱 창 제어 버튼(pywebview API 연결)", "최소화·최대화→이전 크기로·닫기 호출 확인")

    print(json.dumps({"console_errors": errors}, ensure_ascii=False))
    # 목표 검증 실패(의도한 422) 응답의 네트워크 로그는 앱 오류가 아니다
    app_errors = [e for e in errors if "status of 422" not in e]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음")
    browser.close()

print("\n".join(f"{s}  {n}" for n, s in results))
sys.exit(0)
