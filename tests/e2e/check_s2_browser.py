"""S2 전처리 화면 브라우저 검증 (Playwright/Chromium).

사전 준비: pip install playwright && python -m playwright install chromium
실행: 저장소 루트에서 빈 워크스페이스로 `mlwb serve --workspace <빈 폴더>` 실행 후
      python tests/e2e/check_s2_browser.py [스크린샷 폴더]   (기본 ./workspace/e2e-shots-s2)
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
from test_csv_dataset import sensor_csv  # noqa: E402

BASE = os.environ.get("MLWB_BASE_URL", "http://127.0.0.1:8000")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else "workspace/e2e-shots-s2"
os.makedirs(SHOTS, exist_ok=True)
D = tempfile.mkdtemp(prefix="mlwb-e2e-s2-")
Path(D, "sensor_A.csv").write_bytes(sensor_csv(288))
Path(D, "no_goal.csv").write_bytes(sensor_csv(30, devices=("Z",)))
results: list[str] = []


def ok(name: str, detail: str = "") -> None:
    results.append(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def card_names(page, excluded=False):
    sel = ".cards-excluded .card-name" if excluded else "ol.cards .card-name"
    return page.locator(sel).all_inner_texts()


def card(page, name):
    return page.locator(".card", has=page.locator(".card-name", has_text=re.compile(f"^{re.escape(name)}$")))


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 950}, device_scale_factor=2, locale="ko-KR")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    nav = page.get_by_role("navigation", name="주 메뉴")

    # 1. 업로드 → 목표 저장 전에는 전처리 잠김 → 저장 후 진입 -------------------------
    page.goto(BASE)
    page.set_input_files("input[type=file]", f"{D}/sensor_A.csv")
    expect(page.locator(".ds-row .status").first).to_have_text("완료", timeout=30_000)
    page.locator(".ds-select").first.click()
    prep_link = nav.get_by_role("link", name=re.compile("^전처리"))
    expect(prep_link).to_have_attribute("aria-disabled", "true")
    expect(prep_link).to_contain_text("목표 저장 후 사용")
    form = page.locator("form.goal")
    form.get_by_label("예측 타깃").select_option("sensor_1")
    form.get_by_label("설비 구분 열 (선택)").select_option("device_id")
    form.get_by_role("button", name="15분").click()
    form.get_by_role("button", name="1시간").click()
    form.get_by_role("button", name="목표 저장").click()
    expect(page.locator(".toast")).to_contain_text("목표를 저장했습니다")  # 저장 후 전처리로 자동 이동(ADD-UX-09)
    page.wait_for_url(re.compile(r"#/preprocess/"))
    expect(prep_link).not_to_have_attribute("aria-disabled", "true")
    prep_link.click()
    expect(page.get_by_role("heading", name="전처리", level=1)).to_be_visible()
    assert "#/preprocess/ds_" in page.url
    ok("목표 저장 전 전처리 잠김('목표 저장 후 사용') → 저장 후 메뉴로 진입", page.url.split("#")[1])

    # 2. 자동 Mock 추천 + 한글 이유 + 미리보기 표시 ------------------------------------
    expect(page.locator(".source-bar")).to_contain_text("Mock · 오프라인 (계획 미리보기)", timeout=15_000)
    expect(page.locator(".preview-note")).to_contain_text("승인·학습 실행에 쓸 수 없습니다")
    assert card_names(page) == ["세션 분리", "리샘플링", "결측 처리", "lag 피처"], card_names(page)
    expect(page.locator("ol.cards .status")).to_have_count(4)
    for t in page.locator("ol.cards .status").all_inner_texts():
        assert t == "계획 미리보기 · 실행 불가", t
    expect(card(page, "세션 분리").locator(".lock")).to_have_text("순서 고정")
    card(page, "lag 피처").locator(".card-main").click()
    detail = page.locator(".prep-detail")
    expect(detail).to_contain_text("과거 시점의 값을 새 입력 피처로 추가합니다")
    expect(detail.locator(".reason")).to_have_text("직전 관측값이 예측에 유용한 경우가 많습니다")
    page.screenshot(path=f"{SHOTS}/01_recommendation_wide.png", full_page=True)
    ok("자동 Mock 추천·한글 설명·추천 이유·실행 불가 표시", ", ".join(card_names(page)))

    # 3. 체크 해제·추가·파라미터·순서 편집, 검증 메시지 연결 ------------------------------
    card(page, "결측 처리").locator(".card-check").uncheck()
    assert card_names(page, excluded=True) == ["결측 처리"]
    expect(card(page, "세션 분리").get_by_role("button", name="세션 분리 위로")).to_be_disabled()
    page.locator("#add-step").select_option(label="스케일링 — 계획 미리보기 · 실행 불가")
    page.get_by_role("button", name="추가", exact=True).click()
    detail.get_by_label(re.compile("^방법")).select_option("robust")
    card(page, "lag 피처").locator(".card-main").click()
    lags = detail.get_by_label(re.compile("^몇 칸 전 값을 쓸지"))
    lags.fill("0, 2")
    expect(detail.locator(".field-error")).to_contain_text("1 이상이어야 합니다", timeout=5_000)
    expect(card(page, "lag 피처").locator(".card-err")).to_have_text("확인 필요 1")
    expect(page.locator(".validate-summary")).to_contain_text("확인 필요 1건")
    lags.fill("1, 3, 6")
    expect(detail.locator(".field-error")).to_have_count(0, timeout=5_000)
    # 스케일링(뒤 단계)을 lag 위로 올리면 순서 오류가 해당 카드에 표시
    card(page, "스케일링").get_by_role("button", name="스케일링 위로").click()
    expect(card(page, "lag 피처").locator(".card-issue")).to_contain_text("앞 단계에서 실행되어야", timeout=5_000)
    page.screenshot(path=f"{SHOTS}/02_order_error_wide.png", full_page=True)
    card(page, "스케일링").get_by_role("button", name="스케일링 아래로").click()
    expect(page.locator(".card-issue")).to_have_count(0, timeout=5_000)
    page.get_by_role("button", name="초안 저장").click()
    expect(page.locator(".save-bar [role=status]")).to_contain_text("초안을 저장했습니다")
    saved_order = card_names(page)
    ok("체크 해제·추가·파라미터·순서 편집과 항목별 검증 메시지 → 저장", f"{saved_order}, 제외 {card_names(page, True)}")

    # 4. 새로고침·메뉴 이동 후 같은 초안 ----------------------------------------------
    page.reload()
    expect(page.locator("ol.cards .card")).to_have_count(4)
    assert card_names(page) == saved_order == ["세션 분리", "리샘플링", "lag 피처", "스케일링"]
    assert card_names(page, True) == ["결측 처리"]
    card(page, "lag 피처").locator(".card-main").click()
    expect(detail.get_by_label(re.compile("^몇 칸 전 값을 쓸지"))).to_have_value("1, 3, 6")
    card(page, "스케일링").locator(".card-main").click()
    expect(detail.get_by_label(re.compile("^방법"))).to_have_value("robust")
    nav.get_by_role("link", name="데이터셋").click()
    expect(page.get_by_role("heading", name="데이터셋", level=1)).to_be_visible()
    nav.get_by_role("link", name=re.compile("^전처리")).click()
    expect(page.locator("ol.cards .card")).to_have_count(4)
    assert card_names(page) == saved_order
    ok("새로고침·메뉴 이동 후 같은 초안 복원", "순서·제외·lags=1,3,6·method=robust")

    # 5. 다른 탭의 저장과 리비전 충돌 ----------------------------------------------------
    page2 = ctx.new_page()
    page2.goto(page.url)
    expect(page2.locator("ol.cards .card")).to_have_count(4)
    card(page2, "스케일링").locator(".card-check").uncheck()
    page2.get_by_role("button", name="초안 저장").click()
    expect(page2.locator(".save-bar [role=status]")).to_contain_text("초안을 저장했습니다")
    card(page, "리샘플링").locator(".card-main").click()
    detail.get_by_label(re.compile("^집계 방법")).select_option("median")
    page.get_by_role("button", name="초안 저장").click()
    expect(page.locator(".banner-warn")).to_contain_text("다른 곳에서 초안이 수정되었습니다")
    page.get_by_role("button", name="최신 상태 불러오기").click()
    expect(page.locator(".banner-warn")).to_have_count(0)
    assert card_names(page, True) == ["결측 처리", "스케일링"]
    page2.close()
    ok("리비전 충돌 안내 → 최신 상태 불러오기", "다른 탭 저장 반영")

    # 6. 다시 추천: 교체 전 선택 ------------------------------------------------------
    page.get_by_role("button", name="다시 추천").click()
    expect(page.get_by_role("alertdialog")).to_contain_text("지금 편집안을 새 추천으로 바꿀까요?")
    page.get_by_role("button", name="지금 편집안 유지").click()
    assert card_names(page) == ["세션 분리", "리샘플링", "lag 피처"]
    page.get_by_role("button", name="다시 추천").click()
    page.get_by_role("button", name="추천으로 바꾸기").click()
    expect(page.locator("ol.cards .card")).to_have_count(4)
    assert card_names(page) == ["세션 분리", "리샘플링", "결측 처리", "lag 피처"] and card_names(page, True) == []
    ok("다시 추천 시 교체 여부 선택(유지 / 바꾸기)")

    # 7. 추천 실패 안내 (서버 거절 응답 재현) ------------------------------------------
    page.route("**/recommendation", lambda r: r.fulfill(
        status=422, content_type="application/json",
        body=json.dumps({"code": "ALIAS_UNKNOWN",
                         "message_ko": "추천 응답에 현재 데이터 버전에 없는 열 참조가 있어 적용하지 않았습니다"})))
    page.get_by_role("button", name="다시 추천").click()
    fail = page.locator(".banner-fail[role=alert]")
    expect(fail).to_contain_text("현재 데이터 버전에 없는 열 참조")
    expect(fail).to_contain_text("ALIAS_UNKNOWN")
    page.screenshot(path=f"{SHOTS}/03_recommend_failed_wide.png")
    fail.get_by_role("button", name="닫고 직접 편집").click()
    page.unroute("**/recommendation")
    assert card_names(page) == ["세션 분리", "리샘플링", "결측 처리", "lag 피처"]
    ok("추천 실패 시 한글 원인·코드와 다시 추천/직접 편집 안내, 기존 초안 유지")

    # 8. 수동 모드 -----------------------------------------------------------------
    page.get_by_role("button", name="수동으로 시작").click()
    page.get_by_role("button", name="비우고 수동으로 시작").click()
    expect(page.locator(".empty-text")).to_contain_text("수동 모드입니다")
    page.locator("#add-step").select_option(label="세션 분리 — 계획 미리보기 · 실행 불가")
    page.get_by_role("button", name="추가", exact=True).click()
    detail.get_by_label(re.compile("^세션을 나누는 간격 gap")).fill("45")  # 숫자 + 단위(기본 분)
    page.get_by_role("button", name="초안 저장").click()
    expect(page.locator(".save-bar [role=status]")).to_contain_text("초안을 저장했습니다")
    page.reload()
    expect(page.locator(".source-bar")).to_contain_text("수동 편집")
    expect(page.locator("ol.cards .card")).to_have_count(1)
    page.wait_for_timeout(800)  # 자동 추천이 다시 일어나지 않는지
    assert card_names(page) == ["세션 분리"]
    ok("수동으로 시작 → 직접 추가·저장, 새로고침 후 자동 추천 없이 유지")

    # 9. 목표 없는 데이터셋은 전처리 진입 안내 --------------------------------------------
    nav.get_by_role("link", name="데이터셋").click()
    page.set_input_files("input[type=file]", f"{D}/no_goal.csv")
    row = page.locator(".ds-row", has=page.locator(".ds-name", has_text="no_goal.csv"))
    expect(row.locator(".status")).to_have_text("완료", timeout=30_000)
    row.locator(".ds-select").click()
    expect(nav.get_by_role("link", name=re.compile("^전처리"))).to_have_attribute("aria-disabled", "true")
    ng_id = page.url.split("/")[-1]
    page.goto(f"{BASE}/#/preprocess/{ng_id}")
    expect(page.get_by_role("heading", name="예측 목표를 먼저 저장해 주세요")).to_be_visible()
    expect(page.get_by_role("link", name="데이터셋 화면으로")).to_be_visible()
    ok("목표 없는 데이터셋: 메뉴 잠김, 직접 주소 진입 시 저장 안내")

    # 10. 키보드 조작 + 좁은 창 ---------------------------------------------------------
    first_id = [i for i in page.request.get(f"{BASE}/api/datasets").json()["items"] if i["name"] == "sensor_A.csv"][0]["dataset_id"]
    page.goto(f"{BASE}/#/preprocess/{first_id}")
    page.get_by_role("button", name="Mock 추천 받기").click()  # 수동 모드에서는 이 이름
    page.get_by_role("button", name="추천으로 바꾸기").click()
    expect(page.locator("ol.cards .card")).to_have_count(4)
    chk = card(page, "결측 처리").locator(".card-check")
    chk.focus()
    page.keyboard.press("Space")
    assert card_names(page, True) == ["결측 처리"]
    page.keyboard.press("Tab")  # 같은 위치의 다음 카드 본문으로
    lag_down = card(page, "lag 피처").get_by_role("button", name="lag 피처 위로")
    lag_down.focus()
    page.keyboard.press("Enter")
    assert card_names(page) == ["세션 분리", "lag 피처", "리샘플링"]
    expect(card(page, "lag 피처").locator(".card-issue")).to_contain_text("뒤에 와야", timeout=5_000)
    ok("키보드: 체크박스 Space, ▲ 버튼 Enter, 순서 오류 즉시 표시")
    for w, h, tag in ((820, 1000, "04_narrow_820"), (390, 900, "05_narrow_390")):
        page.set_viewport_size({"width": w, "height": h})
        assert page.locator(".prep-list").bounding_box()["y"] < page.locator(".prep-detail").bounding_box()["y"]
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.screenshot(path=f"{SHOTS}/{tag}.png", full_page=True)
        ok(f"좁은 창 {w}px", "목록 아래 상세, 가로 넘침 없음")

    app_errors = [e for e in errors if not re.search(r"status of (409|422)", e)]
    assert not app_errors, app_errors
    ok("브라우저 콘솔 오류 없음", f"의도한 409/422 응답 로그 {len(errors)}건 제외")
    browser.close()

print("\n".join(results))
