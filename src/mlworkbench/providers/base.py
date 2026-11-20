"""LLM Provider 공통 계약.

각 공급자는 원시 응답(str 또는 dict)만 돌려준다. 파싱·스키마 검증·재시도는 Gateway가 공통으로 처리한다.
요청에는 승인 토큰·절대경로·원시 데이터가 들어갈 자리가 없다(extra=forbid).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from mlworkbench import SCHEMA_VERSION
from mlworkbench.core.contracts import Modality, SafeProfile, UserGoal


class ProviderCapabilities(BaseModel):
    tool_calling: bool = False
    json_schema_output: bool = False
    json_object_output: bool = True
    max_input_chars: int = 12000
    detected: bool = False  # 실제 연결로 확인했는지


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    dataset_id: str
    modality: Modality
    task: str
    goal: UserGoal | None = None
    safe_profile: SafeProfile
    catalog: list[dict[str, Any]] = Field(default_factory=list)  # ready 플러그인 공개 정보만


@runtime_checkable
class LLMProvider(Protocol):
    config_id: str

    async def get_capabilities(self) -> ProviderCapabilities: ...

    async def generate_raw(self, request: RecommendationRequest, *, schema: dict[str, Any] | None) -> str | dict: ...

    async def health_check(self) -> dict[str, Any]: ...
