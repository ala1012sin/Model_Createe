"""플러그인 레지스트리 — 기능의 단일 진실 공급원."""

from __future__ import annotations

from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, Iterable

from mlworkbench.core import schema_check
from mlworkbench.core.contracts import PluginKind, PluginSpec
from mlworkbench.core.errors import RegistryError

if TYPE_CHECKING:
    from mlworkbench.plugins.models import ModelAdapter
    from mlworkbench.plugins.preprocessor import PreprocessorAdapter


def _vkey(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _module_available(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class PluginRegistry:
    def __init__(self, specs: Iterable[PluginSpec] = ()):
        self._specs: dict[str, dict[str, PluginSpec]] = {}
        self._preprocessors: dict[tuple[str, str], PreprocessorAdapter] = {}
        self._models: dict[tuple[str, str], ModelAdapter] = {}
        self.load_problems: list[dict[str, str]] = []  # 사용자 플러그인 불러오기 문제(폴더 이름·코드·사유)
        self._user_folders: dict[str, Any] = {}  # 사용자 플러그인 key → 폴더(내부용, 화면·LLM에 내보내지 않음)
        for s in specs:
            self.register(s)

    # 등록 -----------------------------------------------------------------
    def register(self, spec: PluginSpec) -> None:
        versions = self._specs.setdefault(spec.id, {})
        if spec.version in versions:
            raise RegistryError(f"이미 등록된 플러그인 버전입니다: {spec.key}", code="DUPLICATE_PLUGIN")
        if versions:
            first = next(iter(versions.values()))
            if (first.kind, first.modality) != (spec.kind, spec.modality):
                raise RegistryError(f"같은 id의 종류/모달리티가 다릅니다: {spec.id}", code="PLUGIN_CONFLICT")
        versions[spec.version] = spec

    def register_preprocessor(self, adapter: PreprocessorAdapter) -> None:
        """명시적으로 전달된 구현체만 같은 카탈로그에 등록한다."""
        spec = adapter.spec
        if spec.kind != "preprocessor":
            raise RegistryError(f"전처리 명세가 아닙니다: {spec.key}", code="INVALID_PREPROCESSOR")
        if not spec.source_library or not spec.source_library.strip() or spec.fit_scope is None:
            raise RegistryError(f"전처리 라이브러리 출처와 fit 범위를 지정해야 합니다: {spec.key}", code="INVALID_PREPROCESSOR")
        if not all(callable(getattr(adapter, method, None)) for method in ("validate", "fit", "transform", "preview")):
            raise RegistryError(f"전처리 어댑터의 필수 메서드가 없습니다: {spec.key}", code="INVALID_PREPROCESSOR")
        if spec.parameter_schema.get("type") != "object":
            raise RegistryError(f"전처리 파라미터 스키마의 루트 타입은 object여야 합니다: {spec.key}", code="INVALID_PARAMETER_SCHEMA")
        if error := schema_check.schema_error(spec.parameter_schema):
            raise RegistryError(f"잘못된 전처리 파라미터 스키마: {spec.key}: {error}", code="INVALID_PARAMETER_SCHEMA")
        if any(not name.isidentifier() for name in spec.dependencies):
            raise RegistryError(f"의존성은 최상위 Python 모듈 이름으로 지정해야 합니다: {spec.key}", code="INVALID_DEPENDENCY")
        if spec.ready:
            missing = [name for name in spec.dependencies if not _module_available(name)]
            if missing:
                spec = spec.model_copy(update={
                    "status": "missing_dependency",
                    "reason_unavailable": f"필요한 Python 모듈이 없습니다: {', '.join(missing)}",
                })
        self.register(spec)
        self._preprocessors[spec.id, spec.version] = adapter

    def get_preprocessor_adapter(self, plugin_id: str, version: str) -> PreprocessorAdapter | None:
        spec = self.get(plugin_id, version)
        if spec is None or not spec.ready:
            return None
        return self._preprocessors.get((plugin_id, version))

    def register_model(self, adapter: ModelAdapter) -> None:
        """학습 모델 구현체 등록. 의존성 확인 방식은 전처리 어댑터와 같다."""
        spec = adapter.spec
        if spec.kind != "model":
            raise RegistryError(f"모델 명세가 아닙니다: {spec.key}", code="INVALID_MODEL")
        if not spec.source_library or not all(
                callable(getattr(adapter, m, None)) for m in ("validate", "fit", "predict", "save", "load")):
            raise RegistryError(f"모델 어댑터의 출처나 필수 메서드가 없습니다: {spec.key}", code="INVALID_MODEL")
        if error := schema_check.schema_error(spec.parameter_schema):
            raise RegistryError(f"잘못된 모델 파라미터 스키마: {spec.key}: {error}", code="INVALID_PARAMETER_SCHEMA")
        if spec.ready:
            missing = [name for name in spec.dependencies if not _module_available(name)]
            if missing:
                spec = spec.model_copy(update={
                    "status": "missing_dependency",
                    "reason_unavailable": f"필요한 Python 모듈이 없습니다: {', '.join(missing)}",
                })
        self.register(spec)
        self._models[spec.id, spec.version] = adapter

    def get_model_adapter(self, plugin_id: str, version: str) -> ModelAdapter | None:
        spec = self.get(plugin_id, version)
        if spec is None or not spec.ready:
            return None
        return self._models.get((plugin_id, version))

    # 조회 -----------------------------------------------------------------
    def has(self, plugin_id: str) -> bool:
        return plugin_id in self._specs

    def versions(self, plugin_id: str) -> list[str]:
        return sorted(self._specs.get(plugin_id, {}), key=_vkey)

    def get(self, plugin_id: str, version: str) -> PluginSpec | None:
        return self._specs.get(plugin_id, {}).get(version)

    def latest(self, plugin_id: str, *, ready_only: bool = False) -> PluginSpec | None:
        cands = [s for s in self._specs.get(plugin_id, {}).values() if s.ready or not ready_only]
        return max(cands, key=lambda s: _vkey(s.version)) if cands else None

    def all(self) -> list[PluginSpec]:
        return [s for vs in self._specs.values() for s in vs.values()]

    def find(
        self,
        *,
        kind: PluginKind | None = None,
        modality: str | None = None,
        task: str | None = None,
        ready_only: bool = False,
    ) -> list[PluginSpec]:
        out = []
        for s in self.all():
            if kind and s.kind != kind:
                continue
            if modality and s.modality != modality:
                continue
            if task and task not in s.tasks:
                continue
            if ready_only and not s.ready:
                continue
            out.append(s)
        return sorted(out, key=lambda s: (s.kind, s.order.stage, s.id, _vkey(s.version)))

    def current_code_hash(self, plugin_id: str, version: str) -> str | None:
        """사용자 플러그인은 지금 디스크의 코드 해시(파일이 바뀌었는지 확인용), 내장은 None."""
        spec = self.get(plugin_id, version)
        if spec is None or spec.origin != "user":
            return None
        folder = self._user_folders.get(spec.key)
        if folder is None:
            return spec.code_sha256
        from mlworkbench.plugins.user_loader import code_hash

        try:
            return code_hash(folder)
        except OSError:
            return "unreadable"

    def catalog_version(self) -> str:
        from mlworkbench.core.hashing import sha256_of

        return sha256_of(sorted(f"{s.key}:{s.status}" + (f":{s.code_sha256}" if s.code_sha256 else "")
                                for s in self.all()))[:19]


def builtin_registry(*, with_adapters: bool = True) -> PluginRegistry:
    """내장 카탈로그. 구현이 있는 ID·버전은 같은 명세의 어댑터 등록으로 교체한다(P2).

    with_adapters=False 는 명세만 있는 카탈로그(모두 미구현)로, 계획 미리보기 흐름 테스트에 쓴다."""
    from mlworkbench.plugins.builtin import builtin_specs

    specs = builtin_specs()
    if not with_adapters:
        return PluginRegistry(specs)
    from mlworkbench.plugins.models import model_adapters
    from mlworkbench.plugins.timeseries import timeseries_adapters

    pre, models = timeseries_adapters(specs), model_adapters(specs)
    replaced = {a.spec.key for a in pre} | {a.spec.key for a in models}
    reg = PluginRegistry(s for s in specs if s.key not in replaced)
    for a in pre:
        reg.register_preprocessor(a)
    for m in models:
        reg.register_model(m)
    return reg


def default_registry() -> PluginRegistry:
    """앱·worker·CLI가 쓰는 카탈로그: 내장 + 사용자 정의 플러그인(plugins/ 또는 MLWB_PLUGIN_DIRS)."""
    from mlworkbench.plugins.user_loader import load_user_plugins

    reg = builtin_registry()
    reg.load_problems = load_user_plugins(reg)
    return reg
