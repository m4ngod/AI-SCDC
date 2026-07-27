import secrets

from fastapi import HTTPException, Request, status
from sqlmodel import Session

from ai_company_api.services.auth_policy import HumanCredentialType
from ai_company_api.services.identity_audit import record_identity_audit_event


def worker_callback_token_required(request: Request) -> bool:
    accepted_credentials = (
        request.app.state.authentication_policy.accepted_human_credentials
    )
    return HumanCredentialType.DEV_AUTH not in accepted_credentials


def require_local_worker_harness(
    request: Request,
    session: Session,
) -> None:
    if not worker_callback_token_required(request):
        return
    reject_worker_authentication(
        session,
        reason_code="worker_route_not_available",
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Worker route is not available",
    )


def require_presented_worker_callback_credential(
    session: Session,
    *,
    callback_token: str | None,
    cloud_run_id: str | None = None,
    require_cloud_run_id: bool = False,
) -> None:
    if callback_token is None:
        reject_worker_authentication(
            session,
            reason_code="worker_callback_token_required",
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Worker callback token is required",
        )
    if require_cloud_run_id and cloud_run_id is None:
        reject_worker_authentication(
            session,
            reason_code="worker_callback_scope_required",
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cloud run id is required",
        )


def reject_worker_authentication(
    session: Session | None,
    *,
    reason_code: str,
    status_code: int,
    detail: str,
) -> None:
    headers = None
    if session is not None:
        correlation_id = secrets.token_hex(16)
        record_identity_audit_event(
            session,
            event_type="authentication_failure",
            outcome="failure",
            reason_code=reason_code,
            correlation_id=correlation_id,
        )
        headers = {"X-Correlation-ID": correlation_id}
    raise HTTPException(
        status_code=status_code,
        detail=detail,
        headers=headers,
    )
