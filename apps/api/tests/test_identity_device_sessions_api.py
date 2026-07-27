from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    DeviceSession,
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
from ai_company_api.services.user_session_credentials import USER_SESSION_COOKIE
from ai_company_api.services.user_session_credentials import hash_session_secret


WEB_ORIGIN = "https://console.example.test"
WORKSPACE_API_TOKEN = "device-sessions-workspace-api-token"
WEB_CONSOLE_POLICY = AuthenticationPolicy(
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
        self.current = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def _seed_customer(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
) -> None:
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        user = User(
            id="user_device_sessions",
            email="sessions@example.test",
            display_name="Sessions customer",
        )
        account = Organization(
            id="account_device_sessions",
            name="Sessions account",
        )
        workspace = Workspace(
            id="workspace_device_sessions",
            organization_id=account.id,
            name="Sessions workspace",
        )
        membership = OrganizationMember(
            id="member_device_sessions",
            organization_id=account.id,
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.OWNER,
            api_token_hash=hash_api_token(WORKSPACE_API_TOKEN),
        )
        external_identity = ExternalIdentity(
            id="external_identity_device_sessions",
            issuer=provider.issuer,
            subject="subject-device-sessions",
            user_id=user.id,
            email=user.email,
        )
        other_user = User(
            id="user_other_device_sessions",
            email="other-sessions@example.test",
            display_name="Other sessions customer",
        )
        other_account = Organization(
            id="account_other_device_sessions",
            name="Other sessions account",
        )
        other_workspace = Workspace(
            id="workspace_other_device_sessions",
            organization_id=other_account.id,
            name="Other sessions workspace",
        )
        other_membership = OrganizationMember(
            id="member_other_device_sessions",
            organization_id=other_account.id,
            workspace_id=other_workspace.id,
            user_id=other_user.id,
            role=WorkspaceRole.OWNER,
        )
        other_device_session = DeviceSession(
            id="device_session_other_customer",
            user_id=other_user.id,
            active_workspace_id=other_workspace.id,
            active_organization_id=other_account.id,
            secret_hash=hash_session_secret("other-customer-session-secret"),
            secret_rotated_at=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
            device_description="Safari on iOS",
            idle_expires_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
            last_seen_at=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        )
        session.add(user)
        session.add(account)
        session.add(workspace)
        session.add(membership)
        session.add(external_identity)
        session.add(other_user)
        session.add(other_account)
        session.add(other_workspace)
        session.add(other_membership)
        session.add(other_device_session)
        session.commit()


def _build_app(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
    clock: MutableClock,
):
    return create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin=WEB_ORIGIN,
        identity_audit_observer_enabled=True,
        identity_clock=clock,
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
        subject="subject-device-sessions",
        email="sessions@example.test",
    )
    callback = client.get(
        "/auth/callback",
        params={"state": state, "code": code},
        headers={"User-Agent": user_agent},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    session_cookie = client.cookies.get(USER_SESSION_COOKIE)
    assert session_cookie is not None
    return session_cookie


def _csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/auth/csrf")
    assert response.status_code == 200
    return {
        "Origin": WEB_ORIGIN,
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def _set_session_cookie(client: TestClient, value: str) -> None:
    client.cookies.set(
        USER_SESSION_COOKIE,
        value,
        domain="console.example.test",
        path="/",
    )


def test_list_active_device_sessions_identifies_current_with_coarse_metadata(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'device-sessions.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider, clock)
    chrome_user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
    )
    firefox_user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) "
        "Gecko/20100101 Firefox/127.0"
    )

    with (
        TestClient(app, base_url=WEB_ORIGIN) as first_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
    ):
        first_cookie = _sign_in(
            first_browser,
            provider,
            user_agent=chrome_user_agent,
        )
        clock.advance(timedelta(minutes=1))
        current_cookie = _sign_in(
            current_browser,
            provider,
            user_agent=firefox_user_agent,
        )

        response = current_browser.get("/auth/device-sessions")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "sessions": [
                {
                    "id": current_cookie.partition(".")[0],
                    "device_description": "Firefox on macOS",
                    "created_at": "2026-07-27T12:01:00",
                    "last_seen_at": "2026-07-27T12:01:00",
                    "status": "active",
                    "is_current": True,
                },
                {
                    "id": first_cookie.partition(".")[0],
                    "device_description": "Chrome on Windows",
                    "created_at": "2026-07-27T12:00:00",
                    "last_seen_at": "2026-07-27T12:00:00",
                    "status": "active",
                    "is_current": False,
                },
            ]
        }
        audit = current_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": response.headers["x-correlation-id"],
            },
        )

    assert audit.status_code == 200
    assert [
        (
            event["event_type"],
            event["outcome"],
            event["reason_code"],
        )
        for event in audit.json()
    ] == [
        ("device_sessions_listed", "success", "active_sessions_returned"),
    ]
    serialized = response.text + audit.text
    assert chrome_user_agent not in serialized
    assert firefox_user_agent not in serialized
    assert first_cookie not in serialized
    assert current_cookie not in serialized


def test_revoke_selected_device_session_requires_fresh_sign_in_and_is_isolated(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'revoke-device-session.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as revoked_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
        TestClient(app, base_url=WEB_ORIGIN) as api_client,
        TestClient(app, base_url=WEB_ORIGIN) as forged_browser,
    ):
        revoked_cookie = _sign_in(
            revoked_browser,
            provider,
            user_agent="Mozilla/5.0 (Linux) Chrome/126.0.0.0",
        )
        current_cookie = _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Firefox/127.0",
        )
        revoked_session_id = revoked_cookie.partition(".")[0]
        current_session_id = current_cookie.partition(".")[0]

        response = current_browser.delete(
            f"/auth/device-sessions/{revoked_session_id}",
            headers=_csrf_headers(current_browser),
        )

        assert response.status_code == 204, response.text
        assert USER_SESSION_COOKIE not in response.headers.get("set-cookie", "")
        _set_session_cookie(
            forged_browser,
            f"{revoked_session_id}.forged-session-secret",
        )
        forged_next_request = forged_browser.get("/me")
        assert forged_next_request.status_code == 401
        forged_audit = api_client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": forged_next_request.headers[
                    "x-correlation-id"
                ],
            },
        )
        revoked_next_request = revoked_browser.get("/me")
        assert revoked_next_request.status_code == 401
        assert "set-cookie" not in revoked_next_request.headers
        replay_audit = api_client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": revoked_next_request.headers[
                    "x-correlation-id"
                ],
            },
        )
        assert current_browser.get("/me").status_code == 200
        api_identity = api_client.get(
            "/me",
            headers={"Authorization": f"Bearer {WORKSPACE_API_TOKEN}"},
        )
        assert api_identity.status_code == 200
        assert api_identity.json()["auth_mode"] == "api_token"

        refreshed = current_browser.get("/auth/device-sessions")
        assert refreshed.status_code == 200
        assert [
            (item["id"], item["is_current"])
            for item in refreshed.json()["sessions"]
        ] == [(current_session_id, True)]

        audit = api_client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": response.headers["x-correlation-id"],
            },
        )

    assert audit.status_code == 200
    assert forged_audit.json() == [
        {
            "event_type": "authentication_failure",
            "outcome": "failure",
            "reason_code": "invalid_session_credential",
            "correlation_id": forged_next_request.headers["x-correlation-id"],
            "user_id": None,
            "external_identity_id": None,
            "device_session_id": None,
        }
    ]
    assert replay_audit.json() == [
        {
            "event_type": "session_credential_replay",
            "outcome": "failure",
            "reason_code": "revoked_session_reuse",
            "correlation_id": revoked_next_request.headers["x-correlation-id"],
            "user_id": "user_device_sessions",
            "external_identity_id": None,
            "device_session_id": revoked_session_id,
        }
    ]
    assert [
        (
            event["event_type"],
            event["outcome"],
            event["reason_code"],
            event["device_session_id"],
        )
        for event in audit.json()
    ] == [
        (
            "device_session_revoked",
            "success",
            "selected_device_revoked",
            revoked_session_id,
        ),
    ]
    serialized = response.text + audit.text
    assert revoked_cookie not in serialized
    assert current_cookie not in serialized
    assert WORKSPACE_API_TOKEN not in serialized


def test_idle_expired_sessions_are_not_listed_or_revoked_as_active(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'idle-expired-device-session.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as stale_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
    ):
        stale_cookie = _sign_in(
            stale_browser,
            provider,
            user_agent="Mozilla/5.0 (Linux) Chrome/126.0.0.0",
        )
        stale_session_id = stale_cookie.partition(".")[0]
        clock.advance(timedelta(days=31))
        current_cookie = _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Firefox/127.0",
        )

        listed = current_browser.get("/auth/device-sessions")

        assert listed.status_code == 200
        assert [
            item["id"] for item in listed.json()["sessions"]
        ] == [current_cookie.partition(".")[0]]

        revoke = current_browser.delete(
            f"/auth/device-sessions/{stale_session_id}",
            headers=_csrf_headers(current_browser),
        )

        assert revoke.status_code == 404
        assert revoke.json() == {"detail": "device_session_not_found"}
        audit = current_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": revoke.headers["x-correlation-id"],
            },
        )
        expired = stale_browser.get("/me")
        assert expired.status_code == 401
        expiry_audit = current_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": expired.headers["x-correlation-id"],
            },
        )

    assert [
        (
            event["event_type"],
            event["outcome"],
            event["reason_code"],
        )
        for event in audit.json()
    ] == [
        (
            "device_session_revocation_rejected",
            "failure",
            "target_expired",
        ),
    ]
    assert expiry_audit.json() == [
        {
            "event_type": "session_expired",
            "outcome": "failure",
            "reason_code": "idle_timeout",
            "correlation_id": expired.headers["x-correlation-id"],
            "user_id": "user_device_sessions",
            "external_identity_id": None,
            "device_session_id": stale_session_id,
        }
    ]


def test_revocation_failures_do_not_reveal_or_change_other_sessions(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'revoke-session-failures.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as target_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
    ):
        target_cookie = _sign_in(
            target_browser,
            provider,
            user_agent="Mozilla/5.0 (Linux) Chrome/126.0.0.0",
        )
        current_cookie = _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Firefox/127.0",
        )
        target_session_id = target_cookie.partition(".")[0]
        headers = _csrf_headers(current_browser)
        first_revocation = current_browser.delete(
            f"/auth/device-sessions/{target_session_id}",
            headers=headers,
        )
        assert first_revocation.status_code == 204

        attempts = [
            ("device_session_other_customer", "target_not_active_or_not_owned"),
            ("device_session_unknown", "target_not_active_or_not_owned"),
            ("not a session id!", "target_not_active_or_not_owned"),
            (target_session_id, "target_already_revoked"),
        ]
        audit_events: list[tuple[str, str, str]] = []
        for target, expected_reason in attempts:
            response = current_browser.delete(
                f"/auth/device-sessions/{target}",
                headers=_csrf_headers(current_browser),
            )
            assert response.status_code == 404
            assert response.json() == {"detail": "device_session_not_found"}
            audit = current_browser.get(
                "/auth/test/audit-events",
                params={
                    "correlation_id": response.headers["x-correlation-id"],
                },
            )
            assert audit.status_code == 200
            assert len(audit.json()) == 1
            event = audit.json()[0]
            audit_events.append(
                (
                    event["event_type"],
                    event["outcome"],
                    event["reason_code"],
                )
            )
            assert event["reason_code"] == expected_reason

        assert current_browser.get("/me").status_code == 200
        assert target_browser.get("/me").status_code == 401
        visible = current_browser.get("/auth/device-sessions").json()["sessions"]

    assert len(visible) == 1
    assert visible[0]["id"] == current_cookie.partition(".")[0]
    assert "device_session_other_customer" not in str(visible)
    assert audit_events == [
        (
            "device_session_revocation_rejected",
            "failure",
            "target_not_active_or_not_owned",
        ),
        (
            "device_session_revocation_rejected",
            "failure",
            "target_not_active_or_not_owned",
        ),
        (
            "device_session_revocation_rejected",
            "failure",
            "target_not_active_or_not_owned",
        ),
        (
            "device_session_revocation_rejected",
            "failure",
            "target_already_revoked",
        ),
    ]


def test_current_device_session_must_use_sign_out_instead_of_selected_revoke(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'revoke-current-session.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider, clock)

    with TestClient(app, base_url=WEB_ORIGIN) as current_browser:
        current_cookie = _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Firefox/127.0",
        )
        response = current_browser.delete(
            f"/auth/device-sessions/{current_cookie.partition('.')[0]}",
            headers=_csrf_headers(current_browser),
        )

        assert response.status_code == 409
        assert response.json() == {
            "detail": "current_device_session_requires_sign_out"
        }
        assert current_browser.get("/me").status_code == 200
        audit = current_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": response.headers["x-correlation-id"],
            },
        )

    assert [
        (
            event["event_type"],
            event["outcome"],
            event["reason_code"],
        )
        for event in audit.json()
    ] == [
        (
            "device_session_revocation_rejected",
            "failure",
            "current_session_requires_sign_out",
        ),
    ]


@pytest.mark.parametrize(
    ("csrf_value", "expected_reason"),
    [
        (None, "csrf_token_required"),
        ("v1.4102444800.forged.invalid", "csrf_token_mismatch"),
    ],
)
def test_csrf_rejection_does_not_revoke_selected_device_session(
    tmp_path,
    csrf_value: str | None,
    expected_reason: str,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / f'revoke-csrf-{expected_reason}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider, clock)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as target_browser,
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
    ):
        target_cookie = _sign_in(
            target_browser,
            provider,
            user_agent="Mozilla/5.0 (Linux) Chrome/126.0.0.0",
        )
        _sign_in(
            current_browser,
            provider,
            user_agent="Mozilla/5.0 (Windows NT 10.0) Firefox/127.0",
        )
        headers = {"Origin": WEB_ORIGIN}
        if csrf_value is not None:
            headers["X-CSRF-Token"] = csrf_value

        response = current_browser.delete(
            f"/auth/device-sessions/{target_cookie.partition('.')[0]}",
            headers=headers,
        )

        assert response.status_code == 403
        assert response.json() == {"detail": expected_reason}
        assert target_browser.get("/me").status_code == 200
        assert current_browser.get("/me").status_code == 200
        listed = current_browser.get("/auth/device-sessions")
        assert len(listed.json()["sessions"]) == 2
        audit = current_browser.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": response.headers["x-correlation-id"],
            },
        )

    assert [
        (
            event["event_type"],
            event["outcome"],
            event["reason_code"],
        )
        for event in audit.json()
    ] == [
        ("csrf_rejected", "failure", expected_reason),
    ]


def test_existing_device_sessions_receive_safe_coarse_metadata_on_upgrade(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'upgrade-device-sessions.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    _seed_customer(database_url, provider)

    with TestClient(
        _build_app(database_url, provider, clock),
        base_url=WEB_ORIGIN,
    ) as original_browser:
        session_cookie = _sign_in(
            original_browser,
            provider,
            user_agent="SensitiveBrowser/99 PrivatePlatform/42",
        )

    engine = build_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE device_session DROP COLUMN device_description")
        )

    upgraded_app = _build_app(database_url, provider, clock)
    with TestClient(
        upgraded_app,
        base_url=WEB_ORIGIN,
    ) as reopened_browser:
        _set_session_cookie(reopened_browser, session_cookie)
        response = reopened_browser.get("/auth/device-sessions")

    assert response.status_code == 200, response.text
    assert response.json()["sessions"] == [
        {
            "id": session_cookie.partition(".")[0],
            "device_description": "Unknown browser on Unknown device",
            "created_at": "2026-07-27T12:00:00",
            "last_seen_at": "2026-07-27T12:00:00",
            "status": "active",
            "is_current": True,
        }
    ]
    assert "SensitiveBrowser" not in response.text
    assert "PrivatePlatform" not in response.text
