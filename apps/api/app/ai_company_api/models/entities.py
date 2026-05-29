from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from sqlalchemy import Column, JSON
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
    status: PlannerRunStatus = Field(default=PlannerRunStatus.DRAFTED, index=True)
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

    id: str = Field(default_factory=lambda: prefixed_id("approval"), primary_key=True)
    workspace_id: str = "dev_workspace"
    project_id: str = Field(index=True, foreign_key="project.id")
    planner_run_id: str = Field(index=True, foreign_key="planner_run.id")
    action_type: str = "approve_planner_run"
    risk_level: str = "medium"
    reason: str = ""
    status: ApprovalStatus
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


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
