from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import Column, Enum as SAEnum, Index, JSON, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from ai_company_api.services.task_state import TaskStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def uuid_hex() -> str:
    return uuid4().hex


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: prefixed_id("project"), primary_key=True)
    workspace_id: str = "dev_workspace"
    name: str
    description: str = ""
    created_by: str = "dev_user"
    created_at: datetime = Field(default_factory=utc_now)


class Conversation(SQLModel, table=True):
    id: str = Field(
        default_factory=lambda: prefixed_id("conversation"),
        primary_key=True,
    )
    project_id: str = Field(index=True, foreign_key="project.id")
    user_id: str = "dev_user"
    title: str
    conversation_type: str = "planning"
    created_at: datetime = Field(default_factory=utc_now)


class Message(SQLModel, table=True):
    id: str = Field(default_factory=lambda: prefixed_id("message"), primary_key=True)
    conversation_id: str = Field(index=True, foreign_key="conversation.id")
    sender_type: str
    sender_id: str = "dev_user"
    content: str
    structured_payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
    )
    created_at: datetime = Field(default_factory=utc_now)


class PlannerRunStatus(str, Enum):
    DRAFTED = "DRAFTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ModelProviderType(str, Enum):
    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai_compatible"
    DEEPSEEK = "deepseek"


class ModelProviderStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ModelCredentialStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class GitHubCredentialStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class ModelRouteStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UsageType(str, Enum):
    MODEL_TOKENS = "model_tokens"
    CLOUD_RUN_RUNTIME_SECONDS = "cloud_run_runtime_seconds"
    WORKER_SUBMISSIONS = "worker_submissions"
    OBJECT_STORAGE_BYTES = "object_storage_bytes"
    OBJECT_STORAGE_READS = "object_storage_reads"
    LOG_SYNC_CALLS = "log_sync_calls"
    QUEUE_MESSAGES = "queue_messages"
    PR_PUBLISH_ATTEMPTS = "pr_publish_attempts"


class BudgetReservationStatus(str, Enum):
    RESERVED = "reserved"
    RELEASED = "released"
    SETTLED = "settled"


class WorkspaceAuditAccessLevel(str, Enum):
    HIGH_VALUE_WRITE = "high_value_write"
    HIGH_SENSITIVE_READ = "high_sensitive_read"
    SYSTEM_EVENT = "system_event"


class WorkspaceRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    DEVELOPER = "developer"
    REVIEWER = "reviewer"
    BILLING_MANAGER = "billing_manager"
    VIEWER = "viewer"


class User(SQLModel, table=True):
    __tablename__ = "user_account"

    id: str = Field(default_factory=lambda: prefixed_id("user"), primary_key=True)
    email: str | None = Field(default=None, index=True)
    display_name: str = ""
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Organization(SQLModel, table=True):
    __tablename__ = "organization"

    id: str = Field(default_factory=lambda: prefixed_id("org"), primary_key=True)
    name: str
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Workspace(SQLModel, table=True):
    __tablename__ = "workspace"

    id: str = Field(default_factory=lambda: prefixed_id("workspace"), primary_key=True)
    organization_id: str = Field(index=True, foreign_key="organization.id")
    name: str
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OrganizationMember(SQLModel, table=True):
    __tablename__ = "organization_member"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_organization_member_workspace_user",
        ),
    )

    id: str = Field(default_factory=lambda: prefixed_id("member"), primary_key=True)
    organization_id: str = Field(index=True, foreign_key="organization.id")
    workspace_id: str = Field(index=True, foreign_key="workspace.id")
    user_id: str = Field(index=True, foreign_key="user_account.id")
    role: WorkspaceRole = Field(
        default=WorkspaceRole.DEVELOPER,
        sa_column=Column(
            SAEnum(
                WorkspaceRole,
                name="workspace_role",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    api_token_hash: str | None = Field(default=None, index=True)
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExternalIdentity(SQLModel, table=True):
    __tablename__ = "external_identity"
    __table_args__ = (
        UniqueConstraint(
            "issuer",
            "subject",
            name="uq_external_identity_issuer_subject",
        ),
    )

    id: str = Field(
        default_factory=lambda: prefixed_id("external_identity"),
        primary_key=True,
    )
    issuer: str = Field(index=True)
    subject: str = Field(index=True)
    user_id: str = Field(index=True, foreign_key="user_account.id")
    email: str | None = Field(default=None, index=True)
    status: str = Field(default="active", index=True)
    last_confirmed_status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DeviceSession(SQLModel, table=True):
    __tablename__ = "device_session"

    id: str = Field(
        default_factory=lambda: prefixed_id("device_session"),
        primary_key=True,
    )
    user_id: str = Field(index=True, foreign_key="user_account.id")
    active_workspace_id: str = Field(index=True, foreign_key="workspace.id")
    active_organization_id: str = Field(index=True, foreign_key="organization.id")
    secret_hash: str
    status: str = Field(default="active", index=True)
    idle_expires_at: datetime = Field(index=True)
    last_seen_at: datetime = Field(default_factory=utc_now, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)
    revoked_at: datetime | None = None


class LoginTransaction(SQLModel, table=True):
    __tablename__ = "login_transaction"

    id: str = Field(
        default_factory=lambda: prefixed_id("login_transaction"),
        primary_key=True,
    )
    state_hash: str = Field(unique=True, index=True)
    nonce_hash: str
    pkce_verifier: str
    return_to: str
    browser_binding_hash: str
    redirect_uri: str
    correlation_id: str = Field(index=True)
    status: str = Field(default="pending", index=True)
    completed_session_id: str | None = Field(default=None, index=True)
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class IdentityAuditEvent(SQLModel, table=True):
    __tablename__ = "identity_audit_event"

    id: str = Field(
        default_factory=lambda: prefixed_id("identity_audit"),
        primary_key=True,
    )
    event_type: str = Field(index=True)
    outcome: str = Field(index=True)
    reason_code: str = Field(index=True)
    correlation_id: str = Field(index=True)
    user_id: str | None = Field(default=None, index=True)
    external_identity_id: str | None = Field(default=None, index=True)
    device_session_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class Repository(SQLModel, table=True):
    __tablename__ = "repository"

    id: str = Field(default_factory=lambda: prefixed_id("repo"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    name: str
    local_path: str
    default_branch: str = "main"
    provider: str = Field(default="local", index=True)
    repo_url: str = ""
    github_owner: str | None = Field(default=None, index=True)
    github_repo: str | None = Field(default=None, index=True)
    github_credential_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="github_credential.id",
    )
    connection_status: str = Field(default="active", index=True)
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PlannerRun(SQLModel, table=True):
    __tablename__ = "planner_run"

    id: str = Field(default_factory=lambda: prefixed_id("planner_run"), primary_key=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    conversation_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="conversation.id",
    )
    goal: str
    status: PlannerRunStatus = Field(
        default=PlannerRunStatus.DRAFTED,
        sa_column=Column(
            SAEnum(
                PlannerRunStatus,
                name="planner_run_status",
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    planner_kind: str = "fake"
    model_route_id: str | None = Field(default=None, index=True)
    model_provider_name: str | None = None
    model_name: str | None = None
    fallback_reason: str | None = None
    draft_count: int = 0
    created_by: str = "dev_user"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PlannerTaskDraft(SQLModel, table=True):
    __tablename__ = "planner_task_draft"

    id: str = Field(default_factory=lambda: prefixed_id("planner_draft"), primary_key=True)
    planner_run_id: str = Field(index=True, foreign_key="planner_run.id")
    sequence: int = Field(index=True)
    title: str
    role_required: str
    objective: str
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    allowed_paths: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    required_tests: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    risk_level: str = "medium"
    created_at: datetime = Field(default_factory=utc_now)


class Approval(SQLModel, table=True):
    __tablename__ = "approval"
    __table_args__ = (
        UniqueConstraint("planner_run_id", name="uq_approval_planner_run_id"),
    )

    id: str = Field(default_factory=lambda: prefixed_id("approval"), primary_key=True)
    workspace_id: str = "dev_workspace"
    project_id: str = Field(index=True, foreign_key="project.id")
    planner_run_id: str = Field(index=True, foreign_key="planner_run.id")
    action_type: str = "approve_planner_run"
    risk_level: str = "medium"
    reason: str = ""
    status: ApprovalStatus = Field(
        sa_column=Column(
            SAEnum(
                ApprovalStatus,
                name="approval_status",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
        ),
    )
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ModelProvider(SQLModel, table=True):
    __tablename__ = "model_provider"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_model_provider_workspace_name"),
    )

    id: str = Field(
        default_factory=lambda: prefixed_id("model_provider"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    name: str = Field(index=True)
    provider_type: ModelProviderType = Field(
        sa_column=Column(
            SAEnum(
                ModelProviderType,
                name="model_provider_type",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
        ),
    )
    base_url: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    status: ModelProviderStatus = Field(
        default=ModelProviderStatus.ACTIVE,
        sa_column=Column(
            SAEnum(
                ModelProviderStatus,
                name="model_provider_status",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ModelCredential(SQLModel, table=True):
    __tablename__ = "model_credential"

    id: str = Field(
        default_factory=lambda: prefixed_id("model_credential"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    provider_id: str = Field(index=True, foreign_key="model_provider.id")
    display_name: str
    secret_last4: str = ""
    encrypted_secret: str
    status: ModelCredentialStatus = Field(
        default=ModelCredentialStatus.ACTIVE,
        sa_column=Column(
            SAEnum(
                ModelCredentialStatus,
                name="model_credential_status",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class GitHubCredential(SQLModel, table=True):
    __tablename__ = "github_credential"

    id: str = Field(
        default_factory=lambda: prefixed_id("github_credential"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    display_name: str
    token_last4: str = ""
    encrypted_token: str
    status: GitHubCredentialStatus = Field(
        default=GitHubCredentialStatus.ACTIVE,
        sa_column=Column(
            SAEnum(
                GitHubCredentialStatus,
                name="github_credential_status",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SandboxProfile(SQLModel, table=True):
    __tablename__ = "sandbox_profile"

    id: str = Field(
        default_factory=lambda: prefixed_id("sandbox_profile"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    repo_id: str = Field(index=True, foreign_key="repository.id")
    name: str
    docker_image: str
    patch_commands: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    test_commands: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    allowed_env_vars: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    network_enabled: bool = True
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ModelRoute(SQLModel, table=True):
    __tablename__ = "model_route"
    __table_args__ = (
        Index(
            "uq_model_route_active_workspace_role",
            "workspace_id",
            "agent_role",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: str = Field(default_factory=lambda: prefixed_id("model_route"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    agent_role: str = Field(index=True)
    provider_id: str = Field(index=True, foreign_key="model_provider.id")
    credential_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="model_credential.id",
    )
    model_name: str
    fallback_models: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: ModelRouteStatus = Field(
        default=ModelRouteStatus.ACTIVE,
        sa_column=Column(
            SAEnum(
                ModelRouteStatus,
                name="model_route_status",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UsageLedgerEntry(SQLModel, table=True):
    __tablename__ = "usage_ledger_entry"
    __table_args__ = (
        UniqueConstraint(
            "cloud_run_id",
            "usage_type",
            name="uq_usage_ledger_cloud_run_usage_type",
        ),
    )

    id: str = Field(default_factory=lambda: prefixed_id("usage"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    organization_id: str = Field(default="dev_organization", index=True)
    user_id: str = Field(default="dev_user", index=True)
    project_id: str | None = Field(default=None, index=True, foreign_key="project.id")
    task_id: str | None = Field(default=None, index=True, foreign_key="task.id")
    cloud_run_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="cloud_run.id",
    )
    planner_run_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="planner_run.id",
    )
    usage_type: UsageType = Field(
        default=UsageType.MODEL_TOKENS,
        sa_column=Column(
            SAEnum(
                UsageType,
                name="usage_type",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    provider_name: str
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    quantity: int = Field(default=0, index=True)
    unit_name: str = ""
    unit_price_cents: int = 0
    amount_cents: int = 0
    raw_usage_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class SecretAccessAuditLog(SQLModel, table=True):
    __tablename__ = "secret_access_audit_log"

    id: str = Field(
        default_factory=lambda: prefixed_id("secret_access"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    organization_id: str = Field(default="dev_organization", index=True)
    user_id: str = Field(default="dev_user", index=True)
    auth_mode: str = Field(default="system", index=True)
    secret_kind: str = Field(index=True)
    secret_id: str = Field(index=True)
    operation: str = Field(default="open", index=True)
    access_reason: str = Field(index=True)
    success: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)


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


class CreditWallet(SQLModel, table=True):
    __tablename__ = "credit_wallet"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_credit_wallet_workspace"),
    )

    id: str = Field(default_factory=lambda: prefixed_id("wallet"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    organization_id: str = Field(default="dev_organization", index=True)
    balance_cents: int = Field(default=0, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SpendLimit(SQLModel, table=True):
    __tablename__ = "spend_limit"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_spend_limit_workspace"),
    )

    id: str = Field(default_factory=lambda: prefixed_id("spend_limit"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    monthly_limit_cents: int = Field(default=0, index=True)
    per_run_limit_cents: int = Field(default=0, index=True)
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BudgetReservation(SQLModel, table=True):
    __tablename__ = "budget_reservation"

    id: str = Field(
        default_factory=lambda: prefixed_id("budget_reservation"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    organization_id: str = Field(default="dev_organization", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    cloud_run_id: str | None = Field(default=None, index=True, foreign_key="cloud_run.id")
    reserved_cents: int = Field(default=0, index=True)
    settled_cents: int = Field(default=0, index=True)
    status: BudgetReservationStatus = Field(
        default=BudgetReservationStatus.RESERVED,
        sa_column=Column(
            SAEnum(
                BudgetReservationStatus,
                name="budget_reservation_status",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    settled_at: datetime | None = None


class Task(SQLModel, table=True):
    id: str = Field(default_factory=lambda: prefixed_id("task"), primary_key=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    conversation_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="conversation.id",
    )
    parent_task_id: str | None = Field(default=None, index=True, foreign_key="task.id")
    title: str
    description: str = ""
    role_required: str
    status: TaskStatus = Field(default=TaskStatus.CREATED, index=True)
    priority: int = 0
    risk_level: str = "medium"
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    allowed_paths: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    required_tests: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    assigned_agent_profile_id: str | None = None
    repo_id: str | None = None
    branch_name: str | None = None
    worktree_ref: str | None = None
    budget_limit: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class LocalTaskRun(SQLModel, table=True):
    __tablename__ = "local_task_run"

    id: str = Field(default_factory=lambda: prefixed_id("local_run"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    repo_id: str = Field(index=True, foreign_key="repository.id")
    status: str = Field(default="queued", index=True)
    runner_kind: str = "local_worktree"
    base_branch: str = ""
    base_sha: str | None = None
    head_sha: str | None = None
    worktree_path: str | None = None
    patch_artifact_id: str | None = Field(default=None, index=True)
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CloudRun(SQLModel, table=True):
    __tablename__ = "cloud_run"

    id: str = Field(default_factory=lambda: prefixed_id("cloud_run"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    repo_id: str = Field(index=True, foreign_key="repository.id")
    local_run_id: str | None = Field(default=None, index=True, foreign_key="local_task_run.id")
    sandbox_profile_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="sandbox_profile.id",
    )
    patch_command_key: str | None = None
    test_command_keys: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    command_results: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    base_branch: str = ""
    head_branch: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    sandbox_kind: str = "fake"
    patch_artifact_id: str | None = Field(default=None, index=True)
    failure_reason: str | None = None
    cancel_requested: bool = Field(default=False, index=True)
    cancel_requested_at: datetime | None = None
    cancelled_at: datetime | None = None
    worker_id: str | None = Field(default=None, index=True)
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    queue_provider: str = Field(default="local_db", index=True)
    remote_worker_kind: str | None = Field(default=None, index=True)
    lease_id: str | None = Field(default=None, index=True)
    lease_expires_at: datetime | None = Field(default=None, index=True)
    heartbeat_at: datetime | None = None
    attempt_count: int = Field(default=0)
    max_attempts: int = Field(default=3)
    last_queue_error: str | None = None
    queue_message_id: str | None = Field(default=None, index=True)
    queue_receipt: str | None = None
    runtime_provider: str | None = Field(default=None, index=True)
    runtime_job_id: str | None = Field(default=None, index=True)
    storage_provider: str | None = Field(default=None, index=True)
    artifact_manifest_uri: str | None = None
    log_stream_uri: str | None = None
    artifact_manifest_sha256: str | None = None
    artifact_manifest_size_bytes: int | None = None
    artifact_manifest_content_type: str | None = None
    log_stream_sha256: str | None = None
    log_stream_size_bytes: int | None = None
    log_stream_content_type: str | None = None
    external_status: str | None = Field(default=None, index=True)
    external_error: str | None = None
    callback_token_hash: str | None = Field(default=None, index=True)
    callback_token_expires_at: datetime | None = Field(default=None, index=True)
    callback_token_used_at: datetime | None = None
    budget_reservation_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="budget_reservation.id",
    )
    estimated_cost_cents: int = Field(default=0, index=True)
    actual_cost_cents: int = Field(default=0, index=True)
    cost_summary_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class CloudRunLogEntry(SQLModel, table=True):
    __tablename__ = "cloud_run_log_entry"

    id: str = Field(default_factory=uuid_hex, primary_key=True)
    cloud_run_id: str = Field(foreign_key="cloud_run.id", index=True)
    workspace_id: str = Field(index=True)
    level: str = Field(default="info", index=True)
    event: str = Field(index=True)
    message: str
    payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class CloudRunStoredObject(SQLModel, table=True):
    __tablename__ = "cloud_run_stored_object"

    id: str = Field(default_factory=uuid_hex, primary_key=True)
    workspace_id: str = Field(index=True)
    cloud_run_id: str = Field(index=True)
    kind: str = Field(index=True)
    uri: str = Field(index=True)
    sha256: str
    size_bytes: int
    content_type: str = "text/plain"
    text_content: str
    expires_at: datetime | None = Field(default=None, index=True)
    retention_policy: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class PatchArtifact(SQLModel, table=True):
    __tablename__ = "patch_artifact"

    id: str = Field(default_factory=lambda: prefixed_id("patch"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    local_run_id: str = Field(index=True, foreign_key="local_task_run.id")
    summary: str
    files_changed: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tests_run: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    test_result: str = "not_run"
    risks: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    diff_text: str
    created_at: datetime = Field(default_factory=utc_now, index=True)


class LocalTestRun(SQLModel, table=True):
    __tablename__ = "local_test_run"

    id: str = Field(default_factory=lambda: prefixed_id("test_run"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    local_run_id: str = Field(index=True, foreign_key="local_task_run.id")
    patch_artifact_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="patch_artifact.id",
    )
    status: str = Field(default="running", index=True)
    commands: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    command_results: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    failure_reason: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now, index=True)


class PatchReview(SQLModel, table=True):
    __tablename__ = "patch_review"
    __table_args__ = (
        UniqueConstraint(
            "patch_artifact_id",
            "reviewer_kind",
            name="uq_patch_review_artifact_reviewer_kind",
        ),
    )

    id: str = Field(default_factory=lambda: prefixed_id("review"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    local_run_id: str = Field(index=True, foreign_key="local_task_run.id")
    patch_artifact_id: str = Field(index=True, foreign_key="patch_artifact.id")
    test_run_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="local_test_run.id",
    )
    reviewer_kind: str = "deterministic"
    verdict: str = Field(index=True)
    issues: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    required_changes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class PatchApproval(SQLModel, table=True):
    __tablename__ = "patch_approval"
    __table_args__ = (
        UniqueConstraint(
            "patch_artifact_id",
            name="uq_patch_approval_patch_artifact_id",
        ),
    )

    id: str = Field(
        default_factory=lambda: prefixed_id("patch_approval"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    local_run_id: str = Field(index=True, foreign_key="local_task_run.id")
    patch_artifact_id: str = Field(index=True, foreign_key="patch_artifact.id")
    review_id: str = Field(index=True, foreign_key="patch_review.id")
    status: str = Field(default="approved", index=True)
    approved_by: str = "dev_user"
    merge_instructions: str
    created_at: datetime = Field(default_factory=utc_now, index=True)


class PullRequestRecord(SQLModel, table=True):
    __tablename__ = "pull_request_record"
    __table_args__ = (
        UniqueConstraint(
            "patch_approval_id",
            name="uq_pull_request_record_patch_approval_id",
        ),
    )

    id: str = Field(default_factory=lambda: prefixed_id("pull_request"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    repo_id: str = Field(index=True, foreign_key="repository.id")
    patch_artifact_id: str = Field(index=True, foreign_key="patch_artifact.id")
    patch_approval_id: str = Field(index=True, foreign_key="patch_approval.id")
    cloud_run_id: str | None = Field(default=None, index=True, foreign_key="cloud_run.id")
    head_branch: str
    base_branch: str
    github_pr_number: int
    github_pr_url: str
    status: str = Field(default="created", index=True)
    created_by: str = "dev_user"
    created_at: datetime = Field(default_factory=utc_now, index=True)


class DebugAttempt(SQLModel, table=True):
    __tablename__ = "debug_attempt"

    id: str = Field(default_factory=lambda: prefixed_id("debug"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    patch_artifact_id: str = Field(index=True, foreign_key="patch_artifact.id")
    review_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="patch_review.id",
    )
    test_run_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="local_test_run.id",
    )
    status: str = Field(default="requested", index=True)
    root_cause: str
    fix_summary: str
    created_at: datetime = Field(default_factory=utc_now, index=True)


class TaskEvent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: prefixed_id("event"), primary_key=True)
    task_id: str = Field(index=True, foreign_key="task.id")
    event_type: str
    actor_type: str
    actor_id: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)
