from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
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
from ai_company_api.services.auth_context import hash_api_token
from ai_company_api.services.customer_identity_provider import (
    DeterministicFakeCustomerIdentityProvider,
)
from ai_company_api.services.user_session_credentials import USER_SESSION_COOKIE


WEB_ORIGIN = "https://testserver"
USER_SESSION_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset({HumanCredentialType.USER_SESSION}),
)


def _seed_multi_workspace_customer(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
) -> None:
    engine = build_engine(database_url)
    init_db(engine)
    user = User(
        id="user_multi_workspace",
        email="multi@example.test",
        display_name="Multi Workspace",
    )
    first_account = Organization(
        id="account_alpha",
        name="Alpha Account",
    )
    first_workspace = Workspace(
        id="workspace_alpha_primary",
        organization_id=first_account.id,
        name="Primary",
    )
    second_workspace = Workspace(
        id="workspace_alpha_review",
        organization_id=first_account.id,
        name="Review",
    )
    second_account = Organization(
        id="account_beta",
        name="Beta Account",
    )
    third_workspace = Workspace(
        id="workspace_beta_build",
        organization_id=second_account.id,
        name="Build",
    )
    with Session(engine) as session:
        session.add(user)
        session.add(first_account)
        session.add(second_account)
        session.add(first_workspace)
        session.add(second_workspace)
        session.add(third_workspace)
        session.add(
            OrganizationMember(
                id="member_alpha_primary",
                organization_id=first_account.id,
                workspace_id=first_workspace.id,
                user_id=user.id,
                role=WorkspaceRole.OWNER,
            )
        )
        session.add(
            OrganizationMember(
                id="member_alpha_review",
                organization_id=first_account.id,
                workspace_id=second_workspace.id,
                user_id=user.id,
                role=WorkspaceRole.REVIEWER,
            )
        )
        session.add(
            OrganizationMember(
                id="member_beta_build",
                organization_id=second_account.id,
                workspace_id=third_workspace.id,
                user_id=user.id,
                role=WorkspaceRole.DEVELOPER,
            )
        )
        session.add(
            ExternalIdentity(
                issuer=provider.issuer,
                subject="subject-multi-workspace",
                user_id=user.id,
                email=user.email,
            )
        )
        session.commit()


def _sign_in(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
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
        subject="subject-multi-workspace",
        email="multi@example.test",
    )
    callback = client.get(
        "/auth/callback",
        params={"state": state, "code": code},
        follow_redirects=False,
    )
    assert callback.status_code == 303


def _build_app(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
):
    return create_app(
        database_url=database_url,
        authentication_policy=USER_SESSION_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin=WEB_ORIGIN,
        identity_audit_observer_enabled=True,
    )


def _csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/auth/csrf")
    assert response.status_code == 200
    return {
        "Origin": WEB_ORIGIN,
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def test_me_lists_every_live_account_workspace_and_role_without_credentials(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'workspace-list.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_multi_workspace_customer(database_url, provider)

    with TestClient(
        _build_app(database_url, provider),
        base_url=WEB_ORIGIN,
    ) as client:
        _sign_in(client, provider)
        response = client.get("/me")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "user_multi_workspace"
    assert body["selection_state"] == "selected"
    assert {
        account["id"]: {
            workspace["id"]: workspace["role"]
            for workspace in account["workspaces"]
        }
        for account in body["accounts"]
    } == {
        "account_alpha": {
            "workspace_alpha_primary": "owner",
            "workspace_alpha_review": "reviewer",
        },
        "account_beta": {
            "workspace_beta_build": "developer",
        },
    }
    serialized = response.text.casefold()
    assert "secret" not in serialized
    assert "token" not in serialized
    assert "credential" not in serialized


def test_workspace_switch_updates_only_the_current_device_session_and_is_audited(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'workspace-switch.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_multi_workspace_customer(database_url, provider)
    app = _build_app(database_url, provider)

    with (
        TestClient(app, base_url=WEB_ORIGIN) as first_device,
        TestClient(app, base_url=WEB_ORIGIN) as second_device,
    ):
        _sign_in(first_device, provider)
        _sign_in(second_device, provider)

        switched = first_device.put(
            "/auth/workspace-selection",
            headers=_csrf_headers(first_device),
            json={"workspace_id": "workspace_beta_build"},
        )
        first_me = first_device.get("/me")
        second_me = second_device.get("/me")
        persisted_session_cookie = first_device.cookies.get(USER_SESSION_COOKIE)
        audit = first_device.get(
            "/auth/test/audit-events",
            params={"correlation_id": switched.headers["X-Correlation-ID"]},
        )
    assert persisted_session_cookie is not None
    with TestClient(app, base_url=WEB_ORIGIN) as returning_device:
        returning_device.cookies.set(
            USER_SESSION_COOKIE,
            persisted_session_cookie,
        )
        returned_me = returning_device.get("/me")

    assert switched.status_code == 204
    assert first_me.status_code == 200
    assert first_me.json()["workspace_id"] == "workspace_beta_build"
    assert first_me.json()["organization_id"] == "account_beta"
    assert first_me.json()["roles"] == ["developer"]
    assert second_me.status_code == 200
    assert second_me.json()["workspace_id"] == "workspace_alpha_primary"
    assert second_me.json()["organization_id"] == "account_alpha"
    assert returned_me.status_code == 200
    assert returned_me.json()["workspace_id"] == "workspace_beta_build"
    assert [(event["event_type"], event["reason_code"]) for event in audit.json()] == [
        ("workspace_selection_changed", "user_selected_workspace")
    ]
    event = audit.json()[0]
    assert event["outcome"] == "success"
    assert event["user_id"] == "user_multi_workspace"
    assert event["device_session_id"]
    serialized = audit.text.casefold()
    assert "secret" not in serialized
    assert "token" not in serialized


def test_inaccessible_workspace_selection_is_rejected_without_changing_context(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'workspace-rejected.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_multi_workspace_customer(database_url, provider)

    with TestClient(
        _build_app(database_url, provider),
        base_url=WEB_ORIGIN,
    ) as client:
        _sign_in(client, provider)
        rejected = client.put(
            "/auth/workspace-selection",
            headers=_csrf_headers(client),
            json={"workspace_id": "workspace_not_accessible"},
        )
        unchanged = client.get("/me")
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": rejected.headers["X-Correlation-ID"]},
        )

    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "workspace_not_accessible"
    assert unchanged.status_code == 200
    assert unchanged.json()["workspace_id"] == "workspace_alpha_primary"
    assert [(event["event_type"], event["reason_code"]) for event in audit.json()] == [
        ("workspace_selection_rejected", "workspace_not_accessible")
    ]


def test_lost_workspace_access_requires_an_explicit_safe_reselection_without_retry(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'workspace-lost.db').as_posix()}"
    engine = build_engine(database_url)
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_multi_workspace_customer(database_url, provider)

    with TestClient(
        _build_app(database_url, provider),
        base_url=WEB_ORIGIN,
    ) as client:
        _sign_in(client, provider)
        csrf_headers = _csrf_headers(client)
        with Session(engine) as session:
            membership = session.get(OrganizationMember, "member_alpha_primary")
            assert membership is not None
            membership.status = "removed"
            session.add(membership)
            session.commit()

        safe_identity = client.get("/me")
        rejected_write = client.post(
            "/projects",
            headers=csrf_headers,
            json={"name": "Must not be replayed"},
        )
        loss_audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": rejected_write.headers["X-Correlation-ID"],
            },
        )
        first_reselection = client.put(
            "/auth/workspace-selection",
            headers=csrf_headers,
            json={"workspace_id": "workspace_alpha_review"},
        )
        first_workspace_projects = client.get("/projects")
        switched = client.put(
            "/auth/workspace-selection",
            headers=csrf_headers,
            json={"workspace_id": "workspace_beta_build"},
        )
        second_workspace_projects = client.get("/projects")
        restored_identity = client.get("/me")

    assert safe_identity.status_code == 200
    safe_body = safe_identity.json()
    assert safe_body["selection_state"] == "selection_required"
    assert safe_body["workspace_id"] is None
    assert safe_body["organization_id"] is None
    assert safe_body["roles"] == []
    assert safe_body["current_account"] is None
    assert safe_body["current_workspace"] is None
    assert {
        workspace["id"]
        for account in safe_body["accounts"]
        for workspace in account["workspaces"]
    } == {"workspace_alpha_review", "workspace_beta_build"}

    assert rejected_write.status_code == 409
    assert rejected_write.json()["detail"] == "workspace_access_lost"
    assert [
        (event["event_type"], event["reason_code"])
        for event in loss_audit.json()
    ] == [("workspace_authorization_denied", "workspace_access_lost")]
    assert first_reselection.status_code == 204
    assert first_workspace_projects.status_code == 200
    assert first_workspace_projects.json() == []
    assert second_workspace_projects.status_code == 200
    assert second_workspace_projects.json() == []

    assert switched.status_code == 204
    assert restored_identity.status_code == 200
    assert restored_identity.json()["selection_state"] == "selected"
    assert restored_identity.json()["workspace_id"] == "workspace_beta_build"


def test_live_role_changes_grant_and_deny_the_next_protected_request_with_audit(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'workspace-role.db').as_posix()}"
    engine = build_engine(database_url)
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_multi_workspace_customer(database_url, provider)

    with TestClient(
        _build_app(database_url, provider),
        base_url=WEB_ORIGIN,
    ) as client:
        _sign_in(client, provider)
        headers = _csrf_headers(client)
        selected = client.put(
            "/auth/workspace-selection",
            headers=headers,
            json={"workspace_id": "workspace_beta_build"},
        )
        assert selected.status_code == 204

        denied_as_developer = client.get("/github-credentials")
        with Session(engine) as session:
            membership = session.get(OrganizationMember, "member_beta_build")
            assert membership is not None
            membership.role = WorkspaceRole.ADMIN
            session.add(membership)
            session.commit()
        granted_as_admin = client.get("/github-credentials")
        with Session(engine) as session:
            membership = session.get(OrganizationMember, "member_beta_build")
            assert membership is not None
            membership.role = WorkspaceRole.VIEWER
            session.add(membership)
            session.commit()
        denied_as_viewer = client.get("/github-credentials")
        current_identity = client.get("/me")
        denial_audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": denied_as_viewer.headers["X-Correlation-ID"],
            },
        )

    assert denied_as_developer.status_code == 403
    assert denied_as_developer.headers["X-Correlation-ID"]
    assert granted_as_admin.status_code == 200
    assert granted_as_admin.json() == []
    assert denied_as_viewer.status_code == 403
    assert denied_as_viewer.json()["detail"] == "Insufficient workspace role"
    assert current_identity.status_code == 200
    assert current_identity.json()["roles"] == ["viewer"]
    assert [
        (event["event_type"], event["reason_code"])
        for event in denial_audit.json()
    ] == [("workspace_authorization_denied", "insufficient_workspace_role")]


def test_workspace_api_token_keeps_me_scoped_to_its_single_workspace(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'api-token-scope.db').as_posix()}"
    engine = build_engine(database_url)
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_multi_workspace_customer(database_url, provider)
    token = "workspace-token-must-not-list-other-memberships"
    with Session(engine) as session:
        membership = session.get(OrganizationMember, "member_alpha_primary")
        assert membership is not None
        membership.api_token_hash = hash_api_token(token)
        session.add(membership)
        session.commit()
    policy = AuthenticationPolicy(
        environment=AuthenticationEnvironment.TEST,
        accepted_human_credentials=frozenset(
            {
                HumanCredentialType.USER_SESSION,
                HumanCredentialType.WORKSPACE_API_TOKEN,
            }
        ),
    )

    with TestClient(
        create_app(
            database_url=database_url,
            authentication_policy=policy,
            customer_identity_provider=provider,
            allowed_login_return_destinations=frozenset({"/console"}),
            public_origin=WEB_ORIGIN,
        ),
        base_url=WEB_ORIGIN,
    ) as client:
        response = client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["auth_mode"] == "api_token"
    assert [
        workspace["id"]
        for account in body["accounts"]
        for workspace in account["workspaces"]
    ] == ["workspace_alpha_primary"]
