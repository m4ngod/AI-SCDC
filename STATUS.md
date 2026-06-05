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

- `docs/operations/aliyun-operational-runbook.md`
- `docs/operations/aliyun-ram-policies.md`

## Verification

- `pytest apps/api/tests/test_cloud_run_api.py -q -k "retained_receipt_recovery or terminal_cleanup or aliyun_mns_completion_delete_failure or aliyun_eci_submission_cleans_up"` -> pending final run
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns or protected_aliyun or protected_worker or aliyun_eci"` -> pending final run
- `pytest apps/api/tests/test_aliyun_clients.py -q` -> pending final run
- `pytest apps/api/tests/test_remote_worker.py -q` -> pending final run
- `pytest apps/api/tests -q` -> pending final run
- `pnpm --filter @ai-scdc/desktop test -- client.test.ts` -> pending final run
- `pnpm typecheck` -> pending final run
- `git diff --check` -> pending final run

## Warnings

- Existing `StarletteDeprecationWarning`: `starlette.testclient` warns that
  using `httpx` is deprecated and recommends `httpx2`.
