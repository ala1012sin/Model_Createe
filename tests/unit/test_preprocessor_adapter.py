from dataclasses import dataclass

import pytest

from mlworkbench.core.contracts import DraftPlan, PluginSpec
from mlworkbench.core.errors import RegistryError
from mlworkbench.core.registry import PluginRegistry
from mlworkbench.core.validation import PlanValidator


@dataclass
class ExampleAdapter:
    spec: PluginSpec
    validation_calls: int = 0

    def validate(self, dataset, params):
        self.validation_calls += 1
        return ["값 5는 이 어댑터에서 사용할 수 없습니다"] if params["offset"] == 5 else []

    def fit(self, train_data, params):
        pytest.fail("Registry must not fit the adapter")

    def transform(self, data, state, params):
        pytest.fail("Registry must not transform any data")

    def preview(self, sample, params):
        pytest.fail("Registry must not preview any data")


def preprocessor_spec(**updates):
    fields = dict(
        id="ts.example_offset", version="1.0.0", kind="preprocessor", modality="timeseries",
        tasks=["forecasting"], display_name_ko="테스트 전처리", description_ko="어댑터 검증용",
        input_data_kind="table.timeseries", output_data_kind="table.timeseries", status="ready",
        source_library="Python", fit_scope="stateless", dependencies=["math"],
        parameter_schema={"type": "object", "properties": {"offset": {"type": "integer", "minimum": 0}},
                          "required": ["offset"]},
    )
    return PluginSpec(**(fields | updates))


def test_adapter_uses_existing_registry_and_catalog():
    registry = PluginRegistry()
    adapter = ExampleAdapter(preprocessor_spec())
    registry.register_preprocessor(adapter)
    assert registry.find(kind="preprocessor", modality="timeseries", task="forecasting", ready_only=True) == [adapter.spec]
    assert registry.get_preprocessor_adapter(adapter.spec.id, adapter.spec.version) is adapter
    assert registry.get_preprocessor_adapter(adapter.spec.id, "2.0.0") is None
    with pytest.raises(RegistryError, match="이미 등록된"):
        registry.register_preprocessor(ExampleAdapter(adapter.spec))
    assert registry.get_preprocessor_adapter(adapter.spec.id, adapter.spec.version) is adapter
    assert adapter.validation_calls == 0


def test_missing_dependency_keeps_metadata_but_excludes_adapter():
    registry = PluginRegistry()
    adapter = ExampleAdapter(preprocessor_spec(dependencies=["mlwb_add_unused_dependency_7842"]))
    registry.register_preprocessor(adapter)
    [spec] = registry.find(kind="preprocessor")
    assert spec.status == "missing_dependency"
    assert "mlwb_add_unused_dependency_7842" in spec.reason_unavailable
    assert not registry.find(ready_only=True)
    assert registry.get_preprocessor_adapter(spec.id, spec.version) is None


def test_adapter_requires_library_source_and_fit_scope():
    registry = PluginRegistry()
    with pytest.raises(RegistryError) as error:
        registry.register_preprocessor(ExampleAdapter(preprocessor_spec(fit_scope=None)))
    assert error.value.code == "INVALID_PREPROCESSOR"
    assert not registry.all()


@pytest.mark.parametrize("schema", [
    {"type": "array"},
    {"type": "object", "properties": {"offset": {"type": "float"}}},
    {"type": "object", "properties": {"offset": {"type": "integer", "minimum": "bad"}}},
])
def test_invalid_parameter_schema_does_not_register_adapter(schema):
    registry = PluginRegistry()
    with pytest.raises(RegistryError) as error:
        registry.register_preprocessor(ExampleAdapter(preprocessor_spec(parameter_schema=schema)))
    assert error.value.code == "INVALID_PARAMETER_SCHEMA"
    assert not registry.all()


def test_existing_plan_validator_checks_parameters_before_adapter(ready_reg, ts_manifest, ts_plan_dict):
    adapter = ExampleAdapter(preprocessor_spec())
    ready_reg.register_preprocessor(adapter)
    validator = PlanValidator(ready_reg)

    for params, message in [({"offset": -1}, "0 이상"), ({"offset": 5}, "값 5는")]:
        plan = DraftPlan.model_validate({**ts_plan_dict, "preprocessing": [
            {"plugin_id": adapter.spec.id, "plugin_version": adapter.spec.version, "params": params},
        ]})
        result, _ = validator.validate(plan, mode="final", dataset=ts_manifest)
        assert any(issue.code == "INVALID_PARAM" and message in issue.message_ko for issue in result.issues)
    assert adapter.validation_calls == 1
