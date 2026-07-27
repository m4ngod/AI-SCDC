from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import parse_qs, urlparse

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
    utc_now,
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


WEB_ORIGIN = "https://console.example.test"
WORKSPACE_API_TOKEN = "logout-workspace-api-token"
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
            id="user_logout",
            email="logout@example.test",
            display_name="Logout customer",
        )
        account = Organization(
            id="account_logout",
            name="Logout account",
        )
        workspace = Workspace(
            id="workspace_logout",
            organization_id=account.id,
            name="Logout workspace",
        )
        membership = OrganizationMember(
            id="member_logout",
            organization_id=account.id,
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.OWNER,
            api_token_hash=hash_api_token(WORKSPACE_API_TOKEN),
        )
        external_identity = ExternalIdentity(
            id="external_identity_logout",
            issuer=provider.issuer,
            subject="subject-logout",
            user_id=user.id,
            email=user.email,
        )
        session.add(user)
        session.add(account)
        session.add(workspace)
        session.add(membership)
        session.add(external_identity)
        session.commit()


def _build_app(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
    *,
    clock: Callable[[], datetime] | None = None,
):
    return create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin=WEB_ORIGIN,
        identity_audit_observer_enabled=True,
        identity_clock=clock if clock is not None else utc_now,
    )


def _sign_in(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
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
        subject="subject-logout",
        email="logout@example.test",
    )
    callback = client.get(
        "/auth/callback",
        params={"state": state, "code": code},
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


def test_sign_out_revokes_only_the_current_device_and_preserves_api_tokens(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'logout.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as current_browser,
        TestClient(app, base_url=WEB_ORIGIN) as other_browser,
        TestClient(app, base_url=WEB_ORIGIN) as api_client,
    ):
        current_cookie = _sign_in(current_browser, provider)
        _sign_in(other_browser, provider)

        response = current_browser.post(
            "/auth/logout",
            headers=_csrf_headers(current_browser),
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "redirect_to": (
                "https://fake-idp.example.test/logout?"
                "post_logout_redirect_uri="
                "https%3A%2F%2Fconsole.example.test%2F"
            )
        }
        cleared_cookie = response.headers["set-cookie"]
        assert f'{USER_SESSION_COOKIE}=""' in cleared_cookie
        assert "Max-Age=0" in cleared_cookie
        assert "Secure" in cleared_cookie
        assert "HttpOnly" in cleared_cookie
        assert "SameSite=lax" in cleared_cookie
        assert "Path=/" in cleared_cookie
        assert "Domain=" not in cleared_cookie

        assert current_browser.get("/me").status_code == 401
        assert other_browser.get("/me").status_code == 200
        api_identity = api_client.get(
            "/me",
            headers={"Authorization": f"Bearer {WORKSPACE_API_TOKEN}"},
        )
        assert api_identity.status_code == 200
        assert api_identity.json()["auth_mode"] == "api_token"

        correlation_id = response.headers["x-correlation-id"]
        audit = api_client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
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
        ("session_signed_out", "success", "current_device_revoked"),
        (
            "provider_logout",
            "success",
            "end_session_redirect_prepared",
        ),
    ]
    serialized = response.text + audit.text
    assert current_cookie not in serialized
    assert WORKSPACE_API_TOKEN not in serialized


@pytest.mark.parametrize(
    ("provider_failure", "expected_reason"),
    [
        ("unavailable", "provider_unavailable"),
        ("service_error", "provider_error"),
        ("malformed_destination", "end_session_destination_rejected"),
        ("malformed_authority", "end_session_destination_rejected"),
        ("untrusted_destination", "end_session_destination_rejected"),
    ],
)
def test_provider_logout_failure_never_reverses_local_sign_out(
    tmp_path,
    provider_failure: str,
    expected_reason: str,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / f'logout-{provider_failure}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider)

    with TestClient(
        app,
        base_url=WEB_ORIGIN,
        raise_server_exceptions=False,
    ) as client:
        session_cookie = _sign_in(client, provider)
        headers = _csrf_headers(client)
        if provider_failure == "unavailable":
            provider.set_unavailable_for("end_session")
        elif provider_failure == "service_error":
            provider.set_failure_for("end_session")
        elif provider_failure == "malformed_destination":
            provider.set_end_session_endpoint("javascript:provider-payload")
        elif provider_failure == "malformed_authority":
            provider.set_end_session_endpoint("https://[")
        else:
            provider.set_end_session_endpoint(
                "https://untrusted.example.test/logout"
            )

        response = client.post("/auth/logout", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json() == {"redirect_to": None}
        assert "Max-Age=0" in response.headers["set-cookie"]
        assert client.get("/me").status_code == 401
        correlation_id = response.headers["x-correlation-id"]
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
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
        ("session_signed_out", "success", "current_device_revoked"),
        ("provider_logout", "failure", expected_reason),
    ]
    serialized = response.text + audit.text
    assert session_cookie not in serialized
    assert "provider-payload" not in serialized
    assert "https://[" not in serialized
    assert "untrusted.example.test" not in serialized
    assert "fake Customer Identity Provider" not in serialized


def test_provider_without_end_session_still_completes_local_sign_out(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'logout-without-provider-endpoint.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    provider.set_end_session_endpoint(None)
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        response = client.post(
            "/auth/logout",
            headers=_csrf_headers(client),
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"redirect_to": None}
        assert "Max-Age=0" in response.headers["set-cookie"]
        assert client.get("/me").status_code == 401
        audit = client.get(
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
        ("session_signed_out", "success", "current_device_revoked"),
        ("provider_logout", "success", "end_session_not_configured"),
    ]


@pytest.mark.parametrize(
    ("csrf_value", "expected_reason"),
    [
        (None, "csrf_token_required"),
        ("v1.4102444800.forged.invalid", "csrf_token_mismatch"),
    ],
)
def test_missing_or_invalid_csrf_does_not_change_logout_state(
    tmp_path,
    csrf_value: str | None,
    expected_reason: str,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / f'logout-csrf-{expected_reason}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        session_cookie = _sign_in(client, provider)
        headers = {"Origin": WEB_ORIGIN}
        if csrf_value is not None:
            headers["X-CSRF-Token"] = csrf_value

        response = client.post("/auth/logout", headers=headers)

        assert response.status_code == 403
        assert response.json() == {"detail": expected_reason}
        assert USER_SESSION_COOKIE not in response.headers.get("set-cookie", "")
        assert client.cookies.get(USER_SESSION_COOKIE) == session_cookie
        assert client.get("/me").status_code == 200
        correlation_id = response.headers["x-correlation-id"]
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
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
        ("csrf_rejected", "failure", expected_reason),
    ]


def test_sign_out_cookie_deletion_wins_when_rotation_becomes_due(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'logout-at-rotation-boundary.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    clock = MutableClock()
    _seed_customer(database_url, provider)
    app = _build_app(database_url, provider, clock=clock)

    with TestClient(app, base_url=WEB_ORIGIN) as client:
        _sign_in(client, provider)
        clock.advance(timedelta(hours=23, minutes=59))
        headers = _csrf_headers(client)
        clock.advance(timedelta(minutes=2))

        response = client.post("/auth/logout", headers=headers)

        assert response.status_code == 200, response.text
        session_cookie_headers = [
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith(f"{USER_SESSION_COOKIE}=")
        ]
        assert len(session_cookie_headers) == 1
        assert "Max-Age=0" in session_cookie_headers[0]
        assert client.cookies.get(USER_SESSION_COOKIE) is None
        assert client.get("/me").status_code == 401
