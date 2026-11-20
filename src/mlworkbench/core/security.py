"""비밀값 마스킹과 LLM 전송 전 민감정보 점검."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterable

MASK = "****"

# 흔한 비밀키 형태 (값 자체를 찾아 가린다)
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*['\"]?[^\s'\",]{8,}"),
]

# 절대경로 (Windows 드라이브, UNC, POSIX 홈/루트)
_ABS_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/])|(?:^|[\s\"'(])\\\\[^\\\s]+\\|(?:^|[\s\"'(=])/(?:home|Users|root|mnt|var|etc|tmp|sessions)/")

SENSITIVE_KEYS = {"api_key", "apikey", "secret", "password", "token", "authorization", "approval_id", "local_path"}


def secret_values_from_env(env_names: Iterable[str]) -> list[str]:
    values = []
    for name in env_names:
        v = os.environ.get(name)
        if v and len(v) >= 4:
            values.append(v)
    return values


def redact(text: str, extra_secrets: Iterable[str] = ()) -> str:
    for s in sorted(set(extra_secrets), key=len, reverse=True):
        if s:
            text = text.replace(s, MASK)
    for pat in _SECRET_PATTERNS:
        text = pat.sub(MASK, text)
    return text


class RedactingFilter(logging.Filter):
    """로그 레코드에서 비밀값을 가린다. 환경변수 '이름' 목록으로 값을 찾는다."""

    def __init__(self, env_names: Iterable[str] = ()):
        super().__init__()
        self.env_names = list(env_names)

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg = redact(msg, secret_values_from_env(self.env_names))
        record.args = ()
        return True


def find_sensitive(payload: Any, path: str = "$") -> list[str]:
    """LLM/MCP로 나갈 페이로드에서 절대경로·비밀키·금지 키를 찾아 경로 목록으로 반환."""
    found: list[str] = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            p = f"{path}.{k}"
            if str(k).lower() in SENSITIVE_KEYS:
                found.append(p)
                continue
            found.extend(find_sensitive(v, p))
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            found.extend(find_sensitive(v, f"{path}[{i}]"))
    elif isinstance(payload, str):
        if _ABS_PATH.search(payload) or any(p.search(payload) for p in _SECRET_PATTERNS):
            found.append(path)
    return found
