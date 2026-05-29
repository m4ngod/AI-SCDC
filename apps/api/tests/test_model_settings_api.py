from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
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


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


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
