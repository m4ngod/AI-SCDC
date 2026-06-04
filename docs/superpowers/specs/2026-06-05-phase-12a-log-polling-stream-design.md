# Phase 12A Log Polling And Stream Read Design

## Summary

Phase 12A makes cloud-run logs usable as a bounded polling surface after the
Phase 11 real remote worker execution skeleton. It adds a cursor-based log
window endpoint and a safe path for reading remote log-stream objects when the
control plane has full object-storage reference metadata.

This phase is intentionally narrow. It does not add direct MNS receive/delete
semantics, SLS, WebSockets, live tailing, model-backed debugger/reviewer agents,
new billing meters, or a new provider package layout.

## Current State

The current API exposes:

```text
GET /cloud-runs/{cloud_run_id}/logs
```

That endpoint returns every persisted `CloudRunLogEntry` for a run ordered by
`created_at` and `id`. The desktop calls it after cloud-run creation,
processing, and cancellation, then renders compact event/message rows.

Phase 10B and Phase 10C also added `artifact_manifest_uri` and `log_stream_uri`
fields to `CloudRun`. Remote runtime providers can seed a manifest object and a
log object, but the API stores only the URIs. The object storage read path
requires kind, URI, SHA-256, size, and content type. Reading a remote log object
from URI alone would bypass the integrity contract, so Phase 12A must preserve
or recover complete object ref metadata before it reads the stream.

## Design Choice

Use an additive log window endpoint and keep the existing endpoint compatible.

Add:

```text
GET /cloud-runs/{cloud_run_id}/logs/window?after=<cursor>&limit=<n>&include_stream=true
```

The existing `/logs` endpoint continues returning `list[CloudRunLogEntryRead]`
for current desktop and tests. The new `/logs/window` endpoint returns an
object with entries, a next cursor, and a `has_more` flag. Desktop can migrate
to the window endpoint without forcing every existing consumer to change at
once.

The new endpoint reads two sources:

1. persisted control-plane log rows from `cloud_run_log_entry`;
2. optional remote log-stream text from `CloudRun.log_stream_uri`, but only when
   the run also has stored `log_stream_sha256`, `log_stream_size_bytes`, and
   `log_stream_content_type` metadata.

For V1, merged ordering is deterministic rather than real-time interleaved:
control-plane entries are emitted first by `(created_at, id)`, followed by
log-stream lines by line number. That gives clients a stable cursor today and
leaves provider-native live streaming for a later phase.

## Data Model

Extend `CloudRun` with nullable object-ref metadata:

- `artifact_manifest_sha256`
- `artifact_manifest_size_bytes`
- `artifact_manifest_content_type`
- `log_stream_sha256`
- `log_stream_size_bytes`
- `log_stream_content_type`

These fields are not secrets. They allow the API to rebuild an `ObjectStorageRef`
for stored manifest and log objects without trusting a URI by itself.

SQLite upgrade helpers add these columns for existing development databases.
Existing rows keep `NULL` metadata and therefore do not expose remote stream
content through the new window endpoint.

## API Schemas

Add response models:

```python
class CloudRunLogWindowEntryRead(BaseModel):
    id: str
    cloud_run_id: str
    source: Literal["control_plane", "log_stream"]
    level: str
    event: str
    message: str
    payload: dict[str, Any] | None
    created_at: datetime
    sequence: int


class CloudRunLogWindowRead(BaseModel):
    entries: list[CloudRunLogWindowEntryRead]
    next_cursor: str | None
    has_more: bool
```

The cursor is an opaque base64url JSON value. It contains only pagination state:

```json
{"source":"control_plane","created_at":"...","id":"...","stream_line":null}
```

Clients must treat the cursor as opaque and send it back unchanged. Invalid
cursors return `400` with a non-sensitive error message.

## Service Components

Add a focused module:

```text
apps/api/app/ai_company_api/services/cloud_run_logs.py
```

Responsibilities:

- validate and decode cursors;
- query persisted `CloudRunLogEntry` rows with a strict `limit`;
- rebuild stream `ObjectStorageRef` from `CloudRun` metadata;
- read stream text through the existing object-storage provider registry;
- convert stream lines into synthetic log-window entries;
- redact stream text using generic token/error redaction and URI redaction;
- return `CloudRunLogWindowRead`.

Keep `_append_cloud_run_log` in `cloud_runner.py` for this phase to avoid a
large no-behavior refactor. `cloud_runner.list_cloud_run_logs` may delegate to
the new module later, but Phase 12A should not split `cloud_runner.py` broadly.

## Remote Runtime Metadata

Update `RemoteRuntimeSubmissionResult` so providers can return complete refs:

```python
artifact_manifest_ref: ObjectStorageRef | None
log_stream_ref: ObjectStorageRef | None
```

Existing `artifact_manifest_uri` and `log_stream_uri` string properties remain
available for compatibility. When `remote_stub` or `aliyun_eci` creates the
initial manifest/log objects, it returns both the URI and full ref metadata.
`enqueue_cloud_run` persists the metadata on `CloudRun`.

This does not change worker artifact upload or completion payloads.

## Redaction And Safety

The window endpoint never returns queue receipts, callback tokens, clone tokens,
Aliyun AccessKeys, signed query strings, or raw provider errors.

Controls:

- control-plane log payloads continue through `redact_sensitive_values`;
- stream text is split into bounded lines and redacted with generic token-like
  external-error patterns before it is returned;
- stream payloads include only a redacted stream URI, source, and line number;
- object storage reads require kind, URI, SHA-256, size, and content type;
- if stream ref metadata is absent or unreadable, the endpoint returns available
  control-plane logs and appends no synthetic stream entries.

The endpoint does not open GitHub clone credentials solely to redact stream
content. Worker-generated logs must remain redacted before upload, as required
by Phase 11.

## Desktop Behavior

The desktop API client can add:

```typescript
listCloudRunLogWindow(cloudRunId, { after, limit, includeStream })
```

For Phase 12A, the existing task board may keep using `listCloudRunLogs`. A
small client test should verify that the HTTP client maps the new window
response and sends the `after`, `limit`, and `include_stream` query parameters.

Moving the task board to incremental polling can be a follow-up once the API
surface is stable.

## Error Handling

- Unknown cloud run: `404`.
- Invalid cursor: `400`.
- `limit < 1` or `limit > 200`: request validation error.
- Unsupported stream URI scheme or failed stream read: skip stream entries and
  keep control-plane rows in the response. The API may persist a warning log in
  a later phase, but Phase 12A should avoid mutating log state from a read
  request.

## Tests

API tests:

- window endpoint returns a bounded first page and `next_cursor`;
- sending `next_cursor` returns the next page without duplicates;
- invalid cursor returns `400`;
- log stream lines are returned only when metadata is present;
- log stream text is redacted and payload URI has no query string;
- missing stream metadata does not attempt an object read and still returns
  persisted logs;
- SQLite upgrade adds the new metadata columns.

Desktop tests:

- HTTP client builds `/cloud-runs/{id}/logs/window` with the expected query
  parameters;
- response maps `source`, `sequence`, `nextCursor`, and `hasMore`.

Verification:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or metadata_columns" -v
pnpm --filter @ai-scdc/desktop test -- client
pytest apps/api/tests -v
pnpm typecheck
git diff --check
```

## Non-Goals

- No WebSocket or server-sent event streaming.
- No provider-native SLS/CloudWatch/Cloud Logging integration.
- No direct Aliyun MNS receive/delete semantics.
- No artifact browser UI.
- No model reviewer/debugger consumption of logs.
- No broad `cloud_runner.py` module split.
- No production KMS changes.
