# Phase 10C Aliyun Provider MVP Design

## Purpose

Phase 10C attaches the first concrete production provider stack to the Phase 10B
remote execution-plane contracts. The selected stack is Aliyun MNS for queueing,
Aliyun OSS for artifact storage, Aliyun ECI for short-lived remote container
execution, and Aliyun ACR for the worker image.

The goal is to prove that a cloud run can leave the local API process, be
represented in an external queue, launch a remote container, store logs and
artifacts in object storage, and complete through the existing cloud-run worker
completion API without changing the public cloud-run lifecycle.

Target flow:

```text
User starts cloud run
  -> API creates CloudRun with aliyun_mns, aliyun_oss, aliyun_eci metadata
  -> Aliyun MNS receives a message for the run
  -> Aliyun ECI launches a short-lived worker container from ACR
  -> worker claims/heartbeats/completes through existing API worker endpoints
  -> worker writes manifest, logs, command results, and diff refs to OSS
  -> API finalizes patch/test/task state through existing Phase 9/10A/10B logic
```

## Confirmed Decisions

- Use Aliyun as the first concrete production provider stack.
- Use `aliyun_mns` as the queue provider name.
- Use `aliyun_oss` as the object storage provider name.
- Use `aliyun_eci` as the remote runtime provider name.
- Keep `local_db`, `external_stub`, `local_inline`, `remote_stub`, `fake`, and
  `docker_local` available as deterministic development adapters.
- Do not create long-lived ECI instances by hand in the console. The API will
  create short-lived ECI containers only when a cloud run requests the Aliyun
  runtime provider.
- Store Aliyun configuration and credentials in local environment variables for
  the MVP. Do not commit AccessKey values or paste them into logs, docs, tests,
  chat, or shell history.
- Use fake Aliyun clients in automated tests. Real Aliyun calls are allowed only
  through explicit smoke commands that require the operator to set environment
  variables intentionally.

## Current Baseline

Phase 10B provides provider-neutral contracts but only deterministic local
providers:

- `CloudRun` records queue, storage, runtime, artifact manifest, log stream,
  external status, and external error metadata.
- `CloudRunRead` exposes non-sensitive provider metadata and never exposes queue
  receipts.
- `local_db` preserves the Phase 10A local lease contract.
- `external_stub` simulates external queue metadata without a cloud dependency.
- `local_inline` stores text artifacts in the local database behind
  `local-inline://` URIs.
- `remote_stub` records deterministic runtime job metadata and can write
  local-inline manifest/log refs.
- Remote completion accepts `artifact_refs`, resolves storage refs, and finalizes
  through the existing patch/test/task state logic.

Phase 10C should implement the real provider stack behind these boundaries
rather than replacing the worker API or desktop workflow.

## Selected Provider Stack

### Queue: Aliyun MNS

`aliyun_mns` maps cloud-run queue operations to a configured MNS queue.

Responsibilities:

- Build a queue message for a cloud run using only non-sensitive IDs and provider
  names.
- Send the message when an Aliyun cloud run is enqueued.
- Persist the MNS message ID as `CloudRun.queue_message_id` when available.
- Persist the receipt handle internally as `CloudRun.queue_receipt` only where
  the SDK returns a receipt-bearing operation.
- Keep receipts out of read schemas and logs.
- Map transient SDK failures to redacted `external_error` values.

The first implementation can use the existing API-side lease endpoints as the
source of truth for worker claim, heartbeat, requeue, and completion while MNS is
the external wake-up queue. This keeps the public API stable and avoids changing
Phase 10A lease semantics before the worker loop is proven.

### Storage: Aliyun OSS

`aliyun_oss` stores large text and JSON artifacts in a private OSS bucket.

Responsibilities:

- Write artifact kinds already accepted by Phase 10B: `diff`, `log`,
  `command_result`, `test_result`, and `manifest`.
- Generate stable object keys under a cloud-run prefix:
  `workspaces/{workspace_id}/cloud-runs/{cloud_run_id}/{kind}/{sha256}.txt` or
  `.json` depending on content type.
- Return `oss://{bucket}/{object_key}` refs with SHA-256, byte size, content
  type, and kind.
- Read refs by validating scheme, bucket, object key prefix, kind, SHA-256, and
  size before returning text content.
- Never expose signed URLs in API read responses in the MVP.

The OSS bucket must be private. Lifecycle cleanup should be configured in the
Aliyun console, for example 7 or 30 days for development artifacts.

### Runtime: Aliyun ECI

`aliyun_eci` submits a short-lived ECI container group using an image stored in
ACR.

Responsibilities:

- Create one ECI container group per Aliyun cloud run.
- Use the configured ACR image for the AI-SCDC remote worker.
- Pass only non-secret cloud-run metadata and an API callback URL into the
  container environment.
- Use RAM role or tightly scoped AccessKey configuration for the API-side ECI
  create call. The worker container should not receive broad cloud credentials
  in environment variables.
- Store the ECI container group ID as `CloudRun.runtime_job_id`.
- Store submission state such as `submitted`, `submit_failed`, and provider
  failure messages in `external_status` and redacted `external_error`.
- Write an OSS manifest/log seed when `storage_provider == "aliyun_oss"`.

The worker image is not a model-serving image. It is a normal application image
that runs AI-SCDC remote-worker code, polls or claims the assigned run, executes
the existing sandbox flow, uploads artifacts, and calls the API completion
endpoint.

## Aliyun Console Setup

The operator has already opened RAM, OSS, MNS, ACR, and ECI. The MVP assumes
these services exist but does not require manually creating an ECI instance.

Recommended development setup:

- Region: keep MNS, OSS, ACR, VPC, VSwitch, security group, and ECI in one
  region, such as `cn-hangzhou`, to reduce configuration and network surprises.
- RAM: create a dedicated user or role for the API process. Grant the smallest
  practical permissions for MNS queue access, OSS object access to the selected
  bucket/prefix, and ECI container-group creation/destruction.
- OSS: create a private bucket such as `ai-scdc-dev-artifacts`; enable lifecycle
  cleanup for development prefixes.
- MNS: create a queue such as `ai-scdc-cloud-runs-dev`; use queue mode rather
  than topic mode.
- ACR: create a private repository for the remote worker image. Do not bind
  GitHub auto-build until the Dockerfile and build rules exist.
- ECI: do not create a console instance during development. Confirm the service
  is enabled, then let the API submit ECI container groups.
- Network: use VPC and VSwitch. Do not expose inbound public ports for the worker.
  If workers need GitHub or package registry access, provide outbound access via
  NAT gateway plus EIP or another controlled outbound path.

## Configuration

Use environment variables for MVP configuration. Missing required Aliyun
configuration must make the Aliyun provider unavailable with a clear 400 response
when selected, while default local providers continue to work.

Required for Aliyun providers:

```text
AI_SCDC_ALIYUN_REGION_ID=cn-hangzhou
AI_SCDC_ALIYUN_ACCESS_KEY_ID=<set locally>
AI_SCDC_ALIYUN_ACCESS_KEY_SECRET=<set locally>
AI_SCDC_ALIYUN_MNS_ENDPOINT=https://<account-id>.mns.cn-hangzhou.aliyuncs.com
AI_SCDC_ALIYUN_MNS_QUEUE_NAME=ai-scdc-cloud-runs-dev
AI_SCDC_ALIYUN_OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
AI_SCDC_ALIYUN_OSS_BUCKET=ai-scdc-dev-artifacts
AI_SCDC_ALIYUN_ECI_VSWITCH_ID=<vsw-id>
AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID=<sg-id>
AI_SCDC_ALIYUN_ECI_IMAGE=<acr-registry>/<namespace>/<repo>:<tag>
AI_SCDC_API_PUBLIC_BASE_URL=<operator-reachable API base URL>
```

Optional:

```text
AI_SCDC_ALIYUN_ECI_CPU=1
AI_SCDC_ALIYUN_ECI_MEMORY_GB=2
AI_SCDC_ALIYUN_ECI_CONTAINER_GROUP_PREFIX=ai-scdc-run
AI_SCDC_ALIYUN_OSS_PREFIX=ai-scdc/dev
AI_SCDC_ALIYUN_ECI_NAT_REQUIRED=false
```

Secrets must be redacted by existing redaction helpers and by new Aliyun-specific
error handling before they enter logs, API responses, or persisted external
metadata.

## API Behavior

Starting a cloud run with omitted providers keeps existing behavior.

Starting a cloud run with Aliyun providers:

```json
{
  "repo_id": "repo_123",
  "queue_provider": "aliyun_mns",
  "storage_provider": "aliyun_oss",
  "runtime_provider": "aliyun_eci"
}
```

Expected behavior:

- The API validates Aliyun provider names and required configuration.
- The API creates the `CloudRun` as `queued`.
- `aliyun_mns` sends a cloud-run message and stores non-sensitive queue metadata.
- `aliyun_eci` submits the remote worker container group and stores runtime
  metadata.
- `aliyun_oss` writes an initial manifest/log seed if runtime submission creates
  provider refs.
- The returned `CloudRunRead` includes `queue_provider`, `queue_message_id`,
  `runtime_provider`, `runtime_job_id`, `storage_provider`,
  `artifact_manifest_uri`, `log_stream_uri`, and `external_status`.
- The returned data never includes AccessKeys, queue receipts, signed URLs,
  ACR credentials, or raw SDK error payloads.

Provider validation failures must return 400. Unexpected SDK failures during
submission should leave a durable failed or queued-with-error state that can be
diagnosed without exposing secrets.

## Remote Worker MVP

The worker container can be implemented as a small Python entry point in the API
package or a sibling service. Its first responsibility is to prove the callback
contract, not to add new model autonomy.

Worker startup input:

- `AI_SCDC_API_BASE_URL`
- `AI_SCDC_CLOUD_RUN_ID`
- `AI_SCDC_WORKER_ID`
- `AI_SCDC_QUEUE_PROVIDER=aliyun_mns`
- `AI_SCDC_STORAGE_PROVIDER=aliyun_oss`

Worker behavior:

1. Claim or load the assigned cloud run through the existing worker API.
2. Heartbeat while working.
3. Execute the same logical sandbox path already used by `docker_local` where
   possible, with command whitelist and artifact semantics preserved.
4. Write large artifacts to OSS and complete with `artifact_refs`.
5. Complete with failure details if clone, command execution, artifact upload, or
   completion callback fails.

The MVP worker should not create pull requests, merge code, or call model
providers independently. It only produces patch/test artifacts for the existing
approval and PR boundaries.

## Error Handling

- Missing Aliyun configuration: reject provider selection with 400 and a
  non-secret message naming the missing logical setting, not the secret value.
- SDK authentication/authorization failure: persist a redacted external error and
  return a safe API error.
- MNS send failure: cloud run does not silently claim success; store failure
  status and log a redacted provider event.
- OSS write failure: completion with required artifact refs fails before patch
  artifact creation.
- OSS read mismatch: reject the artifact ref before finalization.
- ECI submit failure: persist `external_status = "submit_failed"` and a redacted
  `external_error`.
- Worker callback failure: worker retries according to a bounded policy, then
  leaves the run for lease expiry/requeue.
- Cancellation: existing queued/running cancellation request behavior remains the
  source of truth; worker heartbeat continues to report cancellation requests.

## Security Boundaries

- Do not commit or log Aliyun AccessKeys.
- Do not pass broad Aliyun AccessKeys into the worker container.
- Do not expose queue receipts in public API responses.
- Do not expose signed OSS URLs in Phase 10C MVP responses.
- Do not open inbound ports on ECI worker containers.
- Do not grant OSS bucket-wide public read.
- Prefer RAM roles or tightly scoped RAM users over root account credentials.
- Keep GitHub PAT handling inside the existing credential boundary.
- Preserve existing command whitelist, allowed environment variable, redaction,
  and patch approval boundaries.

## Cost Boundaries

Phase 10C development should use pay-as-you-go resources only.

- MNS costs are driven by queue requests and retained resources.
- OSS costs are driven by stored objects, requests, and traffic.
- ECI costs are driven by container group runtime and selected CPU/memory.
- NAT gateway and EIP can create ongoing charges even when workers are idle.
- ACR Personal Edition is acceptable for development; production can move to a
  paid instance if needed.

The smoke test documentation must include cleanup steps for ECI container groups,
OSS development prefixes, MNS test messages, and unused NAT/EIP resources.

## Testing Strategy

Automated tests must not call Aliyun.

Required test coverage:

- Provider registries accept `aliyun_mns`, `aliyun_oss`, and `aliyun_eci`.
- Unknown provider behavior remains unchanged.
- Missing Aliyun configuration returns safe provider errors.
- OSS provider writes deterministic object keys, records SHA-256/size metadata,
  and validates reads using fake OSS clients.
- OSS provider rejects wrong scheme, bucket, prefix, kind, hash, and size.
- MNS provider serializes safe messages and redacts SDK errors.
- ECI runtime provider builds a safe container-group creation request with the
  configured image, VSwitch, security group, CPU, memory, labels, and env.
- ECI runtime provider never includes AccessKey secrets in container env.
- Cloud-run enqueue with Aliyun providers persists expected metadata and keeps
  `queue_receipt` out of read responses.
- Remote completion with `oss://` artifact refs finalizes through existing
  artifact-ref logic.
- Existing local, stub, desktop, and typecheck suites still pass.

Manual smoke coverage:

- Create a cloud run with `aliyun_mns`, `aliyun_oss`, and `aliyun_eci`.
- Confirm MNS receives or records the cloud-run message.
- Confirm ECI creates a short-lived worker container group from ACR.
- Confirm OSS receives manifest/log and completion artifacts.
- Confirm the cloud run reaches an existing final state such as `patch_ready` or
  `failed` with redacted logs.
- Confirm cleanup instructions remove cloud test resources.

## Documentation

Update the README with an Aliyun Phase 10C smoke section after implementation.
The docs must explain:

- Which Aliyun services are required.
- Why users should not manually create long-lived ECI instances.
- Which environment variables are needed.
- How to build and push the worker image to ACR.
- How to run a smoke test.
- How to clean up resources and avoid idle charges.

Update `docs/architecture.md` and `docs/superpowers/status.md` only after the
implementation is verified.

## Non-Goals

Phase 10C MVP does not:

- Add live log streaming with SLS, SSE, or WebSocket.
- Add Kubernetes, ACK, or a long-running worker cluster.
- Add automatic PR creation or automatic merge.
- Add billing, organizations, production RBAC, or subscriptions.
- Add GitLab, Gitee, or non-GitHub repository providers.
- Add model-backed reviewer/debugger agents.
- Store cloud credentials in the database.
- Replace deterministic local and stub providers.
- Require the desktop app to manage Aliyun credentials directly.

## Acceptance Criteria

- The design keeps Phase 10B public contracts intact.
- Aliyun providers are selected by provider name and do not affect default local
  behavior.
- Automated tests use fake clients and make no network calls.
- Real smoke execution is opt-in and controlled by environment variables.
- Sensitive values are not present in API responses, logs, persisted external
  fields, docs, or tests.
- The worker image path through ACR and ECI is documented and reproducible.
- Cleanup and cost-control steps are documented before real smoke testing.
