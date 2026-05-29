from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from ai_company_api.db.session import get_session_dependency
from ai_company_api.schemas.api import (
    ConversationCreate,
    MessageCreate,
    ModelCredentialCreate,
    ModelCredentialRead,
    ModelProviderCreate,
    ModelProviderRead,
    PlannerRunCreate,
    PlannerRunDecisionRead,
    PlannerRunRead,
    PlannerRunReject,
    ProjectCreate,
    TaskCreate,
    TaskUpdate,
)
from ai_company_api.services.model_settings import (
    create_model_credential,
    create_model_provider,
    delete_model_credential,
    list_model_credentials,
    list_model_providers,
)
from ai_company_api.services.repository import (
    approve_planner_run,
    create_conversation,
    create_message,
    create_planner_run,
    create_project,
    create_task,
    get_planner_run_read,
    get_task,
    list_conversations,
    list_messages,
    list_projects,
    list_task_events,
    list_tasks,
    reject_planner_run,
    transition_task,
)
from ai_company_api.services.task_state import TaskStatus

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session_dependency)]


@router.get("/projects")
def get_projects(session: SessionDep):
    return list_projects(session)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def post_project(data: ProjectCreate, session: SessionDep):
    return create_project(session, data)


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


@router.get("/projects/{project_id}/tasks")
def get_project_tasks(project_id: str, session: SessionDep):
    return list_tasks(session, project_id)


@router.post("/projects/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
def post_project_task(project_id: str, data: TaskCreate, session: SessionDep):
    return create_task(session, project_id, data)


@router.get("/tasks/{task_id}")
def get_task_by_id(task_id: str, session: SessionDep):
    return get_task(session, task_id)


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
