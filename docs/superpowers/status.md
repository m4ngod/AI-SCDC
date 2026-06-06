# AI-SCDC Project Status

Status last updated: 2026-06-06

## Current Phase

The project is through Phase 13A, with Phase 12D now completing the original
Phase 12 artifact plane: cloud-run artifact manifests, safe artifact
listing/detail/content APIs, provider-neutral download descriptors, retention
metadata, local-inline cleanup, external lifecycle-only cleanup intent, and a
minimal desktop artifact browser.

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
17. Phase 12C Aliyun MNS pull-worker claims: protected MNS deliveries,
    callback-token hash storage, message-id binding, internal-only queue
    receipts, and post-terminal MNS acknowledgement or recoverable delete
    failure handling.
18. Phase 12D artifact plane completion: manifest/list/detail/content APIs,
    provider-neutral download descriptors, retention metadata, local-inline
    cleanup, external lifecycle-only cleanup intent, and desktop artifact
    browsing.
19. Phase 13A Aliyun operational hardening: service-level MNS receipt recovery,
    best-effort ECI terminal cleanup, redacted cleanup logs, least-privilege RAM
    examples, provider failure runbooks, OSS lifecycle guidance, and production
    KMS boundaries.

## Verification

Phase 13A final verification has completed:

- `pytest apps/api/tests/test_cloud_run_api.py -q -k "retained_receipt_recovery or terminal_cleanup or aliyun_mns_completion_delete_failure or aliyun_eci_submission_cleans_up"` -> 10 passed, 151 deselected, 1 warning in 5.77s
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns or protected_aliyun or protected_worker or aliyun_eci"` -> 41 passed, 120 deselected, 1 warning in 16.42s
- `pytest apps/api/tests/test_aliyun_clients.py -q` -> 15 passed in 0.05s
- `pytest apps/api/tests/test_remote_worker.py -q` -> 48 passed in 0.17s
- `pytest apps/api/tests -q` -> 465 passed, 1 warning in 196.92s
- `pnpm --filter @ai-scdc/desktop test -- client.test.ts` -> 34 passed in 1.81s
- `pnpm typecheck` -> `apps/desktop` and `packages/agent-protocol` completed
- `git diff --check` -> passed

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

- Phase 13A adds service-level Aliyun cleanup and recovery seams plus
  operations docs, but it does not expose public destructive cleanup endpoints,
  add user auth/RBAC, add billing, integrate real KMS, delete OSS objects from
  code, add WebSockets/SSE, or add a second cloud provider.
- The real remote worker can fetch a protected payload, clone, execute commands,
  capture diffs, upload artifacts, and complete a lease, but it does not push
  branches, create pull requests, merge changes, or provide live
  WebSocket/SSE provider log streaming.
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

The next production phase should add authenticated organization-scoped
operator controls and production KMS integration before commercial beta.
