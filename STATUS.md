# Phase 12D Artifact Plane Status

## Scope

Phase 12D completes the remaining original Phase 12 artifact plane targets
after Phase 12A, 12B, 12C, and 13A. The API exposes cloud-run artifact
manifests, artifact list/detail/content endpoints, provider-neutral download
descriptors, retention metadata, and expired-artifact cleanup. The desktop task
board can display manifest artifacts and open text previews.

The artifact plane keeps provider-specific storage operations behind the
existing object-storage boundary. It returns local API download descriptors
instead of signed provider URLs, redacts provider URI query strings and
fragments for display, deletes expired `local_inline` rows, and reports external
provider cleanup as lifecycle-only operator intent.

## Phase 13A Carry-Forward Operations

Phase 13A Aliyun MNS/ECI cleanup helpers remain service-level only; they are
not public destructive HTTP routes. OSS cleanup remains lifecycle- and
operator-owned, while Phase 12D expired-artifact cleanup is limited to local
`local_inline` rows and does not delete provider objects.

`DevSecretVault` remains development-only. Production use requires a
KMS-backed `SecretVault` implementation before commercial beta.

Operator references:

- [Aliyun operational runbook](docs/operations/aliyun-operational-runbook.md)
- [Aliyun RAM policy examples](docs/operations/aliyun-ram-policies.md)

## Verification

- `pytest apps/api/tests/test_cloud_run_api.py -q -k "artifact_manifest or artifact_content or download_is_local or cleanup_expired or returns_gone"`: 11 passed, 161 deselected, 1 warning in 5.63s.
- `pytest apps/api/tests/test_cloud_object_storage.py -q`: 11 passed in 4.35s.
- `pytest apps/api/tests -q`: 479 passed, 1 warning in 191.96s (0:03:11).
- `pnpm --filter @ai-scdc/desktop test -- App.test.tsx client.test.ts`: 2 test files passed, 75 tests passed in 10.89s.
- `pnpm typecheck`: passed; `apps/desktop` and `packages/agent-protocol` completed.
- `git diff --check`: passed; emitted Git LF-to-CRLF working-copy warnings for edited files.

## Warnings

- Existing `StarletteDeprecationWarning`: `starlette.testclient` warns that
  using `httpx` with `starlette.testclient` is deprecated and recommends
  `httpx2`.
- Git reported LF-to-CRLF working-copy warnings for edited files during
  `git diff --check`.
