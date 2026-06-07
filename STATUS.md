# Phase 13C Cost Guardrail Status

## Scope

Phase 13B Commercial Trust Boundary has started with test-backed identity,
workspace-scope, and secret-access audit foundations. The API now has `User`,
`Organization`, `Workspace`, and `OrganizationMember` models, workspace roles,
request auth context, explicit dev auth mode, API-token member lookup mode, and
`/me` responses based on the current auth context instead of a hard-coded route
body.

The current slice scopes project, repository, task, cloud-run, artifact,
GitHub credential, model provider, model credential, model route, usage ledger,
patch approval, review/debug, and pull-request reads/writes to the active
workspace. Worker callback endpoints keep their existing run-scoped callback
token boundary.

A follow-on Phase 13B slice extends the `SecretVault` protocol with
`rotate`, `delete`, and `fingerprint`, adds a fail-closed provider factory,
adds a not-configured `KmsSecretVault` placeholder for `kms`/`aliyun_kms`, and
records `SecretAccessAuditLog` rows for model/GitHub credential create/delete
plus model planner, GitHub pull-request, Docker cloud-run, and remote-worker
payload credential opens. Audit rows record actor/scope metadata, secret
kind/id, reason, operation, and success status, never raw or encrypted secret
payloads.

Phase 13B now also includes a narrow authenticated cloud-run operator API
facade. Workspace owners and admins can retry retained Aliyun MNS receipt
deletion with
`POST /cloud-runs/{cloud_run_id}/operator/retry-mns-receipt-delete` and request
best-effort Aliyun ECI terminal runtime cleanup with
`POST /cloud-runs/{cloud_run_id}/operator/cleanup-aliyun-eci-runtime`. The
operator response uses a redacted snapshot and does not expose runtime job ids,
queue receipts, callback tokens, raw provider errors, Aliyun access keys, or
raw provider URLs.

Phase 13C has started the cost/quota guardrail foundation. `UsageType` now
covers execution-plane dimensions, `CreditWallet`, `SpendLimit`, and
`BudgetReservation` records protect cloud-run enqueue, and cloud runs expose
reservation-backed estimated, measured, and billable cost summaries. Workspace
usage summaries aggregate usage by project/task and usage type.

## Non-Goals

This is not the full commercial beta trust boundary yet. Remaining Phase 13B
work includes full login/session issuance, production auth/IdP integration,
real KMS-backed `SecretVault` provider integration, a full operator console,
public destructive OSS cleanup, broader audit coverage, and a complete role
permission matrix.

No billing provider, payment flow, invoices, real provider price table, second
cloud provider, public destructive provider cleanup endpoint, WebSocket/SSE log
streaming, desktop billing UI, or model-backed review/debug/coder loop was
added. No real KMS SDK is wired yet.

## Verification

- `pytest apps/api/tests/test_auth_rbac_api.py -q`: 10 passed, 1 warning in 4.80s.
- `pytest apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_api_endpoints.py apps/api/tests/test_model_settings_api.py apps/api/tests/test_github_repository_api.py apps/api/tests/test_usage_ledger_api.py -q`: 75 passed, 1 warning in 34.66s.
- `python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_api_endpoints.py`: passed.
- `pytest apps/api/tests/test_secret_access_audit.py -q`: 9 passed, 1 warning in 2.65s.
- `pytest apps/api/tests/test_secret_access_audit.py apps/api/tests/test_model_settings_api.py apps/api/tests/test_github_repository_api.py apps/api/tests/test_model_planner.py apps/api/tests/test_planner_endpoints.py apps/api/tests/test_pull_request_api.py -q`: 103 passed, 1 warning in 19.01s.
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "docker_cloud_run_enqueue_stores_metadata_without_opening_token or docker_cloud_run_validates_profile_before_opening_github_token"`: 2 passed, 170 deselected, 1 warning in 4.57s.
- `pytest apps/api/tests/test_model_settings_api.py apps/api/tests/test_github_repository_api.py apps/api/tests/test_planner_endpoints.py apps/api/tests/test_pull_request_api.py -q`: 73 passed, 1 warning in 49.83s.
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "remote_worker_payload or docker_cloud_run or github_token or protected_worker"`: 30 passed, 142 deselected, 1 warning in 16.47s.
- `python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_secret_access_audit.py apps/api/tests/test_model_planner.py apps/api/tests/test_cloud_run_api.py`: passed.
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "cloud_run_operator or retained_receipt_recovery or terminal_cleanup"`: 12 passed, 165 deselected, 1 warning in 10.97s.
- `pytest apps/api/tests/test_auth_rbac_api.py apps/api/tests/test_cloud_run_api.py -q -k "operator or money_moving_workspace_endpoints_require_billing_role"`: 5 passed, 183 deselected, 1 warning in 4.41s.
- `python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_cloud_run_api.py`: passed.
- `pytest apps/api/tests/test_cloud_run_api.py apps/api/tests/test_auth_rbac_api.py -q -k "cloud_run_operator or retained_receipt_recovery or terminal_cleanup or money_moving_workspace_endpoints_require_billing_role"`: 13 passed, 175 deselected, 1 warning in 11.03s.
- `pytest apps/api/tests/test_usage_ledger_api.py apps/api/tests/test_usage_cost_quota_api.py -q`: 46 passed, 1 warning in 21.67s.
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "enqueue or cancel or process or completion or lease"`: 50 passed, 123 deselected, 1 warning in 76.56s.
- `pytest apps/api/tests/test_usage_ledger_api.py apps/api/tests/test_usage_cost_quota_api.py apps/api/tests/test_api_endpoints.py apps/api/tests/test_pull_request_api.py apps/api/tests/test_planner_endpoints.py -q`: 60 passed, 1 warning in 16.99s.
- `pytest apps/api/tests -q`: 536 passed, 1 warning in 264.56s.
- `python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_usage_cost_quota_api.py apps/api/tests/test_usage_ledger_api.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_auth_rbac_api.py`: passed.
- `pnpm typecheck`: passed; `apps/desktop` and `packages/agent-protocol` completed.
- `git diff --check`: passed; emitted Git LF-to-CRLF working-copy warnings for edited files.

## Warnings

- Existing `StarletteDeprecationWarning`: `starlette.testclient` warns that
  using `httpx` with `starlette.testclient` is deprecated and recommends
  `httpx2`.
- Git reported LF-to-CRLF working-copy warnings for edited files during
  `git diff --check`.
