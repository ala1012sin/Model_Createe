"""정규화 JSON 해시. 키 순서·공백과 무관하게 같은 설정이면 같은 해시."""

import hashlib
import json
from typing import Any


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_of(data: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def short_ref(secret: str) -> str:
    """비밀 토큰 원문 대신 저장할 참조값."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]
