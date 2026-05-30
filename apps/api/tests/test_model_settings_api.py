from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import IntegrityError
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
    ModelRoute,
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
    assert sealed.encrypted_secret.startswith("dev-vault:v2:")
    assert "sk-example1234" not in sealed.encrypted_secret


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

    assert raw["encrypted_secret"].startswith("dev-vault:v2:")
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


@pytest.mark.parametrize(
    "header_name",
    [
        "Cookie",
        "Proxy-Authorization",
        "X-Auth-Token",
    ],
)
def test_model_provider_rejects_additional_secret_bearing_default_headers(
    header_name: str,
) -> None:
    with build_client() as client:
        response = client.post(
            "/model-providers",
            json={
                "name": f"bad-provider-{header_name.lower()}",
                "provider_type": "openai_compatible",
                "default_headers": {header_name: "secret"},
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


def test_create_model_credential_rejects_short_secret_and_does_not_persist() -> None:
    with build_client() as client:
        provider = client.post(
            "/model-providers",
            json={"name": "deepseek-dev", "provider_type": "deepseek"},
        ).json()
        create_response = client.post(
            "/model-credentials",
            json={
                "provider_id": provider["id"],
                "display_name": "Too short key",
                "secret_value": "abcd",
            },
        )
        list_response = client.get("/model-credentials")

    assert create_response.status_code == 422
    assert "abcd" not in create_response.text
    assert list_response.status_code == 200
    assert list_response.json() == []


def test_model_credential_openapi_schema_excludes_secret_outputs() -> None:
    with build_client() as client:
        schema = client.get("/openapi.json").json()

    credential_read = schema["components"]["schemas"]["ModelCredentialRead"]
    credential_create = schema["components"]["schemas"]["ModelCredentialCreate"]

    assert "secret_value" in credential_create["properties"]
    assert credential_create["properties"]["secret_value"]["writeOnly"] is True
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


def test_create_model_credential_api_rejects_disabled_provider(tmp_path) -> None:
    database_path = tmp_path / "disabled-provider.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        provider = ModelProvider(
            name="disabled-provider",
            provider_type=ModelProviderType.DEEPSEEK,
            status=ModelProviderStatus.DISABLED,
        )
        session.add(provider)
        session.commit()
        provider_id = provider.id

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            "/model-credentials",
            json={
                "provider_id": provider_id,
                "display_name": "Disabled provider key",
                "secret_value": "sk-disabled1234",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Model provider is disabled"


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


def test_model_route_active_uniqueness_is_enforced_by_database() -> None:
    with build_session() as session:
        provider = ModelProvider(
            name="deepseek-dev",
            provider_type=ModelProviderType.DEEPSEEK,
        )
        session.add(provider)
        session.commit()

        session.add(
            ModelRoute(
                agent_role="planner",
                provider_id=provider.id,
                model_name="deepseek-chat",
            ),
        )
        session.add(
            ModelRoute(
                agent_role="planner",
                provider_id=provider.id,
                model_name="deepseek-reasoner",
            ),
        )

        with pytest.raises(IntegrityError):
            session.commit()


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
