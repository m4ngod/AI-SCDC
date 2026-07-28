from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import monotonic, sleep
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
SUBJECT = "subject-identity-status-sync"
EMAIL = "identity-status-sync@example.test"
AUTHENTICATION_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {
            HumanCredentialType.USER_SESSION,
            HumanCredentialType.WORKSPACE_API_TOKEN,
        }
    ),
)


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def _build_app(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
    clock: MutableClock,
    *,
    synchronization_poll_seconds: float = 60.0,
):
    return create_app(
        database_url=database_url,
        authentication_policy=AUTHENTICATION_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin=WEB_ORIGIN,
        identity_audit_observer_enabled=True,
        identity_test_support_enabled=True,
        identity_clock=clock,
        identity_status_synchronization_poll_seconds=(
            synchronization_poll_seconds
        ),
    )


def _sign_in(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
    *,
    user_agent: str,
    subject: str = SUBJECT,
    email: str = EMAIL,
) -> str:
    login = client.get(
        "/auth/login",
        params={"return_to": "/console"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    authorization_url = login.headers["location"]
    state = parse_qs(urlparse(authorization_url).query)["state"][0]
    code = provider.issue_authorization_code(
        authorization_url,
        subject=subject,
        email=email,
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


def test_explicit_locked_status_revokes_every_human_credential_at_five_minutes(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-status-locked.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as first_browser,
        TestClient(app, base_url=WEB_ORIGIN) as second_browser,
        TestClient(app, base_url=WEB_ORIGIN) as token_client,
    ):
        _sign_in(
            first_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        _sign_in(
            second_browser,
            provider,
            user_agent="Mozilla/5.0 (Macintosh) Safari/17.5",
        )
        token_response = first_browser.post(
            "/auth/test/workspace-api-token",
            headers=_csrf_headers(first_browser),
        )
        assert token_response.status_code == 201
        api_token = token_response.json()["token"]

        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status="locked",
        )
        clock.advance(timedelta(minutes=4, seconds=59))

        before_deadline = first_browser.get("/me")
        token_before_deadline = token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {api_token}"},
        )

        clock.advance(timedelta(seconds=1))
        triggering_response = first_browser.get("/me")
        correlation_id = triggering_response.headers["x-correlation-id"]
        second_session_after_sync = second_browser.get("/me")
        token_after_sync = token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        audit = token_client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )

    assert before_deadline.status_code == 200
    assert token_before_deadline.status_code == 200
    assert triggering_response.status_code == 401
    assert triggering_response.json() == {
        "detail": "User Session is not valid",
    }
    assert second_session_after_sync.status_code == 401
    assert token_after_sync.status_code == 401
    assert token_after_sync.json() == {"detail": "Invalid API token"}
    assert audit.status_code == 200
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "identity_status_reconciled",
            "success",
            "identity_status_locked",
        ),
        (
            "identity_status_transition_confirmed",
            "success",
            "active_to_locked",
        ),
        (
            "identity_status_user_sessions_revoked",
            "success",
            "identity_status_locked",
        ),
        (
            "identity_status_workspace_api_tokens_revoked",
            "success",
            "identity_status_locked",
        ),
    ]


def test_active_device_session_is_synchronized_without_a_customer_request(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-status-periodic.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(
        database_url,
        provider,
        clock,
        synchronization_poll_seconds=0.01,
    )

    with TestClient(app, base_url=WEB_ORIGIN) as browser:
        _sign_in(
            browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status="locked",
        )
        clock.advance(timedelta(minutes=5))

        synchronized_without_request = (
            provider.wait_for_identity_status_queries(
                minimum=2,
                timeout=1.0,
            )
        )
        provider.set_unavailable()
        deadline = monotonic() + 1.0
        while True:
            access_after_periodic_sync = browser.get("/me")
            if (
                access_after_periodic_sync.status_code == 401
                or monotonic() >= deadline
            ):
                break
            sleep(0.01)

    assert synchronized_without_request
    assert access_after_periodic_sync.status_code == 401


def test_concurrent_due_requests_claim_one_identity_status_check(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-status-concurrent.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as first_browser,
        TestClient(app, base_url=WEB_ORIGIN) as second_browser,
        TestClient(app, base_url=WEB_ORIGIN) as observer,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        _sign_in(
            first_browser,
            provider,
            user_agent="Mozilla/5.0 First",
        )
        _sign_in(
            second_browser,
            provider,
            user_agent="Mozilla/5.0 Second",
        )
        baseline_queries = provider.identity_status_query_count()
        provider.block_next_identity_status_query()
        clock.advance(timedelta(minutes=5))

        request_start = Barrier(3)

        def get_me(client: TestClient):
            request_start.wait()
            return client.get("/me")

        first_request = executor.submit(get_me, first_browser)
        second_request = executor.submit(get_me, second_browser)
        request_start.wait()
        assert provider.wait_for_identity_status_queries(
            minimum=baseline_queries + 1,
            timeout=1.0,
        )
        provider.release_blocked_identity_status_query()
        first_response = first_request.result(timeout=1.0)
        second_response = second_request.result(timeout=1.0)
        claiming_response = (
            first_response
            if "x-correlation-id" in first_response.headers
            else second_response
        )
        audit = observer.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": claiming_response.headers[
                    "x-correlation-id"
                ],
            },
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert (
        provider.identity_status_query_count()
        == baseline_queries + 1
    )
    assert [
        event["event_type"]
        for event in audit.json()
        if event["event_type"] == "identity_status_reconciled"
    ] == ["identity_status_reconciled"]


def test_stale_identity_status_result_cannot_overwrite_a_newer_lock(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-status-stale.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as first_browser,
        TestClient(app, base_url=WEB_ORIGIN) as second_browser,
        TestClient(app, base_url=WEB_ORIGIN) as observer,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        _sign_in(
            first_browser,
            provider,
            user_agent="Mozilla/5.0 First",
        )
        _sign_in(
            second_browser,
            provider,
            user_agent="Mozilla/5.0 Second",
        )
        baseline_queries = provider.identity_status_query_count()
        provider.block_next_identity_status_query()
        clock.advance(timedelta(minutes=5))

        stale_active_request = executor.submit(first_browser.get, "/me")
        assert provider.wait_for_identity_status_queries(
            minimum=baseline_queries + 1,
            timeout=1.0,
        )
        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status="locked",
        )
        clock.advance(timedelta(minutes=5))
        locked_response = second_browser.get("/me")
        provider.release_blocked_identity_status_query()
        stale_response = stale_active_request.result(timeout=1.0)
        locked_audit = observer.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": locked_response.headers[
                    "x-correlation-id"
                ],
            },
        )

    assert locked_response.status_code == 401
    assert stale_response.status_code == 401
    assert [
        (event["event_type"], event["reason_code"])
        for event in locked_audit.json()
    ] == [
        (
            "identity_status_reconciled",
            "identity_status_locked",
        ),
        (
            "identity_status_transition_confirmed",
            "active_to_locked",
        ),
        (
            "identity_status_user_sessions_revoked",
            "identity_status_locked",
        ),
        (
            "identity_status_workspace_api_tokens_revoked",
            "identity_status_locked",
        ),
    ]


def test_identity_status_worker_shutdown_waits_for_current_sweep(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-status-shutdown.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(
        database_url,
        provider,
        clock,
        synchronization_poll_seconds=0.01,
    )
    browser = TestClient(app, base_url=WEB_ORIGIN)
    browser.__enter__()
    _sign_in(
        browser,
        provider,
        user_agent="Mozilla/5.0 Shutdown",
    )
    baseline_queries = provider.identity_status_query_count()
    provider.block_next_identity_status_query()
    clock.advance(timedelta(minutes=5))
    assert provider.wait_for_identity_status_queries(
        minimum=baseline_queries + 1,
        timeout=1.0,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        shutdown = executor.submit(
            browser.__exit__,
            None,
            None,
            None,
        )
        sleep(0.05)
        assert not shutdown.done()
        provider.release_blocked_identity_status_query()
        shutdown.result(timeout=1.0)

    queries_at_shutdown = provider.identity_status_query_count()
    clock.advance(timedelta(days=1))
    sleep(0.05)
    assert provider.identity_status_query_count() == queries_at_shutdown


def test_identity_status_worker_failure_is_visible_and_retried(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-status-health.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(
        database_url,
        provider,
        clock,
        synchronization_poll_seconds=0.01,
    )

    with TestClient(app, base_url=WEB_ORIGIN) as browser:
        _sign_in(
            browser,
            provider,
            user_agent="Mozilla/5.0 Health",
        )
        provider.set_unexpected_failure_for("status")
        clock.advance(timedelta(minutes=5))
        deadline = monotonic() + 1.0
        while True:
            failed_health = browser.get("/health")
            if (
                failed_health.status_code == 503
                or monotonic() >= deadline
            ):
                break
            sleep(0.01)

        provider.clear_unexpected_failure_for("status")
        clock.advance(timedelta(minutes=5))
        deadline = monotonic() + 1.0
        while True:
            recovered_health = browser.get("/health")
            if (
                recovered_health.status_code == 200
                or monotonic() >= deadline
            ):
                break
            sleep(0.01)

    assert failed_health.status_code == 503
    assert failed_health.json() == {
        "status": "degraded",
        "component": "identity_status_synchronization",
    }
    assert recovered_health.status_code == 200
    assert recovered_health.json() == {"status": "ok"}


def test_provider_outage_preserves_existing_access_indefinitely_until_recovery(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-status-outage.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as browser,
        TestClient(app, base_url=WEB_ORIGIN) as token_client,
    ):
        _sign_in(
            browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        token_response = browser.post(
            "/auth/test/workspace-api-token",
            headers=_csrf_headers(browser),
        )
        assert token_response.status_code == 201
        api_token = token_response.json()["token"]
        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status="locked",
        )
        provider.set_unavailable()

        outage_audits = []
        for _ in range(4):
            clock.advance(timedelta(days=29))
            session_response = browser.get("/me")
            token_response = token_client.get(
                "/me",
                headers={"Authorization": f"Bearer {api_token}"},
            )
            outage_audits.append(
                token_client.get(
                    "/auth/test/audit-events",
                    params={
                        "correlation_id": session_response.headers[
                            "x-correlation-id"
                        ],
                    },
                )
            )
            assert session_response.status_code == 200
            assert token_response.status_code == 200

        provider.set_unavailable(False)
        clock.advance(timedelta(minutes=5))
        recovered_status_response = browser.get("/me")
        token_after_recovery = token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {api_token}"},
        )

    for audit in outage_audits:
        assert audit.status_code == 200
        assert [
            (
                event["event_type"],
                event["outcome"],
                event["reason_code"],
            )
            for event in audit.json()
            if event["event_type"] == "identity_status_reconciliation"
        ] == [
            (
                "identity_status_reconciliation",
                "failure",
                "provider_unavailable",
            )
        ]
    assert recovered_status_response.status_code == 401
    assert token_after_recovery.status_code == 401


def test_identity_status_service_failure_is_temporary_unavailability_for_new_auth(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-status-new-auth-outage.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as signed_in_browser,
        TestClient(app, base_url=WEB_ORIGIN) as new_browser,
    ):
        _sign_in(
            signed_in_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        provider.set_failure_for("status")

        recent_start = signed_in_browser.get(
            "/auth/reauthenticate",
            params={"return_to": "/reauthentication/confirm"},
            follow_redirects=False,
        )
        assert recent_start.status_code == 303
        recent_authorization_url = recent_start.headers["location"]
        recent_query = parse_qs(urlparse(recent_authorization_url).query)
        recent_code = provider.issue_authorization_code(
            recent_authorization_url,
            subject=SUBJECT,
            email=EMAIL,
        )

        login_start = new_browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        assert login_start.status_code == 303
        login_authorization_url = login_start.headers["location"]
        login_query = parse_qs(urlparse(login_authorization_url).query)
        login_code = provider.issue_authorization_code(
            login_authorization_url,
            subject=SUBJECT,
            email=EMAIL,
        )

        recent_callback = signed_in_browser.get(
            "/auth/callback",
            params={
                "state": recent_query["state"][0],
                "code": recent_code,
            },
            follow_redirects=False,
        )
        login_callback = new_browser.get(
            "/auth/callback",
            params={
                "state": login_query["state"][0],
                "code": login_code,
            },
            follow_redirects=False,
        )
        existing_session = signed_in_browser.get("/me")

    assert recent_callback.status_code == 303
    recent_result = parse_qs(urlparse(recent_callback.headers["location"]).query)
    assert recent_result["reauthentication"] == ["provider_unavailable"]
    assert login_callback.status_code == 503
    assert login_callback.json()["error"] == "identity_provider_unavailable"
    assert existing_session.status_code == 200


@pytest.mark.parametrize(
    ("entrypoint", "identity_status"),
    [
        ("login", "disabled"),
        ("recent_authentication", "missing"),
    ],
)
def test_explicit_inactive_status_during_new_auth_revokes_existing_credentials(
    tmp_path,
    entrypoint: str,
    identity_status: str,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / f'identity-status-{entrypoint}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as existing_browser,
        TestClient(app, base_url=WEB_ORIGIN) as auth_browser,
        TestClient(app, base_url=WEB_ORIGIN) as token_client,
    ):
        _sign_in(
            existing_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        if entrypoint == "recent_authentication":
            _sign_in(
                auth_browser,
                provider,
                user_agent="Mozilla/5.0 (Macintosh) Safari/17.5",
            )
            start = auth_browser.get(
                "/auth/reauthenticate",
                params={"return_to": "/reauthentication/confirm"},
                follow_redirects=False,
            )
        else:
            start = auth_browser.get(
                "/auth/login",
                params={"return_to": "/console"},
                follow_redirects=False,
            )
        assert start.status_code == 303
        authorization_url = start.headers["location"]
        query = parse_qs(urlparse(authorization_url).query)
        code = provider.issue_authorization_code(
            authorization_url,
            subject=SUBJECT,
            email=EMAIL,
            identity_status=identity_status,
        )
        token_response = existing_browser.post(
            "/auth/test/workspace-api-token",
            headers=_csrf_headers(existing_browser),
        )
        assert token_response.status_code == 201
        api_token = token_response.json()["token"]

        callback = auth_browser.get(
            "/auth/callback",
            params={"state": query["state"][0], "code": code},
            follow_redirects=False,
        )
        existing_session_after_callback = existing_browser.get("/me")
        token_after_callback = token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        audit = token_client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": callback.headers["x-correlation-id"],
            },
        )

    if entrypoint == "login":
        assert callback.status_code == 400
        assert callback.json()["error"] == "login_callback_rejected"
    else:
        assert callback.status_code == 303
        result = parse_qs(urlparse(callback.headers["location"]).query)
        assert result["reauthentication"] == ["failed"]
    assert existing_session_after_callback.status_code == 401
    assert token_after_callback.status_code == 401
    assert audit.status_code == 200
    expected_events = [
        (
            "recent_authentication_started",
            "forced_email_verification",
        )
    ] if entrypoint == "recent_authentication" else []
    expected_events.extend(
        [
            (
                "identity_status_reconciled",
                f"identity_status_{identity_status}",
            ),
            (
                "identity_status_transition_confirmed",
                f"active_to_{identity_status}",
            ),
            (
                "identity_status_user_sessions_revoked",
                f"identity_status_{identity_status}",
            ),
            (
                "identity_status_workspace_api_tokens_revoked",
                f"identity_status_{identity_status}",
            ),
            (
                (
                    "callback_rejected"
                    if entrypoint == "login"
                    else "recent_authentication_failed"
                ),
                "identity_status_inactive",
            ),
        ]
    )
    assert [
        (event["event_type"], event["reason_code"])
        for event in audit.json()
    ] == expected_events


def test_explicit_active_status_allows_only_a_fresh_login_after_lock(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-recovery-locked.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as old_browser,
        TestClient(app, base_url=WEB_ORIGIN) as new_browser,
        TestClient(app, base_url=WEB_ORIGIN) as token_client,
    ):
        _sign_in(
            old_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        token_response = old_browser.post(
            "/auth/test/workspace-api-token",
            headers=_csrf_headers(old_browser),
        )
        assert token_response.status_code == 201
        api_token = token_response.json()["token"]

        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status="locked",
        )
        clock.advance(timedelta(minutes=5))
        assert old_browser.get("/me").status_code == 401

        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status="active",
        )
        old_session_after_recovery = old_browser.get("/me")
        old_token_after_recovery = token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {api_token}"},
        )

        login = new_browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        query = parse_qs(urlparse(authorization_url).query)
        code = provider.issue_authorization_code(
            authorization_url,
            subject=SUBJECT,
            email=EMAIL,
            identity_status="active",
        )
        callback = new_browser.get(
            "/auth/callback",
            params={"state": query["state"][0], "code": code},
            follow_redirects=False,
        )
        new_session = new_browser.get("/me")
        old_session_after_fresh_login = old_browser.get("/me")
        old_token_after_fresh_login = token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        audit = token_client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": callback.headers["x-correlation-id"],
            },
        )

    assert old_session_after_recovery.status_code == 401
    assert old_token_after_recovery.status_code == 401
    assert callback.status_code == 303
    assert new_session.status_code == 200
    assert old_session_after_fresh_login.status_code == 401
    assert old_token_after_fresh_login.status_code == 401
    assert (
        "identity_status_transition_confirmed",
        "locked_to_active",
    ) in [
        (event["event_type"], event["reason_code"])
        for event in audit.json()
    ]


@pytest.mark.parametrize("inactive_status", ["disabled", "missing"])
def test_disabled_or_missing_identity_requires_controlled_restoration(
    tmp_path,
    inactive_status: str,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / f'controlled-{inactive_status}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)
    operator_subject = f"operator-for-{inactive_status}"
    operator_email = f"{operator_subject}@example.test"

    with (
        TestClient(app, base_url=WEB_ORIGIN) as target_browser,
        TestClient(app, base_url=WEB_ORIGIN) as blocked_login_browser,
        TestClient(app, base_url=WEB_ORIGIN) as restored_login_browser,
        TestClient(app, base_url=WEB_ORIGIN) as operator_browser,
    ):
        _sign_in(
            target_browser,
            provider,
            user_agent="Mozilla/5.0 Target",
        )
        _sign_in(
            operator_browser,
            provider,
            user_agent="Mozilla/5.0 Operator",
            subject=operator_subject,
            email=operator_email,
        )
        grant = operator_browser.post(
            "/auth/test/grant-identity-operator",
            headers=_csrf_headers(operator_browser),
        )
        assert grant.status_code == 204

        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status=inactive_status,
        )
        clock.advance(timedelta(minutes=5))
        assert target_browser.get("/me").status_code == 401

        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status="active",
        )
        blocked_login = blocked_login_browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        blocked_authorization_url = blocked_login.headers["location"]
        blocked_query = parse_qs(
            urlparse(blocked_authorization_url).query
        )
        blocked_code = provider.issue_authorization_code(
            blocked_authorization_url,
            subject=SUBJECT,
            email=EMAIL,
            identity_status="active",
        )
        blocked_callback = blocked_login_browser.get(
            "/auth/callback",
            params={
                "state": blocked_query["state"][0],
                "code": blocked_code,
            },
            follow_redirects=False,
        )

        restoration = operator_browser.post(
            "/auth/operator/external-identities/restore",
            json={
                "issuer": provider.issuer,
                "subject": SUBJECT,
                "reason": "provider_identity_reactivated",
            },
            headers=_csrf_headers(operator_browser),
        )

        restored_login = restored_login_browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        restored_authorization_url = restored_login.headers["location"]
        restored_query = parse_qs(
            urlparse(restored_authorization_url).query
        )
        restored_code = provider.issue_authorization_code(
            restored_authorization_url,
            subject=SUBJECT,
            email=EMAIL,
            identity_status="active",
        )
        restored_callback = restored_login_browser.get(
            "/auth/callback",
            params={
                "state": restored_query["state"][0],
                "code": restored_code,
            },
            follow_redirects=False,
        )
        restored_access = restored_login_browser.get("/me")
        old_access = target_browser.get("/me")
        audit = operator_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": restoration.headers[
                    "x-correlation-id"
                ],
            },
        )

    assert blocked_callback.status_code == 400
    assert blocked_callback.json()["error"] == "login_callback_rejected"
    assert restoration.status_code == 200
    assert restoration.json()["status"] == "restored"
    assert restored_callback.status_code == 303
    assert restored_access.status_code == 200
    assert old_access.status_code == 401
    assert [
        (event["event_type"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            "external_identity_restored",
            "provider_identity_reactivated",
        ),
    ]


def test_locked_human_identity_does_not_revoke_running_worker_callback(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-status-worker.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as browser,
        TestClient(app, base_url=WEB_ORIGIN) as worker_client,
    ):
        _sign_in(
            browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        worker_fixture = browser.post(
            "/auth/test/running-worker-callback",
            headers=_csrf_headers(browser),
        )
        assert worker_fixture.status_code == 201
        worker_credential = worker_fixture.json()

        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status="locked",
        )
        clock.advance(timedelta(minutes=5))
        human_access = browser.get("/me")
        worker_heartbeat = worker_client.post(
            (
                "/cloud-run-worker/leases/"
                f"{worker_credential['lease_id']}/heartbeat"
            ),
            json={
                "worker_id": worker_credential["worker_id"],
                "callback_token": worker_credential["callback_token"],
                "lease_seconds": 60,
            },
        )

    assert human_access.status_code == 401
    assert worker_heartbeat.status_code == 200
    assert worker_heartbeat.json()["lease_id"] == worker_credential["lease_id"]


@pytest.mark.parametrize(
    "local_action",
    [
        "disable-user",
        "remove-membership",
        "set-viewer-role",
        "revoke-api-token",
    ],
)
def test_local_access_controls_remain_immediate_during_provider_outage(
    tmp_path,
    local_action: str,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / f'local-{local_action}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as browser,
        TestClient(app, base_url=WEB_ORIGIN) as token_client,
    ):
        _sign_in(
            browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        token_response = browser.post(
            "/auth/test/workspace-api-token",
            headers=_csrf_headers(browser),
        )
        assert token_response.status_code == 201
        api_token = token_response.json()["token"]
        provider.set_unavailable()
        clock.advance(timedelta(minutes=5))

        mutation = browser.post(
            f"/auth/test/local-access-control/{local_action}",
            headers=_csrf_headers(browser),
        )
        session_after_mutation = browser.get("/me")
        token_after_mutation = token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {api_token}"},
        )

    assert mutation.status_code == 204
    if local_action == "disable-user":
        assert session_after_mutation.status_code == 401
        assert token_after_mutation.status_code == 401
    elif local_action == "remove-membership":
        assert session_after_mutation.status_code == 200
        assert session_after_mutation.json()["selection_state"] == (
            "selection_required"
        )
        assert token_after_mutation.status_code == 401
    elif local_action == "set-viewer-role":
        assert session_after_mutation.status_code == 200
        assert session_after_mutation.json()["roles"] == ["viewer"]
        assert token_after_mutation.status_code == 200
        assert token_after_mutation.json()["roles"] == ["viewer"]
    else:
        assert session_after_mutation.status_code == 200
        assert token_after_mutation.status_code == 401


def test_explicit_device_session_revocation_remains_immediate_during_outage(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'local-session-revocation.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
        TestClient(app, base_url=WEB_ORIGIN) as other_browser,
    ):
        _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        _sign_in(
            other_browser,
            provider,
            user_agent="Mozilla/5.0 (Macintosh) Safari/17.5",
        )
        provider.set_unavailable()
        clock.advance(timedelta(minutes=5))

        sessions = current_browser.get("/auth/device-sessions")
        assert sessions.status_code == 200
        other_session = next(
            session
            for session in sessions.json()["sessions"]
            if not session["is_current"]
        )
        revocation = current_browser.delete(
            f"/auth/device-sessions/{other_session['id']}",
            headers=_csrf_headers(current_browser),
        )
        other_session_after_revocation = other_browser.get("/me")

    assert revocation.status_code == 204
    assert other_session_after_revocation.status_code == 401


def test_identity_status_revocation_clears_api_tokens_across_accounts(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'identity-status-multi-account.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as browser,
        TestClient(app, base_url=WEB_ORIGIN) as first_token_client,
        TestClient(app, base_url=WEB_ORIGIN) as second_token_client,
    ):
        _sign_in(
            browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        first_token_response = browser.post(
            "/auth/test/workspace-api-token",
            headers=_csrf_headers(browser),
        )
        second_token_response = browser.post(
            "/auth/test/secondary-workspace-api-token",
            headers=_csrf_headers(browser),
        )
        assert first_token_response.status_code == 201
        assert second_token_response.status_code == 201
        first_token = first_token_response.json()["token"]
        second_token = second_token_response.json()["token"]
        assert first_token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {first_token}"},
        ).status_code == 200
        assert second_token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {second_token}"},
        ).status_code == 200

        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status="disabled",
        )
        clock.advance(timedelta(minutes=5))
        triggering_response = browser.get("/me")
        first_token_after_sync = first_token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {first_token}"},
        )
        second_token_after_sync = second_token_client.get(
            "/me",
            headers={"Authorization": f"Bearer {second_token}"},
        )

    assert triggering_response.status_code == 401
    assert first_token_after_sync.status_code == 401
    assert second_token_after_sync.status_code == 401


@pytest.mark.parametrize(
    ("provider_status", "expected_outcome", "expected_reason"),
    [
        ("active", "success", "identity_status_active"),
        (
            "unexpected-raw-provider-payload",
            "failure",
            "provider_status_invalid",
        ),
    ],
)
def test_reconciliation_revokes_only_explicit_supported_inactive_statuses(
    tmp_path,
    provider_status: str,
    expected_outcome: str,
    expected_reason: str,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / f'status-{expected_reason}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as browser,
        TestClient(app, base_url=WEB_ORIGIN) as observer,
    ):
        _sign_in(
            browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
        )
        provider.set_identity_status(
            issuer=provider.issuer,
            subject=SUBJECT,
            identity_status=provider_status,
        )
        clock.advance(timedelta(minutes=5))

        response = browser.get("/me")
        audit = observer.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": response.headers["x-correlation-id"],
            },
        )

    assert response.status_code == 200
    assert audit.status_code == 200
    assert [
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    ] == [
        (
            (
                "identity_status_reconciled"
                if provider_status == "active"
                else "identity_status_reconciliation"
            ),
            expected_outcome,
            expected_reason,
        )
    ]
    if provider_status != "active":
        assert provider_status not in audit.text
