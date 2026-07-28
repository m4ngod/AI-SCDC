from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import datetime
import os
import secrets
from threading import Event
from typing import Any, Callable
from urllib.parse import urlparse

from anyio import create_task_group
from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlmodel import Session, select
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ai_company_api.api.identity_routes import router as identity_router
from ai_company_api.api.routes import router
from ai_company_api.db.session import (
    build_engine,
    get_session_dependency,
    init_db,
    plan_identity_schema_migration,
    session_generator,
)
from ai_company_api.schemas.api import (
    AccessibleAccount,
    AccessibleWorkspace,
    CurrentAccount,
    CurrentWorkspace,
    DevIdentity,
)
from ai_company_api.models.entities import (
    AccountKind,
    Organization,
    OrganizationMember,
    User,
    Workspace,
    utc_now,
)
from ai_company_api.services.auth_context import (
    DEV_ORGANIZATION_ID,
    DEV_USER_ID,
    DEV_WORKSPACE_ID,
    USER_SESSION_AUTH_MODE,
    AuthContext,
    get_auth_context_dependency,
    get_workspace_selection_auth_context_dependency,
)
from ai_company_api.services.audit_operations import (
    maintain_audit_retention,
)
from ai_company_api.services.audit_request_context import (
    AuditRequestContext,
    MonotonicAuditClock,
    audit_request_context_scope,
    safe_user_agent,
)
from ai_company_api.services.auth_policy import (
    AUTHENTICATION_ENVIRONMENT_ENV,
    AuthenticationEnvironment,
    AuthenticationPolicy,
    HumanCredentialType,
    authentication_policy_for_environment,
)
from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProvider,
)
from ai_company_api.services.identity_login import (
    PERSONAL_ONBOARDING_FAILURE_STEPS,
)
from ai_company_api.services.identity_rollout import (
    IdentityRolloutStage,
    identity_rollout_policy,
    revoke_user_sessions_for_security_rollback,
)
from ai_company_api.services.identity_status_synchronization import (
    maintain_identity_status_synchronization,
)
from ai_company_api.services.authing_ciam_provider import (
    AuthingCiamConfig,
    AuthingCustomerIdentityProvider,
)
from ai_company_api.services.kms_readiness import (
    run_kms_live_smoke,
    run_kms_preflight,
)
from ai_company_api.services.secret_vault import SECRET_VAULT_PROVIDER_ENV
from ai_company_api.services.user_session_credentials import (
    USER_SESSION_COOKIE,
    USER_SESSION_IDLE_SECONDS,
)


DEV_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
DATABASE_URL_ENV = "AI_SCDC_DATABASE_URL"
PUBLIC_ORIGIN_ENV = "AI_SCDC_PUBLIC_ORIGIN"
CORS_ORIGINS_ENV = "AI_SCDC_CORS_ORIGINS"
CORS_ALLOW_CREDENTIALS_ENV = "AI_SCDC_CORS_ALLOW_CREDENTIALS"
AUTHING_APP_HOST_ENV = "AI_SCDC_AUTHING_APP_HOST"
AUTHING_ISSUER_ENV = "AI_SCDC_AUTHING_ISSUER"
AUTHING_APP_ID_ENV = "AI_SCDC_AUTHING_APP_ID"
AUTHING_APP_SECRET_ENV = "AI_SCDC_AUTHING_APP_SECRET"
AUTHING_USER_POOL_ID_ENV = "AI_SCDC_AUTHING_USER_POOL_ID"
AUTHING_USER_POOL_SECRET_ENV = "AI_SCDC_AUTHING_USER_POOL_SECRET"
USER_SESSION_COOKIE_NAME_ENV = "AI_SCDC_USER_SESSION_COOKIE_NAME"
USER_SESSION_COOKIE_SECURE_ENV = "AI_SCDC_USER_SESSION_COOKIE_SECURE"
USER_SESSION_COOKIE_DOMAIN_ENV = "AI_SCDC_USER_SESSION_COOKIE_DOMAIN"
IDENTITY_ROLLOUT_STAGE_ENV = "AI_SCDC_IDENTITY_ROLLOUT_STAGE"
IDENTITY_INTERNAL_EMAIL_ALLOWLIST_ENV = (
    "AI_SCDC_IDENTITY_INTERNAL_EMAIL_ALLOWLIST"
)
IDENTITY_RELEASE_GATES_PASSED_ENV = (
    "AI_SCDC_IDENTITY_RELEASE_GATES_PASSED"
)
IDENTITY_SECURITY_ROLLBACK_ENV = "AI_SCDC_IDENTITY_SECURITY_ROLLBACK"
IDENTITY_SCHEMA_MIGRATION_MODE_ENV = (
    "AI_SCDC_IDENTITY_SCHEMA_MIGRATION_MODE"
)
SECRET_REQUEST_FIELDS = {"secret_value", "token"}
REDACTED_SECRET_INPUT = "[redacted]"


class UserSessionResponseStateMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        clock: Callable[[], datetime],
        user_session_cookie_name: str,
        user_session_cookie_secure: bool,
        user_session_cookie_domain: str | None,
    ) -> None:
        self.app = app
        self.clock = MonotonicAuditClock(clock)
        self.user_session_cookie_name = user_session_cookie_name
        self.user_session_cookie_secure = user_session_cookie_secure
        self.user_session_cookie_domain = user_session_cookie_domain

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = f"request_{secrets.token_hex(16)}"
        correlation_id = f"correlation_{secrets.token_hex(16)}"
        headers_by_name = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope.get("headers", ())
        }
        client = scope.get("client")
        client_ip_address = (
            str(client[0])
            if isinstance(client, tuple) and client
            else None
        )
        audit_context = AuditRequestContext(
            request_id=request_id,
            correlation_id=correlation_id,
            occurred_at=self.clock.next(),
            client_ip_address=client_ip_address,
            user_agent=safe_user_agent(
                headers_by_name.get("user-agent")
            ),
            timestamp_source=self.clock.next,
        )

        async def apply_response_state(message: Message) -> None:
            if message["type"] == "http.response.start":
                state = scope.get("state", {})
                rotation = state.get("user_session_cookie_rotation")
                if rotation is not None:
                    device_session_id, session_secret = rotation
                    cookie_response = Response()
                    cookie_response.set_cookie(
                        key=self.user_session_cookie_name,
                        value=f"{device_session_id}.{session_secret}",
                        max_age=USER_SESSION_IDLE_SECONDS,
                        secure=self.user_session_cookie_secure,
                        httponly=True,
                        samesite="lax",
                        path="/",
                        domain=self.user_session_cookie_domain,
                    )
                    headers = MutableHeaders(scope=message)
                    for name, value in cookie_response.raw_headers:
                        if name == b"set-cookie":
                            headers.append(
                                "set-cookie",
                                value.decode("latin-1"),
                            )
                correlation_id = state.get("identity_correlation_id")
                headers = MutableHeaders(scope=message)
                if (
                    correlation_id is not None
                    and "x-correlation-id" not in headers
                ):
                    headers["x-correlation-id"] = correlation_id
                if "x-request-id" not in headers:
                    headers["x-request-id"] = request_id
                if (
                    "x-correlation-id" not in headers
                    and audit_context.has_events
                ):
                    headers["x-correlation-id"] = (
                        audit_context.correlation_id
                    )
            await send(message)

        with audit_request_context_scope(audit_context):
            await self.app(scope, receive, apply_response_state)


class AICompanyFastAPI(FastAPI):
    def __init__(
        self,
        *args: Any,
        audit_clock: Callable[[], datetime],
        **kwargs: Any,
    ) -> None:
        self.audit_clock = audit_clock
        super().__init__(*args, **kwargs)

    def build_middleware_stack(self) -> ASGIApp:
        return UserSessionResponseStateMiddleware(
            super().build_middleware_stack(),
            clock=self.audit_clock,
            user_session_cookie_name=self.state.user_session_cookie_name,
            user_session_cookie_secure=self.state.user_session_cookie_secure,
            user_session_cookie_domain=self.state.user_session_cookie_domain,
        )


def redact_secret_validation_input(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: REDACTED_SECRET_INPUT
            if str(key).lower() in SECRET_REQUEST_FIELDS
            else redact_secret_validation_input(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secret_validation_input(item) for item in value]
    return value


def validation_error_contains_secret_field(error: dict[str, object]) -> bool:
    location = error.get("loc", ())
    return any(str(part).lower() in SECRET_REQUEST_FIELDS for part in location)


def redact_validation_errors(
    errors: list[dict[str, object]],
) -> list[dict[str, object]]:
    redacted_errors = []
    for error in errors:
        redacted_error = dict(error)
        if "input" in redacted_error:
            if validation_error_contains_secret_field(redacted_error):
                redacted_error["input"] = REDACTED_SECRET_INPUT
            else:
                redacted_error["input"] = redact_secret_validation_input(
                    redacted_error["input"],
                )
        redacted_errors.append(redacted_error)
    return redacted_errors


def create_configured_app() -> FastAPI:
    authentication_policy = authentication_policy_for_environment(
        os.getenv(AUTHENTICATION_ENVIRONMENT_ENV)
    )
    if authentication_policy.environment not in {
        AuthenticationEnvironment.STAGING,
        AuthenticationEnvironment.PRODUCTION,
    }:
        return create_app(
            database_url=os.getenv(DATABASE_URL_ENV, "sqlite:///./dev.db"),
            authentication_policy=authentication_policy,
            identity_rollout_stage=IdentityRolloutStage.DISABLED,
        )

    database_url = os.getenv(DATABASE_URL_ENV, "").strip()
    if not database_url:
        raise ValueError("Production database configuration is incomplete")
    public_origin = os.getenv(PUBLIC_ORIGIN_ENV, "").strip()
    if not public_origin:
        raise ValueError("Web Application Origin configuration is incomplete")

    rollout_stage = os.getenv(
        IDENTITY_ROLLOUT_STAGE_ENV,
        IdentityRolloutStage.DISABLED.value,
    ).strip()
    security_rollback = _environment_boolean(
        IDENTITY_SECURITY_ROLLBACK_ENV,
        default=False,
    )
    provider: CustomerIdentityProvider | None = None
    if not security_rollback:
        authing_values = {
            AUTHING_APP_HOST_ENV: os.getenv(
                AUTHING_APP_HOST_ENV,
                "",
            ).strip(),
            AUTHING_ISSUER_ENV: os.getenv(
                AUTHING_ISSUER_ENV,
                "",
            ).strip(),
            AUTHING_APP_ID_ENV: os.getenv(
                AUTHING_APP_ID_ENV,
                "",
            ).strip(),
            AUTHING_APP_SECRET_ENV: os.getenv(
                AUTHING_APP_SECRET_ENV,
                "",
            ),
            AUTHING_USER_POOL_ID_ENV: os.getenv(
                AUTHING_USER_POOL_ID_ENV,
                "",
            ).strip(),
            AUTHING_USER_POOL_SECRET_ENV: os.getenv(
                AUTHING_USER_POOL_SECRET_ENV,
                "",
            ),
        }
        if any(not value.strip() for value in authing_values.values()):
            raise ValueError("Authing CIAM configuration is incomplete")
        try:
            provider = AuthingCustomerIdentityProvider(
                AuthingCiamConfig(
                    app_host=authing_values[AUTHING_APP_HOST_ENV],
                    issuer=authing_values[AUTHING_ISSUER_ENV],
                    client_id=authing_values[AUTHING_APP_ID_ENV],
                    app_secret=authing_values[AUTHING_APP_SECRET_ENV],
                    user_pool_id=authing_values[AUTHING_USER_POOL_ID_ENV],
                    user_pool_secret=authing_values[
                        AUTHING_USER_POOL_SECRET_ENV
                    ],
                )
            )
        except ValueError as exc:
            raise ValueError(
                "Authing CIAM configuration is not valid"
            ) from exc

    return create_app(
        database_url=database_url,
        cors_origins=tuple(
            origin.strip()
            for origin in os.getenv(CORS_ORIGINS_ENV, "").split(",")
            if origin.strip()
        ),
        cors_allow_credentials=_environment_boolean(
            CORS_ALLOW_CREDENTIALS_ENV,
            default=False,
        ),
        authentication_policy=authentication_policy,
        customer_identity_provider=provider,
        public_origin=public_origin,
        user_session_cookie_name=os.getenv(
            USER_SESSION_COOKIE_NAME_ENV,
            USER_SESSION_COOKIE,
        ).strip(),
        user_session_cookie_secure=_environment_boolean(
            USER_SESSION_COOKIE_SECURE_ENV,
            default=True,
        ),
        user_session_cookie_domain=(
            os.getenv(USER_SESSION_COOKIE_DOMAIN_ENV, "").strip() or None
        ),
        identity_rollout_stage=rollout_stage,
        identity_release_gates_passed=frozenset(
            _environment_csv(IDENTITY_RELEASE_GATES_PASSED_ENV)
        ),
        identity_internal_email_allowlist=frozenset(
            _environment_csv(IDENTITY_INTERNAL_EMAIL_ALLOWLIST_ENV)
        ),
        identity_security_rollback=security_rollback,
        identity_schema_migration_mode=os.getenv(
            IDENTITY_SCHEMA_MIGRATION_MODE_ENV,
            "apply",
        ).strip(),
    )


def _environment_boolean(name: str, *, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _environment_csv(name: str) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in os.getenv(name, "").split(",")
        if value.strip()
    )


def create_app(
    database_url: str = "sqlite:///./dev.db",
    cors_origins: tuple[str, ...] = DEV_CORS_ORIGINS,
    cors_allow_credentials: bool = False,
    authentication_policy: AuthenticationPolicy | None = None,
    customer_identity_provider: CustomerIdentityProvider | None = None,
    allowed_login_return_destinations: frozenset[str] = frozenset({"/"}),
    allowed_recent_authentication_return_destinations: frozenset[str] = (
        frozenset(
            {
                "/reauthentication/confirm",
                "/reauthentication/revoke-other-sessions",
            }
        )
    ),
    public_origin: str = "https://localhost",
    identity_audit_observer_enabled: bool = False,
    identity_test_support_enabled: bool = False,
    secret_access_audit_observer_enabled: bool = False,
    login_transaction_ttl_seconds: int = 600,
    personal_onboarding_failure_step: str | None = None,
    identity_operator_user_ids: frozenset[str] = frozenset(),
    identity_clock: Callable[[], datetime] = utc_now,
    identity_status_synchronization_poll_seconds: float = 60.0,
    audit_retention_poll_seconds: float = 3600.0,
    audit_retention_failure_step: str | None = None,
    user_session_database_failure: bool = False,
    device_session_revocation_failure: str | None = None,
    user_session_cookie_name: str = USER_SESSION_COOKIE,
    user_session_cookie_secure: bool = True,
    user_session_cookie_domain: str | None = None,
    identity_rollout_stage: IdentityRolloutStage | str | None = None,
    identity_release_gates_passed: frozenset[str] = frozenset(),
    identity_internal_email_allowlist: frozenset[str] = frozenset(),
    identity_security_rollback: bool = False,
    identity_schema_migration_mode: str = "apply",
) -> FastAPI:
    resolved_authentication_policy = (
        authentication_policy
        if authentication_policy is not None
        else authentication_policy_for_environment(
            os.getenv(AUTHENTICATION_ENVIRONMENT_ENV)
        )
    )
    production_identity_environment = (
        resolved_authentication_policy.environment
        in {
            AuthenticationEnvironment.STAGING,
            AuthenticationEnvironment.PRODUCTION,
        }
    )
    resolved_rollout_stage = identity_rollout_stage
    if resolved_rollout_stage is None and customer_identity_provider is None:
        resolved_rollout_stage = IdentityRolloutStage.DISABLED
    resolved_identity_rollout_policy = identity_rollout_policy(
        environment=resolved_authentication_policy.environment,
        stage=resolved_rollout_stage,
        passed_release_gates=identity_release_gates_passed,
        internal_email_allowlist=identity_internal_email_allowlist,
        security_rollback=identity_security_rollback,
    )
    if identity_schema_migration_mode not in {"apply", "dry_run"}:
        raise ValueError("Identity schema migration mode must be apply or dry_run")
    if (
        identity_schema_migration_mode == "dry_run"
        and resolved_identity_rollout_policy.stage
        != IdentityRolloutStage.DISABLED
    ):
        raise ValueError(
            "Identity schema dry run requires the disabled rollout stage"
        )
    if (
        resolved_identity_rollout_policy.security_rollback
        and HumanCredentialType.USER_SESSION
        in resolved_authentication_policy.accepted_human_credentials
    ):
        resolved_authentication_policy = AuthenticationPolicy(
            environment=resolved_authentication_policy.environment,
            accepted_human_credentials=(
                resolved_authentication_policy.accepted_human_credentials
                - {HumanCredentialType.USER_SESSION}
            ),
        )
    production_secret_vault_selected = (
        production_identity_environment
        and os.getenv(SECRET_VAULT_PROVIDER_ENV, "dev").strip().lower()
        != "dev"
    )
    if (
        identity_audit_observer_enabled
        and resolved_authentication_policy.environment
        != AuthenticationEnvironment.TEST
    ):
        raise ValueError(
            "Identity Audit observer is allowed only in the test environment"
        )
    if (
        identity_test_support_enabled
        and resolved_authentication_policy.environment
        != AuthenticationEnvironment.TEST
    ):
        raise ValueError(
            "Identity test support is allowed only in the test environment"
        )
    if (
        secret_access_audit_observer_enabled
        and resolved_authentication_policy.environment
        != AuthenticationEnvironment.TEST
    ):
        raise ValueError(
            "Secret Access Audit observer is allowed only in the test environment"
        )
    if (
        personal_onboarding_failure_step is not None
        and resolved_authentication_policy.environment
        != AuthenticationEnvironment.TEST
    ):
        raise ValueError(
            "Personal onboarding failure injection is allowed only "
            "in the test environment"
        )
    if (
        user_session_database_failure
        and resolved_authentication_policy.environment
        != AuthenticationEnvironment.TEST
    ):
        raise ValueError(
            "User Session database failure injection is allowed only "
            "in the test environment"
        )
    if (
        device_session_revocation_failure is not None
        and resolved_authentication_policy.environment
        != AuthenticationEnvironment.TEST
    ):
        raise ValueError(
            "Device Session revocation failure injection is allowed "
            "only in the test environment"
        )
    if (
        audit_retention_failure_step is not None
        and resolved_authentication_policy.environment
        != AuthenticationEnvironment.TEST
    ):
        raise ValueError(
            "Audit retention failure injection is allowed only "
            "in the test environment"
        )
    if (
        production_identity_environment
        and database_url.strip().lower().startswith("sqlite")
    ):
        raise ValueError(
            "Production identity configuration requires an authoritative database"
        )
    if production_identity_environment:
        parsed_public_origin = urlparse(public_origin)
        if (
            parsed_public_origin.scheme != "https"
            or not parsed_public_origin.hostname
            or parsed_public_origin.username is not None
            or parsed_public_origin.password is not None
            or parsed_public_origin.path not in {"", "/"}
            or parsed_public_origin.params
            or parsed_public_origin.query
            or parsed_public_origin.fragment
        ):
            raise ValueError(
                "Web Application Origin must be a single HTTPS origin"
            )
    if (
        production_identity_environment
        and not production_secret_vault_selected
    ):
        raise ValueError(
            "Production identity configuration requires a production SecretVault"
        )
    if production_identity_environment:
        secret_vault_preflight = run_kms_preflight()
        if secret_vault_preflight.status == "failed":
            raise ValueError(
                "Production SecretVault configuration is not ready: "
                f"{secret_vault_preflight.message}"
            )
        if not user_session_cookie_secure:
            raise ValueError(
                "Production User Session Cookie must be Secure"
            )
        if not user_session_cookie_name.startswith("__Host-"):
            raise ValueError(
                "Production User Session Cookie name must start with __Host-"
            )
        if user_session_cookie_domain is not None:
            raise ValueError(
                "Production User Session Cookie must not set Domain"
            )
        if cors_allow_credentials:
            raise ValueError(
                "Production CORS must not allow credentials"
            )
    if (
        HumanCredentialType.USER_SESSION
        in resolved_authentication_policy.accepted_human_credentials
        and customer_identity_provider is None
    ):
        raise ValueError(
            "Customer Identity Provider is required when User Sessions are enabled"
        )
    if (
        resolved_identity_rollout_policy.oidc_login_enabled
        and customer_identity_provider is None
    ):
        raise ValueError(
            "Customer Identity Provider is required when OIDC login is enabled"
        )
    if personal_onboarding_failure_step is not None:
        if (
            personal_onboarding_failure_step
            not in PERSONAL_ONBOARDING_FAILURE_STEPS
        ):
            raise ValueError("Unsupported Personal onboarding failure step")
    if device_session_revocation_failure is not None:
        if device_session_revocation_failure not in {
            "database",
            "operation",
        }:
            raise ValueError(
                "Unsupported Device Session revocation failure mode"
            )
    if not 0 < identity_status_synchronization_poll_seconds <= 300:
        raise ValueError(
            "Identity status synchronization poll interval must be "
            "between zero and five minutes"
        )
    if not 0 < audit_retention_poll_seconds <= 86400:
        raise ValueError(
            "Audit retention poll interval must be between zero "
            "and one day"
        )
    if audit_retention_failure_step is not None:
        if audit_retention_failure_step != "before_cleanup":
            raise ValueError(
                "Unsupported audit retention failure step"
            )
    engine = build_engine(database_url)

    def authoritative_database_is_ready(*, initialize: bool) -> bool:
        try:
            if initialize:
                init_db(engine)
            else:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    def identity_schema_state() -> tuple[bool, tuple[str, ...]]:
        try:
            pending_actions = plan_identity_schema_migration(engine)
        except Exception:
            return False, ()
        return not pending_actions, pending_actions

    def production_secret_vault_is_ready() -> bool:
        if not production_secret_vault_selected:
            return True
        try:
            readiness = run_kms_live_smoke()
        except Exception:
            return False
        return readiness.status == "passed"

    def customer_identity_provider_is_ready() -> bool:
        if (
            HumanCredentialType.USER_SESSION
            not in resolved_authentication_policy.accepted_human_credentials
        ):
            return True
        assert customer_identity_provider is not None
        try:
            customer_identity_provider.check_availability()
        except Exception:
            return False
        return True

    def security_rollback_sessions_are_revoked() -> bool:
        if not resolved_identity_rollout_policy.security_rollback:
            return True
        try:
            with Session(engine) as rollback_session:
                revoke_user_sessions_for_security_rollback(
                    rollback_session,
                    now=identity_clock(),
                )
        except Exception:
            return False
        return True

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.authoritative_database_healthy = (
            authoritative_database_is_ready(
                initialize=identity_schema_migration_mode == "apply"
            )
        )
        (
            app.state.identity_schema_ready,
            app.state.identity_pending_schema_actions,
        ) = identity_schema_state()
        app.state.production_secret_vault_healthy = (
            production_secret_vault_is_ready()
        )
        app.state.customer_identity_provider_healthy = (
            customer_identity_provider_is_ready()
        )
        if (
            HumanCredentialType.DEV_AUTH
            in resolved_authentication_policy.accepted_human_credentials
            and app.state.authoritative_database_healthy
        ):
            _ensure_dev_auth_scope(engine)
        app.state.identity_security_rollback_healthy = (
            not resolved_identity_rollout_policy.security_rollback
        )
        if (
            app.state.authoritative_database_healthy
            and app.state.identity_schema_ready
        ):
            app.state.identity_security_rollback_healthy = (
                security_rollback_sessions_are_revoked()
            )
        app.state.identity_status_synchronization_healthy = True
        app.state.audit_retention_healthy = True
        if not app.state.identity_schema_ready:
            yield
            return
        stop_identity_status_synchronization = Event()
        stop_audit_retention = Event()
        async with create_task_group() as task_group:
            if (
                HumanCredentialType.USER_SESSION
                in resolved_authentication_policy.accepted_human_credentials
            ):
                assert customer_identity_provider is not None

                async def run_identity_status_synchronization() -> None:
                    await maintain_identity_status_synchronization(
                        engine=engine,
                        provider=customer_identity_provider,
                        clock=identity_clock,
                        poll_seconds=(
                            identity_status_synchronization_poll_seconds
                        ),
                        stop_event=stop_identity_status_synchronization,
                        on_health_change=lambda healthy: setattr(
                            app.state,
                            "identity_status_synchronization_healthy",
                            healthy,
                        ),
                    )

                task_group.start_soon(
                    run_identity_status_synchronization
                )

            async def run_audit_retention() -> None:
                await maintain_audit_retention(
                    engine=engine,
                    clock=identity_clock,
                    poll_seconds=audit_retention_poll_seconds,
                    stop_event=stop_audit_retention,
                    on_health_change=lambda healthy: setattr(
                        app.state,
                        "audit_retention_healthy",
                        healthy,
                    ),
                    failure_step=audit_retention_failure_step,
                )

            task_group.start_soon(run_audit_retention)
            try:
                yield
            finally:
                stop_identity_status_synchronization.set()
                stop_audit_retention.set()

    app = AICompanyFastAPI(
        title="AI Company API",
        lifespan=lifespan,
        audit_clock=identity_clock,
    )

    app.state.authentication_policy = resolved_authentication_policy
    app.state.authoritative_database_healthy = False
    app.state.production_secret_vault_healthy = False
    app.state.customer_identity_provider_healthy = False
    app.state.customer_identity_provider = customer_identity_provider
    app.state.allowed_login_return_destinations = allowed_login_return_destinations
    app.state.allowed_recent_authentication_return_destinations = (
        allowed_recent_authentication_return_destinations
    )
    app.state.public_origin = public_origin.rstrip("/")
    app.state.identity_audit_observer_enabled = identity_audit_observer_enabled
    app.state.identity_test_support_enabled = identity_test_support_enabled
    app.state.secret_access_audit_observer_enabled = (
        secret_access_audit_observer_enabled
    )
    app.state.login_transaction_ttl_seconds = login_transaction_ttl_seconds
    app.state.personal_onboarding_failure_step = (
        personal_onboarding_failure_step
    )
    app.state.identity_operator_user_ids = identity_operator_user_ids
    app.state.identity_clock = identity_clock
    app.state.user_session_database_failure = user_session_database_failure
    app.state.device_session_revocation_failure = (
        device_session_revocation_failure
    )
    app.state.user_session_cookie_secure = user_session_cookie_secure
    app.state.user_session_cookie_name = user_session_cookie_name
    app.state.user_session_cookie_domain = user_session_cookie_domain
    app.state.identity_rollout_policy = resolved_identity_rollout_policy
    app.state.identity_schema_migration_mode = identity_schema_migration_mode
    app.state.identity_schema_ready = False
    app.state.identity_pending_schema_actions = ()
    app.state.identity_security_rollback_healthy = True
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=cors_allow_credentials,
    )

    @app.exception_handler(RequestValidationError)
    async def redact_secret_validation_errors(
        _request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder({"detail": redact_validation_errors(exc.errors())}),
        )

    def session_dependency() -> Generator[Session, None, None]:
        yield from session_generator(engine)

    app.dependency_overrides[get_session_dependency] = session_dependency

    @app.get("/health")
    def health(response: Response) -> dict[str, str]:
        app.state.production_secret_vault_healthy = (
            production_secret_vault_is_ready()
        )
        if not app.state.production_secret_vault_healthy:
            response.status_code = 503
            return {
                "status": "unavailable",
                "component": "production_secret_vault",
            }
        app.state.authoritative_database_healthy = (
            authoritative_database_is_ready(
                initialize=(
                    identity_schema_migration_mode == "apply"
                    and not app.state.authoritative_database_healthy
                ),
            )
        )
        if not app.state.authoritative_database_healthy:
            response.status_code = 503
            return {
                "status": "unavailable",
                "component": "authoritative_database",
            }
        (
            app.state.identity_schema_ready,
            app.state.identity_pending_schema_actions,
        ) = identity_schema_state()
        if not app.state.identity_schema_ready:
            response.status_code = 503
            return {
                "status": "unavailable",
                "component": "identity_schema_migration",
            }
        app.state.identity_security_rollback_healthy = (
            security_rollback_sessions_are_revoked()
        )
        if not app.state.identity_security_rollback_healthy:
            response.status_code = 503
            return {
                "status": "unavailable",
                "component": "identity_security_rollback",
            }
        app.state.customer_identity_provider_healthy = (
            customer_identity_provider_is_ready()
        )
        if not app.state.customer_identity_provider_healthy:
            response.status_code = 503
            return {
                "status": "unavailable",
                "component": "customer_identity_provider",
            }
        if not app.state.identity_status_synchronization_healthy:
            response.status_code = 503
            return {
                "status": "degraded",
                "component": "identity_status_synchronization",
            }
        if not app.state.audit_retention_healthy:
            response.status_code = 503
            return {
                "status": "degraded",
                "component": "audit_retention",
            }
        return {"status": "ok"}

    @app.get("/me")
    def me(
        request: Request,
        auth: AuthContext = Depends(
            get_workspace_selection_auth_context_dependency
        ),
        session: Session = Depends(get_session_dependency),
    ) -> DevIdentity:
        selection_required = bool(
            getattr(request.state, "workspace_selection_required", False)
        )
        account = (
            None
            if selection_required
            else session.get(Organization, auth.organization_id)
        )
        workspace = (
            None
            if selection_required
            else session.get(Workspace, auth.workspace_id)
        )
        accessible_statement = (
            select(OrganizationMember, Workspace, Organization)
            .join(
                Workspace,
                Workspace.id == OrganizationMember.workspace_id,
            )
            .join(
                Organization,
                Organization.id == OrganizationMember.organization_id,
            )
            .where(
                OrganizationMember.user_id == auth.user_id,
                OrganizationMember.status == "active",
                Workspace.status == "active",
                Organization.status == "active",
                Workspace.organization_id == Organization.id,
            )
            .order_by(
                Organization.name,
                Organization.id,
                Workspace.name,
                Workspace.id,
            )
        )
        if auth.auth_mode != USER_SESSION_AUTH_MODE:
            accessible_statement = accessible_statement.where(
                OrganizationMember.workspace_id == auth.workspace_id,
                OrganizationMember.organization_id == auth.organization_id,
            )
        accessible_rows = session.exec(accessible_statement).all()
        accessible_accounts: list[AccessibleAccount] = []
        by_account_id: dict[str, AccessibleAccount] = {}
        for membership, accessible_workspace, accessible_account in accessible_rows:
            account_access = by_account_id.get(accessible_account.id)
            if account_access is None:
                account_access = AccessibleAccount(
                    id=accessible_account.id,
                    name=accessible_account.name,
                    kind=accessible_account.account_kind,
                )
                by_account_id[accessible_account.id] = account_access
                accessible_accounts.append(account_access)
            account_access.workspaces.append(
                AccessibleWorkspace(
                    id=accessible_workspace.id,
                    name=accessible_workspace.name,
                    role=membership.role.value,
                )
            )
        return DevIdentity(
            user_id=auth.user_id,
            workspace_id=None if selection_required else auth.workspace_id,
            organization_id=None if selection_required else auth.organization_id,
            roles=(
                []
                if selection_required
                else sorted(role.value for role in auth.roles)
            ),
            auth_mode=auth.auth_mode,
            selection_state=(
                "selection_required"
                if selection_required
                else "selected"
            ),
            accounts=accessible_accounts,
            current_account=(
                None
                if selection_required
                else CurrentAccount(
                    id=auth.organization_id,
                    name=(
                        account.name
                        if account is not None
                        else auth.organization_id
                    ),
                    kind=(
                        account.account_kind
                        if account is not None
                        else AccountKind.LEGACY
                    ),
                )
            ),
            current_workspace=(
                None
                if selection_required
                else CurrentWorkspace(
                    id=auth.workspace_id,
                    name=(
                        workspace.name
                        if workspace is not None
                        else auth.workspace_id
                    ),
                )
            ),
        )

    app.include_router(identity_router)
    app.include_router(router, dependencies=[Depends(get_auth_context_dependency)])
    return app


def _ensure_dev_auth_scope(engine) -> None:
    with Session(engine) as session:
        if session.get(User, DEV_USER_ID) is None:
            session.add(
                User(
                    id=DEV_USER_ID,
                    email="dev@localhost",
                    display_name="Local developer",
                )
            )
        if session.get(Organization, DEV_ORGANIZATION_ID) is None:
            session.add(
                Organization(
                    id=DEV_ORGANIZATION_ID,
                    name="Local development account",
                )
            )
        if session.get(Workspace, DEV_WORKSPACE_ID) is None:
            session.add(
                Workspace(
                    id=DEV_WORKSPACE_ID,
                    organization_id=DEV_ORGANIZATION_ID,
                    name="Local development workspace",
                )
            )
        session.commit()


app = create_configured_app()
