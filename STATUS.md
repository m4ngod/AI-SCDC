# Phase 12C Status

## Scope

Completed Phase 12C documentation updates for Aliyun MNS pull-worker
capability and launch-mode clarification, including architecture notes,
README smoke-test notes, and this handoff status summary. The docs now state
that Phase 12C adds token-bearing MNS assignments and pull-mode support
without changing the current Aliyun ECI assigned-run launch default.

## Verification

- `pytest apps/api/tests/test_aliyun_clients.py -q` -> `15 passed in 0.08s`
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns or protected_worker_claim or external_stub"` -> `26 passed, 124 deselected, 1 warning in 12.19s`
- `pytest apps/api/tests/test_remote_worker.py -q` -> `48 passed in 0.28s`
- `pytest apps/api/tests -v` -> `454 passed, 1 warning in 234.71s`
- `pnpm --filter @ai-scdc/desktop test -- client.test.ts` -> `1 file passed, 34 tests passed`
- `pnpm typecheck` -> passed for `packages/agent-protocol` and `apps/desktop`
- `git diff --check` -> no whitespace errors; Git printed existing LF/CRLF conversion warnings for `README.md` and `docs/architecture.md`
- `rg -n "Phase 12C|MNS pull|assigned-run|AI_SCDC_CLOUD_RUN_ID|AI_SCDC_MNS_WAIT_SECONDS" README.md docs/architecture.md STATUS.md` -> confirmed the corrected launch-mode wording appears in all three docs

## Warnings

- Existing `StarletteDeprecationWarning`: `starlette.testclient` warns that
  using `httpx` is deprecated and recommends `httpx2`.
- Existing Git working-copy warning: `README.md` and `docs/architecture.md`
  will be normalized from LF to CRLF on a future Git write.
