from base64 import urlsafe_b64encode
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session, select

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
from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProviderUnavailable,
    DeterministicFakeCustomerIdentityProvider,
    OidcAuthorizationRequest,
)


USER_SESSION_TEST_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset({HumanCredentialType.USER_SESSION}),
)


def _seed_linked_identity(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
    *,
    external_identity_status: str = "active",
) -> None:
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        user = User(
            id="user_linked",
            email="linked@example.test",
            display_name="Linked user",
        )
        account = Organization(id="account_linked", name="Linked account")
        workspace = Workspace(
            id="workspace_linked",
            organization_id=account.id,
            name="Linked workspace",
        )
        membership = OrganizationMember(
            organization_id=account.id,
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.ADMIN,
        )
        identity = ExternalIdentity(
            issuer=provider.issuer,
            subject="subject-linked",
            user_id=user.id,
            email=user.email,
            status=external_identity_status,
        )
        session.add(user)
        session.add(account)
        session.add(workspace)
        session.add(membership)
        session.add(identity)
        session.commit()


def test_login_start_redirects_with_oidc_pkce_and_browser_continuity() -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    with TestClient(
        create_app(
            database_url="sqlite://",
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
        ),
        base_url="https://testserver",
    ) as client:
        response = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://fake-idp.example.test/authorize"
    )
    query = parse_qs(location.query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0]
    assert query["nonce"][0]
    assert query["code_challenge"][0]
    assert query["redirect_uri"] == ["https://testserver/auth/callback"]

    cookie = response.headers["set-cookie"]
    assert "__Host-ai_scdc_login=" in cookie
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Domain=" not in cookie


def test_valid_callback_for_linked_identity_creates_device_session_and_me(
    tmp_path,
) -> None:
    database_path = tmp_path / "identity.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_linked_identity(database_url, provider)

    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
        ),
        base_url="https://testserver",
    ) as client:
        login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
        )

        callback = client.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        me = client.get("/me")

    assert callback.status_code == 303
    assert callback.headers["location"] == "/console"
    session_cookie = callback.headers["set-cookie"]
    assert "__Host-ai_scdc_session=" in session_cookie
    assert "Secure" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "Path=/" in session_cookie
    assert "Domain=" not in session_cookie
    assert code not in callback.text
    assert "id_token" not in callback.text
    assert "access_token" not in callback.text

    assert me.status_code == 200
    assert me.json() == {
        "user_id": "user_linked",
        "workspace_id": "workspace_linked",
        "organization_id": "account_linked",
        "roles": ["admin"],
        "auth_mode": "user_session",
        "current_account": {
            "id": "account_linked",
            "name": "Linked account",
            "kind": "legacy",
        },
        "current_workspace": {
            "id": "workspace_linked",
            "name": "Linked workspace",
        },
    }


def test_first_login_atomically_onboards_a_personal_account(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'onboarding.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()

    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-new-customer",
            email="new-customer@example.test",
        )

        callback = client.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        me = client.get("/me")
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": callback.headers["x-correlation-id"]},
        )

    assert callback.status_code == 303
    assert callback.headers["location"] == "/console"
    assert "__Host-ai_scdc_session=" in callback.headers["set-cookie"]
    assert me.status_code == 200
    assert me.json() == {
        "user_id": me.json()["user_id"],
        "workspace_id": me.json()["workspace_id"],
        "organization_id": me.json()["organization_id"],
        "roles": ["owner"],
        "auth_mode": "user_session",
        "current_account": {
            "id": me.json()["organization_id"],
            "name": "Personal Account",
            "kind": "personal",
        },
        "current_workspace": {
            "id": me.json()["workspace_id"],
            "name": "Default Workspace",
        },
    }
    assert {
        (event["event_type"], event["outcome"], event["reason_code"])
        for event in audit.json()
    } == {
        ("onboarding_success", "success", "personal_account_created"),
        ("session_created", "success", "oidc_callback"),
        ("login_success", "success", "personal_account_onboarding"),
    }
    assert all(
        forbidden not in f"{callback.text}{audit.text}"
        for forbidden in (
            code,
            "id_token",
            "access_token",
            "otp",
            provider.issuer,
        )
    )


def test_repeated_login_resolves_the_same_personal_account_without_reonboarding(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'repeat-onboarding.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()

    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        first_login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        first_authorization_url = first_login.headers["location"]
        first_state = parse_qs(urlparse(first_authorization_url).query)["state"][0]
        first_code = provider.issue_authorization_code(
            first_authorization_url,
            subject="subject-returning",
            email="returning@example.test",
        )
        first_callback = client.get(
            "/auth/callback",
            params={"state": first_state, "code": first_code},
            follow_redirects=False,
        )
        first_me = client.get("/me")

        repeated_callback = client.get(
            "/auth/callback",
            params={"state": first_state, "code": first_code},
            follow_redirects=False,
        )
        first_audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": first_callback.headers["x-correlation-id"]},
        )

        second_login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        second_authorization_url = second_login.headers["location"]
        second_state = parse_qs(urlparse(second_authorization_url).query)["state"][0]
        second_code = provider.issue_authorization_code(
            second_authorization_url,
            subject="subject-returning",
            email="returning@example.test",
        )
        second_callback = client.get(
            "/auth/callback",
            params={"state": second_state, "code": second_code},
            follow_redirects=False,
        )
        second_me = client.get("/me")
        second_audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": second_callback.headers["x-correlation-id"]},
        )

    assert first_callback.status_code == 303
    assert repeated_callback.status_code == 303
    assert "__Host-ai_scdc_session=" not in repeated_callback.headers.get(
        "set-cookie",
        "",
    )
    assert sum(
        event["event_type"] == "onboarding_success"
        for event in first_audit.json()
    ) == 1
    assert sum(
        event["event_type"] == "session_created"
        for event in first_audit.json()
    ) == 1
    assert second_callback.status_code == 303
    assert first_me.json()["user_id"] == second_me.json()["user_id"]
    assert first_me.json()["current_account"] == second_me.json()["current_account"]
    assert first_me.json()["current_workspace"] == second_me.json()[
        "current_workspace"
    ]
    assert "onboarding_success" not in {
        event["event_type"] for event in second_audit.json()
    }


@pytest.mark.parametrize(
    "failure_step",
    (
        "user",
        "account",
        "workspace",
        "membership",
        "external_identity",
        "device_session",
    ),
)
def test_onboarding_failure_rolls_back_and_a_new_login_can_retry(
    tmp_path,
    failure_step: str,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / f'onboarding-{failure_step}.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()

    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
            personal_onboarding_failure_step=failure_step,
        ),
        base_url="https://testserver",
    ) as client:
        failed_login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        failed_authorization_url = failed_login.headers["location"]
        failed_state = parse_qs(urlparse(failed_authorization_url).query)["state"][0]
        failed_code = provider.issue_authorization_code(
            failed_authorization_url,
            subject="subject-retry",
            email="retry@example.test",
        )
        failed_callback = client.get(
            "/auth/callback",
            params={"state": failed_state, "code": failed_code},
            follow_redirects=False,
        )
        signed_out = client.get("/me")
        rollback_audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": failed_callback.headers["x-correlation-id"],
            },
        )

    assert failed_callback.status_code == 500
    assert failed_callback.json() == {
        "error": "login_failed",
        "correlation_id": failed_callback.headers["x-correlation-id"],
    }
    assert "__Host-ai_scdc_session=" not in failed_callback.headers.get(
        "set-cookie",
        "",
    )
    assert signed_out.status_code == 401
    assert rollback_audit.json() == [
        {
            "event_type": "onboarding_rollback",
            "outcome": "failure",
            "reason_code": "persistence_failed",
            "correlation_id": failed_callback.headers["x-correlation-id"],
            "user_id": None,
            "external_identity_id": None,
            "device_session_id": None,
        }
    ]
    assert all(
        forbidden not in f"{failed_callback.text}{rollback_audit.text}"
        for forbidden in (
            failed_code,
            "id_token",
            "access_token",
            "otp",
            provider.issuer,
        )
    )

    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
        ),
        base_url="https://testserver",
    ) as retry_client:
        retry_login = retry_client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        retry_authorization_url = retry_login.headers["location"]
        retry_state = parse_qs(urlparse(retry_authorization_url).query)["state"][0]
        retry_code = provider.issue_authorization_code(
            retry_authorization_url,
            subject="subject-retry",
            email="retry@example.test",
        )
        retry_callback = retry_client.get(
            "/auth/callback",
            params={"state": retry_state, "code": retry_code},
            follow_redirects=False,
        )
        retried_me = retry_client.get("/me")

    assert retry_state != failed_state
    assert retry_code != failed_code
    assert retry_callback.status_code == 303
    assert retried_me.status_code == 200
    assert retried_me.json()["current_account"]["kind"] == "personal"
    assert retried_me.json()["roles"] == ["owner"]


def test_login_rejects_unapproved_return_destination_with_safe_audit_event() -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    with TestClient(
        create_app(
            database_url="sqlite://",
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        response = client.get(
            "/auth/login",
            params={"return_to": "https://attacker.example.test/steal"},
            follow_redirects=False,
        )
        correlation_id = response.headers["x-correlation-id"]
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": "login_failed",
        "correlation_id": correlation_id,
    }
    assert "attacker.example.test" not in response.text
    assert audit.status_code == 200
    assert audit.json() == [
        {
            "event_type": "login_failure",
            "outcome": "failure",
            "reason_code": "return_destination_not_allowed",
            "correlation_id": correlation_id,
            "user_id": None,
            "external_identity_id": None,
            "device_session_id": None,
        }
    ]


def test_callback_rejects_nonce_mismatch_with_safe_error_and_audit(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'nonce.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_linked_identity(database_url, provider)
    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
            nonce="wrong-nonce",
        )
        callback = client.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        correlation_id = callback.headers["x-correlation-id"]
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )
        me = client.get("/me")

    assert callback.status_code == 400
    assert callback.json() == {
        "error": "login_callback_rejected",
        "correlation_id": correlation_id,
    }
    assert code not in callback.text
    assert "wrong-nonce" not in callback.text
    assert audit.json() == [
        {
            "event_type": "callback_rejected",
            "outcome": "failure",
            "reason_code": "nonce_mismatch",
            "correlation_id": correlation_id,
            "user_id": None,
            "external_identity_id": None,
            "device_session_id": None,
        }
    ]
    assert me.status_code == 401


def test_callback_rejects_missing_protocol_parameters_with_correlation_and_audit() -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    with TestClient(
        create_app(
            database_url="sqlite://",
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        callback = client.get("/auth/callback", follow_redirects=False)
        correlation_id = callback.headers["x-correlation-id"]
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )

    assert callback.status_code == 400
    assert callback.json() == {
        "error": "login_callback_rejected",
        "correlation_id": correlation_id,
    }
    assert audit.json() == [
        {
            "event_type": "callback_rejected",
            "outcome": "failure",
            "reason_code": "protocol_parameters_missing",
            "correlation_id": correlation_id,
            "user_id": None,
            "external_identity_id": None,
            "device_session_id": None,
        }
    ]


def test_callback_rejects_unknown_state_before_code_exchange() -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    with TestClient(
        create_app(
            database_url="sqlite://",
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        callback = client.get(
            "/auth/callback",
            params={"state": "unknown-state", "code": "untrusted-code"},
            follow_redirects=False,
        )
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": callback.headers["x-correlation-id"]},
        )

    assert callback.status_code == 400
    assert "untrusted-code" not in callback.text
    assert [(event["event_type"], event["reason_code"]) for event in audit.json()] == [
        ("callback_rejected", "state_not_found")
    ]


def test_completed_callback_is_idempotent_only_for_its_authenticated_browser(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'replay.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_linked_identity(database_url, provider)
    app = create_app(
        database_url=database_url,
        authentication_policy=USER_SESSION_TEST_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )
    with TestClient(app, base_url="https://testserver") as original_browser:
        login = original_browser.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
        )
        first_callback = original_browser.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        provider.set_unavailable()
        repeated_callback = original_browser.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )

        with TestClient(app, base_url="https://testserver") as other_browser:
            replay = other_browser.get(
                "/auth/callback",
                params={"state": state, "code": code},
                follow_redirects=False,
            )
            audit = other_browser.get(
                "/auth/test/audit-events",
                params={
                    "correlation_id": first_callback.headers["x-correlation-id"],
                },
            )

    assert first_callback.status_code == 303
    assert repeated_callback.status_code == 303
    assert repeated_callback.headers["location"] == "/console"
    assert replay.status_code == 400
    assert replay.json()["error"] == "login_callback_rejected"
    events = audit.json()
    assert sum(event["event_type"] == "session_created" for event in events) == 1
    assert {
        (event["event_type"], event["reason_code"])
        for event in events
    } >= {
        ("session_created", "oidc_callback"),
        ("login_success", "linked_external_identity"),
        ("callback_rejected", "completed_transaction_replay"),
    }


def test_concurrent_duplicate_callbacks_create_exactly_one_stable_session(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'concurrent-callback.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    provider.set_exchange_delays(initial=0.05, replay_rejection=0.2)
    _seed_linked_identity(database_url, provider)
    app = create_app(
        database_url=database_url,
        authentication_policy=USER_SESSION_TEST_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )
    with (
        TestClient(app, base_url="https://testserver") as starter,
        TestClient(app, base_url="https://testserver") as first_browser_request,
        TestClient(app, base_url="https://testserver") as second_browser_request,
    ):
        login = starter.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
        )
        login_cookie = starter.cookies.get("__Host-ai_scdc_login")
        first_browser_request.cookies.set("__Host-ai_scdc_login", login_cookie)
        second_browser_request.cookies.set("__Host-ai_scdc_login", login_cookie)

        def invoke_callback(client: TestClient):
            return client.get(
                "/auth/callback",
                params={"state": state, "code": code},
                follow_redirects=False,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    invoke_callback,
                    [first_browser_request, second_browser_request],
                )
            )

        successful_index = next(
            index for index, response in enumerate(responses) if response.status_code == 303
        )
        successful_browser = [
            first_browser_request,
            second_browser_request,
        ][successful_index]
        repeated = successful_browser.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        correlation_id = responses[successful_index].headers["x-correlation-id"]
        audit = starter.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )

    assert sorted(response.status_code for response in responses) in (
        [303, 400],
        [303, 409],
    )
    assert repeated.status_code == 303
    assert repeated.headers["location"] == "/console"
    events = audit.json()
    assert sum(event["event_type"] == "session_created" for event in events) == 1
    assert sum(event["event_type"] == "login_success" for event in events) == 1


def test_callback_observing_an_existing_claim_cannot_reject_the_owner(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'claimed-callback.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    provider.set_exchange_delays(initial=0.2)
    _seed_linked_identity(database_url, provider)
    app = create_app(
        database_url=database_url,
        authentication_policy=USER_SESSION_TEST_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )
    with (
        TestClient(app, base_url="https://testserver") as starter,
        TestClient(app, base_url="https://testserver") as owner_request,
        TestClient(app, base_url="https://testserver") as later_request,
    ):
        login = starter.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
        )
        login_cookie = starter.cookies.get("__Host-ai_scdc_login")
        owner_request.cookies.set("__Host-ai_scdc_login", login_cookie)
        later_request.cookies.set("__Host-ai_scdc_login", login_cookie)

        with ThreadPoolExecutor(max_workers=1) as executor:
            owner_future = executor.submit(
                owner_request.get,
                "/auth/callback",
                params={"state": state, "code": code},
                follow_redirects=False,
            )
            assert provider.wait_for_exchange_started(timeout=1.0)
            later = later_request.get(
                "/auth/callback",
                params={"state": state, "code": code},
                follow_redirects=False,
            )
            owner = owner_future.result()

        repeated = owner_request.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        audit = starter.get(
            "/auth/test/audit-events",
            params={"correlation_id": owner.headers["x-correlation-id"]},
        )

    assert owner.status_code == 303
    assert later.status_code == 409
    assert later.json()["error"] == "login_callback_in_progress"
    assert repeated.status_code == 303
    events = audit.json()
    assert sum(event["event_type"] == "session_created" for event in events) == 1
    assert sum(event["event_type"] == "login_success" for event in events) == 1


@pytest.mark.parametrize(
    ("code_options", "reason_code"),
    [
        ({"pkce_valid": False}, "code_exchange_failed"),
        ({"token_valid": False}, "invalid_id_token"),
        ({"audience": "another-client"}, "invalid_id_token"),
        ({"identity_status": "suspended"}, "identity_status_inactive"),
    ],
)
def test_callback_rejects_failed_oidc_security_checks_without_a_session(
    tmp_path,
    code_options,
    reason_code,
) -> None:
    database_url = f"sqlite:///{(tmp_path / f'{reason_code}.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_linked_identity(database_url, provider)
    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
            **code_options,
        )
        callback = client.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        correlation_id = callback.headers["x-correlation-id"]
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )
        me = client.get("/me")

    assert callback.status_code == 400
    assert callback.json()["error"] == "login_callback_rejected"
    assert "__Host-ai_scdc_session=" not in callback.headers.get("set-cookie", "")
    assert code not in callback.text
    assert me.status_code == 401
    assert [(event["event_type"], event["reason_code"]) for event in audit.json()] == [
        ("callback_rejected", reason_code)
    ]


def test_callback_rejects_an_inactive_linked_external_identity(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'inactive-link.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_linked_identity(
        database_url,
        provider,
        external_identity_status="disabled",
    )
    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
        )
        callback = client.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": callback.headers["x-correlation-id"]},
        )

    assert callback.status_code == 400
    assert [(event["event_type"], event["reason_code"]) for event in audit.json()] == [
        ("callback_rejected", "external_identity_not_active")
    ]


def test_callback_reports_provider_failure_without_creating_a_session(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'provider-callback.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_linked_identity(database_url, provider)
    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
        )
        provider.set_unavailable()
        callback = client.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": callback.headers["x-correlation-id"]},
        )

    assert callback.status_code == 503
    assert callback.json()["error"] == "identity_provider_unavailable"
    assert "__Host-ai_scdc_session=" not in callback.headers.get("set-cookie", "")
    assert [(event["event_type"], event["reason_code"]) for event in audit.json()] == [
        ("identity_provider_unavailable", "token_exchange_unavailable")
    ]


def test_callback_handles_general_discovery_failure_safely(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'discovery-callback.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_linked_identity(database_url, provider)
    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
        )
        provider.set_failure_for("discovery")
        callback = client.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": callback.headers["x-correlation-id"]},
        )
        me = client.get("/me")

    assert callback.status_code == 502
    assert callback.json()["error"] == "login_callback_rejected"
    assert "__Host-ai_scdc_session=" not in callback.headers.get("set-cookie", "")
    assert [(event["event_type"], event["reason_code"]) for event in audit.json()] == [
        ("callback_rejected", "discovery_failed")
    ]
    assert me.status_code == 401


def test_callback_rejects_an_expired_login_transaction(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'expired.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_linked_identity(database_url, provider)
    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
            login_transaction_ttl_seconds=-1,
        ),
        base_url="https://testserver",
    ) as client:
        login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
        )
        callback = client.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": callback.headers["x-correlation-id"]},
        )

    assert callback.status_code == 400
    assert callback.json()["error"] == "login_callback_rejected"
    assert [(event["event_type"], event["reason_code"]) for event in audit.json()] == [
        ("callback_rejected", "transaction_expired")
    ]


def test_authenticated_browser_uses_live_membership_role_and_removal(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'live-membership.db').as_posix()}"
    engine = build_engine(database_url)
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_linked_identity(database_url, provider)
    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
        ),
        base_url="https://testserver",
    ) as client:
        login = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        authorization_url = login.headers["location"]
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        code = provider.issue_authorization_code(
            authorization_url,
            subject="subject-linked",
            email="linked@example.test",
        )
        callback = client.get(
            "/auth/callback",
            params={"state": state, "code": code},
            follow_redirects=False,
        )

        with Session(engine) as session:
            membership = session.exec(
                select(OrganizationMember).where(
                    OrganizationMember.user_id == "user_linked"
                )
            ).one()
            membership.role = WorkspaceRole.VIEWER
            session.add(membership)
            session.commit()
        after_role_change = client.get("/me")

        with Session(engine) as session:
            membership = session.exec(
                select(OrganizationMember).where(
                    OrganizationMember.user_id == "user_linked"
                )
            ).one()
            membership.status = "removed"
            session.add(membership)
            session.commit()
        after_removal = client.get("/me")

    assert callback.status_code == 303
    assert after_role_change.status_code == 200
    assert after_role_change.json()["roles"] == ["viewer"]
    assert after_removal.status_code == 401


def test_fake_customer_identity_provider_exercises_the_public_oidc_contract() -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    verifier = "deterministic-verifier"
    challenge = (
        urlsafe_b64encode(sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    discovery = provider.discover()
    authorization_url = provider.authorization_url(
        OidcAuthorizationRequest(
            client_id=provider.client_id,
            redirect_uri="https://console.example.test/auth/callback",
            state="state-value",
            nonce="nonce-value",
            code_challenge=challenge,
        )
    )
    code = provider.issue_authorization_code(
        authorization_url,
        subject="subject-contract",
        email="contract@example.test",
    )
    tokens = provider.exchange_code(
        code=code,
        redirect_uri="https://console.example.test/auth/callback",
        code_verifier=verifier,
    )
    identity = provider.validate_id_token(
        tokens.id_token,
        expected_audience=provider.client_id,
    )

    assert discovery.issuer == provider.issuer
    assert discovery.authorization_endpoint.endswith("/authorize")
    assert discovery.token_endpoint.endswith("/token")
    assert identity.subject == "subject-contract"
    assert identity.email == "contract@example.test"
    assert identity.nonce == "nonce-value"
    assert (
        provider.identity_status(
            issuer=identity.issuer,
            subject=identity.subject,
        )
        == "active"
    )
    assert provider.end_session_url(
        post_logout_redirect_uri="https://console.example.test/"
    ) == (
        "https://fake-idp.example.test/logout?"
        "post_logout_redirect_uri=https%3A%2F%2Fconsole.example.test%2F"
    )

    provider.set_unavailable()
    with pytest.raises(CustomerIdentityProviderUnavailable):
        provider.discover()


def test_identity_audit_observer_cannot_be_enabled_outside_test() -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    production_policy = AuthenticationPolicy(
        environment=AuthenticationEnvironment.PRODUCTION,
        accepted_human_credentials=frozenset(
            {HumanCredentialType.USER_SESSION}
        ),
    )

    with pytest.raises(
        ValueError,
        match="Identity Audit observer is allowed only in the test environment",
    ):
        create_app(
            database_url="sqlite://",
            authentication_policy=production_policy,
            customer_identity_provider=provider,
            identity_audit_observer_enabled=True,
        )


def test_personal_onboarding_failure_injection_cannot_be_enabled_outside_test() -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    production_policy = AuthenticationPolicy(
        environment=AuthenticationEnvironment.PRODUCTION,
        accepted_human_credentials=frozenset(
            {HumanCredentialType.USER_SESSION}
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            "Personal onboarding failure injection is allowed only "
            "in the test environment"
        ),
    ):
        create_app(
            database_url="sqlite://",
            authentication_policy=production_policy,
            customer_identity_provider=provider,
            personal_onboarding_failure_step="user",
        )


def test_login_reports_provider_unavailability_without_leaking_provider_details() -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    provider.set_unavailable()
    with TestClient(
        create_app(
            database_url="sqlite://",
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        response = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        correlation_id = response.headers["x-correlation-id"]
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": "identity_provider_unavailable",
        "correlation_id": correlation_id,
    }
    assert "fake-idp.example.test" not in response.text
    assert audit.json() == [
        {
            "event_type": "identity_provider_unavailable",
            "outcome": "failure",
            "reason_code": "discovery_unavailable",
            "correlation_id": correlation_id,
            "user_id": None,
            "external_identity_id": None,
            "device_session_id": None,
        }
    ]


@pytest.mark.parametrize(
    ("failure_kind", "status_code", "error", "event_type", "reason_code"),
    [
        (
            "unavailable",
            503,
            "identity_provider_unavailable",
            "identity_provider_unavailable",
            "authorization_unavailable",
        ),
        (
            "failure",
            502,
            "login_failed",
            "login_failure",
            "authorization_failed",
        ),
    ],
)
def test_login_handles_authorization_endpoint_failures_safely(
    failure_kind,
    status_code,
    error,
    event_type,
    reason_code,
) -> None:
    provider = DeterministicFakeCustomerIdentityProvider()
    if failure_kind == "unavailable":
        provider.set_unavailable_for("authorization")
    else:
        provider.set_failure_for("authorization")
    with TestClient(
        create_app(
            database_url="sqlite://",
            authentication_policy=USER_SESSION_TEST_POLICY,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin="https://testserver",
            identity_audit_observer_enabled=True,
        ),
        base_url="https://testserver",
    ) as client:
        response = client.get(
            "/auth/login",
            params={"return_to": "/console"},
            follow_redirects=False,
        )
        correlation_id = response.headers["x-correlation-id"]
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )

    assert response.status_code == status_code
    assert response.json() == {
        "error": error,
        "correlation_id": correlation_id,
    }
    assert "__Host-ai_scdc_login=" not in response.headers.get("set-cookie", "")
    assert "fake-idp.example.test" not in response.text
    assert [(event["event_type"], event["reason_code"]) for event in audit.json()] == [
        (event_type, reason_code)
    ]
