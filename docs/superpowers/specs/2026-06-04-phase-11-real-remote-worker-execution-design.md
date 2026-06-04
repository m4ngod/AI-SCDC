# Phase 11 Real Remote Worker Execution Design

## Summary

Phase 11 upgrades the Aliyun ECI remote worker from a deterministic smoke patch
into a real execution skeleton. The worker will claim a run, fetch a protected
execution payload, clone the GitHub repository, check out the configured branch,
run the selected sandbox profile commands, capture a diff, upload artifacts, and
complete the lease.

This phase keeps the existing approval, PR, model, and provider boundaries. It
does not push, merge, create PRs, call models, consume MNS messages directly, or
add live log streaming.

## Current State

Phase 10D protects remote worker callbacks with a run-scoped callback token. The
current remote worker flow is:

1. Read API URL, cloud run ID, worker ID, queue/storage provider, and callback
   token from environment variables.
2. Claim a protected lease.
3. Heartbeat once.
4. Generate a deterministic diff for `AI_SCDC_ALIYUN_ECI.md`.
5. Upload that diff as an artifact ref.
6. Complete the lease.

That proves the callback, lease, and artifact-ref path, but it does not prove
real repository checkout, command execution, test execution, or command-result
redaction in a remote worker.

## Design Choice

Use a protected worker payload API.

The API will add:

```text
POST /cloud-run-worker/leases/{lease_id}/payload
```

The request body includes `worker_id` and `callback_token`. The endpoint reuses
the Phase 10D callback-token validation and only returns payload for the current
lease. `POST` is used so the callback token never appears in URL paths or query
strings.

This is preferred over putting the full payload in the lease response because
the lease response should stay a small control-plane object. The execution
payload includes sensitive clone material and command configuration, so it needs
its own narrow endpoint and tests.

## Worker Payload

The payload response includes only the data the remote worker needs:

- `cloud_run_id`
- `task_id`
- `title`
- `description`
- `repo_url`
- `github_owner`
- `github_repo`
- `base_branch`
- `head_branch`
- `allowed_paths`
- `required_tests`
- `patch_command`
- `test_commands`
- `env`
- `network_enabled`
- `clone_token`

`clone_token` is the current repository's active GitHub credential opened by the
API-side vault. It is returned only to a worker that holds the run-scoped
callback token. It is never persisted in new columns, never returned in
cloud-run read schemas, and never sent in completion payloads.

For V1, the endpoint requires a GitHub repository with an active credential.
Public-repository execution without a credential can be added later, but the
first real closed loop is designed to cover the private-repo path because that
is the harder and more commercially relevant case.

## API Components

Add a focused service module instead of growing `cloud_runner.py` further:

```text
apps/api/app/ai_company_api/services/remote_worker_payload.py
```

Responsibilities:

- Load the current `CloudRun`, `Task`, `Repository`, and `SandboxProfile`.
- Verify the lease belongs to `worker_id`.
- Verify the callback token using the same Phase 10D rules.
- Select patch and test commands from the sandbox profile using existing
  semantics.
- Read allowed sandbox environment variables from the API process environment.
- Open the active GitHub credential through `DevSecretVault`.
- Return a Pydantic response model with the worker execution payload.

`routes.py` will expose the payload endpoint and keep the existing lease,
heartbeat, artifact upload, and completion endpoints unchanged.

## Worker Components

Keep `remote_worker.py` as the entrypoint, but split the real execution logic
into small units:

- `RemoteWorkerClient`: HTTP control-plane client for claim, payload, heartbeat,
  artifact upload, and complete.
- `RemoteWorkerExecutor`: orchestration for payload -> checkout -> commands ->
  diff -> artifacts -> completion.
- `RemoteWorkerGitCheckout`: clone, checkout base branch, create head branch.
- `RemoteWorkerCommandRunner`: run patch and test commands in a temporary repo
  directory with timeouts.
- `RemoteWorkerArtifactBuilder`: convert diff, command results, test results,
  logs, and manifest into artifact uploads.

The production worker runs commands directly inside the ECI worker container.
It does not start Docker inside ECI. The worker container is the remote sandbox
boundary for this phase.

The existing deterministic behavior remains available in tests through fake
clients and fake executor implementations. It is not the default production
path once a payload endpoint is available.

## Execution Flow

1. Worker starts with `AI_SCDC_API_BASE_URL`, `AI_SCDC_CLOUD_RUN_ID`,
   `AI_SCDC_WORKER_ID`, `AI_SCDC_QUEUE_PROVIDER`, `AI_SCDC_STORAGE_PROVIDER`,
   and `AI_SCDC_CALLBACK_TOKEN`.
2. Worker claims the lease with `callback_token`.
3. Worker requests the protected payload with `worker_id` and `callback_token`.
4. Worker heartbeats before checkout.
5. Worker clones `repo_url` into a temporary directory using a temporary
   `GIT_ASKPASS` script backed by `clone_token`.
6. Worker checks out `base_branch` and creates `head_branch`.
7. Worker runs the selected patch command.
8. Worker captures `git add -N .`, changed files, diff, base SHA, and head SHA.
9. Worker rejects completion as failed if no patch was produced or changed files
   violate `allowed_paths`.
10. Worker heartbeats before the test phase.
11. Worker runs selected test commands.
12. Worker uploads artifacts for `diff`, `command_result`, `test_result`, `log`,
    and `manifest`.
13. Worker completes the lease with artifact refs and redacted command results.

If heartbeat reports `cancel_requested`, the worker stops at the next command
boundary and completes with `status=failed` and `failure_reason=cancelled`.

## Artifact Contract

V1 uploads these artifact kinds through the existing worker artifact endpoint:

- `diff`: unified diff text.
- `command_result`: JSON list of clone, checkout, branch, patch, capture, and
  metadata command results.
- `test_result`: JSON list of test command results.
- `log`: plain-text execution summary with redacted command output.
- `manifest`: JSON document listing all uploaded artifact refs and basic
  execution metadata.

Completion continues to include the diff artifact ref. Inline `diff_text` stays
empty when a diff artifact ref is present.

## Redaction And Secret Handling

The worker redacts these secret sources from every command result, log artifact,
manifest, and completion payload:

- `clone_token`
- sandbox `env` values
- repository URL userinfo variants, if any
- callback token

The worker uses `GIT_ASKPASS` rather than embedding the token in the clone URL
or command string. The askpass file is deleted immediately after clone.

The API also redacts returned cloud-run fields and logs using the existing
external URI and command-result redaction paths. Tests must prove the clone
token and callback token are absent from:

- cloud-run read response
- worker completion payload
- uploaded log/manifest content
- command result command/stdout/stderr

## Error Handling

The worker maps failures to existing failure reasons where possible:

- clone, checkout, or branch failure: `repo_checkout_failed`
- patch command failure or timeout: `patch_command_failed`
- diff capture failure, disallowed path, or missing diff: `artifact_capture_failed`
  or `no_patch_produced`
- failed test command: `test_failed`
- heartbeat cancellation: `cancelled`
- unexpected worker exception: `remote_worker_failed`

For command/test failures after a diff exists, the worker still uploads
available artifacts and completes with a failed status so the API can preserve
diagnostic context. The API's existing completion logic decides whether a patch
artifact should be created for test failures.

## Compatibility

Existing Phase 10A-10D APIs remain compatible:

- Lease claim, heartbeat, artifact upload, and complete payload shapes remain
  valid.
- Existing deterministic `test_remote_worker.py` fake-client tests continue to
  pass after they are updated to cover payload fetching.
- `local_db`, `external_stub`, `remote_stub`, and local Docker processing remain
  unchanged.

The new payload endpoint is required only for the real remote worker execution
path.

## Non-Goals

- No direct MNS receive/delete worker loop.
- No live log streaming, SLS, SSE, or WebSocket.
- No production KMS implementation.
- No model-backed patch generation or debugging.
- No Git push, PR creation, merge, or branch publication from the worker.
- No second cloud provider.
- No broad `cloud_runner.py` split.

## Testing Strategy

Add API tests for:

- Payload endpoint requires `callback_token`.
- Wrong, expired, reused, or cross-run callback tokens are rejected.
- Payload endpoint returns repo, branch, sandbox commands, allowed paths, env,
  and clone token for the correct lease.
- Clone token and callback token are absent from cloud-run read responses and
  logs.
- Payload endpoint rejects repositories without active GitHub credentials in V1.

Add worker tests for:

- HTTP client sends callback token to the payload endpoint.
- Worker fetches payload after claiming a lease.
- Fake checkout/command/diff components produce uploaded diff, command-result,
  test-result, log, and manifest artifact refs.
- Worker completion contains artifact refs and redacted command results.
- Clone token, callback token, and sandbox env secret values are redacted from
  uploaded artifacts and completion payload.
- `cancel_requested` from heartbeat stops before the next command group.
- Patch command failure, test failure, no diff, and disallowed paths map to the
  expected failure reasons.

Final verification:

```bash
pytest apps/api/tests/test_remote_worker.py -v
pytest apps/api/tests/test_cloud_run_api.py -k "payload or callback_token or aliyun or artifact_ref or lease" -v
pytest apps/api/tests
pnpm typecheck
git diff --check
rg -n "ghp_|callback-token|AI_SCDC_CALLBACK_TOKEN|clone_token|AccessKey|ACCESS_KEY_SECRET" apps docs README.md
```

## Acceptance Criteria

- A protected worker payload endpoint exists and is covered by callback-token
  tests.
- A remote worker can execute through fakeable clone, command, diff, artifact,
  and completion components.
- Private GitHub clone credential is available only through the protected
  payload endpoint.
- The worker uploads diff, command-result, test-result, log, and manifest
  artifact refs.
- Completion can create the same API-side `PatchArtifact` from the uploaded diff
  ref as the deterministic smoke worker path.
- Secret values are absent from API responses, command results, uploaded
  artifacts, and completion payloads.
- Existing API tests and TypeScript typecheck pass.
