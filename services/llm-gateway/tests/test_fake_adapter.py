from ai_company_llm_gateway.adapters import FakeProviderAdapter
from ai_company_llm_gateway.models import ModelRoute, ProviderRequest


def test_fake_adapter_returns_deterministic_response() -> None:
    route = ModelRoute(
        agent_role="planner",
        primary_model="fake-planner",
        fallback_models=["fake-general"],
    )
    request = ProviderRequest(route=route, prompt="Create a TaskSpec")

    response = FakeProviderAdapter().complete(request)

    assert response.model_name == "fake-planner"
    assert response.content == "fake response for planner: Create a TaskSpec"
    assert response.usage.total_tokens == 8


def test_fake_adapter_exposes_provider_name() -> None:
    adapter = FakeProviderAdapter(provider_name="openai-compatible-dev")

    assert adapter.provider_name == "openai-compatible-dev"
