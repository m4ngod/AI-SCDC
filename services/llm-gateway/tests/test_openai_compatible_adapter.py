import json

import httpx
import pytest
from pydantic import ValidationError

from ai_company_llm_gateway.adapters import ChatProviderAdapter
from ai_company_llm_gateway.models import (
    ChatMessage,
    ChatProviderRequest,
    MalformedProviderResponseError,
    ProviderRequestError,
    UsageRecord,
)
from ai_company_llm_gateway.openai_compatible import OpenAICompatibleChatAdapter


def test_openai_compatible_adapter_sends_chat_completion_without_leaking_secret() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '[{"title":"Plan backend"}]'}},
                ],
                "usage": {"prompt_tokens": 21, "completion_tokens": 8},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleChatAdapter(
        provider_name="deepseek-dev",
        base_url="https://api.deepseek.com",
        api_key="sk-secret1234",
        client=client,
    )

    response = adapter.complete_chat(
        ChatProviderRequest(
            model_name="deepseek-chat",
            messages=[
                ChatMessage(role="system", content="Return JSON only."),
                ChatMessage(role="user", content="Build planner"),
            ],
            temperature=0.2,
        )
    )

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer sk-secret1234"
    assert captured["body"] == {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": "Build planner"},
        ],
        "temperature": 0.2,
    }
    assert response.provider_name == "deepseek-dev"
    assert response.model_name == "deepseek-chat"
    assert response.content == '[{"title":"Plan backend"}]'
    assert response.usage == UsageRecord(prompt_tokens=21, completion_tokens=8)
    assert "sk-secret1234" not in response.model_dump_json()


def test_openai_compatible_adapter_defaults_missing_usage_to_zero() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "[]"}}]},
            )
        )
    )
    adapter = OpenAICompatibleChatAdapter(
        provider_name="openai-compatible-dev",
        base_url="https://provider.example/v1/",
        api_key="sk-secret1234",
        client=client,
    )

    response = adapter.complete_chat(
        ChatProviderRequest(
            model_name="model-a",
            messages=[ChatMessage(role="user", content="Plan")],
        )
    )

    assert response.content == "[]"
    assert response.usage.total_tokens == 0


def test_openai_compatible_adapter_maps_non_2xx_to_provider_request_error() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(401, json={"error": "unauthorized"})
        )
    )
    adapter = OpenAICompatibleChatAdapter(
        provider_name="deepseek-dev",
        base_url="https://api.deepseek.com",
        api_key="sk-secret1234",
        client=client,
    )

    with pytest.raises(ProviderRequestError) as exc_info:
        adapter.complete_chat(
            ChatProviderRequest(
                model_name="deepseek-chat",
                messages=[ChatMessage(role="user", content="Plan")],
            )
        )

    assert "sk-secret1234" not in str(exc_info.value)
    assert "401" in str(exc_info.value)


def test_openai_compatible_adapter_maps_malformed_response() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"choices": []})
        )
    )
    adapter = OpenAICompatibleChatAdapter(
        provider_name="deepseek-dev",
        base_url="https://api.deepseek.com",
        api_key="sk-secret1234",
        client=client,
    )

    with pytest.raises(MalformedProviderResponseError):
        adapter.complete_chat(
            ChatProviderRequest(
                model_name="deepseek-chat",
                messages=[ChatMessage(role="user", content="Plan")],
            )
        )


def test_openai_compatible_adapter_maps_transport_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleChatAdapter(
        provider_name="deepseek-dev",
        base_url="https://api.deepseek.com",
        api_key="sk-secret1234",
        client=client,
    )

    with pytest.raises(ProviderRequestError) as exc_info:
        adapter.complete_chat(
            ChatProviderRequest(
                model_name="deepseek-chat",
                messages=[ChatMessage(role="user", content="Plan")],
            )
        )

    assert "sk-secret1234" not in str(exc_info.value)


def test_openai_compatible_adapter_suppresses_secret_bearing_transport_cause() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed for sk-secret1234")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleChatAdapter(
        provider_name="deepseek-dev",
        base_url="https://api.deepseek.com",
        api_key="sk-secret1234",
        client=client,
    )

    with pytest.raises(ProviderRequestError) as exc_info:
        adapter.complete_chat(
            ChatProviderRequest(
                model_name="deepseek-chat",
                messages=[ChatMessage(role="user", content="Plan")],
            )
        )

    assert "sk-secret1234" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_openai_compatible_adapter_context_manager_closes_owned_client() -> None:
    with OpenAICompatibleChatAdapter(
        provider_name="deepseek-dev",
        base_url="https://api.deepseek.com",
        api_key="sk-secret1234",
    ) as adapter:
        assert not adapter._client.is_closed

    assert adapter._client.is_closed


def test_openai_compatible_adapter_context_manager_does_not_close_injected_client() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "[]"}}]},
            )
        )
    )

    with OpenAICompatibleChatAdapter(
        provider_name="deepseek-dev",
        base_url="https://api.deepseek.com",
        api_key="sk-secret1234",
        client=client,
    ) as adapter:
        assert adapter is not None

    assert not client.is_closed
    client.close()


def test_openai_compatible_adapter_satisfies_chat_provider_adapter_protocol() -> None:
    adapter = OpenAICompatibleChatAdapter(
        provider_name="deepseek-dev",
        base_url="https://api.deepseek.com",
        api_key="sk-secret1234",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(500))
        ),
    )

    assert isinstance(adapter, ChatProviderAdapter)


def test_openai_compatible_adapter_passes_through_provider_specific_roles() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "[]"}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleChatAdapter(
        provider_name="openai-compatible-dev",
        base_url="https://provider.example/v1/",
        api_key="sk-secret1234",
        client=client,
    )

    adapter.complete_chat(
        ChatProviderRequest(
            model_name="model-a",
            messages=[ChatMessage(role="developer", content="Plan")],
        )
    )

    assert captured["body"] == {
        "model": "model-a",
        "messages": [{"role": "developer", "content": "Plan"}],
        "temperature": 0.2,
    }


def test_chat_provider_request_rejects_empty_messages() -> None:
    with pytest.raises(ValidationError):
        ChatProviderRequest(model_name="deepseek-chat", messages=[])
