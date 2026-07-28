from datetime import datetime, timedelta, timezone
import secrets
from time import monotonic, sleep
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from ai_company_api.main import create_app
from ai_company_api.services.auth_policy import (
    AuthenticationEnvironment,
    AuthenticationPolicy,
    HumanCredentialType,
)
from ai_company_api.services.customer_identity_provider import (
    DeterministicFakeCustomerIdentityProvider,
)
from ai_company_api.services.identity_login import (
    RECENT_AUTHENTICATION_EMAIL_ACR,
)


WEB_ORIGIN = "https://console.example.test"
OPERATOR_SUBJECT = "audit-operator"
OPERATOR_EMAIL = "audit-operator@example.test"
AUTHENTICATION_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {HumanCredentialType.USER_SESSION}
    ),
)


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def _build_app(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
    clock: MutableClock,
    *,
    retention_poll_seconds: float = 3600.0,
    retention_failure_step: str | None = None,
):
    app_options = {
        "database_url": database_url,
        "authentication_policy": AUTHENTICATION_POLICY,
        "customer_identity_provider": provider,
        "allowed_login_return_destinations": frozenset({"/console"}),
        "public_origin": WEB_ORIGIN,
        "identity_test_support_enabled": True,
        "identity_clock": clock,
        "audit_retention_poll_seconds": retention_poll_seconds,
    }
    if retention_failure_step is not None:
        app_options["audit_retention_failure_step"] = (
            retention_failure_step
        )
    return create_app(
        **app_options,
    )


def _sign_in(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
    clock: MutableClock,
    *,
    subject: str = OPERATOR_SUBJECT,
    email: str = OPERATOR_EMAIL,
    user_agent: str = "AuditBrowser/1.0",
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
        subject=subject,
        email=email,
        authenticated_at=clock(),
        authentication_context=RECENT_AUTHENTICATION_EMAIL_ACR,
    )
    callback = client.get(
        "/auth/callback",
        params={"state": state, "code": code},
        headers={"User-Agent": user_agent},
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


def _reauthenticate(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
    clock: MutableClock,
) -> None:
    start = client.get(
        "/auth/reauthenticate",
        params={"return_to": "/reauthentication/confirm"},
        follow_redirects=False,
    )
    assert start.status_code == 303
    authorization_url = start.headers["location"]
    state = parse_qs(urlparse(authorization_url).query)["state"][0]
    code = provider.issue_authorization_code(
        authorization_url,
        subject=OPERATOR_SUBJECT,
        email=OPERATOR_EMAIL,
        authenticated_at=clock(),
        authentication_context=RECENT_AUTHENTICATION_EMAIL_ACR,
    )
    callback = client.get(
        "/auth/callback",
        params={"state": state, "code": code},
        follow_redirects=False,
    )
    assert callback.status_code == 303


def _grant_operator(client: TestClient) -> None:
    response = client.post(
        "/auth/test/grant-identity-operator",
        headers=_csrf_headers(client),
    )
    assert response.status_code == 204


def _audit_export(
    client: TestClient,
    *,
    correlation_id: str | None = None,
    identity_event_type: str | None = None,
):
    params = {}
    if correlation_id is not None:
        params["correlation_id"] = correlation_id
    if identity_event_type is not None:
        params["identity_event_type"] = identity_event_type
    response = client.get("/auth/operator/audit-events", params=params)
    assert response.status_code == 200
    return response


def test_cross_boundary_audits_share_request_and_correlation_without_merging(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'audit-correlation.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)
    credential_plaintext = "sk-audit-super-secret-1234"

    with TestClient(app, base_url=WEB_ORIGIN) as browser:
        _sign_in(browser, provider, clock)
        _grant_operator(browser)
        model_provider = browser.post(
            "/model-providers",
            json={
                "name": "Audited provider",
                "provider_type": "deepseek",
            },
            headers=_csrf_headers(browser),
        )
        assert model_provider.status_code == 201
        credential = browser.post(
            "/model-credentials",
            json={
                "provider_id": model_provider.json()["id"],
                "display_name": "Audited credential",
                "secret_value": credential_plaintext,
            },
            headers=_csrf_headers(browser),
        )
        request_id = credential.headers["x-request-id"]
        correlation_id = credential.headers["x-correlation-id"]
        exported = _audit_export(
            browser,
            correlation_id=correlation_id,
        )
        session_cookie = browser.cookies.get("__Host-ai_scdc_session")

    assert credential.status_code == 201
    assert session_cookie is not None
    payload = exported.json()
    assert payload["identity_events"] == []
    assert len(payload["workspace_events"]) == 1
    assert len(payload["secret_access_events"]) == 1
    workspace_event = payload["workspace_events"][0]
    secret_event = payload["secret_access_events"][0]
    assert workspace_event["operation"] == "model_credential.create"
    assert secret_event["operation"] == "create"
    assert {
        workspace_event["request_id"],
        secret_event["request_id"],
    } == {request_id}
    assert {
        workspace_event["correlation_id"],
        secret_event["correlation_id"],
    } == {correlation_id}
    assert "resource_type" in workspace_event
    assert "secret_kind" not in workspace_event
    assert "secret_kind" in secret_event
    assert "resource_type" not in secret_event
    serialized = exported.text + credential.text
    assert credential_plaintext not in serialized
    assert session_cookie not in serialized


def test_identity_audit_before_user_resolution_is_safe_and_traceable(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'audit-pre-user.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)
    unsafe_user_agent = "AuditBrowser/1.0 ghp_must_not_persist"

    with (
        TestClient(app, base_url=WEB_ORIGIN) as anonymous,
        TestClient(app, base_url=WEB_ORIGIN) as operator,
    ):
        rejected = anonymous.get(
            "/auth/login",
            params={"return_to": "https://evil.example.test"},
            headers={"User-Agent": unsafe_user_agent},
            follow_redirects=False,
        )
        _sign_in(operator, provider, clock)
        _grant_operator(operator)
        exported = _audit_export(
            operator,
            correlation_id=rejected.headers["x-correlation-id"],
        )

    assert rejected.status_code == 400
    assert rejected.headers["x-request-id"]
    events = exported.json()["identity_events"]
    assert len(events) == 1
    assert events[0]["request_id"] == rejected.headers["x-request-id"]
    assert events[0]["correlation_id"] == rejected.headers[
        "x-correlation-id"
    ]
    assert events[0]["user_id"] is None
    assert events[0]["workspace_id"] is None
    assert "ghp_must_not_persist" not in exported.text


def test_identity_audit_redacts_real_credential_shapes_from_user_agent(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'audit-credential-shapes.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as anonymous,
        TestClient(app, base_url=WEB_ORIGIN) as operator,
    ):
        _sign_in(operator, provider, clock)
        _grant_operator(operator)
        session_cookie = operator.cookies.get("__Host-ai_scdc_session")
        assert session_cookie is not None
        session_secret = session_cookie.partition(".")[2]
        worker_callback_token = secrets.token_urlsafe(32)
        legacy_workspace_api_token = "legacy-workspace-token-secret"
        unsafe_user_agent = (
            "AuditBrowser/1.0 "
            f"session_secret={session_secret} "
            f"worker_callback={worker_callback_token} "
            f"api_token={legacy_workspace_api_token}"
        )

        rejected = anonymous.get(
            "/auth/login",
            params={"return_to": "https://evil.example.test"},
            headers={"User-Agent": unsafe_user_agent},
            follow_redirects=False,
        )
        exported = _audit_export(
            operator,
            correlation_id=rejected.headers["x-correlation-id"],
        )

    assert rejected.status_code == 400
    assert len(exported.json()["identity_events"]) == 1
    assert exported.json()["identity_events"][0]["user_agent"] == (
        "[redacted]"
    )
    for credential in (
        session_secret,
        worker_callback_token,
        legacy_workspace_api_token,
    ):
        assert credential not in exported.text


def test_retention_cleanup_honors_boundaries_is_repeatable_and_audits_itself(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'audit-retention.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as operator:
        _sign_in(operator, provider, clock)
        _grant_operator(operator)
        correlations = {}
        for age_days in (90, 91, 365, 366):
            fixture = operator.post(
                "/auth/test/audit-retention-fixture",
                json={"age_days": age_days},
                headers=_csrf_headers(operator),
            )
            assert fixture.status_code == 201
            correlations[age_days] = fixture.json()["correlation_id"]

        first_cleanup = operator.post(
            "/auth/operator/audit-retention/cleanup",
            headers=_csrf_headers(operator),
        )
        second_cleanup = operator.post(
            "/auth/operator/audit-retention/cleanup",
            headers=_csrf_headers(operator),
        )
        exports = {
            age_days: _audit_export(
                operator,
                correlation_id=correlation_id,
            ).json()
            for age_days, correlation_id in correlations.items()
        }
        cleanup_audit = _audit_export(
            operator,
            correlation_id=first_cleanup.headers["x-correlation-id"],
        )

    assert first_cleanup.status_code == 200
    assert first_cleanup.json() == {
        "status": "completed",
        "correlation_id": first_cleanup.headers["x-correlation-id"],
        "identity_details_removed": 2,
        "identity_events_deleted": 1,
        "workspace_events_deleted": 1,
        "secret_access_events_deleted": 1,
    }
    assert second_cleanup.status_code == 200
    assert second_cleanup.json() == {
        "status": "completed",
        "correlation_id": second_cleanup.headers["x-correlation-id"],
        "identity_details_removed": 0,
        "identity_events_deleted": 0,
        "workspace_events_deleted": 0,
        "secret_access_events_deleted": 0,
    }

    at_ninety = exports[90]
    assert at_ninety["identity_events"][0]["client_ip_address"]
    assert at_ninety["identity_events"][0]["user_agent"]
    assert len(at_ninety["workspace_events"]) == 1
    assert len(at_ninety["secret_access_events"]) == 1

    after_ninety = exports[91]
    assert after_ninety["identity_events"][0]["client_ip_address"] is None
    assert after_ninety["identity_events"][0]["user_agent"] is None
    assert len(after_ninety["workspace_events"]) == 1
    assert len(after_ninety["secret_access_events"]) == 1

    at_365 = exports[365]
    assert len(at_365["identity_events"]) == 1
    assert len(at_365["workspace_events"]) == 1
    assert len(at_365["secret_access_events"]) == 1
    assert exports[366] == {
        "identity_events": [],
        "workspace_events": [],
        "secret_access_events": [],
    }
    assert [
        (
            event["event_type"],
            event["outcome"],
            event["reason_code"],
        )
        for event in cleanup_audit.json()["identity_events"]
    ] == [
        (
            "audit_retention_cleanup",
            "success",
            "operator_requested",
        )
    ]


def test_daily_cleanup_runs_and_normal_users_cannot_manage_audits(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'audit-daily.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(
        database_url,
        provider,
        clock,
        retention_poll_seconds=0.01,
    )

    with (
        TestClient(app, base_url=WEB_ORIGIN) as operator,
        TestClient(app, base_url=WEB_ORIGIN) as normal_user,
    ):
        _sign_in(operator, provider, clock)
        _grant_operator(operator)
        _sign_in(
            normal_user,
            provider,
            clock,
            subject="normal-audit-user",
            email="normal-audit-user@example.test",
        )
        fixture = operator.post(
            "/auth/test/audit-retention-fixture",
            json={"age_days": 366},
            headers=_csrf_headers(operator),
        )
        correlation_id = fixture.json()["correlation_id"]

        forbidden_read = normal_user.get(
            "/auth/operator/audit-events",
            params={"correlation_id": correlation_id},
        )
        forbidden_delete = operator.delete(
            "/auth/operator/audit-events",
            params={"correlation_id": correlation_id},
            headers=_csrf_headers(operator),
        )

        clock.advance(timedelta(days=1))
        _reauthenticate(operator, provider, clock)
        deadline = monotonic() + 1.5
        while True:
            remaining = _audit_export(
                operator,
                correlation_id=correlation_id,
            ).json()
            if (
                remaining
                == {
                    "identity_events": [],
                    "workspace_events": [],
                    "secret_access_events": [],
                }
                or monotonic() >= deadline
            ):
                break
            sleep(0.01)
        cleanup_events = _audit_export(
            operator,
            identity_event_type="audit_retention_cleanup",
        ).json()["identity_events"]

    assert forbidden_read.status_code == 403
    assert forbidden_delete.status_code in {404, 405}
    assert remaining == {
        "identity_events": [],
        "workspace_events": [],
        "secret_access_events": [],
    }
    assert any(
        event["reason_code"] == "scheduled_daily_cleanup"
        and event["outcome"] == "success"
        for event in cleanup_events
    )


def test_daily_cleanup_failure_is_audited_and_retried_after_recovery(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'audit-daily-failure.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    failing_app = _build_app(
        database_url,
        provider,
        clock,
        retention_poll_seconds=0.01,
        retention_failure_step="before_cleanup",
    )

    with TestClient(failing_app, base_url=WEB_ORIGIN) as client:
        deadline = monotonic() + 1.5
        while True:
            failed_health = client.get("/health")
            if (
                failed_health.status_code == 503
                or monotonic() >= deadline
            ):
                break
            sleep(0.01)

    assert failed_health.status_code == 503
    assert failed_health.json() == {
        "status": "degraded",
        "component": "audit_retention",
    }

    clock.advance(timedelta(hours=2))
    recovered_app = _build_app(
        database_url,
        provider,
        clock,
        retention_poll_seconds=0.01,
    )
    with TestClient(recovered_app, base_url=WEB_ORIGIN) as operator:
        _sign_in(operator, provider, clock)
        _grant_operator(operator)
        deadline = monotonic() + 1.5
        while True:
            cleanup_events = _audit_export(
                operator,
                identity_event_type="audit_retention_cleanup",
            ).json()["identity_events"]
            recovered_health = operator.get("/health")
            outcomes = {
                (event["outcome"], event["reason_code"])
                for event in cleanup_events
            }
            if (
                {
                    ("failure", "scheduled_daily_cleanup_failed"),
                    ("success", "scheduled_daily_cleanup"),
                }.issubset(outcomes)
                or monotonic() >= deadline
            ):
                break
            sleep(0.01)

    assert recovered_health.status_code == 200
    assert (
        "failure",
        "scheduled_daily_cleanup_failed",
    ) in outcomes
    assert ("success", "scheduled_daily_cleanup") in outcomes
