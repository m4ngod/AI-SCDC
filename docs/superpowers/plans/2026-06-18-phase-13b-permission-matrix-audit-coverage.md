# Phase 13B Permission Matrix and Audit Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a complete workspace role permission matrix and redacted workspace audit coverage for high-value writes plus high-sensitive reads.

**Architecture:** Add one permission-policy module and one general workspace audit module, then wire existing API/service entry points through those helpers. Resource-specific endpoints must preserve current 404 workspace hiding by loading and workspace-checking the resource before role denial; collection-level endpoints can check permissions at the route entry.

**Tech Stack:** FastAPI, SQLModel, SQLite migration helpers, pytest, existing AI-SCDC service-layer patterns.

---

## File Structure

- Create `apps/api/app/ai_company_api/services/workspace_permissions.py`
  - Owns the permission-name to role-set map.
  - Exposes `allowed_roles_for_permission()` and
    `require_workspace_permission()`.
- Create `apps/api/app/ai_company_api/services/workspace_audit.py`
  - Owns redacted `WorkspaceAuditLog` creation.
  - Exposes `record_workspace_audit()` and
    `require_audited_workspace_permission()`.
- Modify `apps/api/app/ai_company_api/models/entities.py`
  - Add `WorkspaceAuditAccessLevel` enum.
  - Add `WorkspaceAuditLog` SQLModel table.
- Modify `apps/api/app/ai_company_api/api/routes.py`
  - Replace route-local raw role sets with policy names where a route can be
    checked before resource lookup.
  - Record read/write audit rows for collection-level protected routes.
- Modify service modules that already perform workspace lookup:
  - `apps/api/app/ai_company_api/services/repository.py`
  - `apps/api/app/ai_company_api/services/sandbox_profiles.py`
  - `apps/api/app/ai_company_api/services/github_repository.py`
  - `apps/api/app/ai_company_api/services/model_settings.py`
  - `apps/api/app/ai_company_api/services/usage_ledger.py`
  - `apps/api/app/ai_company_api/services/budgeting.py`
  - `apps/api/app/ai_company_api/services/cloud_runner.py`
  - `apps/api/app/ai_company_api/services/cloud_run_logs.py`
  - `apps/api/app/ai_company_api/services/artifact_plane.py`
  - `apps/api/app/ai_company_api/services/local_runner.py`
  - `apps/api/app/ai_company_api/services/test_review_debug.py`
  - `apps/api/app/ai_company_api/services/patch_approval.py`
  - `apps/api/app/ai_company_api/services/github_pull_request.py`
- Modify `apps/api/tests/test_auth_rbac_api.py`
  - Add matrix tests for role allow/deny behavior.
- Create `apps/api/tests/test_workspace_audit.py`
  - Add audit model, redaction, read/write, and denied-attempt tests.
- Modify existing focused API tests only when new permissions require a more
  privileged test header.
- Modify `docs/architecture.md`, `docs/superpowers/status.md`, and
  `STATUS.md` after implementation.

Use this permission table exactly unless a test proves the route leaks a
sensitive response under a lower permission:

| Permission | Roles |
| --- | --- |
| `workspace.metadata.read` | owner, admin, developer, reviewer, billing_manager, viewer |
| `project.write` | owner, admin, developer |
| `repository.write` | owner, admin, developer |
| `conversation.write` | owner, admin, developer |
| `planner.write` | owner, admin, developer |
| `planner.review` | owner, admin, reviewer |
| `task.write` | owner, admin, developer |
| `run.write` | owner, admin, developer |
| `execution.evidence.read` | owner, admin, developer, reviewer |
| `conversation.sensitive.read` | owner, admin, developer, reviewer |
| `execution_config.read` | owner, admin, developer, reviewer |
| `artifact.sensitive.read` | owner, admin, developer, reviewer |
| `artifact.cleanup` | owner, admin |
| `log.sensitive.read` | owner, admin, developer, reviewer |
| `review.write` | owner, admin, reviewer |
| `approval.write` | owner, admin, reviewer |
| `pull_request.publish` | owner, admin, developer |
| `credential.metadata.read` | owner, admin |
| `credential.write` | owner, admin |
| `model_config.read` | owner, admin, developer, reviewer |
| `model_config.write` | owner, admin |
| `billing.read` | owner, admin, billing_manager |
| `billing.write` | owner, admin, billing_manager |
| `operator.write` | owner, admin |

For this implementation, do not add viewer-specific redacted response schemas.
Current full-detail run, artifact, log, patch, test, debug, conversation
message, sandbox profile, and model configuration reads are protected as
sensitive reads. A later slice can add separate summary schemas for broader
viewer access.

---

### Task 1: Permission Policy Helper

**Files:**
- Create: `apps/api/app/ai_company_api/services/workspace_permissions.py`
- Modify: `apps/api/tests/test_auth_rbac_api.py`

- [ ] **Step 1: Write failing tests for the permission map**

Append these tests to `apps/api/tests/test_auth_rbac_api.py`:

```python
def test_workspace_permission_policy_declares_phase_13b_permissions() -> None:
    from ai_company_api.services.workspace_permissions import (
        PERMISSION_ROLES,
        allowed_roles_for_permission,
    )

    expected_permissions = {
        "workspace.metadata.read",
        "project.write",
        "repository.write",
        "conversation.write",
        "planner.write",
        "planner.review",
        "task.write",
        "run.write",
        "execution.evidence.read",
        "conversation.sensitive.read",
        "execution_config.read",
        "artifact.sensitive.read",
        "artifact.cleanup",
        "log.sensitive.read",
        "review.write",
        "approval.write",
        "pull_request.publish",
        "credential.metadata.read",
        "credential.write",
        "model_config.read",
        "model_config.write",
        "billing.read",
        "billing.write",
        "operator.write",
    }
    assert set(PERMISSION_ROLES) == expected_permissions
    assert {
        role.value for role in allowed_roles_for_permission("workspace.metadata.read")
    } == {
        "owner",
        "admin",
        "developer",
        "reviewer",
        "billing_manager",
        "viewer",
    }
    assert {
        role.value for role in allowed_roles_for_permission("credential.write")
    } == {"owner", "admin"}
    assert {
        role.value for role in allowed_roles_for_permission("billing.read")
    } == {"owner", "admin", "billing_manager"}
```

- [ ] **Step 2: Run the focused failing test**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py::test_workspace_permission_policy_declares_phase_13b_permissions -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_company_api.services.workspace_permissions'`.

- [ ] **Step 3: Create the permission helper**

Create `apps/api/app/ai_company_api/services/workspace_permissions.py`:

```python
from collections.abc import Mapping

from ai_company_api.models.entities import WorkspaceRole
from ai_company_api.services.auth_context import AuthContext, require_workspace_role


PermissionName = str

ALL_WORKSPACE_ROLES = frozenset(
    {
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.DEVELOPER,
        WorkspaceRole.REVIEWER,
        WorkspaceRole.BILLING_MANAGER,
        WorkspaceRole.VIEWER,
    }
)
OWNER_ADMIN_ROLES = frozenset({WorkspaceRole.OWNER, WorkspaceRole.ADMIN})
DEVELOPER_EXECUTION_ROLES = frozenset(
    {WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.DEVELOPER}
)
REVIEW_EXECUTION_ROLES = frozenset(
    {WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.REVIEWER}
)
EVIDENCE_READ_ROLES = frozenset(
    {
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.DEVELOPER,
        WorkspaceRole.REVIEWER,
    }
)
BILLING_ROLES = frozenset(
    {
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.BILLING_MANAGER,
    }
)

PERMISSION_ROLES: Mapping[PermissionName, frozenset[WorkspaceRole]] = {
    "workspace.metadata.read": ALL_WORKSPACE_ROLES,
    "project.write": DEVELOPER_EXECUTION_ROLES,
    "repository.write": DEVELOPER_EXECUTION_ROLES,
    "conversation.write": DEVELOPER_EXECUTION_ROLES,
    "planner.write": DEVELOPER_EXECUTION_ROLES,
    "planner.review": REVIEW_EXECUTION_ROLES,
    "task.write": DEVELOPER_EXECUTION_ROLES,
    "run.write": DEVELOPER_EXECUTION_ROLES,
    "execution.evidence.read": EVIDENCE_READ_ROLES,
    "conversation.sensitive.read": EVIDENCE_READ_ROLES,
    "execution_config.read": EVIDENCE_READ_ROLES,
    "artifact.sensitive.read": EVIDENCE_READ_ROLES,
    "artifact.cleanup": OWNER_ADMIN_ROLES,
    "log.sensitive.read": EVIDENCE_READ_ROLES,
    "review.write": REVIEW_EXECUTION_ROLES,
    "approval.write": REVIEW_EXECUTION_ROLES,
    "pull_request.publish": DEVELOPER_EXECUTION_ROLES,
    "credential.metadata.read": OWNER_ADMIN_ROLES,
    "credential.write": OWNER_ADMIN_ROLES,
    "model_config.read": EVIDENCE_READ_ROLES,
    "model_config.write": OWNER_ADMIN_ROLES,
    "billing.read": BILLING_ROLES,
    "billing.write": BILLING_ROLES,
    "operator.write": OWNER_ADMIN_ROLES,
}


def allowed_roles_for_permission(permission: PermissionName) -> frozenset[WorkspaceRole]:
    try:
        return PERMISSION_ROLES[permission]
    except KeyError as exc:
        raise ValueError(f"Unknown workspace permission: {permission}") from exc


def require_workspace_permission(
    permission: PermissionName,
    *,
    detail: str = "Insufficient workspace role",
) -> AuthContext:
    return require_workspace_role(
        allowed_roles_for_permission(permission),
        detail=detail,
    )
```

- [ ] **Step 4: Run the focused permission test**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py::test_workspace_permission_policy_declares_phase_13b_permissions -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add apps/api/app/ai_company_api/services/workspace_permissions.py apps/api/tests/test_auth_rbac_api.py
git commit -m "feat: add workspace permission policy"
```

---

### Task 2: Workspace Audit Model and Service

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Create: `apps/api/app/ai_company_api/services/workspace_audit.py`
- Create: `apps/api/tests/test_workspace_audit.py`

- [ ] **Step 1: Write failing audit model and redaction tests**

Create `apps/api/tests/test_workspace_audit.py`:

```python
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.models.entities import WorkspaceAuditLog
from ai_company_api.services.workspace_audit import (
    record_workspace_audit,
    redact_audit_metadata,
)


def test_workspace_audit_table_is_created() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)

    with Session(engine) as session:
        rows = session.exec(select(WorkspaceAuditLog)).all()

    assert rows == []


def test_redact_audit_metadata_removes_sensitive_values() -> None:
    metadata = {
        "safe_id": "cloud_run_1",
        "token": "ghp_secret",
        "queue_receipt": "receipt_secret",
        "download_url": "https://example.test/private",
        "nested": {"stdout": "secret output", "status": "ok"},
        "messages": ["secret log line"],
    }

    assert redact_audit_metadata(metadata) == {
        "safe_id": "cloud_run_1",
        "token": "[redacted]",
        "queue_receipt": "[redacted]",
        "download_url": "[redacted]",
        "nested": {"stdout": "[redacted]", "status": "ok"},
        "messages": "[redacted]",
    }


def test_record_workspace_audit_redacts_and_commits_without_auth_context(tmp_path) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)

    with Session(engine) as session:
        log = record_workspace_audit(
            session,
            operation="unit.audit",
            resource_type="unit",
            resource_id="unit_1",
            access_level="high_value_write",
            success=True,
            status_code=200,
            metadata={"token": "secret", "status": "ok"},
            commit=True,
        )

    with Session(build_engine(database_url)) as session:
        stored = session.get(WorkspaceAuditLog, log.id)

    assert stored is not None
    assert stored.workspace_id == "dev_workspace"
    assert stored.organization_id == "dev_organization"
    assert stored.user_id == "dev_user"
    assert stored.auth_mode == "system"
    assert stored.metadata_json == {"token": "[redacted]", "status": "ok"}
```

- [ ] **Step 2: Run the failing audit tests**

Run:

```powershell
pytest apps/api/tests/test_workspace_audit.py -q
```

Expected: FAIL while importing `WorkspaceAuditLog` or `workspace_audit`.

- [ ] **Step 3: Add audit model**

In `apps/api/app/ai_company_api/models/entities.py`, add this enum after
`BudgetReservationStatus`:

```python
class WorkspaceAuditAccessLevel(str, Enum):
    HIGH_VALUE_WRITE = "high_value_write"
    HIGH_SENSITIVE_READ = "high_sensitive_read"
    SYSTEM_EVENT = "system_event"
```

Add this SQLModel after `SecretAccessAuditLog`:

```python
class WorkspaceAuditLog(SQLModel, table=True):
    __tablename__ = "workspace_audit_log"

    id: str = Field(
        default_factory=lambda: prefixed_id("workspace_audit"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    organization_id: str = Field(default="dev_organization", index=True)
    user_id: str = Field(default="dev_user", index=True)
    auth_mode: str = Field(default="system", index=True)
    operation: str = Field(index=True)
    resource_type: str = Field(index=True)
    resource_id: str | None = Field(default=None, index=True)
    access_level: WorkspaceAuditAccessLevel = Field(
        sa_column=Column(
            SAEnum(
                WorkspaceAuditAccessLevel,
                name="workspace_audit_access_level",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    success: bool = Field(default=True, index=True)
    status_code: int = Field(default=200, index=True)
    error_code: str | None = Field(default=None, index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)
```

- [ ] **Step 4: Add audit service**

Create `apps/api/app/ai_company_api/services/workspace_audit.py`:

```python
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session

from ai_company_api.models.entities import WorkspaceAuditAccessLevel, WorkspaceAuditLog
from ai_company_api.services.auth_context import (
    DEV_ORGANIZATION_ID,
    DEV_USER_ID,
    DEV_WORKSPACE_ID,
    get_current_auth_context,
)
from ai_company_api.services.workspace_permissions import require_workspace_permission


SENSITIVE_AUDIT_KEY_PARTS = (
    "secret",
    "token",
    "receipt",
    "content",
    "message",
    "stdout",
    "stderr",
    "payload",
    "diff",
    "download_url",
    "presigned",
    "encrypted",
    "authorization",
    "clone_token",
    "queue_receipt",
    "callback",
)
MAX_AUDIT_STRING_LENGTH = 256


def redact_audit_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(part in normalized_key for part in SENSITIVE_AUDIT_KEY_PARTS):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = redact_audit_metadata(item)
        return redacted
    if isinstance(value, list):
        return [redact_audit_metadata(item) for item in value]
    if isinstance(value, str) and len(value) > MAX_AUDIT_STRING_LENGTH:
        return f"{value[:MAX_AUDIT_STRING_LENGTH]}..."
    return value


def record_workspace_audit(
    session: Session,
    *,
    operation: str,
    resource_type: str,
    resource_id: str | None = None,
    access_level: WorkspaceAuditAccessLevel | str,
    success: bool,
    status_code: int,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
    commit: bool = False,
) -> WorkspaceAuditLog:
    context = get_current_auth_context()
    if context is None:
        workspace_id = DEV_WORKSPACE_ID
        organization_id = DEV_ORGANIZATION_ID
        user_id = DEV_USER_ID
        auth_mode = "system"
    else:
        workspace_id = context.workspace_id
        organization_id = context.organization_id
        user_id = context.user_id
        auth_mode = context.auth_mode

    log = WorkspaceAuditLog(
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
        auth_mode=auth_mode,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        access_level=WorkspaceAuditAccessLevel(access_level),
        success=success,
        status_code=status_code,
        error_code=error_code,
        metadata_json=redact_audit_metadata(metadata or {}),
    )
    session.add(log)
    if commit:
        session.commit()
        session.refresh(log)
    else:
        session.flush()
    return log


def require_audited_workspace_permission(
    session: Session,
    permission: str,
    *,
    operation: str,
    resource_type: str,
    resource_id: str | None = None,
    access_level: WorkspaceAuditAccessLevel | str,
) -> None:
    try:
        require_workspace_permission(permission)
    except HTTPException as exc:
        if exc.status_code == 403:
            record_workspace_audit(
                session,
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
                access_level=access_level,
                success=False,
                status_code=403,
                error_code="insufficient_workspace_role",
                commit=True,
            )
        raise
```

- [ ] **Step 5: Run audit tests**

Run:

```powershell
pytest apps/api/tests/test_workspace_audit.py -q
```

Expected: PASS after the intentionally broken test body is replaced with the
working test shown above.

- [ ] **Step 6: Commit**

Run:

```powershell
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/services/workspace_audit.py apps/api/tests/test_workspace_audit.py
git commit -m "feat: add workspace audit log"
```

---

### Task 3: Collection-Level RBAC and Audit

**Files:**
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Modify: `apps/api/tests/test_auth_rbac_api.py`
- Modify: `apps/api/tests/test_workspace_audit.py`

- [ ] **Step 1: Write collection-level RBAC tests**

Append to `apps/api/tests/test_auth_rbac_api.py`:

```python
def test_viewer_cannot_read_credentials_or_billing_detail() -> None:
    with build_client() as client:
        for path in (
            "/github-credentials",
            "/model-credentials",
            "/usage-ledger",
            "/workspace/usage-summary",
        ):
            response = client.get(path, headers=auth_headers(roles="viewer"))
            assert response.status_code == 403
            assert response.json()["detail"] == "Insufficient workspace role"


def test_billing_manager_can_read_billing_but_not_credentials_or_models() -> None:
    with build_client() as client:
        assert client.get(
            "/usage-ledger",
            headers=auth_headers(roles="billing_manager"),
        ).status_code == 200
        assert client.get(
            "/workspace/usage-summary",
            headers=auth_headers(roles="billing_manager"),
        ).status_code == 200

        for path in (
            "/github-credentials",
            "/model-credentials",
            "/model-providers",
            "/model-routes",
        ):
            response = client.get(path, headers=auth_headers(roles="billing_manager"))
            assert response.status_code == 403


def test_developer_and_reviewer_can_read_model_config_but_not_manage_it() -> None:
    with build_client() as client:
        for role in ("developer", "reviewer"):
            assert client.get(
                "/model-providers",
                headers=auth_headers(roles=role),
            ).status_code == 200
            create_response = client.post(
                "/model-providers",
                json={"name": f"provider-{role}", "provider_type": "fake"},
                headers=auth_headers(roles=role),
            )
            assert create_response.status_code == 403


def test_owner_and_admin_can_manage_credentials_and_model_config() -> None:
    with build_client() as client:
        for role in ("owner", "admin"):
            provider = client.post(
                "/model-providers",
                json={"name": f"fake-{role}", "provider_type": "fake"},
                headers=auth_headers(roles=role, workspace_id=f"workspace_{role}"),
            )
            assert provider.status_code == 201
            github_credential = client.post(
                "/github-credentials",
                json={"display_name": f"gh-{role}", "token": f"ghp_{role}_1234"},
                headers=auth_headers(roles=role, workspace_id=f"workspace_{role}"),
            )
            assert github_credential.status_code == 201
```

- [ ] **Step 2: Run collection-level RBAC tests and verify failure**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py -q -k "viewer_cannot_read_credentials_or_billing_detail or billing_manager_can_read_billing_but_not_credentials_or_models or developer_and_reviewer_can_read_model_config_but_not_manage_it or owner_and_admin_can_manage_credentials_and_model_config"
```

Expected: FAIL because several routes still allow roles that should be denied.

- [ ] **Step 3: Wire route-level permission checks and audit**

In `apps/api/app/ai_company_api/api/routes.py`, replace the auth import with:

```python
from ai_company_api.services.auth_context import current_user_id
from ai_company_api.services.workspace_audit import (
    record_workspace_audit,
    require_audited_workspace_permission,
)
```

Remove `BILLING_WORKSPACE_ROLES` and `OPERATOR_WORKSPACE_ROLES`.

For each route below, add `require_audited_workspace_permission(...)` before
calling the service function and call `record_workspace_audit(..., commit=True)`
after successful service completion:

| Route function | Permission | Operation | Resource type | Access level |
| --- | --- | --- | --- | --- |
| `get_github_credentials` | `credential.metadata.read` | `github_credential.list` | `github_credential` | `high_sensitive_read` |
| `post_github_credential` | `credential.write` | `github_credential.create` | `github_credential` | `high_value_write` |
| `get_model_providers` | `model_config.read` | `model_provider.list` | `model_provider` | `high_sensitive_read` |
| `post_model_provider` | `model_config.write` | `model_provider.create` | `model_provider` | `high_value_write` |
| `get_model_credentials` | `credential.metadata.read` | `model_credential.list` | `model_credential` | `high_sensitive_read` |
| `post_model_credential` | `credential.write` | `model_credential.create` | `model_credential` | `high_value_write` |
| `get_model_routes` | `model_config.read` | `model_route.list` | `model_route` | `high_sensitive_read` |
| `post_model_route` | `model_config.write` | `model_route.create` | `model_route` | `high_value_write` |
| `patch_model_route` | `model_config.write` | `model_route.update` | `model_route` | `high_value_write` |
| `resolve_model_route_for_role` | `model_config.read` | `model_route.resolve` | `model_route` | `high_sensitive_read` |
| `get_usage_ledger` | `billing.read` | `usage_ledger.list` | `usage_ledger` | `high_sensitive_read` |
| `post_usage_ledger_entry` | `billing.write` | `usage_ledger.append` | `usage_ledger` | `high_value_write` |
| `post_manual_credit_grant` | `billing.write` | `billing.credit_grant.create` | `credit_wallet` | `high_value_write` |
| `put_workspace_spend_limit` | `billing.write` | `billing.spend_limit.update` | `spend_limit` | `high_value_write` |
| `get_workspace_usage_summary` | `billing.read` | `billing.usage_summary.read` | `usage_summary` | `high_sensitive_read` |
| `post_cloud_run_operator_retry_mns_receipt_delete` | `operator.write` | `operator.mns_receipt_retry` | `cloud_run` | `high_value_write` |
| `post_cloud_run_operator_cleanup_aliyun_eci_runtime` | `operator.write` | `operator.eci_cleanup` | `cloud_run` | `high_value_write` |
| `post_cloud_run_artifact_cleanup_expired` | `artifact.cleanup` | `artifact.cleanup_expired` | `cloud_run_artifact` | `high_value_write` |

Use this route-level pattern for write routes:

```python
require_audited_workspace_permission(
    session,
    "credential.write",
    operation="github_credential.create",
    resource_type="github_credential",
    access_level="high_value_write",
)
result = create_github_credential(session, data)
record_workspace_audit(
    session,
    operation="github_credential.create",
    resource_type="github_credential",
    resource_id=result.id,
    access_level="high_value_write",
    success=True,
    status_code=status.HTTP_201_CREATED,
    commit=True,
)
return result
```

Use this route-level pattern for read routes:

```python
require_audited_workspace_permission(
    session,
    "credential.metadata.read",
    operation="github_credential.list",
    resource_type="github_credential",
    access_level="high_sensitive_read",
)
result = list_github_credentials(session)
record_workspace_audit(
    session,
    operation="github_credential.list",
    resource_type="github_credential",
    access_level="high_sensitive_read",
    success=True,
    status_code=status.HTTP_200_OK,
    metadata={"count": len(result)},
    commit=True,
)
return result
```

- [ ] **Step 4: Run collection-level tests**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py -q -k "viewer_cannot_read_credentials_or_billing_detail or billing_manager_can_read_billing_but_not_credentials_or_models or developer_and_reviewer_can_read_model_config_but_not_manage_it or owner_and_admin_can_manage_credentials_and_model_config or money_moving_workspace_endpoints_require_billing_role"
```

Expected: PASS.

- [ ] **Step 5: Add and run collection-level audit tests**

Append to `apps/api/tests/test_workspace_audit.py`:

```python
def test_sensitive_collection_read_records_redacted_audit(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.get(
            "/github-credentials",
            headers={
                "x-ai-scdc-user-id": "owner_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "owner",
            },
        )

    assert response.status_code == 200
    with Session(build_engine(database_url)) as session:
        audit_log = session.exec(select(WorkspaceAuditLog)).one()

    assert audit_log.operation == "github_credential.list"
    assert audit_log.resource_type == "github_credential"
    assert audit_log.access_level.value == "high_sensitive_read"
    assert audit_log.success is True
    assert audit_log.workspace_id == "workspace_a"


def test_denied_collection_read_records_audit_without_payload(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.get(
            "/github-credentials",
            headers={
                "x-ai-scdc-user-id": "viewer_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "viewer",
            },
        )

    assert response.status_code == 403
    with Session(build_engine(database_url)) as session:
        audit_log = session.exec(select(WorkspaceAuditLog)).one()

    assert audit_log.operation == "github_credential.list"
    assert audit_log.success is False
    assert audit_log.status_code == 403
    assert audit_log.error_code == "insufficient_workspace_role"
    assert "ghp_" not in str(audit_log.model_dump())
```

Run:

```powershell
pytest apps/api/tests/test_workspace_audit.py -q -k "collection_read_records"
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_workspace_audit.py
git commit -m "feat: enforce collection permission matrix"
```

---

### Task 4: Execution and Review Write Permissions

**Files:**
- Modify: `apps/api/app/ai_company_api/services/repository.py`
- Modify: `apps/api/app/ai_company_api/services/sandbox_profiles.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/app/ai_company_api/services/local_runner.py`
- Modify: `apps/api/app/ai_company_api/services/test_review_debug.py`
- Modify: `apps/api/app/ai_company_api/services/patch_approval.py`
- Modify: `apps/api/app/ai_company_api/services/github_pull_request.py`
- Modify: `apps/api/tests/test_auth_rbac_api.py`
- Modify: `apps/api/tests/test_workspace_audit.py`

- [ ] **Step 1: Write execution/review matrix tests**

Append to `apps/api/tests/test_auth_rbac_api.py`:

```python
def test_viewer_and_billing_manager_cannot_create_execution_resources() -> None:
    with build_client() as client:
        project = client.post(
            "/projects",
            json={"name": "execution matrix"},
            headers=auth_headers(roles="owner"),
        ).json()

        for role in ("viewer", "billing_manager", "reviewer"):
            task_response = client.post(
                f"/projects/{project['id']}/tasks",
                json={"title": f"task {role}", "role_required": "backend"},
                headers=auth_headers(roles=role),
            )
            assert task_response.status_code == 403


def test_reviewer_can_review_but_cannot_start_run_or_publish_pr(tmp_path) -> None:
    with build_client(tmp_path / "reviewer.db") as client:
        project = client.post(
            "/projects",
            json={"name": "review permissions"},
            headers=auth_headers(roles="owner"),
        ).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "review target", "role_required": "backend"},
            headers=auth_headers(roles="developer"),
        ).json()

        start_response = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": "repo_missing"},
            headers=auth_headers(roles="reviewer"),
        )
        assert start_response.status_code == 403


def test_cross_workspace_task_create_still_hides_project() -> None:
    headers_a = auth_headers(workspace_id="workspace_a", organization_id="org_a")
    headers_b = auth_headers(
        user_id="dev_b",
        workspace_id="workspace_b",
        organization_id="org_b",
        roles="viewer",
    )
    with build_client() as client:
        project = client.post(
            "/projects",
            json={"name": "private project"},
            headers=headers_a,
        ).json()
        response = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "hidden", "role_required": "backend"},
            headers=headers_b,
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"
```

- [ ] **Step 2: Run execution/review tests and verify failure**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py -q -k "execution_resources or reviewer_can_review or cross_workspace_task_create_still_hides_project"
```

Expected: FAIL because current execution routes do not enforce the full matrix.

- [ ] **Step 3: Add service-level permission checks after resource lookup**

In each service function, call `require_audited_workspace_permission()` after
the existing parent/resource lookup has performed `enforce_workspace_access`.
Use this mapping:

| File | Function | Permission | Operation | Resource type |
| --- | --- | --- | --- | --- |
| `repository.py` | `create_project` | `project.write` | `project.create` | `project` |
| `repository.py` | `create_repository` | `repository.write` | `repository.create` | `repository` |
| `repository.py` | `delete_repository` | `repository.write` | `repository.delete` | `repository` |
| `repository.py` | `create_conversation` | `conversation.write` | `conversation.create` | `conversation` |
| `repository.py` | `create_message` | `conversation.write` | `conversation.message.create` | `message` |
| `repository.py` | `create_planner_run` | `planner.write` | `planner_run.create` | `planner_run` |
| `repository.py` | `approve_planner_run` | `planner.review` | `planner_run.approve` | `planner_run` |
| `repository.py` | `reject_planner_run` | `planner.review` | `planner_run.reject` | `planner_run` |
| `repository.py` | `create_task` | `task.write` | `task.create` | `task` |
| `repository.py` | `transition_task` | `task.write` | `task.transition` | `task` |
| `sandbox_profiles.py` | `create_sandbox_profile` | `repository.write` | `sandbox_profile.create` | `sandbox_profile` |
| `cloud_runner.py` | `start_cloud_run` | `run.write` | `cloud_run.start` | `cloud_run` |
| `cloud_runner.py` | `process_cloud_run` | `run.write` | `cloud_run.process` | `cloud_run` |
| `cloud_runner.py` | `cancel_cloud_run` | `run.write` | `cloud_run.cancel` | `cloud_run` |
| `local_runner.py` | `start_local_task_run` | `run.write` | `local_run.start` | `local_run` |
| `test_review_debug.py` | `start_patch_test_run` | `run.write` | `patch_test.start` | `patch_artifact` |
| `test_review_debug.py` | `start_patch_review` | `review.write` | `patch_review.start` | `patch_artifact` |
| `patch_approval.py` | `approve_patch_artifact` | `approval.write` | `patch_artifact.approve` | `patch_artifact` |
| `patch_approval.py` | `request_human_approval` | `approval.write` | `patch_approval.request_human` | `patch_approval` |
| `github_pull_request.py` | `create_pull_request_for_approval` | `pull_request.publish` | `pull_request.publish` | `patch_approval` |

Use this pattern after the function has loaded the workspace-scoped resource:

```python
require_audited_workspace_permission(
    session,
    "task.write",
    operation="task.create",
    resource_type="project",
    resource_id=project.id,
    access_level="high_value_write",
)
```

After the write succeeds, record a success audit row with the created or
mutated resource id:

```python
record_workspace_audit(
    session,
    operation="task.create",
    resource_type="task",
    resource_id=task.id,
    access_level="high_value_write",
    success=True,
    status_code=201,
)
```

Import at the top of each touched service file:

```python
from ai_company_api.services.workspace_audit import (
    record_workspace_audit,
    require_audited_workspace_permission,
)
```

- [ ] **Step 4: Run execution/review matrix tests**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py -q -k "execution_resources or reviewer_can_review or cross_workspace_task_create_still_hides_project"
```

Expected: PASS.

- [ ] **Step 5: Add and run high-value write audit test**

Append to `apps/api/tests/test_workspace_audit.py`:

```python
def test_high_value_task_write_records_audit(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        project = client.post(
            "/projects",
            json={"name": "audit project"},
            headers={
                "x-ai-scdc-user-id": "dev_user_a",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "developer",
            },
        ).json()
        response = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "audit task", "role_required": "backend"},
            headers={
                "x-ai-scdc-user-id": "dev_user_a",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "developer",
            },
        )

    assert response.status_code == 201
    with Session(build_engine(database_url)) as session:
        audit_logs = session.exec(select(WorkspaceAuditLog)).all()

    assert any(
        log.operation == "task.create"
        and log.resource_type == "task"
        and log.success is True
        and log.access_level.value == "high_value_write"
        for log in audit_logs
    )
```

Run:

```powershell
pytest apps/api/tests/test_workspace_audit.py::test_high_value_task_write_records_audit -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add apps/api/app/ai_company_api/services/repository.py apps/api/app/ai_company_api/services/sandbox_profiles.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/app/ai_company_api/services/local_runner.py apps/api/app/ai_company_api/services/test_review_debug.py apps/api/app/ai_company_api/services/patch_approval.py apps/api/app/ai_company_api/services/github_pull_request.py apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_workspace_audit.py
git commit -m "feat: enforce execution permission matrix"
```

---

### Task 5: Sensitive Read Permissions and Audit

**Files:**
- Modify: `apps/api/app/ai_company_api/services/artifact_plane.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_run_logs.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/app/ai_company_api/services/local_runner.py`
- Modify: `apps/api/app/ai_company_api/services/test_review_debug.py`
- Modify: `apps/api/app/ai_company_api/services/repository.py`
- Modify: `apps/api/app/ai_company_api/services/sandbox_profiles.py`
- Modify: `apps/api/app/ai_company_api/services/model_settings.py`
- Modify: `apps/api/app/ai_company_api/services/budgeting.py`
- Modify: `apps/api/tests/test_auth_rbac_api.py`
- Modify: `apps/api/tests/test_workspace_audit.py`

- [ ] **Step 1: Write sensitive read tests**

Append to `apps/api/tests/test_auth_rbac_api.py`:

```python
def test_viewer_cannot_read_sensitive_execution_or_conversation_content(tmp_path) -> None:
    with build_client(tmp_path / "sensitive.db") as client:
        project = client.post(
            "/projects",
            json={"name": "sensitive read"},
            headers=auth_headers(roles="owner"),
        ).json()
        conversation = client.post(
            f"/projects/{project['id']}/conversations",
            json={"title": "sensitive conversation"},
            headers=auth_headers(roles="developer"),
        ).json()
        client.post(
            f"/conversations/{conversation['id']}/messages",
            json={"sender_type": "user", "content": "private prompt"},
            headers=auth_headers(roles="developer"),
        )

        response = client.get(
            f"/conversations/{conversation['id']}/messages",
            headers=auth_headers(roles="viewer"),
        )

    assert response.status_code == 403


def test_reviewer_can_read_sensitive_conversation_content(tmp_path) -> None:
    with build_client(tmp_path / "reviewer_sensitive.db") as client:
        project = client.post(
            "/projects",
            json={"name": "reviewer sensitive read"},
            headers=auth_headers(roles="owner"),
        ).json()
        conversation = client.post(
            f"/projects/{project['id']}/conversations",
            json={"title": "review context"},
            headers=auth_headers(roles="developer"),
        ).json()
        response = client.get(
            f"/conversations/{conversation['id']}/messages",
            headers=auth_headers(roles="reviewer"),
        )

    assert response.status_code == 200
```

- [ ] **Step 2: Run sensitive read tests and verify failure**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py -q -k "sensitive_execution_or_conversation_content or reviewer_can_read_sensitive_conversation_content"
```

Expected: FAIL because viewer can still read conversation messages.

- [ ] **Step 3: Protect sensitive read service functions**

Add `require_audited_workspace_permission()` after existing workspace lookups
and before returning sensitive content. Record success audit rows for allowed
reads with `access_level="high_sensitive_read"`.

Use this mapping:

| File | Function | Permission | Operation | Resource type |
| --- | --- | --- | --- | --- |
| `repository.py` | `list_messages` | `conversation.sensitive.read` | `conversation.message.list` | `conversation` |
| `sandbox_profiles.py` | `list_sandbox_profiles` | `execution_config.read` | `sandbox_profile.list` | `sandbox_profile` |
| `sandbox_profiles.py` | `get_sandbox_profile_read` | `execution_config.read` | `sandbox_profile.read` | `sandbox_profile` |
| `model_settings.py` | `list_model_providers` | `model_config.read` | `model_provider.list` | `model_provider` |
| `model_settings.py` | `list_model_routes` | `model_config.read` | `model_route.list` | `model_route` |
| `model_settings.py` | `resolve_model_route` | `model_config.read` | `model_route.resolve` | `model_route` |
| `budgeting.py` | `cloud_run_cost_summary` | `billing.read` | `billing.cloud_run_cost_summary.read` | `cloud_run` |
| `cloud_runner.py` | `list_cloud_runs` | `execution.evidence.read` | `cloud_run.list` | `cloud_run` |
| `cloud_runner.py` | `get_cloud_run_read` | `execution.evidence.read` | `cloud_run.read` | `cloud_run` |
| `local_runner.py` | `list_local_task_runs` | `execution.evidence.read` | `local_run.list` | `local_run` |
| `local_runner.py` | `get_local_task_run` | `execution.evidence.read` | `local_run.read` | `local_run` |
| `local_runner.py` | `get_patch_artifact` | `execution.evidence.read` | `patch_artifact.read` | `patch_artifact` |
| `test_review_debug.py` | `list_patch_test_runs` | `execution.evidence.read` | `patch_test.list` | `local_test_run` |
| `test_review_debug.py` | `get_test_run` | `execution.evidence.read` | `patch_test.read` | `local_test_run` |
| `test_review_debug.py` | `list_patch_reviews` | `execution.evidence.read` | `patch_review.list` | `patch_review` |
| `test_review_debug.py` | `list_debug_attempts` | `execution.evidence.read` | `debug_attempt.list` | `debug_attempt` |
| `artifact_plane.py` | `build_cloud_run_artifact_manifest` | `artifact.sensitive.read` | `artifact.manifest.read` | `cloud_run_artifact` |
| `artifact_plane.py` | `list_cloud_run_artifacts` | `artifact.sensitive.read` | `artifact.list` | `cloud_run_artifact` |
| `artifact_plane.py` | `get_cloud_run_artifact_descriptor` | `artifact.sensitive.read` | `artifact.descriptor.read` | `cloud_run_artifact` |
| `artifact_plane.py` | `read_cloud_run_artifact_content` | `artifact.sensitive.read` | `artifact.content.read` | `cloud_run_artifact` |
| `artifact_plane.py` | `build_cloud_run_artifact_download` | `artifact.sensitive.read` | `artifact.download.create` | `cloud_run_artifact` |
| `cloud_run_logs.py` | `list_cloud_run_log_window` | `log.sensitive.read` | `cloud_log.window.read` | `cloud_run_log` |
| `cloud_runner.py` | `list_cloud_run_logs` | `log.sensitive.read` | `cloud_log.list` | `cloud_run_log` |

Use this sensitive read pattern:

```python
require_audited_workspace_permission(
    session,
    "conversation.sensitive.read",
    operation="conversation.message.list",
    resource_type="conversation",
    resource_id=conversation.id,
    access_level="high_sensitive_read",
)
messages = session.exec(statement).all()
record_workspace_audit(
    session,
    operation="conversation.message.list",
    resource_type="conversation",
    resource_id=conversation.id,
    access_level="high_sensitive_read",
    success=True,
    status_code=200,
    metadata={"count": len(messages)},
    commit=True,
)
return messages
```

- [ ] **Step 4: Run sensitive read tests**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py -q -k "sensitive_execution_or_conversation_content or reviewer_can_read_sensitive_conversation_content"
```

Expected: PASS.

- [ ] **Step 5: Add and run sensitive read audit redaction test**

Append to `apps/api/tests/test_workspace_audit.py`:

```python
def test_sensitive_message_read_audit_does_not_store_message_content(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    secret_prompt = "private prompt that must not be audited"

    with TestClient(create_app(database_url=database_url)) as client:
        project = client.post(
            "/projects",
            json={"name": "audit sensitive read"},
            headers={
                "x-ai-scdc-user-id": "owner_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "owner",
            },
        ).json()
        conversation = client.post(
            f"/projects/{project['id']}/conversations",
            json={"title": "audit conversation"},
            headers={
                "x-ai-scdc-user-id": "dev_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "developer",
            },
        ).json()
        client.post(
            f"/conversations/{conversation['id']}/messages",
            json={"sender_type": "user", "content": secret_prompt},
            headers={
                "x-ai-scdc-user-id": "dev_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "developer",
            },
        )
        response = client.get(
            f"/conversations/{conversation['id']}/messages",
            headers={
                "x-ai-scdc-user-id": "review_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "reviewer",
            },
        )

    assert response.status_code == 200
    with Session(build_engine(database_url)) as session:
        logs = session.exec(select(WorkspaceAuditLog)).all()

    serialized_logs = " ".join(str(log.model_dump()) for log in logs)
    assert any(log.operation == "conversation.message.list" for log in logs)
    assert secret_prompt not in serialized_logs
```

Run:

```powershell
pytest apps/api/tests/test_workspace_audit.py::test_sensitive_message_read_audit_does_not_store_message_content -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add apps/api/app/ai_company_api/services/artifact_plane.py apps/api/app/ai_company_api/services/cloud_run_logs.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/app/ai_company_api/services/local_runner.py apps/api/app/ai_company_api/services/test_review_debug.py apps/api/app/ai_company_api/services/repository.py apps/api/app/ai_company_api/services/sandbox_profiles.py apps/api/app/ai_company_api/services/model_settings.py apps/api/app/ai_company_api/services/budgeting.py apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_workspace_audit.py
git commit -m "feat: protect sensitive workspace reads"
```

---

### Task 6: Route Matrix Regression Sweep

**Files:**
- Modify: `apps/api/tests/test_auth_rbac_api.py`
- Modify API or service files only if this task exposes a route still using
  the wrong permission.

- [ ] **Step 1: Add a compact route matrix regression test**

Append to `apps/api/tests/test_auth_rbac_api.py`:

```python
def test_phase_13b_permission_matrix_representative_routes() -> None:
    cases = [
        ("get", "/github-credentials", None, {"owner", "admin"}, {"viewer", "developer", "reviewer", "billing_manager"}),
        ("get", "/usage-ledger", None, {"owner", "admin", "billing_manager"}, {"viewer", "developer", "reviewer"}),
        ("post", "/model-providers", {"name": "matrix-model", "provider_type": "fake"}, {"owner", "admin"}, {"viewer", "developer", "reviewer", "billing_manager"}),
        ("post", "/projects", {"name": "matrix project"}, {"owner", "admin", "developer"}, {"viewer", "reviewer", "billing_manager"}),
    ]

    with build_client() as client:
        for method, path, payload, allowed_roles, denied_roles in cases:
            for role in allowed_roles:
                response = getattr(client, method)(
                    path,
                    json=payload,
                    headers=auth_headers(
                        roles=role,
                        workspace_id=f"workspace_allowed_{role}_{method}",
                        organization_id=f"org_allowed_{role}_{method}",
                    ),
                )
                assert response.status_code < 400, (method, path, role, response.text)

            for role in denied_roles:
                response = getattr(client, method)(
                    path,
                    json=payload,
                    headers=auth_headers(
                        roles=role,
                        workspace_id=f"workspace_denied_{role}_{method}",
                        organization_id=f"org_denied_{role}_{method}",
                    ),
                )
                assert response.status_code == 403, (method, path, role, response.text)
```

- [ ] **Step 2: Run matrix regression**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py::test_phase_13b_permission_matrix_representative_routes -q
```

Expected: PASS.

- [ ] **Step 3: Run focused RBAC and audit suites**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_workspace_audit.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

Run:

```powershell
git add apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_workspace_audit.py apps/api/app/ai_company_api
git commit -m "test: cover phase 13b permission matrix"
```

---

### Task 7: Documentation and Verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`
- Modify: `STATUS.md`

- [ ] **Step 1: Update documentation**

Update the Phase 13B commercial trust boundary sections in all three docs to
state:

```markdown
Phase 13B now includes a test-backed workspace role permission matrix and a
general `WorkspaceAuditLog` for high-value writes plus high-sensitive reads.
Secret-specific create/open/delete audit remains in `SecretAccessAuditLog`.
Viewer access is limited to low-sensitive metadata; current full-detail
execution evidence, artifact, log, message, sandbox, model configuration,
credential, and billing detail reads require explicit non-viewer permissions.
```

Keep these items listed as remaining work:

```markdown
Remaining commercial readiness work includes production IdP/session issuance,
payment and invoice integration, desktop billing UI, full operator console,
real provider price tables, public destructive OSS cleanup policy, and retained
target-account KMS smoke evidence.
```

- [ ] **Step 2: Run focused trust-boundary tests**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_workspace_audit.py apps/api/tests/test_secret_access_audit.py -q
```

Expected: PASS.

- [ ] **Step 3: Run broader API trust-boundary tests**

Run:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_usage_ledger_api.py apps/api/tests/test_usage_cost_quota_api.py apps/api/tests/test_model_settings_api.py apps/api/tests/test_github_repository_api.py apps/api/tests/test_pull_request_api.py apps/api/tests/test_planner_endpoints.py -q
```

Expected: PASS.

- [ ] **Step 4: Run compile and formatting checks**

Run:

```powershell
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_workspace_audit.py
git diff --check
```

Expected: both commands exit 0.

- [ ] **Step 5: Run full verification**

Run:

```powershell
pytest apps/api/tests -q
pnpm test:js
pnpm typecheck
```

Expected: API tests pass, JS tests pass, and typecheck passes.

- [ ] **Step 6: Commit docs**

Run:

```powershell
git add docs/architecture.md docs/superpowers/status.md STATUS.md
git commit -m "docs: update phase 13b rbac audit status"
```

---

## Self-Review Notes

- Spec coverage: permission policy, high-value writes, high-sensitive reads,
  workspace audit model, secret-audit separation, 404/403 ordering, redaction,
  tests, verification, and docs all have tasks.
- Scope choice: current full-detail evidence routes are protected rather than
  adding new redacted viewer schemas in this slice.
- Execution ordering: implement Task 1 and Task 2 first because later route and
  service tasks import those helpers.
- Cross-workspace safety: service-level checks in Tasks 4 and 5 must happen
  after existing `enforce_workspace_access()` calls for resource-specific
  routes.
