from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ai_company_api.services.task_state import TaskStatus


class AgentRole(str, Enum):
    PLANNER = "planner"
    FRONTEND = "frontend"
    BACKEND = "backend"
    REVIEWER = "reviewer"
    DEBUGGER = "debugger"
    SECURITY = "security"
    PRODUCT = "product"
    DOCUMENTATION = "documentation"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ConversationCreate(BaseModel):
    title: str = "New conversation"
    conversation_type: str = "planning"


class MessageCreate(BaseModel):
    sender_type: str
    content: str
    structured_payload: dict[str, Any] = Field(default_factory=dict)


class PlannerRunCreate(BaseModel):
    goal: str = Field(min_length=1)
    conversation_id: str | None = None


class PlannerTaskDraftRead(BaseModel):
    id: str
    sequence: int
    title: str
    role_required: str
    objective: str
    acceptance_criteria: list[str]
    allowed_paths: list[str]
    required_tests: list[str]
    risk_level: str


class PlannerRunRead(BaseModel):
    id: str
    project_id: str
    conversation_id: str | None
    goal: str
    status: str
    planner_kind: str
    draft_count: int
    drafts: list[PlannerTaskDraftRead]


class PlannerRunReject(BaseModel):
    reason: str = ""


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    role_required: AgentRole
    conversation_id: str | None = None
    parent_task_id: str | None = None
    priority: int = 0
    risk_level: RiskLevel = RiskLevel.MEDIUM
    acceptance_criteria: list[str] = Field(default_factory=list)
    assigned_agent_profile_id: str | None = None
    repo_id: str | None = None
    branch_name: str | None = None
    worktree_ref: str | None = None
    budget_limit: int | None = None


class TaskUpdate(BaseModel):
    status: TaskStatus


class TaskRead(BaseModel):
    id: str
    project_id: str
    conversation_id: str | None
    parent_task_id: str | None
    title: str
    description: str
    role_required: str
    status: TaskStatus
    priority: int
    risk_level: str
    acceptance_criteria: list[str]
    assigned_agent_profile_id: str | None
    repo_id: str | None
    branch_name: str | None
    worktree_ref: str | None
    budget_limit: int | None
    created_at: datetime
    updated_at: datetime


class PlannerRunDecisionRead(BaseModel):
    planner_run_id: str
    approval_id: str
    status: str
    created_tasks: list[TaskRead]


class DevIdentity(BaseModel):
    user_id: str
    workspace_id: str
    organization_id: str
