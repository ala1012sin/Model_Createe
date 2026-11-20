import pytest

from conftest import ROOT, run
from mlworkbench.core.contracts import SafeProfile, TimeseriesGoal
from mlworkbench.core.errors import PolicyError, ProviderError, ProviderOutputError
from mlworkbench.providers.config import load_providers
from mlworkbench.providers.gateway import ProviderGateway, create_provider, extract_json
from mlworkbench.providers.mock import MockProvider
from mlworkbench.providers.stubs import OpenAICompatibleProvider, OpenAIProvider

PROFILE = SafeProfile(dataset_id="ds_example_timeseries", modality="timeseries",
                      summary={"rows": 100, "columns": [{"name": "sensor_1", "dtype": "numeric"}]})
GOAL = TimeseriesGoal(timestamp_col="timestamp", target_col="sensor_1", group_col="device_id", horizons=["15min"])


def _gw(reg, active="offline_test", allow=False):
    pf = load_providers(ROOT / "configs" / "providers.yaml")
    pf = pf.model_copy(update={"active_provider": active, "policy": pf.policy.model_copy(update={"allow_external_data": allow})})
    return ProviderGateway(pf, reg)


def test_factory_by_kind():
    pf = load_providers(ROOT / "configs" / "providers.yaml")
    assert isinstance(create_provider(pf.profiles["offline_test"]), MockProvider)
    assert isinstance(create_provider(pf.profiles["external_openai"]), OpenAIProvider)
    assert isinstance(create_provider(pf.profiles["local_ollama"]), OpenAICompatibleProvider)
    assert pf.active_provider == "offline_test"  # P0 기본값은 Mock


def test_example_providers_file_still_parses():
    pf = load_providers(ROOT / "examples" / "providers.yaml")
    assert pf.profiles["external_openai"].is_external and not pf.profiles["local_ollama"].is_external


def test_mock_proposal_is_deterministic_and_valid(ready_reg):
    gw = _gw(ready_reg)
    req = gw.build_request(safe_profile=PROFILE, task="forecasting", goal=GOAL)
    a = run(gw.generate_plan(req, provider=MockProvider()))
    b = run(gw.generate_plan(req, provider=MockProvider()))
    assert a == b and a.model.plugin_id == "timeseries.forecasting.xgboost"


def test_mock_with_no_ready_catalog_raises(builtin_reg):
    gw = _gw(builtin_reg)
    req = gw.build_request(safe_profile=PROFILE, task="forecasting", goal=GOAL)
    assert req.catalog == []
    with pytest.raises(ProviderError):
        run(gw.generate_plan(req, provider=MockProvider()))


def test_external_requires_consent_and_stub_does_not_call_network(ready_reg):
    gw = _gw(ready_reg, active="external_openai")
    req = gw.build_request(safe_profile=PROFILE, task="forecasting", goal=GOAL)
    with pytest.raises(PolicyError) as e:
        run(gw.generate_plan(req))
    assert e.value.code == "EXTERNAL_CONSENT_REQUIRED"
    with pytest.raises(ProviderError) as e2:
        run(gw.generate_plan(req, external_consent=True))
    assert e2.value.code == "PROVIDER_NOT_IMPLEMENTED"


@pytest.mark.parametrize("leak", [r"C:\Users\me\data\a.csv", "/home/me/data.csv", "sk-abcdefghijklmnopqrstuvwx"])
def test_sensitive_payload_blocked(ready_reg, leak):
    gw = _gw(ready_reg)
    prof = PROFILE.model_copy(update={"summary": {"note": leak}})
    req = gw.build_request(safe_profile=prof, task="forecasting", goal=GOAL)
    with pytest.raises(PolicyError) as e:
        run(gw.generate_plan(req, provider=MockProvider()))
    assert e.value.code == "SENSITIVE_PAYLOAD"


def test_json_fallback_and_retry(ready_reg):
    gw = _gw(ready_reg)
    req = gw.build_request(safe_profile=PROFILE, task="forecasting", goal=GOAL)
    assert run(gw.generate_plan(req, provider=MockProvider(scenario="fenced"))).task == "forecasting"
    m = MockProvider(scenario="bad_then_valid")
    run(gw.generate_plan(req, provider=m))
    assert m.calls == 2
    with pytest.raises(ProviderOutputError):
        run(gw.generate_plan(req, provider=MockProvider(scenario="bad_json")))


def test_extract_json_variants():
    assert extract_json('앞말 ```json\n{"a": 1}\n``` 뒷말') == {"a": 1}
    with pytest.raises(ProviderOutputError):
        extract_json("no json here")


def test_request_has_no_room_for_approval_token(ready_reg):
    from mlworkbench.providers.base import RecommendationRequest

    assert "approval_id" not in RecommendationRequest.model_fields
