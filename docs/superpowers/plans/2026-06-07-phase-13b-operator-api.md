# Phase 13B Operator API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing Aliyun MNS receipt recovery and Aliyun ECI runtime cleanup helpers through narrow owner/admin operator APIs.

**Architecture:** Keep provider maintenance behavior in `cloud_runner.py` unchanged and add a small FastAPI route facade. The facade enforces owner/admin RBAC before invoking helpers, relies on existing workspace checks for 404 resource hiding, and returns a slim operator snapshot that omits queue receipts, callback tokens, and full runtime job ids.

**Tech Stack:** FastAPI, SQLModel, Pydantic, pytest, existing Aliyun fake clients in `apps/api/tests/test_cloud_run_api.py`.

---

### Task 1: HTTP-Level Operator API Tests

**Files:**
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Add dev-auth header helper**

Add this helper near `build_client()`:

```python
def auth_headers(
    *,
    user_id: str = "operator_user",
    workspace_id: str = "dev_workspace",
    organization_id: str = "dev_organization",
    roles: str = "owner",
) -> dict[str, str]:
    return {
        "x-ai-scdc-user-id": user_id,
        "x-ai-scdc-workspace-id": workspace_id,
        "x-ai-scdc-organization-id": organization_id,
        "x-ai-scdc-roles": roles,
    }
```

- [ ] **Step 2: Add failing RBAC test**

Add this test near the existing Aliyun operator helper tests:

```python
def test_cloud_run_operator_endpoints_reject_non_operator_roles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "app.db"
    cloud_run_id, _lease_id, _fake_mns = _start_claimed_aliyun_mns_run(
        tmp_path,
        monkeypatch,
    )
    client = build_client(database_path)
    endpoints = [
        f"/cloud-runs/{cloud_run_id}/operator/retry-mns-receipt-delete",
        f"/cloud-runs/{cloud_run_id}/operator/cleanup-aliyun-eci-runtime",
    ]

    for role in ("developer", "reviewer", "billing_manager", "viewer"):
        for endpoint in endpoints:
            response = client.post(endpoint, headers=auth_headers(roles=role))

            assert response.status_code == 403
            assert response.json()["detail"] == "Insufficient workspace role"
```

- [ ] **Step 3: Add failing MNS success/redaction test**

Add:

```python
def test_cloud_run_operator_retries_mns_receipt_delete_without_leaking_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "app.db"
    cloud_run_id, lease_id, fake_mns = _start_claimed_aliyun_mns_run(
        tmp_path,
        monkeypatch,
    )
    fake_mns.delete_error = RuntimeError("delete failed for receipt-1")
    queued_payload = json.loads(fake_mns.requests[0].body)
    client = build_client(database_path)
    payload = remote_stub_completion_payload(cloud_run_id)
    payload["worker_id"] = queued_payload["worker_id"]
    payload["callback_token"] = queued_payload["callback_token"]
    completion = client.post(
        f"/cloud-run-worker/leases/{lease_id}/complete",
        json=payload,
    )
    assert completion.status_code == 200

    fake_mns.delete_error = None
    response = client.post(
        f"/cloud-runs/{cloud_run_id}/operator/retry-mns-receipt-delete",
        headers=auth_headers(roles="owner"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["reason"] == "mns_message_deleted"
    assert body["cloud_run"]["id"] == cloud_run_id
    assert body["cloud_run"]["external_status"] == "mns_message_deleted"
    assert fake_mns.delete_requests[-1].receipt_handle == "receipt-1"
    assert "queue_receipt" not in response.text
    assert "receipt-1" not in response.text
    assert queued_payload["callback_token"] not in response.text
```

- [ ] **Step 4: Add failing ECI success/redaction test**

Add:

```python
def test_cloud_run_operator_cleans_aliyun_eci_runtime_without_leaking_runtime_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_eci = CleanupRecordingAliyunEciClient()
    database_path = tmp_path / "app.db"
    cloud_run_id, runtime_job_id = _start_completed_aliyun_eci_run(
        tmp_path,
        monkeypatch,
        fake_eci,
    )
    client = build_client(database_path)

    response = client.post(
        f"/cloud-runs/{cloud_run_id}/operator/cleanup-aliyun-eci-runtime",
        headers=auth_headers(roles="admin"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["reason"] == "runtime_cleanup_deleted"
    assert body["cloud_run"]["id"] == cloud_run_id
    assert body["cloud_run"]["external_status"] == "runtime_cleanup_deleted"
    assert fake_eci.deleted_container_group_ids == [runtime_job_id]
    assert "runtime_job_id" not in response.text
    assert runtime_job_id not in response.text
```

- [ ] **Step 5: Add failing cross-workspace 404 test**

Add:

```python
def test_cloud_run_operator_endpoints_hide_cross_workspace_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "app.db"
    cloud_run_id, _lease_id, _fake_mns = _start_claimed_aliyun_mns_run(
        tmp_path,
        monkeypatch,
    )
    client = build_client(database_path)

    response = client.post(
        f"/cloud-runs/{cloud_run_id}/operator/retry-mns-receipt-delete",
        headers=auth_headers(
            user_id="other_operator",
            workspace_id="other_workspace",
            organization_id="other_organization",
            roles="owner",
        ),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Cloud run not found"
```

- [ ] **Step 6: Run tests to verify RED**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "cloud_run_operator"
```

Expected: the selected tests fail with 404 because the operator routes do not exist yet.

---

### Task 2: Operator Response Schemas

**Files:**
- Modify: `apps/api/app/ai_company_api/schemas/api.py`

- [ ] **Step 1: Add narrow response schemas**

Add these schemas after `CloudRunRead`:

```python
class CloudRunOperatorSnapshotRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    status: str
    queue_provider: str
    runtime_provider: str | None
    external_status: str | None
    external_error: str | None
    created_at: datetime
    updated_at: datetime


class CloudRunProviderOperationRead(BaseModel):
    status: Literal["skipped", "succeeded", "failed"]
    reason: str
    cloud_run: CloudRunOperatorSnapshotRead
```

- [ ] **Step 2: Run schema import check**

Run:

```bash
python -m compileall -q apps/api/app/ai_company_api/schemas/api.py
```

Expected: pass.

---

### Task 3: Owner/Admin Operator Routes

**Files:**
- Modify: `apps/api/app/ai_company_api/api/routes.py`

- [ ] **Step 1: Import schemas and service helpers**

Add `CloudRunOperatorSnapshotRead` and `CloudRunProviderOperationRead` to the
schema imports. Add `cleanup_aliyun_eci_terminal_runtime_job` and
`retry_retained_mns_queue_receipt_delete` to the `cloud_runner` service imports.

- [ ] **Step 2: Add operator role set and response mapper**

Add this near `BILLING_WORKSPACE_ROLES`:

```python
OPERATOR_WORKSPACE_ROLES = {
    WorkspaceRole.OWNER,
    WorkspaceRole.ADMIN,
}
```

Add this helper below the constants:

```python
def _cloud_run_provider_operation_read(result) -> CloudRunProviderOperationRead:
    cloud_run = result.cloud_run
    return CloudRunProviderOperationRead(
        status=result.status,
        reason=result.reason,
        cloud_run=CloudRunOperatorSnapshotRead(
            id=cloud_run.id,
            workspace_id=cloud_run.workspace_id,
            project_id=cloud_run.project_id,
            task_id=cloud_run.task_id,
            status=cloud_run.status,
            queue_provider=cloud_run.queue_provider,
            runtime_provider=cloud_run.runtime_provider,
            external_status=cloud_run.external_status,
            external_error=cloud_run.external_error,
            created_at=cloud_run.created_at,
            updated_at=cloud_run.updated_at,
        ),
    )
```

- [ ] **Step 3: Add two route functions**

Add the routes near the existing cloud-run read/cost routes:

```python
@router.post(
    "/cloud-runs/{cloud_run_id}/operator/retry-mns-receipt-delete",
    response_model=CloudRunProviderOperationRead,
)
def post_cloud_run_operator_retry_mns_receipt_delete(
    cloud_run_id: str,
    session: SessionDep,
) -> CloudRunProviderOperationRead:
    require_workspace_role(OPERATOR_WORKSPACE_ROLES)
    result = retry_retained_mns_queue_receipt_delete(
        session,
        cloud_run_id=cloud_run_id,
    )
    return _cloud_run_provider_operation_read(result)


@router.post(
    "/cloud-runs/{cloud_run_id}/operator/cleanup-aliyun-eci-runtime",
    response_model=CloudRunProviderOperationRead,
)
def post_cloud_run_operator_cleanup_aliyun_eci_runtime(
    cloud_run_id: str,
    session: SessionDep,
) -> CloudRunProviderOperationRead:
    require_workspace_role(OPERATOR_WORKSPACE_ROLES)
    result = cleanup_aliyun_eci_terminal_runtime_job(
        session,
        cloud_run_id=cloud_run_id,
    )
    return _cloud_run_provider_operation_read(result)
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "cloud_run_operator"
```

Expected: all selected operator API tests pass.

---

### Task 4: Documentation and Verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`
- Modify: `STATUS.md`
- Modify: `docs/superpowers/specs/2026-06-07-phase-13b-operator-api-design.md`

- [ ] **Step 1: Update phase docs**

Record that Phase 13B includes a narrow authenticated operator API facade for
MNS receipt recovery and ECI runtime cleanup. Keep the remaining-work language
for full sessions, real KMS, full operator console, public destructive OSS
cleanup, and complete permission matrix.

- [ ] **Step 2: Verify focused tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "cloud_run_operator or retained_receipt_recovery or terminal_cleanup"
```

Expected: selected HTTP route and existing helper tests pass.

- [ ] **Step 3: Verify affected auth tests**

Run:

```bash
pytest apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_cloud_run_api.py -q -k "operator or money_moving_workspace_endpoints_require_billing_role"
```

Expected: selected RBAC tests pass.

- [ ] **Step 4: Run final verification**

Run:

```bash
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_cloud_run_api.py
pytest apps/api/tests/test_cloud_run_api.py apps/api/tests/test_auth_rbac_api.py -q -k "cloud_run_operator or retained_receipt_recovery or terminal_cleanup or money_moving_workspace_endpoints_require_billing_role"
git diff --check
```

Expected: compile passes, focused tests pass, and `git diff --check` reports no whitespace errors apart from existing Git LF-to-CRLF working-copy warnings.
