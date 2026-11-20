"""S4 확인·승인 브라우저 검증 (Playwright/Chromium).

사전 준비: pip install playwright && python -m playwright install chromium
실행 (저장소 루트):
  1) `python tests/e2e/serve_ready_registry.py <빈 폴더A> 8000 preview` → 명세만 있는 카탈로그(모두 미구현): 승인 차단 확인
  2) `python tests/e2e/serve_ready_registry.py <빈 폴더B> 8001 ready`   → 테스트 전용 실행 가능 목록: 승인 흐름 확인
     (실제 학습 시작·결과는 check_p2_browser.py)
  3) python tests/e2e/check_s4_browser.py [스크린샷 폴더]
     주소는 MLWB_BASE_URL(기본 http://127.0.0.1:8000), MLWB_READY_URL(기본 http://127.0.0.1:8001).
     MLWB_READY_URL 서버가 없으면 2부를 건너뛴다(결과에 표시).
"""

import os
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
from test_csv_dataset import sensor_csv  # noqa: E402

BASE = os.environ.get("MLWB_BASE_URL", "http://127.0.0.1:8000")
READY = os.environ.get("MLWB_READY_URL", "http://127.0.0.1:8001")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-s4"
os.makedirs(SHOTS, exist_ok=True)
EXAMPLE = Path(os.environ.get("MLWB_EXAMPLE_CSV") or ROOT / "outputs" / "workbench-sensor-example" / "motor_sensor_example.csv")
if EXAMPLE.exists():
    DATA, NAME, TARGET = EXAMPLE, EXAMPLE.name, "temperature_c"
else:
    DATA = Path(tempfile.mkdtemp(prefix="mlwb-e2e-s4-")) / "sensor_A.csv"
    DATA.write_bytes(sensor_csv(2880))
    NAME, TARGET = DATA.name, "sensor_1"
results: list[str] = []


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def pct(page, label):
    return page.locator(".split-field", has=page.get_by_text(label, exact=True)).locator("input")


def alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def through_s3(page, base: str, *, days="30", split=("70", "15", "15")):
    """S1 목표 → S2 전처리 저장 → S3 설정 입력(저장하지 않은 상태로 둠)."""
    nav = page.get_by_role("navigation", name="주 메뉴")
    page.goto(base)
    page.set_input_files("input[type=file]", str(DATA))
    expect(page.locator(".ds-row .status").first).to_have_text("완료", timeout=60_000)
    page.locator(".ds-select").first.click()
    form = page.locator("form.goal")
    form.get_by_label("예측 타깃").select_option(TARGET)
    form.get_by_label("설비 구분 열 (선택)").select_option("device_id")
    form.get_by_role("button", name="15분").click()
    form.get_by_role("button", name="목표 저장").click()
    expect(page.locator(".toast")).to_contain_text("목표를 저장했습니다")  # 저장 후 전처리로 자동 이동(ADD-UX-09)
    page.wait_for_url(re.compile(r"#/preprocess/"))
    nav.get_by_role("link", name=re.compile("^전처리")).click()
    expect(page.locator("ol.cards .card")).to_have_count(4, timeout=15_000)
    page.get_by_role("button", name=re.compile("학습 설정으로$")).click()
    expect(page.get_by_role("heading", name="학습 설정", level=1)).to_be_visible()
    page.locator("input[name=budget]").nth(1).check()
    page.get_by_label("최근 일수").fill(days)
    for label, v in zip(("학습 (%)", "검증 (%)", "테스트 (%)"), split):
        pct(page, label).fill(v)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 950}, device_scale_factor=2, locale="ko-KR")
    page = ctx.new_page()
    errors: list[str] = []
    bodies: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))

    def keep(r):  # 확인·승인 응답 본문을 모아 승인 토큰 노출 여부를 검사
        if re.search(r"/api/datasets/[^/]+/(confirmation|approve)$", r.url):
            try:
                bodies.append(r.text())
            except Exception:  # noqa: BLE001 - 본문이 이미 비워진 경우
                pass

    page.on("response", keep)
    nav = page.get_by_role("navigation", name="주 메뉴")

    # ===== 1부: 내장 레지스트리(미구현 후보) =====================================
    through_s3(page, BASE)
    page.get_by_role("button", name="저장하고 확인·실행으로").click()
    expect(page.get_by_role("heading", name="확인·실행", level=1)).to_be_visible()
    assert "#/confirm/ds_" in page.url
    expect(nav.locator('[aria-current="page"]')).to_contain_text("학습 설정")
    ok("S3 '저장하고 확인·실행으로' → S4 진입", "메뉴 현재 위치는 학습 설정")

    summary = page.locator(".conf-summary")
    expect(summary).to_contain_text(NAME)
    expect(summary).to_contain_text(f"타깃 {TARGET}")
    expect(summary).to_contain_text("설비 device_id")
    expect(summary).to_contain_text("15분 뒤")
    expect(summary.locator(".conf-steps li")).to_have_count(5)  # 전처리 4 + 모델 1
    expect(summary).to_contain_text("XGBoost 예측")
    expect(summary).to_contain_text("최근 30일")
    expect(summary).to_contain_text("실제 기간·행 수는 학습 준비 단계에서 계산합니다")
    expect(summary).to_contain_text("시간순 70/15/15 · 간격 자동")
    expect(summary).to_contain_text("MAE, RMSE · 기준 예측 직전값")
    expect(summary).to_contain_text("승인 전")
    ok("S4 요약: 데이터셋·목표·전처리 순서·모델·학습량·분할·평가", "최근 30일 · 70/15/15 · MAE·RMSE, 미산출 기간은 안내 문구")

    expect(page.locator(".banner-fail.conf-head")).to_contain_text("실행을 막는 오류")
    fixes = page.locator(".conf-issues li.is-error")
    n_err = fixes.count()
    assert n_err >= 2, n_err
    expect(page.locator(".conf-issues")).to_contain_text("전처리에서 고치기 →")
    expect(page.locator(".conf-issues")).to_contain_text("학습 설정에서 고치기 →")
    check = page.get_by_label(re.compile("^위 설정으로 승인합니다"))
    expect(check).to_be_disabled()
    expect(page.get_by_role("button", name="설정 승인")).to_be_disabled()
    expect(page.get_by_role("button", name=re.compile("^학습 시작"))).to_be_disabled()
    page.screenshot(path=f"{SHOTS}/01_confirm_blocked.png", full_page=True)
    ok("미구현 후보 승인 차단", f"오류 {n_err}건, 체크·승인·학습 시작 비활성")

    page.locator(".conf-issues li.is-error", has_text="lag").get_by_role("link", name="전처리에서 고치기 →").first.click()
    expect(page.get_by_role("heading", name="전처리", level=1)).to_be_visible()
    page.go_back()
    expect(page.get_by_role("heading", name="확인·실행", level=1)).to_be_visible()
    page.locator(".conf-issues").get_by_role("link", name="학습 설정에서 고치기 →").first.click()
    expect(page.get_by_role("heading", name="학습 설정", level=1)).to_be_visible()
    expect(page.get_by_label("최근 일수")).to_have_value("30")  # 저장한 설정 그대로
    ok("조치할 단계로 돌아가기", "오류 항목 링크 → S2 전처리 / S3 학습 설정(저장값 유지)")

    page.get_by_role("button", name="다음: 확인·실행").click()  # 변경 없으면 바로 이동
    expect(page.get_by_role("heading", name="확인·실행", level=1)).to_be_visible()
    for w, h, tag in ((820, 1000, "02_narrow_820"), (390, 900, "03_narrow_390")):
        page.set_viewport_size({"width": w, "height": h})
        time.sleep(0.2)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), w
        page.screenshot(path=f"{SHOTS}/{tag}.png", full_page=True)
    page.set_viewport_size({"width": 1440, "height": 950})
    ok("좁은 창 820·390px", "가로 넘침 없음")

    # ===== 2부: 테스트 전용 실행 가능 목록 ======================================
    if not alive(READY):
        results.append(f"SKIP  승인 흐름 — {READY} 서버 없음 (tests/e2e/serve_ready_registry.py)")
        print(results[-1])
    else:
        through_s3(page, READY)
        page.get_by_role("button", name="저장하고 확인·실행으로").click()
        expect(page.locator(".conf-head")).to_contain_text("검증 통과")
        btn = page.get_by_role("button", name="설정 승인")
        expect(btn).to_be_disabled()  # 확인 체크 전
        page.get_by_label(re.compile("^위 설정으로 승인합니다")).check()
        expect(btn).to_be_enabled()
        btn.click()
        appr = page.locator(".conf-approval")
        expect(appr).to_contain_text("승인됨")
        expect(appr.locator("code")).to_contain_text("sha256:")
        h1 = appr.locator("code").get_attribute("title")
        expect(page.get_by_role("button", name="승인됨")).to_be_disabled()
        # 'ready' 목록은 명세만 실행 가능으로 바꾼 것이라 학습 구현이 없다 → 학습 시작 비활성
        expect(page.get_by_role("button", name=re.compile("^학습 시작"))).to_be_disabled()
        page.screenshot(path=f"{SHOTS}/04_confirm_approved.png", full_page=True)
        page.reload()
        expect(page.locator(".conf-approval code")).to_have_attribute("title", h1)
        ok("확인 체크 → 설정 승인 → 승인 상태·plan_hash 표시, 새로고침 후 같은 해시", h1[:19] + "…")

        # 실행 설정 변경 → 승인 무효화 → 다시 승인
        page.locator("a.btn", has_text="← 학습 설정").click()  # 아래 버튼(위쪽 뒤로가기 링크와 같은 이름)
        page.get_by_label("최근 일수").fill("14")
        page.get_by_role("button", name="저장하고 확인·실행으로").click()
        expect(page.locator(".banner-warn")).to_contain_text("이전 승인이 무효화되었습니다")
        expect(page.locator(".conf-approval")).to_contain_text("다시 승인 필요")
        page.get_by_label(re.compile("^위 설정으로 승인합니다")).check()
        page.get_by_role("button", name="설정 승인").click()
        expect(page.locator(".conf-approval")).to_contain_text("승인됨")
        h2 = page.locator(".conf-approval code").get_attribute("title")
        assert h2 != h1
        ok("학습량 변경 → '이전 승인 무효화' 안내 → 다시 승인", "새 plan_hash")

        # 다른 탭에서 바꾼 뒤 이 탭에서 승인 → 리비전 충돌 안내
        page.locator("a.btn", has_text="← 학습 설정").click()  # 아래 버튼(위쪽 뒤로가기 링크와 같은 이름)
        page.get_by_label("최근 일수").fill("21")
        page.get_by_role("button", name="저장하고 확인·실행으로").click()
        expect(page.locator(".conf-approval")).to_contain_text("다시 승인 필요")
        page2 = ctx.new_page()
        page2.goto(page.url.replace("#/confirm/", "#/train/"))
        page2.get_by_label("최근 일수").fill("7")
        page2.get_by_role("button", name="설정 저장").click()
        expect(page2.locator(".save-bar [role=status]")).to_contain_text("학습 설정을 저장했습니다")
        page2.close()
        page.get_by_label(re.compile("^위 설정으로 승인합니다")).check()
        page.get_by_role("button", name="설정 승인").click()
        expect(page.locator(".banner-warn", has_text="다른 곳에서 초안이 수정되었습니다")).to_be_visible()
        page.get_by_role("button", name="최신 상태 불러오기").click()
        expect(page.locator(".conf-summary")).to_contain_text("최근 7일")
        ok("다른 탭 저장 후 승인 → 충돌 안내 → 최신 상태(최근 7일) 불러오기")

    leaked = [b for b in bodies if "approve_" in b or "approval_id" in b]
    assert not leaked, leaked[:1]
    ok("화면 응답에 승인 토큰 없음", f"확인·승인 응답 {len(bodies)}건 검사")
    app_errors = [e for e in errors if not re.search(r"status of (409|422)", e)]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음", f"의도한 409/422 응답 로그 {len(errors)}건 제외")
    browser.close()

print("\n".join(results))
