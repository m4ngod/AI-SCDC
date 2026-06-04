# Phase 10C-H Aliyun Provider Hardening Tests Design

## Purpose

Phase 10C-H turns the Aliyun provider MVP from a smokeable provider path into a
more repeatably verifiable provider boundary. This phase happens before worker
callback authentication and focuses on fake-client tests plus the smallest
runtime cleanup behavior needed to make Aliyun failure states diagnosable and
recoverable.

The user-requested sequence is:

1. Complete Phase 10C-H hardening first.
2. Then implement Phase 10D worker callback token security.

## Current Baseline

The project is through Phase 10C. Existing focused tests cover Aliyun provider
name recognition, MNS enqueue, MNS enqueue failure redaction, ECI submission
failure redaction, ECI client token construction, OSS object writes and reads,
OSS hash mismatch, OSS query/fragment rejection, worker artifact uploads, and
artifact-ref completion.

The verified baseline on 2026-06-04:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun or worker_uploads or artifact_ref or lease" -q
pytest apps/api/tests/test_aliyun_config.py apps/api/tests/test_aliyun_clients.py apps/api/tests/test_cloud_object_storage.py apps/api/tests/test_remote_worker.py -q
```

Results: 26 cloud-run focused tests passed and 16 provider/object-storage/worker
focused tests passed.

## Scope

Phase 10C-H will add tests and minimal implementation for these remaining
hardening gaps:

- ECI create succeeds but OSS manifest or log seeding fails.
- The API attempts best-effort ECI cleanup after that partial submission.
- The persisted run is marked failed with redacted external error state.
- OSS refs reject wrong bucket, wrong prefix, wrong kind segment, wrong size, and
  signed query or fragment variants.
- Aliyun API responses and persisted read schemas continue to hide queue
  receipts, AccessKey-like values, signed query tokens, and raw provider errors.
- ECI idempotency token behavior remains stable.

## Non-Goals

This phase does not add worker callback tokens, real Aliyun network calls, real
MNS worker consumption, billing, production KMS, live log streaming, real remote
repo execution, or provider package extraction.

## Design

### ECI Cleanup After OSS Seed Failure

`AliyunEciRuntimeProvider.submit()` currently creates the ECI container group and
then writes OSS manifest/log seed objects when `storage_provider == "aliyun_oss"`.
If the OSS write fails after ECI creation, the provider should call a new
fake-client-friendly ECI delete method before returning the existing safe
`RemoteRuntimeSubmissionError`.

The cleanup is best-effort. If cleanup itself fails, the API should still return
the same safe failure detail, persist the failed run, and avoid exposing the
cleanup provider error. Phase 10C-H records no new cleanup event; it verifies
with fake clients that cleanup is attempted when a container group ID is known.

### Aliyun Client Contract

Extend `AliyunEciClient` with:

```python
def delete_container_group(self, *, region_id: str, container_group_id: str) -> None:
    return None
```

The SDK-backed client should call the Aliyun ECI delete-container-group API. Fake
clients record the deleted ID. Tests use only fake clients.

### OSS Ref Validation

`AliyunOssObjectStorageProvider.read_text()` should continue validating scheme,
bucket, prefix, SHA-256, and size. It should also validate that the object key
contains the requested artifact kind as a stable path segment:

```text
{prefix}/workspaces/{workspace_id}/cloud-runs/{cloud_run_id}/{kind}/{sha256}.txt
{prefix}/workspaces/{workspace_id}/cloud-runs/{cloud_run_id}/{kind}/{sha256}.json
```

This makes a ref with a valid hash but mismatched `kind` fail before completion.

### Redaction

Tests should assert public API JSON and persisted `external_error` values do not
contain:

- `secret`
- `AccessKey`
- `Signature=`
- `token=`
- query strings from provider URIs
- `queue_receipt`

## Acceptance Criteria

- 10C-H focused tests fail before implementation and pass after implementation.
- Existing Phase 10C tests still pass.
- No real Aliyun SDK network calls are introduced in tests.
- API failure responses use existing safe provider messages.
- `git diff --check` passes.
