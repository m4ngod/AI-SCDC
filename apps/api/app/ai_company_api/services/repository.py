import subprocess
from pathlib import Path
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
    Repository as ProjectRepository,
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
    RepositoryCreate,
    RepositoryRead,
    TaskCreate,
    TaskRead,
)
from ai_company_api.services.model_planner import (
    PlannerExecutionResult,
    create_model_planner_result,
)
from ai_company_api.services.planner import FakePlanner, PlannerService
from ai_company_api.services.task_state import (
    InvalidTaskTransition,
    TaskStatus,
    allowed_next_statuses,
    validate_transition,
)
from ai_company_llm_gateway.openai_compatible import OpenAICompatibleChatAdapter


MODEL_PLANNER_ADAPTER_FACTORY = OpenAICompatibleChatAdapter


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


def _repository_read(repository: ProjectRepository) -> RepositoryRead:
    return RepositoryRead(
        id=repository.id,
        workspace_id=repository.workspace_id,
        project_id=repository.project_id,
        name=repository.name,
        local_path=repository.local_path,
        default_branch=repository.default_branch,
        provider=repository.provider,
        repo_url=repository.repo_url,
        github_owner=repository.github_owner,
        github_repo=repository.github_repo,
        github_credential_id=repository.github_credential_id,
        connection_status=repository.connection_status,
        status=repository.status,
        created_at=repository.created_at,
        updated_at=repository.updated_at,
    )


def _validate_local_git_repository(local_path: str) -> Path:
    repo_path = Path(local_path).resolve()
    if not repo_path.exists():
        raise HTTPException(status_code=400, detail="Local path is not a git repository")

    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(
            status_code=400,
            detail="Local path is not a git repository",
        ) from exc

    if result.returncode != 0:
        raise HTTPException(status_code=400, detail="Local path is not a git repository")

    git_root = Path(result.stdout.strip()).resolve()
    if git_root != repo_path:
        raise HTTPException(status_code=400, detail="Local path is not a git repository")

    return repo_path


def create_repository(
    session: Session,
    project_id: str,
    data: RepositoryCreate,
) -> RepositoryRead:
    get_project(session, project_id)
    repo_path = _validate_local_git_repository(data.local_path)
    repository = ProjectRepository(
        project_id=project_id,
        name=data.name,
        local_path=str(repo_path),
        default_branch=data.default_branch,
    )
    session.add(repository)
    session.commit()
    session.refresh(repository)
    return _repository_read(repository)


def list_repositories(session: Session, project_id: str) -> list[RepositoryRead]:
    get_project(session, project_id)
    statement = (
        select(ProjectRepository)
        .where(ProjectRepository.project_id == project_id)
        .order_by(ProjectRepository.created_at, ProjectRepository.id)
    )
    return [_repository_read(repository) for repository in session.exec(statement).all()]


def get_repository(session: Session, repo_id: str) -> ProjectRepository:
    repository = session.get(ProjectRepository, repo_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repository


def get_repository_read(session: Session, repo_id: str) -> RepositoryRead:
    return _repository_read(get_repository(session, repo_id))


def delete_repository(session: Session, repo_id: str) -> RepositoryRead:
    repository = get_repository(session, repo_id)
    repository.status = "deleted"
    repository.connection_status = "inactive"
    repository.updated_at = utc_now()
    session.add(repository)
    session.commit()
    session.refresh(repository)
    return _repository_read(repository)


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
        model_route_id=planner_run.model_route_id,
        model_provider_name=planner_run.model_provider_name,
        model_name=planner_run.model_name,
        fallback_reason=planner_run.fallback_reason,
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


def _create_default_planner_result(
    session: Session,
    project: Project,
    goal: str,
    planner_run_id: str,
) -> PlannerExecutionResult:
    result = create_model_planner_result(
        session,
        project=project,
        goal=goal,
        planner_run_id=planner_run_id,
        adapter_factory=MODEL_PLANNER_ADAPTER_FACTORY,
    )
    if result.task_specs:
        return result
    fake_specs = FakePlanner().plan(project_id=project.id, goal=goal)
    return PlannerExecutionResult(
        task_specs=fake_specs,
        planner_kind=result.planner_kind,
        model_route_id=result.model_route_id,
        model_provider_name=result.model_provider_name,
        model_name=result.model_name,
        fallback_reason=result.fallback_reason,
    )


def create_planner_run(
    session: Session,
    project_id: str,
    data: PlannerRunCreate,
    planner: PlannerService | None = None,
) -> PlannerRunRead:
    project = get_project(session, project_id)

    if data.conversation_id is not None:
        conversation = get_conversation(session, data.conversation_id)
        if conversation.project_id != project_id:
            raise HTTPException(
                status_code=400,
                detail="Conversation does not belong to project",
            )

    planner_run = PlannerRun(
        project_id=project_id,
        conversation_id=data.conversation_id,
        goal=data.goal,
        status=PlannerRunStatus.DRAFTED,
        draft_count=0,
    )
    session.add(planner_run)

    if planner is not None:
        session.flush()
        task_specs = planner.plan(project_id=project_id, goal=data.goal)
        planner_run.planner_kind = "fake"
    else:
        planner_result = _create_default_planner_result(
            session,
            project=project,
            goal=data.goal,
            planner_run_id=planner_run.id,
        )
        task_specs = planner_result.task_specs
        planner_run.planner_kind = planner_result.planner_kind
        planner_run.model_route_id = planner_result.model_route_id
        planner_run.model_provider_name = planner_result.model_provider_name
        planner_run.model_name = planner_result.model_name
        planner_run.fallback_reason = planner_result.fallback_reason

    planner_run.draft_count = len(task_specs)
    session.add(planner_run)

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


def _task_read(task: Task) -> TaskRead:
    return TaskRead(
        id=task.id,
        project_id=task.project_id,
        conversation_id=task.conversation_id,
        parent_task_id=task.parent_task_id,
        title=task.title,
        description=task.description,
        role_required=task.role_required,
        status=TaskStatus(task.status),
        priority=task.priority,
        risk_level=task.risk_level,
        acceptance_criteria=task.acceptance_criteria,
        allowed_paths=task.allowed_paths,
        required_tests=task.required_tests,
        assigned_agent_profile_id=task.assigned_agent_profile_id,
        repo_id=task.repo_id,
        branch_name=task.branch_name,
        worktree_ref=task.worktree_ref,
        budget_limit=task.budget_limit,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def approve_planner_run(
    session: Session,
    planner_run_id: str,
) -> PlannerRunDecisionRead:
    planner_run = get_planner_run(session, planner_run_id)
    _ensure_planner_run_is_drafted(planner_run)
    project = get_project(session, planner_run.project_id)
    drafts = list_planner_task_drafts(session, planner_run.id)

    created_tasks: list[Task] = []
    try:
        approval = Approval(
            workspace_id=project.workspace_id,
            project_id=project.id,
            planner_run_id=planner_run.id,
            status=ApprovalStatus.APPROVED,
            decided_by="dev_user",
            decided_at=utc_now(),
        )
        session.add(approval)

        for draft in drafts:
            task = Task(
                project_id=project.id,
                conversation_id=planner_run.conversation_id,
                title=draft.title,
                description=draft.objective,
                role_required=draft.role_required,
                risk_level=draft.risk_level,
                acceptance_criteria=draft.acceptance_criteria,
                allowed_paths=draft.allowed_paths,
                required_tests=draft.required_tests,
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
        created_tasks=[_task_read(task) for task in created_tasks],
    )


def reject_planner_run(
    session: Session,
    planner_run_id: str,
    reason: str = "",
) -> PlannerRunDecisionRead:
    planner_run = get_planner_run(session, planner_run_id)
    _ensure_planner_run_is_drafted(planner_run)
    project = get_project(session, planner_run.project_id)

    try:
        approval = Approval(
            workspace_id=project.workspace_id,
            project_id=project.id,
            planner_run_id=planner_run.id,
            reason=reason,
            status=ApprovalStatus.REJECTED,
            decided_by="dev_user",
            decided_at=utc_now(),
        )
        session.add(approval)

        planner_run.status = PlannerRunStatus.REJECTED
        planner_run.updated_at = utc_now()
        session.add(planner_run)
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
        allowed_paths=data.allowed_paths,
        required_tests=data.required_tests,
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
