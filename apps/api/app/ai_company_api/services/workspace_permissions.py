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
