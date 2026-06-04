import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlmodel import Session, select

from ai_company_api.models.entities import CloudRun, CloudRunLogEntry
from ai_company_api.schemas.api import (
    CloudRunLogWindowEntryRead,
    CloudRunLogWindowRead,
)
from ai_company_api.services.object_storage import (
    ObjectStorageProviderNotFound,
    ObjectStorageReadError,
    ObjectStorageRef,
    get_object_storage_provider,
)


BASE64URL_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
STREAM_SECRET_PATTERN = re.compile(
    r"\b(?P<key>access_token|authorization|password|secret|token|sig)"
    r"\b\s*[:=]\s*(?:Bearer\s+)?\S+",
    re.IGNORECASE,
)
STREAM_BEARER_PATTERN = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)


@dataclass(frozen=True)
class LogWindowCursor:
    source: Literal["control_plane", "log_stream"]
    created_at: datetime | None = None
    id: str | None = None
    stream_line: int | None = None


def list_cloud_run_log_window(
    session: Session,
    *,
    cloud_run_id: str,
    after: str | None = None,
    limit: int = 100,
    include_stream: bool = True,
) -> CloudRunLogWindowRead:
    cloud_run = session.get(CloudRun, cloud_run_id)
    if cloud_run is None:
        raise HTTPException(status_code=404, detail="Cloud run not found")

    cursor = _decode_cursor(after)
    entries = _control_plane_entries(
        session,
        cloud_run=cloud_run,
        cursor=cursor,
        limit=limit + 1,
    )
    if include_stream and len(entries) <= limit:
        entries.extend(
            _log_stream_entries(
                session,
                cloud_run=cloud_run,
                cursor=cursor,
                limit=limit + 1 - len(entries),
            )
        )

    if len(entries) > limit:
        returned_entries = entries[:limit]
        return CloudRunLogWindowRead(
            entries=returned_entries,
            next_cursor=_entry_cursor(returned_entries[-1]),
            has_more=True,
        )

    return CloudRunLogWindowRead(
        entries=entries,
        next_cursor=None,
        has_more=False,
    )


def _decode_cursor(after: str | None) -> LogWindowCursor | None:
    if after is None:
        return None
    if not BASE64URL_CURSOR_PATTERN.fullmatch(after):
        raise HTTPException(status_code=400, detail="Invalid log cursor")

    try:
        padding = "=" * (-len(after) % 4)
        decoded = base64.urlsafe_b64decode((after + padding).encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        return _cursor_from_payload(payload)
    except (
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=400, detail="Invalid log cursor") from exc


def _cursor_from_payload(payload: dict[str, Any]) -> LogWindowCursor:
    source = payload.get("source")
    if source == "control_plane":
        created_at_value = payload.get("created_at")
        entry_id = payload.get("id")
        if not isinstance(created_at_value, str) or not isinstance(entry_id, str):
            raise ValueError
        return LogWindowCursor(
            source="control_plane",
            created_at=datetime.fromisoformat(created_at_value),
            id=entry_id,
        )
    if source == "log_stream":
        stream_line = payload.get("stream_line")
        if stream_line is not None and not isinstance(stream_line, int):
            raise ValueError
        if stream_line is not None and stream_line < 0:
            raise ValueError
        return LogWindowCursor(source="log_stream", stream_line=stream_line)
    raise ValueError


def _control_plane_entries(
    session: Session,
    *,
    cloud_run: CloudRun,
    cursor: LogWindowCursor | None,
    limit: int,
) -> list[CloudRunLogWindowEntryRead]:
    if cursor is not None and cursor.source == "log_stream":
        return []

    statement = select(CloudRunLogEntry).where(
        CloudRunLogEntry.cloud_run_id == cloud_run.id
    )
    if cursor is not None and cursor.source == "control_plane":
        statement = statement.where(
            or_(
                CloudRunLogEntry.created_at > cursor.created_at,
                and_(
                    CloudRunLogEntry.created_at == cursor.created_at,
                    CloudRunLogEntry.id > cursor.id,
                ),
            )
        )
    rows = session.exec(
        statement.order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id).limit(limit)
    ).all()
    return [
        _log_window_entry_read(entry, sequence=sequence)
        for sequence, entry in enumerate(rows)
    ]


def _log_window_entry_read(
    entry: CloudRunLogEntry,
    *,
    sequence: int,
) -> CloudRunLogWindowEntryRead:
    return CloudRunLogWindowEntryRead(
        id=entry.id,
        cloud_run_id=entry.cloud_run_id,
        source="control_plane",
        level=entry.level,
        event=entry.event,
        message=entry.message,
        payload=entry.payload,
        created_at=entry.created_at,
        sequence=sequence,
    )


def _log_stream_entries(
    session: Session,
    *,
    cloud_run: CloudRun,
    cursor: LogWindowCursor | None,
    limit: int,
) -> list[CloudRunLogWindowEntryRead]:
    if limit <= 0:
        return []

    start_line = 0
    if cursor is not None and cursor.source == "log_stream":
        start_line = cursor.stream_line or 0

    ref = _log_stream_ref(cloud_run)
    if ref is None:
        return []

    provider_name = _object_storage_provider_name(ref.uri)
    if provider_name is None:
        return []

    try:
        text = get_object_storage_provider(provider_name).read_text(session, ref)
    except (ObjectStorageProviderNotFound, ObjectStorageReadError):
        return []

    redacted_uri = _redact_uri(ref.uri)
    entries: list[CloudRunLogWindowEntryRead] = []
    for line_number, line in enumerate(text.splitlines()):
        if line_number < start_line:
            continue
        entries.append(
            CloudRunLogWindowEntryRead(
                id=f"{cloud_run.id}:log-stream:{line_number}",
                cloud_run_id=cloud_run.id,
                source="log_stream",
                level="info",
                event="log_stream",
                message=_redact_stream_line(line),
                payload={
                    "source": "log_stream",
                    "line": line_number + 1,
                    "uri": redacted_uri,
                },
                created_at=cloud_run.created_at,
                sequence=line_number,
            )
        )
        if len(entries) >= limit:
            break
    return entries


def _log_stream_ref(cloud_run: CloudRun) -> ObjectStorageRef | None:
    if (
        cloud_run.log_stream_uri is None
        or cloud_run.log_stream_sha256 is None
        or cloud_run.log_stream_size_bytes is None
        or cloud_run.log_stream_content_type is None
    ):
        return None
    return ObjectStorageRef(
        kind="log",
        uri=cloud_run.log_stream_uri,
        sha256=cloud_run.log_stream_sha256,
        size_bytes=cloud_run.log_stream_size_bytes,
        content_type=cloud_run.log_stream_content_type,
    )


def _object_storage_provider_name(uri: str) -> str | None:
    scheme = urlsplit(uri).scheme
    if scheme == "local-inline":
        return "local_inline"
    if scheme == "oss":
        return "aliyun_oss"
    return None


def _redact_stream_line(line: str) -> str:
    redacted = STREAM_SECRET_PATTERN.sub(
        lambda match: f"{match.group('key')}=[redacted]",
        line,
    )
    return STREAM_BEARER_PATTERN.sub("Bearer [redacted]", redacted)


def _redact_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _entry_cursor(entry: CloudRunLogWindowEntryRead) -> str:
    if entry.source == "log_stream":
        return _encode_cursor(
            {
                "source": "log_stream",
                "created_at": None,
                "id": None,
                "stream_line": entry.sequence + 1,
            }
        )

    return _encode_cursor(
        {
            "source": entry.source,
            "created_at": entry.created_at.isoformat(),
            "id": entry.id,
            "stream_line": None,
        }
    )


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
