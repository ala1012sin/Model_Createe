"""비전 V2 이상탐지 지표: 정답을 아는 합성 입력으로 값과 경계 조건을 검증(docs/P3_VISION_BRIEF.md 'V2 설계')."""

from __future__ import annotations

import numpy as np
import pytest

from mlworkbench.core.contracts import ThresholdRule, VisionAnomalyEvaluation
from mlworkbench.pipelines import vision_metrics as V
from mlworkbench.pipelines.vision_metrics import EvalImage, VisionMetricInputError

ALL = VisionAnomalyEvaluation(image_metrics=["auroc", "ng_recall", "normal_fpr"], pixel_metrics=["aupro_0.05", "aupro_0.30"])


def ev(method="calibration_normal_max", q=None, value=None, pixel=("aupro_0.05", "aupro_0.30")):
    return VisionAnomalyEvaluation(image_metrics=["auroc", "ng_recall", "normal_fpr"],
                                   threshold=ThresholdRule(method=method, quantile=q, value=value), pixel_metrics=list(pixel))


def img(i, defect, score, amap=None, mask=None):
    return EvalImage(f"t{i}", defect, score, None if amap is None else np.asarray(amap, float),
                     None if mask is None else np.asarray(mask))


# ---------------------------------------------------------------------------
# 느린 참조 구현: 임계값마다 픽셀을 직접 세어 FPR·PRO를 구한다(정확 곡선 구현과 독립)

def _regions_bruteforce(mask):
    lab, n = V.label_regions(mask, backend="python")
    return [lab == k for k in range(1, n + 1)]


def ref_aupro(maps, masks, limit):
    maps = [np.asarray(m, float) for m in maps]
    masks = [np.asarray(m) > 0 for m in masks]
    regions = [(i, r) for i, m in enumerate(masks) for r in _regions_bruteforce(m)]
    n_ok = sum(int((~m).sum()) for m in masks)
    pts = [(0.0, 0.0)]
    for t in sorted({float(v) for m in maps for v in m.ravel()}, reverse=True):
        fp = sum(int(((maps[i] >= t) & ~masks[i]).sum()) for i in range(len(maps)))
        pro = np.mean([((maps[i] >= t) & r).sum() / r.sum() for i, r in regions])
        pts.append((fp / n_ok, float(pro)))
    xs, ys = map(np.array, zip(*pts))
    return V.integrate_to(xs, ys, limit)


# ---------------------------------------------------------------------------
# 이미지 AUROC

def test_auroc_hand_computed_with_tie():
    # 정상 [0.1, 0.4, 0.35], 불량 [0.8, 0.4]: 0.8은 3쌍 승, 0.4는 2승 1무(0.5) → 5.5 / 6
    v, na = V.image_auroc([0, 0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8, 0.4])
    assert na is None and v == pytest.approx(5.5 / 6)


@pytest.mark.parametrize("scores, expected", [([0, 1, 2, 3], 1.0), ([3, 2, 1, 0], 0.0), ([5, 5, 5, 5], 0.5)])
def test_auroc_perfect_reversed_all_ties(scores, expected):
    assert V.image_auroc([0, 0, 1, 1], scores)[0] == pytest.approx(expected)


def test_auroc_matches_sklearn_random_with_ties():
    sk = pytest.importorskip("sklearn.metrics")
    rng = np.random.default_rng(0)
    for _ in range(30):
        n = int(rng.integers(4, 60))
        y = rng.integers(0, 2, n)
        y[0], y[1] = 0, 1
        s = np.round(rng.normal(size=n) + y, 1)  # 반올림으로 동점을 만든다
        assert V.image_auroc(y, s)[0] == pytest.approx(sk.roc_auc_score(y, s), abs=1e-12)


@pytest.mark.parametrize("y, s, code", [([], [], "empty_input"), ([0, 0], [1, 2], "single_class"),
                                        ([1, 1], [1, 2], "single_class"), ([0, 1], [0.1, float("nan")], "non_finite_score")])
def test_auroc_na(y, s, code):
    v, na = V.image_auroc(y, s)
    assert v is None and na["code"] == code and na["message_ko"]


# ---------------------------------------------------------------------------
# 임계값: 보정 정상 점수만

def test_threshold_rules():
    cal = [float(x) for x in range(1, 11)]
    assert V.decide_threshold(ThresholdRule(method="calibration_normal_max"), cal) == (10.0, None)
    # q=0.5, 10개: 위치 4.5 → method='higher' → 6번째 값(관측값 중 하나)
    assert V.decide_threshold(ThresholdRule(method="calibration_quantile", quantile=0.5), cal) == (6.0, None)
    assert V.decide_threshold(ThresholdRule(method="fixed", value=0.42), []) == (0.42, None)
    v, na = V.decide_threshold(ThresholdRule(method="calibration_normal_max"), [])
    assert v is None and na["code"] == "no_calibration_scores"
    v, na = V.decide_threshold(ThresholdRule(method="calibration_quantile", quantile=0.9), [1.0, float("inf")])
    assert v is None and na["code"] == "non_finite_score"


def test_threshold_ignores_test_data_and_boundary_is_normal():
    cal = {"c1": 0.2, "c2": 0.5, "c3": 0.3}
    a = V.evaluate_anomaly(ev(pixel=()), cal, [img(1, False, 0.5), img(2, True, 0.5), img(3, True, 0.9)])
    b = V.evaluate_anomaly(ev(pixel=()), cal, [img(1, False, 99.0), img(2, True, -5.0)])
    assert a["threshold"]["value"] == b["threshold"]["value"] == 0.5
    # 점수 = 임계값 → 정상: 정상 0.5는 오판 아님, 불량 0.5는 놓침
    assert (a["counts"]["fp"], a["counts"]["tp"], a["counts"]["fn"]) == (0, 1, 1)
    assert a["metrics"]["normal_fpr"] == 0.0 and a["metrics"]["ng_recall"] == 0.5
    assert b["metrics"]["normal_fpr"] == 1.0 and b["metrics"]["ng_recall"] == 0.0


def test_calibration_images_not_counted_in_normal_fpr():
    cal = {f"c{i}": s for i, s in enumerate([0.1, 0.2, 0.3, 0.9])}  # 보정 최댓값 0.9
    test = [img(i, False, s) for i, s in enumerate([0.95, 0.1, 0.2, 0.3])] + [img(9, True, 1.5)]
    r = V.evaluate_anomaly(ev(pixel=()), cal, test)
    assert r["counts"]["test_normal"] == 4 and r["counts"]["calibration_images"] == 4
    assert r["metrics"]["normal_fpr"] == pytest.approx(1 / 4)  # 테스트 정상 4장 중 0.95 하나
    assert r["metrics"]["ng_recall"] == 1.0


def test_quantile_rule_end_to_end():
    cal = {f"c{i}": float(i) for i in range(1, 11)}
    test = [img(1, False, 5.5), img(2, False, 6.0), img(3, False, 6.5), img(4, True, 7.0), img(5, True, 6.0)]
    r = V.evaluate_anomaly(ev("calibration_quantile", q=0.5, pixel=()), cal, test)
    assert r["threshold"]["value"] == 6.0
    assert r["metrics"]["normal_fpr"] == pytest.approx(1 / 3) and r["metrics"]["ng_recall"] == pytest.approx(1 / 2)


def test_role_overlap_and_duplicate_ids_are_input_errors():
    with pytest.raises(VisionMetricInputError, match="겹칩니다"):
        V.evaluate_anomaly(ALL, {"t1": 0.1}, [img(1, False, 0.2)])
    with pytest.raises(VisionMetricInputError, match="중복"):
        V.evaluate_anomaly(ALL, {"c": 0.1}, [img(1, False, 0.2), img(1, True, 0.3)])


# ---------------------------------------------------------------------------
# AU-PRO: 정답을 아는 맵

def _one(amap, mask):
    return [img(1, True, 1.0, amap, mask)]


def test_aupro_hand_computed_curve():
    # 한 줄 7픽셀: R1 = {0}(1픽셀), R2 = {2,3}(2픽셀), 정상 픽셀 4개
    mask = [[1, 0, 1, 1, 0, 0, 0]]
    amap = [[0.9, 0.7, 0.8, 0.4, 0.5, 0.3, 0.1]]
    fpr, pro, info = V.pro_curve([np.array(amap)], [np.array(mask) > 0])
    assert info["regions"] == 2 and info["normal_pixels"] == 4
    # 곡선: (0,0)→0.9:(0,.5)→0.8:(0,.75)→0.7:(.25,.75)→0.5:(.5,.75)→0.4:(.5,1)→0.3:(.75,1)→0.1:(1,1)
    assert fpr.tolist() == [0, 0, 0, 0.25, 0.5, 0.5, 0.75, 1.0]
    assert pro.tolist() == pytest.approx([0, 0.5, 0.75, 0.75, 0.75, 1.0, 1.0, 1.0])
    vals, na, _ = V.au_pro(_one(amap, mask), V.AUPRO_LIMITS)
    assert not na and vals["aupro_0.05"] == pytest.approx(0.75) and vals["aupro_0.30"] == pytest.approx(0.75)
    assert V.integrate_to(fpr, pro, 1.0) == pytest.approx(0.875)  # 0.375 + 0.5
    assert V.integrate_to(fpr, pro, 0.6) == pytest.approx((0.375 + 0.1) / 0.6)  # 상한 0.6에서 보간


def test_aupro_perfect_inverted_constant():
    mask = np.zeros((8, 8), int)
    mask[2:4, 2:5] = 1
    mask[6, 6] = 1
    normal = img(2, False, 0.0, np.zeros((8, 8)), None)  # 정상 이미지: 마스크 없음 = 전부 0
    perfect = V.au_pro([img(1, True, 1, mask.astype(float), mask), normal], V.AUPRO_LIMITS)[0]
    assert perfect == {"aupro_0.05": pytest.approx(1.0), "aupro_0.30": pytest.approx(1.0)}
    inverted = V.au_pro([img(1, True, 1, 1.0 - mask, mask), img(2, False, 0, np.ones((8, 8)))], V.AUPRO_LIMITS)[0]
    assert inverted == {"aupro_0.05": pytest.approx(0.0), "aupro_0.30": pytest.approx(0.0)}
    const = V.au_pro([img(1, True, 1, np.full((8, 8), 0.3), mask)], V.AUPRO_LIMITS)[0]
    assert const == {"aupro_0.05": pytest.approx(0.025), "aupro_0.30": pytest.approx(0.15)}  # 상한/2


def test_aupro_is_region_average_not_pixel_tpr():
    # 큰 영역(16픽셀)은 못 찾고 작은 영역(1픽셀)만 FPR 0에서 찾음 → PRO 0.5(픽셀 TPR이면 1/17)
    mask = np.zeros((10, 10), int)
    mask[0:4, 0:4] = 1
    mask[8, 8] = 1
    amap = np.full((10, 10), 0.5)
    amap[8, 8] = 1.0
    amap[0:4, 0:4] = 0.0
    fpr, pro, _ = V.pro_curve([amap], [mask > 0])
    assert (fpr[1], pro[1]) == (0.0, pytest.approx(0.5))
    vals = V.au_pro(_one(amap, mask), {"aupro_0.30": 0.30})[0]
    assert vals["aupro_0.30"] == pytest.approx(0.5)


def test_regions_use_8_connectivity():
    mask = np.zeros((5, 5), int)
    mask[0, 0] = mask[1, 1] = 1  # 대각선 → 한 영역
    mask[4, 4] = 1
    assert V.label_regions(mask, backend="python")[1] == 2
    assert V.label_regions(mask)[1] == 2  # 기본(scipy 있으면 scipy)
    amap = np.zeros((5, 5))
    amap[4, 4] = 1.0  # 외딴 픽셀만 검출: 8-이웃이면 PRO 1/2(4-이웃이었다면 1/3)
    _, pro, _ = V.pro_curve([amap], [mask > 0])
    assert pro[1] == pytest.approx(0.5)


def test_label_backends_agree():
    pytest.importorskip("scipy")
    rng = np.random.default_rng(3)
    if True:
        for _ in range(20):
            m = rng.random((12, 15)) < 0.3
            a, na_ = V.label_regions(m, backend="python")
            b, nb = V.label_regions(m, backend="scipy")
            assert na_ == nb and np.array_equal(a > 0, b > 0)
            # 라벨 번호는 달라도 같은 분할인지: 픽셀쌍이 같은 영역인지가 일치
            pairs = {(int(x), int(y)) for x, y in zip(a[m], b[m])}
            assert len(pairs) == na_


def test_aupro_matches_bruteforce_reference_random():
    rng = np.random.default_rng(7)
    for trial in range(25):
        n_img = int(rng.integers(1, 4))
        maps, masks, test = [], [], []
        for i in range(n_img):
            h, w = int(rng.integers(3, 9)), int(rng.integers(3, 9))
            m = (rng.random((h, w)) < 0.25).astype(int)
            m[0, 0] = 1 if i == 0 else m[0, 0]  # 결함 영역 최소 1개
            m[-1, -1] = 0  # 정상 픽셀 최소 1개
            a = np.round(rng.random((h, w)) + 0.6 * m, 1 if trial % 2 else 3)  # 짝수 회차는 동점 많음
            maps.append(a), masks.append(m), test.append(img(i, True, 1.0, a, m))
        vals = V.au_pro(test, V.AUPRO_LIMITS)[0]
        for name, lim in V.AUPRO_LIMITS.items():
            assert vals[name] == pytest.approx(ref_aupro(maps, masks, lim), abs=1e-12), (trial, name)


def test_image_fpr_zero_but_pixel_fpr_positive():
    # 정상 이미지 점수는 임계값 아래(이미지 오판 0)지만, 맵에는 결함보다 높은 정상 픽셀이 있다 → AU-PRO < 1
    mask = np.zeros((6, 6), int)
    mask[1:3, 1:3] = 1
    dmap = mask * 0.8
    nmap = np.zeros((6, 6))
    nmap[5, 5] = 0.9
    r = V.evaluate_anomaly(ALL, {"c": 0.5}, [img(1, True, 0.9, dmap, mask), img(2, False, 0.4, nmap, None)])
    assert r["metrics"]["normal_fpr"] == 0.0 and r["metrics"]["auroc"] == 1.0
    assert r["metrics"]["aupro_0.05"] < 1.0
    assert r["pixel"]["normal_pixels"] == 72 - 4


# ---------------------------------------------------------------------------
# 합성 데이터 끝까지: 정답 맵 + 작은 잡음

def test_synthetic_dataset_oracle_detector():
    rng = np.random.default_rng(11)
    test, cal = [], {}
    for i in range(12):
        cal[f"cal{i}"] = float(rng.uniform(0.0, 0.2))
    for i in range(10):
        test.append(EvalImage(f"ok{i}", False, float(rng.uniform(0.0, 0.25)), rng.uniform(0, 0.2, (16, 16)), None))
    for i in range(8):
        m = np.zeros((16, 16), int)
        r0, c0 = rng.integers(0, 12, 2)
        m[r0:r0 + 3, c0:c0 + 4] = 1
        test.append(EvalImage(f"ng{i}", True, float(rng.uniform(0.6, 1.0)), m * 0.7 + rng.uniform(0, 0.2, (16, 16)), m))
    r = V.evaluate_anomaly(ALL, cal, test)
    assert r["metrics"]["auroc"] == 1.0 and r["metrics"]["ng_recall"] == 1.0
    assert r["metrics"]["aupro_0.05"] == pytest.approx(1.0) and r["metrics"]["aupro_0.30"] == pytest.approx(1.0)
    # 보정 최댓값 < 0.2 이고 정상 테스트는 0~0.25 → 0.2 초과인 정상만 오판
    thr = max(cal.values())
    expected_fpr = sum(t.score > thr for t in test if not t.is_defect) / 10
    assert r["threshold"]["value"] == thr and r["metrics"]["normal_fpr"] == pytest.approx(expected_fpr)
    assert r["not_available"] == {}


# ---------------------------------------------------------------------------
# 계산 불가(N/A)

def test_na_empty_input():
    r = V.evaluate_anomaly(ALL, {"c": 0.1}, [])
    assert set(r["metrics"]) == {"auroc", "ng_recall", "normal_fpr", "aupro_0.05", "aupro_0.30"}
    assert all(v is None for v in r["metrics"].values())
    assert {k: v["code"] for k, v in r["not_available"].items()} == dict.fromkeys(r["metrics"], "empty_input")
    assert r["threshold"]["value"] == 0.1  # 임계값은 보정으로 정해짐


def test_na_single_class_normal_only():
    r = V.evaluate_anomaly(ev(pixel=()), {"c": 0.5}, [img(1, False, 0.1), img(2, False, 0.9)])
    assert r["not_available"]["auroc"]["code"] == "single_class"
    assert r["not_available"]["ng_recall"]["code"] == "no_defect_images"
    assert r["metrics"]["normal_fpr"] == 0.5 and "normal_fpr" not in r["not_available"]


def test_na_defect_only():
    r = V.evaluate_anomaly(ev(pixel=()), {"c": 0.5}, [img(1, True, 0.9)])
    assert r["not_available"]["auroc"]["code"] == "single_class"
    assert r["not_available"]["normal_fpr"]["code"] == "no_normal_images"
    assert r["metrics"]["ng_recall"] == 1.0


def test_na_no_calibration_keeps_auroc():
    r = V.evaluate_anomaly(ev(pixel=()), {}, [img(1, False, 0.1), img(2, True, 0.9)])
    assert r["metrics"]["auroc"] == 1.0
    for m in ("ng_recall", "normal_fpr"):
        assert r["metrics"][m] is None and r["not_available"][m]["code"] == "threshold_unavailable"
        assert r["not_available"][m]["detail"]["cause"] == "no_calibration_scores"
    assert r["threshold"]["not_available"]["code"] == "no_calibration_scores"
    fixed = V.evaluate_anomaly(ev("fixed", value=0.5, pixel=()), {}, [img(1, False, 0.1), img(2, True, 0.9)])
    assert fixed["metrics"]["ng_recall"] == 1.0  # 직접 지정한 임계값은 보정 없이도 가능


def test_na_non_finite_test_score():
    r = V.evaluate_anomaly(ev(pixel=()), {"c": 0.5}, [img(1, False, float("nan")), img(2, True, 0.9)])
    assert {k: v["code"] for k, v in r["not_available"].items()} == dict.fromkeys(
        ["auroc", "ng_recall", "normal_fpr"], "non_finite_score")


def test_na_missing_gt_mask_partial_is_not_subset():
    m = np.zeros((4, 4), int)
    m[1, 1] = 1
    test = [img(1, True, 0.9, m * 1.0, m), img(2, True, 0.8, np.zeros((4, 4)), None), img(3, False, 0.1, np.zeros((4, 4)))]
    r = V.evaluate_anomaly(ALL, {"c": 0.5}, test)
    for k in ("aupro_0.05", "aupro_0.30"):
        assert r["metrics"][k] is None  # 0점이 아님
        assert r["not_available"][k]["code"] == "missing_gt_masks"
        assert r["not_available"][k]["detail"] == {"defect_images_without_mask": 1, "defect_images": 2}
    assert r["metrics"]["auroc"] == 1.0  # 이미지 지표는 영향 없음


def test_na_missing_map_no_regions_no_normal_pixels_non_finite_map():
    m = np.zeros((3, 3), int)
    m[0, 0] = 1
    lim = V.AUPRO_LIMITS
    assert V.au_pro([img(1, True, 1, None, m)], lim)[1]["aupro_0.05"]["code"] == "missing_anomaly_maps"
    assert V.au_pro([img(1, True, 1, np.ones((3, 3)), np.zeros((3, 3)))], lim)[1]["aupro_0.05"]["code"] == "no_defect_regions"
    assert V.au_pro([img(1, True, 1, np.ones((3, 3)), np.ones((3, 3)))], lim)[1]["aupro_0.30"]["code"] == "no_normal_pixels"
    bad = np.ones((3, 3))
    bad[1, 1] = np.nan
    assert V.au_pro([img(1, True, 1, bad, m)], lim)[1]["aupro_0.05"]["code"] == "non_finite_score"
    assert V.au_pro([], lim)[1]["aupro_0.05"]["code"] == "empty_input"


def test_shape_mismatch_is_input_error():
    with pytest.raises(VisionMetricInputError, match="크기"):
        V.au_pro([img(1, True, 1, np.zeros((4, 4)), np.zeros((4, 5)))], V.AUPRO_LIMITS)


def test_only_requested_metrics_and_meta():
    e = VisionAnomalyEvaluation(image_metrics=["auroc"])
    r = V.evaluate_anomaly(e, {"c": 0.1}, [img(1, False, 0.0), img(2, True, 1.0)])
    assert set(r["metrics"]) == {"auroc"}
    meta = V.metrics_meta(ALL)
    assert set(meta["definitions"]) == {"auroc", "ng_recall", "normal_fpr", "aupro_0.05", "aupro_0.30"}
    assert meta["pixel_protocol"]["threshold_grid"] and meta["aggregation"] and "보정" in meta["protocol"]
    assert V.metrics_meta(e)["pixel_protocol"] is None and set(V.metrics_meta(e)["definitions"]) == {"auroc"}
    assert "다른 지표" in meta["definitions"]["normal_fpr"] and "다른 지표" in meta["definitions"]["aupro_0.05"]
