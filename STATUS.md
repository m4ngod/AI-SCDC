# Phase 12C Status

## Scope

Completed Phase 12C final-review fixes for Aliyun MNS pull-worker capability
and launch-mode clarification. The API now keeps the default
`aliyun_mns + aliyun_eci` launch on the protected assigned-run path without
creating an extra MNS delivery, while queue-only `aliyun_mns` runs create a
token-bearing MNS assignment for external pull workers. MNS pull claims now
bind the supplied receipt to the persisted enqueue message ID before storing
the receipt for post-terminal acknowledgement. Queue-only MNS submissions must
provide a storage provider, and legacy ECI assigned-run records that already
persisted an MNS message ID can still claim with assigned-run credentials.

## Verification

- `pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_eci_assigned_run_does_not_enqueue_mns_message or aliyun_mns_queue_provider_sends_message_on_enqueue or aliyun_mns_enqueue_commits_protected_callback_metadata_before_send or aliyun_eci_runtime_submission_creates_safe_container_request or aliyun_provider_mvp_enqueue_persists_non_sensitive_metadata or aliyun_mns_claim_persists_receipt or aliyun_mns_claim_rejects_mismatched_message_id or aliyun_mns_completion_deletes_receipt"` -> `9 passed, 142 deselected, 1 warning in 7.36s`
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns or protected_aliyun or protected_worker or aliyun_eci"` -> `31 passed, 120 deselected, 1 warning in 15.09s`
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "queue_provider_sends_message_on_enqueue or queue_only_requires_storage_provider or mns_queue_provider_failure_is_controlled or legacy_aliyun_eci_assigned_run_with_stored_mns_message_id_can_claim"` -> `4 passed, 149 deselected, 1 warning in 3.94s`
- `pytest apps/api/tests/test_cloud_run_api.py -q` -> `153 passed, 1 warning in 127.71s`
- `pytest apps/api/tests/test_aliyun_clients.py -q` -> `15 passed in 0.06s`
- `pytest apps/api/tests/test_remote_worker.py -q` -> `48 passed in 0.24s`
- `pytest apps/api/tests -q` -> `457 passed, 1 warning in 197.80s`
- `pnpm --filter @ai-scdc/desktop test -- client.test.ts` -> `1 file passed, 34 tests passed`
- `pnpm typecheck` -> passed for `packages/agent-protocol` and `apps/desktop`
- `git diff --check` -> no whitespace errors; Git reported Windows CRLF conversion warnings for touched files

## Warnings

- Existing `StarletteDeprecationWarning`: `starlette.testclient` warns that
  using `httpx` is deprecated and recommends `httpx2`.
