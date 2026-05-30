# Phase 3 Real Planner Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first backend real model-backed planner path using the Phase 2 model routing and BYOK foundation while preserving human approval.

**Architecture:** Add a generic OpenAI-compatible gateway adapter and call it in-process from the API planner path. The API resolves the planner route, opens a development-only reversible credential, asks the model for JSON TaskSpec drafts, validates them, persists them for approval, records usage, and falls back to `FakePlanner` with an auditable reason when the model path is unavailable.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, Pydantic v2, httpx, pytest, SQLite-backed tests, pnpm workspace verification.

---

## File Structure

- Modify: `services/llm-gateway/pyproject.toml`
  - Add `httpx>=0.28.0` as a runtime dependency for provider HTTP calls.
- Modify: `services/llm-gateway/app/ai_company_llm_gateway/models.py`
  - Add chat message/request/response models and typed provider error classes.
  - Keep existing Phase 2 models backward-compatible.
- Create: `services/llm-gateway/app/ai_company_llm_gateway/openai_compatible.py`
  - Add `OpenAICompatibleChatAdapter`.
  - Use injectable `httpx.Client` or transport-friendly constructor for tests.
- Create: `services/llm-gateway/tests/test_openai_compatible_adapter.py`
  - Cover request shape, secret-free responses, usage parsing, and error mapping.
- Modify: `apps/api/app/ai_company_api/services/secret_vault.py`
  - Add `open()` to `SecretVault`.
  - Replace one-way hash storage with development-only reversible sealed storage.
- Modify: `apps/api/tests/test_model_settings_api.py`
  - Update vault tests to prove `open()` works and API redaction still holds.
- Modify: `apps/api/app/ai_company_api/models/entities.py`
  - Add planner run model metadata fields.
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
  - Add planner run model metadata to `PlannerRunRead`.
- Modify: `apps/api/app/ai_company_api/services/repository.py`
  - Return model metadata in planner read responses.
  - Use a new planner execution helper from `services/model_planner.py` for default planner creation.
- Modify: `apps/api/app/ai_company_api/services/usage_ledger.py`
  - Allow internal callers to append usage in the surrounding planner transaction.
- Create: `apps/api/app/ai_company_api/services/model_planner.py`
  - Own prompt construction, route readiness checks, credential opening, adapter invocation, output parsing, metadata, usage append, and fallback decisions.
- Create: `apps/api/tests/test_model_planner.py`
  - Unit-test model output parsing, prompt construction, model success, and fallback reasons.
- Modify: `apps/api/tests/test_planner_endpoints.py`
  - Cover API-level planner run metadata, model success path, usage ledger entry, fallback behavior, and approval compatibility.
- Modify: `README.md`
  - Add local DeepSeek/OpenAI-compatible smoke-test instructions without real keys.
- Modify: `docs/architecture.md`
  - Add Phase 3 boundary and move the real planner roadmap item to completed after implementation.

## Implementation Notes

- Do not add a desktop model settings UI.
- Do not auto-create tasks from model output.
- Do not run a standalone LLM gateway HTTP service.
- Do not commit or request real API keys.
- Do not log or return raw credential values.
- Keep `FakePlanner` as the no-config and failure fallback.
- Tests must not make external network calls.

### Task 1: OpenAI-Compatible Gateway Adapter

**Files:**
- Modify: `services/llm-gateway/pyproject.toml`
- Modify: `services/llm-gateway/app/ai_company_llm_gateway/models.py`
- Create: `services/llm-gateway/app/ai_company_llm_gateway/openai_compatible.py`
- Test: `services/llm-gateway/tests/test_openai_compatible_adapter.py`

- [ ] **Step 1: Add failing adapter tests**

Create `services/llm-gateway/tests/test_openai_compatible_adapter.py`:

```python
import json

import httpx
import pytest

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
```

- [ ] **Step 2: Run adapter tests and verify failure**

Run:

```bash
pytest services/llm-gateway/tests/test_openai_compatible_adapter.py -v
```

Expected: FAIL with import errors for `ChatMessage`, `ChatProviderRequest`, and `OpenAICompatibleChatAdapter`.

- [ ] **Step 3: Add gateway dependency**

Modify `services/llm-gateway/pyproject.toml` so runtime dependencies are:

```toml
dependencies = [
    "httpx>=0.28.0",
    "pydantic>=2.10.0",
]
```

- [ ] **Step 4: Add chat models and provider errors**

Append these definitions to `services/llm-gateway/app/ai_company_llm_gateway/models.py` after `OpenAICompatibleProviderConfig`:

```python
class ProviderGatewayError(RuntimeError):
    """Base error for provider gateway failures."""


class ProviderRequestError(ProviderGatewayError):
    """Raised when the provider request fails or returns a non-success status."""


class MalformedProviderResponseError(ProviderGatewayError):
    """Raised when a provider response does not match the expected shape."""


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatProviderRequest(BaseModel):
    model_name: str
    messages: list[ChatMessage]
    temperature: float = 0.2


class ChatProviderResponse(BaseModel):
    provider_name: str
    model_name: str
    content: str
    usage: UsageRecord
```

- [ ] **Step 5: Implement OpenAI-compatible adapter**

Create `services/llm-gateway/app/ai_company_llm_gateway/openai_compatible.py`:

```python
from typing import Any

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
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def complete_chat(self, request: ChatProviderRequest) -> ChatProviderResponse:
        payload = {
            "model": request.model_name,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
        }
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ProviderRequestError("Provider request failed") from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderRequestError(
                f"Provider request failed with status {response.status_code}",
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise MalformedProviderResponseError("Provider response is not JSON") from exc

        content = _extract_content(data)
        usage = _extract_usage(data)
        return ChatProviderResponse(
            provider_name=self.provider_name,
            model_name=request.model_name,
            content=content,
            usage=usage,
        )


def _extract_content(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise MalformedProviderResponseError(
            "Provider response is missing choices[0].message.content",
        ) from exc
    if not isinstance(content, str) or content == "":
        raise MalformedProviderResponseError("Provider response content is empty")
    return content


def _extract_usage(data: dict[str, Any]) -> UsageRecord:
    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        raise MalformedProviderResponseError("Provider usage token counts are invalid")
    return UsageRecord(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
```

- [ ] **Step 6: Run gateway tests and verify pass**

Run:

```bash
pytest services/llm-gateway/tests -v
```

Expected: PASS for fake adapter, model contracts, and OpenAI-compatible adapter tests.

- [ ] **Step 7: Reinstall gateway editable package**

Run:

```bash
python -m pip install -e "services/llm-gateway[test]"
py -3.11 -m pip install -e "services/llm-gateway[test]"
```

Expected: both commands complete successfully. If `python` and `py -3.11` point to the same interpreter, the second command can still safely run.

- [ ] **Step 8: Commit gateway adapter**

Run:

```bash
git add services/llm-gateway/pyproject.toml services/llm-gateway/app/ai_company_llm_gateway/models.py services/llm-gateway/app/ai_company_llm_gateway/openai_compatible.py services/llm-gateway/tests/test_openai_compatible_adapter.py
git commit -m "feat: add openai-compatible gateway adapter"
```

Expected: commit succeeds.

### Task 2: Reversible Development Vault and Planner Metadata

**Files:**
- Modify: `apps/api/app/ai_company_api/services/secret_vault.py`
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/services/repository.py`
- Test: `apps/api/tests/test_model_settings_api.py`
- Test: `apps/api/tests/test_planner_endpoints.py`

- [ ] **Step 1: Add failing vault and planner metadata tests**

Append to `apps/api/tests/test_model_settings_api.py`:

```python
def test_dev_secret_vault_opens_sealed_secret_without_plaintext_storage() -> None:
    vault = DevSecretVault()

    sealed = vault.seal("sk-example1234")

    assert sealed.encrypted_secret.startswith("dev-vault:v2:")
    assert "sk-example1234" not in sealed.encrypted_secret
    assert vault.open(sealed.encrypted_secret) == "sk-example1234"


def test_dev_secret_vault_rejects_invalid_payload() -> None:
    vault = DevSecretVault()

    with pytest.raises(ValueError):
        vault.open("dev-vault:v2:not-valid")
```

Modify `apps/api/tests/test_planner_endpoints.py::test_create_planner_run_creates_ordered_drafts_and_no_tasks` by adding these assertions after `assert planner_run["planner_kind"] == "fake"`:

```python
        assert planner_run["model_route_id"] is None
        assert planner_run["model_provider_name"] is None
        assert planner_run["model_name"] is None
        assert planner_run["fallback_reason"] is None
```

Modify `apps/api/tests/test_planner_endpoints.py::test_planner_run_routes_have_stable_openapi_response_schema` by adding:

```python
    run_schema = schema["components"]["schemas"]["PlannerRunRead"]
    assert "model_route_id" in run_schema["properties"]
    assert "model_provider_name" in run_schema["properties"]
    assert "model_name" in run_schema["properties"]
    assert "fallback_reason" in run_schema["properties"]
```

- [ ] **Step 2: Run metadata tests and verify failure**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py::test_dev_secret_vault_opens_sealed_secret_without_plaintext_storage apps/api/tests/test_model_settings_api.py::test_dev_secret_vault_rejects_invalid_payload apps/api/tests/test_planner_endpoints.py::test_create_planner_run_creates_ordered_drafts_and_no_tasks apps/api/tests/test_planner_endpoints.py::test_planner_run_routes_have_stable_openapi_response_schema -v
```

Expected: FAIL because `open()` and planner read metadata fields do not exist yet.

- [ ] **Step 3: Make DevSecretVault reversible**

Replace `apps/api/app/ai_company_api/services/secret_vault.py` with:

```python
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Protocol

from pydantic import BaseModel, Field


class SealedSecret(BaseModel):
    encrypted_secret: str = Field(min_length=1)
    secret_last4: str


class SecretVault(Protocol):
    def seal(self, secret_value: str) -> SealedSecret:
        ...

    def open(self, encrypted_secret: str) -> str:
        ...


class DevSecretVault:
    _prefix = "dev-vault:v2:"

    def seal(self, secret_value: str) -> SealedSecret:
        encoded = urlsafe_b64encode(secret_value.encode("utf-8")).decode("ascii")
        return SealedSecret(
            encrypted_secret=f"{self._prefix}{encoded}",
            secret_last4=secret_value[-4:] if len(secret_value) >= 4 else secret_value,
        )

    def open(self, encrypted_secret: str) -> str:
        if not encrypted_secret.startswith(self._prefix):
            raise ValueError("Unsupported dev vault payload")
        encoded = encrypted_secret.removeprefix(self._prefix)
        try:
            return urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid dev vault payload") from exc
```

- [ ] **Step 4: Add planner run metadata fields**

Modify `apps/api/app/ai_company_api/models/entities.py` in `class PlannerRun` after `planner_kind`:

```python
    model_route_id: str | None = Field(default=None, index=True)
    model_provider_name: str | None = None
    model_name: str | None = None
    fallback_reason: str | None = None
```

Modify `apps/api/app/ai_company_api/schemas/api.py` in `class PlannerRunRead` after `planner_kind`:

```python
    model_route_id: str | None
    model_provider_name: str | None
    model_name: str | None
    fallback_reason: str | None
```

Modify `_planner_run_read()` in `apps/api/app/ai_company_api/services/repository.py` so `PlannerRunRead(...)` includes:

```python
        model_route_id=planner_run.model_route_id,
        model_provider_name=planner_run.model_provider_name,
        model_name=planner_run.model_name,
        fallback_reason=planner_run.fallback_reason,
```

Keep the existing `planner_kind=planner_run.planner_kind` line.

- [ ] **Step 5: Run metadata tests and verify pass**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py::test_dev_secret_vault_opens_sealed_secret_without_plaintext_storage apps/api/tests/test_model_settings_api.py::test_dev_secret_vault_rejects_invalid_payload apps/api/tests/test_planner_endpoints.py::test_create_planner_run_creates_ordered_drafts_and_no_tasks apps/api/tests/test_planner_endpoints.py::test_planner_run_routes_have_stable_openapi_response_schema -v
```

Expected: PASS.

- [ ] **Step 6: Run affected suites**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py apps/api/tests/test_planner_endpoints.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit vault and metadata**

Run:

```bash
git add apps/api/app/ai_company_api/services/secret_vault.py apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/repository.py apps/api/tests/test_model_settings_api.py apps/api/tests/test_planner_endpoints.py
git commit -m "feat: add reversible dev vault and planner metadata"
```

Expected: commit succeeds.

### Task 3: Model Planner Prompt and Output Parser

**Files:**
- Create: `apps/api/app/ai_company_api/services/model_planner.py`
- Test: `apps/api/tests/test_model_planner.py`

- [ ] **Step 1: Add failing parser and prompt tests**

Create `apps/api/tests/test_model_planner.py`:

```python
import pytest

from ai_company_api.services.model_planner import (
    ModelPlannerError,
    build_planner_messages,
    parse_task_spec_drafts,
)


def test_build_planner_messages_instructs_json_only() -> None:
    messages = build_planner_messages(
        goal="Build real planner",
        project_name="Demo Project",
    )

    assert [message.role for message in messages] == ["system", "user"]
    assert "JSON" in messages[0].content
    assert "role_required" in messages[0].content
    assert "frontend" in messages[0].content
    assert "Build real planner" in messages[1].content
    assert "Demo Project" in messages[1].content


def test_parse_task_spec_drafts_accepts_valid_json_array() -> None:
    drafts = parse_task_spec_drafts(
        """
        [
          {
            "title": "Implement model planner",
            "role_required": "backend",
            "objective": "Call a configured model route for planner drafts.",
            "acceptance_criteria": ["Model drafts are persisted."],
            "allowed_paths": ["apps/api/**"],
            "required_tests": ["pytest apps/api/tests/test_model_planner.py -v"],
            "risk_level": "medium"
          }
        ]
        """
    )

    assert len(drafts) == 1
    assert drafts[0].title == "Implement model planner"
    assert drafts[0].role_required.value == "backend"
    assert drafts[0].risk_level.value == "medium"


def test_parse_task_spec_drafts_unwraps_markdown_json_fence() -> None:
    drafts = parse_task_spec_drafts(
        """```json
        [
          {
            "title": "Review planner output",
            "role_required": "reviewer",
            "objective": "Check generated drafts.",
            "acceptance_criteria": ["Review is complete."],
            "allowed_paths": ["apps/api/**"],
            "required_tests": [],
            "risk_level": "low"
          }
        ]
        ```"""
    )

    assert drafts[0].role_required.value == "reviewer"


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "{}",
        "[]",
        '[{"title": "Missing fields"}]',
        """[
          {
            "title": "Bad role",
            "role_required": "sales",
            "objective": "No.",
            "acceptance_criteria": ["Rejected."],
            "allowed_paths": ["apps/api/**"],
            "required_tests": [],
            "risk_level": "medium"
          }
        ]""",
    ],
)
def test_parse_task_spec_drafts_rejects_invalid_output(content: str) -> None:
    with pytest.raises(ModelPlannerError):
        parse_task_spec_drafts(content)
```

- [ ] **Step 2: Run parser tests and verify failure**

Run:

```bash
pytest apps/api/tests/test_model_planner.py -v
```

Expected: FAIL with import error for `ai_company_api.services.model_planner`.

- [ ] **Step 3: Implement model planner parser and prompt helpers**

Create `apps/api/app/ai_company_api/services/model_planner.py`:

```python
import json
from dataclasses import dataclass

from ai_company_api.models.entities import Project
from ai_company_api.services.planner import TaskSpecDraft
from ai_company_llm_gateway.models import ChatMessage
from pydantic import ValidationError


class ModelPlannerError(RuntimeError):
    """Raised when model planner output cannot be used."""


@dataclass(frozen=True)
class PlannerExecutionResult:
    task_specs: list[TaskSpecDraft]
    planner_kind: str
    model_route_id: str | None = None
    model_provider_name: str | None = None
    model_name: str | None = None
    fallback_reason: str | None = None


def build_planner_messages(goal: str, project_name: str) -> list[ChatMessage]:
    return [
        ChatMessage(
            role="system",
            content=(
                "You are the planner for AI Software Company Desktop Console. "
                "Return JSON only: an array of task draft objects. Each object "
                "must include title, role_required, objective, acceptance_criteria, "
                "allowed_paths, required_tests, and risk_level. role_required must "
                "be one of planner, frontend, backend, reviewer, debugger, security, "
                "product, documentation. risk_level must be low, medium, or high. "
                "Do not include Markdown or explanatory text."
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                f"Project: {project_name}\n"
                f"Goal: {goal}\n"
                "Create 2 to 5 implementation task drafts for human approval."
            ),
        ),
    ]


def parse_task_spec_drafts(content: str) -> list[TaskSpecDraft]:
    raw_json = _strip_json_fence(content.strip())
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ModelPlannerError("Model output is not valid JSON") from exc
    if not isinstance(payload, list) or len(payload) == 0:
        raise ModelPlannerError("Model output must be a non-empty JSON array")
    try:
        return [TaskSpecDraft.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise ModelPlannerError("Model output does not match TaskSpec schema") from exc


def _strip_json_fence(content: str) -> str:
    if content.startswith("```json") and content.endswith("```"):
        return content.removeprefix("```json").removesuffix("```").strip()
    if content.startswith("```") and content.endswith("```"):
        return content.removeprefix("```").removesuffix("```").strip()
    return content


def project_name(project: Project) -> str:
    return project.name
```

Later tasks will extend this module with route execution and fallback behavior. Keep this task focused on prompt and parsing.

- [ ] **Step 4: Run parser tests and verify pass**

Run:

```bash
pytest apps/api/tests/test_model_planner.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit parser and prompt helper**

Run:

```bash
git add apps/api/app/ai_company_api/services/model_planner.py apps/api/tests/test_model_planner.py
git commit -m "feat: add model planner prompt parser"
```

Expected: commit succeeds.

### Task 4: Model Planner Success Path and Usage Ledger

**Files:**
- Modify: `apps/api/app/ai_company_api/services/model_planner.py`
- Modify: `apps/api/app/ai_company_api/services/repository.py`
- Modify: `apps/api/app/ai_company_api/services/usage_ledger.py`
- Test: `apps/api/tests/test_model_planner.py`
- Test: `apps/api/tests/test_planner_endpoints.py`

- [ ] **Step 1: Add failing service-level success test**

Append to `apps/api/tests/test_model_planner.py`:

```python
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.models.entities import (
    ModelCredential,
    ModelProvider,
    ModelProviderType,
    ModelRoute,
    PlannerRun,
    Project,
)
from ai_company_api.services.model_planner import create_model_planner_result
from ai_company_api.services.secret_vault import DevSecretVault
from ai_company_api.services.usage_ledger import list_usage_ledger_entries
from ai_company_llm_gateway.models import (
    ChatProviderResponse,
    ProviderRequestError,
    UsageRecord,
)


class RecordingChatAdapter:
    def __init__(self, response: ChatProviderResponse) -> None:
        self.response = response
        self.requests = []

    def complete_chat(self, request):
        self.requests.append(request)
        return self.response


def build_session() -> Session:
    engine = build_engine("sqlite://")
    init_db(engine)
    return Session(engine)


def create_planner_route(session: Session) -> tuple[Project, ModelRoute]:
    vault = DevSecretVault()
    sealed = vault.seal("sk-example1234")
    project = Project(name="Demo Project")
    provider = ModelProvider(
        name="deepseek-dev",
        provider_type=ModelProviderType.DEEPSEEK,
        base_url="https://api.deepseek.com",
    )
    credential = ModelCredential(
        provider_id=provider.id,
        display_name="DeepSeek key",
        secret_last4=sealed.secret_last4,
        encrypted_secret=sealed.encrypted_secret,
    )
    route = ModelRoute(
        agent_role="planner",
        provider_id=provider.id,
        credential_id=credential.id,
        model_name="deepseek-chat",
    )
    session.add(project)
    session.add(provider)
    session.add(credential)
    session.add(route)
    session.commit()
    session.refresh(project)
    session.refresh(route)
    return project, route


def test_create_model_planner_result_uses_configured_route_and_logs_usage() -> None:
    with build_session() as session:
        project, route = create_planner_route(session)
        planner_run = PlannerRun(
            id="planner_run_manual",
            project_id=project.id,
            goal="Build real planner",
        )
        session.add(planner_run)
        session.flush()
        adapter = RecordingChatAdapter(
            ChatProviderResponse(
                provider_name="deepseek-dev",
                model_name="deepseek-chat",
                content="""
                [
                  {
                    "title": "Implement API planner integration",
                    "role_required": "backend",
                    "objective": "Use configured route for planner drafts.",
                    "acceptance_criteria": ["Model drafts are persisted."],
                    "allowed_paths": ["apps/api/**"],
                    "required_tests": ["pytest apps/api/tests/test_model_planner.py -v"],
                    "risk_level": "medium"
                  }
                ]
                """,
                usage=UsageRecord(prompt_tokens=31, completion_tokens=17),
            )
        )

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build real planner",
            planner_run_id=planner_run.id,
            adapter_factory=lambda **_kwargs: adapter,
        )
        usage_entries = list_usage_ledger_entries(
            session,
            planner_run_id="planner_run_manual",
        )

    assert result.planner_kind == "model"
    assert result.model_route_id == route.id
    assert result.model_provider_name == "deepseek-dev"
    assert result.model_name == "deepseek-chat"
    assert result.fallback_reason is None
    assert result.task_specs[0].title == "Implement API planner integration"
    assert adapter.requests[0].model_name == "deepseek-chat"
    assert usage_entries[0].provider_name == "deepseek-dev"
    assert usage_entries[0].model_name == "deepseek-chat"
    assert usage_entries[0].prompt_tokens == 31
    assert usage_entries[0].completion_tokens == 17
    assert usage_entries[0].total_tokens == 48
```

- [ ] **Step 2: Run service-level success test and verify failure**

Run:

```bash
pytest apps/api/tests/test_model_planner.py::test_create_model_planner_result_uses_configured_route_and_logs_usage -v
```

Expected: FAIL because `create_model_planner_result` does not exist.

- [ ] **Step 3: Extend model planner with route execution**

Modify `apps/api/app/ai_company_api/services/usage_ledger.py` so `append_usage_ledger_entry()` accepts an optional `commit` flag:

```python
def append_usage_ledger_entry(
    session: Session,
    data: UsageLedgerCreate,
    commit: bool = True,
) -> UsageLedgerRead:
    _validate_project(session, data.project_id)
    task = _validate_task(session, data.task_id, data.project_id)
    project_id = data.project_id
    if project_id is None and task is not None:
        project_id = task.project_id

    planner_run = _validate_planner_run(session, data.planner_run_id, project_id)
    if project_id is None and planner_run is not None:
        project_id = planner_run.project_id

    entry = UsageLedgerEntry(
        project_id=project_id,
        task_id=data.task_id,
        planner_run_id=data.planner_run_id,
        usage_type=UsageType(data.usage_type.value),
        provider_name=data.provider_name,
        model_name=data.model_name,
        prompt_tokens=data.prompt_tokens,
        completion_tokens=data.completion_tokens,
        total_tokens=data.prompt_tokens + data.completion_tokens,
        unit_price_cents=data.unit_price_cents,
        amount_cents=data.amount_cents,
        raw_usage_json=data.raw_usage_json,
    )
    session.add(entry)
    if commit:
        session.commit()
        session.refresh(entry)
    else:
        session.flush()
    return _usage_read(entry)
```

Append to `apps/api/app/ai_company_api/services/model_planner.py`:

```python
from collections.abc import Callable

from sqlmodel import Session

from ai_company_api.models.entities import (
    ModelCredential,
    ModelCredentialStatus,
    ModelProvider,
    ModelProviderStatus,
    ModelProviderType,
    ModelRoute,
    Project,
)
from ai_company_api.schemas.api import AgentRole, UsageLedgerCreate
from ai_company_api.services.model_settings import resolve_model_route
from ai_company_api.services.secret_vault import DevSecretVault, SecretVault
from ai_company_api.services.usage_ledger import append_usage_ledger_entry
from ai_company_llm_gateway.openai_compatible import OpenAICompatibleChatAdapter


AdapterFactory = Callable[..., object]


def create_model_planner_result(
    session: Session,
    project: Project,
    goal: str,
    planner_run_id: str,
    adapter_factory: AdapterFactory = OpenAICompatibleChatAdapter,
    vault: SecretVault | None = None,
) -> PlannerExecutionResult:
    resolved = resolve_model_route(session, AgentRole.PLANNER)
    if resolved.resolution_source == "fallback_fake":
        return _fake_result("no_configured_route")
    if resolved.provider_type == ModelProviderType.FAKE.value:
        return _fake_result(None)
    if not resolved.is_available or resolved.route_id is None:
        return _fake_result(_unavailable_reason(resolved))

    route = session.get(ModelRoute, resolved.route_id)
    if route is None:
        return _fake_result("no_configured_route")
    provider = session.get(ModelProvider, route.provider_id)
    if provider is None or _enum_value(provider.status) != ModelProviderStatus.ACTIVE.value:
        return _fake_result("provider_unavailable")
    credential = _active_credential(session, route.credential_id)
    if credential is None:
        return _fake_result("credential_unavailable")

    try:
        secret = (vault or DevSecretVault()).open(credential.encrypted_secret)
    except ValueError:
        return _fake_result("credential_unavailable")

    try:
        adapter = adapter_factory(
            provider_name=provider.name,
            base_url=provider.base_url or "",
            api_key=secret,
        )
        response = adapter.complete_chat(
            ChatProviderRequest(
                model_name=route.model_name,
                messages=build_planner_messages(
                    goal=goal,
                    project_name=project_name(project),
                ),
            )
        )
    except ProviderGatewayError:
        return _fake_result("provider_request_failed")

    try:
        task_specs = parse_task_spec_drafts(response.content)
    except ModelPlannerError:
        return _fake_result("invalid_model_output")

    try:
        append_usage_ledger_entry(
            session,
            UsageLedgerCreate(
                project_id=project.id,
                planner_run_id=planner_run_id,
                provider_name=response.provider_name,
                model_name=response.model_name,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                raw_usage_json={
                    "source": "model_planner",
                    "route_id": route.id,
                },
            ),
            commit=False,
        )
    except Exception:
        pass

    return PlannerExecutionResult(
        task_specs=task_specs,
        planner_kind="model",
        model_route_id=route.id,
        model_provider_name=provider.name,
        model_name=route.model_name,
        fallback_reason=None,
    )


def _fake_result(fallback_reason: str | None) -> PlannerExecutionResult:
    return PlannerExecutionResult(
        task_specs=[],
        planner_kind="fake" if fallback_reason is None else "model_fallback_fake",
        fallback_reason=fallback_reason,
    )


def _active_credential(
    session: Session,
    credential_id: str | None,
) -> ModelCredential | None:
    if credential_id is None:
        return None
    credential = session.get(ModelCredential, credential_id)
    if credential is None:
        return None
    if _enum_value(credential.status) != ModelCredentialStatus.ACTIVE.value:
        return None
    return credential


def _unavailable_reason(resolved) -> str:
    if resolved.provider_type != ModelProviderType.FAKE.value and not resolved.credential_available:
        return "credential_unavailable"
    return "provider_unavailable"


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)
```

Also add this import near the top of `model_planner.py` with the other gateway imports:

```python
from ai_company_llm_gateway.models import ChatProviderRequest, ProviderGatewayError
```

- [ ] **Step 4: Run service-level success test and verify pass**

Run:

```bash
pytest apps/api/tests/test_model_planner.py::test_create_model_planner_result_uses_configured_route_and_logs_usage -v
```

Expected: PASS.

- [ ] **Step 5: Add failing API integration success test**

Append to `apps/api/tests/test_planner_endpoints.py`:

```python
from ai_company_api.models.entities import (
    ModelCredential,
    ModelProvider,
    ModelProviderType,
    ModelRoute,
)
from ai_company_api.services.secret_vault import DevSecretVault
from ai_company_llm_gateway.models import ChatProviderResponse, UsageRecord


class PlannerEndpointAdapter:
    def __init__(self, response: ChatProviderResponse) -> None:
        self.response = response

    def complete_chat(self, _request):
        return self.response


def test_create_planner_run_uses_model_route_and_logs_usage(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "model-planner.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)

    with Session(engine) as session:
        project = Project(name="Demo Project")
        provider = ModelProvider(
            name="deepseek-dev",
            provider_type=ModelProviderType.DEEPSEEK,
            base_url="https://api.deepseek.com",
        )
        sealed = DevSecretVault().seal("sk-example1234")
        credential = ModelCredential(
            provider_id=provider.id,
            display_name="DeepSeek key",
            secret_last4=sealed.secret_last4,
            encrypted_secret=sealed.encrypted_secret,
        )
        route = ModelRoute(
            agent_role="planner",
            provider_id=provider.id,
            credential_id=credential.id,
            model_name="deepseek-chat",
        )
        session.add(project)
        session.add(provider)
        session.add(credential)
        session.add(route)
        session.commit()
        project_id = project.id
        route_id = route.id

    def adapter_factory(**_kwargs):
        return PlannerEndpointAdapter(
            ChatProviderResponse(
                provider_name="deepseek-dev",
                model_name="deepseek-chat",
                content="""
                [
                  {
                    "title": "Implement model planner endpoint",
                    "role_required": "backend",
                    "objective": "Wire planner endpoint to the model route.",
                    "acceptance_criteria": ["Planner run uses model metadata."],
                    "allowed_paths": ["apps/api/**"],
                    "required_tests": ["pytest apps/api/tests/test_planner_endpoints.py -v"],
                    "risk_level": "medium"
                  }
                ]
                """,
                usage=UsageRecord(prompt_tokens=11, completion_tokens=7),
            )
        )

    monkeypatch.setattr(
        "ai_company_api.services.repository.MODEL_PLANNER_ADAPTER_FACTORY",
        adapter_factory,
    )

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            f"/projects/{project_id}/planner-runs",
            json={"goal": "Build model planner"},
        )
        usage_response = client.get("/usage-ledger")

    assert response.status_code == 201
    planner_run = response.json()
    assert planner_run["planner_kind"] == "model"
    assert planner_run["model_route_id"] == route_id
    assert planner_run["model_provider_name"] == "deepseek-dev"
    assert planner_run["model_name"] == "deepseek-chat"
    assert planner_run["fallback_reason"] is None
    assert planner_run["draft_count"] == 1
    assert planner_run["drafts"][0]["title"] == "Implement model planner endpoint"
    usage = usage_response.json()
    assert len(usage) == 1
    assert usage[0]["planner_run_id"] == planner_run["id"]
    assert usage[0]["total_tokens"] == 18
```

- [ ] **Step 6: Run API success test and verify failure**

Run:

```bash
pytest apps/api/tests/test_planner_endpoints.py::test_create_planner_run_uses_model_route_and_logs_usage -v
```

Expected: FAIL because `repository.MODEL_PLANNER_ADAPTER_FACTORY` and default model planner integration do not exist.

- [ ] **Step 7: Integrate repository planner creation with model planner**

Modify imports in `apps/api/app/ai_company_api/services/repository.py`:

```python
from ai_company_api.services.model_planner import (
    PlannerExecutionResult,
    create_model_planner_result,
)
from ai_company_llm_gateway.openai_compatible import OpenAICompatibleChatAdapter
```

Add this module constant after imports:

```python
MODEL_PLANNER_ADAPTER_FACTORY = OpenAICompatibleChatAdapter
```

Add this helper before `create_planner_run()`:

```python
def _create_default_planner_result(
    session: Session,
    project: Project,
    goal: str,
    planner_run_id: str,
) -> PlannerExecutionResult:
    result = create_model_planner_result(
        session,
        project=project,
        goal=goal,
        planner_run_id=planner_run_id,
        adapter_factory=MODEL_PLANNER_ADAPTER_FACTORY,
    )
    if result.task_specs:
        return result
    fake_specs = FakePlanner().plan(project_id=project.id, goal=goal)
    return PlannerExecutionResult(
        task_specs=fake_specs,
        planner_kind=result.planner_kind,
        model_route_id=result.model_route_id,
        model_provider_name=result.model_provider_name,
        model_name=result.model_name,
        fallback_reason=result.fallback_reason,
    )
```

Modify `create_planner_run()`:

1. Change `get_project(session, project_id)` to:

```python
    project = get_project(session, project_id)
```

2. Replace:

```python
    planner_service = planner or FakePlanner()
    task_specs = planner_service.plan(project_id=project_id, goal=data.goal)
    planner_run = PlannerRun(
        project_id=project_id,
        conversation_id=data.conversation_id,
        goal=data.goal,
        status=PlannerRunStatus.DRAFTED,
        planner_kind="fake",
        draft_count=len(task_specs),
    )
    session.add(planner_run)
    session.flush()
```

with:

```python
    planner_run = PlannerRun(
        project_id=project_id,
        conversation_id=data.conversation_id,
        goal=data.goal,
        status=PlannerRunStatus.DRAFTED,
        planner_kind="fake",
        draft_count=0,
    )
    session.add(planner_run)
    session.flush()

    if planner is not None:
        task_specs = planner.plan(project_id=project_id, goal=data.goal)
        planner_result = PlannerExecutionResult(
            task_specs=task_specs,
            planner_kind="fake",
        )
    else:
        planner_result = _create_default_planner_result(
            session,
            project=project,
            goal=data.goal,
            planner_run_id=planner_run.id,
        )
        task_specs = planner_result.task_specs

    planner_run.planner_kind = planner_result.planner_kind
    planner_run.model_route_id = planner_result.model_route_id
    planner_run.model_provider_name = planner_result.model_provider_name
    planner_run.model_name = planner_result.model_name
    planner_run.fallback_reason = planner_result.fallback_reason
    planner_run.draft_count = len(task_specs)
    session.add(planner_run)
```

Keep the existing draft persistence loop after this block.

Update existing no-route planner assertions in `apps/api/tests/test_planner_endpoints.py::test_create_planner_run_creates_ordered_drafts_and_no_tasks` after default model planner integration:

```python
        assert planner_run["planner_kind"] == "model_fallback_fake"
        assert planner_run["model_route_id"] is None
        assert planner_run["model_provider_name"] is None
        assert planner_run["model_name"] is None
        assert planner_run["fallback_reason"] == "no_configured_route"
```

Remove the earlier assertion in that test that expected `planner_kind == "fake"` for the default no-route API path. Tests that call `create_planner_run(..., planner=FakePlanner())` or pass another explicit planner should continue to expect `planner_kind == "fake"`.

- [ ] **Step 8: Run API success test and affected suites**

Run:

```bash
pytest apps/api/tests/test_planner_endpoints.py::test_create_planner_run_uses_model_route_and_logs_usage -v
pytest apps/api/tests/test_model_planner.py apps/api/tests/test_planner_endpoints.py apps/api/tests/test_usage_ledger_api.py -v
```

Expected: both commands PASS.

- [ ] **Step 9: Commit model planner success path**

Run:

```bash
git add apps/api/app/ai_company_api/services/model_planner.py apps/api/app/ai_company_api/services/repository.py apps/api/app/ai_company_api/services/usage_ledger.py apps/api/tests/test_model_planner.py apps/api/tests/test_planner_endpoints.py
git commit -m "feat: use model route for planner runs"
```

Expected: commit succeeds.

### Task 5: Fallback Reasons and Approval Compatibility

**Files:**
- Modify: `apps/api/app/ai_company_api/services/model_planner.py`
- Modify: `apps/api/tests/test_model_planner.py`
- Modify: `apps/api/tests/test_planner_endpoints.py`

- [ ] **Step 1: Add failing fallback tests**

Append to `apps/api/tests/test_model_planner.py`:

```python
from ai_company_api.models.entities import (
    ModelCredentialStatus,
    ModelProviderStatus,
)


def test_model_planner_falls_back_when_no_route_is_configured() -> None:
    with build_session() as session:
        project = Project(name="Demo Project")
        session.add(project)
        session.commit()

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build planner",
            planner_run_id="planner_run_manual",
            adapter_factory=lambda **_kwargs: object(),
        )

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "no_configured_route"
    assert result.task_specs == []


def test_model_planner_falls_back_when_credential_is_deleted() -> None:
    with build_session() as session:
        project, route = create_planner_route(session)
        credential = session.get(ModelCredential, route.credential_id)
        assert credential is not None
        credential.status = ModelCredentialStatus.DELETED
        session.add(credential)
        session.commit()

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build planner",
            planner_run_id="planner_run_manual",
            adapter_factory=lambda **_kwargs: object(),
        )

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "credential_unavailable"
    assert result.task_specs == []


def test_model_planner_falls_back_when_provider_is_disabled() -> None:
    with build_session() as session:
        project, route = create_planner_route(session)
        provider = session.get(ModelProvider, route.provider_id)
        assert provider is not None
        provider.status = ModelProviderStatus.DISABLED
        session.add(provider)
        session.commit()

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build planner",
            planner_run_id="planner_run_manual",
            adapter_factory=lambda **_kwargs: object(),
        )

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "provider_unavailable"
    assert result.task_specs == []


def test_model_planner_falls_back_when_provider_request_fails() -> None:
    class FailingAdapter:
        def complete_chat(self, _request):
            raise ProviderRequestError("provider down")

    with build_session() as session:
        project, _route = create_planner_route(session)

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build planner",
            planner_run_id="planner_run_manual",
            adapter_factory=lambda **_kwargs: FailingAdapter(),
        )
        usage = list_usage_ledger_entries(session, planner_run_id="planner_run_manual")

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "provider_request_failed"
    assert result.task_specs == []
    assert usage == []


def test_model_planner_falls_back_when_model_output_is_invalid() -> None:
    with build_session() as session:
        project, _route = create_planner_route(session)
        adapter = RecordingChatAdapter(
            ChatProviderResponse(
                provider_name="deepseek-dev",
                model_name="deepseek-chat",
                content="not json",
                usage=UsageRecord(prompt_tokens=3, completion_tokens=4),
            )
        )

        result = create_model_planner_result(
            session,
            project=project,
            goal="Build planner",
            planner_run_id="planner_run_manual",
            adapter_factory=lambda **_kwargs: adapter,
        )
        usage = list_usage_ledger_entries(session, planner_run_id="planner_run_manual")

    assert result.planner_kind == "model_fallback_fake"
    assert result.fallback_reason == "invalid_model_output"
    assert result.task_specs == []
    assert usage == []
```

Append to `apps/api/tests/test_planner_endpoints.py`:

```python
def test_create_planner_run_falls_back_to_fake_and_can_still_be_approved() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        planner_response = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build fallback planner"},
        )
        planner_run = planner_response.json()
        approval_response = client.post(f"/planner-runs/{planner_run['id']}/approve")

    assert planner_response.status_code == 201
    assert planner_run["planner_kind"] == "model_fallback_fake"
    assert planner_run["fallback_reason"] == "no_configured_route"
    assert planner_run["draft_count"] == 2
    assert approval_response.status_code == 200
    assert len(approval_response.json()["created_tasks"]) == 2
```

- [ ] **Step 2: Run fallback tests and verify failure if needed**

Run:

```bash
pytest apps/api/tests/test_model_planner.py::test_model_planner_falls_back_when_no_route_is_configured apps/api/tests/test_model_planner.py::test_model_planner_falls_back_when_credential_is_deleted apps/api/tests/test_model_planner.py::test_model_planner_falls_back_when_provider_is_disabled apps/api/tests/test_model_planner.py::test_model_planner_falls_back_when_provider_request_fails apps/api/tests/test_model_planner.py::test_model_planner_falls_back_when_model_output_is_invalid apps/api/tests/test_planner_endpoints.py::test_create_planner_run_falls_back_to_fake_and_can_still_be_approved -v
```

Expected: tests may already pass from Task 4 implementation except provider-disabled classification. If provider-disabled returns `credential_unavailable`, continue to Step 3.

- [ ] **Step 3: Ensure provider-disabled fallback reason is precise**

In `apps/api/app/ai_company_api/services/model_planner.py`, adjust `_unavailable_reason()` to:

```python
def _unavailable_reason(resolved) -> str:
    if resolved.provider_type != ModelProviderType.FAKE.value and not resolved.credential_available:
        return "credential_unavailable"
    return "provider_unavailable"
```

If `resolve_model_route()` cannot distinguish disabled provider from missing credential, change `create_model_planner_result()` to load `route` and `provider` before returning on `not resolved.is_available`:

```python
    if not resolved.is_available or resolved.route_id is None:
        if resolved.route_id is not None:
            route = session.get(ModelRoute, resolved.route_id)
            provider = session.get(ModelProvider, route.provider_id) if route else None
            if provider is not None and _enum_value(provider.status) != ModelProviderStatus.ACTIVE.value:
                return _fake_result("provider_unavailable")
        return _fake_result(_unavailable_reason(resolved))
```

- [ ] **Step 4: Run fallback and approval tests**

Run:

```bash
pytest apps/api/tests/test_model_planner.py apps/api/tests/test_planner_endpoints.py -v
```

Expected: PASS.

- [ ] **Step 5: Run broader API tests**

Run:

```bash
pytest apps/api/tests -v
```

Expected: PASS for all API tests.

- [ ] **Step 6: Commit fallback behavior**

Run:

```bash
git add apps/api/app/ai_company_api/services/model_planner.py apps/api/tests/test_model_planner.py apps/api/tests/test_planner_endpoints.py
git commit -m "test: cover model planner fallback behavior"
```

Expected: commit succeeds.

### Task 6: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Test: full workspace verification commands

- [ ] **Step 1: Update README with local real planner smoke test**

Add this section before `See docs/architecture.md` in `README.md`:

````markdown
## Phase 3 Local Real Planner Smoke Test

Phase 3 can call an OpenAI-compatible provider for planner drafts when the API has a configured planner route. DeepSeek can be configured as an OpenAI-compatible provider through the existing backend API. Do not paste API keys into chat, docs, or commits.

Example local setup:

```bash
pnpm dev:api
```

In another shell, create a provider, create a credential with your local API key in the request body, create an active `planner` route, then run the normal desktop planner flow with `VITE_API_BASE_URL=http://127.0.0.1:8000`.

Credential responses remain metadata-only. The API does not return raw or encrypted secrets.
````

- [ ] **Step 2: Update architecture Phase 3 boundary and roadmap**

In `docs/architecture.md`, add this section after `## Phase 2 Boundary`:

```markdown
## Phase 3 Boundary

Phase 3 adds the first real model-backed planner path. The API resolves the configured planner model route, opens the development BYOK credential internally, calls an OpenAI-compatible chat completions provider through the gateway package, validates JSON TaskSpec drafts, and persists those drafts for human approval.

The existing approval boundary remains intact: model output creates planner drafts only, and tasks are created only after a human approves the planner run. If the route, credential, provider request, or model output is unavailable, the API falls back to `FakePlanner` and records a fallback reason on the planner run.

Phase 3 keeps the gateway in-process, does not add desktop model settings UI, does not use production KMS, and does not calculate real model pricing.
```

Update the `Roadmap` section:

Completed list should include a new item:

```markdown
4. Real model-backed planner vertical slice that uses route resolution to create TaskSpec drafts for human approval, logs usage, and falls back to fake drafts on provider failures.
```

Future list should remove the old real model-backed planner item and start with Local Runner:

```markdown
1. Local Runner that reads repositories, creates worktrees, generates diffs, and lets users review patches.
2. Automated tests, reviewer loop, and debug loop.
3. Cloud sandbox workers, GitHub/GitLab integration, artifacts, and PR creation.
4. Commercial beta with users, organizations, subscriptions, credit wallet, usage ledger, rate limits, and billing provider abstraction.
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
pytest services/llm-gateway/tests apps/api/tests/test_model_settings_api.py apps/api/tests/test_model_planner.py apps/api/tests/test_planner_endpoints.py apps/api/tests/test_usage_ledger_api.py -v
```

Expected: PASS.

- [ ] **Step 4: Run complete workspace tests**

Run:

```bash
pnpm test
```

Expected: JavaScript and Python tests pass.

- [ ] **Step 5: Run typecheck**

Run:

```bash
pnpm typecheck
```

Expected: typecheck passes.

- [ ] **Step 6: Run full Python verification**

Run:

```bash
pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v
```

Expected: all Python tests pass.

- [ ] **Step 7: Check whitespace**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 8: Commit docs and verified state**

Run:

```bash
git add README.md docs/architecture.md
git commit -m "docs: describe phase 3 real planner"
```

Expected: commit succeeds.

## Final Review Checklist

- [ ] Gateway adapter sends OpenAI-compatible `/chat/completions` requests.
- [ ] Gateway adapter response objects never contain raw API keys.
- [ ] `DevSecretVault.open()` restores local development credentials.
- [ ] Credential API responses and validation errors still do not leak `secret_value`.
- [ ] Planner run creation uses a configured active planner route when available.
- [ ] Model output is parsed and validated before draft persistence.
- [ ] Model planner success appends usage ledger entry with planner run id.
- [ ] Missing route falls back to fake with `fallback_reason = "no_configured_route"`.
- [ ] Deleted or missing credential falls back with `fallback_reason = "credential_unavailable"`.
- [ ] Provider request failure falls back with `fallback_reason = "provider_request_failed"`.
- [ ] Invalid model output falls back with `fallback_reason = "invalid_model_output"`.
- [ ] Fallback runs do not append fake model usage.
- [ ] Human approval is still required before tasks are created.
- [ ] No desktop model settings UI was added.
- [ ] No standalone gateway HTTP service was added.
- [ ] `pnpm test` passes.
- [ ] `pnpm typecheck` passes.
- [ ] `pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v` passes.
- [ ] `git diff --check` passes.
