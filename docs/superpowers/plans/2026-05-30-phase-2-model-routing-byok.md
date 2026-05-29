# Phase 2 Model Routing and BYOK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend-first model provider, BYOK credential, role-based model route, route resolution, and append-only usage ledger foundation.

**Architecture:** Keep Phase 2 network-free and control-plane only. Add SQLModel persistence tables, Pydantic API contracts, focused service modules, FastAPI routes, and gateway contract models while preserving the Phase 1 `FakePlanner` flow unchanged.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, Pydantic v2, SQLite-backed tests, Vitest and pnpm workspace verification.

---

## File Structure

- Modify: `apps/api/app/ai_company_api/models/entities.py`
  - Add model provider, credential, route, and usage ledger SQLModel tables.
  - Add low-cardinality enums used by those tables.
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
  - Add request and response models for providers, credentials, routes, resolved routes, and usage ledger entries.
  - Ensure credential read models exclude `secret_value` and `encrypted_secret`.
- Create: `apps/api/app/ai_company_api/services/secret_vault.py`
  - Add `SecretVault`, `SealedSecret`, and `DevSecretVault`.
- Create: `apps/api/app/ai_company_api/services/model_settings.py`
  - Add provider, credential, route, and route-resolution business logic.
- Create: `apps/api/app/ai_company_api/services/usage_ledger.py`
  - Add usage append and filtered list logic.
- Modify: `apps/api/app/ai_company_api/api/routes.py`
  - Add FastAPI endpoints and stable response models.
- Create: `apps/api/tests/test_model_settings_api.py`
  - Cover provider, credential, route, route resolution, and OpenAPI secret leakage behavior.
- Create: `apps/api/tests/test_usage_ledger_api.py`
  - Cover usage append, filters, cross-project validation, and OpenAPI route absence.
- Modify: `services/llm-gateway/app/ai_company_llm_gateway/models.py`
  - Expand network-free contract models for provider types, resolved routes, and secret-free credential references.
- Create: `services/llm-gateway/tests/test_model_contracts.py`
  - Cover gateway contract serialization without secrets.
- Modify: `README.md`
  - Document Phase 2 backend-only boundary.
- Modify: `docs/architecture.md`
  - Document provider, credential, route, fake fallback, and usage ledger behavior.

## Implementation Notes

- Do not add real provider SDKs or HTTP calls.
- Do not change `apps/api/app/ai_company_api/services/planner.py`.
- Do not make `create_planner_run()` depend on model routes.
- Keep `usage_ledger` append-only by not adding update or delete endpoints.
- Use `response_model=` on new routes so OpenAPI stays stable.
- Use HTTP 409 for duplicate provider names and duplicate active routes.
- Use HTTP 400 for secret-bearing provider headers, disabled providers, wrong-provider credentials, and cross-project usage references.

### Task 1: LLM Gateway Contract Models

**Files:**
- Modify: `services/llm-gateway/app/ai_company_llm_gateway/models.py`
- Test: `services/llm-gateway/tests/test_model_contracts.py`
- Test: `services/llm-gateway/tests/test_fake_adapter.py`

- [ ] **Step 1: Write failing gateway contract tests**

Create `services/llm-gateway/tests/test_model_contracts.py` with:

```python
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

    assert provider.model_dump() == {
        "name": "deepseek-dev",
        "provider_type": "deepseek",
        "base_url": "https://api.deepseek.com",
    }


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

    assert route.model_dump() == {
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
```

- [ ] **Step 2: Run gateway tests and verify failure**

Run:

```bash
pytest services/llm-gateway/tests/test_model_contracts.py -v
```

Expected: FAIL with import errors for `ProviderType` and `ResolvedModelRoute`.

- [ ] **Step 3: Expand gateway models**

Modify `services/llm-gateway/app/ai_company_llm_gateway/models.py` to include these definitions while keeping the existing `ModelRoute`, `ProviderRequest`, `ProviderResponse`, and `OpenAICompatibleProviderConfig` behavior:

```python
from enum import Enum

from pydantic import BaseModel, Field


class ProviderType(str, Enum):
    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai_compatible"
    DEEPSEEK = "deepseek"


class ModelProvider(BaseModel):
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
```

- [ ] **Step 4: Run gateway tests and verify pass**

Run:

```bash
pytest services/llm-gateway/tests -v
```

Expected: PASS for all gateway tests.

- [ ] **Step 5: Commit gateway contract changes**

Run:

```bash
git add services/llm-gateway/app/ai_company_llm_gateway/models.py services/llm-gateway/tests/test_model_contracts.py services/llm-gateway/tests/test_fake_adapter.py
git commit -m "feat: expand model gateway contracts"
```

Expected: commit succeeds.

### Task 2: Secret Vault and Persistence Models

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Create: `apps/api/app/ai_company_api/services/secret_vault.py`
- Test: `apps/api/tests/test_model_settings_api.py`

- [ ] **Step 1: Write failing secret-vault and persistence tests**

Create `apps/api/tests/test_model_settings_api.py` with these initial tests:

```python
from sqlalchemy import text
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.models.entities import (
    ModelCredential,
    ModelCredentialStatus,
    ModelProvider,
    ModelProviderStatus,
    ModelProviderType,
)
from ai_company_api.services.secret_vault import DevSecretVault


def build_session() -> Session:
    engine = build_engine("sqlite://")
    init_db(engine)
    return Session(engine)


def test_dev_secret_vault_seals_without_plaintext() -> None:
    sealed = DevSecretVault().seal("sk-example1234")

    assert sealed.secret_last4 == "1234"
    assert sealed.encrypted_secret.startswith("dev-vault:v1:")
    assert "sk-example1234" not in sealed.encrypted_secret


def test_model_credential_persists_without_raw_plaintext() -> None:
    sealed = DevSecretVault().seal("sk-example1234")

    with build_session() as session:
        provider = ModelProvider(
            name="deepseek-dev",
            provider_type=ModelProviderType.DEEPSEEK,
        )
        credential = ModelCredential(
            provider_id=provider.id,
            display_name="Personal DeepSeek key",
            secret_last4=sealed.secret_last4,
            encrypted_secret=sealed.encrypted_secret,
        )
        session.add(provider)
        session.add(credential)
        session.commit()

        raw = session.connection().execute(
            text(
                "select encrypted_secret, secret_last4, status "
                "from model_credential where id = :id"
            ),
            {"id": credential.id},
        ).mappings().one()

    assert raw["encrypted_secret"].startswith("dev-vault:v1:")
    assert "sk-example1234" not in raw["encrypted_secret"]
    assert raw["secret_last4"] == "1234"
    assert raw["status"] == ModelCredentialStatus.ACTIVE.value


def test_model_provider_defaults_to_active_status() -> None:
    with build_session() as session:
        provider = ModelProvider(
            name="fake",
            provider_type=ModelProviderType.FAKE,
        )
        session.add(provider)
        session.commit()

        raw_status = session.connection().execute(
            text("select status from model_provider where id = :id"),
            {"id": provider.id},
        ).scalar_one()

    assert raw_status == ModelProviderStatus.ACTIVE.value
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py -v
```

Expected: FAIL with import errors for `ModelProvider` and `DevSecretVault`.

- [ ] **Step 3: Add secret vault service**

Create `apps/api/app/ai_company_api/services/secret_vault.py`:

```python
from hashlib import sha256
from typing import Protocol

from pydantic import BaseModel, Field


class SealedSecret(BaseModel):
    encrypted_secret: str = Field(min_length=1)
    secret_last4: str


class SecretVault(Protocol):
    def seal(self, secret_value: str) -> SealedSecret:
        ...


class DevSecretVault:
    def seal(self, secret_value: str) -> SealedSecret:
        digest = sha256(secret_value.encode("utf-8")).hexdigest()
        return SealedSecret(
            encrypted_secret=f"dev-vault:v1:{digest}",
            secret_last4=secret_value[-4:] if len(secret_value) >= 4 else secret_value,
        )
```

- [ ] **Step 4: Add SQLModel tables and enums**

Modify `apps/api/app/ai_company_api/models/entities.py`.

Add these enum definitions after `ApprovalStatus`:

```python
class ModelProviderType(str, Enum):
    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai_compatible"
    DEEPSEEK = "deepseek"


class ModelProviderStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ModelCredentialStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class ModelRouteStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UsageType(str, Enum):
    MODEL_TOKENS = "model_tokens"
```

Add these SQLModel classes before `Task`:

```python
class ModelProvider(SQLModel, table=True):
    __tablename__ = "model_provider"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_model_provider_workspace_name"),
    )

    id: str = Field(
        default_factory=lambda: prefixed_id("model_provider"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    name: str = Field(index=True)
    provider_type: ModelProviderType = Field(
        sa_column=Column(
            SAEnum(
                ModelProviderType,
                name="model_provider_type",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
        ),
    )
    base_url: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    status: ModelProviderStatus = Field(
        default=ModelProviderStatus.ACTIVE,
        sa_column=Column(
            SAEnum(
                ModelProviderStatus,
                name="model_provider_status",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ModelCredential(SQLModel, table=True):
    __tablename__ = "model_credential"

    id: str = Field(
        default_factory=lambda: prefixed_id("model_credential"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    provider_id: str = Field(index=True, foreign_key="model_provider.id")
    display_name: str
    secret_last4: str = ""
    encrypted_secret: str
    status: ModelCredentialStatus = Field(
        default=ModelCredentialStatus.ACTIVE,
        sa_column=Column(
            SAEnum(
                ModelCredentialStatus,
                name="model_credential_status",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ModelRoute(SQLModel, table=True):
    __tablename__ = "model_route"

    id: str = Field(default_factory=lambda: prefixed_id("model_route"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    agent_role: str = Field(index=True)
    provider_id: str = Field(index=True, foreign_key="model_provider.id")
    credential_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="model_credential.id",
    )
    model_name: str
    fallback_models: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: ModelRouteStatus = Field(
        default=ModelRouteStatus.ACTIVE,
        sa_column=Column(
            SAEnum(
                ModelRouteStatus,
                name="model_route_status",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UsageLedgerEntry(SQLModel, table=True):
    __tablename__ = "usage_ledger_entry"

    id: str = Field(default_factory=lambda: prefixed_id("usage"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    organization_id: str = Field(default="dev_organization", index=True)
    user_id: str = Field(default="dev_user", index=True)
    project_id: str | None = Field(default=None, index=True, foreign_key="project.id")
    task_id: str | None = Field(default=None, index=True, foreign_key="task.id")
    planner_run_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="planner_run.id",
    )
    usage_type: UsageType = Field(
        default=UsageType.MODEL_TOKENS,
        sa_column=Column(
            SAEnum(
                UsageType,
                name="usage_type",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    provider_name: str
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    unit_price_cents: int = 0
    amount_cents: int = 0
    raw_usage_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)
```

- [ ] **Step 5: Run persistence tests and verify pass**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py::test_dev_secret_vault_seals_without_plaintext apps/api/tests/test_model_settings_api.py::test_model_credential_persists_without_raw_plaintext apps/api/tests/test_model_settings_api.py::test_model_provider_defaults_to_active_status -v
```

Expected: PASS for the three tests.

- [ ] **Step 6: Commit secret vault and persistence models**

Run:

```bash
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/services/secret_vault.py apps/api/tests/test_model_settings_api.py
git commit -m "feat: add model routing persistence"
```

Expected: commit succeeds.

### Task 3: Provider and Credential API

**Files:**
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Create: `apps/api/app/ai_company_api/services/model_settings.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Test: `apps/api/tests/test_model_settings_api.py`

- [ ] **Step 1: Add failing provider and credential API tests**

Append to `apps/api/tests/test_model_settings_api.py`:

```python
from fastapi.testclient import TestClient

from ai_company_api.main import create_app


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


def test_create_and_list_model_provider() -> None:
    with build_client() as client:
        create_response = client.post(
            "/model-providers",
            json={
                "name": "deepseek-dev",
                "provider_type": "deepseek",
                "base_url": "https://api.deepseek.com",
                "default_headers": {"X-Client": "ai-scdc-dev"},
            },
        )
        list_response = client.get("/model-providers")

    assert create_response.status_code == 201
    provider = create_response.json()
    assert provider["workspace_id"] == "dev_workspace"
    assert provider["name"] == "deepseek-dev"
    assert provider["provider_type"] == "deepseek"
    assert provider["status"] == "active"
    assert list_response.status_code == 200
    assert [item["name"] for item in list_response.json()] == ["deepseek-dev"]


def test_duplicate_model_provider_name_returns_409() -> None:
    with build_client() as client:
        first = client.post(
            "/model-providers",
            json={"name": "deepseek-dev", "provider_type": "deepseek"},
        )
        second = client.post(
            "/model-providers",
            json={"name": "deepseek-dev", "provider_type": "deepseek"},
        )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "Model provider name already exists"


def test_model_provider_rejects_secret_bearing_default_headers() -> None:
    with build_client() as client:
        response = client.post(
            "/model-providers",
            json={
                "name": "bad-provider",
                "provider_type": "openai_compatible",
                "default_headers": {"Authorization": "Bearer secret"},
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Default headers must not contain secrets"


def test_model_provider_rejects_unsupported_provider_type() -> None:
    with build_client() as client:
        response = client.post(
            "/model-providers",
            json={"name": "unknown-dev", "provider_type": "unknown"},
        )

    assert response.status_code == 422


def test_create_and_list_model_credential_never_returns_secret_fields() -> None:
    with build_client() as client:
        provider = client.post(
            "/model-providers",
            json={"name": "deepseek-dev", "provider_type": "deepseek"},
        ).json()
        create_response = client.post(
            "/model-credentials",
            json={
                "provider_id": provider["id"],
                "display_name": "Personal DeepSeek key",
                "secret_value": "sk-example1234",
            },
        )
        list_response = client.get("/model-credentials")

    assert create_response.status_code == 201
    credential = create_response.json()
    assert credential["provider_id"] == provider["id"]
    assert credential["display_name"] == "Personal DeepSeek key"
    assert credential["secret_last4"] == "1234"
    assert credential["status"] == "active"
    assert "secret_value" not in credential
    assert "encrypted_secret" not in credential
    assert list_response.status_code == 200
    assert list_response.json() == [credential]


def test_model_credential_openapi_schema_excludes_secret_outputs() -> None:
    with build_client() as client:
        schema = client.get("/openapi.json").json()

    credential_read = schema["components"]["schemas"]["ModelCredentialRead"]
    credential_create = schema["components"]["schemas"]["ModelCredentialCreate"]

    assert "secret_value" in credential_create["properties"]
    assert "secret_value" not in credential_read["properties"]
    assert "encrypted_secret" not in credential_read["properties"]


def test_delete_model_credential_soft_deletes() -> None:
    with build_client() as client:
        provider = client.post(
            "/model-providers",
            json={"name": "deepseek-dev", "provider_type": "deepseek"},
        ).json()
        credential = client.post(
            "/model-credentials",
            json={
                "provider_id": provider["id"],
                "display_name": "Personal DeepSeek key",
                "secret_value": "sk-example1234",
            },
        ).json()

        response = client.delete(f"/model-credentials/{credential['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"


def test_create_model_credential_rejects_disabled_provider() -> None:
    from fastapi import HTTPException

    from ai_company_api.schemas.api import ModelCredentialCreate
    from ai_company_api.services.model_settings import create_model_credential

    with build_session() as session:
        provider = ModelProvider(
            name="disabled-provider",
            provider_type=ModelProviderType.DEEPSEEK,
            status=ModelProviderStatus.DISABLED,
        )
        session.add(provider)
        session.commit()

        try:
            create_model_credential(
                session,
                ModelCredentialCreate(
                    provider_id=provider.id,
                    display_name="Disabled provider key",
                    secret_value="sk-disabled1234",
                ),
            )
        except HTTPException as exc:
            assert exc.status_code == 400
            assert exc.detail == "Model provider is disabled"
        else:
            raise AssertionError("Expected disabled provider to reject credentials")
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py -v
```

Expected: FAIL with 404 responses for `/model-providers` or missing schema imports.

- [ ] **Step 3: Add API schemas**

Modify `apps/api/app/ai_company_api/schemas/api.py`.

Add imports:

```python
from pydantic import BaseModel, Field, NonNegativeInt
```

If `BaseModel` and `Field` are already imported, replace the import with the line above.

Add these enums after `RiskLevel`:

```python
class ModelProviderType(str, Enum):
    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai_compatible"
    DEEPSEEK = "deepseek"


class ModelProviderStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ModelCredentialStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class ModelRouteStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ModelRouteResolutionSource(str, Enum):
    CONFIGURED = "configured"
    FALLBACK_FAKE = "fallback_fake"


class UsageType(str, Enum):
    MODEL_TOKENS = "model_tokens"
```

Add these models before `DevIdentity`:

```python
class ModelProviderCreate(BaseModel):
    name: str = Field(min_length=1)
    provider_type: ModelProviderType
    base_url: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict)


class ModelProviderRead(BaseModel):
    id: str
    workspace_id: str
    name: str
    provider_type: str
    base_url: str | None
    default_headers: dict[str, str]
    status: str
    created_at: datetime
    updated_at: datetime


class ModelCredentialCreate(BaseModel):
    provider_id: str
    display_name: str = Field(min_length=1)
    secret_value: str = Field(min_length=1)


class ModelCredentialRead(BaseModel):
    id: str
    workspace_id: str
    provider_id: str
    display_name: str
    secret_last4: str
    status: str
    created_at: datetime
    updated_at: datetime


class ModelRouteCreate(BaseModel):
    agent_role: AgentRole
    provider_id: str
    credential_id: str | None = None
    model_name: str = Field(min_length=1)
    fallback_models: list[str] = Field(default_factory=list)


class ModelRouteUpdate(BaseModel):
    provider_id: str | None = None
    credential_id: str | None = None
    model_name: str | None = Field(default=None, min_length=1)
    fallback_models: list[str] | None = None
    status: ModelRouteStatus | None = None


class ModelRouteRead(BaseModel):
    id: str
    workspace_id: str
    agent_role: str
    provider_id: str
    credential_id: str | None
    model_name: str
    fallback_models: list[str]
    status: str
    created_at: datetime
    updated_at: datetime


class ResolvedModelRouteRead(BaseModel):
    agent_role: str
    provider_name: str
    provider_type: str
    model_name: str
    fallback_models: list[str]
    credential_required: bool
    credential_available: bool
    is_available: bool
    resolution_source: str
    route_id: str | None


class UsageLedgerCreate(BaseModel):
    project_id: str | None = None
    planner_run_id: str | None = None
    task_id: str | None = None
    usage_type: UsageType = UsageType.MODEL_TOKENS
    provider_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    prompt_tokens: NonNegativeInt = 0
    completion_tokens: NonNegativeInt = 0
    unit_price_cents: NonNegativeInt = 0
    amount_cents: NonNegativeInt = 0
    raw_usage_json: dict[str, Any] = Field(default_factory=dict)


class UsageLedgerRead(BaseModel):
    id: str
    workspace_id: str
    organization_id: str
    user_id: str
    project_id: str | None
    planner_run_id: str | None
    task_id: str | None
    usage_type: str
    provider_name: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    unit_price_cents: int
    amount_cents: int
    raw_usage_json: dict[str, Any]
    created_at: datetime
```

- [ ] **Step 4: Add provider and credential service functions**

Create `apps/api/app/ai_company_api/services/model_settings.py`:

```python
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    ModelCredential,
    ModelCredentialStatus,
    ModelProvider,
    ModelProviderStatus,
    ModelProviderType,
    utc_now,
)
from ai_company_api.schemas.api import (
    ModelCredentialCreate,
    ModelCredentialRead,
    ModelProviderCreate,
    ModelProviderRead,
)
from ai_company_api.services.secret_vault import DevSecretVault, SecretVault


SECRET_HEADER_NAMES = {
    "authorization",
    "api-key",
    "apikey",
    "x-api-key",
}


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _reject_secret_headers(headers: dict[str, str]) -> None:
    for key in headers:
        normalized = key.strip().lower().replace("_", "-")
        if normalized in SECRET_HEADER_NAMES:
            raise HTTPException(
                status_code=400,
                detail="Default headers must not contain secrets",
            )


def _provider_read(provider: ModelProvider) -> ModelProviderRead:
    return ModelProviderRead(
        id=provider.id,
        workspace_id=provider.workspace_id,
        name=provider.name,
        provider_type=_enum_value(provider.provider_type),
        base_url=provider.base_url,
        default_headers=provider.default_headers,
        status=_enum_value(provider.status),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _credential_read(credential: ModelCredential) -> ModelCredentialRead:
    return ModelCredentialRead(
        id=credential.id,
        workspace_id=credential.workspace_id,
        provider_id=credential.provider_id,
        display_name=credential.display_name,
        secret_last4=credential.secret_last4,
        status=_enum_value(credential.status),
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


def get_model_provider(session: Session, provider_id: str) -> ModelProvider:
    provider = session.get(ModelProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Model provider not found")
    return provider


def get_model_credential(session: Session, credential_id: str) -> ModelCredential:
    credential = session.get(ModelCredential, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="Model credential not found")
    return credential


def list_model_providers(session: Session) -> list[ModelProviderRead]:
    statement = select(ModelProvider).order_by(ModelProvider.created_at, ModelProvider.id)
    return [_provider_read(provider) for provider in session.exec(statement).all()]


def create_model_provider(
    session: Session,
    data: ModelProviderCreate,
) -> ModelProviderRead:
    _reject_secret_headers(data.default_headers)
    provider = ModelProvider(
        name=data.name,
        provider_type=ModelProviderType(data.provider_type.value),
        base_url=data.base_url,
        default_headers=data.default_headers,
    )
    session.add(provider)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Model provider name already exists",
        ) from exc
    session.refresh(provider)
    return _provider_read(provider)


def list_model_credentials(session: Session) -> list[ModelCredentialRead]:
    statement = select(ModelCredential).order_by(
        ModelCredential.created_at,
        ModelCredential.id,
    )
    return [_credential_read(credential) for credential in session.exec(statement).all()]


def create_model_credential(
    session: Session,
    data: ModelCredentialCreate,
    vault: SecretVault | None = None,
) -> ModelCredentialRead:
    provider = get_model_provider(session, data.provider_id)
    if _enum_value(provider.status) != ModelProviderStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail="Model provider is disabled")

    sealed = (vault or DevSecretVault()).seal(data.secret_value)
    credential = ModelCredential(
        provider_id=provider.id,
        display_name=data.display_name,
        secret_last4=sealed.secret_last4,
        encrypted_secret=sealed.encrypted_secret,
    )
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return _credential_read(credential)


def delete_model_credential(session: Session, credential_id: str) -> ModelCredentialRead:
    credential = get_model_credential(session, credential_id)
    credential.status = ModelCredentialStatus.DELETED
    credential.updated_at = utc_now()
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return _credential_read(credential)
```

- [ ] **Step 5: Add provider and credential routes**

Modify `apps/api/app/ai_company_api/api/routes.py`.

Add schema imports:

```python
    ModelCredentialCreate,
    ModelCredentialRead,
    ModelProviderCreate,
    ModelProviderRead,
```

Add service imports:

```python
from ai_company_api.services.model_settings import (
    create_model_credential,
    create_model_provider,
    delete_model_credential,
    list_model_credentials,
    list_model_providers,
)
```

Add route functions before the task endpoints:

```python
@router.get("/model-providers", response_model=list[ModelProviderRead])
def get_model_providers(session: SessionDep) -> list[ModelProviderRead]:
    return list_model_providers(session)


@router.post(
    "/model-providers",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelProviderRead,
)
def post_model_provider(
    data: ModelProviderCreate,
    session: SessionDep,
) -> ModelProviderRead:
    return create_model_provider(session, data)


@router.get("/model-credentials", response_model=list[ModelCredentialRead])
def get_model_credentials(session: SessionDep) -> list[ModelCredentialRead]:
    return list_model_credentials(session)


@router.post(
    "/model-credentials",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelCredentialRead,
)
def post_model_credential(
    data: ModelCredentialCreate,
    session: SessionDep,
) -> ModelCredentialRead:
    return create_model_credential(session, data)


@router.delete(
    "/model-credentials/{credential_id}",
    response_model=ModelCredentialRead,
)
def delete_model_credential_by_id(
    credential_id: str,
    session: SessionDep,
) -> ModelCredentialRead:
    return delete_model_credential(session, credential_id)
```

- [ ] **Step 6: Run provider and credential API tests**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py -v
```

Expected: PASS for all tests currently in `test_model_settings_api.py`.

- [ ] **Step 7: Commit provider and credential API**

Run:

```bash
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/model_settings.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_model_settings_api.py
git commit -m "feat: add model provider credentials API"
```

Expected: commit succeeds.

### Task 4: Model Routes and Route Resolution

**Files:**
- Modify: `apps/api/app/ai_company_api/services/model_settings.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Test: `apps/api/tests/test_model_settings_api.py`

- [ ] **Step 1: Add failing route tests**

Append to `apps/api/tests/test_model_settings_api.py`:

```python
def create_provider_and_credential(client: TestClient) -> tuple[dict, dict]:
    provider = client.post(
        "/model-providers",
        json={"name": "deepseek-dev", "provider_type": "deepseek"},
    ).json()
    credential = client.post(
        "/model-credentials",
        json={
            "provider_id": provider["id"],
            "display_name": "Personal DeepSeek key",
            "secret_value": "sk-example1234",
        },
    ).json()
    return provider, credential


def test_create_list_and_update_model_route() -> None:
    with build_client() as client:
        provider, credential = create_provider_and_credential(client)
        create_response = client.post(
            "/model-routes",
            json={
                "agent_role": "planner",
                "provider_id": provider["id"],
                "credential_id": credential["id"],
                "model_name": "deepseek-chat",
                "fallback_models": ["deepseek-reasoner"],
            },
        )
        list_response = client.get("/model-routes")
        update_response = client.patch(
            f"/model-routes/{create_response.json()['id']}",
            json={
                "model_name": "deepseek-reasoner",
                "fallback_models": ["deepseek-chat"],
            },
        )

    assert create_response.status_code == 201
    route = create_response.json()
    assert route["agent_role"] == "planner"
    assert route["provider_id"] == provider["id"]
    assert route["credential_id"] == credential["id"]
    assert route["model_name"] == "deepseek-chat"
    assert route["fallback_models"] == ["deepseek-reasoner"]
    assert route["status"] == "active"
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [route["id"]]
    assert update_response.status_code == 200
    assert update_response.json()["model_name"] == "deepseek-reasoner"
    assert update_response.json()["fallback_models"] == ["deepseek-chat"]


def test_duplicate_active_model_route_for_role_returns_409() -> None:
    with build_client() as client:
        provider, credential = create_provider_and_credential(client)
        first = client.post(
            "/model-routes",
            json={
                "agent_role": "planner",
                "provider_id": provider["id"],
                "credential_id": credential["id"],
                "model_name": "deepseek-chat",
            },
        )
        second = client.post(
            "/model-routes",
            json={
                "agent_role": "planner",
                "provider_id": provider["id"],
                "credential_id": credential["id"],
                "model_name": "deepseek-reasoner",
            },
        )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "Active model route already exists for role"


def test_route_rejects_credential_from_different_provider() -> None:
    with build_client() as client:
        provider, _credential = create_provider_and_credential(client)
        other_provider = client.post(
            "/model-providers",
            json={"name": "openai-dev", "provider_type": "openai_compatible"},
        ).json()
        other_credential = client.post(
            "/model-credentials",
            json={
                "provider_id": other_provider["id"],
                "display_name": "Personal OpenAI key",
                "secret_value": "sk-openai1234",
            },
        ).json()

        response = client.post(
            "/model-routes",
            json={
                "agent_role": "planner",
                "provider_id": provider["id"],
                "credential_id": other_credential["id"],
                "model_name": "deepseek-chat",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Credential does not belong to provider"


def test_resolve_model_route_returns_fake_fallback_when_unconfigured() -> None:
    with build_client() as client:
        response = client.get("/model-routes/resolve", params={"agent_role": "planner"})

    assert response.status_code == 200
    assert response.json() == {
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


def test_resolve_model_route_returns_configured_route() -> None:
    with build_client() as client:
        provider, credential = create_provider_and_credential(client)
        route = client.post(
            "/model-routes",
            json={
                "agent_role": "planner",
                "provider_id": provider["id"],
                "credential_id": credential["id"],
                "model_name": "deepseek-chat",
                "fallback_models": ["deepseek-reasoner"],
            },
        ).json()

        response = client.get("/model-routes/resolve", params={"agent_role": "planner"})

    assert response.status_code == 200
    assert response.json() == {
        "agent_role": "planner",
        "provider_name": "deepseek-dev",
        "provider_type": "deepseek",
        "model_name": "deepseek-chat",
        "fallback_models": ["deepseek-reasoner"],
        "credential_required": True,
        "credential_available": True,
        "is_available": True,
        "resolution_source": "configured",
        "route_id": route["id"],
    }


def test_resolve_non_fake_route_without_credential_is_unavailable() -> None:
    with build_client() as client:
        provider = client.post(
            "/model-providers",
            json={"name": "deepseek-dev", "provider_type": "deepseek"},
        ).json()
        route = client.post(
            "/model-routes",
            json={
                "agent_role": "planner",
                "provider_id": provider["id"],
                "model_name": "deepseek-chat",
            },
        ).json()

        response = client.get("/model-routes/resolve", params={"agent_role": "planner"})

    assert response.status_code == 200
    assert response.json()["route_id"] == route["id"]
    assert response.json()["credential_required"] is True
    assert response.json()["credential_available"] is False
    assert response.json()["is_available"] is False


def test_resolve_model_route_marks_deleted_credential_unavailable() -> None:
    with build_client() as client:
        provider, credential = create_provider_and_credential(client)
        route = client.post(
            "/model-routes",
            json={
                "agent_role": "planner",
                "provider_id": provider["id"],
                "credential_id": credential["id"],
                "model_name": "deepseek-chat",
            },
        ).json()
        client.delete(f"/model-credentials/{credential['id']}")

        response = client.get("/model-routes/resolve", params={"agent_role": "planner"})

    assert response.status_code == 200
    assert response.json()["route_id"] == route["id"]
    assert response.json()["credential_required"] is True
    assert response.json()["credential_available"] is False
    assert response.json()["is_available"] is False
```

- [ ] **Step 2: Run route tests and verify failure**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py -v
```

Expected: FAIL with 404 responses for `/model-routes`.

- [ ] **Step 3: Add route service functions**

Append to `apps/api/app/ai_company_api/services/model_settings.py`:

```python
from ai_company_api.models.entities import ModelRoute, ModelRouteStatus
from ai_company_api.schemas.api import (
    AgentRole,
    ModelRouteCreate,
    ModelRouteRead,
    ModelRouteUpdate,
    ResolvedModelRouteRead,
)
```

If imports already exist in the file, merge the names into the existing import groups.

Add these functions:

```python
def _route_read(route: ModelRoute) -> ModelRouteRead:
    return ModelRouteRead(
        id=route.id,
        workspace_id=route.workspace_id,
        agent_role=route.agent_role,
        provider_id=route.provider_id,
        credential_id=route.credential_id,
        model_name=route.model_name,
        fallback_models=route.fallback_models,
        status=_enum_value(route.status),
        created_at=route.created_at,
        updated_at=route.updated_at,
    )


def get_model_route(session: Session, route_id: str) -> ModelRoute:
    route = session.get(ModelRoute, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Model route not found")
    return route


def _active_route_for_role(
    session: Session,
    agent_role: str,
    exclude_route_id: str | None = None,
) -> ModelRoute | None:
    statement = (
        select(ModelRoute)
        .where(ModelRoute.workspace_id == "dev_workspace")
        .where(ModelRoute.agent_role == agent_role)
        .where(ModelRoute.status == ModelRouteStatus.ACTIVE)
    )
    routes = list(session.exec(statement).all())
    for route in routes:
        if route.id != exclude_route_id:
            return route
    return None


def _validate_route_references(
    session: Session,
    provider_id: str,
    credential_id: str | None,
) -> tuple[ModelProvider, ModelCredential | None]:
    provider = get_model_provider(session, provider_id)
    credential = None
    if credential_id is not None:
        credential = get_model_credential(session, credential_id)
        if credential.provider_id != provider.id:
            raise HTTPException(
                status_code=400,
                detail="Credential does not belong to provider",
            )
    return provider, credential


def list_model_routes(session: Session) -> list[ModelRouteRead]:
    statement = select(ModelRoute).order_by(
        ModelRoute.agent_role,
        ModelRoute.created_at,
        ModelRoute.id,
    )
    return [_route_read(route) for route in session.exec(statement).all()]


def create_model_route(session: Session, data: ModelRouteCreate) -> ModelRouteRead:
    _validate_route_references(session, data.provider_id, data.credential_id)
    if _active_route_for_role(session, data.agent_role.value) is not None:
        raise HTTPException(
            status_code=409,
            detail="Active model route already exists for role",
        )

    route = ModelRoute(
        agent_role=data.agent_role.value,
        provider_id=data.provider_id,
        credential_id=data.credential_id,
        model_name=data.model_name,
        fallback_models=data.fallback_models,
    )
    session.add(route)
    session.commit()
    session.refresh(route)
    return _route_read(route)


def update_model_route(
    session: Session,
    route_id: str,
    data: ModelRouteUpdate,
) -> ModelRouteRead:
    route = get_model_route(session, route_id)
    provider_id = data.provider_id if data.provider_id is not None else route.provider_id
    credential_id = route.credential_id
    if "credential_id" in data.model_fields_set:
        credential_id = data.credential_id
    _validate_route_references(session, provider_id, credential_id)

    if data.provider_id is not None:
        route.provider_id = data.provider_id
    if "credential_id" in data.model_fields_set:
        route.credential_id = data.credential_id
    if data.model_name is not None:
        route.model_name = data.model_name
    if data.fallback_models is not None:
        route.fallback_models = data.fallback_models
    if data.status is not None:
        route.status = ModelRouteStatus(data.status.value)

    if _enum_value(route.status) == ModelRouteStatus.ACTIVE.value:
        if _active_route_for_role(session, route.agent_role, route.id) is not None:
            raise HTTPException(
                status_code=409,
                detail="Active model route already exists for role",
            )

    route.updated_at = utc_now()
    session.add(route)
    session.commit()
    session.refresh(route)
    return _route_read(route)


def resolve_model_route(session: Session, agent_role: AgentRole) -> ResolvedModelRouteRead:
    route = _active_route_for_role(session, agent_role.value)
    if route is None:
        return ResolvedModelRouteRead(
            agent_role=agent_role.value,
            provider_name="fake",
            provider_type=ModelProviderType.FAKE.value,
            model_name=f"fake-{agent_role.value}",
            fallback_models=[],
            credential_required=False,
            credential_available=False,
            is_available=True,
            resolution_source="fallback_fake",
            route_id=None,
        )

    provider = get_model_provider(session, route.provider_id)
    provider_type = _enum_value(provider.provider_type)
    provider_active = _enum_value(provider.status) == ModelProviderStatus.ACTIVE.value
    credential_required = provider_type != ModelProviderType.FAKE.value
    credential_available = False
    if route.credential_id is not None:
        credential = session.get(ModelCredential, route.credential_id)
        credential_available = (
            credential is not None
            and _enum_value(credential.status) == ModelCredentialStatus.ACTIVE.value
        )

    is_available = provider_active and (
        not credential_required or credential_available
    )

    return ResolvedModelRouteRead(
        agent_role=agent_role.value,
        provider_name=provider.name,
        provider_type=provider_type,
        model_name=route.model_name,
        fallback_models=route.fallback_models,
        credential_required=credential_required,
        credential_available=credential_available,
        is_available=is_available,
        resolution_source="configured",
        route_id=route.id,
    )
```

- [ ] **Step 4: Add route endpoints**

Modify `apps/api/app/ai_company_api/api/routes.py`.

Add schema imports:

```python
    AgentRole,
    ModelRouteCreate,
    ModelRouteRead,
    ModelRouteUpdate,
    ResolvedModelRouteRead,
```

Add service imports:

```python
    create_model_route,
    list_model_routes,
    resolve_model_route,
    update_model_route,
```

Add route functions after credential routes:

```python
@router.get("/model-routes", response_model=list[ModelRouteRead])
def get_model_routes(session: SessionDep) -> list[ModelRouteRead]:
    return list_model_routes(session)


@router.post(
    "/model-routes",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelRouteRead,
)
def post_model_route(
    data: ModelRouteCreate,
    session: SessionDep,
) -> ModelRouteRead:
    return create_model_route(session, data)


@router.patch("/model-routes/{route_id}", response_model=ModelRouteRead)
def patch_model_route(
    route_id: str,
    data: ModelRouteUpdate,
    session: SessionDep,
) -> ModelRouteRead:
    return update_model_route(session, route_id, data)


@router.get("/model-routes/resolve", response_model=ResolvedModelRouteRead)
def resolve_model_route_for_role(
    agent_role: AgentRole,
    session: SessionDep,
) -> ResolvedModelRouteRead:
    return resolve_model_route(session, agent_role)
```

- [ ] **Step 5: Run route tests and verify pass**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py -v
```

Expected: PASS for all model settings tests.

- [ ] **Step 6: Commit route API**

Run:

```bash
git add apps/api/app/ai_company_api/services/model_settings.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_model_settings_api.py
git commit -m "feat: add model route resolution API"
```

Expected: commit succeeds.

### Task 5: Usage Ledger API

**Files:**
- Create: `apps/api/app/ai_company_api/services/usage_ledger.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Test: `apps/api/tests/test_usage_ledger_api.py`

- [ ] **Step 1: Write failing usage ledger API tests**

Create `apps/api/tests/test_usage_ledger_api.py`:

```python
from fastapi.testclient import TestClient

from ai_company_api.main import create_app


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


def create_project_task_and_planner_run(client: TestClient) -> tuple[dict, dict, dict]:
    project = client.post("/projects", json={"name": "Demo Project"}).json()
    task = client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Backend task", "role_required": "backend"},
    ).json()
    planner_run = client.post(
        f"/projects/{project['id']}/planner-runs",
        json={"goal": "Build model route settings"},
    ).json()
    return project, task, planner_run


def test_append_usage_ledger_entry_computes_total_tokens() -> None:
    with build_client() as client:
        project, task, planner_run = create_project_task_and_planner_run(client)

        response = client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "planner_run_id": planner_run["id"],
                "usage_type": "model_tokens",
                "provider_name": "deepseek-dev",
                "model_name": "deepseek-chat",
                "prompt_tokens": 1200,
                "completion_tokens": 300,
                "unit_price_cents": 0,
                "amount_cents": 0,
                "raw_usage_json": {"source": "manual_phase_2_test"},
            },
        )

    assert response.status_code == 201
    usage = response.json()
    assert usage["workspace_id"] == "dev_workspace"
    assert usage["organization_id"] == "dev_organization"
    assert usage["user_id"] == "dev_user"
    assert usage["project_id"] == project["id"]
    assert usage["task_id"] == task["id"]
    assert usage["planner_run_id"] == planner_run["id"]
    assert usage["total_tokens"] == 1500
    assert usage["raw_usage_json"] == {"source": "manual_phase_2_test"}


def test_list_usage_ledger_filters_by_project_planner_run_and_task() -> None:
    with build_client() as client:
        project, task, planner_run = create_project_task_and_planner_run(client)
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        other_task = client.post(
            f"/projects/{other_project['id']}/tasks",
            json={"title": "Other task", "role_required": "backend"},
        ).json()
        first = client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "planner_run_id": planner_run["id"],
                "provider_name": "deepseek-dev",
                "model_name": "deepseek-chat",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        ).json()
        client.post(
            "/usage-ledger",
            json={
                "project_id": other_project["id"],
                "task_id": other_task["id"],
                "provider_name": "deepseek-dev",
                "model_name": "deepseek-chat",
                "prompt_tokens": 1,
                "completion_tokens": 1,
            },
        )

        by_project = client.get("/usage-ledger", params={"project_id": project["id"]})
        by_task = client.get("/usage-ledger", params={"task_id": task["id"]})
        by_planner = client.get(
            "/usage-ledger",
            params={"planner_run_id": planner_run["id"]},
        )

    assert [item["id"] for item in by_project.json()] == [first["id"]]
    assert [item["id"] for item in by_task.json()] == [first["id"]]
    assert [item["id"] for item in by_planner.json()] == [first["id"]]


def test_usage_ledger_rejects_cross_project_task_reference() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        other_task = client.post(
            f"/projects/{other_project['id']}/tasks",
            json={"title": "Other task", "role_required": "backend"},
        ).json()

        response = client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": other_task["id"],
                "provider_name": "deepseek-dev",
                "model_name": "deepseek-chat",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Task does not belong to project"


def test_usage_ledger_rejects_negative_token_counts() -> None:
    with build_client() as client:
        response = client.post(
            "/usage-ledger",
            json={
                "provider_name": "deepseek-dev",
                "model_name": "deepseek-chat",
                "prompt_tokens": -1,
                "completion_tokens": 5,
            },
        )

    assert response.status_code == 422


def test_usage_ledger_has_no_update_or_delete_openapi_paths() -> None:
    with build_client() as client:
        schema = client.get("/openapi.json").json()

    assert "/usage-ledger/{usage_id}" not in schema["paths"]
    assert set(schema["paths"]["/usage-ledger"].keys()) == {"get", "post"}
```

- [ ] **Step 2: Run usage tests and verify failure**

Run:

```bash
pytest apps/api/tests/test_usage_ledger_api.py -v
```

Expected: FAIL with 404 responses for `/usage-ledger`.

- [ ] **Step 3: Add usage ledger service**

Create `apps/api/app/ai_company_api/services/usage_ledger.py`:

```python
from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    PlannerRun,
    Project,
    Task,
    UsageLedgerEntry,
    UsageType,
)
from ai_company_api.schemas.api import UsageLedgerCreate, UsageLedgerRead


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _usage_read(entry: UsageLedgerEntry) -> UsageLedgerRead:
    return UsageLedgerRead(
        id=entry.id,
        workspace_id=entry.workspace_id,
        organization_id=entry.organization_id,
        user_id=entry.user_id,
        project_id=entry.project_id,
        planner_run_id=entry.planner_run_id,
        task_id=entry.task_id,
        usage_type=_enum_value(entry.usage_type),
        provider_name=entry.provider_name,
        model_name=entry.model_name,
        prompt_tokens=entry.prompt_tokens,
        completion_tokens=entry.completion_tokens,
        total_tokens=entry.total_tokens,
        unit_price_cents=entry.unit_price_cents,
        amount_cents=entry.amount_cents,
        raw_usage_json=entry.raw_usage_json,
        created_at=entry.created_at,
    )


def _validate_project(session: Session, project_id: str | None) -> Project | None:
    if project_id is None:
        return None
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _validate_task(
    session: Session,
    task_id: str | None,
    project_id: str | None,
) -> Task | None:
    if task_id is None:
        return None
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if project_id is not None and task.project_id != project_id:
        raise HTTPException(status_code=400, detail="Task does not belong to project")
    return task


def _validate_planner_run(
    session: Session,
    planner_run_id: str | None,
    project_id: str | None,
) -> PlannerRun | None:
    if planner_run_id is None:
        return None
    planner_run = session.get(PlannerRun, planner_run_id)
    if planner_run is None:
        raise HTTPException(status_code=404, detail="Planner run not found")
    if project_id is not None and planner_run.project_id != project_id:
        raise HTTPException(
            status_code=400,
            detail="Planner run does not belong to project",
        )
    return planner_run


def append_usage_ledger_entry(
    session: Session,
    data: UsageLedgerCreate,
) -> UsageLedgerRead:
    _validate_project(session, data.project_id)
    task = _validate_task(session, data.task_id, data.project_id)
    planner_run = _validate_planner_run(session, data.planner_run_id, data.project_id)
    project_id = data.project_id
    if project_id is None and task is not None:
        project_id = task.project_id
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
    session.commit()
    session.refresh(entry)
    return _usage_read(entry)


def list_usage_ledger_entries(
    session: Session,
    project_id: str | None = None,
    planner_run_id: str | None = None,
    task_id: str | None = None,
) -> list[UsageLedgerRead]:
    statement = select(UsageLedgerEntry)
    if project_id is not None:
        statement = statement.where(UsageLedgerEntry.project_id == project_id)
    if planner_run_id is not None:
        statement = statement.where(UsageLedgerEntry.planner_run_id == planner_run_id)
    if task_id is not None:
        statement = statement.where(UsageLedgerEntry.task_id == task_id)
    statement = statement.order_by(UsageLedgerEntry.created_at, UsageLedgerEntry.id)
    return [_usage_read(entry) for entry in session.exec(statement).all()]
```

- [ ] **Step 4: Add usage ledger routes**

Modify `apps/api/app/ai_company_api/api/routes.py`.

Add schema imports:

```python
    UsageLedgerCreate,
    UsageLedgerRead,
```

Add service imports:

```python
from ai_company_api.services.usage_ledger import (
    append_usage_ledger_entry,
    list_usage_ledger_entries,
)
```

Add routes before task endpoints:

```python
@router.get("/usage-ledger", response_model=list[UsageLedgerRead])
def get_usage_ledger(
    session: SessionDep,
    project_id: str | None = None,
    planner_run_id: str | None = None,
    task_id: str | None = None,
) -> list[UsageLedgerRead]:
    return list_usage_ledger_entries(
        session,
        project_id=project_id,
        planner_run_id=planner_run_id,
        task_id=task_id,
    )


@router.post(
    "/usage-ledger",
    status_code=status.HTTP_201_CREATED,
    response_model=UsageLedgerRead,
)
def post_usage_ledger_entry(
    data: UsageLedgerCreate,
    session: SessionDep,
) -> UsageLedgerRead:
    return append_usage_ledger_entry(session, data)
```

- [ ] **Step 5: Run usage tests and verify pass**

Run:

```bash
pytest apps/api/tests/test_usage_ledger_api.py -v
```

Expected: PASS for all usage ledger tests.

- [ ] **Step 6: Run API model settings and usage tests together**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py apps/api/tests/test_usage_ledger_api.py -v
```

Expected: PASS for both Phase 2 API test files.

- [ ] **Step 7: Commit usage ledger API**

Run:

```bash
git add apps/api/app/ai_company_api/services/usage_ledger.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_usage_ledger_api.py
git commit -m "feat: add usage ledger API"
```

Expected: commit succeeds.

### Task 6: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Test: full workspace verification commands

- [ ] **Step 1: Update README Phase summary**

Modify `README.md` so the opening paragraph says:

```markdown
This repo includes the Phase 0 monorepo foundation, Phase 1 planner approval loop, and Phase 2 backend-first model routing and BYOK foundation for a desktop multi-agent software engineering console.
```

Add this paragraph after the API base URL paragraph:

```markdown
Phase 2 is backend-only. The API can create model providers, write-only BYOK credential metadata, role-based model routes, resolved fake fallback routes, and append-only usage ledger entries. It does not call real model providers yet, and credential responses never include raw or encrypted secrets.
```

- [ ] **Step 2: Update architecture future phases and runtime notes**

Modify `docs/architecture.md`.

Replace the `Future Phases` item for Phase 2:

```markdown
2. Model router and BYOK foundation with encrypted credential placeholder and usage logging.
```

with:

```markdown
2. Backend-first model router and BYOK foundation with provider metadata, write-only credential records, role-based route resolution, fake fallback routes, and append-only usage logging.
```

Add this section before `## Future Phases`:

```markdown
## Phase 2 Boundary

Phase 2 adds backend control-plane records for model providers, BYOK credentials, model routes, and usage ledger entries. Route resolution is metadata-only: if no planner route is configured, the API returns a deterministic fake planner route so the Phase 1 planner approval flow keeps working.

Credentials are write-only through the API. The server stores a development encrypted-secret placeholder and returns only credential metadata such as `secret_last4`. Phase 2 does not make real OpenAI-compatible or DeepSeek network calls.
```

- [ ] **Step 3: Run focused Python tests**

Run:

```bash
pytest apps/api/tests/test_model_settings_api.py apps/api/tests/test_usage_ledger_api.py services/llm-gateway/tests -v
```

Expected: PASS for Phase 2 API and gateway tests.

- [ ] **Step 4: Run complete workspace tests**

Run:

```bash
pnpm test
```

Expected: all JavaScript and Python tests pass.

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

- [ ] **Step 8: Commit docs and verification-ready state**

Run:

```bash
git add README.md docs/architecture.md
git commit -m "docs: describe phase 2 model routing foundation"
```

Expected: commit succeeds.

## Final Review Checklist

- [ ] `GET /model-routes/resolve?agent_role=planner` returns fake fallback on a fresh database.
- [ ] Planner run creation still uses `FakePlanner` and succeeds on a fresh database.
- [ ] `ModelCredentialRead` OpenAPI schema excludes `secret_value`.
- [ ] `ModelCredentialRead` OpenAPI schema excludes `encrypted_secret`.
- [ ] No route exists for `PATCH /usage-ledger/{usage_id}`.
- [ ] No route exists for `DELETE /usage-ledger/{usage_id}`.
- [ ] `pnpm test` passes.
- [ ] `pnpm typecheck` passes.
- [ ] `pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v` passes.
- [ ] `git diff --check` passes.
