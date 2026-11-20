"""MCP 도구 카탈로그와 얇은 Facade (ARCHITECTURE §5, CONTRACTS §5).

P0 범위: 도구 정의·읽기 전용 allowlist·Application Service 위임. MCP 전송(stdio/HTTP)은 구현하지 않는다.
LLM이 보는 컨텍스트("recommendation")에는 읽기 전용 도구만 노출되며, 쓰기 도구는 호출 자체가 거부된다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from mlworkbench.datasets.safe_profile import safe_profile_from_manifest  # noqa: F401 (재노출)
from mlworkbench.core.errors import ToolDenied, WorkbenchError
from mlworkbench.core.registry import PluginRegistry
from mlworkbench.core.security import find_sensitive
from mlworkbench.core.validation import PlanValidator

Context = Literal["recommendation", "host"]


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description_ko: str
    input_schema: dict[str, Any]
    read_only: bool
    requires_user_approval: bool = False
    implemented_in: str = "P0"


def _obj(**props: Any) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_S = {"type": "string"}
TOOLS: tuple[ToolDescriptor, ...] = (
    ToolDescriptor(name="dataset.get_profile", description_ko="등록 데이터셋의 비식별 구조 요약", input_schema=_obj(dataset_id=_S), read_only=True),
    ToolDescriptor(name="catalog.list_preprocessors", description_ko="전처리 목록·설명·스키마·실행 가능 여부",
                   input_schema=_obj(modality=_S, task=_S), read_only=True),
    ToolDescriptor(name="catalog.list_models", description_ko="태스크별 모델 목록·요건·스키마",
                   input_schema=_obj(modality=_S, task=_S), read_only=True),
    ToolDescriptor(name="pipeline.validate", description_ko="계획의 구조·호환성·순서·누수 위험 검증(제출 권한 없음)",
                   input_schema=_obj(candidate_plan={"type": "object"}), read_only=True),
    ToolDescriptor(name="pipeline.preview", description_ko="검증된 초안 리비전으로 소량 미리보기",
                   input_schema=_obj(draft_id=_S, revision={"type": "integer"}), read_only=True, implemented_in="P1"),
    ToolDescriptor(name="experiment.estimate_resources", description_ko="예상 시간·메모리(불확실성 포함)",
                   input_schema=_obj(candidate_plan={"type": "object"}), read_only=True, implemented_in="P2"),
    ToolDescriptor(name="experiment.get_status", description_ko="실행 상태와 이후 이벤트",
                   input_schema={"type": "object", "properties": {"run_id": _S, "after_seq": {"type": "integer"}},
                                 "required": ["run_id"], "additionalProperties": False}, read_only=True),
    ToolDescriptor(name="experiment.get_results", description_ko="완료 실행의 지표·산출물",
                   input_schema=_obj(run_id=_S), read_only=True, implemented_in="P2"),
    ToolDescriptor(name="experiment.compare", description_ko="같은 프로토콜 실험 비교",
                   input_schema=_obj(run_ids={"type": "array"}), read_only=True, implemented_in="P5"),
    ToolDescriptor(name="experiment.submit", description_ko="승인된 계획 실행 등록(사용자 승인 필수)",
                   input_schema=_obj(approved_plan_id=_S, approval_id=_S), read_only=False, requires_user_approval=True),
    ToolDescriptor(name="experiment.cancel", description_ko="실행 취소 요청(별도 확인 필수)",
                   input_schema=_obj(run_id=_S, confirmation_id=_S), read_only=False, requires_user_approval=True),
)
TOOL_INDEX = {t.name: t for t in TOOLS}
RECOMMENDATION_ALLOWLIST = frozenset(t.name for t in TOOLS if t.read_only)


class ToolFacade:
    def __init__(self, registry: PluginRegistry, *, datasets: Any = None, jobs: Any = None):
        self.registry, self.datasets, self.jobs = registry, datasets, jobs
        self.validator = PlanValidator(registry)

    def list_tools(self, context: Context) -> list[dict[str, Any]]:
        tools = TOOLS if context == "host" else [t for t in TOOLS if t.name in RECOMMENDATION_ALLOWLIST]
        return [t.model_dump() for t in tools]

    def call(self, name: str, args: dict[str, Any], *, context: Context) -> dict[str, Any]:
        tool = TOOL_INDEX.get(name)
        if tool is None:
            raise ToolDenied(f"등록되지 않은 도구입니다: {name}", code="UNKNOWN_TOOL")
        if context != "host" and name not in RECOMMENDATION_ALLOWLIST:
            raise ToolDenied(f"추천 단계에서는 '{name}'을(를) 호출할 수 없습니다(읽기 전용만 허용)")
        if tool.implemented_in != "P0":
            raise WorkbenchError(f"{name}은(는) {tool.implemented_in}에서 구현됩니다", code="TOOL_NOT_IMPLEMENTED")
        out = getattr(self, "_" + name.replace(".", "_"))(**args)
        if context != "host" and find_sensitive(out):
            raise ToolDenied("도구 응답에 전송 금지 정보가 있어 차단했습니다", code="SENSITIVE_OUTPUT")
        return out

    # 읽기 전용 -------------------------------------------------------------
    def _catalog(self, kind: str, modality: str, task: str) -> dict[str, Any]:
        items = [s.public_view() for s in self.registry.find(kind=kind, modality=modality, task=task)]  # type: ignore[arg-type]
        return {"items": items, "catalog_version": self.registry.catalog_version()}

    def _catalog_list_preprocessors(self, modality: str, task: str) -> dict[str, Any]:
        return self._catalog("preprocessor", modality, task)

    def _catalog_list_models(self, modality: str, task: str) -> dict[str, Any]:
        return self._catalog("model", modality, task)

    def _dataset_get_profile(self, dataset_id: str) -> dict[str, Any]:
        return self.datasets.get_safe_profile(dataset_id).model_dump(mode="json")

    def _pipeline_validate(self, candidate_plan: dict[str, Any]) -> dict[str, Any]:
        ds = self.datasets.find(candidate_plan.get("dataset_id", "")) if self.datasets else None
        result, _ = self.validator.validate(candidate_plan, mode="draft", dataset=ds)
        return {"ok": result.ok, "issues": [i.model_dump() for i in result.issues]}

    def _experiment_get_status(self, run_id: str, after_seq: int = 0) -> dict[str, Any]:
        return self.jobs.get_status(run_id, after_seq=after_seq).model_dump(mode="json")

    # 쓰기 (host 컨텍스트 전용, 서버 측 승인 검증) ----------------------------
    def _experiment_submit(self, approved_plan_id: str, approval_id: str) -> dict[str, Any]:
        return self.jobs.submit(plan_id=approved_plan_id, approval_id=approval_id)

    def _experiment_cancel(self, run_id: str, confirmation_id: str) -> dict[str, Any]:
        return self.jobs.cancel(run_id, confirmation_id)
