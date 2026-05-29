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
    "cookie",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
}

SECRET_HEADER_SUBSTRINGS = {
    "api-key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
}


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _reject_secret_headers(headers: dict[str, str]) -> None:
    for key in headers:
        normalized = key.strip().lower().replace("_", "-")
        if normalized in SECRET_HEADER_NAMES or any(
            secret_name in normalized for secret_name in SECRET_HEADER_SUBSTRINGS
        ):
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
