# Aliyun Operational Runbook

## Scope

This runbook covers the current Aliyun execution path:

- `aliyun_mns` queue-only pull runs.
- `aliyun_oss` manifest, log, diff, command-result, and test-result objects.
- `aliyun_eci` assigned-run containers.

It does not authorize direct database edits, public destructive HTTP calls,
worker-side receipt deletion by default, or OSS object deletion from code.

## Sensitive Values

Never paste these values into tickets, chat, logs, screenshots, or commit text:

- `AI_SCDC_CALLBACK_TOKEN`
- `callback_token`
- `callback_token_hash`
- `queue_receipt`
- Aliyun access key id or access key secret values
- Signed OSS query strings
- GitHub token values
- Raw provider exception text

Safe fields for troubleshooting:

- `cloud_run.id`
- `cloud_run.status`
- `queue_provider`
- `storage_provider`
- `runtime_provider`
- `queue_message_id`
- `runtime_job_id`
- `external_status`
- `failure_reason`
- redacted `external_error`
- control-plane log `event`, `level`, and redacted `payload`

## KMS Boundary

The codebase currently uses `DevSecretVault` for development secret sealing.
Commercial production must provide a KMS-backed implementation of the existing
`SecretVault` protocol before beta traffic. Operators must not compensate by
copying plaintext access keys, callback tokens, queue receipts, signed URLs, or
GitHub tokens into operational records.

## Cleanup Decision Rules

1. Retry MNS receipt deletion only for terminal Aliyun MNS runs that still have
   an internal `queue_receipt`.
2. Treat MNS delete failure as recoverable. The cloud run can remain
   `patch_ready`, `failed`, or `cancelled` while `external_status` records
   `mns_message_delete_failed`.
3. Delete ECI container groups only for terminal Aliyun ECI runs by persisted
   `runtime_job_id`.
4. Do not delete ECI containers immediately after terminal completion when
   `DescribeContainerLog` may still be needed for provider log sync.
5. Use Aliyun OSS lifecycle rules for development-prefix retention instead of
   adding API-side object deletion.

## Case: MNS Enqueue Fails During Queue-Only Pull Creation

Operator symptom: `POST /tasks/{task_id}/cloud-runs` returns 502 with
`Cloud queue provider aliyun_mns failed to enqueue message`.

Safe fields to inspect: `cloud_run.status`, `failure_reason`,
`last_queue_error`, `external_status`, redacted `external_error`, and
`queue_enqueue_failed` log entries.

Fields that must never be pasted: access keys, callback tokens, MNS message
body, raw SDK error text, and local shell environment dumps.

Expected API state: one failed cloud run, failed local run, `failure_reason` set
to `queue_enqueue_failed`, and no exposed receipt.

Recovery action: verify `AI_SCDC_ALIYUN_MNS_ENDPOINT`,
`AI_SCDC_ALIYUN_MNS_QUEUE_NAME`, region, and RAM permission for
`mns:SendMessage`; then create a new cloud run.

Escalation: if configuration and RAM are correct, capture the redacted
`queue_enqueue_failed` log and Aliyun request id from cloud provider tooling.

## Case: MNS Receive Returns Empty Queue

Operator symptom: pull worker receives no work and keeps polling.

Safe fields to inspect: worker poll interval, MNS queue depth, `cloud_run.status`
for queued runs, and `queue_message_id`.

Fields that must never be pasted: callback token from any MNS body and raw MNS
message body.

Expected API state: queued cloud runs remain queued and unclaimed.

Recovery action: confirm the worker is using `AI_SCDC_QUEUE_PROVIDER=aliyun_mns`
and the same queue name as the API process.

Escalation: compare redacted worker logs and API enqueue logs by
`cloud_run.id`.

## Case: MNS Receive Returns Malformed Token-Bearing Payload

Operator symptom: worker logs an invalid MNS message and does not claim a run.

Safe fields to inspect: MNS message id, API `queue_message_id`, and redacted
worker parse error reason.

Fields that must never be pasted: raw message body, callback token,
`queue_receipt`, and GitHub clone credential values.

Expected API state: the target cloud run is not claimed through the malformed
delivery.

Recovery action: leave the malformed message unclaimed until the queue
visibility timeout expires, then inspect the producer version and MNS queue
source.

Escalation: if malformed messages continue, stop the worker pool and preserve
only redacted message metadata for debugging.

## Case: MNS Terminal Delete Fails And Leaves Retained Receipt

Operator symptom: terminal cloud run has `external_status` set to
`mns_message_delete_failed`.

Safe fields to inspect: `cloud_run.id`, `status`, `queue_provider`,
`queue_message_id`, `external_status`, redacted `external_error`, and
`mns_message_delete_retry_failed` log entries.

Fields that must never be pasted: `queue_receipt`, raw delete exception,
callback token, and MNS message body.

Expected API state: `status` remains terminal and internal `queue_receipt`
remains present until recovery succeeds.

Recovery action: call the service helper
`retry_retained_mns_queue_receipt_delete(session, cloud_run_id=...)` from
authenticated maintenance tooling or an operator script running inside the API
trust boundary.

Escalation: if retry fails repeatedly, verify MNS delete permission and queue
visibility state, then keep the run terminal and document only redacted status.

## Case: OSS Manifest Or Log Seeding Fails After ECI Creation

Operator symptom: ECI submission returns a controlled runtime submission
failure after a container group was created.

Safe fields to inspect: `cloud_run.id`, `failure_reason`,
`runtime_submission_failed`, persisted `runtime_job_id` when present, and
fake-client or Aliyun-side delete outcome.

Fields that must never be pasted: OSS signed query strings, access keys, raw OSS
exception text, and callback token environment values.

Expected API state: cloud run failed with `runtime_submission_failed`.
Operator cleanup is allowed only after the run is terminal and has a persisted
`runtime_job_id`.

Recovery action: fix OSS endpoint, bucket, prefix, and RAM permissions for
`oss:PutObject`, then create a new cloud run.

Escalation: if a terminal Aliyun ECI run retains a persisted `runtime_job_id`,
retry cleanup by that persisted id and record only redacted details.

## Case: OSS Read Fails For URI, Bucket, Prefix, Or Metadata Mismatch

Operator symptom: log window or artifact read omits remote content or returns a
safe read failure.

Safe fields to inspect: redacted object URI path, object kind, stored SHA-256,
stored size, content type, and cloud-run log event.

Fields that must never be pasted: signed query strings, raw OSS errors, access
keys, and artifact content that may contain repository secrets.

Expected API state: cloud-run metadata remains unchanged; public reads avoid
returning unverified content.

Recovery action: verify bucket, configured prefix `ai-scdc/dev`, object
existence, size, and SHA-256 through trusted Aliyun tooling.

Escalation: if metadata differs, preserve the database row and object metadata
for investigation without pasting object bodies.

## Case: ECI Submission Fails Before Container Group Id Is Available

Operator symptom: cloud-run creation fails with controlled runtime submission
error and no persisted `runtime_job_id`.

Safe fields to inspect: `cloud_run.id`, `failure_reason`,
`external_status`, redacted `external_error`, and
`remote_runtime_submission_failed` log.

Fields that must never be pasted: access keys, raw ECI SDK exception, and worker
environment variables.

Expected API state: cloud run failed, local run failed, and no ECI cleanup by
persisted id is possible.

Recovery action: verify region, image, vSwitch, security group, and ECI create
permission; then create a new cloud run.

Escalation: use Aliyun console request diagnostics with redacted API logs.

## Case: ECI Submission Succeeds But Later OSS Seeding Fails

Operator symptom: ECI create request exists, but API returns controlled runtime
submission failure.

Safe fields to inspect: failed cloud-run id, persisted `runtime_job_id` when
present, redacted provider status, and whether cleanup was attempted.

Fields that must never be pasted: raw OSS exception, access keys, callback
token, and full worker environment.

Expected API state: failed cloud run. Operator cleanup is allowed only if the
run is terminal and has a persisted `runtime_job_id`.

Recovery action: check whether the terminal cloud run has a persisted
`runtime_job_id`. Cleanup must use only the persisted `runtime_job_id` for a
terminal Aliyun ECI run. Operators must not delete from transient ids, raw
provider output, or unpersisted submission responses.

Escalation: if deletion by persisted `runtime_job_id` is denied, validate RAM
`eci:DeleteContainerGroup` scope and capture only redacted status.

## Case: ECI Log Sync Returns Empty Or Non-Text Content

Operator symptom: `GET /cloud-runs/{id}/logs/window?include_stream=true&sync_stream=true`
returns control-plane logs but no fresh provider log lines.

Safe fields to inspect: `runtime_job_id`, `runtime_provider`,
`log_stream_uri` path without query, log stream size, and redacted sync reason.

Fields that must never be pasted: raw provider log content if it may contain
secrets, access keys, and callback token environment values.

Expected API state: cloud-run terminal status remains unchanged; log sync
degrades to the previous safe window.

Recovery action: retry after ECI log propagation, then inspect Aliyun
`DescribeContainerLog` availability for the persisted `runtime_job_id`.

Escalation: if provider log content is malformed, preserve only content type,
size, and redacted reason.

## Case: ECI Delete Fails During Best-Effort Cleanup

Operator symptom: cleanup helper returns `runtime_cleanup_failed`.

Safe fields to inspect: `cloud_run.id`, terminal `status`, `runtime_job_id`,
`external_status`, redacted `external_error`, and
`runtime_cleanup_failed` control-plane log.

Fields that must never be pasted: access keys, raw ECI delete exception, worker
environment, callback token, and signed OSS links.

Expected API state: terminal cloud-run status is unchanged and `runtime_job_id`
is retained for another cleanup attempt.

Recovery action: verify RAM `eci:DeleteContainerGroup`, confirm the terminal
Aliyun ECI run still has a persisted `runtime_job_id`, and retry cleanup by
persisted `runtime_job_id`.

Escalation: if delete is denied or the group is stuck, use Aliyun console
support flow with redacted AI-SCDC metadata.
