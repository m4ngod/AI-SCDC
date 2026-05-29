from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import Column, Enum as SAEnum, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel

from ai_company_api.services.task_state import TaskStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


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


class ModelRouteStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UsageType(str, Enum):
    MODEL_TOKENS = "model_tokens"


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


class ModelRoute(SQLModel, table=True):
    __tablename__ = "model_route"

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

    id: str = Field(default_factory=lambda: prefixed_id("usage"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    organization_id: str = Field(default="dev_organization", index=True)
    user_id: str = Field(default="dev_user", index=True)
    project_id: str | None = Field(default=None, index=True, foreign_key="project.id")
    task_id: str | None = Field(default=None, index=True, foreign_key="task.id")
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
    unit_price_cents: int = 0
    amount_cents: int = 0
    raw_usage_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)


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
    assigned_agent_profile_id: str | None = None
    repo_id: str | None = None
    branch_name: str | None = None
    worktree_ref: str | None = None
    budget_limit: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskEvent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: prefixed_id("event"), primary_key=True)
    task_id: str = Field(index=True, foreign_key="task.id")
    event_type: str
    actor_type: str
    actor_id: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)
