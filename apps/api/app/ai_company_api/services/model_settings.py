from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    ModelCredential,
    ModelCredentialStatus,
    ModelProvider,
    ModelProviderStatus,
    ModelProviderType,
    ModelRoute,
    ModelRouteStatus,
    utc_now,
)
from ai_company_api.schemas.api import (
    AgentRole,
    ModelCredentialCreate,
    ModelCredentialRead,
    ModelProviderCreate,
    ModelProviderRead,
    ModelRouteCreate,
    ModelRouteRead,
    ModelRouteUpdate,
    ResolvedModelRouteRead,
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

    sealed = (vault or DevSecretVault()).seal(data.secret_value.get_secret_value())
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
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Active model route already exists for role",
        ) from exc
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
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Active model route already exists for role",
        ) from exc
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
