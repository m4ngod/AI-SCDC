# Phase 12B Provider Log Sync Design

## Summary

Phase 12B adds provider-native log sync on top of the Phase 12A bounded log
window. Clients continue using the polling API instead of a live transport. The
API can optionally ask the runtime provider to refresh the run's remote log
object before returning the existing cursor window.

This phase is intentionally narrow. It does not add WebSockets, Server-Sent
Events, direct MNS receive/delete worker semantics, SLS-managed log stores,
artifact browser UI, model-backed reviewer/debugger agents, production KMS, or
a broad provider package split.

## Current State

Phase 12A exposes:

```text
GET /cloud-runs/{cloud_run_id}/logs/window?after=<cursor>&limit=<n>&include_stream=true
```

The endpoint returns persisted `CloudRunLogEntry` rows and, when complete
object ref metadata is available, redacted lines from `CloudRun.log_stream_uri`.
The log stream is currently a snapshot written during runtime submission. For
`remote_stub` and `aliyun_eci`, that snapshot is enough to prove the object
storage contract but not enough to follow provider-side runtime output after
submission.

`CloudRun` already stores the fields needed to rebuild a log object ref:

- `log_stream_uri`
- `log_stream_sha256`
- `log_stream_size_bytes`
- `log_stream_content_type`

The object storage providers already enforce kind, URI, SHA-256, size, and
content type before reading text. Phase 12B should preserve that integrity
contract by writing refreshed provider logs through the same object storage
providers, then updating the `CloudRun.log_stream_*` metadata.

## Design Choice

Add provider log sync as an optional pre-read step on the existing log window
endpoint:

```text
GET /cloud-runs/{cloud_run_id}/logs/window?after=<cursor>&limit=<n>&include_stream=true&sync_stream=true
```

Defaults remain compatible:

- `sync_stream` defaults to `false`.
- `include_stream=false` prevents provider sync even when `sync_stream=true`.
- The response schema remains `CloudRunLogWindowRead`.
- Invalid cursors, limits, redaction, and pagination keep Phase 12A behavior.

This gives the desktop and tests a single polling surface. A client that wants
fresh provider logs can opt into sync on each poll. A client that only needs
control-plane logs or already-cached stream content can keep the cheaper
default behavior.

## Provider Contract

Extend the runtime provider boundary with a log sync method:

```python
@dataclass(frozen=True)
class RemoteRuntimeLogSyncRequest:
    workspace_id: str
    project_id: str
    task_id: str
    cloud_run_id: str
    runtime_job_id: str | None
    storage_provider: str | None
    current_log_stream_ref: ObjectStorageRef | None


@dataclass(frozen=True)
class RemoteRuntimeLogSyncResult:
    status: Literal["updated", "unchanged", "skipped", "unsupported"]
    log_stream_ref: ObjectStorageRef | None = None
    reason: str | None = None
```

The sync method returns a complete `ObjectStorageRef` only when the provider
writes a new or changed log snapshot. It never returns raw credentials, signed
URLs, callback tokens, or unredacted provider errors.

Provider behavior:

- `remote_stub` writes a deterministic refreshed log snapshot through
  `local_inline` when the run uses `local_inline` storage. Repeated syncs return
  `unchanged` after the deterministic refresh line is already present.
- `aliyun_eci` calls an Aliyun ECI log client seam when the run has
  `runtime_job_id`, `storage_provider="aliyun_oss"`, and required Aliyun
  configuration. It writes the returned text to OSS as a `kind="log"` object and
  returns the new ref when the content changed.
- providers that cannot refresh logs return `unsupported` or `skipped` without
  failing the log window request.

## Aliyun ECI Log Source

Use Aliyun ECI's `DescribeContainerLog` operation as the provider-native log
source. Alibaba Cloud documents this operation as querying logs for a container
in a container group, with `ContainerGroupId`, `ContainerName`, optional tail
and byte limits, and a documented 1 MiB maximum response size:

https://www.alibabacloud.com/help/en/eci/developer-reference/api-eci-2018-08-08-describecontainerlog

Add an Aliyun client seam:

```python
@dataclass(frozen=True)
class AliyunEciDescribeContainerLogRequest:
    region_id: str
    container_group_id: str
    container_name: str
    tail: int = 2000
    limit_bytes: int = 1024 * 1024
    timestamps: bool = False


class AliyunEciClient(Protocol):
    def describe_container_log(
        self,
        request: AliyunEciDescribeContainerLogRequest,
    ) -> dict[str, Any]:
        ...
```

The implementation uses the same ECI SDK family as container group creation.
Tests use the existing `set_aliyun_client_bundle_for_tests` override with a fake
ECI client. Phase 12B does not add SLS resources or long-lived log store
provisioning.

The container name must be stable. Use the existing ECI container group name
helper for the single container created by `AliyunEciRuntimeProvider`, and keep
that same name in both create and describe-log requests. If a future worker
layout adds multiple containers, the runtime provider can choose the worker
container explicitly without changing the API.

## API And Service Flow

Add a focused sync module so `cloud_run_logs.py` stays responsible for window
pagination and object reads:

```text
apps/api/app/ai_company_api/services/cloud_run_log_sync.py
```

Expose:

```python
def sync_cloud_run_log_stream(
    session: Session,
    *,
    cloud_run: CloudRun,
) -> RemoteRuntimeLogSyncResult:
    ...
```

The route flow becomes:

1. Load `CloudRun`.
2. If `include_stream` and `sync_stream` are both true, call
   `sync_cloud_run_log_stream`.
3. If sync returns `updated`, persist the returned `log_stream_ref` into
   `CloudRun.log_stream_*` and flush.
4. Return the existing Phase 12A log window using the normal cursor and stream
   read logic.

Sync failures are bounded:

- provider lookup/configuration errors do not fail `/logs/window`;
- provider API errors do not fail `/logs/window`;
- object storage write failures do not fail `/logs/window`;
- the service logs non-sensitive diagnostics and returns the pre-sync window.

This preserves the existing polling contract. A user should still see
control-plane logs even if the provider log API is unavailable.

## Object Storage And Integrity

Provider sync writes refreshed logs through `ObjectStorageProvider.put_text`.
The API never mutates an existing object in place. Each changed snapshot gets a
new content-addressed object ref and replaces the run's `log_stream_*` metadata.

Unchanged content should not churn object refs. A provider can compare the
new text digest with `current_log_stream_ref.sha256`; if equal, it returns
`unchanged`.

The Phase 12A read safety still applies:

- stream reads remain capped by `MAX_LOG_STREAM_READ_BYTES`;
- unsupported schemes are skipped;
- missing metadata is skipped;
- read integrity checks remain enforced;
- stream lines and stream URIs remain redacted before returning to clients.

## Error Handling And Security

The user-facing endpoint should prefer partial availability over hard failure.
Only request validation errors, missing cloud runs, and invalid cursors should
return HTTP errors. Provider log sync is best effort and must not expose:

- Aliyun access keys;
- callback tokens;
- bearer tokens;
- signed object URLs;
- raw provider stack traces;
- unredacted authorization headers.

Expected provider limitations, such as missing `runtime_job_id`, missing
storage provider, missing Aliyun config, or unsupported runtime provider, return
an internal `skipped` or `unsupported` sync result. These do not append
unbounded control-plane log rows on every poll.

## Testing

Add focused tests before implementation:

- `sync_stream=false` keeps Phase 12A behavior and does not call provider sync.
- `include_stream=false&sync_stream=true` does not call provider sync.
- `remote_stub` sync updates `CloudRun.log_stream_*` and the window returns the
  deterministic refreshed stream line.
- repeated `remote_stub` sync is unchanged and does not churn metadata.
- `aliyun_eci` sync uses the fake ECI `describe_container_log` seam and writes
  returned logs through OSS.
- Aliyun provider errors degrade to the pre-sync window.
- oversized provider log snapshots are not returned by the window reader.
- stream redaction still handles bearer tokens, token-like key/value pairs, and
  URI query/fragment removal.

Run at minimum:

```text
pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or log_sync or phase_12b" -v
pytest apps/api/tests/test_aliyun_clients.py -v
pytest apps/api/tests/test_cloud_object_storage.py -v
pnpm --filter @ai-scdc/desktop test -- client.test.ts
pnpm typecheck
```

## Documentation Updates

After implementation, update:

- `docs/architecture.md` to add a Phase 12B boundary and move provider-native
  log sync from Future to Completed;
- `docs/superpowers/status.md` with current verification commands and remaining
  known limits;
- Aliyun smoke/runbook docs if the new `DescribeContainerLog` permission needs
  to be called out in RAM policy examples.

## Non-Goals

Phase 12B does not:

- introduce WebSocket or SSE tailing;
- guarantee millisecond live streaming;
- provision SLS projects, Logstores, or shipping rules;
- implement direct MNS receive/delete worker semantics;
- change worker callback token authorization;
- change artifact upload or completion semantics;
- add desktop task-board log rendering beyond API-client compatibility if
  needed;
- split provider modules out of `apps/api` into a separate package.

## Success Criteria

Phase 12B is complete when:

- `/logs/window` remains backward compatible;
- `sync_stream=true` can refresh provider logs through the runtime provider
  boundary;
- deterministic providers remain deterministic;
- Aliyun ECI has a tested `DescribeContainerLog` client seam;
- provider failures do not break log polling;
- refreshed stream reads still use object-storage integrity metadata;
- targeted backend tests, desktop client tests, and typecheck pass.
