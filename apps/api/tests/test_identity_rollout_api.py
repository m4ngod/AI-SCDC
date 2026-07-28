from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    AccountKind,
    CloudRun,
    ExternalIdentity,
    Organization,
    OrganizationMember,
    User,
    Workspace,
    WorkspaceRole,
)
from ai_company_api.services.auth_context import hash_api_token
from ai_company_api.services.auth_policy import (
    AuthenticationEnvironment,
    AuthenticationPolicy,
    HumanCredentialType,
)
from ai_company_api.services.customer_identity_provider import (
    DeterministicFakeCustomerIdentityProvider,
)
from ai_company_api.services.identity_rollout import (
    REQUIRED_IDENTITY_RELEASE_GATES,
)
from ai_company_api.services.secret_vault import DevSecretVault
from ai_company_api.services.worker_callback_auth import hash_callback_token
from ai_company_llm_gateway.models import ChatProviderResponse, UsageRecord


WEB_CONSOLE_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {
            HumanCredentialType.USER_SESSION,
            HumanCredentialType.WORKSPACE_API_TOKEN,
        }
    ),
)


class MigratedCredentialChatAdapter:
    def complete_chat(self, _request):
        return ChatProviderResponse(
            provider_name="legacy-deepseek",
            model_name="deepseek-chat",
            content="""
            [
              {
                "title": "Preserve migrated credential execution",
                "role_required": "backend",
                "objective": "Use the existing BYOK credential.",
                "acceptance_criteria": ["The migrated route remains usable."],
                "allowed_paths": ["apps/api/**"],
                "required_tests": ["pytest"],
                "risk_level": "low"
              }
            ]
            """,
            usage=UsageRecord(prompt_tokens=8, completion_tokens=5),
        )

    def close(self) -> None:
        return None


def complete_login(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
    *,
    subject: str,
    email: str,
    logout_hint: str | None = None,
):
    login = client.get(
        "/auth/login",
        params={"return_to": "/"},
        follow_redirects=False,
    )
    authorization_url = login.headers["location"]
    state = parse_qs(urlparse(authorization_url).query)["state"][0]
    code = provider.issue_authorization_code(
        authorization_url,
        subject=subject,
        email=email,
        logout_hint=logout_hint,
    )
    return client.get(
        "/auth/callback",
        params={"state": state, "code": code},
        follow_redirects=False,
    )


def seed_legacy_customer(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
    *,
    user_id: str,
    email: str,
    subject: str | None,
) -> None:
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        account = Organization(
            id=f"org_{user_id}",
            name=f"{user_id} legacy account",
            account_kind=AccountKind.LEGACY,
        )
        workspace = Workspace(
            id=f"workspace_{user_id}",
            organization_id=account.id,
            name=f"{user_id} workspace",
        )
        session.add(
            User(
                id=user_id,
                email=email,
                display_name=user_id,
            )
        )
        session.add(account)
        session.add(workspace)
        session.add(
            OrganizationMember(
                id=f"member_{user_id}",
                organization_id=account.id,
                workspace_id=workspace.id,
                user_id=user_id,
                role=WorkspaceRole.OWNER,
            )
        )
        if subject is not None:
            session.add(
                ExternalIdentity(
                    issuer=provider.issuer,
                    subject=subject,
                    user_id=user_id,
                    email=email,
                )
            )
        session.commit()


def create_representative_legacy_snapshot(
    database_url: str,
    *,
    api_token: str,
    worker_callback_token: str,
) -> None:
    engine = build_engine(database_url)
    CloudRun.__table__.create(engine)
    sealed_legacy_credential = DevSecretVault().seal(
        "sk-legacy-byok-1234"
    )
    with engine.begin() as connection:
        for statement in (
            """
            CREATE TABLE user_account (
                id VARCHAR PRIMARY KEY,
                email VARCHAR,
                display_name VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """,
            """
            CREATE TABLE organization (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """,
            """
            CREATE TABLE workspace (
                id VARCHAR PRIMARY KEY,
                organization_id VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """,
            """
            CREATE TABLE organization_member (
                id VARCHAR PRIMARY KEY,
                organization_id VARCHAR NOT NULL,
                workspace_id VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                api_token_hash VARCHAR,
                status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """,
            """
            CREATE TABLE workspace_audit_log (
                id VARCHAR PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                organization_id VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                auth_mode VARCHAR NOT NULL,
                operation VARCHAR NOT NULL,
                resource_type VARCHAR NOT NULL,
                resource_id VARCHAR,
                access_level VARCHAR NOT NULL,
                success BOOLEAN NOT NULL,
                status_code INTEGER NOT NULL,
                error_code VARCHAR,
                metadata_json JSON NOT NULL,
                created_at DATETIME NOT NULL
            )
            """,
            """
            CREATE TABLE secret_access_audit_log (
                id VARCHAR PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                organization_id VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                auth_mode VARCHAR NOT NULL,
                secret_kind VARCHAR NOT NULL,
                secret_id VARCHAR NOT NULL,
                operation VARCHAR NOT NULL,
                access_reason VARCHAR NOT NULL,
                success BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL
            )
            """,
            """
            CREATE TABLE project (
                id VARCHAR PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL,
                created_at DATETIME NOT NULL
            )
            """,
            """
            CREATE TABLE model_provider (
                id VARCHAR PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                provider_type VARCHAR NOT NULL,
                base_url VARCHAR,
                default_headers JSON NOT NULL,
                status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """,
            """
            CREATE TABLE model_credential (
                id VARCHAR PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                provider_id VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                secret_last4 VARCHAR NOT NULL,
                encrypted_secret VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """,
        ):
            connection.execute(text(statement))
        now = "2026-07-01 00:00:00"
        connection.execute(
            text(
                """
                INSERT INTO user_account
                    (id, email, display_name, status, created_at, updated_at)
                VALUES
                    ('user_legacy_snapshot', 'legacy@example.test',
                     'Legacy customer', 'active', :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO organization
                    (id, name, status, created_at, updated_at)
                VALUES
                    ('org_legacy_snapshot', 'Legacy Account', 'active',
                     :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO workspace
                    (id, organization_id, name, status, created_at, updated_at)
                VALUES
                    ('workspace_legacy_snapshot', 'org_legacy_snapshot',
                     'Legacy Workspace', 'active', :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO organization_member
                    (id, organization_id, workspace_id, user_id, role,
                     api_token_hash, status, created_at, updated_at)
                VALUES
                    ('member_legacy_snapshot', 'org_legacy_snapshot',
                     'workspace_legacy_snapshot', 'user_legacy_snapshot',
                     'owner', :api_token_hash, 'active', :now, :now)
                """
            ),
            {
                "api_token_hash": hash_api_token(api_token),
                "now": now,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO workspace_audit_log
                    (id, workspace_id, organization_id, user_id, auth_mode,
                     operation, resource_type, resource_id, access_level,
                     success, status_code, error_code, metadata_json,
                     created_at)
                VALUES
                    ('workspace_audit_legacy_snapshot',
                     'workspace_legacy_snapshot', 'org_legacy_snapshot',
                     'user_legacy_snapshot', 'api_token', 'project.read',
                     'project', 'project_legacy_snapshot',
                     'high_sensitive_read',
                     1, 200, NULL, '{}', :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO secret_access_audit_log
                    (id, workspace_id, organization_id, user_id, auth_mode,
                     secret_kind, secret_id, operation, access_reason,
                     success, created_at)
                VALUES
                    ('secret_access_legacy_snapshot',
                     'workspace_legacy_snapshot', 'org_legacy_snapshot',
                     'user_legacy_snapshot', 'api_token', 'model_credential',
                     'credential_legacy_snapshot', 'open',
                     'legacy_execution', 1, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO project
                    (id, workspace_id, name, description, created_by,
                     created_at)
                VALUES
                    ('project_legacy_snapshot', 'workspace_legacy_snapshot',
                     'Legacy Project', 'Preserved by identity migration',
                     'user_legacy_snapshot', :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO model_provider
                    (id, workspace_id, name, provider_type, base_url,
                     default_headers, status, created_at, updated_at)
                VALUES
                    ('provider_legacy_snapshot',
                     'workspace_legacy_snapshot', 'Legacy DeepSeek',
                     'deepseek', NULL, '{}', 'active', :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO model_credential
                    (id, workspace_id, provider_id, display_name,
                     secret_last4, encrypted_secret, status, created_at,
                     updated_at)
                VALUES
                    ('credential_legacy_snapshot',
                     'workspace_legacy_snapshot',
                     'provider_legacy_snapshot', 'Legacy BYOK',
                     '1234', :encrypted_secret, 'active',
                     :now, :now)
                """
            ),
            {
                "encrypted_secret": (
                    sealed_legacy_credential.encrypted_secret
                ),
                "now": now,
            },
        )
    with Session(engine) as session:
        session.add(
            CloudRun(
                id="cloud_run_legacy_snapshot",
                workspace_id="workspace_legacy_snapshot",
                project_id="project_legacy_snapshot",
                task_id="task_legacy_snapshot",
                repo_id="repo_legacy_snapshot",
                head_branch="codex/legacy-worker",
                status="running",
                queue_provider="aliyun_mns",
                remote_worker_kind="aliyun_eci",
                worker_id="legacy-worker",
                lease_id="legacy-worker-lease",
                lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
                callback_token_hash=hash_callback_token(
                    "cloud_run_legacy_snapshot",
                    "legacy-worker",
                    worker_callback_token,
                ),
                callback_token_expires_at=datetime(
                    2099,
                    1,
                    1,
                    tzinfo=timezone.utc,
                ),
            )
        )
        session.commit()


def test_disabled_rollout_is_observable_and_rejects_new_oidc_login() -> None:
    app = create_app(
        database_url="sqlite://",
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=DeterministicFakeCustomerIdentityProvider(),
        identity_rollout_stage="disabled",
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )

    with TestClient(app, base_url="https://testserver") as client:
        rollout = client.get("/auth/rollout-status")
        login = client.get(
            "/auth/login",
            params={"return_to": "/"},
            follow_redirects=False,
        )
        login_audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": login.headers["x-correlation-id"]},
        )

    assert rollout.status_code == 200
    assert rollout.json() == {
        "stage": "disabled",
        "oidc_login_enabled": False,
        "cookie_authentication_enabled": True,
        "self_registration": "disabled",
        "schema_migration": {
            "mode": "apply",
            "schema_ready": True,
            "pending_actions": [],
        },
        "release_gates": {
            "passed": [],
            "missing": [
                "api_token_rbac_regression",
                "browser_cookie_csrf_e2e",
                "dependency_graph_complete",
                "fake_provider_automation",
                "identity_audit_regression",
                "migration_rollback_rehearsal",
                "real_ciam_smoke",
                "secret_access_audit_regression",
                "secret_leak_scan",
                "secret_vault_kms_regression",
                "worker_callback_regression",
                "workspace_audit_regression",
                "workspace_isolation_regression",
            ],
        },
    }
    assert login.status_code == 503
    assert login.json() == {"error": "identity_login_disabled"}
    assert login_audit.status_code == 200
    assert login_audit.json()[0]["event_type"] == "login_failure"
    assert login_audit.json()[0]["reason_code"] == (
        "identity_rollout_disabled"
    )


def test_internal_stage_onboards_only_allowlisted_customers(tmp_path) -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    app = create_app(
        database_url=(
            f"sqlite:///{(tmp_path / 'internal-rollout.db').as_posix()}"
        ),
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="production_internal",
        identity_internal_email_allowlist=frozenset(
            {"internal@example.test"}
        ),
        public_origin="https://testserver",
    )

    with TestClient(app, base_url="https://testserver") as denied_client:
        denied = complete_login(
            denied_client,
            provider,
            subject="not-allowlisted",
            email="customer@example.test",
        )
        denied_me = denied_client.get("/me")

    with TestClient(app, base_url="https://testserver") as allowed_client:
        allowed = complete_login(
            allowed_client,
            provider,
            subject="allowlisted",
            email="INTERNAL@example.test",
        )
        allowed_me = allowed_client.get("/me")

    assert denied.status_code == 403
    assert denied.json() == {
        "error": "identity_rollout_denied",
        "correlation_id": denied.headers["x-correlation-id"],
    }
    assert "__Host-ai_scdc_session=" not in denied.headers.get(
        "set-cookie",
        "",
    )
    assert denied_me.status_code == 401
    assert allowed.status_code == 303
    assert allowed_me.status_code == 200
    assert allowed_me.json()["current_account"]["kind"] == "personal"


def test_existing_beta_stage_accepts_only_existing_or_legacy_customers(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'existing-beta-rollout.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    seed_legacy_customer(
        database_url,
        provider,
        user_id="user_linked_beta",
        email="linked-beta@example.test",
        subject="linked-beta",
    )
    seed_legacy_customer(
        database_url,
        provider,
        user_id="user_unlinked_beta",
        email="unlinked-beta@example.test",
        subject=None,
    )
    app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="existing_beta",
        public_origin="https://testserver",
    )

    with TestClient(app, base_url="https://testserver") as linked_client:
        linked = complete_login(
            linked_client,
            provider,
            subject="linked-beta",
            email="linked-beta@example.test",
        )
        linked_me = linked_client.get("/me")

    with TestClient(app, base_url="https://testserver") as unlinked_client:
        unlinked = complete_login(
            unlinked_client,
            provider,
            subject="unlinked-beta",
            email="unlinked-beta@example.test",
        )

    with TestClient(app, base_url="https://testserver") as new_client:
        new_customer = complete_login(
            new_client,
            provider,
            subject="new-customer",
            email="new-customer@example.test",
        )

    assert linked.status_code == 303
    assert linked_me.status_code == 200
    assert linked_me.json()["current_account"]["kind"] == "legacy"
    assert unlinked.status_code == 409
    assert unlinked.json()["error"] == "account_link_required"
    assert new_customer.status_code == 403
    assert new_customer.json()["error"] == "identity_rollout_denied"


def test_public_registration_fails_closed_until_every_release_gate_passes(
    tmp_path,
) -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    with pytest.raises(
        ValueError,
        match="Public self-registration requires every identity release gate",
    ):
        create_app(
            database_url="sqlite://",
            authentication_policy=WEB_CONSOLE_POLICY,
            customer_identity_provider=provider,
            identity_rollout_stage="public",
            public_origin="https://testserver",
        )

    app = create_app(
        database_url=(
            f"sqlite:///{(tmp_path / 'public-rollout.db').as_posix()}"
        ),
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="public",
        identity_release_gates_passed=REQUIRED_IDENTITY_RELEASE_GATES,
        public_origin="https://testserver",
    )
    with TestClient(app, base_url="https://testserver") as client:
        rollout = client.get("/auth/rollout-status")
        callback = complete_login(
            client,
            provider,
            subject="public-customer",
            email="public-customer@example.test",
        )
        me = client.get("/me")

    assert rollout.status_code == 200
    assert rollout.json()["stage"] == "public"
    assert rollout.json()["self_registration"] == "public"
    assert rollout.json()["release_gates"]["missing"] == []
    assert callback.status_code == 303
    assert me.status_code == 200
    assert me.json()["current_account"]["kind"] == "personal"


def test_security_rollback_revokes_sessions_but_preserves_api_token_access(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'security-rollback.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    enabled_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="public",
        identity_release_gates_passed=REQUIRED_IDENTITY_RELEASE_GATES,
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )
    with TestClient(
        enabled_app,
        base_url="https://testserver",
    ) as enabled_client:
        callback = complete_login(
            enabled_client,
            provider,
            subject="rollback-customer",
            email="rollback-customer@example.test",
        )
        before_rollback = enabled_client.get("/me")
        session_cookie = enabled_client.cookies.get(
            "__Host-ai_scdc_session"
        )

    assert callback.status_code == 303
    assert before_rollback.status_code == 200
    assert session_cookie is not None
    device_session_id = session_cookie.partition(".")[0]
    api_token = "workspace-api-token-survives-rollback"
    engine = build_engine(database_url)
    with Session(engine) as session:
        membership = session.exec(
            select(OrganizationMember).where(
                OrganizationMember.user_id
                == before_rollback.json()["user_id"]
            )
        ).one()
        membership.api_token_hash = hash_api_token(api_token)
        session.add(membership)
        session.commit()

    rollback_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=None,
        identity_rollout_stage="public",
        identity_security_rollback=True,
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )
    with TestClient(
        rollback_app,
        base_url="https://testserver",
    ) as rollback_client:
        rollback_status = rollback_client.get("/auth/rollout-status")
        rollback_login = rollback_client.get(
            "/auth/login",
            params={"return_to": "/"},
            follow_redirects=False,
        )
        rollback_client.cookies.set(
            "__Host-ai_scdc_session",
            session_cookie,
        )
        rejected_cookie = rollback_client.get("/me")
        rollback_client.cookies.clear()
        surviving_api_token = rollback_client.get(
            "/me",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        rollback_audit = rollback_client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": (
                    f"security_rollback_{device_session_id}"
                )
            },
        )

    retry_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=None,
        identity_rollout_stage="disabled",
        identity_security_rollback=True,
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )
    with TestClient(
        retry_app,
        base_url="https://testserver",
    ) as retry_client:
        retried_rollback_audit = retry_client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": (
                    f"security_rollback_{device_session_id}"
                )
            },
        )

    assert rollback_status.status_code == 200
    assert rollback_status.json()["oidc_login_enabled"] is False
    assert rollback_status.json()["cookie_authentication_enabled"] is False
    assert rollback_status.json()["self_registration"] == "disabled"
    assert rollback_login.status_code == 503
    assert rejected_cookie.status_code == 401
    assert rejected_cookie.json()["detail"] == (
        "User Session authentication is disabled"
    )
    assert surviving_api_token.status_code == 200
    assert surviving_api_token.json()["auth_mode"] == "api_token"
    assert surviving_api_token.json()["current_account"]["kind"] == "personal"
    assert rollback_audit.status_code == 200
    assert len(rollback_audit.json()) == 1
    assert rollback_audit.json()[0]["event_type"] == (
        "identity_security_rollback"
    )
    assert rollback_audit.json()[0]["device_session_id"] == device_session_id
    assert retried_rollback_audit.json() == rollback_audit.json()


def test_security_rollback_is_idempotent_across_parallel_app_startup(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'parallel-security-rollback.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    enabled_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="public",
        identity_release_gates_passed=REQUIRED_IDENTITY_RELEASE_GATES,
        public_origin="https://testserver",
    )
    with TestClient(
        enabled_app,
        base_url="https://testserver",
    ) as enabled_client:
        callback = complete_login(
            enabled_client,
            provider,
            subject="parallel-rollback-customer",
            email="parallel-rollback-customer@example.test",
        )
        session_cookie = enabled_client.cookies.get(
            "__Host-ai_scdc_session"
        )

    assert callback.status_code == 303
    assert session_cookie is not None
    device_session_id = session_cookie.partition(".")[0]
    rollback_apps = [
        create_app(
            database_url=database_url,
            authentication_policy=WEB_CONSOLE_POLICY,
            customer_identity_provider=None,
            identity_rollout_stage="public",
            identity_security_rollback=True,
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        )
        for _ in range(2)
    ]
    start_barrier = Barrier(2)

    def start_rollback(app):
        start_barrier.wait(timeout=5)
        with TestClient(app, base_url="https://testserver") as client:
            return client.get("/auth/rollout-status").status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        startup_statuses = list(
            executor.map(start_rollback, rollback_apps)
        )

    inspection_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=None,
        identity_rollout_stage="disabled",
        identity_security_rollback=True,
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )
    with TestClient(
        inspection_app,
        base_url="https://testserver",
    ) as inspection_client:
        rollback_audit = inspection_client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": (
                    f"security_rollback_{device_session_id}"
                )
            },
        )

    assert startup_statuses == [200, 200]
    assert len(rollback_audit.json()) == 1


def test_security_rollback_final_health_sweep_revokes_transition_session(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'rollback-final-sweep.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    stale_non_rollback_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="public",
        identity_release_gates_passed=REQUIRED_IDENTITY_RELEASE_GATES,
        public_origin="https://testserver",
    )
    rollback_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=None,
        identity_rollout_stage="public",
        identity_security_rollback=True,
        public_origin="https://testserver",
    )
    with TestClient(
        stale_non_rollback_app,
        base_url="https://testserver",
    ) as stale_client:
        with TestClient(
            rollback_app,
            base_url="https://testserver",
        ) as rollback_client:
            callback = complete_login(
                stale_client,
                provider,
                subject="transition-window-customer",
                email="transition-window-customer@example.test",
            )
            transition_cookie = stale_client.cookies.get(
                "__Host-ai_scdc_session"
            )
            rollback_client.cookies.set(
                "__Host-ai_scdc_session",
                transition_cookie,
            )
            blocked_during_rollback = rollback_client.get("/me")
            final_sweep = rollback_client.get("/health")

    assert callback.status_code == 303
    assert transition_cookie is not None
    assert blocked_during_rollback.status_code == 401
    assert final_sweep.status_code == 200

    restored_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="public",
        identity_release_gates_passed=REQUIRED_IDENTITY_RELEASE_GATES,
        public_origin="https://testserver",
    )
    with TestClient(
        restored_app,
        base_url="https://testserver",
    ) as restored_client:
        restored_client.cookies.set(
            "__Host-ai_scdc_session",
            transition_cookie,
        )
        rejected_after_restore = restored_client.get("/me")

    assert rejected_after_restore.status_code == 401
    assert rejected_after_restore.json()["detail"] == (
        "User Session is not valid"
    )


def test_security_rollback_rejects_pending_callback_before_provider_use(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'rollback-pending-callback.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    enabled_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="public",
        identity_release_gates_passed=REQUIRED_IDENTITY_RELEASE_GATES,
        public_origin="https://testserver",
    )
    with TestClient(
        enabled_app,
        base_url="https://testserver",
    ) as enabled_client:
        login = enabled_client.get(
            "/auth/login",
            params={"return_to": "/"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="rollback-pending-callback",
            email="rollback-pending-callback@example.test",
        )
        browser_binding = enabled_client.cookies.get(
            "__Host-ai_scdc_login"
        )

    assert browser_binding is not None
    correlation_id = login.headers["x-correlation-id"]
    rollback_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=None,
        identity_rollout_stage="public",
        identity_security_rollback=True,
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )
    with TestClient(
        rollback_app,
        base_url="https://testserver",
    ) as rollback_client:
        rollback_client.cookies.set(
            "__Host-ai_scdc_login",
            browser_binding,
        )
        callback = rollback_client.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        audit = rollback_client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )

    assert callback.status_code == 503
    assert callback.json() == {
        "error": "identity_login_disabled",
        "correlation_id": correlation_id,
    }
    assert callback.headers["x-correlation-id"] == correlation_id
    assert [
        (event["event_type"], event["reason_code"])
        for event in audit.json()
    ] == [("callback_rejected", "identity_rollout_disabled")]


def test_security_rollback_consumes_provider_logout_locally_without_provider(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'rollback-provider-logout.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    enabled_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="public",
        identity_release_gates_passed=REQUIRED_IDENTITY_RELEASE_GATES,
        public_origin="https://testserver",
    )
    with TestClient(
        enabled_app,
        base_url="https://testserver",
    ) as enabled_client:
        login = complete_login(
            enabled_client,
            provider,
            subject="rollback-provider-logout",
            email="rollback-provider-logout@example.test",
            logout_hint="sealed-provider-logout-hint",
        )
        csrf_token = enabled_client.get("/auth/csrf").json()["csrf_token"]
        logout = enabled_client.post(
            "/auth/logout",
            headers={
                "Origin": "https://testserver",
                "X-CSRF-Token": csrf_token,
            },
        )
        continuation_cookie = enabled_client.cookies.get(
            "__Host-ai_scdc_provider_logout"
        )

    assert login.status_code == 303
    assert logout.status_code == 200
    assert continuation_cookie is not None
    correlation_id = logout.headers["x-correlation-id"]
    rollback_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=None,
        identity_rollout_stage="public",
        identity_security_rollback=True,
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )
    with TestClient(
        rollback_app,
        base_url="https://testserver",
    ) as rollback_client:
        rollback_client.cookies.set(
            "__Host-ai_scdc_provider_logout",
            continuation_cookie,
        )
        provider_logout = rollback_client.get(
            "/auth/logout/provider",
            follow_redirects=False,
        )
        audit = rollback_client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )

    assert provider_logout.status_code == 303
    assert provider_logout.headers["location"] == "https://testserver/"
    assert [
        (
            event["event_type"],
            event["outcome"],
            event["reason_code"],
        )
        for event in audit.json()
    ] == [
        (
            "session_signed_out",
            "success",
            "current_device_revoked",
        ),
        (
            "provider_logout",
            "failure",
            "identity_rollout_disabled",
        )
    ]


def test_identity_schema_dry_run_and_retry_preserve_legacy_access(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'legacy-production-snapshot.db').as_posix()}"
    )
    api_token = "legacy-workspace-api-token"
    worker_callback_token = "legacy-worker-callback-token"
    create_representative_legacy_snapshot(
        database_url,
        api_token=api_token,
        worker_callback_token=worker_callback_token,
    )
    opened_api_keys: list[str] = []

    def migrated_adapter_factory(**kwargs):
        opened_api_keys.append(kwargs["api_key"])
        return MigratedCredentialChatAdapter()

    monkeypatch.setattr(
        "ai_company_api.services.repository.MODEL_PLANNER_ADAPTER_FACTORY",
        migrated_adapter_factory,
    )

    dry_run_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=DeterministicFakeCustomerIdentityProvider(),
        identity_rollout_stage="disabled",
        identity_schema_migration_mode="dry_run",
        public_origin="https://testserver",
    )
    with TestClient(dry_run_app, base_url="https://testserver") as client:
        first_dry_run = client.get("/auth/rollout-status")
        second_dry_run = client.get("/auth/rollout-status")
        dry_run_health = client.get("/health")

    assert first_dry_run.status_code == 200
    assert first_dry_run.json() == second_dry_run.json()
    assert first_dry_run.json()["schema_migration"] == {
        "mode": "dry_run",
        "schema_ready": False,
        "pending_actions": [
            "add_account_classification",
            "create_identity_tables",
            "extend_audit_correlation",
        ],
    }
    assert dry_run_health.status_code == 503
    assert dry_run_health.json() == {
        "status": "unavailable",
        "component": "identity_schema_migration",
    }

    migrated_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=DeterministicFakeCustomerIdentityProvider(),
        identity_rollout_stage="disabled",
        public_origin="https://testserver",
    )
    with TestClient(migrated_app, base_url="https://testserver") as client:
        api_token_headers = {"Authorization": f"Bearer {api_token}"}
        migration_status = client.get("/auth/rollout-status")
        legacy_me = client.get(
            "/me",
            headers=api_token_headers,
        )
        legacy_projects = client.get(
            "/projects",
            headers=api_token_headers,
        )
        legacy_credentials = client.get(
            "/model-credentials",
            headers=api_token_headers,
        )
        legacy_route = client.post(
            "/model-routes",
            headers=api_token_headers,
            json={
                "agent_role": "planner",
                "provider_id": "provider_legacy_snapshot",
                "credential_id": "credential_legacy_snapshot",
                "model_name": "deepseek-chat",
                "fallback_models": [],
            },
        )
        legacy_planner_execution = client.post(
            "/projects/project_legacy_snapshot/planner-runs",
            headers=api_token_headers,
            json={"goal": "Verify the migrated BYOK credential"},
        )
        legacy_worker_callback = client.post(
            "/cloud-run-worker/leases/legacy-worker-lease/heartbeat",
            json={
                "worker_id": "legacy-worker",
                "callback_token": worker_callback_token,
                "lease_seconds": 60,
            },
        )

    retried_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=DeterministicFakeCustomerIdentityProvider(),
        identity_rollout_stage="disabled",
        public_origin="https://testserver",
    )
    with TestClient(retried_app, base_url="https://testserver") as client:
        retried_status = client.get("/auth/rollout-status")
        retried_legacy_me = client.get(
            "/me",
            headers={"Authorization": f"Bearer {api_token}"},
        )

    assert migration_status.json()["schema_migration"] == {
        "mode": "apply",
        "schema_ready": True,
        "pending_actions": [],
    }
    assert retried_status.json() == migration_status.json()
    assert legacy_me.status_code == 200
    assert legacy_me.json()["current_account"] == {
        "id": "org_legacy_snapshot",
        "name": "Legacy Account",
        "kind": "legacy",
    }
    assert retried_legacy_me.json() == legacy_me.json()
    assert legacy_projects.status_code == 200
    assert [project["id"] for project in legacy_projects.json()] == [
        "project_legacy_snapshot"
    ]
    assert legacy_credentials.status_code == 200
    assert len(legacy_credentials.json()) == 1
    assert {
        key: legacy_credentials.json()[0][key]
        for key in (
            "id",
            "provider_id",
            "display_name",
            "secret_last4",
            "status",
        )
    } == {
        "id": "credential_legacy_snapshot",
        "provider_id": "provider_legacy_snapshot",
        "display_name": "Legacy BYOK",
        "secret_last4": "1234",
        "status": "active",
    }
    assert "sk-legacy-byok-1234" not in legacy_credentials.text
    assert legacy_route.status_code == 201
    assert legacy_planner_execution.status_code == 201
    assert legacy_planner_execution.json()["planner_kind"] == "model"
    assert opened_api_keys == ["sk-legacy-byok-1234"]
    assert legacy_worker_callback.status_code == 200
    assert (
        legacy_worker_callback.json()["cloud_run"]["id"]
        == "cloud_run_legacy_snapshot"
    )
    assert worker_callback_token not in legacy_worker_callback.text

    provider = DeterministicFakeCustomerIdentityProvider()
    public_app = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="public",
        identity_release_gates_passed=REQUIRED_IDENTITY_RELEASE_GATES,
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
        identity_test_support_enabled=True,
    )
    with TestClient(public_app, base_url="https://testserver") as client:
        onboarding = complete_login(
            client,
            provider,
            subject="post-migration-customer",
            email="post-migration-customer@example.test",
        )
        personal_me = client.get("/me")
        personal_cookie = client.cookies.get("__Host-ai_scdc_session")
        csrf_token = client.get("/auth/csrf").json()["csrf_token"]
        operator_grant = client.post(
            "/auth/test/grant-identity-operator",
            headers={
                "Origin": "https://testserver",
                "X-CSRF-Token": csrf_token,
            },
        )
        legacy_workspace_audit = client.get(
            "/auth/operator/audit-events",
            params={
                "correlation_id": (
                    "legacy_workspace_audit_"
                    "workspace_audit_legacy_snapshot"
                )
            },
        )
        legacy_secret_access_audit = client.get(
            "/auth/operator/audit-events",
            params={
                "correlation_id": (
                    "legacy_secret_access_audit_"
                    "secret_access_legacy_snapshot"
                )
            },
        )

    assert onboarding.status_code == 303
    assert personal_cookie is not None
    assert operator_grant.status_code == 204
    assert legacy_workspace_audit.status_code == 200
    assert [
        event["operation"]
        for event in legacy_workspace_audit.json()["workspace_events"]
    ] == ["project.read"]
    assert legacy_workspace_audit.json()["secret_access_events"] == []
    assert legacy_secret_access_audit.status_code == 200
    assert legacy_secret_access_audit.json()["workspace_events"] == []
    assert [
        event["operation"]
        for event in legacy_secret_access_audit.json()[
            "secret_access_events"
        ]
    ] == ["open"]
    onboarding_correlation_id = onboarding.headers["x-correlation-id"]

    post_onboarding_retry = create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        identity_rollout_stage="public",
        identity_release_gates_passed=REQUIRED_IDENTITY_RELEASE_GATES,
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )
    with TestClient(
        post_onboarding_retry,
        base_url="https://testserver",
    ) as client:
        client.cookies.set("__Host-ai_scdc_session", personal_cookie)
        personal_me_after_retry = client.get("/me")
        device_sessions_after_retry = client.get("/auth/device-sessions")
        onboarding_audit_after_retry = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": onboarding_correlation_id},
        )

    assert personal_me.status_code == 200
    assert personal_me.json()["current_account"]["kind"] == "personal"
    assert personal_me.json()["current_account"] == (
        personal_me_after_retry.json()["current_account"]
    )
    assert len(personal_me_after_retry.json()["accounts"]) == 1
    assert device_sessions_after_retry.status_code == 200
    assert len(device_sessions_after_retry.json()["sessions"]) == 1
    assert [
        event["event_type"]
        for event in onboarding_audit_after_retry.json()
    ].count("onboarding_success") == 1
