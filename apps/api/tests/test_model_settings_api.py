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
