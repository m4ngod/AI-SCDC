from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session

from ai_company_api.db.session import get_session_dependency
from ai_company_api.schemas.api import (
    AgentRole,
    CloudRunCreate,
    CloudRunLeaseCreate,
    CloudRunLeaseHeartbeat,
    CloudRunLeaseRead,
    CloudRunLogEntryRead,
    CloudRunRead,
    CloudRunResultRead,
    ConversationCreate,
    DebugAttemptRead,
    GitHubCredentialCreate,
    GitHubCredentialRead,
    GitHubRepositoryCreate,
    LocalRunCreate,
    LocalTaskRunRead,
    LocalTestRunRead,
    MessageCreate,
    ModelCredentialCreate,
    ModelCredentialRead,
    ModelProviderCreate,
    ModelProviderRead,
    ModelRouteCreate,
    ModelRouteRead,
    ModelRouteUpdate,
    PlannerRunCreate,
    PlannerRunDecisionRead,
    PlannerRunRead,
    PlannerRunReject,
    PatchApprovalRead,
    PatchApprovalResultRead,
    PatchArtifactRead,
    PatchReviewRead,
    PatchReviewResultRead,
    PatchTestRunResultRead,
    ProjectCreate,
    PullRequestRead,
    PullRequestResultRead,
    RepositoryCreate,
    RepositoryRead,
    ResolvedModelRouteRead,
    SandboxProfileCreate,
    SandboxProfileRead,
    TaskCreate,
    TaskUpdate,
    UsageLedgerCreate,
    UsageLedgerRead,
)
from ai_company_api.services.cloud_runner import (
    cancel_cloud_run,
    claim_next_cloud_run_lease,
    get_cloud_run_read,
    heartbeat_cloud_run_lease,
    list_cloud_run_logs,
    list_cloud_runs,
    process_cloud_run,
    process_next_cloud_run,
    start_cloud_run,
)
from ai_company_api.services.github_repository import (
    create_github_credential,
    create_github_repository,
    delete_github_credential,
    list_github_credentials,
)
from ai_company_api.services.github_pull_request import (
    create_pull_request_for_approval,
    get_pull_request,
    list_pull_requests_for_patch_artifact,
)
from ai_company_api.services.local_runner import (
    get_local_task_run,
    get_patch_artifact,
    list_local_task_runs,
    start_local_task_run,
)
from ai_company_api.services.patch_approval import (
    approve_patch_artifact,
    get_patch_approval,
    list_patch_approvals,
    request_human_approval,
)
from ai_company_api.services.test_review_debug import (
    get_patch_review,
    get_test_run,
    list_debug_attempts,
    list_patch_reviews,
    list_patch_test_runs,
    start_patch_review,
    start_patch_test_run,
)
from ai_company_api.services.model_settings import (
    create_model_credential,
    create_model_provider,
    create_model_route,
    delete_model_credential,
    list_model_credentials,
    list_model_providers,
    list_model_routes,
    resolve_model_route,
    update_model_route,
)
from ai_company_api.services.repository import (
    approve_planner_run,
    create_conversation,
    create_message,
    create_planner_run,
    create_project,
    create_repository,
    create_task,
    delete_repository,
    get_planner_run_read,
    get_repository_read,
    get_task,
    list_repositories,
    list_conversations,
    list_messages,
    list_projects,
    list_task_events,
    list_tasks,
    reject_planner_run,
    transition_task,
)
from ai_company_api.services.sandbox_profiles import (
    create_sandbox_profile,
    get_sandbox_profile_read,
    list_sandbox_profiles,
)
from ai_company_api.services.task_state import TaskStatus
from ai_company_api.services.usage_ledger import (
    append_usage_ledger_entry,
    list_usage_ledger_entries,
)

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session_dependency)]


@router.get("/projects")
def get_projects(session: SessionDep):
    return list_projects(session)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def post_project(data: ProjectCreate, session: SessionDep):
    return create_project(session, data)


@router.get(
    "/projects/{project_id}/repositories",
    response_model=list[RepositoryRead],
)
def get_project_repositories(
    project_id: str,
    session: SessionDep,
) -> list[RepositoryRead]:
    return list_repositories(session, project_id)


@router.post(
    "/projects/{project_id}/repositories",
    status_code=status.HTTP_201_CREATED,
    response_model=RepositoryRead,
)
def post_project_repository(
    project_id: str,
    data: RepositoryCreate,
    session: SessionDep,
) -> RepositoryRead:
    return create_repository(session, project_id, data)


@router.get("/repositories/{repo_id}", response_model=RepositoryRead)
def get_repository_by_id(repo_id: str, session: SessionDep) -> RepositoryRead:
    return get_repository_read(session, repo_id)


@router.delete("/repositories/{repo_id}", response_model=RepositoryRead)
def delete_repository_by_id(repo_id: str, session: SessionDep) -> RepositoryRead:
    return delete_repository(session, repo_id)


@router.get("/github-credentials", response_model=list[GitHubCredentialRead])
def get_github_credentials(session: SessionDep) -> list[GitHubCredentialRead]:
    return list_github_credentials(session)


@router.post(
    "/github-credentials",
    status_code=status.HTTP_201_CREATED,
    response_model=GitHubCredentialRead,
)
def post_github_credential(
    data: GitHubCredentialCreate,
    session: SessionDep,
) -> GitHubCredentialRead:
    return create_github_credential(session, data)


@router.delete(
    "/github-credentials/{credential_id}",
    response_model=GitHubCredentialRead,
)
def delete_github_credential_by_id(
    credential_id: str,
    session: SessionDep,
) -> GitHubCredentialRead:
    return delete_github_credential(session, credential_id)


@router.post(
    "/projects/{project_id}/github-repositories",
    status_code=status.HTTP_201_CREATED,
    response_model=RepositoryRead,
)
def post_project_github_repository(
    project_id: str,
    data: GitHubRepositoryCreate,
    session: SessionDep,
) -> RepositoryRead:
    return create_github_repository(session, project_id, data)


@router.post(
    "/projects/{project_id}/sandbox-profiles",
    status_code=status.HTTP_201_CREATED,
    response_model=SandboxProfileRead,
)
def post_project_sandbox_profile(
    project_id: str,
    data: SandboxProfileCreate,
    session: SessionDep,
) -> SandboxProfileRead:
    return create_sandbox_profile(session, project_id, data)


@router.get(
    "/projects/{project_id}/sandbox-profiles",
    response_model=list[SandboxProfileRead],
)
def get_project_sandbox_profiles(
    project_id: str,
    session: SessionDep,
) -> list[SandboxProfileRead]:
    return list_sandbox_profiles(session, project_id)


@router.get(
    "/sandbox-profiles/{sandbox_profile_id}",
    response_model=SandboxProfileRead,
)
def get_sandbox_profile_by_id(
    sandbox_profile_id: str,
    session: SessionDep,
) -> SandboxProfileRead:
    return get_sandbox_profile_read(session, sandbox_profile_id)


@router.get("/projects/{project_id}/conversations")
def get_project_conversations(project_id: str, session: SessionDep):
    return list_conversations(session, project_id)


@router.post(
    "/projects/{project_id}/conversations",
    status_code=status.HTTP_201_CREATED,
)
def post_project_conversation(
    project_id: str,
    data: ConversationCreate,
    session: SessionDep,
):
    return create_conversation(session, project_id, data)


@router.get("/conversations/{conversation_id}/messages")
def get_conversation_messages(conversation_id: str, session: SessionDep):
    return list_messages(session, conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
)
def post_conversation_message(
    conversation_id: str,
    data: MessageCreate,
    session: SessionDep,
):
    return create_message(session, conversation_id, data)


@router.post(
    "/projects/{project_id}/planner-runs",
    status_code=status.HTTP_201_CREATED,
    response_model=PlannerRunRead,
)
def post_project_planner_run(
    project_id: str,
    data: PlannerRunCreate,
    session: SessionDep,
) -> PlannerRunRead:
    return create_planner_run(session, project_id, data)


@router.get(
    "/planner-runs/{planner_run_id}",
    response_model=PlannerRunRead,
)
def get_planner_run_by_id(planner_run_id: str, session: SessionDep) -> PlannerRunRead:
    return get_planner_run_read(session, planner_run_id)


@router.post(
    "/planner-runs/{planner_run_id}/approve",
    response_model=PlannerRunDecisionRead,
)
def approve_planner_run_by_id(
    planner_run_id: str,
    session: SessionDep,
) -> PlannerRunDecisionRead:
    return approve_planner_run(session, planner_run_id)


@router.post(
    "/planner-runs/{planner_run_id}/reject",
    response_model=PlannerRunDecisionRead,
)
def reject_planner_run_by_id(
    planner_run_id: str,
    data: PlannerRunReject,
    session: SessionDep,
) -> PlannerRunDecisionRead:
    return reject_planner_run(session, planner_run_id, data.reason)


@router.get("/model-providers", response_model=list[ModelProviderRead])
def get_model_providers(session: SessionDep) -> list[ModelProviderRead]:
    return list_model_providers(session)


@router.post(
    "/model-providers",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelProviderRead,
)
def post_model_provider(
    data: ModelProviderCreate,
    session: SessionDep,
) -> ModelProviderRead:
    return create_model_provider(session, data)


@router.get("/model-credentials", response_model=list[ModelCredentialRead])
def get_model_credentials(session: SessionDep) -> list[ModelCredentialRead]:
    return list_model_credentials(session)


@router.post(
    "/model-credentials",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelCredentialRead,
)
def post_model_credential(
    data: ModelCredentialCreate,
    session: SessionDep,
) -> ModelCredentialRead:
    return create_model_credential(session, data)


@router.delete(
    "/model-credentials/{credential_id}",
    response_model=ModelCredentialRead,
)
def delete_model_credential_by_id(
    credential_id: str,
    session: SessionDep,
) -> ModelCredentialRead:
    return delete_model_credential(session, credential_id)


@router.get("/model-routes", response_model=list[ModelRouteRead])
def get_model_routes(session: SessionDep) -> list[ModelRouteRead]:
    return list_model_routes(session)


@router.post(
    "/model-routes",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelRouteRead,
)
def post_model_route(
    data: ModelRouteCreate,
    session: SessionDep,
) -> ModelRouteRead:
    return create_model_route(session, data)


@router.patch("/model-routes/{route_id}", response_model=ModelRouteRead)
def patch_model_route(
    route_id: str,
    data: ModelRouteUpdate,
    session: SessionDep,
) -> ModelRouteRead:
    return update_model_route(session, route_id, data)


@router.get("/model-routes/resolve", response_model=ResolvedModelRouteRead)
def resolve_model_route_for_role(
    agent_role: AgentRole,
    session: SessionDep,
) -> ResolvedModelRouteRead:
    return resolve_model_route(session, agent_role)


@router.get("/usage-ledger", response_model=list[UsageLedgerRead])
def get_usage_ledger(
    session: SessionDep,
    project_id: str | None = None,
    planner_run_id: str | None = None,
    task_id: str | None = None,
) -> list[UsageLedgerRead]:
    return list_usage_ledger_entries(
        session,
        project_id=project_id,
        planner_run_id=planner_run_id,
        task_id=task_id,
    )


@router.post(
    "/usage-ledger",
    status_code=status.HTTP_201_CREATED,
    response_model=UsageLedgerRead,
)
def post_usage_ledger_entry(
    data: UsageLedgerCreate,
    session: SessionDep,
) -> UsageLedgerRead:
    return append_usage_ledger_entry(session, data)


@router.get("/projects/{project_id}/tasks")
def get_project_tasks(project_id: str, session: SessionDep):
    return list_tasks(session, project_id)


@router.post("/projects/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
def post_project_task(project_id: str, data: TaskCreate, session: SessionDep):
    return create_task(session, project_id, data)


@router.get("/tasks/{task_id}")
def get_task_by_id(task_id: str, session: SessionDep):
    return get_task(session, task_id)


@router.post(
    "/tasks/{task_id}/cloud-runs",
    status_code=status.HTTP_201_CREATED,
    response_model=CloudRunResultRead,
)
def post_task_cloud_run(
    task_id: str,
    data: CloudRunCreate,
    session: SessionDep,
) -> CloudRunResultRead:
    return start_cloud_run(session, task_id, data)


@router.get(
    "/tasks/{task_id}/cloud-runs",
    response_model=list[CloudRunRead],
)
def get_task_cloud_runs(
    task_id: str,
    session: SessionDep,
) -> list[CloudRunRead]:
    return list_cloud_runs(session, task_id)


@router.get("/cloud-runs/{cloud_run_id}", response_model=CloudRunRead)
def get_cloud_run_by_id(
    cloud_run_id: str,
    session: SessionDep,
) -> CloudRunRead:
    return get_cloud_run_read(session, cloud_run_id)


@router.post(
    "/cloud-run-worker/leases",
    status_code=status.HTTP_201_CREATED,
    response_model=CloudRunLeaseRead,
)
def post_cloud_run_worker_lease(
    data: CloudRunLeaseCreate,
    session: SessionDep,
) -> CloudRunLeaseRead | Response:
    lease = claim_next_cloud_run_lease(
        session,
        worker_id=data.worker_id,
        worker_kind=data.worker_kind,
        lease_seconds=data.lease_seconds,
    )
    if lease is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return lease


@router.post(
    "/cloud-run-worker/leases/{lease_id}/heartbeat",
    response_model=CloudRunLeaseRead,
)
def post_cloud_run_worker_lease_heartbeat(
    lease_id: str,
    data: CloudRunLeaseHeartbeat,
    session: SessionDep,
) -> CloudRunLeaseRead:
    return heartbeat_cloud_run_lease(
        session,
        lease_id=lease_id,
        worker_id=data.worker_id,
        lease_seconds=data.lease_seconds,
    )


@router.post(
    "/cloud-run-worker/process-next",
    response_model=CloudRunResultRead,
    responses={status.HTTP_204_NO_CONTENT: {"description": "No queued cloud runs"}},
)
def post_cloud_run_worker_process_next(
    session: SessionDep,
    worker_id: str = "local-worker",
) -> CloudRunResultRead | Response:
    result = process_next_cloud_run(session, worker_id=worker_id)
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


@router.post("/cloud-runs/{cloud_run_id}/process", response_model=CloudRunResultRead)
def post_cloud_run_process(
    cloud_run_id: str,
    session: SessionDep,
    worker_id: str = "local-worker",
) -> CloudRunResultRead:
    return process_cloud_run(session, cloud_run_id=cloud_run_id, worker_id=worker_id)


@router.post("/cloud-runs/{cloud_run_id}/cancel", response_model=CloudRunRead)
def post_cloud_run_cancel(
    cloud_run_id: str,
    session: SessionDep,
) -> CloudRunRead:
    return cancel_cloud_run(session, cloud_run_id=cloud_run_id)


@router.get("/cloud-runs/{cloud_run_id}/logs", response_model=list[CloudRunLogEntryRead])
def get_cloud_run_logs(
    cloud_run_id: str,
    session: SessionDep,
) -> list[CloudRunLogEntryRead]:
    return list_cloud_run_logs(session, cloud_run_id=cloud_run_id)


@router.post(
    "/tasks/{task_id}/local-runs",
    status_code=status.HTTP_201_CREATED,
    response_model=LocalTaskRunRead,
)
def post_task_local_run(
    task_id: str,
    data: LocalRunCreate,
    session: SessionDep,
) -> LocalTaskRunRead:
    return start_local_task_run(session, task_id, data)


@router.get(
    "/tasks/{task_id}/local-runs",
    response_model=list[LocalTaskRunRead],
)
def get_task_local_runs(
    task_id: str,
    session: SessionDep,
) -> list[LocalTaskRunRead]:
    return list_local_task_runs(session, task_id)


@router.get("/local-runs/{local_run_id}", response_model=LocalTaskRunRead)
def get_local_run_by_id(
    local_run_id: str,
    session: SessionDep,
) -> LocalTaskRunRead:
    return get_local_task_run(session, local_run_id)


@router.get("/patch-artifacts/{patch_artifact_id}", response_model=PatchArtifactRead)
def get_patch_artifact_by_id(
    patch_artifact_id: str,
    session: SessionDep,
) -> PatchArtifactRead:
    return get_patch_artifact(session, patch_artifact_id)


@router.get(
    "/patch-artifacts/{patch_artifact_id}/pull-requests",
    response_model=list[PullRequestRead],
)
def get_patch_artifact_pull_requests(
    patch_artifact_id: str,
    session: SessionDep,
) -> list[PullRequestRead]:
    return list_pull_requests_for_patch_artifact(session, patch_artifact_id)


@router.post(
    "/patch-artifacts/{patch_artifact_id}/test-runs",
    status_code=status.HTTP_201_CREATED,
    response_model=PatchTestRunResultRead,
)
def post_patch_artifact_test_run(
    patch_artifact_id: str,
    session: SessionDep,
) -> PatchTestRunResultRead:
    return start_patch_test_run(session, patch_artifact_id)


@router.get(
    "/patch-artifacts/{patch_artifact_id}/test-runs",
    response_model=list[LocalTestRunRead],
)
def get_patch_artifact_test_runs(
    patch_artifact_id: str,
    session: SessionDep,
) -> list[LocalTestRunRead]:
    return list_patch_test_runs(session, patch_artifact_id)


@router.post(
    "/patch-artifacts/{patch_artifact_id}/reviews",
    status_code=status.HTTP_201_CREATED,
    response_model=PatchReviewResultRead,
)
def post_patch_artifact_review(
    patch_artifact_id: str,
    session: SessionDep,
) -> PatchReviewResultRead:
    return start_patch_review(session, patch_artifact_id)


@router.get(
    "/patch-artifacts/{patch_artifact_id}/reviews",
    response_model=list[PatchReviewRead],
)
def get_patch_artifact_reviews(
    patch_artifact_id: str,
    session: SessionDep,
) -> list[PatchReviewRead]:
    return list_patch_reviews(session, patch_artifact_id)


@router.get("/patch-reviews/{review_id}", response_model=PatchReviewRead)
def get_patch_review_by_id(
    review_id: str,
    session: SessionDep,
) -> PatchReviewRead:
    return get_patch_review(session, review_id)


@router.post(
    "/patch-artifacts/{patch_artifact_id}/approvals",
    response_model=PatchApprovalResultRead,
)
def post_patch_artifact_approval(
    patch_artifact_id: str,
    response: Response,
    session: SessionDep,
) -> PatchApprovalResultRead:
    result, status_code = approve_patch_artifact(session, patch_artifact_id)
    response.status_code = status_code
    return result


@router.get(
    "/patch-artifacts/{patch_artifact_id}/approvals",
    response_model=list[PatchApprovalRead],
)
def get_patch_artifact_approvals(
    patch_artifact_id: str,
    session: SessionDep,
) -> list[PatchApprovalRead]:
    return list_patch_approvals(session, patch_artifact_id)


@router.get("/patch-approvals/{approval_id}", response_model=PatchApprovalRead)
def get_patch_approval_by_id(
    approval_id: str,
    session: SessionDep,
) -> PatchApprovalRead:
    return get_patch_approval(session, approval_id)


@router.post(
    "/patch-approvals/{approval_id}/pull-requests",
    response_model=PullRequestResultRead,
)
def post_patch_approval_pull_request(
    approval_id: str,
    response: Response,
    session: SessionDep,
) -> PullRequestResultRead:
    result, status_code = create_pull_request_for_approval(session, approval_id)
    response.status_code = status_code
    return result


@router.post(
    "/patch-approvals/{approval_id}/request-human-approval",
    response_model=PatchApprovalResultRead,
)
def post_patch_approval_human_approval_request(
    approval_id: str,
    session: SessionDep,
) -> PatchApprovalResultRead:
    return request_human_approval(session, approval_id)


@router.get("/pull-requests/{pull_request_id}", response_model=PullRequestRead)
def get_pull_request_by_id(
    pull_request_id: str,
    session: SessionDep,
) -> PullRequestRead:
    return get_pull_request(session, pull_request_id)


@router.get("/test-runs/{test_run_id}", response_model=LocalTestRunRead)
def get_test_run_by_id(
    test_run_id: str,
    session: SessionDep,
) -> LocalTestRunRead:
    return get_test_run(session, test_run_id)


@router.get("/tasks/{task_id}/debug-attempts", response_model=list[DebugAttemptRead])
def get_task_debug_attempts(
    task_id: str,
    session: SessionDep,
) -> list[DebugAttemptRead]:
    return list_debug_attempts(session, task_id)


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, data: TaskUpdate, session: SessionDep):
    return transition_task(
        session,
        task_id,
        data.status,
        actor_type="user",
        actor_id="dev_user",
    )


@router.post("/tasks/{task_id}/run")
def run_task(task_id: str, session: SessionDep):
    return transition_task(
        session,
        task_id,
        TaskStatus.ASSIGNED,
        actor_type="system",
        actor_id="dev_system",
    )


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, session: SessionDep):
    return transition_task(
        session,
        task_id,
        TaskStatus.CANCELLED,
        actor_type="user",
        actor_id="dev_user",
    )


@router.get("/tasks/{task_id}/events")
def get_task_events(task_id: str, session: SessionDep):
    return list_task_events(session, task_id)
