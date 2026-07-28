from datetime import datetime

from sqlalchemy import update
from sqlmodel import Session

from ai_company_api.models.entities import ProviderLogoutContinuation


def replace_provider_logout_continuation(
    session: Session,
    *,
    device_session_id: str,
    sealed_provider_hint: str | None,
    now: datetime,
) -> None:
    session.execute(
        update(ProviderLogoutContinuation)
        .where(
            ProviderLogoutContinuation.device_session_id
            == device_session_id,
            ProviderLogoutContinuation.status == "available",
        )
        .values(
            sealed_provider_hint=None,
            status="superseded",
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if sealed_provider_hint is None:
        return
    session.add(
        ProviderLogoutContinuation(
            device_session_id=device_session_id,
            sealed_provider_hint=sealed_provider_hint,
            created_at=now,
            updated_at=now,
        )
    )
