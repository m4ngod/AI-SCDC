from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from ai_company_api.db.session import get_session_dependency
from ai_company_api.schemas.api import (
    ConversationCreate,
    MessageCreate,
    PlannerRunCreate,
    PlannerRunReject,
    ProjectCreate,
    TaskCreate,
    TaskUpdate,
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


@router.post("/projects/{project_id}/planner-runs", status_code=status.HTTP_201_CREATED)
def post_project_planner_run(
    project_id: str,
    data: PlannerRunCreate,
    session: SessionDep,
):
    return create_planner_run(session, project_id, data)


@router.get("/planner-runs/{planner_run_id}")
def get_planner_run_by_id(planner_run_id: str, session: SessionDep):
    return get_planner_run_read(session, planner_run_id)


@router.post("/planner-runs/{planner_run_id}/approve")
def approve_planner_run_by_id(planner_run_id: str, session: SessionDep):
    return approve_planner_run(session, planner_run_id)


@router.post("/planner-runs/{planner_run_id}/reject")
def reject_planner_run_by_id(
    planner_run_id: str,
    data: PlannerRunReject,
    session: SessionDep,
):
    return reject_planner_run(session, planner_run_id, data.reason)


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
