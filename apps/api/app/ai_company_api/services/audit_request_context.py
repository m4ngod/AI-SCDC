from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
import secrets
from threading import Lock
from typing import Callable, Iterator


MAX_USER_AGENT_LENGTH = 512
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(?:"
    r"bearer\s+\S+"
    r"|gh[pousr]_[A-Za-z0-9_-]+"
    r"|sk-[A-Za-z0-9_-]+"
    r"|__Host-ai_scdc_session=\S+"
    r"|scdc_(?:test|worker)_[A-Za-z0-9_-]+"
    r"|(?:session(?:_secret)?|worker_callback|api_token"
    r"|authorization_code|otp|cookie)\s*[:=]\s*\S+"
    r")"
)
_OPAQUE_SECRET_CANDIDATE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}"
    r"(?![A-Za-z0-9_-])"
)


@dataclass
class AuditRequestContext:
    request_id: str
    correlation_id: str
    occurred_at: datetime
    client_ip_address: str | None
    user_agent: str | None
    timestamp_source: Callable[[], datetime] | None = field(
        default=None,
        repr=False,
    )
    _event_sequence: int = field(default=0, init=False, repr=False)
    _sequence_lock: Lock = field(
        default_factory=Lock,
        init=False,
        repr=False,
    )

    def next_occurred_at(
        self,
        explicit_value: datetime | None = None,
    ) -> datetime:
        with self._sequence_lock:
            sequence = self._event_sequence
            self._event_sequence += 1
        if explicit_value is not None:
            return _as_utc(explicit_value)
        if self.timestamp_source is not None:
            return self.timestamp_source()
        return _as_utc(self.occurred_at) + timedelta(
            microseconds=sequence
        )

    @property
    def has_events(self) -> bool:
        with self._sequence_lock:
            return self._event_sequence > 0


class MonotonicAuditClock:
    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._last_value: datetime | None = None
        self._lock = Lock()

    def next(self) -> datetime:
        candidate = _as_utc(self._clock())
        with self._lock:
            if (
                self._last_value is not None
                and candidate <= self._last_value
            ):
                candidate = self._last_value + timedelta(
                    microseconds=1
                )
            self._last_value = candidate
        return candidate


_current_audit_request_context: ContextVar[
    AuditRequestContext | None
] = ContextVar(
    "ai_scdc_audit_request_context",
    default=None,
)


@contextmanager
def audit_request_context_scope(
    context: AuditRequestContext,
) -> Iterator[AuditRequestContext]:
    token = _current_audit_request_context.set(context)
    try:
        yield context
    finally:
        _current_audit_request_context.reset(token)


def current_audit_request_context() -> AuditRequestContext | None:
    return _current_audit_request_context.get()


def resolved_request_id(value: str | None = None) -> str:
    if value:
        return value
    context = current_audit_request_context()
    if context is not None:
        return context.request_id
    return f"request_{secrets.token_hex(16)}"


def resolved_correlation_id(value: str | None = None) -> str:
    if value:
        return value
    context = current_audit_request_context()
    if context is not None:
        return context.correlation_id
    return f"correlation_{secrets.token_hex(16)}"


def resolved_audit_time(value: datetime | None = None) -> datetime:
    context = current_audit_request_context()
    if context is not None:
        return context.next_occurred_at(value)
    if value is not None:
        return _as_utc(value)
    return datetime.now(timezone.utc)


def safe_user_agent(value: str | None) -> str | None:
    return safe_audit_text(
        value,
        max_length=MAX_USER_AGENT_LENGTH,
    )


def safe_audit_text(
    value: str | None,
    *,
    max_length: int,
) -> str | None:
    if value is None:
        return None
    normalized = "".join(
        character
        for character in value.strip()
        if character.isprintable()
    )
    if not normalized:
        return None
    if (
        _SENSITIVE_TEXT_PATTERN.search(normalized)
        or _contains_opaque_secret_candidate(normalized)
    ):
        return "[redacted]"
    return normalized[:max_length]


def _contains_opaque_secret_candidate(value: str) -> bool:
    return any(
        len(set(match.group(0))) >= 8
        for match in _OPAQUE_SECRET_CANDIDATE_PATTERN.finditer(value)
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
