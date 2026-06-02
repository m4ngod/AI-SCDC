# Phase 10B Provider-Neutral Remote Contract Design

## Purpose

Phase 10B turns the Phase 10A remote-worker lease API into a provider-neutral
production execution-plane contract. It does not connect to a real cloud
provider yet. The goal is to define queue, storage, and runtime provider
boundaries that can later be implemented by AWS, GCP, Azure, Aliyun, or another
execution platform without changing the API-visible cloud-run lifecycle.

The target flow becomes:

```text
User starts cloud run
  -> API creates CloudRun and provider metadata
  -> queue provider enqueues or exposes claimable work
  -> remote worker claims and heartbeats a current lease
  -> worker writes large logs/diffs/artifacts through storage provider refs
  -> worker completes with execution result and optional artifact refs
  -> API resolves refs, finalizes the existing patch/test/task state
```

## Current Baseline

Phase 10A provides the control-plane lease contract:

- `CloudRun` records include queue, lease, heartbeat, attempt, and worker
  metadata.
- `POST /cloud-run-worker/leases` claims queued work with compare-and-set style
  updates.
- `POST /cloud-run-worker/leases/{lease_id}/heartbeat` renews current leases and
  reports cancellation requests.
- `POST /cloud-run-worker/leases/{lease_id}/complete` accepts current lease
  completion and reuses the existing Phase 9 finalization path.
- `POST /cloud-run-worker/leases/requeue-expired` requeues expired leases or
  fails exhausted attempts.
- Fake, `docker_local`, and `remote_stub` development adapters remain available.

This is enough to exercise a remote worker lifecycle, but it still stores all
queue and artifact behavior inside the local API database and inline completion
payloads. There is no provider boundary for external queues, object storage, or
remote runtime orchestration.

## Selected Approach

Phase 10B adds provider-neutral contracts first.

1. Add explicit queue, storage, and runtime provider abstractions.
2. Keep `local_db` as the default queue provider and add an `external_stub`
   queue provider that simulates external queue message lifecycle without a
   cloud dependency.
3. Add a local deterministic object-storage provider for text and JSON payload
   refs. It validates content hashes and sizes but does not require S3, GCS,
   OSS, or MinIO.
4. Extend remote completion to accept artifact refs while keeping inline result
   payloads for development and backward compatibility.
5. Add a `remote_stub` runtime provider that records runtime metadata but does
   not launch a VM, container, or daemon.
6. Preserve all Phase 9 and Phase 10A endpoints and behavior.

This approach gives the product a stable production execution-plane contract
without prematurely choosing or embedding a specific cloud vendor.

## Non-Goals

Phase 10B does not:

- Provision real VMs, Kubernetes jobs, ECS tasks, Cloud Run jobs, or Aliyun jobs.
- Require Redis, SQS, Pub/Sub, Celery, Dramatiq, Kafka, or another external
  queue dependency.
- Require S3, GCS, OSS, MinIO, or another object storage service.
- Add a desktop-managed worker daemon loop.
- Add WebSocket or SSE live log streaming UI.
- Broker raw GitHub tokens, model API keys, or cloud credentials to untrusted
  remote workers.
- Add cloud-provider-specific IAM, KMS, secrets manager, or account setup.
- Add automatic PR creation, automatic merge, billing, or production RBAC.

## Provider Boundaries

### Queue Provider

`CloudQueueProvider` owns queue lifecycle only. It does not execute tasks and
does not store artifact payloads.

Provider contract:

```python
class CloudQueueProvider(Protocol):
    provider_name: str

    def enqueue(self, session: Session, cloud_run: CloudRun) -> CloudRun:
        ...

    def claim_next(
        self,
        session: Session,
        *,
        worker_id: str,
        worker_kind: str,
        lease_seconds: int,
    ) -> CloudRunLeaseRead | None:
        ...

    def heartbeat(
        self,
        session: Session,
        *,
        lease_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> CloudRunLeaseRead:
        ...

    def complete(
        self,
        session: Session,
        *,
        lease_id: str,
        worker_id: str,
        result: CloudRunExecutionResultCreate,
    ) -> CloudRunResultRead:
        ...

    def requeue_expired(
        self,
        session: Session,
        *,
        limit: int,
    ) -> list[CloudRunRead]:
        ...
```

Initial providers:

- `local_db`: wraps the existing Phase 10A implementation.
- `external_stub`: records provider-neutral queue metadata while still using
  deterministic local test storage.

The `external_stub` provider must create stable external message identifiers,
claim receipts, and ack/release state transitions so future SQS, Redis, or
Pub/Sub providers can implement the same behavior.

### Object Storage Provider

`ObjectStorageProvider` owns large text/JSON payload refs. It does not finalize
cloud runs and does not decide task status.

Provider contract:

```python
class ObjectStorageProvider(Protocol):
    provider_name: str

    def put_text(
        self,
        session: Session,
        *,
        kind: str,
        content: str,
        content_type: str = "text/plain",
    ) -> CloudRunArtifactRefCreate:
        ...

    def get_text(
        self,
        session: Session,
        ref: CloudRunArtifactRefCreate,
    ) -> str:
        ...

    def put_json(
        self,
        session: Session,
        *,
        kind: str,
        payload: dict,
    ) -> CloudRunArtifactRefCreate:
        ...

    def get_json(
        self,
        session: Session,
        ref: CloudRunArtifactRefCreate,
    ) -> dict:
        ...
```

Initial provider:

- `local_inline`: stores deterministic payloads inside local database records or
  local process memory owned by tests. It returns `local-inline://...` URIs.

The provider validates `sha256` and `size_bytes` on read. A ref with mismatched
hash or size is rejected before completion finalization.

### Runtime Provider

`RemoteRuntimeProvider` records and inspects remote job metadata. It does not
own queue lease semantics and does not store artifacts.

Provider contract:

```python
class RemoteRuntimeProvider(Protocol):
    provider_name: str

    def submit(
        self,
        session: Session,
        *,
        cloud_run: CloudRun,
    ) -> RemoteRuntimeSubmission:
        ...

    def cancel(
        self,
        session: Session,
        *,
        runtime_job_id: str,
    ) -> RemoteRuntimeStatus:
        ...

    def inspect(
        self,
        session: Session,
        *,
        runtime_job_id: str,
    ) -> RemoteRuntimeStatus:
        ...
```

Initial provider:

- `remote_stub`: records `runtime_provider`, `runtime_job_id`, and deterministic
  status metadata. It does not start a real worker process.

## Data Model

Extend `CloudRun` with provider-neutral external execution metadata:

- `queue_message_id: str | None`
- `queue_receipt: str | None`
- `runtime_provider: str | None`
- `runtime_job_id: str | None`
- `storage_provider: str | None`
- `artifact_manifest_uri: str | None`
- `log_stream_uri: str | None`
- `external_status: str | None`
- `external_error: str | None`

Existing fields keep their current meaning:

- `queue_provider` continues to default to `local_db`.
- `remote_worker_kind` continues to identify the worker kind that claimed the
  lease.
- `lease_id`, `lease_expires_at`, `heartbeat_at`, `attempt_count`,
  `max_attempts`, and `last_queue_error` remain Phase 10A lease fields.

If SQLite upgrade helpers are still used, they must add the new nullable fields
without changing existing rows. Existing cloud runs default to:

```python
queue_message_id = None
queue_receipt = None
runtime_provider = None
runtime_job_id = None
storage_provider = None
artifact_manifest_uri = None
log_stream_uri = None
external_status = None
external_error = None
```

## API and Schemas

### CloudRunRead

`CloudRunRead` returns non-sensitive provider metadata:

- `queue_message_id`
- `runtime_provider`
- `runtime_job_id`
- `storage_provider`
- `artifact_manifest_uri`
- `log_stream_uri`
- `external_status`
- `external_error`

`queue_receipt` is not returned by default because real providers may treat
receipts as sensitive claim tokens. If a future provider needs to expose a
receipt to a worker, it should do so only through worker-scoped lease responses.

### CloudRunArtifactRef

Add a completion artifact ref schema:

```python
class CloudRunArtifactRefCreate(BaseModel):
    kind: Literal["diff", "log", "command_result", "test_result", "manifest"]
    uri: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    content_type: str = "text/plain"
```

`uri` must be accepted only when its scheme is allowed for the selected storage
provider. Initial accepted scheme:

- `local-inline://`

Future accepted schemes are provider-specific and belong in the provider
implementation:

- `s3://`
- `gs://`
- `oss://`

### CloudRunExecutionResultCreate

Extend remote completion input:

```python
artifact_refs: list[CloudRunArtifactRefCreate] = Field(default_factory=list)
```

Inline fields such as `diff_text`, `command_results`, and
`test_command_results` remain supported. For `remote_stub`, inline completion
continues to be valid. When both inline `diff_text` and a `diff` artifact ref
are present, the service uses the artifact ref for diff content and records a
log entry noting that the inline diff was ignored.

### Provider Selection

Cloud-run creation may continue to use existing request fields. Phase 10B adds
optional provider selection fields:

```python
queue_provider: str = "local_db"
runtime_provider: str | None = None
storage_provider: str | None = None
```

Provider names are validated against registered provider factories. Unknown
providers return `400` with a non-secret error message.

## Service Behavior

### Enqueue

Creating a cloud run:

1. Creates the `CloudRun` row.
2. Selects the configured queue provider.
3. Calls `queue_provider.enqueue(session, cloud_run)`.
4. Persists provider metadata such as `queue_message_id`.
5. Returns `CloudRunRead`.

For `local_db`, enqueue behavior remains Phase 10A-compatible.

For `external_stub`, enqueue creates deterministic queue metadata but does not
publish to an external system.

### Claim, Heartbeat, Complete, Requeue

Existing worker endpoints keep the same paths and response semantics. Their
service implementations delegate to the selected queue provider:

- `POST /cloud-run-worker/leases`
- `POST /cloud-run-worker/leases/{lease_id}/heartbeat`
- `POST /cloud-run-worker/leases/{lease_id}/complete`
- `POST /cloud-run-worker/leases/requeue-expired`

The default provider is `local_db`, so existing Phase 10A tests and smoke flows
continue to pass.

### Completion With Artifact Refs

When a worker completes a current lease with artifact refs:

1. Validate the lease is current and not expired.
2. Validate every artifact ref for allowed scheme, hash, and size.
3. Load `diff` ref content when present.
4. Convert the completion payload into `SandboxExecutionResult`.
5. Reuse existing cloud-run finalization.
6. Persist `artifact_manifest_uri` and `storage_provider` where applicable.

If a required ref cannot be loaded or validated, completion returns a `400` or
`409` before artifact finalization and does not create a patch artifact.

## Security and Redaction

Phase 10B must keep the worker boundary secret-free.

- API requests accept provider names and refs, not raw cloud credentials.
- `queue_receipt` is treated as sensitive internal metadata.
- URI query strings are redacted before logs or read schemas.
- `external_error` is redacted before storage.
- Command output and test output redaction continues to use existing redaction
  helpers.
- Artifact ref `sha256` and `size_bytes` are verified before use.
- Inline completion payload size limits are introduced for remote completion:
  - `diff_text`: 2 MiB
  - `summary`: 16 KiB
  - `external_error`: 16 KiB
  - each command stdout/stderr: 512 KiB

Payloads that exceed these limits return `413` or a validation error before
finalization. Larger payloads must use artifact refs.

## Testing Strategy

Tests should prove the contract, not cloud-vendor behavior.

### API and Schema Tests

- `CloudRunRead` includes non-sensitive provider metadata.
- `queue_receipt` is not returned in standard read schemas.
- Unknown queue, runtime, or storage provider names are rejected.
- Artifact refs reject unknown URI schemes, invalid hashes, and mismatched sizes.
- Remote completion status remains limited to `patch_ready` and `failed`.

### Queue Provider Tests

- `local_db` remains compatible with Phase 10A claim, heartbeat, completion, and
  requeue tests.
- `external_stub` enqueue records `queue_message_id`.
- `external_stub` claim records `queue_receipt` and lease metadata.
- `external_stub` heartbeat extends a current lease.
- `external_stub` completion reuses shared finalization.
- `external_stub` requeue handles expired leases below max attempts and
  exhausted attempts.

### Storage Provider Tests

- `local_inline` stores and reads text payloads.
- `local_inline` stores and reads JSON payloads.
- Hash and size validation fails before finalization.
- URI query strings are redacted from logs and `external_error`.

### Completion Tests

- A `diff` artifact ref can produce the same `PatchArtifact` output as inline
  `diff_text`.
- Inline completion still works for `remote_stub`.
- Stale, expired, wrong-worker, and cancelled lease completion cannot create
  artifacts.
- Failed remote completion with refs does not create a patch artifact unless it
  matches the existing test-failed artifact semantics.

### Backward Compatibility Tests

- Phase 9 process endpoints still return successful fake and `docker_local`
  results.
- Phase 10A lease claim, heartbeat, completion, cancellation, and requeue tests
  continue to pass.
- Desktop client tests and root typecheck continue to pass.

## Documentation Updates

When implementation passes, update:

- `docs/architecture.md`: add a Phase 10B boundary and move Phase 10B into the
  completed roadmap.
- `docs/superpowers/status.md`: current phase, completed item, known limits,
  recommended next phase, and verification counts.
- `README.md`: add a compact provider-neutral artifact ref smoke. The smoke must
  state that it uses local/stub providers and does not require cloud accounts.

## Acceptance Criteria

Phase 10B is complete when:

- Provider-neutral queue, storage, and runtime contracts exist in service code.
- `local_db` remains the default queue provider.
- `external_stub` can enqueue, claim, heartbeat, complete, and requeue through
  the same API-visible worker lifecycle.
- `local_inline` storage can persist text/JSON refs and validate hash/size.
- Remote completion can use artifact refs to produce a patch artifact.
- Inline `remote_stub` completion remains supported.
- External provider metadata is persisted and returned only when non-sensitive.
- `queue_receipt` is not exposed in normal read schemas.
- Secrets in URI query strings, external errors, logs, and command payloads are
  redacted.
- The full API suite, selected desktop tests, root typecheck, and `git diff
  --check` pass.

## Future Work After Phase 10B

Phase 10C can attach a concrete provider stack, such as:

- AWS SQS + S3 + ECS/Fargate
- GCP Pub/Sub + GCS + Cloud Run Jobs
- Aliyun MNS/OSS/ECS
- Redis + MinIO + Kubernetes Jobs for private deployments

Those provider integrations should implement the Phase 10B contracts without
changing the public cloud-run worker API.
