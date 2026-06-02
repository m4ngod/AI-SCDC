# AI-SCDC Project Status

Last verified: 2026-06-02

## Current Phase

The project is through Phase 10A: remote worker control-plane contract.

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

## Verification

Latest automated verification:

```bash
pytest apps/api/tests
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts src/test/App.test.tsx
pnpm typecheck
git diff --check
```

Results:

- `pytest apps/api/tests`: passed, 295 tests, 1 existing Starlette/httpx warning.
- Desktop client/App tests: passed, 66 tests.
- Root `pnpm typecheck`: passed.
- `git diff --check`: passed.

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

- The Phase 10A worker lease contract is exposed through API endpoints; there
  is no desktop-managed daemon loop or external queue runtime yet.
- Docker execution is still local-first; Phase 10A adds a `remote_stub`
  contract but does not add remote cloud runtime, object storage, or live log
  streaming.
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

Phase 10B should be production remote cloud sandbox workers:

1. Replace the local queue provider with a real external queue runtime.
2. Add remote cloud VM/container workers.
3. Add object storage contracts for large logs, diffs, and artifacts.
4. Add live log streaming on top of the Phase 9 polling/log contract.
5. Keep fake, `docker_local`, and `remote_stub` executors as development
   adapters while remote workers become the production execution path.

The production remote-worker step should come before model-backed
reviewer/debugger agents or commercial beta work, because the execution plane
still needs a production queue, storage, and streaming contract.
