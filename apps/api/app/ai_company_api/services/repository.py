from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    Approval,
    ApprovalStatus,
    Conversation,
    Message,
    PlannerRun,
    PlannerRunStatus,
    PlannerTaskDraft,
    Project,
    Task,
    TaskEvent,
    utc_now,
)
from ai_company_api.schemas.api import (
    ConversationCreate,
    MessageCreate,
    PlannerRunCreate,
    PlannerRunDecisionRead,
    PlannerRunRead,
    PlannerTaskDraftRead,
    ProjectCreate,
    TaskCreate,
)
from ai_company_api.services.planner import FakePlanner, PlannerService
from ai_company_api.services.task_state import (
    InvalidTaskTransition,
    TaskStatus,
    allowed_next_statuses,
    validate_transition,
)


def create_project(session: Session, data: ProjectCreate) -> Project:
    project = Project(name=data.name, description=data.description)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def list_projects(session: Session) -> list[Project]:
    return list(session.exec(select(Project).order_by(Project.created_at)).all())


def get_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def create_conversation(
    session: Session,
    project_id: str,
    data: ConversationCreate,
) -> Conversation:
    get_project(session, project_id)
    conversation = Conversation(
        project_id=project_id,
        title=data.title,
        conversation_type=data.conversation_type,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def list_conversations(session: Session, project_id: str) -> list[Conversation]:
    get_project(session, project_id)
    statement = (
        select(Conversation)
        .where(Conversation.project_id == project_id)
        .order_by(Conversation.created_at)
    )
    return list(session.exec(statement).all())


def get_conversation(session: Session, conversation_id: str) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def create_message(
    session: Session,
    conversation_id: str,
    data: MessageCreate,
) -> Message:
    get_conversation(session, conversation_id)
    message = Message(
        conversation_id=conversation_id,
        sender_type=data.sender_type,
        content=data.content,
        structured_payload=data.structured_payload,
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


def list_messages(session: Session, conversation_id: str) -> list[Message]:
    get_conversation(session, conversation_id)
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return list(session.exec(statement).all())


def get_task(session: Session, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def create_task_event(
    session: Session,
    task_id: str,
    event_type: str,
    actor_type: str,
    actor_id: str,
    payload: dict[str, Any] | None = None,
) -> TaskEvent:
    event = TaskEvent(
        task_id=task_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        payload=payload or {},
    )
    session.add(event)
    return event


def _planner_draft_read(draft: PlannerTaskDraft) -> PlannerTaskDraftRead:
    return PlannerTaskDraftRead(
        id=draft.id,
        sequence=draft.sequence,
        title=draft.title,
        role_required=draft.role_required,
        objective=draft.objective,
        acceptance_criteria=draft.acceptance_criteria,
        allowed_paths=draft.allowed_paths,
        required_tests=draft.required_tests,
        risk_level=draft.risk_level,
    )


def list_planner_task_drafts(
    session: Session,
    planner_run_id: str,
) -> list[PlannerTaskDraft]:
    statement = (
        select(PlannerTaskDraft)
        .where(PlannerTaskDraft.planner_run_id == planner_run_id)
        .order_by(PlannerTaskDraft.sequence)
    )
    return list(session.exec(statement).all())


def _planner_run_read(session: Session, planner_run: PlannerRun) -> PlannerRunRead:
    status = planner_run.status
    if isinstance(status, PlannerRunStatus):
        status = status.value

    return PlannerRunRead(
        id=planner_run.id,
        project_id=planner_run.project_id,
        conversation_id=planner_run.conversation_id,
        goal=planner_run.goal,
        status=status,
        planner_kind=planner_run.planner_kind,
        draft_count=planner_run.draft_count,
        drafts=[
            _planner_draft_read(draft)
            for draft in list_planner_task_drafts(session, planner_run.id)
        ],
    )


def get_planner_run(session: Session, planner_run_id: str) -> PlannerRun:
    planner_run = session.get(PlannerRun, planner_run_id)
    if planner_run is None:
        raise HTTPException(status_code=404, detail="Planner run not found")
    return planner_run


def get_planner_run_read(session: Session, planner_run_id: str) -> PlannerRunRead:
    return _planner_run_read(session, get_planner_run(session, planner_run_id))


def create_planner_run(
    session: Session,
    project_id: str,
    data: PlannerRunCreate,
    planner: PlannerService | None = None,
) -> PlannerRunRead:
    get_project(session, project_id)

    if data.conversation_id is not None:
        conversation = get_conversation(session, data.conversation_id)
        if conversation.project_id != project_id:
            raise HTTPException(
                status_code=400,
                detail="Conversation does not belong to project",
            )

    planner_service = planner or FakePlanner()
    task_specs = planner_service.plan(project_id=project_id, goal=data.goal)
    planner_run = PlannerRun(
        project_id=project_id,
        conversation_id=data.conversation_id,
        goal=data.goal,
        status=PlannerRunStatus.DRAFTED,
        planner_kind="fake",
        draft_count=len(task_specs),
    )
    session.add(planner_run)
    session.flush()

    for sequence, task_spec in enumerate(task_specs, start=1):
        session.add(
            PlannerTaskDraft(
                planner_run_id=planner_run.id,
                sequence=sequence,
                title=task_spec.title,
                role_required=task_spec.role_required.value,
                objective=task_spec.objective,
                acceptance_criteria=list(task_spec.acceptance_criteria),
                allowed_paths=list(task_spec.allowed_paths),
                required_tests=list(task_spec.required_tests),
                risk_level=task_spec.risk_level.value,
            )
        )

    session.commit()
    session.refresh(planner_run)
    return _planner_run_read(session, planner_run)


def _ensure_planner_run_is_drafted(planner_run: PlannerRun) -> None:
    status = planner_run.status
    if isinstance(status, PlannerRunStatus):
        status = status.value

    if status != PlannerRunStatus.DRAFTED.value:
        raise HTTPException(
            status_code=400,
            detail="Planner run has already been decided",
        )


def _task_decision_read(task: Task) -> dict[str, Any]:
    return task.model_dump(mode="json")


def approve_planner_run(
    session: Session,
    planner_run_id: str,
) -> PlannerRunDecisionRead:
    planner_run = get_planner_run(session, planner_run_id)
    _ensure_planner_run_is_drafted(planner_run)
    project = get_project(session, planner_run.project_id)
    drafts = list_planner_task_drafts(session, planner_run.id)

    approval = Approval(
        workspace_id=project.workspace_id,
        project_id=project.id,
        planner_run_id=planner_run.id,
        status=ApprovalStatus.APPROVED,
        decided_by="dev_user",
        decided_at=utc_now(),
    )
    session.add(approval)

    created_tasks: list[Task] = []
    for draft in drafts:
        task = Task(
            project_id=project.id,
            conversation_id=planner_run.conversation_id,
            title=draft.title,
            description=draft.objective,
            role_required=draft.role_required,
            risk_level=draft.risk_level,
            acceptance_criteria=draft.acceptance_criteria,
        )
        session.add(task)
        session.flush()
        create_task_event(
            session,
            task.id,
            "task_created",
            "user",
            "dev_user",
            {
                "status": task.status.value,
                "planner_run_id": planner_run.id,
                "planner_task_draft_id": draft.id,
            },
        )
        created_tasks.append(task)

    planner_run.status = PlannerRunStatus.APPROVED
    planner_run.updated_at = utc_now()
    session.add(planner_run)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=400,
            detail="Planner run has already been decided",
        ) from exc

    session.refresh(approval)
    session.refresh(planner_run)
    for task in created_tasks:
        session.refresh(task)

    return PlannerRunDecisionRead(
        planner_run_id=planner_run.id,
        approval_id=approval.id,
        status="APPROVED",
        created_tasks=[_task_decision_read(task) for task in created_tasks],
    )


def reject_planner_run(
    session: Session,
    planner_run_id: str,
    reason: str = "",
) -> PlannerRunDecisionRead:
    planner_run = get_planner_run(session, planner_run_id)
    _ensure_planner_run_is_drafted(planner_run)
    project = get_project(session, planner_run.project_id)

    approval = Approval(
        workspace_id=project.workspace_id,
        project_id=project.id,
        planner_run_id=planner_run.id,
        action_type="reject_planner_run",
        reason=reason,
        status=ApprovalStatus.REJECTED,
        decided_by="dev_user",
        decided_at=utc_now(),
    )
    session.add(approval)

    planner_run.status = PlannerRunStatus.REJECTED
    planner_run.updated_at = utc_now()
    session.add(planner_run)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=400,
            detail="Planner run has already been decided",
        ) from exc

    session.refresh(approval)
    session.refresh(planner_run)

    return PlannerRunDecisionRead(
        planner_run_id=planner_run.id,
        approval_id=approval.id,
        status="REJECTED",
        created_tasks=[],
    )


def create_task(session: Session, project_id: str, data: TaskCreate) -> Task:
    get_project(session, project_id)

    if data.conversation_id is not None:
        conversation = get_conversation(session, data.conversation_id)
        if conversation.project_id != project_id:
            raise HTTPException(
                status_code=400,
                detail="Conversation does not belong to project",
            )

    if data.parent_task_id is not None:
        parent_task = get_task(session, data.parent_task_id)
        if parent_task.project_id != project_id:
            raise HTTPException(
                status_code=400,
                detail="Parent task does not belong to project",
            )

    task = Task(
        project_id=project_id,
        conversation_id=data.conversation_id,
        parent_task_id=data.parent_task_id,
        title=data.title,
        description=data.description,
        role_required=data.role_required.value,
        priority=data.priority,
        risk_level=data.risk_level.value,
        acceptance_criteria=data.acceptance_criteria,
        assigned_agent_profile_id=data.assigned_agent_profile_id,
        repo_id=data.repo_id,
        branch_name=data.branch_name,
        worktree_ref=data.worktree_ref,
        budget_limit=data.budget_limit,
    )
    session.add(task)
    session.flush()
    create_task_event(
        session,
        task.id,
        "task_created",
        "user",
        "dev_user",
        {"status": task.status.value},
    )
    session.commit()
    session.refresh(task)
    return task


def list_tasks(session: Session, project_id: str) -> list[Task]:
    get_project(session, project_id)
    statement = select(Task).where(Task.project_id == project_id).order_by(Task.created_at)
    return list(session.exec(statement).all())


def list_task_events(session: Session, task_id: str) -> list[TaskEvent]:
    get_task(session, task_id)
    statement = (
        select(TaskEvent)
        .where(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.created_at, TaskEvent.id)
    )
    return list(session.exec(statement).all())


def transition_task(
    session: Session,
    task_id: str,
    requested_status: TaskStatus,
    actor_type: str,
    actor_id: str,
) -> Task:
    task = get_task(session, task_id)
    current_status = TaskStatus(task.status)

    try:
        next_status = validate_transition(current_status, requested_status, actor_type)
    except InvalidTaskTransition as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "current_status": current_status.value,
                "requested_status": requested_status.value,
                "allowed_next_statuses": allowed_next_statuses(current_status),
            },
        ) from exc

    task.status = next_status
    task.updated_at = utc_now()
    create_task_event(
        session,
        task.id,
        "task_transitioned",
        actor_type,
        actor_id,
        {"from_status": current_status.value, "to_status": next_status.value},
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task
