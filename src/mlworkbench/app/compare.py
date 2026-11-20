"""S6 실험 비교 (docs/P2_COMPARE_BRIEF.md).

- 평가 조건(protocol): 같은 표본·같은 정답·같은 지표로 계산된 숫자인지. 다르면 순위를 매기지 않고 경고한다.
- 조건 차이(conditions): 사용자가 바꿔 본 실험 변수(전처리·모델·학습량·플러그인 코드). 경고가 아니라 차이로 보여 준다.
완료 실행만 비교한다. 실행 폴더의 compare_key.json에 계산 결과를 캐시한다(입력 파일이 바뀌면 다시 계산).
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from mlworkbench.core.hashing import sha256_of

CACHE = "compare_key.json"
CACHE_VERSION = 1
MAX_POINTS = 600

PROTOCOL_KO = {
    "dataset_version": "데이터 버전",
    "target": "예측 타깃",
    "timestamp_col": "시간열",
    "group_col": "설비 구분 열",
    "horizons": "예측 시점",
    "split": "분할 비율·gap",
    "metrics": "평가 지표",
    "baseline": "기준 예측",
    "test_counts": "예측 시점별 테스트 표본 수",
    "test_set": "테스트 표본(설비·기준 시각)",
}
CONDITION_KO = {
    "preprocessing": "전처리",
    "model": "모델",
    "model_params": "모델 파라미터",
    "train_budget": "학습량",
    "plugin_code": "사용자 플러그인 코드",
    "dataset": "데이터셋",
}


def _files_sig(exp: Path) -> list:
    out = []
    for name in ("predictions/predictions.csv", "split.json", "metrics.json"):
        p = exp / name
        try:
            st = p.stat()
            out.append([name, st.st_size, int(st.st_mtime)])
        except OSError:
            out.append([name, None, None])
    return out


def _test_keys(exp: Path) -> dict[str, list[tuple[str, str]]]:
    """예측 시점별 테스트 표본 키 (설비, 기준 시각) 정렬 목록."""
    keys: dict[str, list[tuple[str, str]]] = {}
    with (exp / "predictions" / "predictions.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("split") == "test":
                keys.setdefault(row["horizon"], []).append((row["group"], row["origin_time"]))
    return {h: sorted(v) for h, v in keys.items()}


def _hash_keys(keys: list[tuple[str, str]]) -> str:
    h = hashlib.sha256()
    for g, t in keys:
        h.update(f"{g}\t{t}\n".encode("utf-8"))
    return h.hexdigest()[:16]


def protocol_of(cfg: dict[str, Any], dataset_version: str, exp: Path) -> dict[str, Any]:
    """완료 실행의 평가 조건. 예측 파일이 없으면 test_* 항목은 None."""
    goal = cfg.get("user_goal") or {}
    sp = cfg.get("split_policy") or {}
    ev = cfg.get("evaluation") or {}
    proto: dict[str, Any] = {
        "dataset_version": dataset_version,
        "target": goal.get("target_col"),
        "timestamp_col": goal.get("timestamp_col"),
        "group_col": goal.get("group_col"),
        "horizons": list(goal.get("horizons") or []),
        "split": {k: sp.get(k) for k in ("method", "train", "val", "test", "gap")},
        "metrics": sorted(ev.get("metrics") or []),
        "baseline": ev.get("baseline"),
        "test_counts": None,
        "test_set": None,
        "test_set_by_horizon": None,
    }
    if (exp / "predictions" / "predictions.csv").exists():
        keys = _test_keys(exp)
        proto["test_counts"] = {h: len(v) for h, v in sorted(keys.items())}
        proto["test_set_by_horizon"] = {h: _hash_keys(v) for h, v in sorted(keys.items())}
        proto["test_set"] = sha256_of(proto["test_set_by_horizon"])[7:23]
    return proto


def protocol_key(proto: dict[str, Any]) -> str:
    return sha256_of({k: v for k, v in proto.items() if k != "test_set_by_horizon"})[7:19]


def cached_protocol(cfg: dict[str, Any], dataset_version: str, exp: Path) -> dict[str, Any]:
    sig = _files_sig(exp)
    f = exp / CACHE
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("v") == CACHE_VERSION and data.get("sig") == sig and data.get("dataset_version") == dataset_version:
            return data["protocol"]
    except (OSError, ValueError, KeyError):
        pass
    proto = protocol_of(cfg, dataset_version, exp)
    try:
        f.write_text(json.dumps({"v": CACHE_VERSION, "sig": sig, "dataset_version": dataset_version, "protocol": proto},
                                ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return proto


def conditions_of(cfg: dict[str, Any], *, step_names: dict[str, str], dataset_name: str | None,
                  plugin_code: dict[str, str] | None) -> dict[str, Any]:
    model = cfg.get("model") or {}
    steps = []
    for s in cfg.get("preprocessing") or []:
        params = ", ".join(f"{k}={_short(v)}" for k, v in sorted((s.get("params") or {}).items()))
        steps.append(f"{step_names.get(s['plugin_id'], s['plugin_id'])}" + (f"({params})" if params else ""))
    b = cfg.get("train_budget") or {}
    return {
        "dataset": dataset_name,
        "preprocessing": " → ".join(steps) or "없음",
        "model": f"{model.get('plugin_id')}@{model.get('plugin_version')}",
        "model_params": ", ".join(f"{k}={v}" for k, v in sorted((model.get("params") or {}).items())) or "기본값",
        "train_budget": "전체" if b.get("mode") != "recent_days" else f"최근 {b.get('days')}일",
        "plugin_code": ", ".join(f"{k}:{v[:8]}" for k, v in sorted((plugin_code or {}).items())) or "—",
    }


def _short(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return str(v)


def protocol_diffs(ref: dict[str, Any], other: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for k, label in PROTOCOL_KO.items():
        if ref.get(k) != other.get(k):
            out.append({"key": k, "label_ko": label, "reference": ref.get(k), "value": other.get(k)})
    return out


def comparable_horizons(ref: dict[str, Any], other: dict[str, Any]) -> list[str]:
    """두 실행에서 같은 표본 집합으로 평가된 예측 시점(나머지 평가 조건도 같을 때만)."""
    base_keys = ("dataset_version", "target", "timestamp_col", "group_col", "split", "metrics", "baseline")
    if any(ref.get(k) != other.get(k) for k in base_keys):
        return []
    a, b = ref.get("test_set_by_horizon") or {}, other.get("test_set_by_horizon") or {}
    return [h for h in ref.get("horizons") or [] if h in a and a.get(h) == b.get(h)]


def series(exps: list[Path], *, horizon: str, group: str, split: str = "test") -> dict[str, Any]:
    """실행별 예측을 같은 (대상 시각) 표본끼리 맞춘 그래프 자료. 실제값은 첫 실행 기준(같은 데이터면 같음)."""
    tables: list[dict[str, tuple[float, float]]] = []
    for exp in exps:
        t: dict[str, tuple[float, float]] = {}
        with (exp / "predictions" / "predictions.csv").open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["split"] == split and row["horizon"] == horizon and row["group"] == group:
                    t[row["target_time"]] = (float(row["actual"]), float(row["predicted"]))
        tables.append(t)
    common = sorted(set.intersection(*(set(t) for t in tables))) if tables else []
    total = len(common)
    if total > MAX_POINTS:
        step = total / MAX_POINTS
        common = [common[int(i * step)] for i in range(MAX_POINTS)]
    return {
        "horizon": horizon, "group": group, "split": split, "points_total": total, "points_shown": len(common),
        "target_time": common,
        "actual": [tables[0][k][0] for k in common],
        "predicted": [[t[k][1] for k in common] for t in tables],
    }


def groups_of(exp: Path, split: str = "test") -> list[str]:
    out: set[str] = set()
    with (exp / "predictions" / "predictions.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["split"] == split:
                out.add(row["group"])
    return sorted(out)
