from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, NonNegativeInt

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


class ModelRouteResolutionSource(str, Enum):
    CONFIGURED = "configured"
    FALLBACK_FAKE = "fallback_fake"


class UsageType(str, Enum):
    MODEL_TOKENS = "model_tokens"


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


class ModelProviderCreate(BaseModel):
    name: str = Field(min_length=1)
    provider_type: ModelProviderType
    base_url: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict)


class ModelProviderRead(BaseModel):
    id: str
    workspace_id: str
    name: str
    provider_type: str
    base_url: str | None
    default_headers: dict[str, str]
    status: str
    created_at: datetime
    updated_at: datetime


class ModelCredentialCreate(BaseModel):
    provider_id: str
    display_name: str = Field(min_length=1)
    secret_value: str = Field(min_length=5)


class ModelCredentialRead(BaseModel):
    id: str
    workspace_id: str
    provider_id: str
    display_name: str
    secret_last4: str
    status: str
    created_at: datetime
    updated_at: datetime


class ModelRouteCreate(BaseModel):
    agent_role: AgentRole
    provider_id: str
    credential_id: str | None = None
    model_name: str = Field(min_length=1)
    fallback_models: list[str] = Field(default_factory=list)


class ModelRouteUpdate(BaseModel):
    provider_id: str | None = None
    credential_id: str | None = None
    model_name: str | None = Field(default=None, min_length=1)
    fallback_models: list[str] | None = None
    status: ModelRouteStatus | None = None


class ModelRouteRead(BaseModel):
    id: str
    workspace_id: str
    agent_role: str
    provider_id: str
    credential_id: str | None
    model_name: str
    fallback_models: list[str]
    status: str
    created_at: datetime
    updated_at: datetime


class ResolvedModelRouteRead(BaseModel):
    agent_role: str
    provider_name: str
    provider_type: str
    model_name: str
    fallback_models: list[str]
    credential_required: bool
    credential_available: bool
    is_available: bool
    resolution_source: str
    route_id: str | None


class UsageLedgerCreate(BaseModel):
    project_id: str | None = None
    planner_run_id: str | None = None
    task_id: str | None = None
    usage_type: UsageType = UsageType.MODEL_TOKENS
    provider_name: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    prompt_tokens: NonNegativeInt = 0
    completion_tokens: NonNegativeInt = 0
    unit_price_cents: NonNegativeInt = 0
    amount_cents: NonNegativeInt = 0
    raw_usage_json: dict[str, Any] = Field(default_factory=dict)


class UsageLedgerRead(BaseModel):
    id: str
    workspace_id: str
    organization_id: str
    user_id: str
    project_id: str | None
    planner_run_id: str | None
    task_id: str | None
    usage_type: str
    provider_name: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    unit_price_cents: int
    amount_cents: int
    raw_usage_json: dict[str, Any]
    created_at: datetime


class DevIdentity(BaseModel):
    user_id: str
    workspace_id: str
    organization_id: str
