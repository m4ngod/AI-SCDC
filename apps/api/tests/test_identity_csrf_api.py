from datetime import datetime, timedelta, timezone
import subprocess
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    CloudRun,
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
from ai_company_api.services.worker_callback_auth import hash_callback_token
from ai_company_api.services.user_session_credentials import USER_SESSION_COOKIE


WEB_APPLICATION_ORIGIN = "https://testserver"
WORKSPACE_API_TOKEN = "workspace-api-token-csrf-boundary"
WORKER_CALLBACK_TOKEN = "worker-callback-token-csrf-boundary"
WORKER_ID = "csrf-boundary-worker"
WEB_CONSOLE_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {
            HumanCredentialType.USER_SESSION,
            HumanCredentialType.WORKSPACE_API_TOKEN,
        }
    ),
)
def _build_app(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
    *,
    clock=lambda: datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
):
    return create_app(
        database_url=database_url,
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin=WEB_APPLICATION_ORIGIN,
        identity_audit_observer_enabled=True,
        identity_clock=clock,
    )


def _sign_in(
    client: TestClient,
    provider: DeterministicFakeCustomerIdentityProvider,
    *,
    subject: str = "csrf-user",
    email: str = "csrf-user@example.test",
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
        "Origin": WEB_APPLICATION_ORIGIN,
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def _seed_machine_and_api_credentials(database_url: str) -> None:
    engine = build_engine(database_url)
    init_db(engine)
    user = User(
        id="user_csrf_api_token",
        email="csrf-api-token@example.test",
        display_name="CSRF API token user",
    )
    account = Organization(
        id="account_csrf_api_token",
        name="CSRF API token account",
    )
    workspace = Workspace(
        id="workspace_csrf_api_token",
        organization_id=account.id,
        name="CSRF API token workspace",
    )
    membership = OrganizationMember(
        id="member_csrf_api_token",
        organization_id=account.id,
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceRole.OWNER,
        api_token_hash=hash_api_token(WORKSPACE_API_TOKEN),
    )
    cloud_run = CloudRun(
        id="cloud_run_csrf_worker",
        workspace_id=workspace.id,
        project_id="project_csrf_worker",
        task_id="task_csrf_worker",
        repo_id="repo_csrf_worker",
        head_branch="codex/csrf-worker",
        status="running",
        queue_provider="local_db",
        worker_id=WORKER_ID,
        lease_id="lease_csrf_worker",
        lease_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        callback_token_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    cloud_run.callback_token_hash = hash_callback_token(
        cloud_run.id,
        WORKER_ID,
        WORKER_CALLBACK_TOKEN,
    )
    with Session(engine) as session:
        session.add(user)
        session.add(account)
        session.add(workspace)
        session.add(membership)
        session.add(cloud_run)
        session.commit()


def test_cookie_get_is_safe_but_unsafe_request_requires_csrf_before_mutation(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'csrf-required.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    app = _build_app(database_url, provider)

    with TestClient(app, base_url=WEB_APPLICATION_ORIGIN) as client:
        _sign_in(client, provider)

        safe_read = client.get(
            "/me",
            headers={"Origin": WEB_APPLICATION_ORIGIN},
        )
        rejected_write = client.post(
            "/projects",
            headers={"Origin": WEB_APPLICATION_ORIGIN},
            json={"name": "Must not be created"},
        )
        projects_after_rejection = client.get("/projects")

        correlation_id = rejected_write.headers["X-Correlation-ID"]
        audit = client.get(
            "/auth/test/audit-events",
            params={"correlation_id": correlation_id},
        )

    assert safe_read.status_code == 200
    assert rejected_write.status_code == 403
    assert rejected_write.json()["detail"] == "csrf_token_required"
    assert projects_after_rejection.status_code == 200
    assert projects_after_rejection.json() == []
    assert audit.status_code == 200
    assert len(audit.json()) == 1
    event = audit.json()[0]
    assert event["event_type"] == "csrf_rejected"
    assert event["outcome"] == "failure"
    assert event["reason_code"] == "csrf_token_required"
    assert event["correlation_id"] == correlation_id
    assert event["user_id"] == safe_read.json()["user_id"]
    assert event["external_identity_id"] is None
    assert event["device_session_id"]


def test_valid_session_bound_csrf_allows_every_unsafe_http_method(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'csrf-valid.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    app = _build_app(database_url, provider)
    repository_path = tmp_path / "repository"
    subprocess.run(
        ["git", "init", str(repository_path)],
        check=True,
        capture_output=True,
    )

    with TestClient(app, base_url=WEB_APPLICATION_ORIGIN) as client:
        _sign_in(client, provider)
        headers = _csrf_headers(client)

        created_project = client.post(
            "/projects",
            headers=headers,
            json={"name": "CSRF protected project"},
        )
        project_id = created_project.json()["id"]
        created_repository = client.post(
            f"/projects/{project_id}/repositories",
            headers=headers,
            json={
                "name": "CSRF protected repository",
                "local_path": str(repository_path),
            },
        )
        assert created_repository.status_code == 201, created_repository.text
        repository_id = created_repository.json()["id"]
        updated_limit = client.put(
            "/workspace/spend-limit",
            headers=headers,
            json={
                "monthly_limit_cents": 1_000,
                "per_run_limit_cents": 100,
            },
        )
        created_task = client.post(
            f"/projects/{project_id}/tasks",
            headers=headers,
            json={"title": "CSRF protected task", "role_required": "backend"},
        )
        updated_task = client.patch(
            f"/tasks/{created_task.json()['id']}",
            headers=headers,
            json={"status": "ASSIGNED"},
        )
        deleted_repository = client.delete(
            f"/repositories/{repository_id}",
            headers=headers,
        )

    assert created_project.status_code == 201
    assert created_repository.status_code == 201
    assert updated_limit.status_code == 200
    assert updated_limit.json()["monthly_limit_cents"] == 1_000
    assert updated_task.status_code == 200
    assert updated_task.json()["status"] == "ASSIGNED"
    assert deleted_repository.status_code == 200
    assert deleted_repository.json()["status"] == "deleted"


def test_origin_and_csrf_failures_are_correlated_and_never_mutate(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'csrf-rejections.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    current_time = [datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)]
    app = _build_app(
        database_url,
        provider,
        clock=lambda: current_time[0],
    )

    with (
        TestClient(app, base_url=WEB_APPLICATION_ORIGIN) as first_device,
        TestClient(app, base_url=WEB_APPLICATION_ORIGIN) as second_device,
    ):
        _sign_in(first_device, provider)
        first_token = _csrf_headers(first_device)["X-CSRF-Token"]
        _sign_in(second_device, provider)
        second_token = _csrf_headers(second_device)["X-CSRF-Token"]
        second_session_cookie = second_device.cookies.get(USER_SESSION_COOKIE)
        assert second_session_cookie is not None
        replacement = "0" if second_token[-1] != "0" else "1"
        tampered_token = f"{second_token[:-1]}{replacement}"

        cases = [
            (
                {},
                "origin_required",
            ),
            (
                {
                    "Origin": "https://cross-site.example.test",
                    "X-CSRF-Token": second_token,
                },
                "origin_mismatch",
            ),
            (
                {"Origin": WEB_APPLICATION_ORIGIN},
                "csrf_token_required",
            ),
            (
                {
                    "Origin": WEB_APPLICATION_ORIGIN,
                    "X-CSRF-Token": "malformed-csrf-secret",
                },
                "csrf_token_malformed",
            ),
            (
                {
                    "Origin": WEB_APPLICATION_ORIGIN,
                    "X-CSRF-Token": first_token,
                },
                "csrf_token_mismatch",
            ),
            (
                {
                    "Origin": WEB_APPLICATION_ORIGIN,
                    "X-CSRF-Token": tampered_token,
                },
                "csrf_token_mismatch",
            ),
        ]
        responses = []
        audits = []
        for index, (headers, reason_code) in enumerate(cases):
            response = second_device.post(
                "/projects",
                headers=headers,
                json={"name": f"Rejected project {index}"},
            )
            responses.append((response, reason_code))
            audits.append(
                second_device.get(
                    "/auth/test/audit-events",
                    params={
                        "correlation_id": response.headers["X-Correlation-ID"],
                    },
                )
            )

        current_time[0] += timedelta(hours=1, seconds=1)
        expired = second_device.post(
            "/projects",
            headers={
                "Origin": WEB_APPLICATION_ORIGIN,
                "X-CSRF-Token": second_token,
            },
            json={"name": "Rejected expired CSRF project"},
        )
        responses.append((expired, "csrf_token_expired"))
        audits.append(
            second_device.get(
                "/auth/test/audit-events",
                params={
                    "correlation_id": expired.headers["X-Correlation-ID"],
                },
            )
        )
        projects = second_device.get("/projects")

    for (response, reason_code), audit in zip(
        responses,
        audits,
        strict=True,
    ):
        assert response.status_code == 403
        assert response.json()["detail"] == reason_code
        serialized_response = f"{response.headers} {response.text}"
        assert first_token not in serialized_response
        assert second_token not in serialized_response
        assert second_session_cookie not in serialized_response
        assert "malformed-csrf-secret" not in serialized_response
        assert audit.status_code == 200
        assert len(audit.json()) == 1
        assert audit.json()[0]["reason_code"] == reason_code
        assert audit.json()[0]["event_type"] in {
            "origin_rejected",
            "csrf_rejected",
        }
        serialized_audit = str(audit.json())
        assert first_token not in serialized_audit
        assert second_token not in serialized_audit
        assert second_session_cookie not in serialized_audit
        assert "malformed-csrf-secret" not in serialized_audit
    assert projects.status_code == 200
    assert projects.json() == []


def test_api_and_worker_tokens_remain_outside_browser_csrf_protection(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'csrf-non-browser-credentials.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_machine_and_api_credentials(database_url)
    app = _build_app(database_url, provider)

    with TestClient(app, base_url=WEB_APPLICATION_ORIGIN) as client:
        api_token_write = client.post(
            "/projects",
            headers={"Authorization": f"Bearer {WORKSPACE_API_TOKEN}"},
            json={"name": "API token project"},
        )
        worker_callback = client.post(
            "/cloud-run-worker/leases/lease_csrf_worker/heartbeat",
            json={
                "worker_id": WORKER_ID,
                "callback_token": WORKER_CALLBACK_TOKEN,
                "lease_seconds": 60,
            },
        )

    assert api_token_write.status_code == 201
    assert api_token_write.json()["name"] == "API token project"
    assert worker_callback.status_code == 200
    assert (
        worker_callback.json()["cloud_run"]["id"]
        == "cloud_run_csrf_worker"
    )


def test_wildcard_cors_never_enables_browser_credentials(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'csrf-cors.db').as_posix()}"
    provider = DeterministicFakeCustomerIdentityProvider()
    app = create_app(
        database_url=database_url,
        cors_origins=("*",),
        authentication_policy=WEB_CONSOLE_POLICY,
        customer_identity_provider=provider,
        public_origin=WEB_APPLICATION_ORIGIN,
    )

    with TestClient(app, base_url=WEB_APPLICATION_ORIGIN) as client:
        response = client.options(
            "/projects",
            headers={
                "Origin": "https://cross-site.example.test",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
