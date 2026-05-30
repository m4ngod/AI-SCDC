from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProviderType(str, Enum):
    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai_compatible"
    DEEPSEEK = "deepseek"


class ModelProvider(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: str
    provider_type: ProviderType
    base_url: str | None = None


class ModelRoute(BaseModel):
    agent_role: str
    primary_model: str
    fallback_models: list[str] = Field(default_factory=list)


class ModelCredentialRef(BaseModel):
    credential_id: str
    provider_name: str
    secret_last4: str | None = None


class UsageRecord(BaseModel):
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ResolvedModelRoute(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    agent_role: str
    provider_name: str
    provider_type: ProviderType
    model_name: str
    fallback_models: list[str] = Field(default_factory=list)
    credential_required: bool
    credential_available: bool
    is_available: bool
    resolution_source: str
    route_id: str | None = None


class ProviderRequest(BaseModel):
    route: ModelRoute
    prompt: str


class ProviderResponse(BaseModel):
    provider_name: str
    model_name: str
    content: str
    usage: UsageRecord


class OpenAICompatibleProviderConfig(BaseModel):
    provider_name: str
    base_url: str
    default_headers: dict[str, str] = Field(default_factory=dict)


class ProviderGatewayError(RuntimeError):
    """Base error for provider gateway failures."""


class ProviderRequestError(ProviderGatewayError):
    """Raised when the provider request fails or returns a non-success status."""


class MalformedProviderResponseError(ProviderGatewayError):
    """Raised when a provider response does not match the expected shape."""


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ChatProviderRequest(BaseModel):
    model_name: str
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float = 0.2


class ChatProviderResponse(BaseModel):
    provider_name: str
    model_name: str
    content: str
    usage: UsageRecord
