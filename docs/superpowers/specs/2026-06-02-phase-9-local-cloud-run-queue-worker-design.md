# Phase 9 Local Cloud Run Queue Worker Design

## Purpose

Phase 9 moves cloud-run execution out of the synchronous API request path while
keeping the execution backend local and deterministic. The goal is to establish
the queue, worker, cancellation, and log contracts needed for future remote
cloud sandbox workers without adding Redis, Celery, object storage, remote VMs,
or production scheduling infrastructure.

The product flow becomes:

```text
User starts cloud run
  -> API validates and enqueues CloudRun
  -> worker claims queued CloudRun
  -> worker executes fake or docker_local sandbox
  -> worker stores logs, patch artifact, test run, and final status
  -> existing review, approval, human approval, and PR flow continues
```

## Current Baseline

Phase 8 has a working `CloudRun` control-plane record and two executor choices:

- `fake`, the deterministic fake cloud sandbox.
- `docker_local`, the real local Docker sandbox executor.

The current `start_cloud_run()` service validates the request, creates
`CloudRun` and `LocalTaskRun`, immediately runs the selected executor inside the
API request, stores artifacts/logs, and returns only after execution finishes.

This proves the execution contract, but it is not a cloud-worker architecture:
long-running Docker work blocks the API request, users cannot cancel queued
work, and run progress is only visible after the request completes.

## Selected Approach

Phase 9 adds a local queue/worker vertical slice.

1. Keep `CloudRun` as the API-visible run record.
2. Change `POST /tasks/{task_id}/cloud-runs` to enqueue work and return quickly.
3. Add a worker service that claims and processes queued runs explicitly.
4. Keep the existing fake and Docker executors as worker backends.
5. Add durable run logs that can be listed through the API.
6. Add a cancellation request boundary for queued and running runs.
7. Keep polling as the first UI/API observation mechanism.

The worker is local-first and explicitly triggered. It can be called from tests,
dev tooling, or a future background process, but Phase 9 does not add a daemon
or external queue runtime.

## Non-Goals

Phase 9 does not:

- Add Redis, Celery, Dramatiq, Arq, or another queue framework.
- Add remote cloud VM/container workers.
- Add object storage for large artifacts.
- Add WebSocket or SSE streaming.
- Interrupt a running Docker process mid-command.
- Add model-backed patch generation, reviewer, or debugger agents.
- Change the patch approval, human approval, or PR creation boundaries.
- Add production auth, organization RBAC, billing, or rate limits.

## Data Model

### CloudRun

Extend the existing `CloudRun` record rather than creating a parallel job table.

Status values:

- `queued`: run has been accepted and is waiting for worker processing.
- `running`: worker has claimed the run and is executing it.
- `patch_ready`: execution produced a usable patch artifact.
- `failed`: execution completed with a failure reason.
- `cancelled`: run was cancelled before executor work started.

Add fields:

- `cancel_requested`: boolean, default `false`.
- `cancel_requested_at`: nullable datetime.
- `cancelled_at`: nullable datetime.
- `worker_id`: nullable string identifying the worker that claimed the run.
- `claimed_at`: nullable datetime.
- `completed_at`: nullable datetime.

`cancel_requested=true` on a `running` run records human intent. The first
worker implementation checks cancellation before starting execution and after
executor completion, but it does not stop an in-flight Docker command. Hard
process interruption belongs to a later executor-cancellation phase.

### CloudRunLogEntry

Add a compact log table for progress and safe diagnostics:

- `id`
- `workspace_id`
- `project_id`
- `task_id`
- `cloud_run_id`
- `sequence`
- `level`: `info`, `warning`, or `error`
- `message`
- `payload`: JSON object with redacted structured metadata
- `created_at`

Sequence is monotonic per `cloud_run_id`. Logs are append-only and safe for UI
display. They must not include raw GitHub tokens, model keys, or repository URL
credentials.

## API Design

### Start Cloud Run

`POST /tasks/{task_id}/cloud-runs`

Current behavior: validates, creates records, runs executor synchronously, and
returns final execution result.

Phase 9 behavior: validates, creates `CloudRun` in `queued`, creates companion
`LocalTaskRun` in `queued`, stores selected sandbox profile/command metadata,
appends a `cloud_run_queued` log entry, and returns immediately with
`patch_artifact=null`.

The response shape remains `CloudRunResultRead`:

```json
{
  "cloud_run": {
    "status": "queued",
    "sandbox_kind": "docker_local"
  },
  "patch_artifact": null
}
```

### Process Next Run

`POST /cloud-run-worker/process-next`

Processes the oldest queued run. Query/body options may include:

- `worker_id`, defaulting to `local_worker`.
- `sandbox_kind`, optional filter.

Response:

- `204 No Content` if no queued run exists.
- `200 CloudRunResultRead` when a run was claimed and processed.

This endpoint is a development/testing control-plane hook. It is not a public
commercial API surface.

### Process Specific Run

`POST /cloud-runs/{cloud_run_id}/process`

Claims and processes one queued run. This is useful for deterministic tests and
manual smoke runs. It returns `409` if the run is not `queued`.

### Request Cancellation

`POST /cloud-runs/{cloud_run_id}/cancel`

Behavior:

- `queued -> cancelled`: no executor work runs, companion `LocalTaskRun` becomes
  `cancelled`, `cancelled_at` is set, and logs record `cloud_run_cancelled`.
- `running`: sets `cancel_requested=true`, records `cancel_requested_at`, and
  logs `cloud_run_cancel_requested`.
- terminal statuses return the existing run state without changing it.

The first version does not kill a running Docker process. It records the request
and leaves executor interruption for a later phase.

### List Logs

`GET /cloud-runs/{cloud_run_id}/logs`

Returns ordered `CloudRunLogEntry` records. Polling is the Phase 9 observation
mechanism.

## Worker Design

Create a focused worker service, separate from the API enqueue function:

- `enqueue_cloud_run(session, task_id, data) -> CloudRunResultRead`
- `process_next_cloud_run(session, worker_id="local_worker") -> CloudRunResultRead | None`
- `process_cloud_run(session, cloud_run_id, worker_id="local_worker") -> CloudRunResultRead`
- `cancel_cloud_run(session, cloud_run_id) -> CloudRunRead`
- `list_cloud_run_logs(session, cloud_run_id) -> list[CloudRunLogEntryRead]`

The worker claim algorithm is intentionally simple for SQLite-backed local
development:

1. Select the oldest `queued` run by `created_at, id`.
2. Refresh and verify it is still `queued`.
3. Set `status=running`, `worker_id`, `claimed_at`, `updated_at`.
4. Set companion `LocalTaskRun.status=running`.
5. Commit before executor work starts.

This is not a distributed lock. It is enough for Phase 9 local/manual worker
processing and keeps the later Redis/remote-worker migration straightforward.

## Execution Flow

Worker happy path:

```text
process queued run
  -> claim run
  -> append cloud_run_started log
  -> rebuild SandboxExecutionRequest from persisted task/repo/profile metadata
  -> run selected executor
  -> persist command logs and test logs
  -> create PatchArtifact and LocalTestRun when applicable
  -> transition task to PATCH_READY when applicable
  -> append patch_artifact_created or cloud_run_failed log
  -> set completed_at
```

The existing Phase 8 artifact semantics remain unchanged:

- No-artifact failures produce failed `CloudRun` and no `PatchArtifact`.
- Test failures may produce `PatchArtifact` plus failed `LocalTestRun`.
- Successful runs produce `PatchArtifact`, command results, and `PATCH_READY`.
- Existing review/debug/approval/PR services consume the same records as before.

## Error Handling

Failure reason codes remain compatible with Phase 8:

- `docker_unavailable`
- `repo_checkout_failed`
- `patch_command_failed`
- `no_patch_produced`
- `test_failed`
- `artifact_capture_failed`
- `executor_failed`

Add queue/worker-specific handling:

- `run_not_queued`: returned as HTTP 409 when processing a non-queued run.
- `cloud_run_not_found`: returned as HTTP 404 when a run does not exist.
- `cancelled`: stored as status, not a failure reason, when queued work is
  cancelled before execution.

Every worker transition appends a safe log entry. Logs and command payloads are
redacted before persistence.

## Desktop Design

Keep the desktop changes minimal:

- Starting a cloud run should show `queued` immediately.
- The task board should show `queued`, `running`, `patch_ready`, `failed`, and
  `cancelled` cloud-run states.
- A compact log view can poll `GET /cloud-runs/{cloud_run_id}/logs`.
- A cancel control can call `POST /cloud-runs/{cloud_run_id}/cancel`.
- No live streaming UI is required in Phase 9.

If the desktop does not yet run a worker process, the UI can expose queued state
and rely on dev/test worker endpoints for processing. A later phase can add a
desktop-managed worker loop.

## Test Strategy

Backend unit and API tests:

- Starting a fake cloud run returns `queued` with no patch artifact.
- Starting a Docker cloud run stores sandbox profile and command keys but does
  not call the executor during enqueue.
- Processing next queued fake run creates a patch artifact and moves the task to
  `PATCH_READY`.
- Processing a specific queued Docker run with a stub executor preserves Phase 8
  artifact and test-run semantics.
- Processing a non-queued run returns 409.
- Cancelling a queued run moves it to `cancelled` and prevents processing.
- Cancelling a running run records `cancel_requested=true` without changing
  terminal state.
- Listing logs returns ordered safe log entries.
- Command results and logs redact GitHub token values.

Desktop tests:

- Cloud run creation displays queued state.
- Cloud run status mapping supports `cancelled`.
- Log entries render in order.
- Cancel button calls the cancel endpoint and updates state.

Full verification:

- `pnpm test:js`
- `pnpm typecheck`
- `pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -q`
- `git diff --check`

## Acceptance Criteria

- `POST /tasks/{task_id}/cloud-runs` returns a queued run without synchronous
  executor work.
- A local worker endpoint can process the next queued run into existing
  artifact/review flow records.
- A specific queued run can be processed deterministically for tests and smoke.
- Queued cancellation prevents executor work.
- Running cancellation is durably recorded.
- Cloud-run logs are append-only, ordered, and safe for UI display.
- Existing fake and `docker_local` executor behavior remains compatible.
- Phase 9 does not add Redis, Celery, remote workers, object storage, or live
  streaming.

## Future Extensions

- Move worker claiming to Redis or a production queue.
- Run workers as separate processes with heartbeat and lease expiration.
- Add hard cancellation for Docker commands and remote containers.
- Add object storage for large command logs and diff artifacts.
- Add SSE/WebSocket live log streaming.
- Add remote VM/container sandbox providers.
- Add per-run resource limits and scheduling policies.
