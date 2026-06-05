# Phase 13A Status

## Scope

Completed Aliyun operational hardening for the current MNS/OSS/ECI execution
path. The API now has service-level helpers for retrying retained MNS receipt
deletion and best-effort ECI terminal cleanup by persisted runtime id. Cleanup
success and failure paths append redacted control-plane logs and do not expose
callback tokens, queue receipts, access keys, signed URLs, or raw provider
secrets.

No public destructive cleanup endpoint was added. OSS object cleanup remains a
bucket lifecycle responsibility until authenticated organization-scoped
operator controls exist. `DevSecretVault` remains development-only and must be
replaced by a KMS-backed `SecretVault` implementation before commercial beta.

## Operator Docs

- [Aliyun operational runbook](docs/operations/aliyun-operational-runbook.md)
- [Aliyun RAM policy examples](docs/operations/aliyun-ram-policies.md)

## Verification

- `pytest apps/api/tests/test_cloud_run_api.py -q -k "retained_receipt_recovery or terminal_cleanup or aliyun_mns_completion_delete_failure or aliyun_eci_submission_cleans_up"` -> 10 passed, 151 deselected, 1 warning in 5.77s
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns or protected_aliyun or protected_worker or aliyun_eci"` -> 41 passed, 120 deselected, 1 warning in 16.42s
- `pytest apps/api/tests/test_aliyun_clients.py -q` -> 15 passed in 0.05s
- `pytest apps/api/tests/test_remote_worker.py -q` -> 48 passed in 0.17s
- `pytest apps/api/tests -q` -> 465 passed, 1 warning in 196.92s
- `pnpm --filter @ai-scdc/desktop test -- client.test.ts` -> 34 passed in 1.81s
- `pnpm typecheck` -> `apps/desktop` and `packages/agent-protocol` completed
- `git diff --check` -> passed
- `rg -n "retry_retained_mns_queue_receipt_delete|cleanup_aliyun_eci_terminal_runtime_job" apps/api/app/ai_company_api/api/routes.py` -> no direct route-level provider helper references found

## Warnings

- Existing `StarletteDeprecationWarning`: `starlette.testclient` warns that
  using `httpx` is deprecated and recommends `httpx2`.
