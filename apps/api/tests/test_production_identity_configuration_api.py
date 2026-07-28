import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app, create_configured_app
from ai_company_api.models.entities import (
    Organization,
    OrganizationMember,
    User,
    Workspace,
    WorkspaceRole,
)
from ai_company_api.services.auth_context import hash_api_token
from ai_company_api.services.auth_policy import (
    AUTHENTICATION_ENVIRONMENT_ENV,
    AuthenticationEnvironment,
    AuthenticationPolicy,
    HumanCredentialType,
)
from ai_company_api.services.customer_identity_provider import (
    DeterministicFakeCustomerIdentityProvider,
)
from ai_company_api.services.secret_vault import (
    SECRET_VAULT_KMS_KEY_ID_ENV,
    SECRET_VAULT_PROVIDER_ENV,
    set_kms_client_for_tests,
)


PRODUCTION_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.PRODUCTION,
    accepted_human_credentials=frozenset(
        {
            HumanCredentialType.USER_SESSION,
            HumanCredentialType.WORKSPACE_API_TOKEN,
        }
    ),
)
STAGING_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.STAGING,
    accepted_human_credentials=frozenset(
        {
            HumanCredentialType.USER_SESSION,
            HumanCredentialType.WORKSPACE_API_TOKEN,
        }
    ),
)
TEST_USER_SESSION_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {HumanCredentialType.USER_SESSION}
    ),
)
TEST_WEB_CONSOLE_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {
            HumanCredentialType.USER_SESSION,
            HumanCredentialType.WORKSPACE_API_TOKEN,
        }
    ),
)


def complete_production_process_environment() -> dict[str, str]:
    repository_root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment.update(
        {
            AUTHENTICATION_ENVIRONMENT_ENV: (
                AuthenticationEnvironment.PRODUCTION.value
            ),
            "AI_SCDC_DATABASE_URL": (
                "postgresql+psycopg://db-user:"
                "database-secret@db.example.test/ai_scdc"
            ),
            "AI_SCDC_PUBLIC_ORIGIN": "https://console.example.test",
            SECRET_VAULT_PROVIDER_ENV: "aliyun_kms",
            SECRET_VAULT_KMS_KEY_ID_ENV: "production-key",
            "AI_SCDC_ALIYUN_REGION_ID": "cn-hangzhou",
            "AI_SCDC_ALIYUN_ACCESS_KEY_ID": "production-access-key",
            "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET": "production-access-secret",
            "AI_SCDC_AUTHING_APP_HOST": "https://tenant.authing.cn",
            "AI_SCDC_AUTHING_ISSUER": "https://tenant.authing.cn/oidc",
            "AI_SCDC_AUTHING_APP_ID": "application-id",
            "AI_SCDC_AUTHING_APP_SECRET": "application-secret",
            "AI_SCDC_AUTHING_USER_POOL_ID": "0123456789abcdef01234567",
            "AI_SCDC_AUTHING_USER_POOL_SECRET": "user-pool-secret",
            "PYTHONPATH": os.pathsep.join(
                str(repository_root / path)
                for path in (
                    Path("apps/api/app"),
                    Path("apps/api/release_tests"),
                    Path("apps/worker/app"),
                    Path("services/llm-gateway/app"),
                )
            ),
        }
    )
    return environment


def run_production_module(
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    repository_root = Path(__file__).resolve().parents[3]
    return subprocess.run(
        [sys.executable, "-c", "import ai_company_api.main"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


class UnusableProductionKms:
    def encrypt(self, key_id: str, plaintext: str) -> str:
        raise RuntimeError(
            "KMS unavailable for production-access-secret "
            f"and {plaintext}"
        )

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        raise AssertionError("decrypt must not follow a failed encrypt")

    def delete(self, key_id: str, ciphertext: str) -> None:
        raise AssertionError("delete must not follow a failed encrypt")


class WorkingProductionKms:
    def encrypt(self, key_id: str, plaintext: str) -> str:
        return f"encrypted:{key_id}:{plaintext}"

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        return ciphertext.removeprefix(f"encrypted:{key_id}:")

    def delete(self, key_id: str, ciphertext: str) -> None:
        return None


class RecoverableProductionKms(WorkingProductionKms):
    def __init__(self) -> None:
        self.available = False

    def encrypt(self, key_id: str, plaintext: str) -> str:
        if not self.available:
            raise RuntimeError("KMS temporarily unavailable")
        return super().encrypt(key_id, plaintext)


def test_production_process_refuses_sqlite_before_serving() -> None:
    with pytest.raises(
        ValueError,
        match="Production identity configuration requires an authoritative database",
    ):
        create_app(
            database_url="sqlite://",
            authentication_policy=PRODUCTION_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
        )


def test_staging_process_refuses_sqlite_before_serving() -> None:
    with pytest.raises(
        ValueError,
        match="Production identity configuration requires an authoritative database",
    ):
        create_app(
            database_url="sqlite:///staging.db",
            authentication_policy=STAGING_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
        )


def test_production_process_refuses_development_secret_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "dev")

    with pytest.raises(
        ValueError,
        match="Production identity configuration requires a production SecretVault",
    ):
        create_app(
            database_url="postgresql+psycopg://db.example.test/ai_scdc",
            authentication_policy=PRODUCTION_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
        )


def test_production_process_refuses_incomplete_secret_vault_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.delenv(SECRET_VAULT_KMS_KEY_ID_ENV, raising=False)
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "credential-id-must-not-leak")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "credential-value-must-not-leak",
    )

    with pytest.raises(ValueError) as exc_info:
        create_app(
            database_url="postgresql+psycopg://db.example.test/ai_scdc",
            authentication_policy=PRODUCTION_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
        )

    message = str(exc_info.value)
    assert "Production SecretVault configuration is not ready" in message
    assert SECRET_VAULT_KMS_KEY_ID_ENV in message
    assert "credential-id-must-not-leak" not in message
    assert "credential-value-must-not-leak" not in message


def test_production_process_refuses_non_secure_session_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )

    with pytest.raises(
        ValueError,
        match="Production User Session Cookie must be Secure",
    ):
        create_app(
            database_url="postgresql+psycopg://db.example.test/ai_scdc",
            authentication_policy=PRODUCTION_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
            user_session_cookie_secure=False,
        )


def test_production_process_refuses_session_cookie_without_host_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )

    with pytest.raises(
        ValueError,
        match="Production User Session Cookie name must start with __Host-",
    ):
        create_app(
            database_url="postgresql+psycopg://db.example.test/ai_scdc",
            authentication_policy=PRODUCTION_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
            user_session_cookie_name="ai_scdc_session",
        )


def test_production_process_refuses_session_cookie_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )

    with pytest.raises(
        ValueError,
        match="Production User Session Cookie must not set Domain",
    ):
        create_app(
            database_url="postgresql+psycopg://db.example.test/ai_scdc",
            authentication_policy=PRODUCTION_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
            user_session_cookie_domain=".example.test",
        )


def test_production_process_refuses_credentialed_wildcard_cors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )

    with pytest.raises(
        ValueError,
        match="Production CORS must not allow credentials",
    ):
        create_app(
            database_url="postgresql+psycopg://db.example.test/ai_scdc",
            cors_origins=("*",),
            cors_allow_credentials=True,
            authentication_policy=PRODUCTION_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
        )


def test_production_process_refuses_missing_authing_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        AUTHENTICATION_ENVIRONMENT_ENV,
        AuthenticationEnvironment.PRODUCTION.value,
    )
    monkeypatch.setenv(
        "AI_SCDC_DATABASE_URL",
        "postgresql+psycopg://db-user:database-secret@db.example.test/ai_scdc",
    )
    monkeypatch.setenv("AI_SCDC_PUBLIC_ORIGIN", "https://console.example.test")
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )
    for name in (
        "AI_SCDC_AUTHING_APP_HOST",
        "AI_SCDC_AUTHING_ISSUER",
        "AI_SCDC_AUTHING_APP_ID",
        "AI_SCDC_AUTHING_APP_SECRET",
        "AI_SCDC_AUTHING_USER_POOL_ID",
        "AI_SCDC_AUTHING_USER_POOL_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(
        ValueError,
        match="Authing CIAM configuration is incomplete",
    ) as exc_info:
        create_configured_app()

    message = str(exc_info.value)
    assert "database-secret" not in message
    assert "production-access-secret" not in message


def test_local_configured_process_keeps_oidc_login_disabled_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        AUTHENTICATION_ENVIRONMENT_ENV,
        AuthenticationEnvironment.LOCAL.value,
    )
    monkeypatch.setenv("AI_SCDC_DATABASE_URL", "sqlite://")

    app = create_configured_app()

    with TestClient(app) as client:
        status = client.get("/auth/rollout-status")
        login = client.get("/auth/login?return_to=/", follow_redirects=False)

    assert status.status_code == 200
    assert status.json()["stage"] == "disabled"
    assert status.json()["oidc_login_enabled"] is False
    assert login.status_code == 503
    assert login.json() == {"error": "identity_login_disabled"}


def test_production_process_refuses_invalid_authing_configuration_without_secret_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        AUTHENTICATION_ENVIRONMENT_ENV,
        AuthenticationEnvironment.PRODUCTION.value,
    )
    monkeypatch.setenv(
        "AI_SCDC_DATABASE_URL",
        "postgresql+psycopg://db.example.test/ai_scdc",
    )
    monkeypatch.setenv("AI_SCDC_PUBLIC_ORIGIN", "https://console.example.test")
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )
    monkeypatch.setenv(
        "AI_SCDC_AUTHING_APP_HOST",
        "https://tenant.authing.cn",
    )
    monkeypatch.setenv(
        "AI_SCDC_AUTHING_ISSUER",
        "https://different-tenant.authing.cn/oidc",
    )
    monkeypatch.setenv("AI_SCDC_AUTHING_APP_ID", "application-id")
    monkeypatch.setenv(
        "AI_SCDC_AUTHING_APP_SECRET",
        "application-secret-must-not-leak",
    )
    monkeypatch.setenv(
        "AI_SCDC_AUTHING_USER_POOL_ID",
        "0123456789abcdef01234567",
    )
    monkeypatch.setenv(
        "AI_SCDC_AUTHING_USER_POOL_SECRET",
        "user-pool-secret-must-not-leak",
    )

    with pytest.raises(
        ValueError,
        match="Authing CIAM configuration is not valid",
    ) as exc_info:
        create_configured_app()

    message = str(exc_info.value)
    assert "application-secret-must-not-leak" not in message
    assert "user-pool-secret-must-not-leak" not in message


@pytest.mark.parametrize(
    "secret_environment_name",
    (
        "AI_SCDC_AUTHING_APP_SECRET",
        "AI_SCDC_AUTHING_USER_POOL_SECRET",
    ),
)
def test_production_module_refuses_whitespace_only_authing_secrets(
    secret_environment_name: str,
) -> None:
    environment = complete_production_process_environment()
    environment[secret_environment_name] = "   "

    completed = run_production_module(environment)

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0
    assert "Authing CIAM configuration is incomplete" in output
    assert "database-secret" not in output
    assert "production-access-secret" not in output


def test_production_module_entrypoint_uses_configured_identity_deployment() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment.update(
        {
            AUTHENTICATION_ENVIRONMENT_ENV: (
                AuthenticationEnvironment.PRODUCTION.value
            ),
            "AI_SCDC_DATABASE_URL": (
                "postgresql+psycopg://db-user:"
                "database-secret@db.example.test/ai_scdc"
            ),
            "AI_SCDC_PUBLIC_ORIGIN": "https://console.example.test",
            SECRET_VAULT_PROVIDER_ENV: "aliyun_kms",
            SECRET_VAULT_KMS_KEY_ID_ENV: "production-key",
            "AI_SCDC_ALIYUN_REGION_ID": "cn-hangzhou",
            "AI_SCDC_ALIYUN_ACCESS_KEY_ID": "production-access-key",
            "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET": "production-access-secret",
            "PYTHONPATH": os.pathsep.join(
                str(repository_root / path)
                for path in (
                    Path("apps/api/app"),
                    Path("apps/api/release_tests"),
                    Path("apps/worker/app"),
                    Path("services/llm-gateway/app"),
                )
            ),
        }
    )
    for name in (
        "AI_SCDC_AUTHING_APP_HOST",
        "AI_SCDC_AUTHING_ISSUER",
        "AI_SCDC_AUTHING_APP_ID",
        "AI_SCDC_AUTHING_APP_SECRET",
        "AI_SCDC_AUTHING_USER_POOL_ID",
        "AI_SCDC_AUTHING_USER_POOL_SECRET",
    ):
        environment.pop(name, None)

    completed = subprocess.run(
        [sys.executable, "-c", "import ai_company_api.main"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0
    assert "Authing CIAM configuration is incomplete" in output
    assert "database-secret" not in output
    assert "production-access-secret" not in output


def test_production_process_refuses_non_https_web_application_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        AUTHENTICATION_ENVIRONMENT_ENV,
        AuthenticationEnvironment.PRODUCTION.value,
    )
    monkeypatch.setenv(
        "AI_SCDC_DATABASE_URL",
        "postgresql+psycopg://db.example.test/ai_scdc",
    )
    monkeypatch.setenv("AI_SCDC_PUBLIC_ORIGIN", "http://console.example.test")
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )
    monkeypatch.setenv(
        "AI_SCDC_AUTHING_APP_HOST",
        "https://tenant.authing.cn",
    )
    monkeypatch.setenv(
        "AI_SCDC_AUTHING_ISSUER",
        "https://tenant.authing.cn/oidc",
    )
    monkeypatch.setenv("AI_SCDC_AUTHING_APP_ID", "application-id")
    monkeypatch.setenv("AI_SCDC_AUTHING_APP_SECRET", "application-secret")
    monkeypatch.setenv(
        "AI_SCDC_AUTHING_USER_POOL_ID",
        "0123456789abcdef01234567",
    )
    monkeypatch.setenv(
        "AI_SCDC_AUTHING_USER_POOL_SECRET",
        "user-pool-secret",
    )

    with pytest.raises(
        ValueError,
        match="Web Application Origin must be a single HTTPS origin",
    ):
        create_configured_app()


@pytest.mark.parametrize(
    "authentication_environment",
    (
        AuthenticationEnvironment.STAGING,
        AuthenticationEnvironment.PRODUCTION,
    ),
)
def test_correctly_configured_production_module_entrypoint_loads(
    authentication_environment: AuthenticationEnvironment,
) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment.update(
        {
            AUTHENTICATION_ENVIRONMENT_ENV: (
                authentication_environment.value
            ),
            "AI_SCDC_DATABASE_URL": (
                "postgresql+psycopg://db-user:"
                "database-secret@db.example.test/ai_scdc"
            ),
            "AI_SCDC_PUBLIC_ORIGIN": "https://console.example.test",
            SECRET_VAULT_PROVIDER_ENV: "aliyun_kms",
            SECRET_VAULT_KMS_KEY_ID_ENV: "production-key",
            "AI_SCDC_ALIYUN_REGION_ID": "cn-hangzhou",
            "AI_SCDC_ALIYUN_ACCESS_KEY_ID": "production-access-key",
            "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET": "production-access-secret",
            "AI_SCDC_AUTHING_APP_HOST": "https://tenant.authing.cn",
            "AI_SCDC_AUTHING_ISSUER": "https://tenant.authing.cn/oidc",
            "AI_SCDC_AUTHING_APP_ID": "application-id",
            "AI_SCDC_AUTHING_APP_SECRET": "application-secret",
            "AI_SCDC_AUTHING_USER_POOL_ID": "0123456789abcdef01234567",
            "AI_SCDC_AUTHING_USER_POOL_SECRET": "user-pool-secret",
            "PYTHONPATH": os.pathsep.join(
                str(repository_root / path)
                for path in (
                    Path("apps/api/app"),
                    Path("apps/api/release_tests"),
                    Path("apps/worker/app"),
                    Path("services/llm-gateway/app"),
                )
            ),
        }
    )

    completed = subprocess.run(
        [sys.executable, "-c", "import ai_company_api.main"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, output
    assert "database-secret" not in output
    assert "production-access-secret" not in output
    assert "application-secret" not in output
    assert "user-pool-secret" not in output


def test_production_module_refuses_non_secure_cookie_environment() -> None:
    environment = complete_production_process_environment()
    environment["AI_SCDC_USER_SESSION_COOKIE_SECURE"] = "false"

    completed = run_production_module(environment)

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0
    assert "Production User Session Cookie must be Secure" in output
    assert "database-secret" not in output
    assert "production-access-secret" not in output


def test_production_module_refuses_cookie_name_without_host_prefix() -> None:
    environment = complete_production_process_environment()
    environment["AI_SCDC_USER_SESSION_COOKIE_NAME"] = "ai_scdc_session"

    completed = run_production_module(environment)

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0
    assert (
        "Production User Session Cookie name must start with __Host-"
        in output
    )
    assert "database-secret" not in output


def test_production_module_refuses_cookie_domain_environment() -> None:
    environment = complete_production_process_environment()
    environment["AI_SCDC_USER_SESSION_COOKIE_DOMAIN"] = ".example.test"

    completed = run_production_module(environment)

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0
    assert "Production User Session Cookie must not set Domain" in output
    assert "database-secret" not in output


def test_production_security_rollback_loads_without_authing_credentials() -> None:
    environment = complete_production_process_environment()
    environment["AI_SCDC_IDENTITY_SECURITY_ROLLBACK"] = "true"
    environment["AI_SCDC_IDENTITY_ROLLOUT_STAGE"] = "public"
    for name in (
        "AI_SCDC_AUTHING_APP_HOST",
        "AI_SCDC_AUTHING_ISSUER",
        "AI_SCDC_AUTHING_APP_ID",
        "AI_SCDC_AUTHING_APP_SECRET",
        "AI_SCDC_AUTHING_USER_POOL_ID",
        "AI_SCDC_AUTHING_USER_POOL_SECRET",
    ):
        environment.pop(name, None)

    completed = run_production_module(environment)

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, output
    assert "database-secret" not in output
    assert "production-access-secret" not in output


def test_production_public_registration_refuses_incomplete_release_gates() -> None:
    environment = complete_production_process_environment()
    environment["AI_SCDC_IDENTITY_ROLLOUT_STAGE"] = "public"
    environment["AI_SCDC_IDENTITY_RELEASE_GATES_PASSED"] = (
        "fake_provider_automation,real_ciam_smoke"
    )

    completed = run_production_module(environment)

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0
    assert (
        "Public self-registration requires every identity release gate"
        in output
    )
    assert "database-secret" not in output
    assert "production-access-secret" not in output
    assert "application-secret" not in output
    assert "user-pool-secret" not in output


def test_production_refuses_ciam_test_tenant_rollout_stage() -> None:
    environment = complete_production_process_environment()
    environment["AI_SCDC_IDENTITY_ROLLOUT_STAGE"] = "ciam_test_tenant"

    completed = run_production_module(environment)

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0
    assert "CIAM test-tenant rollout is not allowed in production" in output
    assert "database-secret" not in output
    assert "production-access-secret" not in output
    assert "application-secret" not in output
    assert "user-pool-secret" not in output


@pytest.mark.parametrize(
    "cors_origins",
    ("*", "https://other-origin.example.test"),
)
def test_production_module_refuses_credentialed_cors_environment(
    cors_origins: str,
) -> None:
    environment = complete_production_process_environment()
    environment["AI_SCDC_CORS_ORIGINS"] = cors_origins
    environment["AI_SCDC_CORS_ALLOW_CREDENTIALS"] = "true"

    completed = run_production_module(environment)

    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode != 0
    assert "Production CORS must not allow credentials" in output
    assert "database-secret" not in output


def test_production_readiness_fails_closed_when_authoritative_database_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )
    set_kms_client_for_tests(WorkingProductionKms())
    try:
        app = create_app(
            database_url=(
                "postgresql+psycopg://db-user:database-secret@"
                "127.0.0.1:1/ai_scdc?connect_timeout=1"
            ),
            cors_origins=(),
            authentication_policy=PRODUCTION_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
            public_origin="https://console.example.test",
        )

        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        set_kms_client_for_tests(None)

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "component": "authoritative_database",
    }
    assert "database-secret" not in response.text


def test_production_readiness_fails_closed_when_secret_vault_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )
    set_kms_client_for_tests(UnusableProductionKms())
    try:
        app = create_app(
            database_url=(
                "postgresql+psycopg://db.example.test@127.0.0.1:1/"
                "ai_scdc?connect_timeout=1"
            ),
            cors_origins=(),
            authentication_policy=PRODUCTION_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
            public_origin="https://console.example.test",
        )

        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        set_kms_client_for_tests(None)

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "component": "production_secret_vault",
    }
    assert "production-access-secret" not in response.text


def test_production_readiness_recovers_when_secret_vault_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )
    kms = RecoverableProductionKms()
    set_kms_client_for_tests(kms)
    try:
        app = create_app(
            database_url=(
                "postgresql+psycopg://db.example.test@127.0.0.1:1/"
                "ai_scdc?connect_timeout=1"
            ),
            cors_origins=(),
            authentication_policy=PRODUCTION_POLICY,
            customer_identity_provider=(
                DeterministicFakeCustomerIdentityProvider()
            ),
            public_origin="https://console.example.test",
        )

        with TestClient(app) as client:
            unavailable = client.get("/health")
            kms.available = True
            recovered = client.get("/health")
    finally:
        set_kms_client_for_tests(None)

    assert unavailable.status_code == 503
    assert unavailable.json()["component"] == "production_secret_vault"
    assert recovered.status_code == 503
    assert recovered.json()["component"] == "authoritative_database"


def test_production_readiness_reports_customer_identity_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    provider.set_unavailable_for("discovery")
    set_kms_client_for_tests(WorkingProductionKms())
    try:
        app = create_app(
            database_url="sqlite://",
            cors_origins=(),
            authentication_policy=TEST_USER_SESSION_POLICY,
            customer_identity_provider=provider,
            public_origin="https://console.example.test",
        )

        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        set_kms_client_for_tests(None)

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "component": "customer_identity_provider",
    }
    assert "production-access-secret" not in response.text


def test_production_readiness_recovers_when_customer_identity_provider_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "production-key")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "production-access-key")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "production-access-secret",
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    provider.set_unavailable()
    set_kms_client_for_tests(WorkingProductionKms())
    try:
        app = create_app(
            database_url="sqlite://",
            cors_origins=(),
            authentication_policy=TEST_USER_SESSION_POLICY,
            customer_identity_provider=provider,
            public_origin="https://console.example.test",
        )

        with TestClient(app) as client:
            unavailable = client.get("/health")
            provider.set_unavailable(False)
            recovered = client.get("/health")
    finally:
        set_kms_client_for_tests(None)

    assert unavailable.status_code == 503
    assert unavailable.json()["component"] == "customer_identity_provider"
    assert recovered.status_code == 200
    assert recovered.json() == {"status": "ok"}


def test_ciam_outage_degrades_readiness_without_revoking_workspace_api_token(
    tmp_path: Path,
) -> None:
    token = "scdc_existing_automation_token"
    database_url = (
        f"sqlite:///{(tmp_path / 'ciam-api-token.db').as_posix()}"
    )
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        user = User(
            id="user_existing_automation",
            email="automation@example.test",
            display_name="Automation owner",
        )
        account = Organization(
            id="account_existing_automation",
            name="Personal Account",
        )
        workspace = Workspace(
            id="workspace_existing_automation",
            organization_id=account.id,
            name="Default Workspace",
        )
        membership = OrganizationMember(
            organization_id=account.id,
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.OWNER,
            api_token_hash=hash_api_token(token),
        )
        session.add(user)
        session.add(account)
        session.add(workspace)
        session.add(membership)
        session.commit()

    provider = DeterministicFakeCustomerIdentityProvider()
    provider.set_unavailable()
    app = create_app(
        database_url=database_url,
        cors_origins=(),
        authentication_policy=TEST_WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        public_origin="https://console.example.test",
    )

    with TestClient(
        app,
        base_url="https://console.example.test",
    ) as client:
        readiness = client.get("/health")
        identity = client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        identity_with_dev_header = client.get(
            "/me",
            headers={
                "Authorization": f"Bearer {token}",
                "X-AI-SCDC-User-ID": "forbidden-dev-identity",
            },
        )

    assert readiness.status_code == 503
    assert readiness.json()["component"] == "customer_identity_provider"
    assert identity.status_code == 200
    assert identity.json()["auth_mode"] == "api_token"
    assert identity.json()["workspace_id"] == "workspace_existing_automation"
    assert identity_with_dev_header.status_code == 401
    assert identity_with_dev_header.json()["detail"] == "Dev Auth is not allowed"


def test_ciam_outage_degrades_readiness_without_revoking_user_session(
    tmp_path: Path,
) -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    app = create_app(
        database_url=(
            f"sqlite:///{(tmp_path / 'ciam-user-session.db').as_posix()}"
        ),
        cors_origins=(),
        authentication_policy=TEST_USER_SESSION_POLICY,
        customer_identity_provider=provider,
        public_origin="https://console.example.test",
    )

    with TestClient(
        app,
        base_url="https://console.example.test",
    ) as client:
        login = client.get(
            "/auth/login?return_to=/",
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="existing-session-subject",
            email="session@example.test",
        )
        callback = client.get(
            f"/auth/callback?code={code}&state={state}",
            follow_redirects=False,
        )
        provider.set_unavailable()
        readiness = client.get("/health")
        identity = client.get("/me")

    assert callback.status_code == 303
    assert readiness.status_code == 503
    assert readiness.json()["component"] == "customer_identity_provider"
    assert identity.status_code == 200
    assert identity.json()["auth_mode"] == "user_session"


def test_safe_custom_host_cookie_is_used_for_login_authentication_and_logout(
    tmp_path: Path,
) -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    cookie_name = "__Host-ai_scdc_custom_session"
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'custom-cookie.db').as_posix()}",
        cors_origins=(),
        authentication_policy=TEST_USER_SESSION_POLICY,
        customer_identity_provider=provider,
        public_origin="https://console.example.test",
        user_session_cookie_name=cookie_name,
    )

    with TestClient(
        app,
        base_url="https://console.example.test",
    ) as client:
        login = client.get(
            "/auth/login?return_to=/",
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="custom-cookie-subject",
            email="custom-cookie@example.test",
        )
        callback = client.get(
            f"/auth/callback?code={code}&state={state}",
            follow_redirects=False,
        )
        identity = client.get("/me")
        csrf = client.get("/auth/csrf")
        logout = client.post(
            "/auth/logout",
            headers={
                "Origin": "https://console.example.test",
                "X-CSRF-Token": csrf.json()["csrf_token"],
            },
        )

    assert callback.status_code == 303
    assert f"{cookie_name}=" in callback.headers["set-cookie"]
    assert identity.status_code == 200
    assert identity.json()["auth_mode"] == "user_session"
    assert logout.status_code == 200
    assert f'{cookie_name}=""' in logout.headers["set-cookie"]
