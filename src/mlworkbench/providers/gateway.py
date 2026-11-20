"""ProviderGateway — 공급자 교체·전송 정책·정형 출력 파싱 (CONTRACTS §6)."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from pydantic import ValidationError

from mlworkbench.core.contracts import PlanProposal, SafeProfile, UserGoal
from mlworkbench.core.errors import PolicyError, ProviderOutputError
from mlworkbench.core.registry import PluginRegistry
from mlworkbench.core.security import find_sensitive
from mlworkbench.core.validation import pydantic_issues
from mlworkbench.providers.base import LLMProvider, RecommendationRequest
from mlworkbench.providers.config import ProviderConfig, ProvidersFile
from mlworkbench.providers.mock import MockProvider
from mlworkbench.providers.stubs import OpenAICompatibleProvider, OpenAIProvider

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def create_provider(config: ProviderConfig) -> LLMProvider:
    if config.kind == "mock":
        return MockProvider(config.id)
    if config.kind == "openai":
        return OpenAIProvider(config)
    return OpenAICompatibleProvider(config)


def extract_json(raw: str | dict) -> dict[str, Any]:
    """JSON-only fallback: 순수 JSON → 코드펜스 → 첫 '{'~마지막 '}' 순서로 추출."""
    if isinstance(raw, dict):
        return raw
    text = raw.strip()
    candidates = [text]
    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1))
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"): text.rindex("}") + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise ProviderOutputError("추천 응답에서 JSON을 찾지 못했습니다")


def parse_proposal(raw: str | dict) -> PlanProposal:
    data = extract_json(raw)
    try:
        return PlanProposal.model_validate(data)
    except ValidationError as e:
        raise ProviderOutputError("추천 응답이 계약 형식(PlanProposal)과 맞지 않습니다", pydantic_issues(e)) from e


class ProviderGateway:
    def __init__(self, providers: ProvidersFile, registry: PluginRegistry,
                 factory: Callable[[ProviderConfig], LLMProvider] = create_provider, max_retries: int = 1):
        self.providers, self.registry, self.factory, self.max_retries = providers, registry, factory, max_retries

    @property
    def config(self) -> ProviderConfig:
        return self.providers.active

    def build_request(self, *, safe_profile: SafeProfile, task: str, goal: UserGoal | None,
                      include_unready: bool = False) -> RecommendationRequest:
        """include_unready=True는 로컬 계획 미리보기 전용(실행 불가 후보 포함, ready 값은 그대로 전달)."""
        catalog = [s.public_view() for s in
                   self.registry.find(modality=safe_profile.modality, task=task, ready_only=not include_unready)]
        return RecommendationRequest(dataset_id=safe_profile.dataset_id, modality=safe_profile.modality, task=task,
                                     goal=goal, safe_profile=safe_profile, catalog=catalog)

    def check_policy(self, request: RecommendationRequest, *, external_consent: bool = False) -> None:
        pol = self.providers.policy
        if self.config.is_external and not (pol.allow_external_data or external_consent):
            raise PolicyError("외부 LLM으로 데이터 요약을 보내려면 전송 요약을 확인하고 허용해야 합니다",
                              code="EXTERNAL_CONSENT_REQUIRED")
        payload = request.model_dump(mode="json")
        hits = find_sensitive(payload)
        if hits:
            raise PolicyError(f"전송 금지 정보(절대경로·비밀값 등)가 포함되어 차단했습니다: {', '.join(hits[:5])}",
                              code="SENSITIVE_PAYLOAD")
        if len(json.dumps(payload["safe_profile"], ensure_ascii=False)) > pol.max_profile_chars:
            raise PolicyError("데이터 요약이 전송 한도를 넘습니다", code="PROFILE_TOO_LARGE")

    async def generate_plan(self, request: RecommendationRequest, *, external_consent: bool = False,
                            provider: LLMProvider | None = None) -> PlanProposal:
        self.check_policy(request, external_consent=external_consent)
        prov = provider or self.factory(self.config)
        caps = await prov.get_capabilities()
        schema = PlanProposal.model_json_schema() if caps.json_schema_output else None
        last: ProviderOutputError | None = None
        for _ in range(self.max_retries + 1):
            raw = await prov.generate_raw(request, schema=schema)
            try:
                proposal = parse_proposal(raw)
            except ProviderOutputError as e:
                last = e
                continue
            if proposal.dataset_id != request.dataset_id or proposal.modality != request.modality:
                raise ProviderOutputError("추천안의 데이터셋/종류가 요청과 다릅니다")
            return proposal
        assert last is not None
        raise last
