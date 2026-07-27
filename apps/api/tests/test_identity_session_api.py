from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import Depends
from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    ExternalIdentity,
    Organization,
    OrganizationMember,
    User,
    Workspace,
    WorkspaceRole,
)
from ai_company_api.services.auth_policy import (
    AuthenticationEnvironment,
    AuthenticationPolicy,
    HumanCredentialType,
)
from ai_company_api.services.auth_context import get_auth_context_dependency
from ai_company_api.services.customer_identity_provider import (
    DeterministicFakeCustomerIdentityProvider,
)
from ai_company_api.services.identity_login import USER_SESSION_COOKIE


USER_SESSION_TEST_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset({HumanCredentialType.USER_SESSION}),
)


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def _seed_linked_identity(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
) -> None:
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        user = User(
            id="user_session_customer",
            email="session-customer@example.test",
            display_name="Session customer",
        )
        account = Organization(
            id="account_session_customer",
            name="Session customer account",
        )
        workspace = Workspace(
            id="workspace_session_customer",
            organization_id=account.id,
            name="Session customer workspace",
        )
        membership = OrganizationMember(
            organization_id=account.id,
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.OWNER,
        )
        identity = ExternalIdentity(
            issuer=provider.issuer,
            subject="subject-session-customer",
            user_id=user.id,
            email=user.email,
        )
        session.add(user)
        session.add(account)
        session.add(workspace)
        session.add(membership)
        session.add(identity)
        session.commit()


def _session_app(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
    clock: MutableClock,
    *,
    database_failure: bool = False,
):
    return create_app(
        database_url=database_url,
        authentication_policy=USER_SESSION_TEST_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
        identity_clock=clock,
        user_session_database_failure=database_failure,
    )


def _sign_in(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
):
    login = client.get(
        "/auth/login",
        params={"return_to": "/console"},
        follow_redirects=False,
    )
    authorization_url = login.headers["location"]
    state = parse_qs(urlparse(authorization_url).query)["state"][0]
    code = provider.issue_authorization_code(
        authorization_url,
        subject="subject-session-customer",
        email="session-customer@example.test",
    )
    return client.get(
        "/auth/callback",
        params={"state": state, "code": code},
        follow_redirects=False,
    )


def _set_session_cookie(client: TestClient, value: str) -> None:
    client.cookies.set(
        USER_SESSION_COOKIE,
        value,
        domain="testserver.local",
        path="/",
    )


def test_active_session_survives_restarts_and_renews_without_absolute_lifetime(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'durable-session.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    _seed_linked_identity(database_url, provider)

    with TestClient(
        _session_app(database_url, provider, clock),
        base_url="https://testserver",
    ) as first_browser:
        callback = _sign_in(first_browser, provider)
        initial_cookie = first_browser.cookies.get(USER_SESSION_COOKIE)

    assert callback.status_code == 303
    assert "Max-Age=2592000" in callback.headers["set-cookie"]
    assert initial_cookie

    clock.advance(timedelta(days=29))
    with TestClient(
        _session_app(database_url, provider, clock),
        base_url="https://testserver",
    ) as reopened_browser:
        _set_session_cookie(reopened_browser, initial_cookie)
        first_renewal = reopened_browser.get("/me")
        renewed_cookie = reopened_browser.cookies.get(USER_SESSION_COOKIE)

    assert first_renewal.status_code == 200
    assert renewed_cookie

    clock.advance(timedelta(days=29))
    with TestClient(
        _session_app(database_url, provider, clock),
        base_url="https://testserver",
    ) as restarted_computer:
        _set_session_cookie(restarted_computer, renewed_cookie)
        second_renewal = restarted_computer.get("/me")

    assert second_renewal.status_code == 200
    assert second_renewal.json()["user_id"] == "user_session_customer"


def test_session_idle_for_more_than_thirty_days_expires_and_requires_login(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'idle-expiry.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    _seed_linked_identity(database_url, provider)

    with TestClient(
        _session_app(database_url, provider, clock),
        base_url="https://testserver",
    ) as client:
        callback = _sign_in(client, provider)
        expired_cookie = client.cookies.get(USER_SESSION_COOKIE)
        device_session_id = expired_cookie.partition(".")[0]

        clock.advance(timedelta(days=30, seconds=1))
        expired = client.get("/me")
        repeated = client.get("/me")
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": expired.headers["x-correlation-id"]},
        )

        fresh_callback = _sign_in(client, provider)
        restored = client.get("/me")

    assert callback.status_code == 303
    assert expired.status_code == 401
    assert expired.json() == {"detail": "User Session is not valid"}
    assert repeated.status_code == 401
    assert audit.json() == [
        {
            "event_type": "session_expired",
            "outcome": "failure",
            "reason_code": "idle_timeout",
            "correlation_id": expired.headers["x-correlation-id"],
            "user_id": "user_session_customer",
            "external_identity_id": None,
            "device_session_id": device_session_id,
        }
    ]
    assert fresh_callback.status_code == 303
    assert restored.status_code == 200
    assert all(
        forbidden not in f"{expired.text}{repeated.text}{audit.text}"
        for forbidden in (
            expired_cookie,
            expired_cookie.partition(".")[2],
            "id_token",
            "access_token",
            "otp",
        )
    )


def test_session_secret_rotates_with_overlap_and_replay_revokes_the_session(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'session-rotation.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    _seed_linked_identity(database_url, provider)
    app = _session_app(database_url, provider, clock)

    with TestClient(app, base_url="https://testserver") as current_browser:
        callback = _sign_in(current_browser, provider)
        previous_cookie = current_browser.cookies.get(USER_SESSION_COOKIE)

        clock.advance(timedelta(hours=24))
        rotated = current_browser.get("/me")
        current_cookie = current_browser.cookies.get(USER_SESSION_COOKIE)
        rotation_audit = current_browser.get(
            "/auth/test/audit-events",
            params={"correlation_id": rotated.headers["x-correlation-id"]},
        )

        with (
            TestClient(app, base_url="https://testserver") as parallel_one,
            TestClient(app, base_url="https://testserver") as parallel_two,
        ):
            _set_session_cookie(parallel_one, previous_cookie)
            _set_session_cookie(parallel_two, previous_cookie)
            with ThreadPoolExecutor(max_workers=2) as executor:
                overlap_responses = list(
                    executor.map(
                        lambda browser: browser.get("/me"),
                        (parallel_one, parallel_two),
                    )
                )

        clock.advance(timedelta(minutes=2, seconds=1))
        with TestClient(app, base_url="https://testserver") as replay_browser:
            _set_session_cookie(replay_browser, previous_cookie)
            replay = replay_browser.get("/me")
            replay_audit = replay_browser.get(
                "/auth/test/audit-events",
                params={"correlation_id": replay.headers["x-correlation-id"]},
            )

        _set_session_cookie(current_browser, current_cookie)
        current_after_replay = current_browser.get("/me")

    assert callback.status_code == 303
    assert rotated.status_code == 200
    assert current_cookie != previous_cookie
    assert current_cookie.partition(".")[0] == previous_cookie.partition(".")[0]
    rotated_set_cookie = rotated.headers["set-cookie"]
    assert "Max-Age=2592000" in rotated_set_cookie
    assert "Secure" in rotated_set_cookie
    assert "HttpOnly" in rotated_set_cookie
    assert "SameSite=lax" in rotated_set_cookie
    assert "Path=/" in rotated_set_cookie
    assert [response.status_code for response in overlap_responses] == [200, 200]
    assert rotation_audit.json() == [
        {
            "event_type": "session_credential_rotated",
            "outcome": "success",
            "reason_code": "scheduled_rotation",
            "correlation_id": rotated.headers["x-correlation-id"],
            "user_id": "user_session_customer",
            "external_identity_id": None,
            "device_session_id": previous_cookie.partition(".")[0],
        }
    ]

    assert replay.status_code == 401
    assert replay.json() == {"detail": "User Session is not valid"}
    assert current_after_replay.status_code == 401
    assert replay_audit.json() == [
        {
            "event_type": "session_credential_replay",
            "outcome": "failure",
            "reason_code": "suspected_replay",
            "correlation_id": replay.headers["x-correlation-id"],
            "user_id": "user_session_customer",
            "external_identity_id": None,
            "device_session_id": previous_cookie.partition(".")[0],
        }
    ]
    assert all(
        forbidden not in (
            f"{rotated.text}{rotation_audit.text}"
            f"{replay.text}{replay_audit.text}"
        )
        for forbidden in (
            previous_cookie,
            previous_cookie.partition(".")[2],
            current_cookie,
            current_cookie.partition(".")[2],
            "id_token",
            "access_token",
            "otp",
        )
    )


def test_completed_callback_uses_the_session_overlap_and_replay_rules(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'callback-session-replay.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    _seed_linked_identity(database_url, provider)
    app = _session_app(database_url, provider, clock)

    with TestClient(app, base_url="https://testserver") as current_browser:
        login = current_browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-session-customer",
            email="session-customer@example.test",
        )
        callback = current_browser.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        previous_cookie = current_browser.cookies.get(USER_SESSION_COOKIE)

        clock.advance(timedelta(hours=24))
        rotated = current_browser.get("/me")
        current_cookie = current_browser.cookies.get(USER_SESSION_COOKIE)

        with TestClient(app, base_url="https://testserver") as overlap_browser:
            _set_session_cookie(overlap_browser, previous_cookie)
            repeated_within_overlap = overlap_browser.get(
                "/auth/callback",
                params={"state": state, "code": code},
                follow_redirects=False,
            )

        clock.advance(timedelta(minutes=2, seconds=1))
        with TestClient(app, base_url="https://testserver") as replay_browser:
            _set_session_cookie(replay_browser, previous_cookie)
            repeated_after_overlap = replay_browser.get(
                "/auth/callback",
                params={"state": state, "code": code},
                follow_redirects=False,
            )
            replay_audit = replay_browser.get(
                "/auth/test/audit-events",
                params={
                    "correlation_id": repeated_after_overlap.headers[
                        "x-correlation-id"
                    ],
                },
            )

        _set_session_cookie(current_browser, current_cookie)
        current_after_replay = current_browser.get("/me")

    assert callback.status_code == 303
    assert rotated.status_code == 200
    assert repeated_within_overlap.status_code == 303
    assert repeated_within_overlap.headers["location"] == "/console"
    assert repeated_after_overlap.status_code == 401
    assert repeated_after_overlap.json()["error"] == "login_callback_rejected"
    assert current_after_replay.status_code == 401
    assert [
        (event["event_type"], event["reason_code"])
        for event in replay_audit.json()
    ] == [("session_credential_replay", "suspected_replay")]


def test_rotation_cookie_is_delivered_when_the_protected_endpoint_returns_404(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'rotation-error-response.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    _seed_linked_identity(database_url, provider)
    app = _session_app(database_url, provider, clock)

    with TestClient(app, base_url="https://testserver") as client:
        _sign_in(client, provider)
        previous_cookie = client.cookies.get(USER_SESSION_COOKIE)
        clock.advance(timedelta(hours=24))

        not_found = client.get("/projects/missing/repositories")
        current_cookie = client.cookies.get(USER_SESSION_COOKIE)

        clock.advance(timedelta(minutes=2, seconds=1))
        still_signed_in = client.get("/me")

    assert not_found.status_code == 404
    assert "set-cookie" in not_found.headers
    assert current_cookie != previous_cookie
    assert still_signed_in.status_code == 200


def test_rotation_cookie_is_delivered_when_protected_endpoint_returns_500(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'rotation-unhandled-error.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    _seed_linked_identity(database_url, provider)
    app = _session_app(database_url, provider, clock)

    def fail_after_authentication() -> None:
        raise RuntimeError("simulated protected endpoint failure")

    app.add_api_route(
        "/test/protected-failure",
        fail_after_authentication,
        dependencies=[Depends(get_auth_context_dependency)],
    )

    with TestClient(
        app,
        base_url="https://testserver",
        raise_server_exceptions=False,
    ) as client:
        _sign_in(client, provider)
        previous_cookie = client.cookies.get(USER_SESSION_COOKIE)
        clock.advance(timedelta(hours=24))

        failed = client.get("/test/protected-failure")
        current_cookie = client.cookies.get(USER_SESSION_COOKIE)

        clock.advance(timedelta(minutes=2, seconds=1))
        still_signed_in = client.get("/me")

    assert failed.status_code == 500
    assert "set-cookie" in failed.headers
    assert current_cookie != previous_cookie
    assert still_signed_in.status_code == 200


def test_high_frequency_activity_persists_session_renewal_at_most_hourly(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'session-throttle.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    _seed_linked_identity(database_url, provider)

    with TestClient(
        _session_app(database_url, provider, clock),
        base_url="https://testserver",
    ) as client:
        _sign_in(client, provider)
        responses = []
        for minutes in (20, 20, 20, 30, 30):
            clock.advance(timedelta(minutes=minutes))
            responses.append(client.get("/me"))
        first_renewal_audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": responses[2].headers["x-correlation-id"],
            },
        )
        second_renewal_audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": responses[4].headers["x-correlation-id"],
            },
        )

    assert [response.status_code for response in responses] == [200] * 5
    assert "x-correlation-id" not in responses[0].headers
    assert "x-correlation-id" not in responses[1].headers
    assert "x-correlation-id" in responses[2].headers
    assert "x-correlation-id" not in responses[3].headers
    assert "x-correlation-id" in responses[4].headers
    for audit in (first_renewal_audit, second_renewal_audit):
        assert [
            (
                event["event_type"],
                event["outcome"],
                event["reason_code"],
            )
            for event in audit.json()
        ] == [
            (
                "session_activity_renewed",
                "success",
                "hourly_activity_checkpoint",
            )
        ]


def test_parallel_activity_creates_only_one_hourly_session_checkpoint(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'parallel-session-checkpoint.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    _seed_linked_identity(database_url, provider)
    app = _session_app(database_url, provider, clock)

    with TestClient(app, base_url="https://testserver") as signed_in_browser:
        _sign_in(signed_in_browser, provider)
        session_cookie = signed_in_browser.cookies.get(USER_SESSION_COOKIE)

    clock.advance(timedelta(hours=1))
    with (
        TestClient(app, base_url="https://testserver") as first_browser,
        TestClient(app, base_url="https://testserver") as second_browser,
    ):
        _set_session_cookie(first_browser, session_cookie)
        _set_session_cookie(second_browser, session_cookie)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda browser: browser.get("/me"),
                    (first_browser, second_browser),
                )
            )
        checkpoint_responses = [
            response
            for response in responses
            if "x-correlation-id" in response.headers
        ]
        audit = first_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": checkpoint_responses[0].headers[
                    "x-correlation-id"
                ],
            },
        )

    assert [response.status_code for response in responses] == [200, 200]
    assert len(checkpoint_responses) == 1
    assert [
        event["event_type"] for event in audit.json()
    ] == ["session_activity_renewed"]


def test_cookie_authentication_fails_closed_when_session_database_is_unavailable(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'session-database-unavailable.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    _seed_linked_identity(database_url, provider)

    with TestClient(
        _session_app(database_url, provider, clock),
        base_url="https://testserver",
    ) as signed_in_browser:
        _sign_in(signed_in_browser, provider)
        session_cookie = signed_in_browser.cookies.get(USER_SESSION_COOKIE)

    with TestClient(
        _session_app(
            database_url,
            provider,
            clock,
            database_failure=True,
        ),
        base_url="https://testserver",
    ) as unavailable_browser:
        _set_session_cookie(unavailable_browser, session_cookie)
        unavailable = unavailable_browser.get("/me")

    with TestClient(
        _session_app(database_url, provider, clock),
        base_url="https://testserver",
    ) as recovered_browser:
        _set_session_cookie(recovered_browser, session_cookie)
        recovered = recovered_browser.get("/me")

    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "detail": "User Session database is unavailable",
    }
    assert unavailable.headers["x-correlation-id"]
    assert session_cookie not in unavailable.text
    assert session_cookie.partition(".")[2] not in unavailable.text
    assert recovered.status_code == 200


def test_session_database_failure_injection_is_test_only() -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    production_policy = AuthenticationPolicy(
        environment=AuthenticationEnvironment.PRODUCTION,
        accepted_human_credentials=frozenset(
            {HumanCredentialType.USER_SESSION}
        ),
    )

    with pytest.raises(
        ValueError,
        match="database failure injection is allowed only",
    ):
        create_app(
            database_url="sqlite://",
            authentication_policy=production_policy,
            customer_identity_provider=provider,
            user_session_database_failure=True,
        )
