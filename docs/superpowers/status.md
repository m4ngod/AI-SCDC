# AI-SCDC Project Status

Last verified: 2026-06-05

## Current Phase

The project is through Phase 12B: bounded cloud-run log polling, safe remote
log-stream reads, and optional provider-native log sync over the existing
polling API.

`docs/architecture.md` is the authoritative phase boundary document. The older
`docs/superpowers/plans/*.md` files still contain unchecked implementation
checklists, but those checkboxes are not current progress markers. Current
progress should be judged from the architecture roadmap, implemented services,
tests, README smoke instructions, and git history.

## Completed

1. Phase 0 monorepo foundation: desktop shell, FastAPI API, agent protocol,
   deterministic gateway interface, worker simulator, SQLite-backed tests, and
   Docker Compose reservations.
2. Phase 1 planner approval loop: fake planner drafts, human approval or
   rejection, task creation, and audit events.
3. Phase 2 model routing and BYOK foundation: provider metadata, write-only
   credentials, model routes, fake fallback route, and usage ledger records.
4. Phase 3 real model-backed planner: OpenAI-compatible planner calls, validated
   TaskSpec drafts, usage logging, and fake fallback on provider failures.
5. Phase 4 local runner: repository registration, git worktree execution, patch
   artifact capture, and desktop run controls.
6. Phase 5 deterministic verification: local test runs, patch review,
   debug-attempt records, and desktop controls.
7. Phase 6 patch approval: compact diff preview, durable patch approval,
   `MERGE_READY`, and `HUMAN_APPROVAL` boundaries.
8. Phase 7 GitHub PR boundary: GitHub credential metadata, repository records,
   fake cloud sandbox artifacts, explicit PR creation, and no automatic merge.
9. Phase 8 Docker local sandbox executor: sandbox profiles, command whitelists,
   GitHub clone credential boundary, redacted command payloads, Docker failure
   codes, timeout cleanup, and patch/test artifact capture.
10. Phase 9 local cloud-run queue worker: enqueue-only cloud-run creation,
    explicit worker processing endpoints, queued/running cancellation, ordered
    redacted cloud-run logs, and desktop Process/Cancel/log controls.
11. Phase 10A remote worker control plane: local queue adapter, renewable
    worker leases, heartbeats, stale completion rejection, expired lease
    requeue, and remote stub completion contract.
12. Phase 10B provider-neutral remote execution plane: queue provider
    selection, `local_db` dispatch, `external_stub` queue metadata,
    `local_inline` object storage, remote completion artifact refs,
    `remote_stub` runtime submission, external metadata redaction, and payload
    size guards.
13. Phase 10C Aliyun provider MVP: `aliyun_mns` queue enqueue, `aliyun_oss`
    artifact storage refs, `aliyun_eci` remote runtime submission, worker
    artifact upload endpoint, ACR worker image path, fake-client automated
    tests, and opt-in Aliyun smoke documentation.
14. Phase 10D remote worker callback token hardening: run- and worker-bound
    callback token hash storage, ECI worker env injection, protected lease,
    heartbeat, artifact-upload, and completion callbacks, callback-token
    expiry, completion invalidation, and queued-cancel invalidation.
15. Phase 11 real remote worker execution skeleton: protected execution payload
    fetch, private GitHub clone credential boundary, selected sandbox profile
    command/test execution inside the worker container, diff capture, artifact
    uploads, and redacted completion payloads.
16. Phase 12B provider log sync: cursor-based log windows, persisted
    log-stream metadata, safe object-storage reads, optional `sync_stream`
    provider refresh, deterministic `remote_stub` sync, and Aliyun ECI
    `DescribeContainerLog` sync seam.

## Verification

Latest Phase 12B final verification:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or log_sync or sync_stream or phase_12a or phase_12b" -v
pytest apps/api/tests/test_aliyun_clients.py -v
pytest apps/api/tests/test_cloud_object_storage.py -v
pytest apps/api/tests -v
pnpm --filter @ai-scdc/desktop test -- client.test.ts
pnpm typecheck
git diff --check
```

Results:

- `pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or log_sync or sync_stream or phase_12a or phase_12b" -v`:
  passed, 19 tests, 108 deselected, 1 existing Starlette/httpx warning.
- `pytest apps/api/tests/test_aliyun_clients.py -v`: passed, 6 tests.
- `pytest apps/api/tests/test_cloud_object_storage.py -v`: passed, 8 tests.
- `pytest apps/api/tests -v`: passed after `c074977`, 410 tests, 1
  existing Starlette/httpx warning.
- `pnpm --filter @ai-scdc/desktop test -- client.test.ts`: passed, 1 test
  file, 34 tests.
- `pnpm typecheck`: passed for `apps/desktop` and
  `packages/agent-protocol`.
- `git diff --check`: passed with Git LF-to-CRLF working-copy warnings only.

Previous Phase 10D verification:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun or worker_uploads or artifact_ref or lease or callback_token" -v
pytest apps/api/tests/test_aliyun_config.py apps/api/tests/test_aliyun_clients.py apps/api/tests/test_cloud_object_storage.py apps/api/tests/test_remote_worker.py -v
pytest apps/api/tests
pnpm typecheck
git diff --check
rg -n "AccessKey|ACCESS_KEY_SECRET|secret-value|ak-secret|very-secret-value|ALIYUN_ACCESS_KEY_SECRET" apps docs README.md
```

Results:

- Phase 10D cloud-run focused tests: passed, 34 tests, 67 deselected, 1 existing Starlette/httpx warning.
- Phase 10D Aliyun config/client/object-storage/worker focused tests: passed, 19 tests.
- `pytest apps/api/tests`: passed, 350 tests, 1 existing Starlette/httpx warning.
- Root `pnpm typecheck`: passed.
- `git diff --check`: passed with Git LF-to-CRLF working-copy warnings only.
- Secret scan found only environment variable names, README placeholders, plan
  examples, and fake test secret values; no real Aliyun credential values were
  present.

## Phase 8 Smoke

A real Docker local sandbox smoke was run on 2026-06-02 with:

- `AI_SCDC_CLOUD_RUNNER=docker_local`
- temporary SQLite database
- public repository: `https://github.com/octocat/Hello-World`
- cached Docker image: `mcr.microsoft.com/devcontainers/python:1-3.12-bookworm`
- fake local GitHub token value, used only to exercise credential handling

Smoke result:

```text
cloud_run_status: patch_ready
sandbox_kind: docker_local
failure_reason: null
files_changed: AI_SCDC_DOCKER_SMOKE.md
test_result: passed
workflow_test_status: passed
review_verdict: approved
approval_status: MERGE_READY
human_approval_status: HUMAN_APPROVAL
pr_status: PR_CREATED
token_redacted: true
```

This verifies that Phase 8 Docker-produced patch artifacts can flow through the
existing Phase 5 test workflow, Phase 5 deterministic review, Phase 6 patch
approval, Phase 6 human approval request, and Phase 7 fake PR adapter.

## Known Limits

- Phase 12B adds optional provider-native log sync over the polling API, but it
  does not add WebSockets, Server-Sent Events, direct MNS receive/delete
  semantics, SLS-managed log stores, Kubernetes/ACK orchestration, billing, or
  model-backed reviewer/debugger agents.
- The real remote worker can fetch a protected payload, clone, execute commands,
  capture diffs, upload artifacts, and complete a lease, but it does not push
  branches, create pull requests, merge changes, or stream provider-native logs.
- Docker execution is still available as a local-first adapter; `remote_stub`,
  `external_stub`, and `local_inline` remain deterministic development adapters
  for the provider-neutral contract.
- Docker Hub image pulls failed in the local environment with an EOF response
  from `registry-1.docker.io`, so the smoke used an already cached image.
- Real GitHub PR publishing still requires starting the API with
  `AI_SCDC_GITHUB_PR_ADAPTER=real` and providing a real PAT.
- Authentication, organization RBAC, subscriptions, billing collection, and
  production KMS are still development placeholders.
- Reviewer and debugger behavior is deterministic, not model-backed.
- The API still initializes schema through SQLModel metadata and SQLite upgrade
  helpers; Alembic migrations remain reserved for later.

## Recommended Next Phase

The next production hardening phase should build on Phase 12B without widening
the approval boundary:

1. Add or harden direct Aliyun MNS receive/delete worker semantics while keeping
   callback-token-protected payload access and completion boundaries.
2. Harden Aliyun operations with cleanup automation, least-privilege RAM policy
   examples, provider failure runbooks, and production KMS boundaries.
3. Keep fake, `docker_local`, `remote_stub`, `external_stub`, and
   `local_inline` as deterministic development adapters.
4. Defer model-backed reviewer/debugger agents and commercial beta work until
   the remote execution plane is operationally reliable.
