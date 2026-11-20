"""외부/로컬 OpenAI 호환 Provider 스텁 (P1에서 구현).

P0는 외부 네트워크 호출을 하지 않는다. 설정·상태 확인 경로만 제공한다.
"""

from __future__ import annotations

from typing import Any

from mlworkbench.core.errors import ProviderError
from mlworkbench.providers.base import ProviderCapabilities, RecommendationRequest
from mlworkbench.providers.config import ProviderConfig


class _StubProvider:
    def __init__(self, config: ProviderConfig):
        self.config = config
        self.config_id = config.id

    async def get_capabilities(self) -> ProviderCapabilities:
        # 실제 연결 전에는 보수적으로 가정: 도구 호출·JSON Schema 미지원 → JSON-only fallback
        return ProviderCapabilities(detected=False)

    async def generate_raw(self, request: RecommendationRequest, *, schema: dict[str, Any] | None = None) -> str | dict:
        raise ProviderError(f"{self.config.kind} 공급자는 P1에서 구현됩니다. 지금은 Mock 또는 수동 설정을 사용하세요",
                            code="PROVIDER_NOT_IMPLEMENTED")

    async def health_check(self) -> dict[str, Any]:
        return {
            "ok": False,
            "provider": self.config.id,
            "kind": self.config.kind,
            "reason": "P0: 연결 테스트 미구현(외부 호출 안 함)",
            "api_key_env": self.config.api_key_env,
            "api_key_present": self.config.key_present(),  # 값은 노출하지 않음
        }


class OpenAIProvider(_StubProvider):
    pass


class OpenAICompatibleProvider(_StubProvider):
    """Ollama/vLLM 등. auth_mode=none의 더미 토큰은 인증이 아니다."""
