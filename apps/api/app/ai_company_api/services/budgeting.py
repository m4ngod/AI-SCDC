from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    BudgetReservation,
    BudgetReservationStatus,
    CloudRun,
    CloudRunStoredObject,
    CreditWallet,
    Project,
    SpendLimit,
    Task,
    UsageLedgerEntry,
    UsageType,
    utc_now,
)
from ai_company_api.schemas.api import (
    BudgetReservationRead,
    CloudRunCostSummaryRead,
    CreditWalletRead,
    ManualCreditGrantCreate,
    SpendLimitRead,
    SpendLimitUpdate,
    UsageLedgerCostSummaryRead,
    UsageSummaryItemRead,
    UsageSummaryRead,
)
from ai_company_api.services.auth_context import (
    current_organization_id,
    current_workspace_id,
    enforce_workspace_access,
)
from ai_company_api.services.workspace_audit import (
    record_workspace_audit,
    require_audited_workspace_permission_if_authenticated,
)


DEFAULT_CLOUD_RUN_RESERVATION_CENTS = 100
WORKER_SUBMISSION_CENTS = 25
RUNTIME_SECOND_CENTS = 1
QUEUE_MESSAGE_CENTS = 1
OBJECT_STORAGE_BYTE_CENTS = 0
LOG_SYNC_CALL_CENTS = 1


def _wallet_read(wallet: CreditWallet) -> CreditWalletRead:
    return CreditWalletRead(
        id=wallet.id,
        workspace_id=wallet.workspace_id,
        organization_id=wallet.organization_id,
        balance_cents=wallet.balance_cents,
        created_at=wallet.created_at,
        updated_at=wallet.updated_at,
    )


def _spend_limit_read(spend_limit: SpendLimit) -> SpendLimitRead:
    return SpendLimitRead(
        id=spend_limit.id,
        workspace_id=spend_limit.workspace_id,
        monthly_limit_cents=spend_limit.monthly_limit_cents,
        per_run_limit_cents=spend_limit.per_run_limit_cents,
        status=spend_limit.status,
        created_at=spend_limit.created_at,
        updated_at=spend_limit.updated_at,
    )


def _reservation_read(
    reservation: BudgetReservation,
) -> BudgetReservationRead:
    status = (
        reservation.status.value
        if hasattr(reservation.status, "value")
        else str(reservation.status)
    )
    return BudgetReservationRead(
        id=reservation.id,
        workspace_id=reservation.workspace_id,
        organization_id=reservation.organization_id,
        project_id=reservation.project_id,
        task_id=reservation.task_id,
        cloud_run_id=reservation.cloud_run_id,
        reserved_cents=reservation.reserved_cents,
        settled_cents=reservation.settled_cents,
        status=status,
        created_at=reservation.created_at,
        updated_at=reservation.updated_at,
        settled_at=reservation.settled_at,
    )


def _usage_entry_read(entry: UsageLedgerEntry) -> UsageLedgerCostSummaryRead:
    usage_type = entry.usage_type.value if hasattr(entry.usage_type, "value") else str(entry.usage_type)

    return UsageLedgerCostSummaryRead(
        id=entry.id,
        workspace_id=entry.workspace_id,
        organization_id=entry.organization_id,
        user_id=entry.user_id,
        project_id=entry.project_id,
        planner_run_id=entry.planner_run_id,
        task_id=entry.task_id,
        cloud_run_id=entry.cloud_run_id,
        usage_type=usage_type,
        provider_name=entry.provider_name,
        model_name=entry.model_name,
        prompt_tokens=entry.prompt_tokens,
        completion_tokens=entry.completion_tokens,
        total_tokens=entry.total_tokens,
        quantity=entry.quantity,
        unit_name=entry.unit_name,
        unit_price_cents=entry.unit_price_cents,
        amount_cents=entry.amount_cents,
        created_at=entry.created_at,
    )


def _get_or_create_wallet(session: Session) -> CreditWallet:
    return _get_or_create_wallet_for_scope(
        session,
        workspace_id=current_workspace_id(),
        organization_id=current_organization_id(),
    )


def _get_or_create_wallet_for_scope(
    session: Session,
    *,
    workspace_id: str,
    organization_id: str,
) -> CreditWallet:
    wallet = session.exec(
        select(CreditWallet).where(CreditWallet.workspace_id == workspace_id)
    ).first()
    if wallet is not None:
        return wallet
    wallet = CreditWallet(
        workspace_id=workspace_id,
        organization_id=organization_id,
    )
    session.add(wallet)
    session.flush()
    return wallet


def _get_or_create_spend_limit(session: Session) -> SpendLimit:
    workspace_id = current_workspace_id()
    spend_limit = session.exec(
        select(SpendLimit).where(SpendLimit.workspace_id == workspace_id)
    ).first()
    if spend_limit is not None:
        return spend_limit
    spend_limit = SpendLimit(workspace_id=workspace_id)
    session.add(spend_limit)
    session.flush()
    return spend_limit


def _active_wallet_or_402(session: Session) -> CreditWallet:
    workspace_id = current_workspace_id()
    wallet = session.exec(
        select(CreditWallet).where(CreditWallet.workspace_id == workspace_id)
    ).first()
    if wallet is None or wallet.balance_cents <= 0:
        raise HTTPException(status_code=402, detail="Insufficient credits")
    return wallet


def _ensure_spend_limit_allows(session: Session, estimate_cents: int) -> None:
    workspace_id = current_workspace_id()
    spend_limit = session.exec(
        select(SpendLimit).where(
            SpendLimit.workspace_id == workspace_id,
            SpendLimit.status == "active",
        )
    ).first()
    if (
        spend_limit is not None
        and spend_limit.per_run_limit_cents > 0
        and estimate_cents > spend_limit.per_run_limit_cents
    ):
        raise HTTPException(status_code=402, detail="Spend limit exceeded")
    if (
        spend_limit is not None
        and spend_limit.monthly_limit_cents > 0
        and _current_month_reserved_and_settled_cents(session, workspace_id)
        + estimate_cents
        > spend_limit.monthly_limit_cents
    ):
        raise HTTPException(status_code=402, detail="Spend limit exceeded")


def _current_month_reserved_and_settled_cents(
    session: Session,
    workspace_id: str,
) -> int:
    now = utc_now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        next_month_start = month_start.replace(
            year=month_start.year + 1,
            month=1,
        )
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)

    active_reservations = session.exec(
        select(BudgetReservation).where(
            BudgetReservation.workspace_id == workspace_id,
            BudgetReservation.status == BudgetReservationStatus.RESERVED,
            BudgetReservation.created_at >= month_start,
            BudgetReservation.created_at < next_month_start,
        )
    ).all()
    settled_reservations = session.exec(
        select(BudgetReservation).where(
            BudgetReservation.workspace_id == workspace_id,
            BudgetReservation.status == BudgetReservationStatus.SETTLED,
            BudgetReservation.settled_at >= month_start,
            BudgetReservation.settled_at < next_month_start,
        )
    ).all()
    return sum(reservation.reserved_cents for reservation in active_reservations) + sum(
        reservation.settled_cents for reservation in settled_reservations
    )


def grant_manual_credit(
    session: Session,
    data: ManualCreditGrantCreate,
    *,
    commit: bool = True,
) -> CreditWalletRead:
    wallet = _get_or_create_wallet(session)
    wallet.balance_cents += data.amount_cents
    wallet.updated_at = utc_now()
    session.add(wallet)
    if commit:
        session.commit()
        session.refresh(wallet)
    else:
        session.flush()
        session.refresh(wallet)
    return _wallet_read(wallet)


def reserve_cloud_run_budget(
    session: Session,
    *,
    project_id: str,
    task_id: str,
) -> BudgetReservation:
    estimate_cents = DEFAULT_CLOUD_RUN_RESERVATION_CENTS
    _ensure_spend_limit_allows(session, estimate_cents)
    wallet = _active_wallet_or_402(session)
    if wallet.balance_cents < estimate_cents:
        raise HTTPException(status_code=402, detail="Insufficient credits")
    wallet.balance_cents -= estimate_cents
    wallet.updated_at = utc_now()
    reservation = BudgetReservation(
        workspace_id=current_workspace_id(),
        organization_id=current_organization_id(),
        project_id=project_id,
        task_id=task_id,
        reserved_cents=estimate_cents,
        status=BudgetReservationStatus.RESERVED,
    )
    session.add(wallet)
    session.add(reservation)
    session.flush()
    return reservation


def _active_reservation_for_cloud_run(
    session: Session,
    cloud_run: CloudRun,
) -> BudgetReservation | None:
    if cloud_run.budget_reservation_id is None:
        return None
    reservation = session.get(BudgetReservation, cloud_run.budget_reservation_id)
    if (
        reservation is None
        or reservation.status != BudgetReservationStatus.RESERVED
    ):
        return None
    return reservation


def _transition_reserved_reservation(
    session: Session,
    reservation: BudgetReservation,
    *,
    status: BudgetReservationStatus,
    settled_cents: int,
    settled_at,
) -> bool:
    result = session.execute(
        update(BudgetReservation)
        .where(
            BudgetReservation.id == reservation.id,
            BudgetReservation.status == BudgetReservationStatus.RESERVED,
        )
        .values(
            status=status,
            settled_cents=settled_cents,
            settled_at=settled_at,
            updated_at=settled_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.refresh(reservation)
        return False

    reservation.status = status
    reservation.settled_cents = settled_cents
    reservation.settled_at = settled_at
    reservation.updated_at = settled_at
    return True


def release_cloud_run_reservation(session: Session, cloud_run: CloudRun) -> None:
    reservation = _active_reservation_for_cloud_run(session, cloud_run)
    if reservation is None:
        return
    now = utc_now()
    if not _transition_reserved_reservation(
        session,
        reservation,
        status=BudgetReservationStatus.RELEASED,
        settled_cents=0,
        settled_at=now,
    ):
        return
    wallet = _get_or_create_wallet_for_scope(
        session,
        workspace_id=reservation.workspace_id,
        organization_id=reservation.organization_id,
    )
    wallet.balance_cents += reservation.reserved_cents
    wallet.updated_at = utc_now()
    cloud_run.actual_cost_cents = 0
    cloud_run.cost_summary_json = {
        "reservation_id": reservation.id,
        "reserved_cents": reservation.reserved_cents,
        "measured_cents": 0,
        "measured_cost_cents": 0,
        "actual_cents": 0,
        "actual_cost_cents": 0,
        "billable_cost_cents": 0,
        "status": "released",
    }
    session.add(wallet)
    session.add(reservation)
    session.add(cloud_run)


def settle_cloud_run_budget(session: Session, cloud_run: CloudRun) -> None:
    reservation = _active_reservation_for_cloud_run(session, cloud_run)
    if reservation is None:
        return

    usage_measurements = _cloud_run_usage_measurements(session, cloud_run)
    measured_cents = sum(measurement["amount_cents"] for measurement in usage_measurements)
    billable_cents = min(measured_cents, reservation.reserved_cents)
    now = utc_now()
    if not _transition_reserved_reservation(
        session,
        reservation,
        status=BudgetReservationStatus.SETTLED,
        settled_cents=billable_cents,
        settled_at=now,
    ):
        return

    for measurement in usage_measurements:
        _append_cloud_run_usage(
            session,
            cloud_run=cloud_run,
            organization_id=reservation.organization_id,
            usage_type=measurement["usage_type"],
            quantity=measurement["quantity"],
            unit_name=measurement["unit_name"],
            amount_cents=measurement["amount_cents"],
        )

    wallet = _get_or_create_wallet_for_scope(
        session,
        workspace_id=reservation.workspace_id,
        organization_id=reservation.organization_id,
    )
    wallet.balance_cents += max(reservation.reserved_cents - billable_cents, 0)
    wallet.updated_at = utc_now()
    cloud_run.actual_cost_cents = billable_cents
    cloud_run.cost_summary_json = {
        "reservation_id": reservation.id,
        "reserved_cents": reservation.reserved_cents,
        "actual_cents": billable_cents,
        "actual_cost_cents": billable_cents,
        "measured_cents": measured_cents,
        "measured_cost_cents": measured_cents,
        "billable_cost_cents": billable_cents,
        "usage": [
            {
                "usage_type": measurement["usage_type"].value,
                "quantity": measurement["quantity"],
                "unit_name": measurement["unit_name"],
                "amount_cents": measurement["amount_cents"],
            }
            for measurement in usage_measurements
        ],
        "status": "settled",
    }
    session.add(wallet)
    session.add(reservation)
    session.add(cloud_run)


def _cloud_run_runtime_seconds(cloud_run: CloudRun) -> int:
    if cloud_run.claimed_at is None or cloud_run.completed_at is None:
        return 0
    started_at = cloud_run.claimed_at.replace(tzinfo=None)
    completed_at = cloud_run.completed_at.replace(tzinfo=None)
    elapsed = int((completed_at - started_at).total_seconds())
    return max(elapsed, 1)


def _cloud_run_usage_measurements(
    session: Session,
    cloud_run: CloudRun,
) -> list[dict]:
    measurements = [
        {
            "usage_type": UsageType.WORKER_SUBMISSIONS,
            "quantity": 1,
            "unit_name": "submission",
            "amount_cents": WORKER_SUBMISSION_CENTS,
        }
    ]
    runtime_seconds = _cloud_run_runtime_seconds(cloud_run)
    if runtime_seconds > 0:
        measurements.append(
            {
                "usage_type": UsageType.CLOUD_RUN_RUNTIME_SECONDS,
                "quantity": runtime_seconds,
                "unit_name": "seconds",
                "amount_cents": runtime_seconds * RUNTIME_SECOND_CENTS,
            }
        )
    if cloud_run.queue_message_id is not None:
        measurements.append(
            {
                "usage_type": UsageType.QUEUE_MESSAGES,
                "quantity": 1,
                "unit_name": "message",
                "amount_cents": QUEUE_MESSAGE_CENTS,
            }
        )
    object_storage_bytes = _cloud_run_object_storage_bytes(session, cloud_run)
    if object_storage_bytes > 0:
        measurements.append(
            {
                "usage_type": UsageType.OBJECT_STORAGE_BYTES,
                "quantity": object_storage_bytes,
                "unit_name": "bytes",
                "amount_cents": object_storage_bytes * OBJECT_STORAGE_BYTE_CENTS,
            }
        )
    log_sync_calls = _known_cloud_run_log_sync_calls(cloud_run)
    if log_sync_calls > 0:
        measurements.append(
            {
                "usage_type": UsageType.LOG_SYNC_CALLS,
                "quantity": log_sync_calls,
                "unit_name": "call",
                "amount_cents": log_sync_calls * LOG_SYNC_CALL_CENTS,
            }
        )
    return measurements


def _cloud_run_object_storage_bytes(session: Session, cloud_run: CloudRun) -> int:
    sizes_by_ref: dict[str, int] = {}

    def add_size(ref: str, size_bytes: int | None) -> None:
        if size_bytes is None or size_bytes <= 0:
            return
        sizes_by_ref[ref] = max(sizes_by_ref.get(ref, 0), size_bytes)

    add_size(
        cloud_run.artifact_manifest_uri or "cloud_run:artifact_manifest",
        cloud_run.artifact_manifest_size_bytes,
    )
    add_size(
        cloud_run.log_stream_uri or "cloud_run:log_stream",
        cloud_run.log_stream_size_bytes,
    )
    stored_objects = session.exec(
        select(CloudRunStoredObject).where(
            CloudRunStoredObject.cloud_run_id == cloud_run.id,
        )
    ).all()
    for stored_object in stored_objects:
        add_size(stored_object.uri, stored_object.size_bytes)

    return sum(sizes_by_ref.values())


def _known_cloud_run_log_sync_calls(cloud_run: CloudRun) -> int:
    cost_summary = cloud_run.cost_summary_json or {}
    if isinstance(cost_summary, dict):
        raw_calls = cost_summary.get("log_sync_calls")
        if isinstance(raw_calls, int) and raw_calls > 0:
            return raw_calls

    # Runtime-backed runs with persisted log stream metadata have one known
    # deterministic log stream capture even when no provider counter exists.
    if cloud_run.runtime_provider is not None and cloud_run.log_stream_uri is not None:
        return 1
    return 0


def _append_cloud_run_usage(
    session: Session,
    *,
    cloud_run: CloudRun,
    organization_id: str,
    usage_type: UsageType,
    quantity: int,
    unit_name: str,
    amount_cents: int,
) -> None:
    existing_entry = session.exec(
        select(UsageLedgerEntry.id).where(
            UsageLedgerEntry.cloud_run_id == cloud_run.id,
            UsageLedgerEntry.usage_type == usage_type,
        )
    ).first()
    if existing_entry is not None:
        return

    unit_price_cents = amount_cents // quantity if quantity > 0 else 0
    entry = UsageLedgerEntry(
        workspace_id=cloud_run.workspace_id,
        organization_id=organization_id,
        user_id="system",
        project_id=cloud_run.project_id,
        task_id=cloud_run.task_id,
        cloud_run_id=cloud_run.id,
        usage_type=usage_type,
        provider_name=cloud_run.queue_provider,
        model_name="execution",
        quantity=quantity,
        unit_name=unit_name,
        unit_price_cents=unit_price_cents,
        amount_cents=amount_cents,
        raw_usage_json={
            "cloud_run_id": cloud_run.id,
            "usage_type": usage_type.value,
        },
    )
    try:
        with session.begin_nested():
            session.add(entry)
            session.flush()
    except IntegrityError:
        return


def _cost_summary_int(
    cost_summary: dict,
    keys: tuple[str, ...],
    fallback: int,
) -> int:
    for key in keys:
        value = cost_summary.get(key)
        if type(value) is int:
            return value
    return fallback


def cloud_run_cost_summary(
    session: Session,
    cloud_run_id: str,
) -> CloudRunCostSummaryRead:
    cloud_run = session.get(CloudRun, cloud_run_id)
    if cloud_run is None:
        raise HTTPException(status_code=404, detail="Cloud run not found")
    enforce_workspace_access(cloud_run.workspace_id, detail="Cloud run not found")
    should_audit = require_audited_workspace_permission_if_authenticated(
        session,
        "billing.read",
        operation="billing.cloud_run_cost_summary.read",
        resource_type="cloud_run",
        resource_id=cloud_run.id,
        access_level="high_sensitive_read",
    )
    reservation = (
        session.get(BudgetReservation, cloud_run.budget_reservation_id)
        if cloud_run.budget_reservation_id is not None
        else None
    )
    usage_entries = session.exec(
        select(UsageLedgerEntry)
        .where(
            UsageLedgerEntry.cloud_run_id == cloud_run.id,
            UsageLedgerEntry.workspace_id == cloud_run.workspace_id,
        )
        .order_by(UsageLedgerEntry.created_at, UsageLedgerEntry.id)
    ).all()
    cost_summary = (
        cloud_run.cost_summary_json
        if isinstance(cloud_run.cost_summary_json, dict)
        else {}
    )
    measured_cost_cents = _cost_summary_int(
        cost_summary,
        ("measured_cost_cents", "measured_cents"),
        cloud_run.actual_cost_cents,
    )
    billable_cost_cents = _cost_summary_int(
        cost_summary,
        ("billable_cost_cents", "actual_cost_cents", "actual_cents"),
        cloud_run.actual_cost_cents,
    )
    result = CloudRunCostSummaryRead(
        cloud_run_id=cloud_run.id,
        workspace_id=cloud_run.workspace_id,
        project_id=cloud_run.project_id,
        task_id=cloud_run.task_id,
        estimated_cost_cents=cloud_run.estimated_cost_cents,
        measured_cost_cents=measured_cost_cents,
        billable_cost_cents=billable_cost_cents,
        actual_cost_cents=cloud_run.actual_cost_cents,
        reservation=_reservation_read(reservation) if reservation is not None else None,
        usage_entries=[_usage_entry_read(entry) for entry in usage_entries],
    )
    if should_audit:
        record_workspace_audit(
            session,
            operation="billing.cloud_run_cost_summary.read",
            resource_type="cloud_run",
            resource_id=cloud_run.id,
            access_level="high_sensitive_read",
            success=True,
            status_code=200,
            metadata={
                "cloud_run_id": cloud_run.id,
                "usage_entry_count": len(usage_entries),
                "has_reservation": reservation is not None,
            },
            commit=True,
        )
    return result


def set_workspace_spend_limit(
    session: Session,
    data: SpendLimitUpdate,
    *,
    commit: bool = True,
) -> SpendLimitRead:
    spend_limit = _get_or_create_spend_limit(session)
    spend_limit.monthly_limit_cents = data.monthly_limit_cents
    spend_limit.per_run_limit_cents = data.per_run_limit_cents
    spend_limit.updated_at = utc_now()
    session.add(spend_limit)
    if commit:
        session.commit()
        session.refresh(spend_limit)
    else:
        session.flush()
        session.refresh(spend_limit)
    return _spend_limit_read(spend_limit)


def workspace_usage_summary(
    session: Session,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
) -> UsageSummaryRead:
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        enforce_workspace_access(project.workspace_id, detail="Project not found")
    if task_id is not None:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        task_project = session.get(Project, task.project_id)
        if task_project is None:
            raise HTTPException(status_code=404, detail="Task not found")
        enforce_workspace_access(task_project.workspace_id, detail="Task not found")
        if project_id is not None and task.project_id != project_id:
            raise HTTPException(status_code=400, detail="Task does not belong to project")

    workspace_id = current_workspace_id()
    statement = select(UsageLedgerEntry).where(
        UsageLedgerEntry.workspace_id == workspace_id
    )
    if project_id is not None:
        statement = statement.where(UsageLedgerEntry.project_id == project_id)
    if task_id is not None:
        statement = statement.where(UsageLedgerEntry.task_id == task_id)

    totals: dict[str, dict[str, int]] = {}
    for entry in session.exec(statement).all():
        usage_type = entry.usage_type.value if hasattr(entry.usage_type, "value") else str(entry.usage_type)
        aggregate = totals.setdefault(usage_type, {"quantity": 0, "amount_cents": 0})
        aggregate["quantity"] += entry.quantity
        aggregate["amount_cents"] += entry.amount_cents

    items = [
        UsageSummaryItemRead(
            usage_type=usage_type,
            quantity=values["quantity"],
            amount_cents=values["amount_cents"],
        )
        for usage_type, values in sorted(totals.items())
    ]
    return UsageSummaryRead(
        workspace_id=workspace_id,
        project_id=project_id,
        task_id=task_id,
        total_amount_cents=sum(item.amount_cents for item in items),
        items=items,
    )
