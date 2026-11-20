"""S3 학습 설정 브라우저 검증 (Playwright/Chromium).

사전 준비: pip install playwright && python -m playwright install chromium
실행: 저장소 루트에서 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>` 실행 후
      python tests/e2e/check_s3_browser.py [스크린샷 폴더]   (기본 ./workspace/e2e-shots-s3)
예제 데이터 outputs/workbench-sensor-example/motor_sensor_example.csv 가 있으면 그것을, 없으면 합성 CSV를 쓴다.
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
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-s3"
os.makedirs(SHOTS, exist_ok=True)
EXAMPLE = Path(os.environ.get("MLWB_EXAMPLE_CSV") or ROOT / "outputs" / "workbench-sensor-example" / "motor_sensor_example.csv")
if EXAMPLE.exists():
    DATA, NAME, TARGET = EXAMPLE, EXAMPLE.name, "temperature_c"
else:
    DATA = Path(tempfile.mkdtemp(prefix="mlwb-e2e-s3-")) / "sensor_A.csv"
    DATA.write_bytes(sensor_csv(2880))
    NAME, TARGET = DATA.name, "sensor_1"
results: list[str] = []


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def card(page, name):
    return page.locator(".card", has=page.locator(".card-name", has_text=re.compile(f"^{re.escape(name)}$")))


def pct(page, label):
    return page.locator(".split-field", has=page.get_by_text(label, exact=True)).locator("input")


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 950}, device_scale_factor=2, locale="ko-KR")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    nav = page.get_by_role("navigation", name="주 메뉴")
    search = page.get_by_role("searchbox", name="데이터셋 이름 검색")

    # 1. S1 목표 → S2 전처리 저장 → S3 진입 -----------------------------------------
    page.goto(BASE)
    expect(nav.get_by_role("link", name="학습 설정 목표 저장 후 사용", exact=True)).to_have_attribute("aria-disabled", "true")
    page.set_input_files("input[type=file]", str(DATA))
    expect(page.locator(".ds-row .status").first).to_have_text("완료", timeout=60_000)
    page.locator(".ds-select").first.click()
    form = page.locator("form.goal")
    form.get_by_label("예측 타깃").select_option(TARGET)
    form.get_by_label("설비 구분 열 (선택)").select_option("device_id")
    form.get_by_role("button", name="15분").click()
    form.get_by_role("button", name="1시간").click()
    form.get_by_role("button", name="목표 저장").click()
    expect(page.locator(".toast")).to_contain_text("목표를 저장했습니다")  # 저장 후 전처리로 자동 이동(ADD-UX-09)
    page.wait_for_url(re.compile(r"#/preprocess/"))
    nav.get_by_role("link", name=re.compile("^전처리")).click()
    expect(page.locator("ol.cards .card")).to_have_count(4, timeout=15_000)
    card(page, "결측 처리").locator(".card-check").uncheck()
    card(page, "lag 피처").locator(".card-main").click()
    page.locator(".prep-detail").get_by_label(re.compile("^몇 칸 전 값을 쓸지")).fill("1, 4, 8")
    page.get_by_role("button", name="저장하고 학습 설정으로").click()
    expect(page.get_by_role("heading", name="학습 설정", level=1)).to_be_visible()
    assert "#/train/ds_" in page.url
    expect(page.locator(".prep-line")).to_contain_text("세션 분리 → 리샘플링 → lag 피처 (제외: 결측 처리)")
    expect(nav.locator('[aria-current="page"]')).to_contain_text("학습 설정")
    ok("S1 목표 → S2 저장하고 학습 설정으로 → S3 진입", f"{NAME}, 전처리 요약 표시")

    # 2. 모델 섹션 ------------------------------------------------------------------
    first = page.locator(".model-opt").first
    expect(first).to_contain_text("XGBoost 예측")
    expect(first).to_contain_text("timeseries.forecasting.xgboost@1.0.0")
    expect(first.locator(".tag")).to_have_text("추천")
    expect(first.locator(".status")).to_have_text("계획 미리보기 · 실행 불가")
    expect(first.locator("input[type=radio]")).to_be_checked()
    expect(page.locator(".preview-note")).to_contain_text("확인·실행 화면에서 승인이 차단됩니다")
    ok("모델 후보: 정확한 버전·추천·구현 상태·계획 미리보기 표시")

    # 3. 설정 입력과 항목별 오류 ------------------------------------------------------
    page.locator("input[name=budget]").nth(1).check()
    days = page.get_by_label("최근 일수")
    days.fill("0")
    expect(page.locator(".tsec").nth(1).locator(".field-error")).to_contain_text("1 이상이어야 합니다", timeout=5_000)
    days.fill("30")
    expect(page.locator(".seg-used.is-partial")).to_have_text("최근 30일 사용")
    pct(page, "학습 (%)").fill("70")
    pct(page, "검증 (%)").fill("20")
    pct(page, "테스트 (%)").fill("20")
    expect(page.locator(".split-sum")).to_have_text("합계 110%")
    expect(page.locator(".tsec").nth(2).locator(".field-error").first).to_contain_text("합이 100%가 되어야 합니다", timeout=5_000)
    pct(page, "학습 (%)").fill("60")
    expect(page.locator(".split-sum")).to_have_text("합계 100%")
    for m in ("MAE", "RMSE"):
        page.locator(".metric-checks label", has_text=m).first.locator("input").uncheck()
    expect(page.locator(".tsec").nth(2).locator(".field-error")).to_contain_text("하나 이상 선택하세요", timeout=5_000)
    page.screenshot(path=f"{SHOTS}/01_errors_wide.png", full_page=True)
    for m in ("MAE", "RMSE"):
        page.locator(".metric-checks label", has_text=m).first.locator("input").check()
    depth = page.locator(".model-params").get_by_label(re.compile("^최대 깊이"))
    depth.fill("100")
    expect(page.locator(".model-params .field-error")).to_contain_text("16 이하여야 합니다", timeout=5_000)
    depth.fill("8")
    expect(page.locator(".model-params .field-error")).to_have_count(0, timeout=5_000)
    page.locator(".checks label", has_text="설비별").locator("input").check()
    expect(page.locator(".validate-summary")).to_contain_text("형식 문제 없음", timeout=5_000)
    page.get_by_role("button", name="설정 저장").click()
    expect(page.locator(".save-bar [role=status]")).to_contain_text("학습 설정을 저장했습니다")
    saved_text = page.locator(".save-bar [role=status]").inner_text()
    ok("최근 30일·60/20/20·MAE·RMSE·max_depth 8·설비별 저장, 오류는 입력 근처 표시",
       "최근 0일·합계 110%·지표 0개·max_depth 100 확인 → " + saved_text)

    # 4. 새로고침 후 복원 + S1/S2 보존 -----------------------------------------------
    page.reload()
    expect(page.get_by_label("최근 일수")).to_have_value("30")
    expect(page.locator("input[name=budget]").nth(1)).to_be_checked()
    for label, v in (("학습 (%)", "60"), ("검증 (%)", "20"), ("테스트 (%)", "20")):
        expect(pct(page, label)).to_have_value(v)
    expect(page.locator(".metric-checks label", has_text="MAE").first.locator("input")).to_be_checked()
    expect(page.locator(".metric-checks label", has_text="RMSE").first.locator("input")).to_be_checked()
    expect(page.locator(".model-params").get_by_label(re.compile("^최대 깊이"))).to_have_value("8")
    expect(page.locator(".checks label", has_text="설비별").locator("input")).to_be_checked()
    page.locator("a.btn", has_text="← 전처리").click()  # 아래 버튼(위쪽 뒤로가기 링크와 같은 이름)
    expect(page.locator("ol.cards .card")).to_have_count(3)
    assert page.locator("ol.cards .card-name").all_inner_texts() == ["세션 분리", "리샘플링", "lag 피처"]
    assert page.locator(".cards-excluded .card-name").all_inner_texts() == ["결측 처리"]
    card(page, "lag 피처").locator(".card-main").click()
    expect(page.locator(".prep-detail").get_by_label(re.compile("^몇 칸 전 값을 쓸지"))).to_have_value("1, 4, 8")
    nav.get_by_role("link", name="데이터셋").click()
    expect(page.locator("form.goal").get_by_label("예측 타깃")).to_have_value(TARGET)
    expect(page.locator("form.goal").get_by_label("설비 구분 열 (선택)")).to_have_value("device_id")
    nav.get_by_role("link", name=re.compile("^학습 설정")).click()
    expect(page.get_by_label("최근 일수")).to_have_value("30")
    ok("새로고침 후 S3 복원, S2 전처리·제외·파라미터와 S1 목표 보존")

    # 5. 상단 검색: 입력만 하면 머무르고, Enter면 이동(저장하지 않은 편집은 선택) ----------------
    page.get_by_label("최근 일수").fill("45")
    search.fill("motor" if "motor" in NAME else "sensor")
    page.wait_for_timeout(400)
    assert "#/train/" in page.url
    expect(page.get_by_label("최근 일수")).to_have_value("45")
    search.press("Enter")
    prompt = page.get_by_role("alertdialog")
    expect(prompt).to_contain_text("저장하지 않은 변경이 있습니다")
    prompt.get_by_role("button", name="취소").click()
    assert "#/train/" in page.url and page.get_by_label("최근 일수").input_value() == "45"
    search.press("Enter")
    page.get_by_role("alertdialog").get_by_role("button", name="버리고 이동").click()
    expect(page.get_by_role("heading", name="데이터셋", level=1)).to_be_visible()
    expect(page.locator(".ds-row")).to_have_count(1)  # 검색어로 거른 목록
    nav.get_by_role("link", name=re.compile("^학습 설정")).click()
    expect(page.get_by_label("최근 일수")).to_have_value("30")  # 버린 변경
    page.get_by_label("최근 일수").fill("45")
    search.press("Enter")
    page.get_by_role("alertdialog").get_by_role("button", name="저장하고 이동").click()
    expect(page.get_by_role("heading", name="데이터셋", level=1)).to_be_visible()
    nav.get_by_role("link", name=re.compile("^학습 설정")).click()
    expect(page.get_by_label("최근 일수")).to_have_value("45")  # 저장된 변경
    search.press("Enter")  # 변경 없음 → 바로 이동
    expect(page.get_by_role("heading", name="데이터셋", level=1)).to_be_visible()
    nav.get_by_role("link", name=re.compile("^전처리")).click()
    search.fill("없는이름")
    page.wait_for_timeout(300)
    assert "#/preprocess/" in page.url
    search.press("Enter")
    expect(page.get_by_role("status").filter(has_text="검색 결과가 없습니다.")).to_be_visible()
    search.fill("")
    ok("상단 검색: 편집 화면에서 입력 중 유지, Enter 시 이동", "미저장 시 취소·버리고 이동·저장하고 이동, 변경 없으면 바로 이동(S2·S3)")

    # 6. 리비전 충돌 ---------------------------------------------------------------
    nav.get_by_role("link", name=re.compile("^학습 설정")).click()
    expect(page.get_by_label("최근 일수")).to_have_value("45")
    page2 = ctx.new_page()
    page2.goto(page.url)
    page2.get_by_label("최근 일수").fill("14")
    page2.get_by_role("button", name="설정 저장").click()
    expect(page2.locator(".save-bar [role=status]")).to_contain_text("학습 설정을 저장했습니다")
    page.get_by_label("최근 일수").fill("60")
    page.get_by_role("button", name="설정 저장").click()
    expect(page.locator(".banner-warn")).to_contain_text("다른 곳에서 초안이 수정되었습니다")
    page.get_by_role("button", name="최신 상태 불러오기").click()
    expect(page.get_by_label("최근 일수")).to_have_value("14")
    page2.close()
    ok("리비전 충돌 안내 → 최신 상태 불러오기", "다른 탭 저장값 14일 반영")

    # 6-1. 요청 예시 그대로: 최근 30일·시간순 70/15/15·MAE·RMSE → 저장 → 다시 열기 ----------------
    page.get_by_label("최근 일수").fill("30")
    for label, v in (("학습 (%)", "70"), ("검증 (%)", "15"), ("테스트 (%)", "15")):
        pct(page, label).fill(v)
    for m, on in (("MAE", True), ("RMSE", True), ("R²", False), ("sMAPE", False)):
        box = page.locator(".metric-checks label", has_text=m).first.locator("input")
        box.check() if on else box.uncheck()
    page.get_by_role("button", name="설정 저장").click()
    expect(page.locator(".save-bar [role=status]")).to_contain_text("학습 설정을 저장했습니다")
    page.goto(BASE)  # 앱을 새로 연 것처럼 처음 화면에서 다시 진입
    page.locator(".ds-select").first.click()
    nav.get_by_role("link", name=re.compile("^학습 설정")).click()
    expect(page.get_by_label("최근 일수")).to_have_value("30")
    for label, v in (("학습 (%)", "70"), ("검증 (%)", "15"), ("테스트 (%)", "15")):
        expect(pct(page, label)).to_have_value(v)
    checked = [m for m in ("MAE", "RMSE", "R²", "sMAPE")
               if page.locator(".metric-checks label", has_text=m).first.locator("input").is_checked()]
    assert checked == ["MAE", "RMSE"], checked
    expect(page.locator(".prep-line")).to_contain_text("세션 분리 → 리샘플링 → lag 피처 (제외: 결측 처리)")
    ok("요청 예시: 최근 30일·70/15/15·MAE·RMSE 저장 후 다시 열어 복원", "기존 전처리 함께 유지")

    # 7. 좁은 창 ---------------------------------------------------------------
    page.screenshot(path=f"{SHOTS}/02_training_wide.png", full_page=True)
    for w, h, tag in ((820, 1000, "03_narrow_820"), (390, 900, "04_narrow_390")):
        page.set_viewport_size({"width": w, "height": h})
        time.sleep(0.2)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), w
        page.screenshot(path=f"{SHOTS}/{tag}.png", full_page=True)
        ok(f"좁은 창 {w}px", "가로 넘침 없음")

    app_errors = [e for e in errors if not re.search(r"status of (409|422)", e)]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음", f"의도한 409/422 응답 로그 {len(errors)}건 제외")
    browser.close()

print("\n".join(results))
