from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, SecretStr

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


class GitHubCredentialStatus(str, Enum):
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


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    default_branch: str = Field(default="main", min_length=1)


class RepositoryRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    name: str
    local_path: str
    default_branch: str
    provider: str
    repo_url: str
    github_owner: str | None
    github_repo: str | None
    github_credential_id: str | None
    connection_status: str
    status: str
    created_at: datetime
    updated_at: datetime


class GitHubCredentialCreate(BaseModel):
    display_name: str = Field(min_length=1)
    token: SecretStr = Field(min_length=5)


class GitHubCredentialRead(BaseModel):
    id: str
    workspace_id: str
    display_name: str
    token_last4: str
    status: str
    created_at: datetime
    updated_at: datetime


class GitHubRepositoryCreate(BaseModel):
    name: str = Field(min_length=1)
    repo_url: str = Field(min_length=1)
    github_owner: str = Field(min_length=1)
    github_repo: str = Field(min_length=1)
    default_branch: str = Field(default="main", min_length=1)
    github_credential_id: str = Field(min_length=1)


class SandboxCommand(BaseModel):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    is_default: bool = False


class SandboxProfileCreate(BaseModel):
    repo_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    docker_image: str = Field(min_length=1)
    patch_commands: list[SandboxCommand] = Field(min_length=1)
    test_commands: list[SandboxCommand] = Field(default_factory=list)
    allowed_env_vars: list[str] = Field(default_factory=list)
    network_enabled: bool = True


class SandboxProfileRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    repo_id: str
    name: str
    docker_image: str
    patch_commands: list[SandboxCommand]
    test_commands: list[SandboxCommand]
    allowed_env_vars: list[str]
    network_enabled: bool
    status: str
    created_at: datetime
    updated_at: datetime


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
    model_route_id: str | None
    model_provider_name: str | None
    model_name: str | None
    fallback_reason: str | None
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
    allowed_paths: list[str] = Field(default_factory=list)
    required_tests: list[str] = Field(default_factory=list)
    assigned_agent_profile_id: str | None = None
    repo_id: str | None = None
    branch_name: str | None = None
    worktree_ref: str | None = None
    budget_limit: int | None = None


class TaskUpdate(BaseModel):
    status: TaskStatus


class LocalRunCreate(BaseModel):
    repo_id: str = Field(min_length=1)


class CloudRunCreate(BaseModel):
    repo_id: str = Field(min_length=1)
    sandbox_profile_id: str | None = Field(default=None, min_length=1)
    patch_command_key: str | None = Field(default=None, min_length=1)
    test_command_keys: list[str] = Field(default_factory=list)
    queue_provider: str = Field(default="local_db", min_length=1)
    runtime_provider: str | None = Field(default=None, min_length=1)
    storage_provider: str | None = Field(default=None, min_length=1)


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
    allowed_paths: list[str]
    required_tests: list[str]
    assigned_agent_profile_id: str | None
    repo_id: str | None
    branch_name: str | None
    worktree_ref: str | None
    budget_limit: int | None
    created_at: datetime
    updated_at: datetime


class LocalTaskRunRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    repo_id: str
    status: str
    runner_kind: str
    base_branch: str
    base_sha: str | None
    head_sha: str | None
    worktree_path: str | None
    patch_artifact_id: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class PatchArtifactRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    local_run_id: str
    summary: str
    files_changed: list[str]
    tests_run: list[str]
    test_result: str
    risks: list[str]
    diff_text: str
    created_at: datetime


class CommandResultRead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


class CloudRunRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    repo_id: str
    local_run_id: str | None
    sandbox_profile_id: str | None
    patch_command_key: str | None
    test_command_keys: list[str]
    command_results: list[CommandResultRead]
    base_branch: str
    head_branch: str
    status: str
    sandbox_kind: str
    patch_artifact_id: str | None
    failure_reason: str | None
    cancel_requested: bool
    cancel_requested_at: datetime | None
    cancelled_at: datetime | None
    worker_id: str | None
    claimed_at: datetime | None
    completed_at: datetime | None
    queue_provider: str
    remote_worker_kind: str | None
    lease_id: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    attempt_count: int
    max_attempts: int
    last_queue_error: str | None
    queue_message_id: str | None
    runtime_provider: str | None
    runtime_job_id: str | None
    storage_provider: str | None
    artifact_manifest_uri: str | None
    log_stream_uri: str | None
    external_status: str | None
    external_error: str | None
    created_at: datetime
    updated_at: datetime


class CloudRunLogEntryRead(BaseModel):
    id: str
    cloud_run_id: str
    level: str
    event: str
    message: str
    payload: dict[str, Any] | None
    created_at: datetime


class CloudRunResultRead(BaseModel):
    cloud_run: CloudRunRead
    patch_artifact: PatchArtifactRead | None = None


class CloudRunLeaseCreate(BaseModel):
    worker_id: str = Field(min_length=1)
    worker_kind: str = Field(default="remote_stub", min_length=1)
    lease_seconds: int = Field(default=60, ge=1, le=3600)


class CloudRunLeaseHeartbeat(BaseModel):
    worker_id: str = Field(min_length=1)
    lease_seconds: int = Field(default=60, ge=1, le=3600)


class CloudRunLeaseRequeueExpired(BaseModel):
    limit: int = Field(default=25, ge=1, le=100)


class CloudRunCommandResultCreate(BaseModel):
    command: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False


class CloudRunExecutionResultCreate(BaseModel):
    status: Literal["patch_ready", "failed"]
    runner_kind: str
    base_sha: str | None = None
    head_sha: str | None = None
    worktree_ref: str | None = None
    summary: str = ""
    files_changed: list[str] = Field(default_factory=list)
    tests_run: list[str] = Field(default_factory=list)
    test_result: str = "not_run"
    risks: list[str] = Field(default_factory=list)
    diff_text: str = ""
    command_results: list[CloudRunCommandResultCreate] = Field(default_factory=list)
    test_command_results: list[CloudRunCommandResultCreate] = Field(default_factory=list)
    failure_reason: str | None = None


class CloudRunLeaseComplete(BaseModel):
    worker_id: str = Field(min_length=1)
    result: CloudRunExecutionResultCreate


class CloudRunLeaseRead(BaseModel):
    cloud_run: CloudRunRead
    lease_id: str
    lease_expires_at: datetime
    heartbeat_at: datetime
    attempt_count: int
    cancel_requested: bool


class LocalTestRunRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    local_run_id: str
    patch_artifact_id: str | None
    status: str
    commands: list[str]
    command_results: list[CommandResultRead]
    failure_reason: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime


class PatchReviewRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    local_run_id: str
    patch_artifact_id: str
    test_run_id: str | None
    reviewer_kind: str
    verdict: str
    issues: list[dict[str, Any]]
    required_changes: list[str]
    created_at: datetime


class DebugAttemptRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    patch_artifact_id: str
    review_id: str | None
    test_run_id: str | None
    status: str
    root_cause: str
    fix_summary: str
    created_at: datetime


class PatchTestRunResultRead(BaseModel):
    task: TaskRead
    patch_artifact: PatchArtifactRead
    test_run: LocalTestRunRead
    debug_attempt: DebugAttemptRead | None = None


class PatchReviewResultRead(BaseModel):
    task: TaskRead
    patch_artifact: PatchArtifactRead
    review: PatchReviewRead
    debug_attempt: DebugAttemptRead | None = None


class PatchApprovalRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    local_run_id: str
    patch_artifact_id: str
    review_id: str
    status: str
    approved_by: str
    merge_instructions: str
    created_at: datetime


class PatchApprovalResultRead(BaseModel):
    task: TaskRead
    patch_artifact: PatchArtifactRead
    review: PatchReviewRead
    approval: PatchApprovalRead


class PullRequestRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    repo_id: str
    patch_artifact_id: str
    patch_approval_id: str
    cloud_run_id: str | None
    head_branch: str
    base_branch: str
    github_pr_number: int
    github_pr_url: str
    status: str
    created_by: str
    created_at: datetime


class PullRequestResultRead(BaseModel):
    task: TaskRead
    patch_artifact: PatchArtifactRead
    approval: PatchApprovalRead
    pull_request: PullRequestRead


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
    secret_value: SecretStr = Field(min_length=5)


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
