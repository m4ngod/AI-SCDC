# Phase 13C Usage Cost Quota Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add execution-plane usage types, workspace credit guardrails, cloud-run budget reservations, and usage/cost summary APIs without integrating payments.

**Architecture:** Keep `UsageLedgerEntry` append-only as the usage fact table. Add focused wallet, spend-limit, and budget-reservation models plus a small budget service that `cloud_runner.py` calls at enqueue and terminal settlement points. Expose workspace usage summary and per-cloud-run cost summary endpoints for Phase 14 desktop/billing work.

**Tech Stack:** FastAPI, SQLModel, SQLite test databases, Pydantic schemas, pytest.

---

### Task 1: Usage Ledger Execution Dimensions

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/services/usage_ledger.py`
- Modify: `apps/api/app/ai_company_api/db/session.py`
- Test: `apps/api/tests/test_usage_ledger_api.py`

- [ ] **Step 1: Write the failing test**

Add a test that posts a non-model usage row:

```python
def test_usage_ledger_accepts_execution_usage_dimensions() -> None:
    with build_client() as client:
        project, task, _planner_run = create_project_task_and_planner_run(client)
        response = client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "usage_type": "cloud_run_runtime_seconds",
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 12,
                "unit_name": "seconds",
                "amount_cents": 12,
                "raw_usage_json": {"cloud_run_id": "cloud_run_example"},
            },
        )

    assert response.status_code == 201
    usage = response.json()
    assert usage["usage_type"] == "cloud_run_runtime_seconds"
    assert usage["quantity"] == 12
    assert usage["unit_name"] == "seconds"
    assert usage["total_tokens"] == 0
```

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
pytest apps/api/tests/test_usage_ledger_api.py::test_usage_ledger_accepts_execution_usage_dimensions -q
```

Expected: fail because `cloud_run_runtime_seconds`, `quantity`, and `unit_name` are not supported yet.

- [ ] **Step 3: Implement the minimal schema/model support**

Add `UsageType` values:

```python
CLOUD_RUN_RUNTIME_SECONDS = "cloud_run_runtime_seconds"
WORKER_SUBMISSIONS = "worker_submissions"
OBJECT_STORAGE_BYTES = "object_storage_bytes"
OBJECT_STORAGE_READS = "object_storage_reads"
LOG_SYNC_CALLS = "log_sync_calls"
QUEUE_MESSAGES = "queue_messages"
PR_PUBLISH_ATTEMPTS = "pr_publish_attempts"
```

Add nullable execution dimensions to `UsageLedgerEntry` and matching Pydantic fields:

```python
cloud_run_id: str | None = Field(default=None, index=True, foreign_key="cloud_run.id")
quantity: int = Field(default=0, index=True)
unit_name: str = ""
```

Add these fields to `_usage_read()` and `append_usage_ledger_entry()`.

- [ ] **Step 4: Add SQLite upgrade support**

In `init_db()`, add a non-destructive upgrade helper for existing SQLite tables:

```python
_upgrade_sqlite_usage_ledger_phase_13c_columns(engine)
```

The helper adds `cloud_run_id`, `quantity`, and `unit_name` to existing `usage_ledger_entry`.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
pytest apps/api/tests/test_usage_ledger_api.py::test_usage_ledger_accepts_execution_usage_dimensions -q
```

Expected: pass.

---

### Task 2: Wallet, Spend Limit, and Manual Credit APIs

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Create: `apps/api/app/ai_company_api/services/budgeting.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Test: `apps/api/tests/test_usage_cost_quota_api.py`

- [ ] **Step 1: Write the failing tests**

Create tests for manual grants and spend-limit storage:

```python
def test_manual_credit_grant_creates_workspace_wallet() -> None:
    with build_client() as client:
        response = client.post(
            "/workspace/credits/manual-grants",
            json={"amount_cents": 500, "reason": "test grant"},
        )

    assert response.status_code == 201
    wallet = response.json()
    assert wallet["workspace_id"] == "dev_workspace"
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
```

- [ ] **Step 2: Run the tests to verify RED**

Run:

```bash
pytest apps/api/tests/test_usage_cost_quota_api.py -q -k "manual_credit or spend_limit"
```

Expected: fail because routes and models do not exist.

- [ ] **Step 3: Implement models, schemas, and service functions**

Add `CreditWallet`, `SpendLimit`, and service functions:

```python
def grant_manual_credit(session: Session, data: ManualCreditGrantCreate) -> CreditWalletRead:
    wallet = _get_or_create_wallet(session)
    wallet.balance_cents += data.amount_cents
    wallet.updated_at = utc_now()
    session.add(wallet)
    session.commit()
    session.refresh(wallet)
    return _wallet_read(wallet)
```

```python
def set_workspace_spend_limit(session: Session, data: SpendLimitUpdate) -> SpendLimitRead:
    limit = _get_or_create_spend_limit(session)
    limit.monthly_limit_cents = data.monthly_limit_cents
    limit.per_run_limit_cents = data.per_run_limit_cents
    limit.updated_at = utc_now()
    session.add(limit)
    session.commit()
    session.refresh(limit)
    return _spend_limit_read(limit)
```

- [ ] **Step 4: Add API routes**

Wire:

```python
@router.post("/workspace/credits/manual-grants", status_code=status.HTTP_201_CREATED)
def post_manual_credit_grant(
    data: ManualCreditGrantCreate,
    session: SessionDep,
) -> CreditWalletRead:
    return grant_manual_credit(session, data)

@router.put("/workspace/spend-limit")
def put_workspace_spend_limit(
    data: SpendLimitUpdate,
    session: SessionDep,
) -> SpendLimitRead:
    return set_workspace_spend_limit(session, data)
```

- [ ] **Step 5: Verify GREEN**

Run:

```bash
pytest apps/api/tests/test_usage_cost_quota_api.py -q -k "manual_credit or spend_limit"
```

Expected: pass.

---

### Task 3: Workspace Usage Summary API

**Files:**
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/services/budgeting.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Test: `apps/api/tests/test_usage_cost_quota_api.py`

- [ ] **Step 1: Write the failing test**

```python
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
    assert summary["total_amount_cents"] == 27
    assert {item["usage_type"]: item["amount_cents"] for item in summary["items"]} == {
        "worker_submissions": 25,
        "queue_messages": 2,
    }
```

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
pytest apps/api/tests/test_usage_cost_quota_api.py::test_workspace_usage_summary_aggregates_by_usage_type_and_project -q
```

Expected: fail because the endpoint does not exist.

- [ ] **Step 3: Implement summary query**

Read `UsageLedgerEntry` rows scoped to the active workspace, optional
`project_id` and `task_id`, and return totals grouped by `usage_type`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
pytest apps/api/tests/test_usage_cost_quota_api.py::test_workspace_usage_summary_aggregates_by_usage_type_and_project -q
```

Expected: pass.

---

### Task 4: Cloud-Run Budget Reservation at Enqueue

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/services/budgeting.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/app/ai_company_api/db/session.py`
- Test: `apps/api/tests/test_usage_cost_quota_api.py`

- [ ] **Step 1: Write failing tests**

```python
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
    assert cloud_run["budget_reservation_id"] is not None
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest apps/api/tests/test_usage_cost_quota_api.py -q -k "enqueue_requires_available_credits or enqueue_creates_budget_reservation"
```

Expected: fail because budget reservation is not wired.

- [ ] **Step 3: Implement reservation model and service**

Add `BudgetReservation`, `CloudRun` cost fields, and:

```python
def reserve_cloud_run_budget(session: Session, *, project_id: str, task_id: str) -> BudgetReservation:
    wallet = _active_wallet_or_402(session)
    estimate = DEFAULT_CLOUD_RUN_RESERVATION_CENTS
    _ensure_spend_limit_allows(session, estimate)
    if wallet.balance_cents < estimate:
        raise HTTPException(status_code=402, detail="Insufficient credits")
    wallet.balance_cents -= estimate
    reservation = BudgetReservation(
        workspace_id=current_workspace_id(),
        organization_id=current_organization_id(),
        project_id=project_id,
        task_id=task_id,
        reserved_cents=estimate,
        status=BudgetReservationStatus.RESERVED,
    )
    session.add(wallet)
    session.add(reservation)
    session.flush()
    return reservation
```

- [ ] **Step 4: Wire enqueue**

Call `reserve_cloud_run_budget()` after task/repo/profile validation and before
external queue/runtime submission. Attach the reservation id and estimated cost
to the `CloudRun` row after `session.flush()`.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
pytest apps/api/tests/test_usage_cost_quota_api.py -q -k "enqueue_requires_available_credits or enqueue_creates_budget_reservation"
```

Expected: pass.

---

### Task 5: Reservation Release, Settlement, and Cost Summary

**Files:**
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/services/budgeting.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Test: `apps/api/tests/test_usage_cost_quota_api.py`

- [ ] **Step 1: Write failing tests**

```python
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
    assert cost.json()["reservation"]["status"] == "released"
    assert cost.json()["actual_cost_cents"] == 0


def test_failed_cloud_run_settles_measurable_usage(tmp_path: Path) -> None:
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
    assert cost.json()["reservation"]["status"] in {"settled", "released"}
    assert "usage_entries" in cost.json()
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
pytest apps/api/tests/test_usage_cost_quota_api.py -q -k "releases_reservation or settles_measurable_usage"
```

Expected: fail because settlement and cost summary do not exist.

- [ ] **Step 3: Implement release and settlement**

Add idempotent functions:

```python
def release_cloud_run_reservation(session: Session, cloud_run: CloudRun) -> None:
    reservation = _active_reservation_for_cloud_run(session, cloud_run)
    if reservation is None:
        return
    wallet = _get_or_create_wallet(session)
    wallet.balance_cents += reservation.reserved_cents
    reservation.status = BudgetReservationStatus.RELEASED
    reservation.settled_cents = 0
    reservation.settled_at = utc_now()

def settle_cloud_run_budget(session: Session, cloud_run: CloudRun) -> None:
    reservation = _active_reservation_for_cloud_run(session, cloud_run)
    if reservation is None:
        return
    actual_cents = estimate_cloud_run_actual_cost_cents(cloud_run)
    wallet = _get_or_create_wallet(session)
    wallet.balance_cents += max(reservation.reserved_cents - actual_cents, 0)
    reservation.status = BudgetReservationStatus.SETTLED
    reservation.settled_cents = actual_cents
    reservation.settled_at = utc_now()
```

Settlement appends usage rows for `worker_submissions`, `queue_messages`, and
`cloud_run_runtime_seconds` when known.

- [ ] **Step 4: Wire terminal paths**

Call release for queued cancellation and pre-submission enqueue failure. Call
settle before terminal commits in claimed completion, claimed failure, claimed
cancelled, and runtime submission failure paths.

- [ ] **Step 5: Implement cost-summary endpoint**

Wire:

```python
@router.get("/cloud-runs/{cloud_run_id}/cost-summary")
def get_cloud_run_cost_summary(
    cloud_run_id: str,
    session: SessionDep,
) -> CloudRunCostSummaryRead:
    return cloud_run_cost_summary(session, cloud_run_id)
```

- [ ] **Step 6: Verify GREEN**

Run:

```bash
pytest apps/api/tests/test_usage_cost_quota_api.py -q
```

Expected: pass.

---

### Task 6: Documentation and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `STATUS.md`
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`

- [ ] **Step 1: Update docs**

Document Phase 13C as started with execution usage types, wallet/spend-limit
guardrails, budget reservations, and usage/cost summary APIs. State that Stripe,
real provider price tables, and desktop billing UI remain out of scope.

- [ ] **Step 2: Run focused tests**

Run:

```bash
pytest apps/api/tests/test_usage_ledger_api.py apps/api/tests/test_usage_cost_quota_api.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run affected cloud-run tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "enqueue or cancel or process or completion"
```

Expected: all selected tests pass.

- [ ] **Step 4: Run full verification**

Run:

```bash
pytest apps/api/tests -q
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_usage_cost_quota_api.py apps/api/tests/test_usage_ledger_api.py
pnpm typecheck
git diff --check
```

Expected: tests pass; compile/typecheck pass; `git diff --check` has no whitespace errors.
