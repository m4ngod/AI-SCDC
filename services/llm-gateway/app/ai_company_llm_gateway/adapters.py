from typing import Protocol, runtime_checkable

from ai_company_llm_gateway.models import (
    ChatProviderRequest,
    ChatProviderResponse,
    ProviderRequest,
    ProviderResponse,
    UsageRecord,
)


class ProviderAdapter(Protocol):
    provider_name: str

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        ...


@runtime_checkable
class ChatProviderAdapter(Protocol):
    provider_name: str

    def complete_chat(self, request: ChatProviderRequest) -> ChatProviderResponse:
        ...


class FakeProviderAdapter:
    def __init__(self, provider_name: str = "fake") -> None:
        self.provider_name = provider_name

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=request.route.primary_model,
            content=f"fake response for {request.route.agent_role}: {request.prompt}",
            usage=UsageRecord(prompt_tokens=3, completion_tokens=5),
        )
