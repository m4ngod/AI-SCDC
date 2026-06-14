# AI-SCDC Project Status

Status last updated: 2026-06-07

## Current Phase

The project is through Phase 13A, has Phase 13B Commercial Trust Boundary
foundations in progress, and has started the Phase 13C cost/quota guardrail
slice. The current slices add identity, workspace scope, secret-access audit,
and execution-plane usage/cost foundations while preserving the existing
development adapters and worker callback-token boundary.

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

## In Progress

Phase 13B Commercial Trust Boundary has started with test-backed identity,
workspace-scope, and secret-open audit slices:

- `User`, `Organization`, `Workspace`, and `OrganizationMember` models.
- Workspace roles: owner, admin, developer, reviewer, billing_manager, viewer.
- Request auth context with explicit `dev` mode and API-token member lookup
  mode.
- `/me` returns the active auth context rather than a route-local fixed
  identity.
- Project, repository, task, cloud-run, artifact, credential, route, usage,
  approval, review/debug, and pull-request HTTP reads/writes are scoped to the
  active workspace.
- `SecretVault` protocol coverage for seal/open/rotate/delete/fingerprint with
  a fail-closed provider factory, a test-backed `KmsSecretVault` provider
  boundary for generic `kms`, and a real Aliyun Classic KMS SDK adapter for
  `aliyun_kms`. KMS mode requires `AI_SCDC_KMS_KEY_ID`; `aliyun_kms` also
  requires existing Aliyun region/access-key settings and never falls back to
  development storage.
- Local KMS readiness command for redacted preflight and explicit live-smoke
  validation through the configured SecretVault provider.
- `SecretAccessAuditLog` plus centralized secret-open auditing for model
  planner, GitHub pull-request, Docker cloud-run, and remote-worker payload
  credential opens.
- Secret create/delete audit rows for model and GitHub credentials without
  storing raw or encrypted secret payloads.
- Narrow authenticated cloud-run operator API facade for owner/admin MNS
  receipt recovery and ECI runtime cleanup:
  `POST /cloud-runs/{cloud_run_id}/operator/retry-mns-receipt-delete` and
  `POST /cloud-runs/{cloud_run_id}/operator/cleanup-aliyun-eci-runtime`.
- Phase 13C execution usage types, workspace credit wallets, spend limits,
  cloud-run budget reservations, per-run cost summaries, and workspace usage
  summary APIs.

Remaining commercial trust work includes full session issuance, production
auth/IdP integration, cloud KMS credential provisioning and retained
target-account KMS smoke evidence, a full operator console, public destructive
OSS cleanup, broader audit coverage, a complete role-specific permission
matrix, real provider price tables, payment integration, invoices, and desktop
billing UI.

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

Phase 13B/13C current verification snapshot:

- `pytest apps/api/tests/test_auth_rbac_api.py -q` -> 10 passed, 1 warning in 4.80s
- `pytest apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_api_endpoints.py apps/api/tests/test_model_settings_api.py apps/api/tests/test_github_repository_api.py apps/api/tests/test_usage_ledger_api.py -q` -> 75 passed, 1 warning in 34.66s
- `python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_api_endpoints.py` -> passed
- `pytest apps/api/tests/test_aliyun_kms.py -q` -> 19 passed; covers the
  fake-SDK Aliyun KMS adapter seam.
- `pytest apps/api/tests/test_secret_access_audit.py -q` -> 27 passed, 1
  warning; includes KMS provider boundary and Aliyun KMS factory tests.
- `pytest apps/api/tests/test_kms_readiness.py -q` -> KMS readiness preflight,
  live-smoke, and CLI tests passed without contacting Aliyun.
- `pytest apps/api/tests/test_secret_access_audit.py apps/api/tests/test_model_settings_api.py apps/api/tests/test_github_repository_api.py apps/api/tests/test_model_planner.py apps/api/tests/test_planner_endpoints.py apps/api/tests/test_pull_request_api.py -q` -> 103 passed, 1 warning in 19.01s
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "docker_cloud_run_enqueue_stores_metadata_without_opening_token or docker_cloud_run_validates_profile_before_opening_github_token"` -> 2 passed, 170 deselected, 1 warning in 4.57s
- `pytest apps/api/tests/test_model_settings_api.py apps/api/tests/test_github_repository_api.py apps/api/tests/test_planner_endpoints.py apps/api/tests/test_pull_request_api.py -q` -> 73 passed, 1 warning in 49.83s
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "remote_worker_payload or docker_cloud_run or github_token or protected_worker"` -> 30 passed, 142 deselected, 1 warning in 16.47s
- `python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_secret_access_audit.py apps/api/tests/test_model_planner.py apps/api/tests/test_cloud_run_api.py` -> passed
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "cloud_run_operator or retained_receipt_recovery or terminal_cleanup"` -> 12 passed, 165 deselected, 1 warning in 10.97s
- `pytest apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_cloud_run_api.py -q -k "operator or money_moving_workspace_endpoints_require_billing_role"` -> 5 passed, 183 deselected, 1 warning in 4.41s
- `python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_cloud_run_api.py` -> passed
- `pytest apps/api/tests/test_cloud_run_api.py apps/api/tests/test_auth_rbac_api.py -q -k "cloud_run_operator or retained_receipt_recovery or terminal_cleanup or money_moving_workspace_endpoints_require_billing_role"` -> 13 passed, 175 deselected, 1 warning in 11.03s
- `pytest apps/api/tests/test_usage_ledger_api.py apps/api/tests/test_usage_cost_quota_api.py -q` -> 46 passed, 1 warning in 21.67s
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "enqueue or cancel or process or completion or lease"` -> 50 passed, 123 deselected, 1 warning in 76.56s
- `pytest apps/api/tests/test_usage_ledger_api.py apps/api/tests/test_usage_cost_quota_api.py apps/api/tests/test_api_endpoints.py apps/api/tests/test_pull_request_api.py apps/api/tests/test_planner_endpoints.py -q` -> 60 passed, 1 warning in 16.99s
- `pytest apps/api/tests -q` -> 577 passed, 1 warning as part of `pnpm test`
- `pnpm test` -> JavaScript 91 passed; Python 611 passed, 1 warning
- `pnpm typecheck` -> `apps/desktop` and `packages/agent-protocol` completed
- `python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_secret_access_audit.py apps/api/tests/test_aliyun_kms.py` -> passed
- `git diff --check` -> passed with Git LF-to-CRLF working-copy warnings only

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

- Phase 13B now has request identity, workspace scope, secret-open audit
  foundations, and a test-backed KMS SecretVault boundary, but it does not yet
  add full login/session issuance, production IdP integration,
  cloud KMS credential provisioning, retained target-account KMS smoke evidence,
  billing, subscriptions, complete audit logging, WebSockets/SSE, or a second
  cloud provider.
- Phase 13B exposes authenticated owner/admin MNS receipt recovery and ECI
  terminal cleanup endpoints for cloud runs, but public destructive OSS cleanup
  and provider deletion APIs remain unavailable; the full operator console is
  still future work.
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
  cloud KMS credential provisioning are still development boundaries. The local
  readiness command exists, but operators still must run and retain
  target-account live-smoke evidence before beta traffic.
- Reviewer and debugger behavior is deterministic, not model-backed.
- The API still initializes schema through SQLModel metadata and SQLite upgrade
  helpers; Alembic migrations remain reserved for later.

## Recommended Next Phase

The next production step should continue Phase 13B with cloud KMS credential
provisioning, retained target-account KMS smoke evidence, and broader/full
organization-scoped operator controls and operator console coverage before
commercial beta.
