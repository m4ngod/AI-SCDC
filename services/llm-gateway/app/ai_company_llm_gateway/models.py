from pydantic import BaseModel, Field


class ModelProvider(BaseModel):
    name: str
    provider_type: str
    base_url: str | None = None


class ModelRoute(BaseModel):
    agent_role: str
    primary_model: str
    fallback_models: list[str] = Field(default_factory=list)


class ModelCredentialRef(BaseModel):
    credential_id: str
    provider_name: str


class UsageRecord(BaseModel):
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


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
