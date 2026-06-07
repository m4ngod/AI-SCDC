from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    BudgetReservation,
    BudgetReservationStatus,
    CloudRun,
    CloudRunStoredObject,
    CreditWallet,
    Project,
    Repository,
    Task,
    UsageLedgerEntry,
    UsageType,
    utc_now,
)
from ai_company_api.services.budgeting import (
    cloud_run_cost_summary,
    release_cloud_run_reservation,
    settle_cloud_run_budget,
)
from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    SandboxExecutionResult,
)


def build_client(database_path: Path | None = None) -> TestClient:
    database_url = "sqlite://" if database_path is None else f"sqlite:///{database_path.as_posix()}"
    return TestClient(create_app(database_url=database_url))


def auth_headers(
    *,
    user_id: str = "user_a",
    workspace_id: str = "workspace_a",
    organization_id: str = "org_a",
) -> dict[str, str]:
    return {
        "x-ai-scdc-user-id": user_id,
        "x-ai-scdc-workspace-id": workspace_id,
        "x-ai-scdc-organization-id": organization_id,
    }


def create_github_task_and_repo(
    client: TestClient,
    headers: dict[str, str] | None = None,
) -> tuple[dict, dict]:
    project = client.post(
        "/projects",
        json={"name": "Budget Project"},
        headers=headers,
    ).json()
    task = client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Budgeted task", "role_required": "backend"},
        headers=headers,
    ).json()
    credential = client.post(
        "/github-credentials",
        json={"display_name": "Budget GitHub", "token": "ghp_budget_token_1234"},
        headers=headers,
    ).json()
    repository = client.post(
        f"/projects/{project['id']}/github-repositories",
        json={
            "name": "Budget repo",
            "repo_url": "https://github.com/example/budget",
            "github_owner": "example",
            "github_repo": "budget",
            "default_branch": "main",
            "github_credential_id": credential["id"],
        },
        headers=headers,
    ).json()
    return task, repository


def create_budgeted_cloud_run_for_settlement(
    session: Session,
    *,
    workspace_id: str = "workspace_a",
    organization_id: str = "org_a",
    reserved_cents: int = 100,
    claimed_at=None,
    completed_at=None,
    queue_message_id: str | None = None,
) -> tuple[BudgetReservation, CloudRun]:
    project = Project(workspace_id=workspace_id, name="Workspace A")
    session.add(project)
    session.flush()
    task = Task(
        project_id=project.id,
        title="Workspace task",
        role_required="backend",
    )
    repository = Repository(
        workspace_id=workspace_id,
        project_id=project.id,
        name="Workspace repo",
        local_path="",
        provider="github",
    )
    wallet = CreditWallet(
        workspace_id=workspace_id,
        organization_id=organization_id,
        balance_cents=400,
    )
    session.add(task)
    session.add(repository)
    session.add(wallet)
    session.flush()
    reservation = BudgetReservation(
        workspace_id=workspace_id,
        organization_id=organization_id,
        project_id=project.id,
        task_id=task.id,
        reserved_cents=reserved_cents,
        status=BudgetReservationStatus.RESERVED,
    )
    session.add(reservation)
    session.flush()
    cloud_run = CloudRun(
        workspace_id=workspace_id,
        project_id=project.id,
        task_id=task.id,
        repo_id=repository.id,
        base_branch="main",
        head_branch="budget-test",
        budget_reservation_id=reservation.id,
        estimated_cost_cents=reserved_cents,
        queue_provider="external_stub",
        queue_message_id=queue_message_id,
        claimed_at=claimed_at,
        completed_at=completed_at,
    )
    session.add(cloud_run)
    session.flush()
    reservation.cloud_run_id = cloud_run.id
    return reservation, cloud_run


def test_manual_credit_grant_creates_workspace_wallet() -> None:
    with build_client() as client:
        response = client.post(
            "/workspace/credits/manual-grants",
            json={"amount_cents": 500, "reason": "test grant"},
        )

    assert response.status_code == 201
    wallet = response.json()
    assert wallet["workspace_id"] == "dev_workspace"
    assert wallet["organization_id"] == "dev_organization"
    assert wallet["balance_cents"] == 500


def test_set_workspace_spend_limit_returns_limit() -> None:
    with build_client() as client:
        response = client.put(
            "/workspace/spend-limit",
            json={"monthly_limit_cents": 1000, "per_run_limit_cents": 200},
        )

    assert response.status_code == 200
    limit = response.json()
    assert limit["workspace_id"] == "dev_workspace"
    assert limit["monthly_limit_cents"] == 1000
    assert limit["per_run_limit_cents"] == 200


def test_workspace_usage_summary_aggregates_by_usage_type_and_project() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Cost Project"}).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Run task", "role_required": "backend"},
        ).json()
        client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "usage_type": "worker_submissions",
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 1,
                "unit_name": "submission",
                "amount_cents": 25,
            },
        )
        client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "usage_type": "queue_messages",
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 2,
                "unit_name": "message",
                "amount_cents": 2,
            },
        )

        response = client.get(
            "/workspace/usage-summary",
            params={"project_id": project["id"]},
        )

    assert response.status_code == 200
    summary = response.json()
    assert summary["workspace_id"] == "dev_workspace"
    assert summary["project_id"] == project["id"]
    assert summary["task_id"] is None
    assert summary["total_amount_cents"] == 27
    assert {item["usage_type"]: item["amount_cents"] for item in summary["items"]} == {
        "worker_submissions": 25,
        "queue_messages": 2,
    }
    assert {item["usage_type"]: item["quantity"] for item in summary["items"]} == {
        "worker_submissions": 1,
        "queue_messages": 2,
    }


def test_workspace_usage_summary_filters_by_task_id() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Task Cost Project"}).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Run task", "role_required": "backend"},
        ).json()
        other_task = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Other run task", "role_required": "backend"},
        ).json()
        client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "usage_type": "worker_submissions",
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 1,
                "unit_name": "submission",
                "amount_cents": 25,
            },
        )
        client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": other_task["id"],
                "usage_type": "worker_submissions",
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 1,
                "unit_name": "submission",
                "amount_cents": 25,
            },
        )

        response = client.get(
            "/workspace/usage-summary",
            params={"task_id": task["id"]},
        )

    assert response.status_code == 200
    summary = response.json()
    assert summary["task_id"] == task["id"]
    assert summary["total_amount_cents"] == 25
    assert [(item["usage_type"], item["quantity"]) for item in summary["items"]] == [
        ("worker_submissions", 1),
    ]


def test_workspace_usage_summary_hides_cross_workspace_task() -> None:
    headers_a = auth_headers(workspace_id="workspace_a", organization_id="org_a")
    headers_b = auth_headers(
        user_id="user_b",
        workspace_id="workspace_b",
        organization_id="org_b",
    )
    with build_client() as client:
        project = client.post(
            "/projects",
            json={"name": "Workspace A cost project"},
            headers=headers_a,
        ).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Run task", "role_required": "backend"},
            headers=headers_a,
        ).json()

        response = client.get(
            "/workspace/usage-summary",
            params={"task_id": task["id"]},
            headers=headers_b,
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Task not found"


def test_cloud_run_enqueue_requires_available_credits(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_client(database_path) as client:
        task, repo = create_github_task_and_repo(client)

        response = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": repo["id"]},
        )

    assert response.status_code == 402
    assert response.json()["detail"] == "Insufficient credits"


def test_cloud_run_enqueue_denied_by_per_run_spend_limit(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_client(database_path) as client:
        client.post(
            "/workspace/credits/manual-grants",
            json={"amount_cents": 500, "reason": "test grant"},
        )
        client.put(
            "/workspace/spend-limit",
            json={"monthly_limit_cents": 0, "per_run_limit_cents": 99},
        )
        task, repo = create_github_task_and_repo(client)

        response = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": repo["id"]},
        )

    assert response.status_code == 402
    assert response.json()["detail"] == "Spend limit exceeded"


def test_cloud_run_enqueue_denied_by_monthly_spend_limit(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_client(database_path) as client:
        client.post(
            "/workspace/credits/manual-grants",
            json={"amount_cents": 500, "reason": "test grant"},
        )
        client.put(
            "/workspace/spend-limit",
            json={"monthly_limit_cents": 150, "per_run_limit_cents": 0},
        )
        task, repo = create_github_task_and_repo(client)

        first = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": repo["id"]},
        )
        second = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": repo["id"]},
        )

    assert first.status_code == 201
    assert second.status_code == 402
    assert second.json()["detail"] == "Spend limit exceeded"


def test_cloud_run_enqueue_creates_budget_reservation(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_client(database_path) as client:
        client.post(
            "/workspace/credits/manual-grants",
            json={"amount_cents": 500, "reason": "test grant"},
        )
        task, repo = create_github_task_and_repo(client)

        response = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": repo["id"]},
        )

    assert response.status_code == 201
    cloud_run = response.json()["cloud_run"]
    assert cloud_run["estimated_cost_cents"] > 0
    assert cloud_run["actual_cost_cents"] == 0
    assert cloud_run["budget_reservation_id"] is not None


def test_cloud_run_cost_summary_hides_cross_workspace_run(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    headers_a = auth_headers(workspace_id="workspace_a", organization_id="org_a")
    headers_b = auth_headers(
        user_id="user_b",
        workspace_id="workspace_b",
        organization_id="org_b",
    )
    with build_client(database_path) as client:
        client.post(
            "/workspace/credits/manual-grants",
            json={"amount_cents": 500, "reason": "test grant"},
            headers=headers_a,
        )
        task, repo = create_github_task_and_repo(client, headers=headers_a)
        cloud_run = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": repo["id"]},
            headers=headers_a,
        ).json()["cloud_run"]

        response = client.get(
            f"/cloud-runs/{cloud_run['id']}/cost-summary",
            headers=headers_b,
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Cloud run not found"


def test_cloud_run_cost_summary_filters_workspace_usage_and_omits_raw_usage_json(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    with build_client(database_path) as client:
        client.post(
            "/workspace/credits/manual-grants",
            json={"amount_cents": 500, "reason": "test grant"},
        )
        task, repo = create_github_task_and_repo(client)
        cloud_run = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": repo["id"]},
        ).json()["cloud_run"]

    with Session(build_engine(database_url)) as session:
        session.add(
            UsageLedgerEntry(
                id="usage_visible",
                workspace_id="dev_workspace",
                organization_id="dev_organization",
                user_id="system",
                project_id=cloud_run["project_id"],
                task_id=task["id"],
                cloud_run_id=cloud_run["id"],
                usage_type=UsageType.WORKER_SUBMISSIONS,
                provider_name="local_db",
                model_name="execution",
                quantity=1,
                unit_name="submission",
                amount_cents=25,
                raw_usage_json={"secret_provider_payload": "visible only in ledger"},
            )
        )
        session.add(
            UsageLedgerEntry(
                id="usage_cross_workspace",
                workspace_id="workspace_b",
                organization_id="org_b",
                user_id="system",
                project_id=cloud_run["project_id"],
                task_id=task["id"],
                cloud_run_id=cloud_run["id"],
                usage_type=UsageType.QUEUE_MESSAGES,
                provider_name="local_db",
                model_name="execution",
                quantity=1,
                unit_name="message",
                amount_cents=1,
                raw_usage_json={"secret_provider_payload": "wrong workspace"},
            )
        )
        session.commit()

    with build_client(database_path) as client:
        response = client.get(f"/cloud-runs/{cloud_run['id']}/cost-summary")

    assert response.status_code == 200
    entries = response.json()["usage_entries"]
    assert [entry["id"] for entry in entries] == ["usage_visible"]
    assert "raw_usage_json" not in entries[0]


def test_queued_cloud_run_cancel_releases_reservation(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_client(database_path) as client:
        client.post(
            "/workspace/credits/manual-grants",
            json={"amount_cents": 500, "reason": "test grant"},
        )
        task, repo = create_github_task_and_repo(client)
        cloud_run = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": repo["id"]},
        ).json()["cloud_run"]

        cancel = client.post(f"/cloud-runs/{cloud_run['id']}/cancel")
        cost = client.get(f"/cloud-runs/{cloud_run['id']}/cost-summary")

    assert cancel.status_code == 200
    assert cost.status_code == 200
    summary = cost.json()
    assert summary["reservation"]["status"] == "released"
    assert summary["actual_cost_cents"] == 0
    assert summary["usage_entries"] == []


def test_processed_cloud_run_settles_measurable_usage(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_client(database_path) as client:
        client.post(
            "/workspace/credits/manual-grants",
            json={"amount_cents": 500, "reason": "test grant"},
        )
        task, repo = create_github_task_and_repo(client)
        cloud_run = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": repo["id"]},
        ).json()["cloud_run"]

        processed = client.post(f"/cloud-runs/{cloud_run['id']}/process")
        cost = client.get(f"/cloud-runs/{cloud_run['id']}/cost-summary")

    assert processed.status_code == 200
    assert cost.status_code == 200
    summary = cost.json()
    assert summary["reservation"]["status"] == "settled"
    assert summary["actual_cost_cents"] > 0
    usage_types = {entry["usage_type"] for entry in summary["usage_entries"]}
    assert "worker_submissions" in usage_types
    assert "cloud_run_runtime_seconds" in usage_types


def test_failed_processed_cloud_run_settles_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FailingExecutor:
        sandbox_kind = "fake"

        def run(self, _request):
            return SandboxExecutionResult(
                status="failed",
                runner_kind="fake",
                base_sha=None,
                head_sha=None,
                worktree_ref=None,
                summary="",
                files_changed=[],
                tests_run=[],
                test_result="not_run",
                risks=[],
                diff_text="",
                command_results=[
                    CommandResult(
                        command="fake cloud run",
                        exit_code=1,
                        stdout="",
                        stderr="failed",
                        duration_ms=1,
                    )
                ],
                test_command_results=[],
                failure_reason="executor_failed",
            )

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FailingExecutor(),
    )
    database_path = tmp_path / "app.db"
    with build_client(database_path) as client:
        client.post(
            "/workspace/credits/manual-grants",
            json={"amount_cents": 500, "reason": "test grant"},
        )
        task, repo = create_github_task_and_repo(client)
        cloud_run = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": repo["id"]},
        ).json()["cloud_run"]

        processed = client.post(f"/cloud-runs/{cloud_run['id']}/process")
        cost = client.get(f"/cloud-runs/{cloud_run['id']}/cost-summary")

    assert processed.status_code == 200
    assert processed.json()["cloud_run"]["status"] == "failed"
    assert cost.status_code == 200
    summary = cost.json()
    assert summary["reservation"]["status"] == "settled"
    assert summary["actual_cost_cents"] > 0
    assert {entry["usage_type"] for entry in summary["usage_entries"]} >= {
        "worker_submissions",
        "cloud_run_runtime_seconds",
    }


def test_cloud_run_settlement_records_known_execution_usage_once() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)
    with Session(engine) as session:
        project = Project(workspace_id="workspace_a", name="Workspace A")
        session.add(project)
        session.flush()
        task = Task(
            project_id=project.id,
            title="Workspace task",
            role_required="backend",
        )
        repository = Repository(
            workspace_id="workspace_a",
            project_id=project.id,
            name="Workspace repo",
            local_path="",
            provider="github",
        )
        wallet = CreditWallet(
            workspace_id="workspace_a",
            organization_id="org_a",
            balance_cents=400,
        )
        session.add(task)
        session.add(repository)
        session.add(wallet)
        session.flush()
        reservation = BudgetReservation(
            workspace_id="workspace_a",
            organization_id="org_a",
            project_id=project.id,
            task_id=task.id,
            reserved_cents=100,
            status=BudgetReservationStatus.RESERVED,
        )
        session.add(reservation)
        session.flush()
        cloud_run = CloudRun(
            workspace_id="workspace_a",
            project_id=project.id,
            task_id=task.id,
            repo_id=repository.id,
            base_branch="main",
            head_branch="budget-test",
            budget_reservation_id=reservation.id,
            estimated_cost_cents=100,
            queue_provider="external_stub",
            queue_message_id="queue-message-1",
            runtime_provider="remote_stub",
            runtime_job_id="runtime-job-1",
            storage_provider="local_inline",
            artifact_manifest_uri="local-inline://cloud-run-objects/manifest",
            artifact_manifest_size_bytes=41,
            log_stream_uri="local-inline://cloud-run-objects/log",
            log_stream_size_bytes=59,
            claimed_at=utc_now(),
            completed_at=utc_now(),
        )
        session.add(cloud_run)
        session.flush()
        reservation.cloud_run_id = cloud_run.id
        session.add(
            CloudRunStoredObject(
                workspace_id="workspace_a",
                cloud_run_id=cloud_run.id,
                kind="diff",
                uri="local-inline://cloud-run-objects/diff",
                sha256="0" * 64,
                size_bytes=13,
                content_type="text/x-diff",
                text_content="diff",
            )
        )

        settle_cloud_run_budget(session, cloud_run)
        settle_cloud_run_budget(session, cloud_run)
        session.commit()

        entries = session.exec(
            select(UsageLedgerEntry)
            .where(UsageLedgerEntry.cloud_run_id == cloud_run.id)
            .order_by(UsageLedgerEntry.usage_type)
        ).all()

    quantities = {
        entry.usage_type.value if hasattr(entry.usage_type, "value") else str(entry.usage_type): entry.quantity
        for entry in entries
    }
    assert quantities == {
        "cloud_run_runtime_seconds": 1,
        "log_sync_calls": 1,
        "object_storage_bytes": 113,
        "queue_messages": 1,
        "worker_submissions": 1,
    }
    assert len(entries) == len(quantities)


def test_cloud_run_usage_uniqueness_allows_manual_null_cloud_run_entries() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)
    with Session(engine) as session:
        _reservation, cloud_run = create_budgeted_cloud_run_for_settlement(session)
        session.add(
            UsageLedgerEntry(
                id="usage_manual_1",
                workspace_id="workspace_a",
                organization_id="org_a",
                user_id="user_a",
                usage_type=UsageType.MODEL_TOKENS,
                provider_name="deepseek-dev",
                model_name="deepseek-chat",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            )
        )
        session.add(
            UsageLedgerEntry(
                id="usage_manual_2",
                workspace_id="workspace_a",
                organization_id="org_a",
                user_id="user_a",
                usage_type=UsageType.MODEL_TOKENS,
                provider_name="deepseek-dev",
                model_name="deepseek-chat",
                prompt_tokens=20,
                completion_tokens=6,
                total_tokens=26,
            )
        )
        session.commit()

        session.add(
            UsageLedgerEntry(
                id="usage_cloud_run_1",
                workspace_id="workspace_a",
                organization_id="org_a",
                user_id="system",
                project_id=cloud_run.project_id,
                task_id=cloud_run.task_id,
                cloud_run_id=cloud_run.id,
                usage_type=UsageType.WORKER_SUBMISSIONS,
                provider_name="local_db",
                model_name="execution",
                quantity=1,
                unit_name="submission",
                amount_cents=25,
            )
        )
        session.add(
            UsageLedgerEntry(
                id="usage_cloud_run_2",
                workspace_id="workspace_a",
                organization_id="org_a",
                user_id="system",
                project_id=cloud_run.project_id,
                task_id=cloud_run.task_id,
                cloud_run_id=cloud_run.id,
                usage_type=UsageType.WORKER_SUBMISSIONS,
                provider_name="local_db",
                model_name="execution",
                quantity=1,
                unit_name="submission",
                amount_cents=25,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_cloud_run_settlement_caps_billable_actual_cost_at_reserved_cents() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)
    with Session(engine) as session:
        started_at = utc_now()
        reservation, cloud_run = create_budgeted_cloud_run_for_settlement(
            session,
            claimed_at=started_at,
            completed_at=started_at + timedelta(seconds=200),
        )

        settle_cloud_run_budget(session, cloud_run)
        session.flush()

        entries = session.exec(
            select(UsageLedgerEntry).where(UsageLedgerEntry.cloud_run_id == cloud_run.id)
        ).all()
        raw_measured_cents = sum(entry.amount_cents for entry in entries)
        summary = cloud_run_cost_summary(session, cloud_run.id)

    assert raw_measured_cents > reservation.reserved_cents
    assert summary.measured_cost_cents == raw_measured_cents
    assert summary.billable_cost_cents == reservation.reserved_cents
    assert summary.actual_cost_cents == reservation.reserved_cents
    assert summary.reservation is not None
    assert summary.reservation.settled_cents == reservation.reserved_cents
    assert cloud_run.cost_summary_json["measured_cost_cents"] == raw_measured_cents
    assert cloud_run.cost_summary_json["billable_cost_cents"] == reservation.reserved_cents
    assert cloud_run.cost_summary_json["actual_cost_cents"] == reservation.reserved_cents


def test_settle_cloud_run_budget_noops_after_stale_terminal_transition() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)
    with Session(engine) as session:
        reservation, cloud_run = create_budgeted_cloud_run_for_settlement(session)

        settle_cloud_run_budget(session, cloud_run)
        session.flush()
        wallet_after_first = session.exec(
            select(CreditWallet).where(CreditWallet.workspace_id == "workspace_a")
        ).one()
        first_balance = wallet_after_first.balance_cents
        first_entries = session.exec(
            select(UsageLedgerEntry).where(UsageLedgerEntry.cloud_run_id == cloud_run.id)
        ).all()
        assert first_entries

        set_committed_value(reservation, "status", BudgetReservationStatus.RESERVED)
        settle_cloud_run_budget(session, cloud_run)
        session.flush()

        wallet_after_second = session.exec(
            select(CreditWallet).where(CreditWallet.workspace_id == "workspace_a")
        ).one()
        second_entries = session.exec(
            select(UsageLedgerEntry).where(UsageLedgerEntry.cloud_run_id == cloud_run.id)
        ).all()

    assert first_balance == 475
    assert wallet_after_second.balance_cents == first_balance
    assert len(second_entries) == len(first_entries)
    assert reservation.status == BudgetReservationStatus.SETTLED
    assert reservation.settled_cents == 25


def test_release_cloud_run_reservation_noops_after_stale_terminal_transition() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)
    with Session(engine) as session:
        reservation, cloud_run = create_budgeted_cloud_run_for_settlement(session)

        release_cloud_run_reservation(session, cloud_run)
        session.flush()
        wallet_after_first = session.exec(
            select(CreditWallet).where(CreditWallet.workspace_id == "workspace_a")
        ).one()
        first_balance = wallet_after_first.balance_cents

        set_committed_value(reservation, "status", BudgetReservationStatus.RESERVED)
        release_cloud_run_reservation(session, cloud_run)
        session.flush()

        wallet_after_second = session.exec(
            select(CreditWallet).where(CreditWallet.workspace_id == "workspace_a")
        ).one()

    assert first_balance == 500
    assert wallet_after_second.balance_cents == first_balance
    assert reservation.status == BudgetReservationStatus.RELEASED
    assert reservation.settled_cents == 0


def test_reservation_release_uses_cloud_run_workspace_without_auth_context() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)
    with Session(engine) as session:
        project = Project(workspace_id="workspace_a", name="Workspace A")
        session.add(project)
        session.flush()
        task = Task(
            project_id=project.id,
            title="Workspace task",
            role_required="backend",
        )
        repository = Repository(
            workspace_id="workspace_a",
            project_id=project.id,
            name="Workspace repo",
            local_path="",
            provider="github",
        )
        wallet = CreditWallet(
            workspace_id="workspace_a",
            organization_id="org_a",
            balance_cents=400,
        )
        session.add(task)
        session.add(repository)
        session.add(wallet)
        session.flush()
        reservation = BudgetReservation(
            workspace_id="workspace_a",
            organization_id="org_a",
            project_id=project.id,
            task_id=task.id,
            reserved_cents=100,
            status=BudgetReservationStatus.RESERVED,
        )
        session.add(reservation)
        session.flush()
        cloud_run = CloudRun(
            workspace_id="workspace_a",
            project_id=project.id,
            task_id=task.id,
            repo_id=repository.id,
            base_branch="main",
            head_branch="budget-test",
            budget_reservation_id=reservation.id,
            estimated_cost_cents=100,
        )
        session.add(cloud_run)
        session.flush()
        reservation.cloud_run_id = cloud_run.id

        release_cloud_run_reservation(session, cloud_run)
        session.commit()

        workspace_wallet = session.exec(
            select(CreditWallet).where(CreditWallet.workspace_id == "workspace_a")
        ).one()
        dev_wallet = session.exec(
            select(CreditWallet).where(CreditWallet.workspace_id == "dev_workspace")
        ).first()

    assert workspace_wallet.balance_cents == 500
    assert dev_wallet is None
