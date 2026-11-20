"""비전 V1 브라우저 검증: 이미지 ZIP 업로드 → S1-V 구조(요약·폴더 역할·예시) → 역할 수정·보정·목표 저장 → 새로고침 복원,
이미지 폴더 등록(제자리 읽기), 오류 안내, 390px.

실행: 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>` 후 python tests/e2e/check_vision_browser.py [스크린샷 폴더]
합성 MVTec 형식 이미지를 임시 폴더에 만들어 쓴다(외부 다운로드 없음). 폴더 등록은 서버와 같은 PC에서 실행할 때만 의미가 있다.
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
from vision_samples import make_mvtec, zip_dir  # noqa: E402

BASE = os.environ.get("MLWB_BASE_URL", "http://127.0.0.1:8000")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-vision"
os.makedirs(SHOTS, exist_ok=True)
D = Path(tempfile.mkdtemp(prefix="mlwb-vis-"))
zip_path = D / "bottle_synth.zip"
zip_path.write_bytes(zip_dir(make_mvtec(D / "zipsrc", size=64)))
folder = make_mvtec(D / "folder_src", category="cable_synth", train=15, test_good=4, defects={"bent": 3}, size=48, masks=False)
results: list[str] = []


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 950}, device_scale_factor=2, locale="ko-KR")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    bad: list[str] = []
    page.on("response", lambda r: r.status >= 400 and bad.append(f"{r.status} {r.request.method} {r.url}"))

    # 1. ZIP 업로드 → 구조 ---------------------------------------------------------------
    page.goto(BASE)
    page.set_input_files("input[type=file]", str(zip_path))
    row = page.locator(".ds-row", has_text="bottle_synth.zip")
    expect(row.locator(".status")).to_have_text("완료", timeout=60_000)
    expect(row).to_contain_text("이미지 39장")
    row.locator(".ds-select").click()
    panel = page.locator(".panel.vision")
    expect(panel.locator(".vis-summary")).to_contain_text("이미지 39장")
    expect(panel.locator(".vis-summary")).to_contain_text("GT 마스크 7장")
    expect(panel).to_contain_text("손상 0장")
    expect(panel).to_contain_text("불량-마스크 짝 7/7")
    rows = panel.locator(".vis-folders tbody tr")
    expect(rows).to_have_count(6)
    expect(panel.get_by_label("bottle/train/good 역할")).to_have_value("train_normal")
    expect(panel.get_by_label("bottle/test/crack 역할")).to_have_value("test_defect")
    expect(panel.get_by_label("bottle/ground_truth/crack 역할")).to_have_value("gt_mask")
    imgs = panel.locator(".vis-examples img")
    expect(imgs).to_have_count(2)
    page.wait_for_function("Array.from(document.querySelectorAll('.vis-examples img')).every(i => i.complete && i.naturalWidth === 64)")
    expect(panel.locator(".vis-examples figcaption").nth(1)).to_contain_text("불량 · crack")
    page.screenshot(path=f"{SHOTS}/01_structure.png", full_page=True)
    ok("ZIP 업로드 → S1-V: 39장·GT 7장·손상 0·짝 7/7, 폴더 6개 역할 추정, 예시 정상·불량 2장 표시")

    # 2. 역할 수정·보정·목표 저장 → 복원 ------------------------------------------------------
    expect(panel.locator(".vis-counts")).to_contain_text("정상 학습 18장 · 보정 2장 · 정상 평가 5장 · 불량 평가 7장")
    panel.get_by_label("bottle/test/good 역할").select_option("calibration_normal")
    expect(panel.locator(".vis-folders tr.cmp-changed")).to_have_count(1)
    panel.get_by_label("'보정 정상' 역할 폴더 사용").check()
    expect(panel.locator(".vis-counts")).to_contain_text("정상 학습 20장 · 보정 5장 · 정상 평가 0장")
    panel.get_by_role("button", name="목표 저장").click()
    expect(panel.locator(".saved-text")).to_have_text("목표를 저장했습니다")
    expect(panel.locator(".field-warn", has_text="정상과 불량 이미지가 모두")).to_be_visible()
    page.reload()
    row.locator(".ds-select").click()
    expect(panel.locator(".saved-text")).to_have_text("저장된 목표를 불러왔습니다")
    expect(panel.get_by_label("bottle/test/good 역할")).to_have_value("calibration_normal")
    expect(panel.get_by_label("'보정 정상' 역할 폴더 사용")).to_be_checked()
    ok("역할 수정(정상 평가 → 보정 정상)·보정 방식 → 장수 즉시 반영 → 저장(경고 표시) → 새로고침 후 복원")

    # 3. 오류: 정상 학습 부족 ------------------------------------------------------------------
    panel.get_by_label("bottle/train/good 역할").select_option("ignore")
    panel.get_by_role("button", name="목표 저장").click()
    expect(panel.locator(".field-error").first).to_contain_text("정상 학습 이미지가 0장")
    page.screenshot(path=f"{SHOTS}/02_error.png", full_page=True)
    ok("정상 학습 폴더를 빼고 저장 → '정상 학습 이미지가 0장…최소 10장' 오류, 저장 안 됨")

    # 4. 폴더 등록 ------------------------------------------------------------------------------
    page.get_by_role("button", name="이미지 폴더 등록").click()
    page.get_by_label(re.compile("이미지 폴더 전체 경로")).fill(str(folder))
    page.get_by_role("button", name="등록", exact=True).click()
    frow = page.locator(".ds-row", has_text="cable_synth")
    expect(frow.locator(".status")).to_have_text("완료", timeout=60_000)
    expect(frow).to_contain_text("이미지 22장")
    expect(panel).to_contain_text("로컬 폴더(읽기 전용)")
    expect(panel).to_contain_text("GT 마스크가 없어 픽셀 지표(AU-PRO)는 '적용 불가(N/A)'")
    assert str(D) not in page.content()  # 화면에 절대경로를 보여 주지 않음
    page.get_by_role("button", name="이미지 폴더 등록").click()
    page.get_by_label(re.compile("이미지 폴더 전체 경로")).fill("relative/path")
    page.get_by_role("button", name="등록", exact=True).click()
    expect(page.locator(".folder-form .field-error")).to_contain_text("전체 경로")
    page.screenshot(path=f"{SHOTS}/03_folder.png", full_page=True)
    ok("이미지 폴더 등록(제자리) → 22장·GT 없음 안내·절대경로 미표시, 상대 경로는 오류 안내")

    page.set_viewport_size({"width": 390, "height": 900})
    row.locator(".ds-select").click()
    expect(panel.locator(".vis-folders")).to_be_visible()
    time.sleep(0.3)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=f"{SHOTS}/04_narrow_390.png", full_page=True)
    ok("좁은 창 390px S1-V", "가로 넘침 없음")

    # 의도한 오류 응답 2건(목표 422·상대 경로 400)만 허용. 이미지 404 등은 실패로 본다.
    expected = [b for b in bad if re.match(r"(422 PUT .*/vision-goal|400 POST .*/datasets/folder)$", b)]
    assert len(expected) == 2 and len(bad) == 2, bad
    app_errors = [e for e in errors if not re.search(r"status of (400|422)", e)]
    assert not app_errors, (app_errors, bad)
    ok("브라우저 콘솔 오류 없음", "의도한 오류 응답 2건 외 4xx/5xx 없음(데이터셋 전환 시 이미지 404 회귀 확인)")
    browser.close()

print("\n".join(results))
