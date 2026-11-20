"""비전 이상탐지 평가 지표(V2, docs/P3_VISION_BRIEF.md 'V2 설계').

모델과 무관한 순수 함수: 입력은 이미지 점수·anomaly map·GT 마스크 배열이다(파일 읽기·크기 맞춤은 V3 worker 책임).
- 임계값은 **보정 정상 이미지 점수만**으로 정한다. `decide_threshold`는 테스트 데이터를 입력으로 받지 않는다.
- 계산할 수 없는 지표는 0이 아니라 `null`이며, `not_available`에 이유 코드와 한글 설명을 남긴다(CONTRACTS §8).
- 계산 불가 = `null`, 유한값 정리는 시계열 지표(`pipelines/metrics.py`)와 같은 규칙을 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from mlworkbench.core.contracts import ThresholdRule, VisionAnomalyEvaluation
from mlworkbench.pipelines.metrics import _clean

AUPRO_LIMITS = {"aupro_0.05": 0.05, "aupro_0.30": 0.30}

DEFINITIONS = {
    "auroc": "이미지 AUROC. 테스트 이미지 1장 = 표본 1개, 양성 = 불량. 정상·불량 전체로 한 번 계산(Mann-Whitney 순위 통계, "
             "동점은 평균 순위 = 0.5 기여). 임계값과 무관",
    "ng_recall": "불량 검출률 = 불량으로 판정된 테스트 불량 이미지 수 / 테스트 불량 이미지 수. 판정: 이미지 점수 > 임계값",
    "normal_fpr": "정상 이미지 오판율 = 불량으로 판정된 테스트 정상 이미지 수 / 테스트 정상 이미지 수(보정 이미지 제외). "
                  "판정: 이미지 점수 > 임계값. 픽셀 FPR과 다른 지표",
    "aupro_0.05": "AU-PRO@0.05. 픽셀 FPR 0~0.05 구간의 PRO 곡선 아래 넓이 / 0.05(0~1). 이미지 정상 오판율과 다른 지표",
    "aupro_0.30": "AU-PRO@0.30. 픽셀 FPR 0~0.30 구간의 PRO 곡선 아래 넓이 / 0.30(0~1). 이미지 정상 오판율과 다른 지표",
}

THRESHOLD_DEFINITIONS = {
    "calibration_normal_max": "보정 정상 이미지 점수의 최댓값",
    "calibration_quantile": "보정 정상 이미지 점수의 q 분위(관측 점수 중 하나, numpy method='higher')",
    "fixed": "계획에 지정한 값",
}

PIXEL_PROTOCOL = {
    "pixel_fpr": "모든 테스트 이미지(정상 포함)의 GT=0 픽셀 중 맵 값 ≥ 픽셀 임계값인 비율",
    "pro": "GT 연결 영역(8-이웃) 하나를 1단위로, 영역마다 맵 값 ≥ 픽셀 임계값인 픽셀 비율을 구해 모든 영역에서 평균",
    "threshold_grid": "모든 고유 맵 값(정확 곡선, 높은 값부터). 시작점 (FPR 0, PRO 0)",
    "integration": "사다리꼴 적분, 상한 FPR에서 이웃 두 점 선형 보간",
    "normalization": "적분값 / 상한 (완전한 맵 = 1, 상수 맵 = 0.5 × 상한 / 상한 = 상한/2)",
    "masks": "마스크 값 > 0 = 결함 픽셀. 정상 테스트 이미지는 마스크가 없으면 전부 0으로 본다. "
             "불량 이미지 중 하나라도 마스크가 없으면 일부만으로 계산하지 않고 N/A",
}

NA_MESSAGES = {
    "empty_input": "평가할 테스트 이미지가 없습니다",
    "single_class": "테스트 이미지가 정상 또는 불량 한쪽뿐이라 AUROC를 계산할 수 없습니다",
    "no_defect_images": "테스트 불량 이미지가 없어 불량 검출률을 계산할 수 없습니다",
    "no_normal_images": "테스트 정상 이미지가 없어 정상 이미지 오판율을 계산할 수 없습니다",
    "no_calibration_scores": "보정용 정상 이미지가 없어 판정 임계값을 정할 수 없습니다",
    "threshold_unavailable": "판정 임계값을 정하지 못해 계산할 수 없습니다",
    "non_finite_score": "점수에 NaN 또는 무한대가 있어 계산할 수 없습니다",
    "missing_gt_masks": "GT 마스크가 없는 불량 이미지가 있어 AU-PRO를 계산하지 않았습니다",
    "missing_anomaly_maps": "anomaly map이 없는 테스트 이미지가 있어 AU-PRO를 계산하지 않았습니다",
    "no_defect_regions": "GT 마스크에 결함 영역이 하나도 없어 AU-PRO를 계산할 수 없습니다",
    "no_normal_pixels": "정상(GT=0) 픽셀이 없어 픽셀 FPR을 계산할 수 없습니다",
}


class VisionMetricInputError(ValueError):
    """계산 불가(N/A)가 아니라 입력 자체가 잘못된 경우(크기 불일치, 역할 중복, id 중복)."""


@dataclass(frozen=True)
class EvalImage:
    image_id: str
    is_defect: bool
    score: float
    anomaly_map: np.ndarray | None = None
    gt_mask: np.ndarray | None = None


def _na(code: str, **detail: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message_ko": NA_MESSAGES[code]}
    if detail:
        out["detail"] = detail
    return out


def _finite(xs: np.ndarray) -> bool:
    return bool(np.all(np.isfinite(xs)))


# ---------------------------------------------------------------------------
# 임계값 — 보정 정상 점수만 입력으로 받는다

def decide_threshold(rule: ThresholdRule, calibration_scores: Sequence[float]) -> tuple[float | None, dict | None]:
    """(임계값, N/A 이유). 테스트 데이터는 인자에 없다."""
    if rule.method == "fixed":
        return float(rule.value), None  # type: ignore[arg-type]
    cal = np.asarray(list(calibration_scores), dtype="float64")
    if cal.size == 0:
        return None, _na("no_calibration_scores")
    if not _finite(cal):
        return None, _na("non_finite_score", where="calibration")
    if rule.method == "calibration_normal_max":
        return float(cal.max()), None
    return float(np.quantile(cal, rule.quantile, method="higher")), None


def predict_defect(scores, threshold: float) -> np.ndarray:
    """판정 규칙(유일한 정의): 점수 > 임계값이면 불량(같으면 정상). 스칼라·배열 모두."""
    return np.asarray(scores, dtype="float64") > threshold


# ---------------------------------------------------------------------------
# 이미지 AUROC

def _average_ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    sorted_x = x[order]
    _, first, counts = np.unique(sorted_x, return_index=True, return_counts=True)
    avg = first + (counts + 1) / 2.0  # 1부터 세는 평균 순위
    ranks_sorted = np.repeat(avg, counts)
    ranks = np.empty_like(ranks_sorted)
    ranks[order] = ranks_sorted
    return ranks


def image_auroc(is_defect: Sequence[bool], scores: Sequence[float]) -> tuple[float | None, dict | None]:
    y = np.asarray(list(is_defect), dtype=bool)
    s = np.asarray(list(scores), dtype="float64")
    if y.size == 0:
        return None, _na("empty_input")
    n_pos, n_neg = int(y.sum()), int((~y).sum())
    if n_pos == 0 or n_neg == 0:
        return None, _na("single_class", normal=n_neg, defect=n_pos)
    if not _finite(s):
        return None, _na("non_finite_score", where="test")
    r = _average_ranks(s)
    auc = (r[y].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return _clean(auc), None


# ---------------------------------------------------------------------------
# AU-PRO

_EIGHT = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def _label_python(mask: np.ndarray) -> tuple[np.ndarray, int]:
    lab = np.zeros(mask.shape, dtype=np.int32)
    h, w = mask.shape
    n = 0
    for i0, j0 in zip(*np.nonzero(mask)):
        if lab[i0, j0]:
            continue
        n += 1
        lab[i0, j0] = n
        stack = [(int(i0), int(j0))]
        while stack:
            i, j = stack.pop()
            for di, dj in _EIGHT:
                a, b = i + di, j + dj
                if 0 <= a < h and 0 <= b < w and mask[a, b] and not lab[a, b]:
                    lab[a, b] = n
                    stack.append((a, b))
    return lab, n


def label_regions(mask: np.ndarray, *, backend: str = "auto") -> tuple[np.ndarray, int]:
    """GT 결함 영역 라벨링(8-이웃). scipy가 있으면 scipy, 없으면 순수 Python(결과 동일)."""
    m = np.asarray(mask) > 0
    if backend in ("auto", "scipy"):
        try:
            from scipy import ndimage
        except ImportError:
            if backend == "scipy":
                raise
        else:
            lab, n = ndimage.label(m, structure=np.ones((3, 3), dtype=int))
            return lab.astype(np.int32), int(n)
    return _label_python(m)


def pro_curve(maps: Sequence[np.ndarray], masks: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray, dict]:
    """정확한 (pixel FPR, PRO) 곡선. 높은 맵 값부터 픽셀 임계값을 내리며 누적, 동점은 한 점으로 묶는다."""
    scores, w_pro, ok = [], [], []
    region_sizes: list[int] = []
    for amap, mask in zip(maps, masks):
        lab, n = label_regions(mask)
        sizes = np.bincount(lab.ravel(), minlength=n + 1)
        region_sizes.extend(int(x) for x in sizes[1:])
        scores.append(np.asarray(amap, dtype="float64").ravel())
        inv = np.zeros(n + 1, dtype="float64")
        inv[1:] = 1.0 / sizes[1:]
        w_pro.append(inv[lab.ravel()])  # 영역 픽셀 하나 = 1/영역크기
        ok.append(lab.ravel() == 0)
    s = np.concatenate(scores)
    wp = np.concatenate(w_pro)
    okm = np.concatenate(ok)
    k = len(region_sizes)
    n_ok = int(okm.sum())
    info = {"regions": k, "normal_pixels": n_ok, "defect_pixels": int(s.size - n_ok),
            "min_region_pixels": min(region_sizes) if region_sizes else None}
    if k == 0 or n_ok == 0:
        return np.array([]), np.array([]), info
    order = np.argsort(-s, kind="mergesort")
    s_sorted = s[order]
    fpr = np.cumsum(okm[order]) / n_ok           # 정수 누적 / 정상 픽셀 수 → 끝점 정확히 1
    pro = np.cumsum(wp[order]) / k
    last = np.r_[s_sorted[1:] != s_sorted[:-1], True]  # 동점 묶음의 마지막 위치
    fpr = np.r_[0.0, fpr[last]]
    pro = np.r_[0.0, np.minimum(pro[last], 1.0)]
    info["curve_points"] = int(fpr.size)
    return fpr, pro, info


def integrate_to(fpr: np.ndarray, pro: np.ndarray, limit: float) -> float:
    """0~limit 사다리꼴 적분 / limit. limit에서 선형 보간."""
    keep = fpr <= limit
    x, y = fpr[keep], pro[keep]
    if x[-1] < limit:
        j = int(keep.sum())
        x0, y0, x1, y1 = fpr[j - 1], pro[j - 1], fpr[j], pro[j]
        y_lim = y0 + (y1 - y0) * (limit - x0) / (x1 - x0)
        x, y = np.r_[x, limit], np.r_[y, y_lim]
    area = float(np.sum((x[1:] - x[:-1]) * (y[1:] + y[:-1]) / 2.0))
    return area / limit


def au_pro(test: Sequence[EvalImage], limits: Mapping[str, float]) -> tuple[dict[str, float | None], dict[str, dict], dict]:
    """요청한 상한별 AU-PRO. (값, N/A 이유, 곡선 정보)."""
    names = list(limits)
    if not test:
        return {n: None for n in names}, {n: _na("empty_input") for n in names}, {}
    no_map = [t.image_id for t in test if t.anomaly_map is None]
    no_mask = [t.image_id for t in test if t.is_defect and t.gt_mask is None]
    reason = None
    if no_mask:
        reason = _na("missing_gt_masks", defect_images_without_mask=len(no_mask),
                     defect_images=sum(t.is_defect for t in test))
    elif no_map:
        reason = _na("missing_anomaly_maps", images_without_map=len(no_map))
    if reason:
        return {n: None for n in names}, {n: reason for n in names}, {}
    maps, masks = [], []
    for t in test:
        amap = np.asarray(t.anomaly_map, dtype="float64")
        if amap.ndim != 2:
            raise VisionMetricInputError(f"anomaly map은 2차원이어야 합니다: {t.image_id} {amap.shape}")
        mask = np.zeros(amap.shape, dtype=bool) if t.gt_mask is None else np.asarray(t.gt_mask) > 0
        if mask.shape != amap.shape:
            raise VisionMetricInputError(f"맵과 마스크 크기가 다릅니다: {t.image_id} {amap.shape} vs {mask.shape}")
        maps.append(amap)
        masks.append(mask)
    if not all(_finite(m) for m in maps):
        r = _na("non_finite_score", where="anomaly_map")
        return {n: None for n in names}, {n: r for n in names}, {}
    fpr, pro, info = pro_curve(maps, masks)
    if info["regions"] == 0:
        r = _na("no_defect_regions")
        return {n: None for n in names}, {n: r for n in names}, info
    if info["normal_pixels"] == 0:
        r = _na("no_normal_pixels")
        return {n: None for n in names}, {n: r for n in names}, info
    return {n: _clean(integrate_to(fpr, pro, lim)) for n, lim in limits.items()}, {}, info


# ---------------------------------------------------------------------------
# 묶음 평가

def evaluate_anomaly(evaluation: VisionAnomalyEvaluation, calibration: Mapping[str, float],
                     test: Sequence[EvalImage]) -> dict[str, Any]:
    """계획의 평가 설정대로 지표를 계산한다. calibration = {보정 정상 이미지 id: 점수}."""
    ids = [t.image_id for t in test]
    if len(set(ids)) != len(ids):
        raise VisionMetricInputError("테스트 이미지 id가 중복됩니다")
    overlap = set(ids) & set(calibration)
    if overlap:
        raise VisionMetricInputError(f"보정 이미지와 테스트 이미지가 겹칩니다({len(overlap)}장) — 역할을 분리하세요")

    # 1) 임계값: 보정 점수만 사용(테스트를 보기 전에 확정)
    thr, thr_na = decide_threshold(evaluation.threshold, list(calibration.values()))

    metrics: dict[str, float | None] = {}
    na: dict[str, dict] = {}
    y = [t.is_defect for t in test]
    s = np.asarray([t.score for t in test], dtype="float64")
    n_def, n_norm = sum(y), len(y) - sum(y)
    counts: dict[str, Any] = {"test_images": len(test), "test_normal": n_norm, "test_defect": n_def,
                              "calibration_images": len(calibration)}
    scores_ok = _finite(s)
    decided = thr is not None and scores_ok and len(test) > 0
    if decided:
        pred = predict_defect(s, thr)
        yy = np.asarray(y, dtype=bool)
        counts.update(tp=int((pred & yy).sum()), fn=int((~pred & yy).sum()),
                      fp=int((pred & ~yy).sum()), tn=int((~pred & ~yy).sum()))

    for m in evaluation.image_metrics:
        if m == "auroc":
            metrics[m], r = image_auroc(y, s)
        else:
            denom_key, empty_code = ("test_defect", "no_defect_images") if m == "ng_recall" else ("test_normal", "no_normal_images")
            if not test:
                r = _na("empty_input")
            elif counts[denom_key] == 0:
                r = _na(empty_code)
            elif thr is None:
                r = _na("threshold_unavailable", cause=thr_na["code"] if thr_na else None)
            elif not scores_ok:
                r = _na("non_finite_score", where="test")
            else:
                r = None
            metrics[m] = None if r else _clean(
                counts["tp"] / n_def if m == "ng_recall" else counts["fp"] / n_norm)
        if r:
            na[m] = r

    pixel_info: dict = {}
    if evaluation.pixel_metrics:
        limits = {m: AUPRO_LIMITS[m] for m in evaluation.pixel_metrics}
        vals, pna, pixel_info = au_pro(test, limits)
        metrics.update(vals)
        na.update(pna)
    return {
        "metrics": metrics,
        "not_available": na,
        "threshold": {"method": evaluation.threshold.method, "quantile": evaluation.threshold.quantile,
                      "value": thr, "calibration_images": len(calibration),
                      "not_available": thr_na, "decision": "점수 > 임계값 → 불량"},
        "counts": counts,
        "pixel": pixel_info,
    }


def metrics_meta(evaluation: VisionAnomalyEvaluation) -> dict[str, Any]:
    """`metrics_meta.json`에 쓸 정의·계산 단위·집계(시계열 metrics_meta와 같은 형식)."""
    names = list(evaluation.image_metrics) + list(evaluation.pixel_metrics)
    return {
        "definitions": {k: DEFINITIONS[k] for k in names},
        "evaluation": evaluation.model_dump(mode="json"),
        "threshold_rule": THRESHOLD_DEFINITIONS[evaluation.threshold.method],
        "protocol": "임계값은 보정 정상 이미지 점수만으로 정하고 테스트 데이터로 조정하지 않음. 보정·테스트 이미지는 겹치지 않음",
        "aggregation": "이미지 지표 = 테스트 이미지 전체를 한 번에(불량 종류별로 나눈 뒤 평균하지 않음). "
                       "AU-PRO = 모든 테스트 이미지의 픽셀을 합쳐 하나의 곡선",
        "pixel_protocol": PIXEL_PROTOCOL if evaluation.pixel_metrics else None,
        "not_available": "계산할 수 없는 지표는 0이 아니라 null이며 not_available에 이유 코드를 기록",
    }


__all__ = ["EvalImage", "VisionMetricInputError", "decide_threshold", "predict_defect", "image_auroc",
           "label_regions", "pro_curve", "integrate_to", "au_pro", "evaluate_anomaly", "metrics_meta",
           "DEFINITIONS", "NA_MESSAGES", "AUPRO_LIMITS"]
