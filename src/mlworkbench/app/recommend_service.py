"""S2 전처리 추천 — 저장된 목표 + 별칭 SafeProfile로 Mock 추천을 받아 초안 후보로 만든다.

경로 두 가지 (같은 요청 조립·정책 검사·별칭 복원을 공유)
- ready      : 실제 추천 경로. 카탈로그에는 ready 플러그인만 들어가고 ProviderGateway가 활성 공급자를 쓴다.
               ready 모델이 하나라도 있으면 이 경로를 쓴다.
- preview    : 로컬 계획 미리보기. 내장 플러그인이 아직 미구현이라 ready 후보가 없을 때만 쓴다.
               외부 공급자를 부르지 않고 항상 로컬 MockProvider(allow_unready)를 쓴다.
               후보는 '등록·호환'이지만 실행 불가(status 그대로)로 표시되며, 최종 검증·승인·제출에서 차단된다.

별칭
- 요청: 시간열·타깃·설비 구분 열을 같은 프로파일의 column_N 별칭으로 바꿔 보낸다.
- 응답: user_goal과 x-column 파라미터의 별칭을 그 버전의 사전에서만 원래 이름으로 복원한다.
        사전에 없는 값(다른 버전의 별칭, 원래 헤더 등)이 오면 추천 전체를 거부한다.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any

from mlworkbench.core.contracts import (
    DatasetState,
    LocalProfile,
    PlanProposal,
    TimeseriesGoal,
    ToolSelection,
    utcnow_iso,
)
from mlworkbench.core.errors import NotFound, ProviderError, StateTransitionError, WorkbenchError
from mlworkbench.core.registry import PluginRegistry
from mlworkbench.datasets.safe_profile import build_safe_profile
from mlworkbench.providers.base import RecommendationRequest
from mlworkbench.providers.config import ProvidersFile
from mlworkbench.providers.gateway import ProviderGateway
from mlworkbench.providers.mock import MockProvider


class RecommendationRejected(WorkbenchError):
    code = "RECOMMENDATION_REJECTED"


@dataclass(frozen=True)
class AliasBook:
    """한 데이터셋 버전·프로파일 버전에 묶인 별칭 사전."""

    dataset_id: str
    dataset_version: str
    profile_version: int
    name_by_alias: dict[str, str]

    @classmethod
    def of(cls, lp: LocalProfile) -> "AliasBook":
        return cls(lp.dataset_id, lp.dataset_version, lp.profile_version, lp.alias_map())

    def alias(self, name: str | None) -> str | None:
        if name is None:
            return None
        for a, n in self.name_by_alias.items():
            if n == name:
                return a
        raise RecommendationRejected(f"목표에 지정한 열을 현재 데이터에서 찾을 수 없습니다", code="GOAL_COLUMN_MISSING")

    def unalias(self, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in self.name_by_alias:
            raise RecommendationRejected("추천 응답에 현재 데이터 버전에 없는 열 참조가 있어 적용하지 않았습니다",
                                         code="ALIAS_UNKNOWN")
        return self.name_by_alias[value]


def _column_params(registry: PluginRegistry, sel: ToolSelection) -> list[str]:
    spec = registry.get(sel.plugin_id, sel.plugin_version)
    if spec is None:
        return []
    props = spec.parameter_schema.get("properties", {})
    return [k for k, v in props.items() if v.get("x-column")]


class RecommendationService:
    def __init__(self, ws, datasets, plans, registry: PluginRegistry, providers: ProvidersFile):
        self.ws, self.datasets, self.plans, self.registry = ws, datasets, plans, registry
        self.gateway = ProviderGateway(providers, registry)
        self.last_request: RecommendationRequest | None = None  # 테스트·감사용(서버 메모리만)

    # 요청 조립 --------------------------------------------------------------
    def _context(self, dataset_id: str) -> tuple[LocalProfile, TimeseriesGoal, AliasBook]:
        m = self.datasets.get(dataset_id)
        if m.state != DatasetState.PROFILED:
            raise StateTransitionError("분석이 끝난 데이터셋에서만 추천을 받을 수 있습니다")
        lp = self.datasets.get_local_profile(dataset_id)
        if lp is None:
            raise StateTransitionError("분석 결과를 찾을 수 없습니다. 데이터셋을 다시 분석해 주세요")
        found = self.plans.latest_draft_for_dataset(dataset_id)
        goal = found[1].user_goal if found else None
        if not isinstance(goal, TimeseriesGoal) or not goal.target_col:
            raise StateTransitionError("데이터셋 화면에서 예측 목표를 먼저 저장해 주세요", code="GOAL_REQUIRED")
        return lp, goal, AliasBook.of(lp)

    def _has_ready_model(self, task: str) -> bool:
        return any(self.registry.find(kind="model", modality="timeseries", task=task, ready_only=True))

    def build_request(self, dataset_id: str) -> tuple[RecommendationRequest, AliasBook, TimeseriesGoal, str]:
        lp, goal, book = self._context(dataset_id)
        aliased = TimeseriesGoal(timestamp_col=book.alias(goal.timestamp_col), target_col=book.alias(goal.target_col),
                                 group_col=book.alias(goal.group_col), horizons=list(goal.horizons))
        mode = "ready" if self._has_ready_model(goal.task) else "preview"
        req = self.gateway.build_request(safe_profile=build_safe_profile(lp), task=goal.task, goal=aliased,
                                         include_unready=(mode == "preview"))
        return req, book, goal, mode

    # 추천 -------------------------------------------------------------------
    async def recommend(self, dataset_id: str, *, provider=None) -> dict[str, Any]:
        req, book, goal, mode = self.build_request(dataset_id)
        self.last_request = req
        if mode == "preview":
            prov = provider or MockProvider(allow_unready=True)
            source_label = "Mock · 오프라인 (계획 미리보기)"
        else:
            prov = provider  # None이면 활성 공급자
            source_label = "Mock · 오프라인" if self.gateway.config.kind == "mock" else self.gateway.config.id
        try:
            proposal = await self.gateway.generate_plan(req, provider=prov)
        except ProviderError as e:
            raise RecommendationRejected(e.message_ko, code=e.code) from e
        restored = self._restore(proposal, book, goal)
        rec_id = "rec_" + secrets.token_hex(6)
        payload = {"proposal": restored.model_dump(mode="json"), "source_label": source_label, "mode": mode,
                   "catalog_version": self.registry.catalog_version()}
        with self.ws.session() as conn:
            conn.execute("INSERT INTO recommendations VALUES (?,?,?,?,?,?,?)",
                         (rec_id, dataset_id, book.dataset_version, book.profile_version, mode,
                          json.dumps(payload, ensure_ascii=False), utcnow_iso()))
        return {"recommendation_id": rec_id, **payload, "dataset_version": book.dataset_version}

    def _restore(self, p: PlanProposal, book: AliasBook, goal: TimeseriesGoal) -> PlanProposal:
        """응답 별칭을 같은 버전 사전으로 복원하고, 저장된 목표와 일치하는지 확인한다."""
        g = p.user_goal
        if not isinstance(g, TimeseriesGoal):
            raise RecommendationRejected("추천 응답에 예측 목표가 없습니다", code="RECOMMENDATION_GOAL_MISMATCH")
        restored_goal = TimeseriesGoal(timestamp_col=book.unalias(g.timestamp_col), target_col=book.unalias(g.target_col),
                                       group_col=book.unalias(g.group_col), horizons=list(g.horizons))
        if restored_goal != goal:
            raise RecommendationRejected("추천 응답의 목표가 저장한 목표와 달라 적용하지 않았습니다",
                                         code="RECOMMENDATION_GOAL_MISMATCH")
        steps = []
        for sel in p.preprocessing:
            params = dict(sel.params)
            for key in _column_params(self.registry, sel):
                if key in params and isinstance(params[key], list):
                    params[key] = [book.unalias(v) for v in params[key]]
            steps.append(sel.model_copy(update={"params": params}))
        return p.model_copy(update={"user_goal": restored_goal, "preprocessing": steps})

    def get(self, recommendation_id: str) -> dict[str, Any]:
        with self.ws.session() as conn:
            row = conn.execute("SELECT * FROM recommendations WHERE recommendation_id=?", (recommendation_id,)).fetchone()
        if row is None:
            raise NotFound("추천 기록을 찾을 수 없습니다")
        return {"dataset_id": row["dataset_id"], "dataset_version": row["dataset_version"],
                "profile_version": row["profile_version"], "mode": row["mode"], **json.loads(row["payload_json"])}
