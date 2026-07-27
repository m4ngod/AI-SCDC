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


WEB_ORIGIN = "https://console.example.test"
EMAIL_VERIFICATION_ACR = "urn:ai-scdc:email-verification-code"
USER_SESSION_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {
            HumanCredentialType.USER_SESSION,
            HumanCredentialType.WORKSPACE_API_TOKEN,
        }
    ),
)
PRODUCTION_USER_SESSION_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.PRODUCTION,
    accepted_human_credentials=frozenset(
        {
            HumanCredentialType.USER_SESSION,
            HumanCredentialType.WORKSPACE_API_TOKEN,
        }
    ),
)


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def _build_app(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
    clock: MutableClock,
    *,
    device_session_revocation_failure: str | None = None,
):
    return create_app(
        database_url=database_url,
        authentication_policy=USER_SESSION_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin=WEB_ORIGIN,
        identity_audit_observer_enabled=True,
        identity_test_support_enabled=True,
        identity_clock=clock,
        device_session_revocation_failure=(
            device_session_revocation_failure
        ),
    )


def _sign_in(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
    *,
    user_agent: str,
) -> str:
    login = client.get(
        "/auth/login",
        params={"return_to": "/console"},
        follow_redirects=False,
    )
    authorization_url = login.headers["location"]
    state = parse_qs(urlparse(authorization_url).query)["state"][0]
    code = provider.issue_authorization_code(
        authorization_url,
        subject="subject-revoke-other-sessions",
        email="revoke-other-sessions@example.test",
    )
    callback = client.get(
        "/auth/callback",
        params={"state": state, "code": code},
        headers={"User-Agent": user_agent},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    session_cookie = client.cookies.get("__Host-ai_scdc_session")
    assert session_cookie is not None
    return session_cookie


def _csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/auth/csrf")
    assert response.status_code == 200
    return {
        "Origin": WEB_ORIGIN,
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def _complete_recent_authentication(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
    clock: MutableClock,
) -> object:
    start = client.get(
        "/auth/reauthenticate",
        params={
            "return_to": "/reauthentication/revoke-other-sessions",
        },
        follow_redirects=False,
    )
    assert start.status_code == 303
    authorization_url = start.headers["location"]
    query = parse_qs(urlparse(authorization_url).query)
    assert query["prompt"] == ["login"]
    assert query["max_age"] == ["0"]
    assert query["acr_values"] == [EMAIL_VERIFICATION_ACR]
    code = provider.issue_authorization_code(
        authorization_url,
        subject="subject-revoke-other-sessions",
        email="revoke-other-sessions@example.test",
        authenticated_at=clock(),
        authentication_context=EMAIL_VERIFICATION_ACR,
    )
    return client.get(
        "/auth/callback",
        params={"state": query["state"][0], "code": code},
        follow_redirects=False,
    )


def test_revoke_other_sessions_requires_recent_authentication_without_changes(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'revoke-others-requires-recent.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as first_browser,
        TestClient(app, base_url=WEB_ORIGIN) as second_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
    ):
        _sign_in(
            first_browser,
            provider,
            user_agent="Mozilla/5.0 (Linux) Chrome/126.0.0.0",
        )
        _sign_in(
            second_browser,
            provider,
            user_agent="Mozilla/5.0 (Macintosh) Safari/17.5",
        )
        _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Firefox/127.0",
        )
        clock.advance(timedelta(minutes=15))

        response = current_browser.post(
            "/auth/device-sessions/revoke-others",
            headers=_csrf_headers(current_browser),
        )
        sessions = current_browser.get("/auth/device-sessions")
        first_identity = first_browser.get("/me")
        second_identity = second_browser.get("/me")
        audit = current_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": response.headers["x-correlation-id"],
            },
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "reauthentication_required"}
    assert sessions.status_code == 200
    assert len(sessions.json()["sessions"]) == 3
    assert first_identity.status_code == 200
    assert second_identity.status_code == 200
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "other_device_sessions_revocation_requested",
            "success",
            "customer_requested",
        ),
        (
            "recent_authentication_required",
            "failure",
            "all_other_sessions_revocation",
        ),
        (
            "other_device_sessions_revocation_failed",
            "failure",
            "recent_authentication_required",
        ),
    ]


def test_confirming_after_recent_authentication_revokes_only_other_sessions(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'revoke-others-success.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as first_browser,
        TestClient(app, base_url=WEB_ORIGIN) as second_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
        TestClient(app, base_url=WEB_ORIGIN) as audit_client,
    ):
        first_cookie = _sign_in(
            first_browser,
            provider,
            user_agent="Mozilla/5.0 (Linux) Chrome/126.0.0.0",
        )
        second_cookie = _sign_in(
            second_browser,
            provider,
            user_agent="Mozilla/5.0 (Macintosh) Safari/17.5",
        )
        current_cookie = _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Firefox/127.0",
        )

        callback = _complete_recent_authentication(
            current_browser,
            provider,
            clock,
        )

        assert callback.status_code == 303
        assert callback.headers["location"] == (
            "/reauthentication/revoke-other-sessions"
            "?reauthentication=confirmed"
        )
        assert first_browser.get("/me").status_code == 200
        assert second_browser.get("/me").status_code == 200
        before_confirmation = current_browser.get(
            "/auth/device-sessions"
        )
        assert len(before_confirmation.json()["sessions"]) == 3

        confirmed = current_browser.post(
            "/auth/device-sessions/revoke-others",
            headers=_csrf_headers(current_browser),
        )

        first_next_request = first_browser.get("/me")
        second_next_request = second_browser.get("/me")
        current_next_request = current_browser.get("/me")
        refreshed = current_browser.get("/auth/device-sessions")
        audit = audit_client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": confirmed.headers["x-correlation-id"],
            },
        )

    assert confirmed.status_code == 204
    assert first_next_request.status_code == 401
    assert second_next_request.status_code == 401
    assert current_next_request.status_code == 200
    assert [
        (item["id"], item["is_current"])
        for item in refreshed.json()["sessions"]
    ] == [(current_cookie.partition(".")[0], True)]
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "other_device_sessions_revocation_requested",
            "success",
            "customer_requested",
        ),
        (
            "other_device_sessions_revocation_confirmed",
            "success",
            "recent_authentication_confirmed",
        ),
        (
            "other_device_sessions_revoked",
            "success",
            "all_other_active_sessions_revoked",
        ),
    ]
    serialized = (
        confirmed.text
        + refreshed.text
        + audit.text
    )
    assert first_cookie not in serialized
    assert second_cookie not in serialized
    assert current_cookie not in serialized


def test_confirmation_rejects_recent_authentication_at_fifteen_minutes(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'revoke-others-stale.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as other_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
    ):
        _sign_in(
            other_browser,
            provider,
            user_agent="Mozilla/5.0 Chrome/126.0.0.0",
        )
        _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 Firefox/127.0",
        )
        callback = _complete_recent_authentication(
            current_browser,
            provider,
            clock,
        )
        assert callback.status_code == 303

        clock.advance(timedelta(minutes=15))
        stale = current_browser.post(
            "/auth/device-sessions/revoke-others",
            headers=_csrf_headers(current_browser),
        )
        sessions = current_browser.get("/auth/device-sessions")
        other_identity = other_browser.get("/me")
        audit = current_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": stale.headers["x-correlation-id"],
            },
        )

    assert stale.status_code == 403
    assert stale.json() == {"detail": "reauthentication_required"}
    assert len(sessions.json()["sessions"]) == 2
    assert other_identity.status_code == 200
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "other_device_sessions_revocation_requested",
            "success",
            "customer_requested",
        ),
        (
            "recent_authentication_required",
            "failure",
            "all_other_sessions_revocation",
        ),
        (
            "other_device_sessions_revocation_failed",
            "failure",
            "recent_authentication_required",
        ),
    ]


def test_cancelling_recent_authentication_does_not_revoke_sessions(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'revoke-others-cancelled.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as other_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
    ):
        _sign_in(
            other_browser,
            provider,
            user_agent="Mozilla/5.0 Chrome/126.0.0.0",
        )
        _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 Firefox/127.0",
        )
        start = current_browser.get(
            "/auth/reauthenticate",
            params={
                "return_to": (
                    "/reauthentication/revoke-other-sessions"
                ),
            },
            follow_redirects=False,
        )
        authorization_query = parse_qs(
            urlparse(start.headers["location"]).query
        )
        cancelled = current_browser.get(
            "/auth/callback",
            params={
                "state": authorization_query["state"][0],
                "error": "access_denied",
            },
            follow_redirects=False,
        )
        sessions = current_browser.get("/auth/device-sessions")
        other_identity = other_browser.get("/me")
        audit = current_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": cancelled.headers[
                    "x-correlation-id"
                ],
            },
        )

    assert cancelled.status_code == 303
    assert cancelled.headers["location"] == (
        "/reauthentication/revoke-other-sessions"
        "?reauthentication=cancelled"
    )
    assert len(sessions.json()["sessions"]) == 2
    assert other_identity.status_code == 200
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


@pytest.mark.parametrize(
    ("failure_mode", "expected_reason"),
    [
        ("database", "persistence_failed"),
        ("operation", "operation_failed"),
    ],
)
def test_revocation_failure_rolls_back_every_other_session(
    tmp_path,
    failure_mode: str,
    expected_reason: str,
) -> None:
    database_url = (
        "sqlite:///"
        f"{(tmp_path / f'revoke-others-{failure_mode}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(
        database_url,
        provider,
        clock,
        device_session_revocation_failure=failure_mode,
    )

    with (
        TestClient(app, base_url=WEB_ORIGIN) as first_browser,
        TestClient(app, base_url=WEB_ORIGIN) as second_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
    ):
        first_cookie = _sign_in(
            first_browser,
            provider,
            user_agent="Mozilla/5.0 Chrome/126.0.0.0",
        )
        second_cookie = _sign_in(
            second_browser,
            provider,
            user_agent="Mozilla/5.0 Safari/17.5",
        )
        current_cookie = _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 Firefox/127.0",
        )
        callback = _complete_recent_authentication(
            current_browser,
            provider,
            clock,
        )
        assert callback.status_code == 303

        failed = current_browser.post(
            "/auth/device-sessions/revoke-others",
            headers=_csrf_headers(current_browser),
        )
        sessions = current_browser.get("/auth/device-sessions")
        first_identity = first_browser.get("/me")
        second_identity = second_browser.get("/me")
        audit = current_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": failed.headers["x-correlation-id"],
            },
        )

    assert failed.status_code == 503
    assert failed.json() == {
        "detail": "device_session_revocation_failed"
    }
    assert len(sessions.json()["sessions"]) == 3
    assert first_identity.status_code == 200
    assert second_identity.status_code == 200
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "other_device_sessions_revocation_requested",
            "success",
            "customer_requested",
        ),
        (
            "other_device_sessions_revocation_confirmed",
            "success",
            "recent_authentication_confirmed",
        ),
        (
            "other_device_sessions_revocation_failed",
            "failure",
            expected_reason,
        ),
    ]
    serialized = failed.text + audit.text
    assert first_cookie not in serialized
    assert second_cookie not in serialized
    assert current_cookie not in serialized


def test_workspace_api_token_remains_valid_after_other_sessions_revoked(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'revoke-others-token.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)
    with (
        TestClient(app, base_url=WEB_ORIGIN) as other_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
        TestClient(app, base_url=WEB_ORIGIN) as token_client,
    ):
        _sign_in(
            other_browser,
            provider,
            user_agent="Mozilla/5.0 Chrome/126.0.0.0",
        )
        _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 Firefox/127.0",
        )
        issued_token = current_browser.post(
            "/auth/test/workspace-api-token",
            headers=_csrf_headers(current_browser),
        )
        assert issued_token.status_code == 201
        workspace_api_token = issued_token.json()["token"]

        token_headers = {
            "Authorization": f"Bearer {workspace_api_token}",
        }
        before = token_client.get("/me", headers=token_headers)
        callback = _complete_recent_authentication(
            current_browser,
            provider,
            clock,
        )
        assert callback.status_code == 303
        revoked = current_browser.post(
            "/auth/device-sessions/revoke-others",
            headers=_csrf_headers(current_browser),
        )
        after = token_client.get("/me", headers=token_headers)

    assert before.status_code == 200
    assert before.json()["auth_mode"] == "api_token"
    assert revoked.status_code == 204
    assert after.status_code == 200
    assert after.json()["auth_mode"] == "api_token"
    assert workspace_api_token not in (
        revoked.text + before.text + after.text
    )


def test_revoked_browser_must_verify_email_before_new_session(
    tmp_path,
) -> None:
    database_url = (
        "sqlite:///"
        f"{(tmp_path / 'revoke-others-fresh-login.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as revoked_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
    ):
        revoked_cookie = _sign_in(
            revoked_browser,
            provider,
            user_agent="Mozilla/5.0 Chrome/126.0.0.0",
        )
        _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 Firefox/127.0",
        )
        callback = _complete_recent_authentication(
            current_browser,
            provider,
            clock,
        )
        assert callback.status_code == 303
        confirmed = current_browser.post(
            "/auth/device-sessions/revoke-others",
            headers=_csrf_headers(current_browser),
        )
        assert confirmed.status_code == 204
        assert revoked_browser.get("/me").status_code == 401
        revoked_browser.cookies.delete("__Host-ai_scdc_session")

        first_login = revoked_browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        first_authorization_url = first_login.headers["location"]
        first_query = parse_qs(
            urlparse(first_authorization_url).query
        )
        assert first_query["prompt"] == ["login"]
        assert first_query["max_age"] == ["0"]
        assert first_query["acr_values"] == [EMAIL_VERIFICATION_ACR]
        unverified_code = provider.issue_authorization_code(
            first_authorization_url,
            subject="subject-revoke-other-sessions",
            email="revoke-other-sessions@example.test",
            satisfy_requested_authentication=False,
        )
        unverified = revoked_browser.get(
            "/auth/callback",
            params={
                "state": first_query["state"][0],
                "code": unverified_code,
            },
            follow_redirects=False,
        )
        sessions_after_rejection = current_browser.get(
            "/auth/device-sessions"
        )

        second_login = revoked_browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        second_authorization_url = second_login.headers["location"]
        second_query = parse_qs(
            urlparse(second_authorization_url).query
        )
        verified_code = provider.issue_authorization_code(
            second_authorization_url,
            subject="subject-revoke-other-sessions",
            email="revoke-other-sessions@example.test",
            authenticated_at=clock(),
            authentication_context=EMAIL_VERIFICATION_ACR,
        )
        verified = revoked_browser.get(
            "/auth/callback",
            params={
                "state": second_query["state"][0],
                "code": verified_code,
            },
            follow_redirects=False,
        )
        new_cookie = revoked_browser.cookies.get(
            "__Host-ai_scdc_session"
        )
        restored_identity = revoked_browser.get("/me")

    assert unverified.status_code == 400
    assert unverified.json()["error"] == "login_callback_rejected"
    assert len(sessions_after_rejection.json()["sessions"]) == 1
    assert verified.status_code == 303
    assert verified.headers["location"] == "/console"
    assert new_cookie is not None
    assert new_cookie != revoked_cookie
    assert restored_identity.status_code == 200


def test_revocation_failure_injection_is_test_only() -> None:
    provider = DeterministicFakeCustomerIdentityProvider()

    with pytest.raises(
        ValueError,
        match=(
            "Device Session revocation failure injection is allowed "
            "only in the test environment"
        ),
    ):
        create_app(
            database_url="sqlite://",
            authentication_policy=PRODUCTION_USER_SESSION_POLICY,
            customer_identity_provider=provider,
            device_session_revocation_failure="database",
        )

    with pytest.raises(
        ValueError,
        match="Identity test support is allowed only in the test environment",
    ):
        create_app(
            database_url="sqlite://",
            authentication_policy=PRODUCTION_USER_SESSION_POLICY,
            customer_identity_provider=provider,
            identity_test_support_enabled=True,
        )

    with pytest.raises(
        ValueError,
        match="Unsupported Device Session revocation failure mode",
    ):
        create_app(
            database_url="sqlite://",
            authentication_policy=USER_SESSION_POLICY,
            customer_identity_provider=provider,
            device_session_revocation_failure="unsupported",
        )
