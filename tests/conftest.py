"""공통 fixture. 합성 매니페스트·Mock만 사용한다 (실제 데이터·외부 API 없음)."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest
import yaml

from mlworkbench.app.services import DatasetService, JobService, PlanService
from mlworkbench.core.contracts import DatasetManifest, DraftPlan
from mlworkbench.core.registry import PluginRegistry, builtin_registry
from mlworkbench.storage.database import Workspace

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_project_plugins(monkeypatch):
    """프로젝트 plugins/ 폴더(예제 포함)가 다른 테스트의 카탈로그에 섞이지 않게 기본은 사용자 플러그인 없음."""
    monkeypatch.setenv("MLWB_PLUGIN_DIRS", "")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def builtin_reg() -> PluginRegistry:
    """내장 '명세' 카탈로그(구현 어댑터 없이, 모두 미구현). P0 계약·계획 미리보기 흐름 검증용."""
    return builtin_registry(with_adapters=False)


@pytest.fixture
def ready_reg() -> PluginRegistry:
    """테스트 전용: 내장 명세를 '실행 가능'으로 복제. 실제 구현이 있다는 뜻이 아니다."""
    return PluginRegistry(s.model_copy(update={"status": "ready", "reason_unavailable": None}) for s in builtin_registry(with_adapters=False).all())


@pytest.fixture
def ts_manifest() -> DatasetManifest:
    return DatasetManifest.model_validate(yaml.safe_load((FIX / "ts_manifest.yaml").read_text(encoding="utf-8")))


@pytest.fixture
def vis_manifest() -> DatasetManifest:
    return DatasetManifest.model_validate(yaml.safe_load((FIX / "vision_manifest.yaml").read_text(encoding="utf-8")))


@pytest.fixture
def ts_plan_dict() -> dict:
    return yaml.safe_load((ROOT / "examples" / "timeseries_plan.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def vis_plan_dict() -> dict:
    return yaml.safe_load((ROOT / "examples" / "vision_plan.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def ws(tmp_path) -> Workspace:
    return Workspace(tmp_path / "workspace").init()


@pytest.fixture
def services(ws, ready_reg, ts_manifest, vis_manifest):
    ds = DatasetService(ws)
    ds.register(ts_manifest)
    ds.register(vis_manifest)
    plans = PlanService(ws, ready_reg, ds)
    jobs = JobService(ws, ready_reg, ds, plans)
    return ds, plans, jobs


@pytest.fixture
def approved(services, ts_plan_dict):
    """검증·승인까지 마친 시계열 초안."""
    _, plans, _ = services
    draft_id, rev = plans.create_draft(DraftPlan.model_validate(ts_plan_dict))
    receipt = plans.approve(draft_id, rev, approved_by="local_user")
    return draft_id, rev, receipt


def mutate(d: dict, fn) -> dict:
    d = copy.deepcopy(d)
    fn(d)
    return d
