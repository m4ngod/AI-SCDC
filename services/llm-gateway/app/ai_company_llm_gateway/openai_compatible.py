import httpx

from ai_company_llm_gateway.models import (
    ChatProviderRequest,
    ChatProviderResponse,
    MalformedProviderResponseError,
    ProviderRequestError,
    UsageRecord,
)


class OpenAICompatibleChatAdapter:
    def __init__(
        self,
        provider_name: str,
        base_url: str,
        api_key: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OpenAICompatibleChatAdapter":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def complete_chat(self, request: ChatProviderRequest) -> ChatProviderResponse:
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": request.model_name,
                    "messages": [
                        {"role": message.role, "content": message.content}
                        for message in request.messages
                    ],
                    "temperature": request.temperature,
                },
            )
        except httpx.HTTPError:
            raise ProviderRequestError("Provider request failed") from None

        if not 200 <= response.status_code < 300:
            raise ProviderRequestError(
                f"Provider request failed with status {response.status_code}"
            )

        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise MalformedProviderResponseError(
                "Provider response did not include chat content"
            ) from exc

        if not isinstance(content, str) or not content:
            raise MalformedProviderResponseError(
                "Provider response did not include chat content"
            )

        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            raise MalformedProviderResponseError(
                "Provider response did not include integer usage counts"
            )

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        if type(prompt_tokens) is not int or type(completion_tokens) is not int:
            raise MalformedProviderResponseError(
                "Provider response did not include integer usage counts"
            )

        return ChatProviderResponse(
            provider_name=self.provider_name,
            model_name=request.model_name,
            content=content,
            usage=UsageRecord(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
        )
