import pytest

from mlworkbench.core.contracts import PluginSpec
from mlworkbench.core.errors import RegistryError
from mlworkbench.core.registry import PluginRegistry


def _spec(version="1.0.0", **kw):
    base = dict(id="ts.custom_feature", version=version, kind="preprocessor", modality="timeseries",
                tasks=["forecasting"], display_name_ko="사용자 피처", description_ko="테스트용",
                input_data_kind="table.timeseries", output_data_kind="table.timeseries")
    base.update(kw)
    return PluginSpec(**base)


def test_register_find_and_versions():
    r = PluginRegistry()
    r.register(_spec("1.0.0", status="ready"))
    r.register(_spec("1.2.0"))
    assert r.versions("ts.custom_feature") == ["1.0.0", "1.2.0"]
    assert r.latest("ts.custom_feature").version == "1.2.0"
    assert r.latest("ts.custom_feature", ready_only=True).version == "1.0.0"
    assert r.get("ts.custom_feature", "9.9.9") is None
    assert [s.key for s in r.find(ready_only=True)] == ["ts.custom_feature@1.0.0"]


def test_duplicate_and_conflicting_registration_rejected():
    r = PluginRegistry([_spec()])
    with pytest.raises(RegistryError) as e:
        r.register(_spec())
    assert e.value.code == "DUPLICATE_PLUGIN"
    with pytest.raises(RegistryError):
        r.register(_spec("2.0.0", modality="vision", tasks=["anomaly_detection"]))


def test_new_plugin_appears_in_catalog_without_core_change(builtin_reg):
    from mlworkbench.mcp.catalog import ToolFacade

    builtin_reg.register(_spec(status="ready"))
    out = ToolFacade(builtin_reg).call("catalog.list_preprocessors", {"modality": "timeseries", "task": "forecasting"},
                                       context="recommendation")
    assert "ts.custom_feature" in {i["id"] for i in out["items"]}


def test_builtin_catalog_is_not_executable_in_p0(builtin_reg):
    specs = builtin_reg.all()
    assert specs and not any(s.ready for s in specs)
    assert all(s.display_name_ko and s.description_ko and s.reason_unavailable for s in specs)


def test_catalog_version_changes_with_status(builtin_reg, ready_reg):
    assert builtin_reg.catalog_version() != ready_reg.catalog_version()


def test_builtin_registry_swaps_implemented_ids_to_adapters(monkeypatch):
    """P2: 구현이 있는 ID·버전은 같은 명세의 어댑터로 교체. 의존성이 없으면 missing_dependency."""
    from mlworkbench.core import registry as R
    from mlworkbench.core.registry import builtin_registry
    from mlworkbench.plugins.models import IMPLEMENTED_MODELS
    from mlworkbench.plugins.timeseries import IMPLEMENTED_PREPROCESSORS

    spec_only = {s.key: s for s in builtin_registry(with_adapters=False).all()}
    reg = builtin_registry()
    assert {s.key for s in reg.all()} == set(spec_only)  # ID·버전 공간은 그대로
    for s in reg.all():
        base = spec_only[s.key]
        assert s.parameter_schema == base.parameter_schema and s.order == base.order
        if s.id in IMPLEMENTED_PREPROCESSORS | IMPLEMENTED_MODELS:
            assert s.status in ("ready", "missing_dependency") and s.source_library
            assert (reg.get_preprocessor_adapter(s.id, s.version) or reg.get_model_adapter(s.id, s.version)) \
                is not None or s.status == "missing_dependency"
        else:
            assert s.status == "not_implemented"
    monkeypatch.setattr(R, "_module_available", lambda name: name != "xgboost")
    lacking = builtin_registry()
    x = lacking.get("timeseries.forecasting.xgboost", "1.0.0")
    assert x.status == "missing_dependency" and "xgboost" in x.reason_unavailable
    assert lacking.get_model_adapter(x.id, x.version) is None
    assert lacking.get("ts.lag", "1.0.0").ready


def test_missing_linear_interpolation_is_not_offered():
    from mlworkbench.core.registry import builtin_registry

    spec = builtin_registry().get("ts.missing", "1.0.0")
    assert spec.parameter_schema["properties"]["method"]["enum"] == ["ffill", "drop"]
