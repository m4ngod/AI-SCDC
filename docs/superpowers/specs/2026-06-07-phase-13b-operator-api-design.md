# Phase 13B Operator API Facade Design

## Purpose

Phase 13B still needs broader/full organization-scoped operator controls and
operator console coverage before commercial beta. This slice exposes two
existing Aliyun maintenance helpers through narrow HTTP APIs while preserving
the current provider behavior, redaction boundaries, and workspace isolation
rules.

## Scope

Add two explicit cloud-run operator endpoints:

- `POST /cloud-runs/{cloud_run_id}/operator/retry-mns-receipt-delete`
- `POST /cloud-runs/{cloud_run_id}/operator/cleanup-aliyun-eci-runtime`

The routes wrap the existing `retry_retained_mns_queue_receipt_delete()` and
`cleanup_aliyun_eci_terminal_runtime_job()` service helpers. They do not add a
generic operator action framework, public OSS object deletion, payment changes,
new cloud providers, or a desktop operator console.

## Access Control

Only workspace `owner` and `admin` roles can call these endpoints. `developer`,
`reviewer`, `billing_manager`, and `viewer` receive `403 Insufficient workspace
role`. Cross-workspace cloud runs continue to be hidden as `404 Cloud run not
found` through the existing cloud-run lookup and workspace check.

Worker callback endpoints remain separate run-scoped token APIs and are not
converted into user-session endpoints.

## API Contract

Both endpoints return a shared response shape:

```json
{
  "status": "succeeded",
  "reason": "mns_message_deleted",
  "cloud_run": {
    "id": "cloud_run_example",
    "status": "patch_ready",
    "external_status": "mns_message_deleted"
  }
}
```

`status` is one of `skipped`, `succeeded`, or `failed`. `reason` is the stable
helper reason string already used in tests and operations docs. `cloud_run` is
a narrow operator snapshot with the run id, workspace/project/task ids, status,
provider names, sanitized external status/error fields, and timestamps. It does
not expose queue receipts, callback tokens, raw provider errors, Aliyun access
keys, raw provider URLs, or full runtime job ids.

## Error Handling

RBAC failures return `403`. Missing or cross-workspace runs return `404`.
Provider helper outcomes are represented in the response body rather than
raising for expected maintenance results:

- inapplicable run or already-clean state: `status="skipped"`
- successful provider maintenance: `status="succeeded"`
- provider cleanup/delete failure: `status="failed"`

Unexpected authentication mode errors continue to use the existing auth
dependency behavior.

## Testing

Tests are written before implementation and cover:

- non-operator roles cannot call either endpoint
- owner/admin can call the MNS receipt retry endpoint
- owner/admin can call the ECI runtime cleanup endpoint
- cross-workspace operator calls return 404
- API responses and logs do not leak MNS receipt handles, callback tokens,
  Aliyun secrets, or full runtime job ids

The implementation reuses existing helper tests for provider-specific behavior
and adds HTTP-level regression coverage for the new commercial trust boundary.

## Documentation

Update `docs/architecture.md`, `docs/superpowers/status.md`, and `STATUS.md` to
record that Phase 13B now includes a narrow authenticated operator API facade.
The docs must still state that full operator consoles, public destructive OSS
cleanup, real KMS integration, full session issuance, and complete role
permission matrices remain future work.

Documentation update completed for the Phase 13B operator API slice. Phase 13B
now records a narrow authenticated owner/admin cloud-run operator facade for
MNS receipt recovery and ECI runtime cleanup, while still treating full
sessions, production IdP integration, real KMS-backed `SecretVault`
integration, the full operator console, public destructive OSS cleanup, and the
complete role permission matrix as remaining work.
