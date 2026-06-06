# AI Software Company Desktop Console Architecture

## Product Shape

AI Software Company Desktop Console is a commercial multi-agent software engineering control plane. It is not a collection of chat windows. The long-term architecture is a desktop client, cloud control plane, model router, agent orchestrator, execution plane, and approval/audit system.

## Target Architecture

```text
Tauri Desktop Client
  -> Cloud Control Plane
  -> Agent Orchestrator
  -> LLM Gateway / Model Router
  -> Sandbox Workers / Local Runner
  -> Git Worktree / Tests / Review / PR
```

## Phase 0 Boundary

Phase 0 builds the runnable monorepo foundation:

- Vite React desktop shell.
- FastAPI control-plane API.
- JSON Schema Agent protocol.
- Deterministic LLM gateway interface.
- Mock worker simulator.
- SQLite-backed tests.
- PostgreSQL and Redis reservations in Docker Compose.

Phase 0 does not perform real model calls, real Tauri builds, real code patching, real billing, production auth, or cloud sandbox execution.

## First Runtime Flow

```text
User enters goal
  -> desktop shell requests a planner run
  -> FakePlanner creates structured TaskSpec drafts by default/no-route fallback,
     or a configured model-backed planner creates drafts
  -> user approves or rejects the batch
  -> approved drafts become normal tasks
  -> task events capture audit trail
  -> desktop right panel shows created tasks
```

The desktop task client defaults to mock mode when `VITE_API_BASE_URL` is unset
so demos and tests stay deterministic. Setting
`VITE_API_BASE_URL=http://127.0.0.1:8000` enables the minimal HTTP integration:
the desktop resolves or creates a demo project, creates planner runs, approves
or rejects generated drafts, and maps approved tasks into the right-panel task
board.

## Phase 2 Boundary

Phase 2 adds backend control-plane records for model providers, BYOK credentials, model routes, and usage ledger entries. Route resolution is metadata-only: if no planner route is configured, the API returns a deterministic fake planner route so the Phase 1 planner approval flow keeps working.

Credentials are write-only through the API. The server stores a development encrypted-secret placeholder and returns only credential metadata such as `secret_last4`. Phase 2 does not make real OpenAI-compatible or DeepSeek network calls.

## Phase 3 Boundary

Phase 3 adds the first real model-backed planner path. The API resolves the configured planner model route, opens the reversible development-only BYOK credential internally, calls an OpenAI-compatible chat completions provider through the gateway package, validates JSON TaskSpec drafts, and persists those drafts for human approval.

The existing approval boundary remains intact: model output creates planner drafts only, and tasks are created only after a human approves the planner run. If the route, credential, provider request, or model output is unavailable, the API falls back to `FakePlanner` and records a fallback reason on the planner run.

Phase 3 keeps the gateway in-process, does not add desktop model settings UI, does not use production KMS, and does not calculate real model pricing.

## Phase 4 Boundary

Phase 4 adds the Local Runner vertical slice. A developer can register an existing local git repository, run an approved task in a git worktree under `.worktrees`, capture a reviewable diff artifact, and move the task to `PATCH_READY`.

The review boundary remains intact: Phase 4 does not auto-commit, push, merge, create PRs, or run reviewer/debugger loops. It is a local execution and patch-review foundation for later automation. Patches are constrained by task `allowed_paths`, and approved planner drafts now preserve `allowed_paths` and `required_tests` on created tasks.

## Phase 5 Boundary

Phase 5 adds the deterministic local test, review, and debug-attempt workflow on top of Phase 4 patch artifacts. A patch-ready task now moves through `PATCH_READY -> SELF_TESTING -> REVIEWING -> APPROVED` when tests and deterministic review pass, or to `FIX_REQUESTED` when tests fail or review requests changes. The desktop exposes this as `Run local`, `Run tests`, and `Review patch` controls.

The Local test runner executes each task's `required_tests` commands inside the local runner worktree and records stdout, stderr, exit code, command timing, and failure reasons in `LocalTestRun`. It updates the patch artifact's test metadata and keeps command execution local.

Phase 5 adds durable `LocalTestRun`, `PatchReview`, and `DebugAttempt` records. `LocalTestRun` belongs to a project, task, local run, and patch artifact. `PatchReview` belongs to the same patch boundary, links to the latest test run when available, stores deterministic verdicts and required changes, and has an idempotency uniqueness constraint on `(patch_artifact_id, reviewer_kind)`. Re-running the deterministic review for the same artifact returns the existing review result rather than creating duplicate reviewer output. `DebugAttempt` records root cause and fix summary for failed tests or deterministic review findings; it does not edit files.

The deterministic review rules are intentionally small and auditable: require non-empty diff text, require at least one changed file, verify changed files stay inside task `allowed_paths`, and require the latest local test run to have passed. The workflow remains local and deterministic; Phase 5 does not auto-commit, push, merge, create PRs, call reviewer/debugger models, or automatically modify the worktree during debug.

## Phase 6 Boundary

Phase 6 adds the human patch approval boundary and a compact unified diff viewer. A task that has passed deterministic review can be patch-approved, which records a durable `PatchApproval`, moves the task to `MERGE_READY`, and exposes merge instructions without modifying git state.

The desktop shows changed files, unified diff text, test result, review verdict, patch approval state, worktree path, and merge instructions. A separate human-approval request moves `MERGE_READY -> HUMAN_APPROVAL`; Phase 6 still does not commit, merge, push, apply patches, create branches, or open pull requests.

## Phase 7 Boundary

Phase 7 adds a GitHub-only pull request publishing boundary. A task can run through a deterministic fake cloud sandbox, produce a normal patch artifact, pass the existing local verification/review workflow, receive patch approval, move to `HUMAN_APPROVAL`, and then create a GitHub pull request only after the user clicks `Create PR`. Local/dev mode defaults to a fake GitHub PR adapter; real GitHub publishing is an explicit API startup mode selected with `AI_SCDC_GITHUB_PR_ADAPTER=real`.

The first cloud sandbox is a control-plane fake worker, not a real container service. The API stores GitHub PAT metadata through the development secret vault, registers GitHub repositories, records `CloudRun` and `PullRequestRecord` rows, and moves tasks to `PR_CREATED` after successful PR creation. Phase 7 does not merge pull requests, write to default branches, deploy code, add GitHub OAuth, or add GitLab support.

## Phase 8 Boundary

Phase 8 adds the first real sandbox executor by running GitHub cloud tasks inside local Docker. The executor is still local-first and synchronous, but it establishes the sandbox profile, command whitelist, GitHub clone credential boundary, redacted logs, Docker failure codes, timeout cleanup, and artifact capture contract needed for future remote cloud workers.

## Phase 9 Boundary

Phase 9 moves cloud-run execution out of the synchronous enqueue request path. `POST /tasks/{task_id}/cloud-runs` now validates inputs, creates `CloudRun` and companion `LocalTaskRun` records in `queued`, stores sandbox profile and command choices, appends a redacted log entry, and returns immediately without running the fake or Docker executor.

A local worker boundary claims queued runs through `POST /cloud-run-worker/process-next` or `POST /cloud-runs/{cloud_run_id}/process`, marks claimed runs as `running`, executes the selected fake or `docker_local` backend, records logs, stores patch artifacts when produced, and moves the run to `patch_ready`, `failed`, or `cancelled`. `POST /cloud-runs/{cloud_run_id}/cancel` supports queued cancellation and running cancellation requests. `GET /cloud-runs/{cloud_run_id}/logs` exposes ordered, redacted log records for polling. The desktop task board now shows queued cloud runs, explicit `Process` and `Cancel` controls, and compact cloud logs.

The Phase 9 worker remains local-first and explicitly triggered. It does not add Redis, Celery, a daemon process, remote VMs, object storage, live streaming, automatic PR creation, or automatic merges.

## Phase 10A Boundary

Phase 10A adds a remote-worker control-plane contract for cloud runs. Workers
can claim a renewable lease, heartbeat while executing, complete a current
lease with a sandbox execution result, and requeue expired leases. The default
queue provider remains `local_db`, and the `remote_stub` worker kind exercises
the contract without provisioning remote VMs, containers, object storage, or
live streaming.

Phase 10A keeps the Phase 9 fake and `docker_local` development adapters and
does not add a production queue dependency, cloud runtime, object storage,
credential broker, automatic PR creation, or automatic merge.

## Phase 10B Boundary

Phase 10B adds provider-neutral contracts for the remote execution plane without
integrating a real cloud vendor. Cloud runs now record queue, storage, runtime,
artifact manifest, log stream, external status, and external error metadata.
Standard cloud-run read responses expose only non-sensitive provider metadata
and never expose queue receipts.

The queue provider boundary includes `local_db`, which preserves the Phase 10A
lease, heartbeat, completion, and expired-lease behavior, and `external_stub`,
which records deterministic message IDs, receipts, and external statuses for
claim, requeue, and completion flows. The storage provider boundary includes
`local_inline`, which stores text artifacts in the local database behind
`local-inline://` URIs and validates kind, SHA-256, and byte size before reads.
Remote completion payloads can reference stored diff artifacts through
`artifact_refs`; invalid references are rejected before patch artifact creation.

The runtime provider boundary includes `remote_stub`, which records a
deterministic runtime job ID and writes local-inline manifest/log references at
cloud-run creation time. Phase 10B also redacts external URI query strings,
external token-like errors, and enforces remote completion payload size limits.

Phase 10B does not add real cloud SDKs, cloud credentials, external queues,
remote VMs or containers, live log streaming, automatic PR creation, or
automatic merge. Concrete production providers can implement these contracts in
a later phase.

## Phase 10C Boundary

Phase 10C adds the first concrete production provider MVP for the Phase 10B
execution-plane contracts. The selected stack is Aliyun MNS for queue messages,
Aliyun OSS for remote artifact refs, Aliyun ECI for short-lived remote worker
containers, and ACR for the worker image.

The public cloud-run lifecycle remains unchanged. Aliyun providers are selected
by provider names (`aliyun_mns`, `aliyun_oss`, and `aliyun_eci`), automated tests
use fake clients, and real cloud calls are opt-in through environment variables
and smoke commands. Worker containers receive API callback metadata and a
run-scoped callback token, not broad Aliyun AccessKeys.

Phase 10C does not add live log streaming, SLS, Kubernetes, automatic PR
creation, automatic merge, billing, or model-backed reviewer/debugger agents.

## Phase 10D Boundary

Phase 10D hardens the remote worker callback surface. Protected remote runtime
submissions now generate a high-entropy callback token, store only a run- and
worker-bound SHA-256 hash, inject the raw token only into the worker container
environment, and require that token on lease claim, heartbeat, artifact upload,
and completion callbacks.

Tokens are scoped to a single cloud run and deterministic worker ID, expire
after one hour, and are invalidated on completion or queued cancellation. Legacy
local/stub runs without a stored token remain compatible for local tests and
development flows.

Phase 10D does not add user identity auth for worker endpoints, rotate tokens
during a running lease, add live log streaming, or change automatic PR/merge
boundaries.

## Phase 11 Boundary

Phase 11 upgrades the Aliyun ECI remote worker from deterministic smoke output
to a real execution skeleton. The worker claims a protected lease, fetches a
callback-token-protected execution payload, clones the GitHub repository with
the repository's active clone credential returned only through that protected
worker payload, runs selected sandbox profile commands inside the worker
container, captures diff and command/test output, uploads artifact refs, and
completes the lease.

Phase 11 does not add direct MNS receive/delete semantics, live log streaming,
model-backed debugging, Git push, PR creation, automatic merge, production KMS,
or a second cloud provider.

## Phase 12 Boundary

Phase 12A adds a bounded log polling surface for cloud runs. The API keeps the
legacy full log list endpoint and adds a cursor-based log window endpoint that
can return persisted control-plane log rows and redacted remote log-stream
lines when the run has complete object-storage ref metadata.

Phase 12B adds optional provider-native log sync to that polling surface.
`GET /cloud-runs/{cloud_run_id}/logs/window` accepts `sync_stream=true`; when
paired with `include_stream=true`, the API asks the configured runtime provider
to refresh the run's log stream object before returning the same cursor window.
Provider failures degrade to the pre-sync window, refreshed logs still flow
through object-storage integrity metadata, and Aliyun ECI log sync uses a
tested `DescribeContainerLog` client seam.

## Phase 12C Boundary

Phase 12C adds the API, provider, and worker capability for Aliyun MNS
pull-worker operation alongside the existing protected assigned-run launch
contract. Queue-only Aliyun MNS pull assignments include the worker identity,
a short-lived callback token, the cloud-run identity, the selected storage
provider, and MNS delivery metadata needed to correlate the claimed message.
The API stores only the callback token hash.

Aliyun ECI launch still supports and currently uses assigned-run mode when
`AI_SCDC_CLOUD_RUN_ID`, `AI_SCDC_WORKER_ID`, and `AI_SCDC_CALLBACK_TOKEN` are
provided to the worker container. The default `aliyun_mns` plus `aliyun_eci`
path does not enqueue an extra MNS message; `queue_message_id` remains empty
until a true MNS pull delivery exists. MNS pull mode is activated for worker
processes started without `AI_SCDC_CLOUD_RUN_ID`, using
`AI_SCDC_QUEUE_PROVIDER=aliyun_mns`,
`AI_SCDC_STORAGE_PROVIDER=aliyun_oss`, and optional
`AI_SCDC_MNS_WAIT_SECONDS`. Queue-only MNS pull cloud-run submissions must
provide a storage provider so the MNS message carries a worker-consumable
storage contract.

In pull mode, workers claim through `POST /cloud-run-worker/leases` or the
existing worker lease route and include the callback token plus the claimed MNS
message ID and receipt in that request. The API accepts the receipt only when
the claimed message ID matches the persisted enqueue result for that cloud run.
`queue_receipt` remains internal only and is never returned through standard
cloud-run read responses.

After successful terminal lease completion is committed, the API owns MNS
receipt deletion or acknowledgement. The worker default path does not
double-delete receipts. If delete fails, the terminal cloud-run state remains
committed and the stored receipt is retained for recovery while the public
cloud-run status exposes only redacted provider metadata.

Phase 12D completes the artifact/log plane goal from the original Phase 12
roadmap. The API now exposes
`GET /cloud-runs/{cloud_run_id}/artifacts/manifest`, artifact list/detail/content
endpoints, provider-neutral download descriptors, retention metadata, and
`POST /cloud-runs/artifacts/cleanup-expired`. The artifact plane builds
descriptors from cloud-run manifest/log metadata, local-inline stored objects,
and patch-artifact diff fallback while preserving workspace and run-scope
checks.

The artifact plane does not return signed provider URLs or delete Aliyun OSS
objects. It redacts provider refs for display by removing query strings and
fragments, validates content reads through existing object-storage integrity
checks, deletes only expired `local_inline` rows, and reports external-provider
cleanup as lifecycle-only operator intent.

The desktop now renders a compact artifact browser inside the cloud-run task
detail area. It shows retention policy, artifact count, grouped artifact
metadata, redacted refs, and inline text previews for readable artifacts.

Phase 12 still does not add WebSockets, Server-Sent Events, SLS-managed log
stores, model-backed reviewer or debugger agents, production KMS, or billing.
Phase 12D intentionally keeps the artifact browser minimal and scoped to the
existing task detail area.

## Phase 13A Boundary

Phase 13A hardens the Aliyun MNS/OSS/ECI path for operator use without widening
the product boundary. The API now has service-level seams for retrying retained
Aliyun MNS receipt deletion and best-effort Aliyun ECI terminal cleanup by
persisted `runtime_job_id`.

Cleanup failures do not rewind terminal cloud-run status. MNS receipt recovery
clears only the internal `queue_receipt` after delete succeeds, and ECI cleanup
retains `runtime_job_id` for audit and repeat attempts. Cleanup logs and
responses use redacted provider status only and never expose callback tokens,
queue receipts, access keys, signed URLs, or raw provider exceptions.

Phase 13A also documents Aliyun RAM policy examples, provider failure runbooks,
OSS lifecycle boundaries, and the production KMS boundary. It does not add a
public destructive operations API, user auth, organization RBAC, billing, a real
KMS SDK, API-side OSS deletion, or a second cloud provider.

## Roadmap

Completed:

1. Phase 0 monorepo foundation with desktop shell, API, agent protocol, deterministic gateway interface, mock worker simulator, and local test infrastructure.
2. Phase 1 planner approval loop with fake planner drafts, human approval or rejection, task creation, and audit trail events.
3. Backend-first model router and BYOK foundation with provider metadata, write-only credential records, role-based route resolution, fake fallback routes, and append-only usage logging.
4. Real model-backed planner vertical slice that uses route resolution to create TaskSpec drafts for human approval, logs token usage, and falls back to fake drafts on provider failures.
5. Local Runner vertical slice with repository registration, git worktree execution, patch artifact capture, task events, and desktop run controls.
6. Deterministic local test, patch review, and debug-attempt workflow with desktop controls, durable verification records, and idempotent review results.
7. Human patch approval boundary with compact diff preview, durable approval records, `MERGE_READY` and `HUMAN_APPROVAL` transitions, and no automatic git merge.
8. GitHub-only cloud-run and pull-request boundary with PAT metadata, fake cloud sandbox artifacts, explicit `Create PR`, durable PR records, and no automatic merge.
9. Docker local sandbox executor with GitHub repository cloning, sandbox profiles, command whitelists, redacted logs, Docker failure codes, and patch/test artifact capture.
10. Local cloud-run queue worker boundary with queued enqueue, explicit worker processing, cancellation, ordered redacted logs, and desktop Process/Cancel controls.
11. Remote worker control-plane contract with local queue adapter, renewable leases, heartbeats, stale completion rejection, expired lease requeue, and remote stub completion.
12. Provider-neutral remote execution-plane contract with queue, local-inline object storage, remote runtime stub, artifact refs, external metadata redaction, and payload size guards.
13. Aliyun provider MVP with MNS queue enqueue, OSS artifact refs, ECI remote worker submission, ACR worker image path, fake-client tests, and opt-in smoke documentation.
14. Run-scoped remote worker callback token hardening with token hash storage, ECI env injection, protected worker callbacks, expiry, completion invalidation, and queued-cancel invalidation.
15. Real remote worker execution skeleton with protected payload fetch, private GitHub clone credential boundary, command/test execution, diff capture, artifact uploads, and redacted completion.
16. Bounded cloud-run log polling with cursor windows, safe remote log-stream reads, and optional provider-native log sync.
17. Aliyun MNS pull-worker claims for protected MNS deliveries with callback-token hash storage, message-id binding, internal-only queue receipts, and post-terminal MNS acknowledgement or recoverable delete failure handling.
18. Cloud-run artifact plane with manifest/list/detail/content APIs, provider-neutral download descriptors, retention metadata, local-inline cleanup, external lifecycle-only cleanup intent, and desktop artifact browser.
19. Aliyun operational hardening with retained MNS receipt recovery, best-effort ECI terminal cleanup by persisted runtime id, least-privilege RAM examples, provider failure runbooks, OSS lifecycle guidance, and production KMS boundary documentation.

Future:

1. Authenticated organization-scoped operator controls for cleanup, audit, billing, and production KMS integration before commercial beta.
2. Broader provider coverage beyond the current Aliyun MNS/OSS/ECI production-provider path while preserving callback-token-protected payload access and completion boundaries.
