"""LLM 전송용 SafeProfile 생성 — 허용 필드만 골라 담는다(allowlist).

담는 것: 행·열 수, 별칭(column_N), 추정 타입, 결측률, 고유값 개수, 중복·제외 행 수,
         시간 후보의 별칭·해석률·간격 추정, 알림 코드.
담지 않는 것: 원래 파일명, 경로, 원래 헤더, 개별 셀 값(최소/최대·시각 범위 포함),
              줄 번호, 오류 원문, 인코딩/구분자 같은 파일 세부.
"""

from __future__ import annotations

import re

from mlworkbench.core.contracts import DatasetManifest, ImageProfile, LocalProfile, SafeProfile
from mlworkbench.core.errors import PolicyError
from mlworkbench.core.security import find_sensitive

HIDDEN_FIELD_KINDS = ["file_name", "local_path", "column_names", "cell_values", "value_ranges", "line_numbers", "error_details",
                      "sheet_names", "formulas"]


def build_safe_profile(local: LocalProfile, *, source_kind: str = "csv") -> SafeProfile:
    summary = {
        "source_kind": "xlsx" if source_kind == "xlsx" else "csv",
        "rows": local.row_count,
        "column_count": local.column_count,
        "duplicate_rows": local.duplicate_rows,
        "excluded_rows": local.malformed_rows,
        "columns": [
            {"alias": c.alias, "dtype": c.dtype, "null_pct": round(c.null_pct, 4),
             "distinct_count": c.distinct_count, "distinct_capped": c.distinct_capped}
            for c in local.columns
        ],
        "time_candidates": [
            {"alias": t.alias, "parse_rate": round(t.parse_rate, 4), "interval_status": t.interval.status,
             "interval": t.interval.label, "interval_share": t.interval.share,
             "backward_steps": t.backward_steps, "duplicate_timestamps": t.duplicate_timestamps}
            for t in local.time_candidates
        ],
        "notices": sorted({n.code for n in local.notices}),
    }
    sp = SafeProfile(dataset_id=local.dataset_id, profile_version=local.profile_version, modality="timeseries",
                     summary=summary, hidden_field_kinds=HIDDEN_FIELD_KINDS)
    assert_no_leak(sp, local)
    return sp


def safe_profile_from_manifest(m: DatasetManifest) -> SafeProfile:
    """로컬 프로파일이 없는 매니페스트(합성·비전)용 요약. 컬럼은 별칭으로만."""
    summary: dict = {"source_kind": m.source_kind, "state": m.state.value}
    if m.modality == "timeseries":
        summary.update(rows=m.row_count, columns=[
            {"alias": c.alias or f"column_{i + 1}", "dtype": c.dtype, "null_pct": c.null_pct}
            for i, c in enumerate(m.columns)])
    else:
        summary.update(images=m.image_count, split_roles=m.split_roles, artifacts=m.artifacts)
    return SafeProfile(dataset_id=m.dataset_id, profile_version=m.profile_version, modality=m.modality,
                       summary=summary, hidden_field_kinds=HIDDEN_FIELD_KINDS)


IMAGE_HIDDEN_FIELD_KINDS = ["file_name", "local_path", "folder_names", "file_names", "pixels", "defect_type_names"]


def build_image_safe_profile(ip: ImageProfile, *, source_kind: str = "zip") -> SafeProfile:
    """비전 요약: 폴더 역할(추정)별 장수·형식·크기·채널·GT 짝 수·불량 종류 개수. 폴더·파일 이름·경로·픽셀 없음."""
    by_role: dict[str, int] = {}
    for f in ip.folders:
        by_role[f.role_guess] = by_role.get(f.role_guess, 0) + f.images
    summary = {
        "source_kind": "image_folder" if source_kind == "image_folder" else "zip",
        "images": ip.image_count,
        "images_by_role_guess": by_role,
        "defect_type_count": sum(1 for f in ip.folders if f.role_guess == "test_defect"),
        "formats": dict(ip.formats),
        "sizes": [dict(x) for x in ip.sizes],
        "channels": dict(ip.channels),
        "mask_pairs": dict(ip.mask_pairs),
        "corrupt_in_sample": int(ip.integrity.get("corrupt", 0)),
        "notices": sorted({n.code for n in ip.notices}),
    }
    sp = SafeProfile(dataset_id=ip.dataset_id, profile_version=ip.profile_version, modality="vision",
                     summary=summary, hidden_field_kinds=IMAGE_HIDDEN_FIELD_KINDS)
    bad = [v for v in _strings(sp.summary) if not _ALLOWED_VALUE.match(v)]
    bad += [k for k in _keys(sp.summary) if not _ALLOWED_KEY.match(k)]
    bad += find_sensitive(sp.model_dump(mode="json"))
    if bad:
        raise PolicyError("전송 요약에 허용되지 않은 값이 섞여 생성을 중단했습니다", code="SAFE_PROFILE_LEAK")
    return sp


_ALLOWED_KEY = re.compile(r"^([a-z_]+|[0-9]|PNG|JPEG|BMP|TIFF)$")  # 요약 키는 고정 영문 이름·채널 수(폴더 이름이 키로 섞이지 않게)


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


_ALLOWED_VALUE = re.compile(
    r"^(column_\d+"                                        # 별칭
    r"|datetime|numeric|categorical|text|empty|excluded"     # 타입
    r"|estimated|insufficient|irregular"                     # 간격 상태
    r"|csv|xlsx|timeseries|zip|image_folder"                 # 출처·종류
    r"|\d+(\.\d+)?(s|min|h|d)"                              # 간격 라벨
    r"|[A-Z][A-Z0-9_]+)$"                                    # 알림 코드
)


def _strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


def assert_no_leak(sp: SafeProfile, local: LocalProfile) -> None:
    """마지막 방어선: 요약의 모든 문자열 값이 정해진 어휘(별칭·타입·상태 코드 등)에 속해야 한다.
    원래 헤더·셀 값·파일명·경로가 어떤 필드로든 섞이면 생성을 중단한다."""
    bad = [v for v in _strings(sp.summary) if not _ALLOWED_VALUE.match(v)]
    bad += find_sensitive(sp.model_dump(mode="json"))
    if bad:
        raise PolicyError("전송 요약에 허용되지 않은 값이 섞여 생성을 중단했습니다", code="SAFE_PROFILE_LEAK")
