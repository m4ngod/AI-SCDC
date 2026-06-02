# Phase 10A Remote Worker Control Plane Design

## Purpose

Phase 10A turns the Phase 9 local cloud-run worker boundary into a remote-worker
control-plane contract. It does not launch real cloud infrastructure yet. The
goal is to define how external workers claim work, keep a lease alive, report
completion, handle cancellation, and recover expired work while preserving the
existing fake and `docker_local` development adapters.

The product flow becomes:

```text
User starts cloud run
  -> API validates and creates queued CloudRun
  -> queue adapter exposes eligible work
  -> worker claims a lease
  -> worker heartbeats while executing
  -> worker completes with execution result or cancellation
  -> API persists logs, artifacts, test runs, and final task state
```

## Current Baseline

Phase 9 already provides the durable enqueue and local worker boundary:

- `POST /tasks/{task_id}/cloud-runs` creates a queued `CloudRun` and returns
  immediately.
- `POST /cloud-run-worker/process-next` and
  `POST /cloud-runs/{cloud_run_id}/process` synchronously claim and process work
  inside the API process for local development and tests.
- Queued cancellation, running cancellation requests, and ordered redacted logs
  are persisted.
- Running cancellation races are resolved so cancellation wins before artifact
  finalization, while terminal runs are not mutated by late cancel requests.

This is a good local contract, but it is not yet a remote-worker contract:
workers do not own renewable leases, missed heartbeats cannot requeue work, and
there is no queue adapter boundary for replacing the local DB polling behavior
with Redis, SQS, or another external runtime.

## Selected Approach

Phase 10A adds a small remote-worker control plane on top of the Phase 9 data
model.

1. Add a `CloudRunQueueAdapter` interface with a local SQLite-backed
   implementation as the default.
2. Extend `CloudRun` with lease, heartbeat, attempt, and provider metadata.
3. Add worker lease endpoints for claim, heartbeat, completion, cancellation
   acknowledgement, and expired lease requeue.
4. Keep the existing Phase 9 process endpoints as development shortcuts.
5. Add a `remote_stub` worker adapter for tests and smoke flows. It exercises
   lease and heartbeat semantics without creating remote VMs or containers.
6. Keep fake and `docker_local` executors as development execution backends.

This approach keeps Phase 10A small and testable. The system gains the contract
needed by a remote worker fleet without committing to a cloud vendor or queue
provider.

## Non-Goals

Phase 10A does not:

- Provision or run real cloud VMs, Kubernetes jobs, ECS tasks, or Cloud Run jobs.
- Add Redis, SQS, Celery, Dramatiq, or another production queue as a required
  dependency.
- Store large logs, diffs, or artifacts in object storage.
- Add WebSocket or SSE live streaming.
- Pass raw GitHub tokens or model API keys to an untrusted remote worker.
- Add automatic PR creation, automatic merge, model-backed reviewer agents, or
  model-backed debugger agents.
- Add production authentication, organization RBAC, subscriptions, billing, or
  rate limiting.

## Data Model

### CloudRun

Extend the existing `CloudRun` record. Keep the current status values:

- `queued`
- `running`
- `patch_ready`
- `failed`
- `cancelled`

Add fields:

- `queue_provider`: string, default `local_db`. Records which queue adapter owns
  the run.
- `remote_worker_kind`: nullable string. Examples: `remote_stub`,
  `docker_local`, `cloud_vm`.
- `lease_id`: nullable string. Opaque token owned by the active worker lease.
- `lease_expires_at`: nullable datetime. The run can be requeued after this
  time if it is still non-terminal.
- `heartbeat_at`: nullable datetime. Last accepted worker heartbeat.
- `attempt_count`: integer, default `0`. Incremented when a worker successfully
  claims a new lease for execution.
- `max_attempts`: integer, default `3`. Prevents infinite requeue loops.
- `last_queue_error`: nullable string. Stores a compact queue/lease failure code
  for diagnostics.

The active lease is the authorization boundary for worker completion. A worker
can only heartbeat or complete a run when its `lease_id` matches the current
`CloudRun.lease_id` and the run is still `running`.

### CloudRunLogEntry

Keep the Phase 9 log table and add new event names:

- `lease_claimed`
- `lease_heartbeat`
- `lease_released`
- `lease_expired`
- `run_requeued`
- `worker_completed`
- `worker_completion_rejected`

Log payloads remain redacted through the existing redaction helper. Lease IDs
are safe to log only as truncated values such as the last six characters.

## Queue Adapter Contract

Create a queue adapter interface in the cloud runner service layer:

```python
class CloudRunQueueAdapter(Protocol):
    def enqueue(self, session: Session, *, cloud_run: CloudRun) -> None: ...
    def claim_next(
        self,
        session: Session,
        *,
        worker_id: str,
        worker_kind: str,
        lease_seconds: int,
    ) -> CloudRunLease | None: ...
    def heartbeat(
        self,
        session: Session,
        *,
        lease_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> CloudRunLease: ...
    def complete(
        self,
        session: Session,
        *,
        lease_id: str,
        worker_id: str,
        result: SandboxExecutionResult,
    ) -> CloudRunResultRead: ...
    def requeue_expired(
        self,
        session: Session,
        *,
        now: datetime,
        limit: int,
    ) -> list[CloudRunRead]: ...
```

The default `local_db` adapter uses conditional SQL updates for compare-and-set
behavior. It must only claim rows where:

- `status = queued`
- `cancel_requested = false`
- `attempt_count < max_attempts`

It must only heartbeat and complete rows where:

- `status = running`
- `lease_id` matches
- `worker_id` matches
- `completed_at IS NULL`

If a heartbeat or completion arrives with a stale lease, the API returns `409`
and records `worker_completion_rejected` for completion attempts.

## API Design

### Claim Next Lease

`POST /cloud-run-worker/leases`

Request:

```json
{
  "worker_id": "worker-dev-1",
  "worker_kind": "remote_stub",
  "lease_seconds": 60
}
```

Response:

- `204 No Content` if no eligible run exists.
- `201 CloudRunLeaseRead` if a run was claimed.

`CloudRunLeaseRead` includes:

- `cloud_run`
- `lease_id`
- `lease_expires_at`
- `heartbeat_at`
- `attempt_count`
- `cancel_requested`

The response does not include raw GitHub credentials. Phase 10A remote stubs use
the already persisted run metadata and fake execution result contracts. Real
credential brokering belongs to the remote execution phase after this control
plane is proven.

### Heartbeat Lease

`POST /cloud-run-worker/leases/{lease_id}/heartbeat`

Request:

```json
{
  "worker_id": "worker-dev-1",
  "lease_seconds": 60
}
```

Response:

- `200 CloudRunLeaseRead` when the lease is still current.
- `409` when the lease is stale, expired, already terminal, or owned by another
  worker.

The response includes `cancel_requested`. A cooperative remote worker should
stop starting new executor steps after this value becomes true and then complete
the lease as `cancelled`.

### Complete Lease

`POST /cloud-run-worker/leases/{lease_id}/complete`

Request:

```json
{
  "worker_id": "worker-dev-1",
  "result": {
    "status": "patch_ready",
    "runner_kind": "remote_stub",
    "base_sha": "abc123",
    "head_sha": "def456",
    "worktree_ref": "remote-stub://cloud_run_123",
    "summary": "Remote stub produced a patch artifact.",
    "files_changed": ["AI_SCDC_REMOTE_STUB.md"],
    "tests_run": [],
    "test_result": "not_run",
    "risks": [],
    "diff_text": "diff --git ...",
    "command_results": [],
    "test_command_results": [],
    "failure_reason": null
  }
}
```

Response:

- `200 CloudRunResultRead` when the lease is current and finalization succeeds.
- `409` when the lease is stale or already terminal.

Completion reuses the existing Phase 9 artifact and task-transition logic. If
`cancel_requested=true` before finalization, the API stores a cancelled result
and does not create a patch artifact.

### Requeue Expired Leases

`POST /cloud-run-worker/leases/requeue-expired`

Request:

```json
{
  "limit": 25
}
```

Behavior:

- Non-terminal `running` runs with expired leases become `queued` when
  `attempt_count < max_attempts`.
- Runs at `max_attempts` become `failed` with
  `failure_reason=lease_attempts_exhausted`.
- Requeue appends `lease_expired` and `run_requeued` logs.

This endpoint is explicit in Phase 10A. A production queue scheduler can call
the same service function later.

### Existing Endpoints

Keep Phase 9 endpoints:

- `POST /cloud-run-worker/process-next`
- `POST /cloud-runs/{cloud_run_id}/process`
- `POST /cloud-runs/{cloud_run_id}/cancel`
- `GET /cloud-runs/{cloud_run_id}/logs`

The process endpoints become development shortcuts implemented in terms of the
same queue adapter and finalization functions where practical.

## Worker Lifecycle

Happy path:

```text
queued CloudRun
  -> claim lease: status=running, attempt_count += 1, lease_id set
  -> heartbeat extends lease_expires_at
  -> complete with SandboxExecutionResult
  -> API creates patch artifact or failed result
  -> status becomes patch_ready, failed, or cancelled
```

Expired lease path:

```text
running CloudRun with expired lease
  -> requeue-expired sees lease_expires_at < now
  -> old lease_id cleared
  -> worker_id cleared
  -> status returns to queued, or failed if max attempts reached
```

Cancellation path:

```text
queued run cancel
  -> cancelled immediately

running run cancel
  -> cancel_requested=true
  -> heartbeat response tells worker cancellation was requested
  -> worker completes as cancelled, or API cancels at finalization if worker
     returns a patch result after the request
```

Stale worker path:

```text
worker A lease expires
  -> run requeued
  -> worker B claims new lease
  -> worker A completion arrives
  -> completion rejected with 409 because lease_id no longer matches
```

## Remote Stub Adapter

Add `remote_stub` as a test-only worker kind. It does not open Docker, create a
VM, or access secrets. It exists to exercise the remote control-plane lifecycle.

The remote stub can:

- Claim a lease through the new lease endpoint.
- Send one or more heartbeats.
- Complete with a deterministic `SandboxExecutionResult`.
- Complete as cancelled when heartbeat reports `cancel_requested=true`.
- Simulate stale completion by trying to complete after requeue.

The fake and `docker_local` executors stay available for development. The
remote stub is a contract test adapter, not a production execution backend.

## Desktop Impact

Phase 10A does not require a new desktop workflow. The existing task board can
continue showing:

- queued cloud runs
- running cloud runs
- compact logs
- `Process` and `Cancel` controls

Optional display additions are limited to compact diagnostics:

- worker id
- lease expiry
- heartbeat age
- attempt count

These diagnostics should not create new primary workflow controls until the
remote worker runtime exists.

## Testing Strategy

Add backend tests first:

1. Claiming a queued run creates a lease and increments `attempt_count`.
2. Two workers cannot claim the same run.
3. Heartbeat extends `lease_expires_at` for the current lease.
4. Heartbeat with a stale lease returns `409`.
5. Completing a current lease creates the same patch artifact shape as Phase 9.
6. Completing a stale lease returns `409` and does not create an artifact.
7. Expired leases requeue below `max_attempts`.
8. Expired leases fail at `max_attempts`.
9. Running cancellation is returned by heartbeat and prevents artifact
   finalization.
10. The Phase 9 process endpoints still work through the local development
    path.

Desktop tests only need to cover new displayed diagnostics if they are added.

## Rollout

Phase 10A ships behind the existing local development defaults:

- `queue_provider=local_db`
- `remote_worker_kind=remote_stub` only in tests or explicit smoke flows
- Phase 9 process endpoints retained

After Phase 10A, the next implementation phase can add one real queue provider
and one real remote worker runtime without changing the API-visible cloud-run
state machine.

## Success Criteria

Phase 10A is complete when:

- `CloudRun` records can be claimed through a renewable lease contract.
- Stale leases cannot heartbeat or complete work.
- Expired leases can be requeued or failed after max attempts.
- Running cancellation remains authoritative before final artifact creation.
- Existing fake and `docker_local` development processing still pass.
- The design leaves credential brokering, object storage, and live streaming as
  explicit later phases rather than implicit hidden requirements.
