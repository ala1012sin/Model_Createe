"""Provider 설정 (CONTRACTS §2 ProviderConfig, examples/providers.yaml).

비밀키 '값'은 설정에 쓰지 않는다. api_key_env에는 환경변수 '이름'만 허용한다.
"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mlworkbench.core.errors import ConfigError

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["openai", "openai_compatible", "mock"]
    base_url: str | None = None
    model: str
    auth_mode: Literal["bearer_env", "none"] = "bearer_env"
    api_key_env: str | None = None  # 비밀키의 '환경변수 이름'만
    timeout_s: int = Field(default=45, ge=5, le=180)
    max_tokens: int | None = Field(default=None, ge=1)

    @field_validator("api_key_env")
    @classmethod
    def _env_name_only(cls, v: str | None) -> str | None:
        if v is not None and not _ENV_NAME.match(v):
            raise ValueError("api_key_env에는 환경변수 이름(예: OPENAI_API_KEY)만 쓸 수 있습니다. 키 값을 넣지 마세요")
        return v

    @model_validator(mode="after")
    def _check(self) -> "ProviderConfig":
        if self.kind != "mock" and not self.base_url:
            raise ValueError("외부/로컬 공급자에는 base_url이 필요합니다")
        if self.auth_mode == "bearer_env" and self.kind != "mock" and not self.api_key_env:
            raise ValueError("bearer_env 인증에는 api_key_env가 필요합니다")
        return self

    @property
    def is_external(self) -> bool:
        """데이터가 이 PC 밖으로 나가는지. 판단이 불확실하면 외부로 본다."""
        if self.kind == "mock":
            return False
        if self.kind == "openai":
            return True
        host = (urlparse(self.base_url or "").hostname or "").lower()
        if host == "localhost":
            return False
        try:
            return not ipaddress.ip_address(host).is_loopback
        except ValueError:
            return True

    def key_present(self) -> bool:
        return bool(self.api_key_env and os.environ.get(self.api_key_env))


class DataPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_external_data: bool = False
    send_raw_files: Literal[False] = False
    send_absolute_paths: Literal[False] = False
    send_individual_sample_rows: Literal[False] = False
    send_image_pixels: Literal[False] = False
    max_profile_chars: int = Field(default=12000, ge=500, le=200000)
    expose_tools_during_recommendation: Literal["read_only"] = "read_only"
    require_user_approval_for_training: Literal[True] = True


class ProvidersFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_provider: str
    profiles: dict[str, ProviderConfig]
    policy: DataPolicy = Field(default_factory=DataPolicy)

    @model_validator(mode="before")
    @classmethod
    def _inject_ids(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
            data = dict(data)
            data["profiles"] = {k: {"id": k, **(v or {})} for k, v in data["profiles"].items()}
        return data

    @model_validator(mode="after")
    def _active_exists(self) -> "ProvidersFile":
        if self.active_provider not in self.profiles:
            raise ValueError(f"active_provider '{self.active_provider}'가 profiles에 없습니다")
        return self

    @property
    def active(self) -> ProviderConfig:
        return self.profiles[self.active_provider]

    def env_names(self) -> list[str]:
        return sorted({p.api_key_env for p in self.profiles.values() if p.api_key_env})


def load_providers(path: str | Path) -> ProvidersFile:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Provider 설정 파일이 없습니다: {p.name}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return ProvidersFile.model_validate(data)
    except (yaml.YAMLError, ValidationError) as e:
        raise ConfigError(f"Provider 설정 오류: {e}") from e
