from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    PlannerRun,
    Project,
    Task,
    UsageLedgerEntry,
    UsageType,
)
from ai_company_api.schemas.api import UsageLedgerCreate, UsageLedgerRead


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _usage_read(entry: UsageLedgerEntry) -> UsageLedgerRead:
    return UsageLedgerRead(
        id=entry.id,
        workspace_id=entry.workspace_id,
        organization_id=entry.organization_id,
        user_id=entry.user_id,
        project_id=entry.project_id,
        planner_run_id=entry.planner_run_id,
        task_id=entry.task_id,
        usage_type=_enum_value(entry.usage_type),
        provider_name=entry.provider_name,
        model_name=entry.model_name,
        prompt_tokens=entry.prompt_tokens,
        completion_tokens=entry.completion_tokens,
        total_tokens=entry.total_tokens,
        unit_price_cents=entry.unit_price_cents,
        amount_cents=entry.amount_cents,
        raw_usage_json=entry.raw_usage_json,
        created_at=entry.created_at,
    )


def _validate_project(session: Session, project_id: str | None) -> Project | None:
    if project_id is None:
        return None
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _validate_task(
    session: Session,
    task_id: str | None,
    project_id: str | None,
) -> Task | None:
    if task_id is None:
        return None
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if project_id is not None and task.project_id != project_id:
        raise HTTPException(status_code=400, detail="Task does not belong to project")
    return task


def _validate_planner_run(
    session: Session,
    planner_run_id: str | None,
    project_id: str | None,
) -> PlannerRun | None:
    if planner_run_id is None:
        return None
    planner_run = session.get(PlannerRun, planner_run_id)
    if planner_run is None:
        raise HTTPException(status_code=404, detail="Planner run not found")
    if project_id is not None and planner_run.project_id != project_id:
        raise HTTPException(
            status_code=400,
            detail="Planner run does not belong to project",
        )
    return planner_run


def append_usage_ledger_entry(
    session: Session,
    data: UsageLedgerCreate,
) -> UsageLedgerRead:
    _validate_project(session, data.project_id)
    task = _validate_task(session, data.task_id, data.project_id)
    project_id = data.project_id
    if project_id is None and task is not None:
        project_id = task.project_id

    planner_run = _validate_planner_run(session, data.planner_run_id, project_id)
    if project_id is None and planner_run is not None:
        project_id = planner_run.project_id

    entry = UsageLedgerEntry(
        project_id=project_id,
        task_id=data.task_id,
        planner_run_id=data.planner_run_id,
        usage_type=UsageType(data.usage_type.value),
        provider_name=data.provider_name,
        model_name=data.model_name,
        prompt_tokens=data.prompt_tokens,
        completion_tokens=data.completion_tokens,
        total_tokens=data.prompt_tokens + data.completion_tokens,
        unit_price_cents=data.unit_price_cents,
        amount_cents=data.amount_cents,
        raw_usage_json=data.raw_usage_json,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return _usage_read(entry)


def list_usage_ledger_entries(
    session: Session,
    project_id: str | None = None,
    planner_run_id: str | None = None,
    task_id: str | None = None,
) -> list[UsageLedgerRead]:
    statement = select(UsageLedgerEntry)
    if project_id is not None:
        statement = statement.where(UsageLedgerEntry.project_id == project_id)
    if planner_run_id is not None:
        statement = statement.where(UsageLedgerEntry.planner_run_id == planner_run_id)
    if task_id is not None:
        statement = statement.where(UsageLedgerEntry.task_id == task_id)
    statement = statement.order_by(UsageLedgerEntry.created_at, UsageLedgerEntry.id)
    return [_usage_read(entry) for entry in session.exec(statement).all()]
