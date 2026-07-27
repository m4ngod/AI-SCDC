from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import pytest

from ai_company_api.main import create_app
from ai_company_api.services.auth_policy import (
    AuthenticationEnvironment,
    AuthenticationPolicy,
    HumanCredentialType,
)
from ai_company_api.services.customer_identity_provider import (
    DeterministicFakeCustomerIdentityProvider,
)
from ai_company_api.services.user_session_credentials import USER_SESSION_COOKIE
from ai_company_api.services.secret_vault import (
    SealedSecret,
    set_secret_vault_for_tests,
)


WEB_ORIGIN = "https://console.example.test"
EMAIL_VERIFICATION_ACR = "urn:ai-scdc:email-verification-code"
TEST_START_TIME = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
USER_SESSION_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {HumanCredentialType.USER_SESSION}
    ),
)


class MutableClock:
    def __init__(self) -> None:
        self.current = TEST_START_TIME

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class RecoverableSecretVault:
    def __init__(self) -> None:
        self.available = False
        self._sequence = 0
        self._values: dict[str, str] = {}

    def _require_available(self) -> None:
        if not self.available:
            raise RuntimeError("KMS unavailable with internal diagnostics")

    def seal(self, secret_value: str) -> SealedSecret:
        self._require_available()
        self._sequence += 1
        ciphertext = f"opaque-kms-ciphertext-{self._sequence}"
        self._values[ciphertext] = secret_value
        return SealedSecret(
            encrypted_secret=ciphertext,
            secret_last4=secret_value[-4:],
        )

    def open(self, encrypted_secret: str) -> str:
        self._require_available()
        return self._values[encrypted_secret]

    def rotate(
        self,
        encrypted_secret: str,
        new_secret_value: str,
    ) -> SealedSecret:
        self.open(encrypted_secret)
        return self.seal(new_secret_value)

    def delete(self, encrypted_secret: str) -> None:
        self.open(encrypted_secret)

    def fingerprint(self, encrypted_secret: str) -> str:
        self.open(encrypted_secret)
        return "opaque-fingerprint"


@pytest.fixture(autouse=True)
def _reset_secret_vault_override():
    set_secret_vault_for_tests(None)
    yield
    set_secret_vault_for_tests(None)


def _build_app(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
    clock: MutableClock,
):
    return create_app(
        database_url=database_url,
        authentication_policy=USER_SESSION_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin=WEB_ORIGIN,
        identity_audit_observer_enabled=True,
        secret_access_audit_observer_enabled=True,
        identity_clock=clock,
    )


def _sign_in(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
    *,
    recent_authentication_proof: bool = True,
) -> None:
    login = client.get(
        "/auth/login",
        params={"return_to": "/console"},
        follow_redirects=False,
    )
    authorization_url = login.headers["location"]
    state = parse_qs(urlparse(authorization_url).query)["state"][0]
    code = provider.issue_authorization_code(
        authorization_url,
        subject="subject-recent-authentication",
        email="recent-authentication@example.test",
        **(
            {
                "authenticated_at": TEST_START_TIME,
                "authentication_context": EMAIL_VERIFICATION_ACR,
            }
            if recent_authentication_proof
            else {}
        ),
    )
    callback = client.get(
        "/auth/callback",
        params={"state": state, "code": code},
        follow_redirects=False,
    )
    assert callback.status_code == 303


def _csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/auth/csrf")
    assert response.status_code == 200
    return {
        "Origin": WEB_ORIGIN,
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def test_stale_user_session_cannot_create_github_credential(tmp_path) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'recent-auth-required.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        clock.advance(timedelta(minutes=15))

        response = client.post(
            "/github-credentials",
            headers=_csrf_headers(client),
            json={
                "display_name": "Production GitHub",
                "token": "ghp_recent_authentication_secret",
            },
        )
        credentials = client.get("/github-credentials")

    assert response.status_code == 403
    assert response.json() == {"detail": "reauthentication_required"}
    assert credentials.status_code == 200
    assert credentials.json() == []


def test_login_without_fresh_email_proof_requires_recent_authentication_for_credentials(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'login-without-recent-proof.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(
            client,
            provider,
            recent_authentication_proof=False,
        )
        identity = client.get("/me")
        denied = client.post(
            "/github-credentials",
            headers=_csrf_headers(client),
            json={
                "display_name": "Must require email verification",
                "token": "ghp_login_without_email_proof",
            },
        )
        credentials = client.get("/github-credentials")

    assert identity.status_code == 200
    assert denied.status_code == 403
    assert denied.json() == {"detail": "reauthentication_required"}
    assert credentials.json() == []


def test_recent_authentication_rotates_session_and_requires_explicit_confirmation(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'recent-auth-success.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        original_cookie = client.cookies.get(USER_SESSION_COOKIE)
        assert original_cookie is not None
        clock.advance(timedelta(minutes=15))
        denied = client.post(
            "/github-credentials",
            headers=_csrf_headers(client),
            json={
                "display_name": "Production GitHub",
                "token": "ghp_recent_authentication_secret",
            },
        )
        assert denied.status_code == 403

        start = client.get(
            "/auth/reauthenticate",
            params={"return_to": "/reauthentication/confirm"},
            follow_redirects=False,
        )

        assert start.status_code == 303
        authorization_url = start.headers["location"]
        authorization_query = parse_qs(urlparse(authorization_url).query)
        assert authorization_query["prompt"] == ["login"]
        assert authorization_query["max_age"] == ["0"]
        assert authorization_query["acr_values"] == [
            "urn:ai-scdc:email-verification-code"
        ]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-recent-authentication",
            email="recent-authentication@example.test",
            authenticated_at=clock(),
            authentication_context=EMAIL_VERIFICATION_ACR,
        )
        callback = client.get(
            "/auth/callback",
            params={
                "state": authorization_query["state"][0],
                "code": code,
            },
            follow_redirects=False,
        )

        assert callback.status_code == 303
        assert callback.headers["location"] == (
            "/reauthentication/confirm?reauthentication=confirmed"
        )
        rotated_cookie = client.cookies.get(USER_SESSION_COOKIE)
        assert rotated_cookie is not None
        assert rotated_cookie != original_cookie
        assert client.get("/github-credentials").json() == []
        repeated_callback = client.get(
            "/auth/callback",
            params={
                "state": authorization_query["state"][0],
                "code": code,
            },
            follow_redirects=False,
        )
        assert repeated_callback.status_code == 303
        assert repeated_callback.headers["location"] == (
            "/reauthentication/confirm?reauthentication=confirmed"
        )

        confirmed = client.post(
            "/github-credentials",
            headers=_csrf_headers(client),
            json={
                "display_name": "Production GitHub",
                "token": "ghp_recent_authentication_secret",
            },
        )
        audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": callback.headers["x-correlation-id"],
            },
        )
        clock.advance(timedelta(minutes=15))
        expired_again = client.post(
            "/github-credentials",
            headers=_csrf_headers(client),
            json={
                "display_name": "Must require verification again",
                "token": "ghp_expired_recent_authentication",
            },
        )
        credentials_after_expiry = client.get("/github-credentials")

    assert confirmed.status_code == 201
    assert confirmed.json()["token_last4"] == "cret"
    assert expired_again.status_code == 403
    assert expired_again.json() == {"detail": "reauthentication_required"}
    assert [item["id"] for item in credentials_after_expiry.json()] == [
        confirmed.json()["id"]
    ]
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "recent_authentication_started",
            "success",
            "forced_email_verification",
        ),
        (
            "session_credential_rotated",
            "success",
            "recent_authentication",
        ),
        (
            "recent_authentication_succeeded",
            "success",
            "email_verification_completed",
        ),
    ]


def test_all_credential_mutations_require_recent_authentication_and_preserve_state(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'all-credential-mutations.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        headers = _csrf_headers(client)
        github_credential = client.post(
            "/github-credentials",
            headers=headers,
            json={
                "display_name": "Original GitHub",
                "token": "ghp_original_secret_1234",
            },
        ).json()
        model_provider = client.post(
            "/model-providers",
            headers=headers,
            json={
                "name": "Recent auth provider",
                "provider_type": "openai_compatible",
                "base_url": "https://models.example.test",
            },
        ).json()
        model_credential = client.post(
            "/model-credentials",
            headers=headers,
            json={
                "provider_id": model_provider["id"],
                "display_name": "Original model secret",
                "secret_value": "sk-original-secret-5678",
            },
        ).json()
        clock.advance(timedelta(minutes=15))

        mutations = [
            client.post(
                "/github-credentials",
                headers=_csrf_headers(client),
                json={
                    "display_name": "New GitHub",
                    "token": "ghp_new_secret_2345",
                },
            ),
            client.put(
                f"/github-credentials/{github_credential['id']}",
                headers=_csrf_headers(client),
                json={
                    "display_name": "Replaced GitHub",
                    "token": "ghp_replaced_secret_3456",
                },
            ),
            client.delete(
                f"/github-credentials/{github_credential['id']}",
                headers=_csrf_headers(client),
            ),
            client.post(
                "/model-credentials",
                headers=_csrf_headers(client),
                json={
                    "provider_id": model_provider["id"],
                    "display_name": "New model secret",
                    "secret_value": "sk-new-secret-6789",
                },
            ),
            client.put(
                f"/model-credentials/{model_credential['id']}",
                headers=_csrf_headers(client),
                json={
                    "provider_id": model_provider["id"],
                    "display_name": "Replaced model secret",
                    "secret_value": "sk-replaced-secret-7890",
                },
            ),
            client.delete(
                f"/model-credentials/{model_credential['id']}",
                headers=_csrf_headers(client),
            ),
        ]
        github_credentials = client.get("/github-credentials").json()
        model_credentials = client.get("/model-credentials").json()

    assert [
        (response.status_code, response.json())
        for response in mutations
    ] == [
        (403, {"detail": "reauthentication_required"}),
    ] * 6
    assert [
        (
            item["id"],
            item["display_name"],
            item["token_last4"],
            item["status"],
        )
        for item in github_credentials
    ] == [
        (
            github_credential["id"],
            "Original GitHub",
            "1234",
            "active",
        )
    ]
    assert [
        (
            item["id"],
            item["display_name"],
            item["secret_last4"],
            item["status"],
        )
        for item in model_credentials
    ] == [
        (
            model_credential["id"],
            "Original model secret",
            "5678",
            "active",
        )
    ]


def test_provider_cancellation_returns_to_recoverable_confirmation_without_mutation(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'recent-auth-cancelled.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        clock.advance(timedelta(minutes=15))
        start = client.get(
            "/auth/reauthenticate",
            params={"return_to": "/reauthentication/confirm"},
            follow_redirects=False,
        )
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

        cancelled = client.get(
            "/auth/callback",
            params={
                "state": state,
                "error": "access_denied",
                "error_description": (
                    "customer cancelled with secret "
                    "ghp_must_never_be_persisted"
                ),
            },
            follow_redirects=False,
        )
        credentials = client.get("/github-credentials")
        audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": cancelled.headers["x-correlation-id"],
            },
        )

    assert cancelled.status_code == 303
    assert cancelled.headers["location"] == (
        "/reauthentication/confirm?reauthentication=cancelled"
    )
    assert credentials.json() == []
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "recent_authentication_started",
            "success",
            "forced_email_verification",
        ),
        (
            "recent_authentication_failed",
            "failure",
            "provider_cancelled",
        ),
    ]
    serialized = f"{cancelled.text}{cancelled.headers}{audit.text}"
    assert "ghp_must_never_be_persisted" not in serialized


@pytest.mark.parametrize(
    (
        "provider_error",
        "redirect_result",
        "event_type",
        "reason_code",
    ),
    [
        (
            "temporarily_unavailable",
            "provider_unavailable",
            "identity_provider_unavailable",
            "provider_temporarily_unavailable",
        ),
        (
            "server_error",
            "provider_unavailable",
            "identity_provider_unavailable",
            "provider_server_error",
        ),
        (
            "invalid_request",
            "failed",
            "recent_authentication_failed",
            "provider_error",
        ),
    ],
)
def test_provider_callback_errors_are_safely_classified_and_recoverable(
    tmp_path,
    provider_error: str,
    redirect_result: str,
    event_type: str,
    reason_code: str,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / f'recent-auth-{provider_error}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        clock.advance(timedelta(minutes=15))
        start = client.get(
            "/auth/reauthenticate",
            params={"return_to": "/reauthentication/confirm"},
            follow_redirects=False,
        )
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

        callback = client.get(
            "/auth/callback",
            params={
                "state": state,
                "error": provider_error,
                "error_description": (
                    "private provider details "
                    "ghp_must_never_be_persisted"
                ),
            },
            follow_redirects=False,
        )
        denied = client.post(
            "/github-credentials",
            headers=_csrf_headers(client),
            json={
                "display_name": "Must not exist",
                "token": "ghp_callback_error_not_stored",
            },
        )
        audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": callback.headers["x-correlation-id"],
            },
        )

    assert callback.status_code == 303
    assert callback.headers["location"] == (
        "/reauthentication/confirm"
        f"?reauthentication={redirect_result}"
    )
    assert denied.status_code == 403
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "recent_authentication_started",
            "success",
            "forced_email_verification",
        ),
        (event_type, "failure", reason_code),
    ]
    serialized = f"{callback.text}{callback.headers}{audit.text}"
    assert "ghp_must_never_be_persisted" not in serialized


@pytest.mark.parametrize(
    ("failure_case", "redirect_result", "event_type", "reason_code"),
    [
        (
            "nonce_mismatch",
            "failed",
            "recent_authentication_failed",
            "nonce_mismatch",
        ),
        (
            "identity_locked",
            "failed",
            "recent_authentication_failed",
            "identity_status_inactive",
        ),
        (
            "provider_unavailable",
            "provider_unavailable",
            "identity_provider_unavailable",
            "token_exchange_unavailable",
        ),
        (
            "authentication_too_old",
            "failed",
            "recent_authentication_failed",
            "authentication_not_fresh",
        ),
        (
            "authentication_method_mismatch",
            "failed",
            "recent_authentication_failed",
            "authentication_method_not_satisfied",
        ),
    ],
)
def test_recent_authentication_callback_failures_recover_without_mutation(
    tmp_path,
    failure_case: str,
    redirect_result: str,
    event_type: str,
    reason_code: str,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / f'recent-auth-{failure_case}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        clock.advance(timedelta(minutes=15))
        start = client.get(
            "/auth/reauthenticate",
            params={"return_to": "/reauthentication/confirm"},
            follow_redirects=False,
        )
        authorization_url = start.headers["location"]
        query = parse_qs(urlparse(authorization_url).query)
        issue_options: dict[str, object] = {
            "authenticated_at": clock(),
            "authentication_context": EMAIL_VERIFICATION_ACR,
        }
        if failure_case == "nonce_mismatch":
            issue_options["nonce"] = "wrong-nonce"
        elif failure_case == "identity_locked":
            issue_options["identity_status"] = "locked"
        elif failure_case == "authentication_too_old":
            issue_options["authenticated_at"] = clock() - timedelta(minutes=2)
        elif failure_case == "authentication_method_mismatch":
            issue_options["authentication_context"] = "urn:example:password"
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-recent-authentication",
            email="recent-authentication@example.test",
            **issue_options,
        )
        if failure_case == "provider_unavailable":
            provider.set_unavailable_for("exchange")

        callback = client.get(
            "/auth/callback",
            params={"state": query["state"][0], "code": code},
            follow_redirects=False,
        )
        denied = client.post(
            "/github-credentials",
            headers=_csrf_headers(client),
            json={
                "display_name": "Must not exist",
                "token": "ghp_must_not_be_stored_4567",
            },
        )
        credentials = client.get("/github-credentials")
        audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": callback.headers["x-correlation-id"],
            },
        )

    assert callback.status_code == 303
    assert callback.headers["location"] == (
        "/reauthentication/confirm"
        f"?reauthentication={redirect_result}"
    )
    assert denied.status_code == 403
    assert credentials.json() == []
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "recent_authentication_started",
            "success",
            "forced_email_verification",
        ),
        (event_type, "failure", reason_code),
    ]


def test_provider_unavailable_at_start_returns_to_recoverable_web_console(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'recent-auth-start-unavailable.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        clock.advance(timedelta(minutes=15))
        provider.set_unavailable_for("discovery")

        start = client.get(
            "/auth/reauthenticate",
            params={"return_to": "/reauthentication/confirm"},
            follow_redirects=False,
        )
        credentials = client.get("/github-credentials")
        audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": start.headers["x-correlation-id"],
            },
        )

    assert start.status_code == 303
    assert start.headers["location"] == (
        "/reauthentication/confirm"
        "?reauthentication=provider_unavailable"
    )
    assert credentials.json() == []
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "identity_provider_unavailable",
            "failure",
            "discovery_unavailable",
        )
    ]


def test_recent_authentication_rejects_unapproved_return_destination(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'recent-auth-return-destination.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        response = client.get(
            "/auth/reauthenticate",
            params={"return_to": "https://attacker.example.test/capture"},
            follow_redirects=False,
        )
        audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": response.headers["x-correlation-id"],
            },
        )

    assert response.status_code == 400
    assert response.json()["error"] == "recent_authentication_failed"
    assert "location" not in response.headers
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "recent_authentication_failed",
            "failure",
            "return_destination_not_allowed",
        )
    ]


def test_kms_unavailability_fails_closed_and_confirmation_can_be_retried(
    tmp_path,
) -> None:
    database_path = tmp_path / "recent-auth-kms-unavailable.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    vault = RecoverableSecretVault()
    set_secret_vault_for_tests(vault)
    app = _build_app(database_url, provider, clock)
    secret_value = "ghp_kms_failure_must_stay_secret_6789"

    with TestClient(
        app,
        base_url=WEB_ORIGIN,
        raise_server_exceptions=False,
    ) as client:
        _sign_in(client, provider)
        failed = client.post(
            "/github-credentials",
            headers=_csrf_headers(client),
            json={
                "display_name": "KMS protected GitHub",
                "token": secret_value,
            },
        )
        assert client.get("/github-credentials").json() == []

        vault.available = True
        retried = client.post(
            "/github-credentials",
            headers=_csrf_headers(client),
            json={
                "display_name": "KMS protected GitHub",
                "token": secret_value,
            },
        )
        audit = client.get(
            "/auth/test/secret-access-audit-events"
        ).json()

    assert failed.status_code == 503
    assert failed.json() == {"detail": "secret_vault_unavailable"}
    assert retried.status_code == 201
    assert retried.json()["token_last4"] == "6789"
    assert [
        (
            event["operation"],
            event["access_reason"],
            event["success"],
        )
        for event in audit
    ] == [
        ("create", "github_credential_create", False),
        ("create", "github_credential_create", True),
    ]
    serialized = " ".join(
        [
            failed.text,
            retried.text,
            *(str(event) for event in audit),
        ]
    )
    assert secret_value not in serialized
    assert "internal diagnostics" not in serialized


def test_replace_and_delete_use_vault_and_record_success_and_failure(
    tmp_path,
) -> None:
    database_path = tmp_path / "recent-auth-vault-mutations.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    vault = RecoverableSecretVault()
    vault.available = True
    set_secret_vault_for_tests(vault)
    app = _build_app(database_url, provider, clock)
    secret_values = {
        "ghp_original_1111",
        "ghp_replaced_2222",
        "ghp_failed_3333",
        "sk-original-4444",
        "sk-replaced-5555",
        "sk-failed-6666",
    }

    with TestClient(
        app,
        base_url=WEB_ORIGIN,
        raise_server_exceptions=False,
    ) as client:
        _sign_in(client, provider)
        headers = _csrf_headers(client)
        github = client.post(
            "/github-credentials",
            headers=headers,
            json={
                "display_name": "Original GitHub",
                "token": "ghp_original_1111",
            },
        ).json()
        model_provider = client.post(
            "/model-providers",
            headers=headers,
            json={
                "name": "Vault mutation provider",
                "provider_type": "openai_compatible",
                "base_url": "https://models.example.test",
            },
        ).json()
        model = client.post(
            "/model-credentials",
            headers=headers,
            json={
                "provider_id": model_provider["id"],
                "display_name": "Original model",
                "secret_value": "sk-original-4444",
            },
        ).json()

        github_replaced = client.put(
            f"/github-credentials/{github['id']}",
            headers=_csrf_headers(client),
            json={
                "display_name": "Replaced GitHub",
                "token": "ghp_replaced_2222",
            },
        )
        model_replaced = client.put(
            f"/model-credentials/{model['id']}",
            headers=_csrf_headers(client),
            json={
                "display_name": "Replaced model",
                "secret_value": "sk-replaced-5555",
            },
        )

        vault.available = False
        failed_responses = [
            client.put(
                f"/github-credentials/{github['id']}",
                headers=_csrf_headers(client),
                json={
                    "display_name": "Must not replace GitHub",
                    "token": "ghp_failed_3333",
                },
            ),
            client.put(
                f"/model-credentials/{model['id']}",
                headers=_csrf_headers(client),
                json={
                    "display_name": "Must not replace model",
                    "secret_value": "sk-failed-6666",
                },
            ),
            client.delete(
                f"/github-credentials/{github['id']}",
                headers=_csrf_headers(client),
            ),
            client.delete(
                f"/model-credentials/{model['id']}",
                headers=_csrf_headers(client),
            ),
        ]
        github_after_failure = client.get("/github-credentials").json()
        model_after_failure = client.get("/model-credentials").json()

        vault.available = True
        github_deleted = client.delete(
            f"/github-credentials/{github['id']}",
            headers=_csrf_headers(client),
        )
        model_deleted = client.delete(
            f"/model-credentials/{model['id']}",
            headers=_csrf_headers(client),
        )
        github_after_delete = client.get("/github-credentials").json()
        model_after_delete = client.get("/model-credentials").json()
        audit = client.get(
            "/auth/test/secret-access-audit-events"
        ).json()

    assert github_replaced.status_code == 200
    assert github_replaced.json()["id"] == github["id"]
    assert github_replaced.json()["token_last4"] == "2222"
    assert model_replaced.status_code == 200
    assert model_replaced.json()["id"] == model["id"]
    assert model_replaced.json()["secret_last4"] == "5555"
    assert [
        (response.status_code, response.json())
        for response in failed_responses
    ] == [
        (503, {"detail": "secret_vault_unavailable"}),
    ] * 4
    assert [
        (
            item["display_name"],
            item["token_last4"],
            item["status"],
        )
        for item in github_after_failure
    ] == [("Replaced GitHub", "2222", "active")]
    assert [
        (
            item["display_name"],
            item["secret_last4"],
            item["status"],
        )
        for item in model_after_failure
    ] == [("Replaced model", "5555", "active")]
    assert github_deleted.status_code == 200
    assert github_deleted.json()["status"] == "deleted"
    assert model_deleted.status_code == 200
    assert model_deleted.json()["status"] == "deleted"
    assert [
        (
            event["secret_kind"],
            event["operation"],
            event["access_reason"],
            event["success"],
        )
        for event in audit
    ] == [
        (
            "github_credential",
            "create",
            "github_credential_create",
            True,
        ),
        (
            "model_credential",
            "create",
            "model_credential_create",
            True,
        ),
        (
            "github_credential",
            "rotate",
            "github_credential_replace",
            True,
        ),
        (
            "model_credential",
            "rotate",
            "model_credential_replace",
            True,
        ),
        (
            "github_credential",
            "rotate",
            "github_credential_replace",
            False,
        ),
        (
            "model_credential",
            "rotate",
            "model_credential_replace",
            False,
        ),
        (
            "github_credential",
            "delete",
            "github_credential_delete",
            False,
        ),
        (
            "model_credential",
            "delete",
            "model_credential_delete",
            False,
        ),
        (
            "github_credential",
            "delete",
            "github_credential_delete",
            True,
        ),
        (
            "model_credential",
            "delete",
            "model_credential_delete",
            True,
        ),
    ]
    assert [
        (item["id"], item["status"])
        for item in github_after_delete
    ] == [(github["id"], "deleted")]
    assert [
        (item["id"], item["status"])
        for item in model_after_delete
    ] == [(model["id"], "deleted")]
    serialized = " ".join(
        [
            github_replaced.text,
            model_replaced.text,
            *(response.text for response in failed_responses),
            github_deleted.text,
            model_deleted.text,
            str(github_after_delete),
            str(model_after_delete),
            *(str(event) for event in audit),
        ]
    )
    assert all(secret not in serialized for secret in secret_values)


def test_stale_recent_authentication_does_not_block_routine_workspace_work(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'recent-auth-routine-work.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        identity = client.get("/me").json()
        workspace_id = identity["workspace_id"]
        clock.advance(timedelta(minutes=15))

        project = client.post(
            "/projects",
            headers=_csrf_headers(client),
            json={
                "name": "Routine project",
                "description": "Does not require Recent Authentication",
            },
        )
        workspace_switch = client.put(
            "/auth/workspace-selection",
            headers=_csrf_headers(client),
            json={"workspace_id": workspace_id},
        )
        projects = client.get("/projects")

    assert project.status_code == 201
    assert workspace_switch.status_code == 204
    assert projects.status_code == 200
    assert [item["id"] for item in projects.json()] == [
        project.json()["id"]
    ]
