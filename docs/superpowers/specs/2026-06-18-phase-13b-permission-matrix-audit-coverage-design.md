# Phase 13B Permission Matrix and Audit Coverage Design

## Purpose

Phase 13B has identity, workspace scoping, secret access audit, KMS provider
boundaries, billing guardrails, and narrow owner/admin operator APIs in place.
The remaining commercial trust gap is that most HTTP routes still rely only on
"authenticated and workspace-scoped" access. This slice adds a complete
workspace role permission matrix and broader audit coverage for high-value
writes and high-sensitive reads.

The goal is to make the trust boundary testable before commercial beta without
adding full IdP sessions, payment integration, an operator console, or a new
cloud KMS purchase dependency.

## Current State

The API already has these foundations:

- workspace roles: `owner`, `admin`, `developer`, `reviewer`,
  `billing_manager`, and `viewer`
- request auth context in dev-header and API-token modes
- workspace scoping for project, repository, task, run, artifact, credential,
  usage, approval, review, and pull-request resources
- route-level role checks for billing mutation endpoints and cloud-run operator
  endpoints
- `SecretAccessAuditLog` for secret create, open, and delete paths

The missing pieces are:

- a single documented role-by-route policy instead of scattered role checks
- explicit route enforcement for all high-value write endpoints
- explicit route enforcement for high-sensitive read endpoints
- a general workspace audit log for non-secret high-value actions
- tests proving denied roles, allowed roles, workspace hiding, and audit
  redaction across the broader route surface

## Role Semantics

The permission matrix follows these role semantics.

`owner` and `admin` can manage the full workspace. They can create and modify
business resources, credentials, model configuration, billing controls,
operator actions, artifact cleanup, reviews, approvals, and PR publishing.

`developer` can create and operate project execution resources. This includes
projects, repositories, conversations, planner runs, tasks, local runs, cloud
runs, task transitions, patch tests, PR publication from approved artifacts,
and reading artifacts/logs needed to execute and debug work. Developers cannot
manage GitHub credentials, model credentials, model providers, model routes,
billing controls, operator maintenance endpoints, or artifact cleanup.

`reviewer` is an execution-review role. Reviewers can read project/task/run
evidence, read artifact content and logs, approve or reject planner runs,
start patch reviews, approve patch artifacts, and request human approval.
Reviewers cannot create tasks, start local/cloud runs, modify repositories,
manage credentials, manage model configuration, change billing controls, call
operator endpoints, clean artifacts, or publish pull requests.

`billing_manager` is scoped to billing and usage. Billing managers can read and
mutate billing resources such as usage ledger, usage summary, cloud-run cost
summary, manual credit grants, and spend limits. They cannot read code
artifacts/logs, list credentials, manage models, modify project execution
resources, review/approve patches, publish PRs, or call operator endpoints.

`viewer` can read low-sensitive workspace metadata only. Viewers can list and
view project/task/run summaries, artifact manifests, artifact descriptors,
reviews, approvals, and PR metadata. They cannot read artifact content,
download descriptors, logs, credential lists, usage/cost details, or perform
any write action.

## Permission Families

Centralize route permissions by operation family instead of scattering raw role
sets through every route.

Suggested families:

- `workspace.metadata.read`: all workspace roles
- `project.write`: owner, admin, developer
- `repository.write`: owner, admin, developer
- `conversation.write`: owner, admin, developer
- `planner.write`: owner, admin, developer
- `planner.review`: owner, admin, reviewer
- `task.write`: owner, admin, developer
- `run.write`: owner, admin, developer
- `execution.evidence.read`: owner, admin, developer, reviewer
- `conversation.sensitive.read`: owner, admin, developer, reviewer
- `execution_config.read`: owner, admin, developer, reviewer
- `artifact.sensitive.read`: owner, admin, developer, reviewer
- `artifact.cleanup`: owner, admin
- `log.sensitive.read`: owner, admin, developer, reviewer
- `review.write`: owner, admin, reviewer
- `approval.write`: owner, admin, reviewer
- `pull_request.publish`: owner, admin, developer
- `credential.metadata.read`: owner, admin
- `credential.write`: owner, admin
- `model_config.read`: owner, admin, developer, reviewer
- `model_config.write`: owner, admin
- `billing.read`: owner, admin, billing_manager
- `billing.write`: owner, admin, billing_manager
- `operator.write`: owner, admin

Route handlers should call a helper such as
`require_workspace_permission("task.write")`. The helper maps operation names
to roles and delegates to the existing auth context role check. Keeping a
single policy map gives tests, route enforcement, and documentation one source
of truth.

## Route Classification

Low-sensitive metadata reads are allowed to all workspace roles after normal
workspace scoping when the response contains only safe metadata:

- `GET /projects`
- `GET /projects/{project_id}/repositories`
- `GET /repositories/{repo_id}`
- `GET /projects/{project_id}/conversations`
- `GET /planner-runs/{planner_run_id}`
- `GET /projects/{project_id}/tasks`
- `GET /tasks/{task_id}`
- `GET /patch-artifacts/{patch_artifact_id}/pull-requests`
- `GET /patch-reviews/{review_id}`
- `GET /patch-artifacts/{patch_artifact_id}/approvals`
- `GET /patch-approvals/{approval_id}`
- `GET /pull-requests/{pull_request_id}`
- `GET /tasks/{task_id}/events`

If an existing response model contains execution evidence or direct access
material, the route must either return a redacted summary for viewer access or
be classified as a sensitive read. Examples in the current API include
`CloudRunRead.command_results`, `PatchArtifactRead.diff_text`,
`LocalTestRunRead.command_results`, `DebugAttemptRead.root_cause`, cloud log
messages, conversation messages, and artifact descriptor `download_url`
values. Artifact manifest/list/detail responses must not expose presigned or
direct download URLs to viewers; the dedicated download endpoint is the only
place that may return a usable download URL.

High-sensitive reads require explicit permission:

- `GET /projects/{project_id}/sandbox-profiles`: `execution_config.read`
- `GET /sandbox-profiles/{sandbox_profile_id}`: `execution_config.read`
- `GET /conversations/{conversation_id}/messages`:
  `conversation.sensitive.read`
- `GET /model-providers`: `model_config.read`
- `GET /model-routes`: `model_config.read`
- `GET /model-routes/resolve`: `model_config.read`
- `GET /github-credentials`: `credential.metadata.read`
- `GET /model-credentials`: `credential.metadata.read`
- `GET /usage-ledger`: `billing.read`
- `GET /workspace/usage-summary`: `billing.read`
- `GET /cloud-runs/{cloud_run_id}/cost-summary`: `billing.read`
- `GET /tasks/{task_id}/cloud-runs`: `execution.evidence.read` unless a
  redacted summary response is introduced
- `GET /cloud-runs/{cloud_run_id}`: `execution.evidence.read` unless a
  redacted summary response is introduced
- `GET /tasks/{task_id}/local-runs`: `execution.evidence.read` unless a
  redacted summary response is introduced
- `GET /local-runs/{local_run_id}`: `execution.evidence.read`
- `GET /patch-artifacts/{patch_artifact_id}`: `execution.evidence.read`
- `GET /patch-artifacts/{patch_artifact_id}/test-runs`:
  `execution.evidence.read`
- `GET /test-runs/{test_run_id}`: `execution.evidence.read`
- `GET /patch-artifacts/{patch_artifact_id}/reviews`:
  `execution.evidence.read`
- `GET /tasks/{task_id}/debug-attempts`: `execution.evidence.read`
- `GET /cloud-runs/{cloud_run_id}/artifacts/manifest`:
  `artifact.sensitive.read` unless download URLs are removed or redacted for
  viewer access
- `GET /cloud-runs/{cloud_run_id}/artifacts`: `artifact.sensitive.read`
  unless download URLs are removed or redacted for viewer access
- `GET /cloud-runs/{cloud_run_id}/artifacts/{artifact_id}`:
  `artifact.sensitive.read` unless download URLs are removed or redacted for
  viewer access
- `GET /cloud-runs/{cloud_run_id}/artifacts/{artifact_id}/content`:
  `artifact.sensitive.read`
- `POST /cloud-runs/{cloud_run_id}/artifacts/{artifact_id}/download`:
  `artifact.sensitive.read`
- `GET /cloud-runs/{cloud_run_id}/logs/window`: `log.sensitive.read`
- `GET /cloud-runs/{cloud_run_id}/logs`: `log.sensitive.read`

High-value writes require explicit permission:

- project, repository, GitHub repository, sandbox profile, conversation,
  message, planner run, task, local run, cloud run, task transition, and cloud
  run cancellation routes use the developer execution families
- planner approve/reject, patch review, patch approval, and human approval
  request routes use the reviewer families
- GitHub credential, model credential, model provider, and model route create,
  update, and delete routes use the credential/model admin families
- usage ledger append, manual credit grant, spend-limit update, and budget or
  cost-control mutations use billing families
- cloud-run operator routes and artifact cleanup use owner/admin-only families
- pull-request creation from patch approval uses `pull_request.publish`

Worker callback endpoints under `/cloud-run-worker/...` remain callback-token
and lease scoped. They are not converted into user-session workspace role
routes. Their service-level effects may still be recorded as system audit
events where useful, with `auth_mode="system"` and no secret or artifact
payloads.

## Audit Model

Keep `SecretAccessAuditLog` as the dedicated secret audit table for secret
create, open, and delete operations. Add a separate general audit table for
workspace actions that are not secret-specific.

Suggested model: `WorkspaceAuditLog`.

Fields:

- `id`
- `workspace_id`
- `organization_id`
- `user_id`
- `auth_mode`
- `operation`
- `resource_type`
- `resource_id`
- `access_level`
- `success`
- `status_code`
- `error_code`
- `metadata_json`
- `created_at`

`operation` should use stable names such as `task.create`, `run.start`,
`artifact.content.read`, `cloud_log.read`, `billing.spend_limit.update`, and
`operator.eci_cleanup`. `access_level` should distinguish `high_value_write`,
`high_sensitive_read`, and `system_event`.

Audit metadata must be redacted by default. It may include safe identifiers,
counts, provider names, route names, and status labels. It must not include raw
or encrypted secrets, callback tokens, queue receipts, artifact contents, log
lines, prompt bodies, generated code, provider raw errors, or presigned
download URLs.

## Audit Coverage

Record audit rows for successful and failed attempts when a route reaches the
policy/audit wrapper. The first implementation should cover:

High-value writes:

- project/repository/sandbox/conversation/planner/task creation and updates
- local/cloud run start, process, cancel, and task transitions
- patch test, review, approval, human approval request, and PR publish
- credential and model configuration create/update/delete
- billing ledger append, manual grants, and spend-limit updates
- operator MNS receipt retry and Aliyun ECI cleanup
- artifact cleanup

High-sensitive reads:

- GitHub and model credential metadata listing
- usage ledger, workspace usage summary, and cloud-run cost summary reads
- artifact content reads and artifact download descriptor creation
- cloud-run log window and full log reads

Denied permission checks should not leak the target resource if the route would
otherwise hide cross-workspace access as 404. Preserve existing workspace
lookups and hiding behavior. For same-workspace RBAC denial, return 403 with
the existing `Insufficient workspace role` detail.

## Testing

Write tests before implementation. Focused tests should cover:

- the permission map contains every declared operation family
- representative allowed/denied matrix cases for every role
- viewers can read low-sensitive metadata but cannot read artifact content,
  downloads, logs, credentials, or billing detail
- billing managers can read and mutate billing but cannot read artifacts/logs
  or manage execution resources
- developers can create/run execution resources and read evidence but cannot
  manage credentials/model config/billing/operator routes
- reviewers can perform review/approval actions and read evidence but cannot
  start runs, create tasks, publish PRs, or manage credentials/model config
- owners/admins can call all protected route families
- cross-workspace resources remain hidden as 404 rather than becoming 403
- high-value writes create redacted audit rows on success
- high-sensitive reads create redacted audit rows on success
- failed same-workspace RBAC attempts create redacted audit rows where the
  route can safely identify the attempted operation without leaking payloads
- audit serialization does not include raw secrets, encrypted secrets,
  callback tokens, queue receipts, log text, artifact content, or presigned
  URLs

Run a focused API test set first:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_secret_access_audit.py -q
```

Then run the wider trust-boundary tests:

```powershell
pytest apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_usage_ledger_api.py apps/api/tests/test_usage_cost_quota_api.py apps/api/tests/test_model_settings_api.py apps/api/tests/test_github_repository_api.py apps/api/tests/test_pull_request_api.py apps/api/tests/test_planner_endpoints.py -q
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_secret_access_audit.py
git diff --check
```

Before completion, run the full API suite and repo checks used by the current
Phase 13B slices when time allows:

```powershell
pytest apps/api/tests -q
pnpm test:js
pnpm typecheck
```

## Documentation

After implementation, update `docs/architecture.md`,
`docs/superpowers/status.md`, and `STATUS.md` to say Phase 13B now has a
complete test-backed workspace role permission matrix and broader audit
coverage for high-value writes plus high-sensitive reads.

The docs must still list full production IdP integration, full session
issuance, payment/invoice integration, desktop billing UI, full operator
console, real provider price tables, and retained target-account KMS smoke
evidence as remaining commercial readiness work.
