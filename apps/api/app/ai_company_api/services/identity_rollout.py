from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import update
from sqlmodel import Session, select

from ai_company_api.models.entities import DeviceSession, IdentityAuditEvent
from ai_company_api.services.auth_policy import AuthenticationEnvironment
from ai_company_api.services.identity_audit import record_identity_audit_event


class IdentityRolloutStage(StrEnum):
    DISABLED = "disabled"
    CIAM_TEST_TENANT = "ciam_test_tenant"
    PRODUCTION_INTERNAL = "production_internal"
    EXISTING_BETA = "existing_beta"
    PUBLIC = "public"


REQUIRED_IDENTITY_RELEASE_GATES = frozenset(
    {
        "api_token_rbac_regression",
        "browser_cookie_csrf_e2e",
        "dependency_graph_complete",
        "fake_provider_automation",
        "identity_audit_regression",
        "migration_rollback_rehearsal",
        "real_ciam_smoke",
        "secret_access_audit_regression",
        "secret_leak_scan",
        "secret_vault_kms_regression",
        "worker_callback_regression",
        "workspace_audit_regression",
        "workspace_isolation_regression",
    }
)


@dataclass(frozen=True)
class IdentityRolloutPolicy:
    stage: IdentityRolloutStage
    passed_release_gates: frozenset[str] = frozenset()
    internal_email_allowlist: frozenset[str] = frozenset()
    security_rollback: bool = False

    def __post_init__(self) -> None:
        try:
            stage = IdentityRolloutStage(self.stage)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported identity rollout stage: {self.stage}"
            ) from exc
        object.__setattr__(self, "stage", stage)
        passed_release_gates = frozenset(
            gate.strip()
            for gate in self.passed_release_gates
            if gate.strip()
        )
        unknown_gates = passed_release_gates - REQUIRED_IDENTITY_RELEASE_GATES
        if unknown_gates:
            raise ValueError(
                "Unsupported identity release gates: "
                + ", ".join(sorted(unknown_gates))
            )
        object.__setattr__(
            self,
            "passed_release_gates",
            passed_release_gates,
        )
        object.__setattr__(
            self,
            "internal_email_allowlist",
            frozenset(
                email.strip().casefold()
                for email in self.internal_email_allowlist
                if email.strip()
            ),
        )
        if (
            stage == IdentityRolloutStage.PUBLIC
            and self.missing_release_gates
            and not self.security_rollback
        ):
            raise ValueError(
                "Public self-registration requires every identity release gate"
            )

    @property
    def oidc_login_enabled(self) -> bool:
        return (
            not self.security_rollback
            and self.stage != IdentityRolloutStage.DISABLED
        )

    @property
    def cookie_authentication_enabled(self) -> bool:
        return not self.security_rollback

    @property
    def self_registration(self) -> str:
        if self.security_rollback or self.stage == IdentityRolloutStage.DISABLED:
            return "disabled"
        if self.stage == IdentityRolloutStage.PRODUCTION_INTERNAL:
            return "internal_allowlist"
        if self.stage == IdentityRolloutStage.EXISTING_BETA:
            return "existing_beta_only"
        if self.stage == IdentityRolloutStage.PUBLIC:
            return "public"
        return "test_tenant"

    @property
    def missing_release_gates(self) -> frozenset[str]:
        return REQUIRED_IDENTITY_RELEASE_GATES - self.passed_release_gates

    def admits_login(
        self,
        *,
        email: str | None,
        has_external_identity: bool,
        has_legacy_account: bool,
    ) -> bool:
        if not self.oidc_login_enabled:
            return False
        if self.stage == IdentityRolloutStage.PRODUCTION_INTERNAL:
            return (
                email is not None
                and email.strip().casefold()
                in self.internal_email_allowlist
            )
        if self.stage == IdentityRolloutStage.EXISTING_BETA:
            return has_external_identity or has_legacy_account
        return True

    def public_status(
        self,
        *,
        schema_migration_mode: str,
        schema_ready: bool,
        pending_schema_actions: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "oidc_login_enabled": self.oidc_login_enabled,
            "cookie_authentication_enabled": (
                self.cookie_authentication_enabled
            ),
            "self_registration": self.self_registration,
            "schema_migration": {
                "mode": schema_migration_mode,
                "schema_ready": schema_ready,
                "pending_actions": list(pending_schema_actions),
            },
            "release_gates": {
                "passed": sorted(self.passed_release_gates),
                "missing": sorted(self.missing_release_gates),
            },
        }


def revoke_user_sessions_for_security_rollback(
    session: Session,
    *,
    now: datetime,
) -> int:
    active_session_ids = session.exec(
        select(DeviceSession.id)
        .where(DeviceSession.status == "active")
        .order_by(DeviceSession.created_at, DeviceSession.id)
    ).all()
    revoked_count = 0
    for device_session_id in active_session_ids:
        result = session.execute(
            update(DeviceSession)
            .where(
                DeviceSession.id == device_session_id,
                DeviceSession.status == "active",
            )
            .values(
                status="revoked",
                revoked_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        revoked_count += 1
        correlation_id = f"security_rollback_{device_session_id}"
        existing_audit = session.exec(
            select(IdentityAuditEvent.id).where(
                IdentityAuditEvent.event_type
                == "identity_security_rollback",
                IdentityAuditEvent.correlation_id == correlation_id,
            )
        ).first()
        if existing_audit is not None:
            continue
        device_session = session.get(DeviceSession, device_session_id)
        assert device_session is not None
        record_identity_audit_event(
            session,
            event_type="identity_security_rollback",
            outcome="success",
            reason_code="user_sessions_revoked",
            correlation_id=correlation_id,
            user_id=device_session.user_id,
            device_session_id=device_session_id,
            commit=False,
        )
    if revoked_count:
        session.commit()
    return revoked_count


def identity_rollout_policy(
    *,
    environment: AuthenticationEnvironment,
    stage: IdentityRolloutStage | str | None,
    passed_release_gates: frozenset[str] = frozenset(),
    internal_email_allowlist: frozenset[str] = frozenset(),
    security_rollback: bool = False,
) -> IdentityRolloutPolicy:
    if stage is None:
        resolved_stage = (
            IdentityRolloutStage.CIAM_TEST_TENANT
            if environment == AuthenticationEnvironment.TEST
            else IdentityRolloutStage.DISABLED
        )
    else:
        try:
            resolved_stage = IdentityRolloutStage(stage)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported identity rollout stage: {stage}"
            ) from exc
    if (
        environment == AuthenticationEnvironment.PRODUCTION
        and resolved_stage == IdentityRolloutStage.CIAM_TEST_TENANT
        and not security_rollback
    ):
        raise ValueError(
            "CIAM test-tenant rollout is not allowed in production"
        )
    return IdentityRolloutPolicy(
        stage=resolved_stage,
        passed_release_gates=passed_release_gates,
        internal_email_allowlist=internal_email_allowlist,
        security_rollback=security_rollback,
    )
