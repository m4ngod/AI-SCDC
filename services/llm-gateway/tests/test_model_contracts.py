from ai_company_llm_gateway.models import (
    ModelCredentialRef,
    ModelProvider,
    ProviderType,
    ResolvedModelRoute,
    UsageRecord,
)


def test_usage_record_exposes_total_tokens() -> None:
    usage = UsageRecord(prompt_tokens=12, completion_tokens=8)

    assert usage.total_tokens == 20


def test_provider_config_supports_deepseek_without_network_behavior() -> None:
    provider = ModelProvider(
        name="deepseek-dev",
        provider_type=ProviderType.DEEPSEEK,
        base_url="https://api.deepseek.com",
    )

    payload = provider.model_dump()

    assert payload == {
        "name": "deepseek-dev",
        "provider_type": "deepseek",
        "base_url": "https://api.deepseek.com",
    }
    assert type(payload["provider_type"]) is str


def test_credential_ref_serializes_without_secret_material() -> None:
    credential = ModelCredentialRef(
        credential_id="model_credential_abc",
        provider_name="deepseek-dev",
        secret_last4="1234",
    )

    payload = credential.model_dump()

    assert payload == {
        "credential_id": "model_credential_abc",
        "provider_name": "deepseek-dev",
        "secret_last4": "1234",
    }
    assert "secret_value" not in payload
    assert "encrypted_secret" not in payload


def test_resolved_route_serializes_availability_metadata() -> None:
    route = ResolvedModelRoute(
        agent_role="planner",
        provider_name="fake",
        provider_type=ProviderType.FAKE,
        model_name="fake-planner",
        fallback_models=[],
        credential_required=False,
        credential_available=False,
        is_available=True,
        resolution_source="fallback_fake",
        route_id=None,
    )

    payload = route.model_dump()

    assert payload == {
        "agent_role": "planner",
        "provider_name": "fake",
        "provider_type": "fake",
        "model_name": "fake-planner",
        "fallback_models": [],
        "credential_required": False,
        "credential_available": False,
        "is_available": True,
        "resolution_source": "fallback_fake",
        "route_id": None,
    }
    assert type(payload["provider_type"]) is str
