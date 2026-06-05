# Phase 12C MNS Worker Pull Design

## Summary

Phase 12C upgrades `aliyun_mns` from an enqueue/submission audit record into a
real worker delivery queue. A remote worker can receive a cloud-run message from
Aliyun MNS, extract the run assignment, and then use the existing
callback-token-protected worker API to claim, fetch payload, heartbeat, upload
artifacts, and complete the run.

This phase makes one explicit security tradeoff: MNS becomes a secret-bearing
delivery channel. The message body carries the short-lived worker callback token
needed to claim and operate the assigned run. That token is still stored only as
a hash in the API database, expires, and is invalidated by completion or
cancellation. Possessing an MNS message is therefore equivalent to possessing the
worker's current callback credential, so queue access must be restricted through
RAM policy to the intended worker role.

Phase 12C does not add WebSockets, Server-Sent Events, SLS-managed logs,
dead-letter queue management, billing, organization RBAC, production KMS,
automatic pull requests, automatic merges, or model-backed reviewer/debugger
agents.

## Current State

After Phase 12B, Aliyun cloud runs have these pieces:

- `aliyun_mns` sends a safe JSON message when the API enqueues a cloud run.
- `CloudRun.queue_message_id` stores the provider message id when available.
- `CloudRun.queue_receipt` exists internally but is not exposed in
  `CloudRunRead`.
- `aliyun_eci` launches a worker container with `AI_SCDC_CLOUD_RUN_ID`,
  `AI_SCDC_WORKER_ID`, `AI_SCDC_CALLBACK_TOKEN`,
  `AI_SCDC_QUEUE_PROVIDER=aliyun_mns`, and storage/runtime metadata.
- The worker calls `/cloud-run-worker/leases` with `cloud_run_id`,
  `worker_id`, and `callback_token`.
- Heartbeat, payload, artifact upload, and completion already require the
  callback token for protected remote-provider runs.

The missing production queue behavior is direct worker consumption. MNS is
currently useful for submission/audit metadata, but the worker does not receive
or delete MNS messages itself.

## Design Choice

Add direct MNS pull as an optional worker startup mode while preserving assigned
run mode:

```text
AI_SCDC_CLOUD_RUN_ID set
  -> existing assigned-run behavior

AI_SCDC_CLOUD_RUN_ID missing and AI_SCDC_QUEUE_PROVIDER=aliyun_mns
  -> receive one MNS message
  -> parse cloud_run_id, worker_id, callback_token, and provider metadata
  -> claim the API lease with callback token
  -> persist queue receipt in the API during claim
  -> execute through existing worker flow
  -> delete the MNS message only after terminal API success
```

This gives the project a real MNS delivery path without removing the deterministic
local, stub, or assigned-run Aliyun paths. The ECI runtime provider can keep
injecting `AI_SCDC_CLOUD_RUN_ID` for the current per-run container model. Later
operator-managed worker pools can omit that variable and use the MNS-pull entry
path.

## Token-Bearing Queue Message

For token-protected remote runs, the API must generate the worker id and raw
callback token before enqueueing the MNS message. The database stores only:

```text
sha256(f"{cloud_run_id}:{worker_id}:{callback_token}")
```

The MNS message carries the raw callback token because a generic MNS-pull worker
has no other way to authenticate the assigned run.

Expected message shape:

```json
{
  "workspace_id": "dev_workspace",
  "project_id": "project_...",
  "task_id": "task_...",
  "cloud_run_id": "cloud_run_...",
  "worker_id": "aliyun-eci-cloud_run_...",
  "callback_token": "<short-lived one-time token>",
  "callback_token_expires_at": "2026-06-05T12:00:00+00:00",
  "queue_provider": "aliyun_mns",
  "runtime_provider": "aliyun_eci",
  "storage_provider": "aliyun_oss"
}
```

This message body must never be logged, returned through public API schemas, or
included in README smoke output. Tests may use fake token values.

## Aliyun MNS Client Seam

Extend `apps/api/app/ai_company_api/services/aliyun_clients.py` with receive and
delete request types:

```python
@dataclass(frozen=True)
class AliyunMnsReceiveMessageRequest:
    queue_name: str
    wait_seconds: int = 10


@dataclass(frozen=True)
class AliyunMnsDeleteMessageRequest:
    queue_name: str
    receipt_handle: str


@dataclass(frozen=True)
class AliyunMnsReceivedMessage:
    message_id: str | None
    receipt_handle: str
    body: str
```

Extend the `AliyunMnsClient` protocol:

```python
class AliyunMnsClient(Protocol):
    def send_message(self, request: AliyunMnsSendMessageRequest) -> dict[str, Any]:
        ...

    def receive_message(
        self,
        request: AliyunMnsReceiveMessageRequest,
    ) -> AliyunMnsReceivedMessage | None:
        ...

    def delete_message(self, request: AliyunMnsDeleteMessageRequest) -> None:
        ...
```

SDK-backed behavior should use the same `mns.account.Account(...).get_queue()`
construction as `send_message`. Tests use fake clients through
`set_aliyun_client_bundle_for_tests`; automated tests must not call Aliyun.

Provider exceptions are converted to non-secret provider errors. Empty queues
return `None`, not an exception.

## Queue Provider Contract

Keep `CloudQueueProvider.enqueue()` and add optional receive/delete operations
for providers that support direct worker consumption.

Suggested service types:

```python
@dataclass(frozen=True)
class CloudQueueReceivedMessage:
    cloud_run_id: str
    worker_id: str
    callback_token: str
    queue_message_id: str | None
    queue_receipt: str
    runtime_provider: str | None
    storage_provider: str | None


class CloudQueueProvider(Protocol):
    name: str

    def validate_configuration(self) -> None:
        ...

    def enqueue(self, request: CloudQueueEnqueueRequest) -> CloudQueueEnqueueResult:
        ...

    def receive(self, *, wait_seconds: int = 10) -> CloudQueueReceivedMessage | None:
        ...

    def delete(self, *, queue_receipt: str) -> None:
        ...
```

`aliyun_mns` implements receive/delete. `local_db` and `external_stub` may raise
`CloudQueueProviderError("Cloud queue provider <name> does not support receive")`
for receive/delete. Existing local lease scans remain unchanged.

The receive parser accepts only the expected token-bearing message shape.
Messages that are not valid JSON, have missing or non-string required fields, or
declare the wrong `queue_provider` are rejected as malformed and are not deleted
by default. This allows operator inspection and MNS redelivery rather than
silently dropping unknown payloads.

## Start-Run Ordering

`start_cloud_run()` currently enqueues before remote runtime submission. Phase
12C keeps that broad order but moves callback-token generation earlier for
remote-provider runs:

1. Create the `CloudRun` and `LocalTaskRun`.
2. If a remote runtime provider is selected, compute the deterministic worker id
   and generate the callback token before queue enqueue.
3. Store the callback token hash and expiry on `CloudRun`.
4. Enqueue to `aliyun_mns` with the token-bearing message.
5. Submit runtime provider with the same worker id and callback token.

If MNS enqueue fails, the run follows the existing queue enqueue failure path.
If runtime submission fails after enqueue, the run follows the existing runtime
submission failure path and the MNS message remains available for redelivery or
operator cleanup until Phase 12C terminal cleanup can delete stored receipts.

Assigned-run compatibility remains: `aliyun_eci` may still inject
`AI_SCDC_CLOUD_RUN_ID` and `AI_SCDC_CALLBACK_TOKEN` into the per-run ECI
environment. MNS-pull workers are enabled by omitting `AI_SCDC_CLOUD_RUN_ID`.

## Worker API Claim Flow

`POST /cloud-run-worker/leases` remains the only API endpoint that grants a
lease. Extend `CloudRunLeaseCreate` with optional queue delivery metadata:

```python
class CloudRunLeaseCreate(BaseModel):
    worker_id: str
    worker_kind: str = "remote_stub"
    queue_provider: str = "local_db"
    cloud_run_id: str | None = None
    callback_token: str | None = None
    lease_seconds: int = Field(default=60, ge=1, le=3600)
    queue_message_id: str | None = Field(default=None, min_length=1)
    queue_receipt: str | None = Field(default=None, min_length=1)
```

When a protected worker claims a specific `cloud_run_id` from MNS:

1. The API validates `queue_provider`.
2. The API finds the queued run by `cloud_run_id`, queue provider, status,
   cancellation state, and attempt limit.
3. The API verifies the callback token against the run and worker id.
4. The API uses the existing atomic claim update.
5. After a successful claim, the API stores `queue_message_id` and
   `queue_receipt` internally on `CloudRun`.
6. `CloudRunRead` and `CloudRunLeaseRead` continue to omit `queue_receipt`.
7. The lease response continues to include non-sensitive `queue_message_id`.

If claim fails because the run is already claimed, cancelled, terminal, expired,
or token validation fails, the API must not store the receipt. The worker must
not delete the MNS message in those cases.

Duplicate MNS deliveries are expected. Only one worker can win the API claim.
The loser receives the existing 204/401/403/409 behavior and leaves the MNS
message for redelivery or operator cleanup.

## Worker Pull Flow

Add a small MNS queue-consumer seam to the remote worker code. It should be
usable with fake tests and avoid importing Aliyun SDK modules unless the MNS
provider is selected.

Worker startup behavior:

1. `config_from_env()` keeps assigned-run mode when `AI_SCDC_CLOUD_RUN_ID` is
   set.
2. If `AI_SCDC_CLOUD_RUN_ID` is missing and `AI_SCDC_QUEUE_PROVIDER=aliyun_mns`,
   the worker calls the queue provider receive method.
3. If no message is available, the worker exits successfully with a no-work
   result.
4. If a message is available, the worker builds `RemoteWorkerConfig` from the
   received `cloud_run_id`, `worker_id`, `callback_token`, and provider
   metadata.
5. `HttpRemoteWorkerClient.claim()` sends `queue_message_id` and
   `queue_receipt` in addition to the existing fields.
6. The worker runs the existing `run_remote_worker_once()` execution path.
7. After API completion returns a terminal cloud-run result, the worker deletes
   the MNS message using the stored receipt.

Do not delete the message after:

- receive parse failure;
- claim returns no lease;
- missing, wrong, expired, or reused callback token;
- payload fetch failure;
- heartbeat failure before terminal completion;
- artifact upload failure before terminal completion;
- completion callback failure.

These cases rely on MNS visibility timeout and the existing API lease expiry or
attempt exhaustion logic.

## Worker MNS Credentials

Direct MNS receive/delete means the worker needs queue access. Phase 12C allows
the worker to receive a narrow Aliyun credential or RAM role with only the
minimum MNS permissions for the selected queue:

- receive message;
- delete message;
- optional queue metadata read if the SDK requires it.

The worker must not receive API-side OSS write credentials, ECI management
permissions, or broad account credentials as part of this phase. Existing
`AI_SCDC_ALIYUN_ACCESS_KEY_ID` and `AI_SCDC_ALIYUN_ACCESS_KEY_SECRET` names can
be used in a worker environment only when the underlying RAM principal is
MNS-only. Documentation must call this out explicitly.

## Delete/Ack Semantics

MNS delete is an acknowledgement that the delivery no longer needs redelivery.
In Phase 12C, worker-side delete should happen only after the API has reached a
terminal state for the same lease:

- successful `patch_ready` completion;
- worker-reported terminal `failed` completion;
- API-accepted cancellation terminal state after worker completion.

API-side cleanup should also be able to delete a stored receipt when a run
becomes terminal without a worker-side delete path:

- queued cancellation invalidates the callback token and can delete a stored
  receipt if one exists;
- lease attempts exhausted can delete a stored receipt if one exists;
- explicit terminal failure paths can delete a stored receipt if one exists.

Provider delete failures should not roll back terminal API state. Instead, they
should leave a redacted control-plane log entry and keep the receipt so a
subsequent cleanup attempt or operator can see that acknowledgement did not
finish.

## Security And Redaction

The MNS message body may contain only the one worker callback token and
non-sensitive routing metadata. It must never contain:

- callback token hashes;
- GitHub clone tokens;
- Aliyun AccessKey values;
- OSS signed URLs;
- queue receipts;
- raw provider exception text.

`queue_receipt` remains internal-only:

- not in `CloudRunRead`;
- not in `CloudRunLeaseRead`;
- not in desktop API client output;
- not in cloud-run logs;
- not in README smoke examples.

Logs may include `queue_message_id`. The preferred Phase 12C default is to omit
receipt values entirely from logs.

The callback token remains the execution authorization boundary. Possessing an
MNS receipt without the message body callback token is not enough to claim,
fetch payload, upload artifacts, heartbeat, or complete.

## Error Handling

Expected outcomes:

- Missing Aliyun MNS config returns a safe provider configuration error.
- Empty MNS queue returns no work.
- MNS receive failure exits as a controlled worker/provider failure without
  exposing SDK causes.
- Malformed message is not deleted and does not call the API claim endpoint.
- Claim conflict does not delete the message.
- Wrong callback token does not delete the message.
- Completion success followed by MNS delete failure keeps the API terminal state
  and records a redacted warning.
- Repeated duplicate deliveries do not create duplicate leases because the API
  claim remains atomic.

## Testing

Add tests before implementation:

- SDK `receive_message` builds an MNS receive request and maps `message_id`,
  `receipt_handle`, and body.
- SDK `delete_message` builds an MNS delete request with queue name and receipt.
- `AliyunMnsQueueProvider.enqueue()` includes callback token only when provided
  and never logs the raw body.
- `AliyunMnsQueueProvider.receive()` parses a token-bearing message and returns
  `CloudQueueReceivedMessage`.
- malformed MNS messages are rejected without delete.
- worker pull mode receives an MNS message and claims the correct `cloud_run_id`.
- worker pull mode sends `queue_message_id` and `queue_receipt` to the lease
  endpoint but read responses omit the receipt.
- successful completion deletes the MNS message.
- wrong callback token does not delete the MNS message.
- duplicate delivery cannot create a second active lease.
- lease attempts exhausted keeps API state consistent and does not expose
  receipts.
- existing assigned-run worker mode still works when `AI_SCDC_CLOUD_RUN_ID` is
  set.

Run at minimum:

```text
pytest apps/api/tests/test_aliyun_clients.py -v
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun_mns or queue_provider or lease or callback_token" -v
pytest apps/api/tests/test_remote_worker.py -k "mns or config_from_env or callback_token" -v
pytest apps/api/tests -v
pnpm --filter @ai-scdc/desktop test -- client.test.ts
pnpm typecheck
git diff --check
```

## Documentation Updates

After implementation and verification, update:

- `docs/architecture.md` to add Phase 12C under the Phase 12 boundary and move
  direct MNS receive/delete from Future to Completed.
- `docs/superpowers/status.md` with current phase, verification commands,
  remaining limits, and recommended next phase.
- `README.md` Aliyun smoke section to distinguish assigned-run ECI workers from
  MNS-pull workers and to document cleanup expectations.

Documentation must keep explicit limits:

- MNS pull is worker delivery, not user auth.
- MNS is a secret-bearing delivery channel in Phase 12C.
- The API worker endpoints remain callback-token protected.
- MNS receipt handles are never shown to clients.
- Dead-letter queue setup and operational replay tooling are future work.

## Non-Goals

Phase 12C does not:

- add SLS, WebSocket, or SSE live logs;
- add MNS dead-letter queue provisioning or replay UI;
- create a long-running worker fleet manager;
- change GitHub clone credential handling;
- change object-storage artifact integrity rules;
- loosen callback-token requirements;
- pass broad Aliyun credentials into worker containers;
- add billing, organization RBAC, subscriptions, or production KMS;
- add automatic PR creation or automatic merge;
- add model-backed reviewer/debugger agents.

## Success Criteria

Phase 12C is complete when:

- `aliyun_mns` has tested receive and delete client seams.
- The API can enqueue token-bearing MNS messages for protected remote runs
  without logging or exposing the raw token.
- A worker can pull an MNS message and claim the matching cloud run through the
  existing callback-token-protected API.
- MNS receipts are stored only internally and are never exposed in public read
  schemas or logs.
- Successful terminal completion deletes the MNS message.
- Claim failures, wrong tokens, and malformed messages do not delete MNS
  messages.
- Duplicate delivery is bounded by the existing atomic API lease claim.
- Existing assigned-run remote worker mode remains compatible.
- Backend tests, desktop client tests, typecheck, and `git diff --check` pass.
