from typing import Protocol

from ai_company_llm_gateway.models import ProviderRequest, ProviderResponse, UsageRecord


class ProviderAdapter(Protocol):
    provider_name: str

    def complete(self, request: ProviderRequest) -> ProviderResponse:
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
