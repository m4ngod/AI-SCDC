# Phase 13A Aliyun Operational Hardening Design

## Summary

Phase 13A hardens the existing Aliyun MNS/OSS/ECI execution path for operator
use without widening the product boundary. It adds testable cleanup and
recovery seams, least-privilege RAM policy examples, provider failure runbooks,
and explicit production KMS boundaries.

This phase builds on Phase 12C:

- Queue-only `aliyun_mns` runs create token-bearing MNS assignments for pull
  workers.
- `aliyun_mns + aliyun_eci` runs use protected assigned-run mode and do not
  create an extra MNS delivery.
- MNS receipts are internal-only and are deleted by the API after terminal
  completion when a real pull delivery exists.
- Aliyun ECI submissions seed OSS manifest/log refs and already clean up a
  created container group if OSS seeding fails.

Phase 13A does not add organization auth, tenant RBAC, billing, a second cloud
provider, real KMS integration, model-backed reviewer/debugger agents, or a
public destructive operations API.

## Problem

The Aliyun path is functional, but operators still lack clear, test-backed
answers for common production questions:

1. Which provider resources can be cleaned up automatically or manually?
2. What happens when cleanup itself fails?
3. Which Aliyun RAM permissions should the API process and worker process
   receive?
4. How should MNS, OSS, and ECI failures be diagnosed without exposing
   callback tokens, queue receipts, access keys, signed URLs, or provider
   secrets?
5. Where is the future production KMS boundary, given the current development
   `DevSecretVault`?

The goal is to make the current single-provider path operationally reliable
before expanding into commercial control-plane work.

## Design Choice

Use a small operational hardening layer around existing provider seams instead
of adding public admin endpoints or broad infrastructure.

Phase 13A introduces three deliverables:

1. **Provider cleanup and recovery services** inside the API codebase, invoked
   from existing terminal or maintenance-safe code paths and exercised by fake
   clients in tests.
2. **Operations documentation** for Aliyun RAM policy, provider failure
   runbooks, cleanup decision rules, and KMS boundaries.
3. **Status and architecture updates** that mark operational hardening as
   complete while keeping auth/RBAC/billing deferred.

This keeps the blast radius narrow: the provider seams become more recoverable,
but no unauthenticated public route can delete cloud resources.

## Cleanup And Recovery Scope

### MNS Receipt Recovery

Phase 12C already stores `CloudRun.queue_receipt` only when a real MNS pull
delivery claims a lease. Terminal completion attempts to delete that receipt.
If deletion fails, terminal cloud-run state remains committed and the receipt
is retained internally.

Phase 13A makes that recovery path explicit:

- Add a service-level retry helper for terminal Aliyun MNS runs with a retained
  `queue_receipt`.
- On retry success, delete the MNS message, clear `queue_receipt`, and append a
  redacted log entry.
- On retry failure, retain terminal state and receipt, append a redacted log
  entry, and avoid exposing the receipt in public schemas.
- Keep wrong-token and cross-run protections unchanged.

This helper may be called by future authenticated admin tooling, but Phase 13A
does not expose it through a public unauthenticated route.

### ECI Container Cleanup

The Aliyun ECI runtime already attempts container-group deletion if a container
group is created but OSS manifest/log seeding fails. Phase 13A keeps that
behavior and adds clearer operational handling for persisted ECI jobs:

- Add a service-level cleanup helper for terminal `aliyun_eci` runs with a
  stored `runtime_job_id`.
- Treat cleanup as best-effort and idempotent. A cleanup failure must not
  change terminal cloud-run status.
- Append redacted log entries for attempted, skipped, succeeded, and failed
  cleanup.
- Do not automatically delete ECI container groups immediately on terminal
  completion if provider log sync may still need `DescribeContainerLog`.
  Operator documentation must describe when it is safe to run cleanup after
  logs have been synced or retention windows have expired.
- Never log Aliyun access keys, callback tokens, queue receipts, signed query
  strings, or raw provider exceptions.

Phase 13A does not add ECI list/discovery APIs. Cleanup uses persisted
`runtime_job_id` values only.

### OSS Artifact And Log Cleanup

The current OSS provider can put and read objects. It does not have a delete
object or delete prefix seam, and object lifecycle is safer to manage through
bucket policy until tenant/auth boundaries exist.

Phase 13A therefore does not add runtime OSS deletion code. It documents:

- Required private-bucket posture.
- Recommended development prefix lifecycle cleanup.
- Which object prefixes are written by API-controlled cloud runs.
- How to verify that run artifacts and logs are retained long enough before
  ECI cleanup.

This avoids adding broad object deletion before organization scoping and
authenticated operator controls exist.

## RAM Policy Boundary

Phase 13A documents separate least-privilege policy examples for two roles.

### API Control Plane Role

The API process needs permission to:

- Send MNS messages for queue-only pull runs.
- Receive/delete MNS receipts only through API-owned terminal acknowledgement
  or recovery paths.
- Put and read OSS manifest, log, diff, command-result, and test-result
  objects under the configured development prefix.
- Create ECI container groups, describe container logs, and delete known
  container groups by persisted id.

The policy example must scope resources to the configured region, queue,
bucket, object prefix, and ECI container group prefix where Aliyun supports
resource-level constraints.

### Pull Worker Role

A worker-pool process that runs in MNS pull mode needs only:

- Receive messages from the configured MNS queue.
- Delete the receipt it successfully completed through the API path only if
  future deployment chooses worker-side delete. The Phase 13A default remains
  API-owned receipt acknowledgement.
- Call the AI-SCDC API over HTTPS with the callback token from the MNS message.

The worker role does not need ECI create/delete, OSS read/write, model
credentials, or GitHub credentials from Aliyun RAM. GitHub clone credentials
remain available only through the callback-token-protected payload endpoint.

### Assigned ECI Worker

The current assigned-run ECI container receives `AI_SCDC_CLOUD_RUN_ID`,
`AI_SCDC_WORKER_ID`, and `AI_SCDC_CALLBACK_TOKEN`. It does not need MNS
credentials because it does not pull from MNS. It should not receive the API
process's Aliyun access key secret.

## Secret Vault And KMS Boundary

The codebase currently uses `DevSecretVault` for development secret sealing and
opening. Phase 13A does not replace it with a real KMS provider.

The design boundary is:

- Keep the existing `SecretVault` protocol as the code-facing abstraction.
- Document that production must provide a KMS-backed implementation before
  commercial beta.
- Do not add real KMS SDK dependencies in Phase 13A.
- Add docs and tests only for redaction and boundary behavior that already
  exists or is introduced by the cleanup services.
- Ensure runbooks never instruct operators to paste raw access keys, callback
  tokens, MNS receipts, or signed URLs into logs or tickets.

## Failure Runbooks

Create an Aliyun operations runbook that covers these cases:

- MNS enqueue fails during queue-only pull run creation.
- MNS receive returns empty queue.
- MNS receive returns malformed token-bearing payload.
- MNS terminal delete fails and leaves `queue_receipt` retained.
- OSS manifest/log seeding fails after ECI container creation.
- OSS read fails because URI is wrong, bucket mismatches, prefix mismatches, or
  content metadata does not match.
- ECI submission fails before container group id is available.
- ECI submission succeeds but later OSS seeding fails and cleanup is attempted.
- ECI log sync returns empty or non-text content.
- ECI delete fails during best-effort cleanup.

Each runbook entry must include:

- Operator symptom.
- Safe fields to inspect.
- Fields that must never be pasted into tickets or logs.
- Expected API state.
- Recovery action.
- Escalation when recovery cannot be completed safely.

## Data Flow

### Queue-Only MNS Pull Run

1. API validates `queue_provider=aliyun_mns` and a non-empty
   `storage_provider`.
2. API creates a protected worker id and callback token.
3. API stores only the callback token hash.
4. API sends a token-bearing MNS message.
5. Pull worker receives the message and claims with callback token,
   `queue_message_id`, and `queue_receipt`.
6. API stores the receipt only after message id binding succeeds.
7. Worker completes the lease through the existing protected endpoint.
8. API commits terminal state, then deletes the MNS receipt.
9. If delete fails, the terminal state remains committed and recovery can
   retry the receipt deletion.

### Assigned ECI Run

1. API creates a protected worker id and callback token.
2. API skips MNS enqueue for `aliyun_mns + aliyun_eci`.
3. API submits an ECI container group with assigned-run environment variables.
4. API seeds OSS manifest/log refs.
5. If OSS seed fails after ECI creation, the existing cleanup path attempts
   ECI deletion and redacts provider errors.
6. Worker claims with assigned-run credentials and no MNS delivery metadata.
7. Cleanup of terminal ECI resources remains explicit and best-effort because
   log sync may need the provider container log after terminal completion.

## Error Handling

Operational cleanup must follow these rules:

- Cleanup failure never rewinds a terminal cloud-run state.
- Cleanup failure never clears a still-needed internal receipt or runtime id.
- Cleanup success may clear only the internal field that is no longer needed,
  such as `queue_receipt` after MNS delete succeeds.
- Public response models must continue to omit `queue_receipt`.
- Logs must use existing redaction helpers for sensitive payload values and raw
  provider errors.
- Provider errors should be normalized to stable reason codes where behavior is
  tested, with detailed raw errors excluded from public responses.

## Documentation Updates

Phase 13A updates:

- `README.md` with concise operator smoke cleanup guidance and links.
- `docs/architecture.md` with a Phase 13A boundary and roadmap update.
- `docs/superpowers/status.md` with completed scope and next-phase guidance.
- A new `docs/operations/aliyun-operational-runbook.md` for failure recovery.
- A new `docs/operations/aliyun-ram-policies.md` for least-privilege examples.
- `STATUS.md` with handoff status and verification commands.

## Testing Strategy

All tests use fake clients and local SQLite. No automated test may call Aliyun.

Required test areas:

- MNS retained receipt recovery succeeds, clears only internal receipt, and
  does not expose receipt in API responses or logs.
- MNS retained receipt recovery failure keeps terminal status and receipt, logs
  a redacted failure, and exposes only a safe provider status.
- ECI cleanup helper deletes a known persisted `runtime_job_id` through the
  fake client and logs a redacted success.
- ECI cleanup helper failure keeps terminal status and runtime id, logs a
  redacted failure, and does not expose raw provider secrets.
- Existing ECI submission cleanup on OSS seed failure remains covered.
- Queue-only MNS still requires `storage_provider`.
- Assigned-run ECI remains able to claim without MNS delivery metadata.
- Docs contain the RAM policy examples and runbook sections.

Suggested verification commands:

```text
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns or aliyun_eci or cleanup"
pytest apps/api/tests/test_aliyun_clients.py -q
pytest apps/api/tests/test_remote_worker.py -q
pytest apps/api/tests -q
pnpm --filter @ai-scdc/desktop test -- client.test.ts
pnpm typecheck
git diff --check
```

## Acceptance Criteria

Phase 13A is complete when:

- Operators have a documented Aliyun runbook and RAM policy examples.
- The API has test-backed service seams for retrying retained MNS receipt
  deletion and best-effort ECI cleanup by persisted runtime id.
- Cleanup success and failure paths are redacted and do not leak callback
  tokens, queue receipts, Aliyun secrets, signed URLs, or raw provider errors.
- Terminal cloud-run state remains stable across cleanup failures.
- No new public destructive endpoint exists before auth/RBAC.
- Existing Phase 12C MNS pull and assigned-run ECI behavior remains green.

## Explicit Non-Goals

Phase 13A does not:

- Add real KMS or cloud secret-manager SDK integration.
- Add user auth, organizations, workspace membership, or RBAC.
- Add billing or rate-limit enforcement.
- Add provider discovery or a second cloud provider.
- Add SLS-managed logs, WebSockets, or Server-Sent Events.
- Delete OSS objects from code.
- Push branches, create pull requests, merge code, or alter worker execution
  semantics.
