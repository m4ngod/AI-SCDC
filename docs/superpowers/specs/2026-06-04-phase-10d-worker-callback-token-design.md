# Phase 10D Worker Callback Token Design

## Purpose

Phase 10D adds run-scoped worker callback authentication after Phase 10C-H is
complete. It closes the current remote worker endpoint gap where possession of a
current `lease_id` and `worker_id` is enough to claim, heartbeat, upload
artifacts, and complete a cloud run.

## Current Baseline

Worker endpoints currently accept callback payloads through these schemas:

- `CloudRunLeaseCreate`
- `CloudRunLeaseHeartbeat`
- `CloudRunArtifactUploadCreate`
- `CloudRunLeaseComplete`

The service validates `lease_id`, `worker_id`, running status, and lease expiry.
It does not generate a run-scoped callback secret, store a token hash, inject a
raw token into remote runtime environment, or invalidate the token on completion.

## Scope

Phase 10D will implement the minimum callback security boundary:

- Generate a one-time callback token for remote cloud runs that use a remote
  runtime provider.
- Store only a SHA-256 hash on `CloudRun`.
- Inject the raw token only into remote worker environment.
- Require `callback_token` on claim, heartbeat, artifact upload, and complete for
  token-protected runs.
- Bind token validation to `cloud_run_id` and `worker_id`.
- Expire token after completion or cancellation.
- Reject missing, wrong, expired, reused, and cross-run tokens.

## Non-Goals

This phase does not add JWT user auth, signed callback payloads, KMS, production
secret storage, real remote repo execution, model-backed reviewer/debugger
agents, billing, or MNS worker pull semantics.

## Design

### Token Storage

Add these nullable columns to `CloudRun`:

- `callback_token_hash`
- `callback_token_expires_at`
- `callback_token_used_at`

SQLite upgrade support follows the existing `init_db()` pattern used for Phase
9, 10A, and 10B cloud-run columns.

### Token Generation

When `start_cloud_run()` creates a run with a remote runtime provider, generate a
URL-safe random token and store only:

```text
sha256(f"{cloud_run_id}:{worker_id}:{raw_token}")
```

The raw token is held in process memory only long enough to build the remote
runtime submission environment.

### Runtime Environment

`RemoteRuntimeSubmission` should carry:

- `worker_id`
- `callback_token`
- `callback_token_expires_at`

`AliyunEciRuntimeProvider` injects the raw token into:

```text
AI_SCDC_CALLBACK_TOKEN
```

It must not inject Aliyun AccessKeys.

### Endpoint Behavior

For token-protected cloud runs:

- Claim requires `cloud_run_id`, `worker_id`, and `callback_token`.
- Heartbeat requires `worker_id` and `callback_token`.
- Artifact upload requires `worker_id` and `callback_token`.
- Complete requires `worker_id` and `callback_token`.
- Missing or wrong token returns 401 or 403.
- Expired or reused token returns 403.
- Cross-run token returns 403.
- Successful completion sets `callback_token_used_at`.
- Cancellation also sets `callback_token_used_at` when a token hash exists.

For unprotected local/stub development runs, existing tests may continue to omit
the token. This keeps backward compatibility for local deterministic tests while
protecting remote-provider runs.

### Remote Worker

`RemoteWorkerConfig` gains `callback_token`. `config_from_env()` reads
`AI_SCDC_CALLBACK_TOKEN`. `HttpRemoteWorkerClient` includes it in claim,
heartbeat, upload, and complete payloads.

## Acceptance Criteria

- Missing token is rejected on protected worker endpoints.
- Wrong token cannot claim, heartbeat, upload, or complete.
- A token cannot be used for another cloud run.
- Expired token cannot heartbeat or complete.
- Successful completion invalidates the token.
- Local/stub tests remain compatible.
- Existing Phase 10C-H and Phase 10C tests pass.
