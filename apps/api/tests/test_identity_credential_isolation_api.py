from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    CloudRun,
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
from ai_company_api.services.worker_callback_auth import hash_callback_token


HUMAN_CREDENTIAL_TEST_POLICY = AuthenticationPolicy(
    environment=AuthenticationEnvironment.TEST,
    accepted_human_credentials=frozenset(
        {
            HumanCredentialType.USER_SESSION,
            HumanCredentialType.WORKSPACE_API_TOKEN,
        }
    ),
)
WORKSPACE_API_TOKEN = "scdc_workspace_api_token_secret"
WORKER_CALLBACK_TOKEN = "worker-callback-token-secret"
WORKER_ID = "worker-credential-isolation"


def _seed_human_credentials(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
) -> None:
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        user = User(
            id="user_credential_isolation",
            email="credential-isolation@example.test",
            display_name="Credential isolation",
        )
        account = Organization(
            id="account_credential_isolation",
            name="Credential isolation account",
        )
        workspace = Workspace(
            id="workspace_credential_isolation",
            organization_id=account.id,
            name="Credential isolation workspace",
        )
        membership = OrganizationMember(
            id="member_credential_isolation",
            organization_id=account.id,
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.OWNER,
            api_token_hash=hash_api_token(WORKSPACE_API_TOKEN),
        )
        identity = ExternalIdentity(
            issuer=provider.issuer,
            subject="subject-credential-isolation",
            user_id=user.id,
            email=user.email,
        )
        session.add(user)
        session.add(account)
        session.add(workspace)
        session.add(membership)
        session.add(identity)
        session.commit()


def _credential_isolation_app(
    database_url: str,
    provider: DeterministicFakeCustomerIdentityProvider,
):
    return create_app(
        database_url=database_url,
        authentication_policy=HUMAN_CREDENTIAL_TEST_POLICY,
        customer_identity_provider=provider,
        allowed_login_return_destinations=frozenset({"/console"}),
        public_origin="https://testserver",
        identity_audit_observer_enabled=True,
    )


def _seed_worker_callbacks(database_url: str) -> None:
    engine = build_engine(database_url)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    queued_run = CloudRun(
        id="cloud_run_credential_isolation_queued",
        workspace_id="workspace_credential_isolation",
        project_id="project_credential_isolation",
        task_id="task_credential_isolation",
        repo_id="repo_credential_isolation",
        head_branch="codex/credential-isolation",
        queue_provider="local_db",
        callback_token_expires_at=expires_at,
    )
    queued_run.callback_token_hash = hash_callback_token(
        queued_run.id,
        WORKER_ID,
        WORKER_CALLBACK_TOKEN,
    )
    running_run = CloudRun(
        id="cloud_run_credential_isolation_running",
        workspace_id="workspace_credential_isolation",
        project_id="project_credential_isolation",
        task_id="task_credential_isolation",
        repo_id="repo_credential_isolation",
        head_branch="codex/credential-isolation",
        status="running",
        queue_provider="local_db",
        worker_id=WORKER_ID,
        lease_id="lease_credential_isolation",
        lease_expires_at=expires_at,
        callback_token_expires_at=expires_at,
    )
    running_run.callback_token_hash = hash_callback_token(
        running_run.id,
        WORKER_ID,
        WORKER_CALLBACK_TOKEN,
    )
    tokenless_queued_run = CloudRun(
        id="cloud_run_credential_isolation_tokenless_queued",
        workspace_id="workspace_credential_isolation",
        project_id="project_credential_isolation",
        task_id="task_credential_isolation",
        repo_id="repo_credential_isolation",
        head_branch="codex/credential-isolation",
        queue_provider="local_db",
    )
    tokenless_running_run = CloudRun(
        id="cloud_run_credential_isolation_tokenless_running",
        workspace_id="workspace_credential_isolation",
        project_id="project_credential_isolation",
        task_id="task_credential_isolation",
        repo_id="repo_credential_isolation",
        head_branch="codex/credential-isolation",
        status="running",
        queue_provider="local_db",
        worker_id=WORKER_ID,
        lease_id="lease_credential_isolation_tokenless",
        lease_expires_at=expires_at,
    )
    with Session(engine) as session:
        session.add(queued_run)
        session.add(running_run)
        session.add(tokenless_queued_run)
        session.add(tokenless_running_run)
        session.commit()


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
        subject="subject-credential-isolation",
        email="credential-isolation@example.test",
    )
    callback = client.get(
        "/auth/callback",
        params={"state": state, "code": code},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    return client.cookies.get(USER_SESSION_COOKIE)


def _set_session_cookie(client: TestClient, value: str) -> None:
    client.cookies.set(
        USER_SESSION_COOKIE,
        value,
        domain="testserver.local",
        path="/",
    )


def test_cookie_and_bearer_are_rejected_before_either_credential_is_selected(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'ambiguous-credentials.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_human_credentials(database_url, provider)
    app = _credential_isolation_app(database_url, provider)

    with TestClient(app, base_url="https://testserver") as signed_in:
        valid_cookie = _sign_in(signed_in, provider)

    cases = (
        (valid_cookie, WORKSPACE_API_TOKEN),
        (valid_cookie, "invalid-workspace-api-token"),
        ("invalid-device-session.invalid-session-secret", WORKSPACE_API_TOKEN),
    )
    responses_and_audits = []
    for cookie_value, bearer_value in cases:
        with TestClient(app, base_url="https://testserver") as client:
            _set_session_cookie(client, cookie_value)
            response = client.get(
                "/me",
                headers={"Authorization": f"Bearer {bearer_value}"},
            )
            audit = client.get(
                "/auth/test/audit-events",
                params={
                    "correlation_id": response.headers["x-correlation-id"],
                },
            )
            responses_and_audits.append((response, audit))

    for response, audit in responses_and_audits:
        assert response.status_code == 400
        assert response.json() == {"detail": "ambiguous_credentials"}
        assert audit.json() == [
            {
                "event_type": "authentication_failure",
                "outcome": "failure",
                "reason_code": "ambiguous_credentials",
                "correlation_id": response.headers["x-correlation-id"],
                "user_id": None,
                "external_identity_id": None,
                "device_session_id": None,
            }
        ]
        serialized_result = f"{response.text}{audit.text}"
        assert valid_cookie not in serialized_result
        assert WORKSPACE_API_TOKEN not in serialized_result
        assert "invalid-workspace-api-token" not in serialized_result
        assert "invalid-session-secret" not in serialized_result


def test_human_credentials_resolve_live_authority_and_token_status(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'live-human-authority.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_human_credentials(database_url, provider)
    app = _credential_isolation_app(database_url, provider)
    engine = build_engine(database_url)

    def request_with_each_credential(
        session_client: TestClient,
        token_client: TestClient,
    ):
        return (
            session_client.get("/me"),
            token_client.get(
                "/me",
                headers={
                    "Authorization": f"Bearer {WORKSPACE_API_TOKEN}",
                },
            ),
        )

    def set_scope_state(
        *,
        user_status: str = "active",
        account_status: str = "active",
        workspace_status: str = "active",
        membership_status: str = "active",
        role: WorkspaceRole = WorkspaceRole.OWNER,
        token_active: bool = True,
    ) -> None:
        with Session(engine) as session:
            user = session.get(User, "user_credential_isolation")
            account = session.get(
                Organization,
                "account_credential_isolation",
            )
            workspace = session.get(
                Workspace,
                "workspace_credential_isolation",
            )
            membership = session.get(
                OrganizationMember,
                "member_credential_isolation",
            )
            assert user is not None
            assert account is not None
            assert workspace is not None
            assert membership is not None
            user.status = user_status
            account.status = account_status
            workspace.status = workspace_status
            membership.status = membership_status
            membership.role = role
            membership.api_token_hash = (
                hash_api_token(WORKSPACE_API_TOKEN)
                if token_active
                else None
            )
            session.add(user)
            session.add(account)
            session.add(workspace)
            session.add(membership)
            session.commit()

    with (
        TestClient(app, base_url="https://testserver") as session_client,
        TestClient(app, base_url="https://testserver") as token_client,
    ):
        _sign_in(session_client, provider)
        initial = request_with_each_credential(
            session_client,
            token_client,
        )

        set_scope_state(role=WorkspaceRole.VIEWER)
        changed_role = request_with_each_credential(
            session_client,
            token_client,
        )

        rejected_states = []
        for state in (
            {"membership_status": "revoked"},
            {"user_status": "disabled"},
            {"account_status": "disabled"},
            {"workspace_status": "disabled"},
        ):
            set_scope_state(**state)
            rejected_states.append(
                request_with_each_credential(
                    session_client,
                    token_client,
                )
            )
            set_scope_state(role=WorkspaceRole.VIEWER)

        set_scope_state(role=WorkspaceRole.VIEWER, token_active=False)
        revoked_token = request_with_each_credential(
            session_client,
            token_client,
        )

    for response in initial:
        assert response.status_code == 200
        assert response.json()["roles"] == ["owner"]
    for response in changed_role:
        assert response.status_code == 200
        assert response.json()["roles"] == ["viewer"]
    for session_response, token_response in rejected_states:
        assert session_response.status_code == 401
        assert token_response.status_code == 401
    assert revoked_token[0].status_code == 200
    assert revoked_token[0].json()["roles"] == ["viewer"]
    assert revoked_token[1].status_code == 401


def test_invalid_human_credentials_are_audited_without_raw_secrets(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'human-auth-failures.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_human_credentials(database_url, provider)
    app = _credential_isolation_app(database_url, provider)
    invalid_api_token = "invalid-api-token-secret"
    invalid_session = "invalid-session-id.invalid-session-secret"

    with TestClient(app, base_url="https://testserver") as client:
        api_token_failure = client.get(
            "/me",
            headers={"Authorization": f"Bearer {invalid_api_token}"},
        )
        api_token_audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": api_token_failure.headers[
                    "x-correlation-id"
                ],
            },
        )

        _set_session_cookie(client, invalid_session)
        session_failure = client.get("/me")
        session_audit = client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": session_failure.headers[
                    "x-correlation-id"
                ],
            },
        )

    assert api_token_failure.status_code == 401
    assert api_token_failure.json() == {"detail": "Invalid API token"}
    assert api_token_audit.json() == [
        {
            "event_type": "authentication_failure",
            "outcome": "failure",
            "reason_code": "invalid_workspace_api_token",
            "correlation_id": api_token_failure.headers["x-correlation-id"],
            "user_id": None,
            "external_identity_id": None,
            "device_session_id": None,
        }
    ]
    assert session_failure.status_code == 401
    assert session_failure.json() == {"detail": "User Session is not valid"}
    assert session_audit.json() == [
        {
            "event_type": "authentication_failure",
            "outcome": "failure",
            "reason_code": "invalid_session_credential",
            "correlation_id": session_failure.headers["x-correlation-id"],
            "user_id": None,
            "external_identity_id": None,
            "device_session_id": None,
        }
    ]
    serialized_result = (
        f"{api_token_failure.text}{api_token_audit.text}"
        f"{session_failure.text}{session_audit.text}"
    )
    assert invalid_api_token not in serialized_result
    assert invalid_session not in serialized_result
    assert "invalid-session-secret" not in serialized_result


def test_worker_callback_credentials_remain_route_and_lifecycle_isolated(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'worker-credential-isolation.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_human_credentials(database_url, provider)
    _seed_worker_callbacks(database_url)
    app = _credential_isolation_app(database_url, provider)
    invalid_worker_token = "invalid-worker-callback-secret"
    lease_request = {
        "worker_id": WORKER_ID,
        "worker_kind": "remote_stub",
        "queue_provider": "local_db",
        "cloud_run_id": "cloud_run_credential_isolation_queued",
        "lease_seconds": 60,
    }

    with (
        TestClient(app, base_url="https://testserver") as session_client,
        TestClient(app, base_url="https://testserver") as token_client,
    ):
        _sign_in(session_client, provider)

        session_as_worker = session_client.post(
            "/cloud-run-worker/leases",
            json=lease_request,
        )
        api_token_as_worker = token_client.post(
            "/cloud-run-worker/leases",
            json=lease_request,
            headers={
                "Authorization": f"Bearer {WORKSPACE_API_TOKEN}",
            },
        )
        worker_failure_audits = [
            client.get(
                "/auth/test/audit-events",
                params={
                    "correlation_id": response.headers[
                        "x-correlation-id"
                    ],
                },
            )
            for client, response in (
                (session_client, session_as_worker),
                (token_client, api_token_as_worker),
            )
        ]
        invalid_worker_callback = token_client.post(
            "/cloud-run-worker/leases",
            json={
                **lease_request,
                "callback_token": invalid_worker_token,
            },
        )
        invalid_worker_audit = token_client.get(
            "/auth/test/audit-events",
            params={
                "correlation_id": invalid_worker_callback.headers[
                    "x-correlation-id"
                ],
            },
        )

        worker_token_on_me = token_client.get(
            "/me",
            headers={
                "Authorization": f"Bearer {WORKER_CALLBACK_TOKEN}",
            },
        )
        worker_token_on_control_plane = token_client.get(
            "/projects",
            headers={
                "Authorization": f"Bearer {WORKER_CALLBACK_TOKEN}",
            },
        )

        with Session(build_engine(database_url)) as session:
            user = session.get(User, "user_credential_isolation")
            assert user is not None
            user.status = "disabled"
            session.add(user)
            session.commit()

        disabled_session = session_client.get("/me")
        disabled_api_token = token_client.get(
            "/me",
            headers={
                "Authorization": f"Bearer {WORKSPACE_API_TOKEN}",
            },
        )
        independent_callback = token_client.post(
            "/cloud-run-worker/leases/lease_credential_isolation/heartbeat",
            json={
                "worker_id": WORKER_ID,
                "callback_token": WORKER_CALLBACK_TOKEN,
                "lease_seconds": 60,
            },
        )

    for response in (session_as_worker, api_token_as_worker):
        assert response.status_code == 401
        assert response.json() == {
            "detail": "Worker callback token is required",
        }
    for response, audit in zip(
        (session_as_worker, api_token_as_worker),
        worker_failure_audits,
        strict=True,
    ):
        assert audit.json() == [
            {
                "event_type": "authentication_failure",
                "outcome": "failure",
                "reason_code": "worker_callback_token_required",
                "correlation_id": response.headers["x-correlation-id"],
                "user_id": None,
                "external_identity_id": None,
                "device_session_id": None,
            }
        ]
    assert invalid_worker_callback.status_code == 403
    assert invalid_worker_callback.json() == {
        "detail": "Worker callback token is not valid",
    }
    assert invalid_worker_audit.json() == [
        {
            "event_type": "authentication_failure",
            "outcome": "failure",
            "reason_code": "worker_callback_token_invalid",
            "correlation_id": invalid_worker_callback.headers[
                "x-correlation-id"
            ],
            "user_id": None,
            "external_identity_id": None,
            "device_session_id": None,
        }
    ]
    for response in (
        worker_token_on_me,
        worker_token_on_control_plane,
        disabled_session,
        disabled_api_token,
    ):
        assert response.status_code == 401
    assert independent_callback.status_code == 200
    assert independent_callback.json()["cloud_run"]["id"] == (
        "cloud_run_credential_isolation_running"
    )
    serialized_results = "".join(
        response.text
        for response in (
            session_as_worker,
            api_token_as_worker,
            *worker_failure_audits,
            invalid_worker_callback,
            invalid_worker_audit,
            worker_token_on_me,
            worker_token_on_control_plane,
            disabled_session,
            disabled_api_token,
            independent_callback,
        )
    )
    assert WORKSPACE_API_TOKEN not in serialized_results
    assert WORKER_CALLBACK_TOKEN not in serialized_results
    assert invalid_worker_token not in serialized_results


def test_production_style_policy_requires_callback_token_for_every_callback(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'tokenless-worker-callbacks.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_human_credentials(database_url, provider)
    _seed_worker_callbacks(database_url)
    app = _credential_isolation_app(database_url, provider)

    with (
        TestClient(app, base_url="https://testserver") as session_client,
        TestClient(app, base_url="https://testserver") as token_client,
        TestClient(app, base_url="https://testserver") as anonymous_client,
    ):
        _sign_in(session_client, provider)
        responses = (
            session_client.post(
                "/cloud-run-worker/leases",
                json={
                    "worker_id": WORKER_ID,
                    "worker_kind": "remote_stub",
                    "queue_provider": "local_db",
                    "cloud_run_id": (
                        "cloud_run_credential_isolation_tokenless_queued"
                    ),
                    "callback_token": WORKER_CALLBACK_TOKEN,
                    "lease_seconds": 60,
                },
            ),
            token_client.post(
                (
                    "/cloud-run-worker/leases/"
                    "lease_credential_isolation_tokenless/heartbeat"
                ),
                json={
                    "worker_id": WORKER_ID,
                    "callback_token": WORKER_CALLBACK_TOKEN,
                    "lease_seconds": 60,
                },
                headers={
                    "Authorization": f"Bearer {WORKSPACE_API_TOKEN}",
                },
            ),
            anonymous_client.post(
                (
                    "/cloud-run-worker/leases/"
                    "lease_credential_isolation_tokenless/payload"
                ),
                json={
                    "worker_id": WORKER_ID,
                    "callback_token": WORKER_CALLBACK_TOKEN,
                },
            ),
            session_client.post(
                (
                    "/cloud-run-worker/leases/"
                    "lease_credential_isolation_tokenless/artifacts"
                ),
                json={
                    "worker_id": WORKER_ID,
                    "callback_token": WORKER_CALLBACK_TOKEN,
                    "kind": "log",
                    "content": "must not be stored",
                },
            ),
            token_client.post(
                (
                    "/cloud-run-worker/leases/"
                    "lease_credential_isolation_tokenless/complete"
                ),
                json={
                    "worker_id": WORKER_ID,
                    "callback_token": WORKER_CALLBACK_TOKEN,
                    "result": {
                        "status": "patch_ready",
                        "runner_kind": "remote_stub",
                    },
                },
                headers={
                    "Authorization": f"Bearer {WORKSPACE_API_TOKEN}",
                },
            ),
        )
        audits = [
            anonymous_client.get(
                "/auth/test/audit-events",
                params={
                    "correlation_id": response.headers[
                        "x-correlation-id"
                    ],
                },
            )
            for response in responses
        ]

    for response, audit in zip(responses, audits, strict=True):
        assert response.status_code == 403
        assert response.json() == {
            "detail": "Worker callback token is not valid",
        }
        assert audit.json() == [
            {
                "event_type": "authentication_failure",
                "outcome": "failure",
                "reason_code": "worker_callback_token_invalid",
                "correlation_id": response.headers["x-correlation-id"],
                "user_id": None,
                "external_identity_id": None,
                "device_session_id": None,
            }
        ]
    serialized_results = "".join(
        response.text
        for response in (
            *responses,
            *audits,
        )
    )
    assert WORKSPACE_API_TOKEN not in serialized_results
    assert WORKER_CALLBACK_TOKEN not in serialized_results


def test_local_worker_harness_routes_require_explicit_dev_auth_policy(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'worker-harness-policy.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_human_credentials(database_url, provider)
    app = _credential_isolation_app(database_url, provider)

    with (
        TestClient(app, base_url="https://testserver") as session_client,
        TestClient(app, base_url="https://testserver") as token_client,
        TestClient(app, base_url="https://testserver") as anonymous_client,
    ):
        _sign_in(session_client, provider)
        responses = (
            session_client.post("/cloud-run-worker/process-next"),
            token_client.post(
                "/cloud-run-worker/leases/requeue-expired",
                json={"queue_provider": "local_db"},
                headers={
                    "Authorization": f"Bearer {WORKSPACE_API_TOKEN}",
                },
            ),
            anonymous_client.post("/cloud-run-worker/process-next"),
            anonymous_client.post(
                "/cloud-run-worker/leases/requeue-expired",
                json={"queue_provider": "local_db"},
            ),
        )
        audits = [
            anonymous_client.get(
                "/auth/test/audit-events",
                params={
                    "correlation_id": response.headers[
                        "x-correlation-id"
                    ],
                },
            )
            for response in responses
        ]

    for response, audit in zip(responses, audits, strict=True):
        assert response.status_code == 403
        assert response.json() == {
            "detail": "Worker route is not available",
        }
        assert audit.json() == [
            {
                "event_type": "authentication_failure",
                "outcome": "failure",
                "reason_code": "worker_route_not_available",
                "correlation_id": response.headers["x-correlation-id"],
                "user_id": None,
                "external_identity_id": None,
                "device_session_id": None,
            }
        ]


def test_worker_authentication_precedes_queue_and_lease_discovery(
    tmp_path,
) -> None:
    database_url = (
        f"sqlite:///{(tmp_path / 'worker-auth-order.db').as_posix()}"
    )
    provider = DeterministicFakeCustomerIdentityProvider()
    _seed_human_credentials(database_url, provider)
    app = _credential_isolation_app(database_url, provider)
    untrusted_worker_token = "untrusted-worker-token-secret"

    with (
        TestClient(app, base_url="https://testserver") as session_client,
        TestClient(app, base_url="https://testserver") as token_client,
        TestClient(app, base_url="https://testserver") as anonymous_client,
    ):
        _sign_in(session_client, provider)
        missing_credential_responses = (
            anonymous_client.post(
                "/cloud-run-worker/leases",
                json={
                    "worker_id": WORKER_ID,
                    "worker_kind": "remote_stub",
                },
            ),
            token_client.post(
                "/cloud-run-worker/leases",
                json={
                    "worker_id": WORKER_ID,
                    "worker_kind": "remote_stub",
                    "cloud_run_id": "unknown-cloud-run",
                },
                headers={
                    "Authorization": f"Bearer {WORKSPACE_API_TOKEN}",
                },
            ),
            session_client.post(
                "/cloud-run-worker/leases/unknown-lease/heartbeat",
                json={
                    "worker_id": WORKER_ID,
                    "lease_seconds": 60,
                },
            ),
            anonymous_client.post(
                "/cloud-run-worker/leases/unknown-lease/payload",
                json={"worker_id": WORKER_ID},
            ),
            anonymous_client.post(
                "/cloud-run-worker/leases/unknown-lease/artifacts",
                json={
                    "worker_id": WORKER_ID,
                    "kind": "log",
                    "content": "must not be stored",
                },
            ),
            anonymous_client.post(
                "/cloud-run-worker/leases/unknown-lease/complete",
                json={
                    "worker_id": WORKER_ID,
                    "result": {
                        "status": "patch_ready",
                        "runner_kind": "remote_stub",
                    },
                },
            ),
        )
        missing_scope = anonymous_client.post(
            "/cloud-run-worker/leases",
            json={
                "worker_id": WORKER_ID,
                "worker_kind": "remote_stub",
                "callback_token": untrusted_worker_token,
            },
        )
        invalid_scoped_responses = (
            anonymous_client.post(
                "/cloud-run-worker/leases",
                json={
                    "worker_id": WORKER_ID,
                    "worker_kind": "remote_stub",
                    "cloud_run_id": "unknown-cloud-run",
                    "callback_token": untrusted_worker_token,
                },
            ),
            anonymous_client.post(
                "/cloud-run-worker/leases/unknown-lease/heartbeat",
                json={
                    "worker_id": WORKER_ID,
                    "callback_token": untrusted_worker_token,
                    "lease_seconds": 60,
                },
            ),
        )
        responses = (
            *missing_credential_responses,
            missing_scope,
            *invalid_scoped_responses,
        )
        audits = [
            anonymous_client.get(
                "/auth/test/audit-events",
                params={
                    "correlation_id": response.headers[
                        "x-correlation-id"
                    ],
                },
            )
            for response in responses
        ]

    for response in missing_credential_responses:
        assert response.status_code == 401
        assert response.json() == {
            "detail": "Worker callback token is required",
        }
    assert missing_scope.status_code == 400
    assert missing_scope.json() == {"detail": "Cloud run id is required"}
    for response in invalid_scoped_responses:
        assert response.status_code == 403
        assert response.json() == {
            "detail": "Worker callback token is not valid",
        }
    expected_reason_codes = (
        *("worker_callback_token_required",) * len(
            missing_credential_responses
        ),
        "worker_callback_scope_required",
        *("worker_callback_token_invalid",) * len(
            invalid_scoped_responses
        ),
    )
    for response, audit, reason_code in zip(
        responses,
        audits,
        expected_reason_codes,
        strict=True,
    ):
        assert audit.json() == [
            {
                "event_type": "authentication_failure",
                "outcome": "failure",
                "reason_code": reason_code,
                "correlation_id": response.headers["x-correlation-id"],
                "user_id": None,
                "external_identity_id": None,
                "device_session_id": None,
            }
        ]
    serialized_results = "".join(
        response.text
        for response in (
            *responses,
            *audits,
        )
    )
    assert WORKSPACE_API_TOKEN not in serialized_results
    assert untrusted_worker_token not in serialized_results
