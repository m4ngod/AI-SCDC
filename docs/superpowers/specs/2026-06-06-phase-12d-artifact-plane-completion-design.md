# Phase 12D Artifact Plane Completion Design

## Summary

Phase 12D completes the remaining original Phase 12 artifact/log plane goals
after Phase 12A, 12B, 12C, and 13A. The system already has cursor-based log
windows, provider-native log sync, worker artifact uploads, object-storage refs,
and Aliyun operational hardening. Phase 12D adds a first-class artifact manifest
read surface, artifact listing and text read APIs, provider-neutral download
descriptors, retention policy metadata, a cleanup seam, and a minimal desktop
artifact browser.

This phase keeps the product boundary narrow. It does not add user auth,
organization RBAC, billing, SLS or CloudWatch, real OSS object deletion, or a
full desktop productization pass.

## Goals

1. Define an `ArtifactManifest` schema that can describe diff, log,
   command-result, test-result, and manifest refs for a cloud run.
2. Let API clients locate cloud-run artifacts through one manifest endpoint
   instead of scraping individual cloud-run fields.
3. Let clients list cloud-run artifact refs and read safe text artifacts through
   scope-checked API endpoints.
4. Add a provider-neutral download descriptor abstraction that exposes safe
   metadata and a local API download URL, not raw signed provider URLs.
5. Add retention policy metadata and a cleanup job seam. `local_inline` objects
   can be deleted by the API; `aliyun_oss` objects remain governed by OSS
   lifecycle rules and produce cleanup intent records only.
6. Add a minimal desktop artifact browser so the UI can find diff, log, command
   result, and test result artifacts through the manifest.
7. Preserve existing Phase 12A/12B log window behavior and Phase 13A no-public-
   destructive-Aliyun-operation boundary.

## Non-Goals

Phase 12D does not:

- add production user auth, organization RBAC, or workspace isolation beyond
  existing `workspace_id` checks on cloud-run and stored-object rows;
- delete Aliyun OSS objects from API code;
- generate or return signed OSS URLs;
- add SLS, CloudWatch, Cloud Logging, WebSockets, or Server-Sent Events;
- add billing meters, subscriptions, quotas, or rate limits;
- replace the existing patch-artifact review flow;
- perform a broad `cloud_runner.py` split;
- build the full Phase 14 desktop productization scope.

## Current State

The API already has:

- `CloudRun.artifact_manifest_uri`, `artifact_manifest_sha256`,
  `artifact_manifest_size_bytes`, and `artifact_manifest_content_type`;
- equivalent `log_stream_*` metadata;
- `CloudRunStoredObject` rows for `local_inline` objects;
- `ObjectStorageRef` with `kind`, `uri`, `sha256`, `size_bytes`, and
  `content_type`;
- worker upload support through `POST /cloud-run-worker/leases/{lease_id}/artifacts`;
- artifact-ref resolution during worker completion for diff refs;
- bounded log polling through `GET /cloud-runs/{cloud_run_id}/logs/window`;
- optional provider-native log sync through `sync_stream=true`;
- redaction for external URIs, stream lines, callback tokens, receipts, and
  provider errors.

The missing original Phase 12 pieces are:

- a durable manifest schema exposed through a stable API;
- a cloud-run artifact list API;
- text artifact read and download descriptor APIs;
- retention and cleanup semantics for stored objects;
- desktop artifact discovery through manifest data.

## Artifact Manifest Schema

The API will expose a manifest read model with these fields:

```json
{
  "version": 1,
  "cloud_run_id": "cloud_run_abc",
  "workspace_id": "dev_workspace",
  "generated_at": "2026-06-06T00:00:00Z",
  "retention": {
    "policy": "development_default",
    "expires_at": "2026-06-13T00:00:00Z",
    "cleanup_supported": true
  },
  "artifacts": [
    {
      "id": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "kind": "diff",
      "label": "Unified diff",
      "provider": "local_inline",
      "uri": "local-inline://cloud-run-objects/cloud_object_example",
      "redacted_uri": "local-inline://cloud-run-objects/cloud_object_example",
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "size_bytes": 1024,
      "content_type": "text/x-diff",
      "created_at": "2026-06-06T00:00:00Z",
      "download_url": "/cloud-runs/cloud_run_abc/artifacts/sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/content"
    }
  ]
}
```

Artifact ids are deterministic API ids derived from `sha256`, `kind`, and
cloud-run id. They are not raw object ids for external providers.

## API Surface

Add these endpoints:

```text
GET  /cloud-runs/{cloud_run_id}/artifacts/manifest
GET  /cloud-runs/{cloud_run_id}/artifacts
GET  /cloud-runs/{cloud_run_id}/artifacts/{artifact_id}
GET  /cloud-runs/{cloud_run_id}/artifacts/{artifact_id}/content
POST /cloud-runs/{cloud_run_id}/artifacts/{artifact_id}/download
POST /cloud-runs/artifacts/cleanup-expired
```

Endpoint behavior:

- `manifest` returns the generated manifest, including redacted provider refs.
- `artifacts` returns the manifest's artifact list.
- `artifact_id` returns one artifact descriptor.
- `content` returns text content for supported text artifacts after integrity
  and workspace/run checks.
- `download` returns a provider-neutral descriptor with local API content URL,
  size, content type, sha256, and expiration metadata. It never returns raw
  signed provider URLs.
- `cleanup-expired` scans expired `local_inline` stored objects and deletes
  those rows. For `aliyun_oss`, it returns cleanup intent records and instructs
  operators to use OSS lifecycle rules.

## Service Design

Create `ai_company_api.services.artifact_plane` with focused responsibilities:

- build manifests from cloud-run metadata and stored-object rows;
- convert `ObjectStorageRef` values into safe artifact descriptors;
- resolve artifact ids back to scoped refs;
- read text artifacts through the existing object-storage provider contract;
- build download descriptors without exposing provider signatures;
- execute cleanup for local-inline objects and record skipped external-provider
  cleanup intents.

The service must not import desktop code or mutate cloud-run state during
read-only manifest/list/read operations.

## Storage And Retention

`CloudRunStoredObject` will gain nullable retention fields:

- `expires_at`;
- `retention_policy`.

Local-inline cleanup deletes expired `CloudRunStoredObject` rows. If a
local-inline object is expired but has not yet been cleaned, text reads return
`410 Cloud run artifact expired`. After cleanup deletes the row, later reads
return `404 Cloud run artifact not found`.

Aliyun OSS cleanup remains lifecycle-only. The cleanup job should report an
item such as:

```json
{
  "provider": "aliyun_oss",
  "action": "lifecycle_only",
  "redacted_uri": "oss://bucket/path/to/object",
  "reason": "external_provider_cleanup_not_supported_by_api"
}
```

## Redaction And Access Safety

The artifact plane must never expose:

- callback tokens;
- callback token hashes;
- MNS receipts;
- Aliyun access key values;
- signed query parameters;
- raw provider exception strings;
- clone credentials;
- sandbox environment secret values.

All provider URIs are redacted by dropping query strings and fragments. Artifact
content reads still validate kind, sha256, size, and content type before
returning text. `CloudRunStoredObject` reads must verify that the object belongs
to the requested `cloud_run_id` and `workspace_id`.

## Desktop Design

Add a minimal artifact browser to the existing cloud-run task detail area:

- show artifact count and manifest retention policy;
- group artifacts by kind;
- show label, redacted URI, size, and content type;
- allow opening text artifacts in an inline preview;
- keep existing cloud-run log window behavior for logs;
- do not add routing, workspace switching, login, or a full artifact page.

The desktop fake client should expose deterministic artifact data so tests stay
stable when `VITE_API_BASE_URL` is unset.

## Error Handling

API errors should be explicit and redacted:

- missing cloud run: `404 Cloud run not found`;
- missing artifact: `404 Cloud run artifact not found`;
- unsupported artifact kind: `400 Unsupported object storage artifact kind`;
- integrity mismatch: `400 Object storage content sha256 mismatch` or existing
  object-storage error text;
- deleted or expired local-inline object: `410 Cloud run artifact expired`;
- unsupported external cleanup: reported in cleanup result, not as a failed job.

## Testing Strategy

API tests should cover:

1. manifest generation includes diff/log/command-result/test-result refs;
2. artifact ids are deterministic and do not expose raw object ids for external
   providers;
3. artifact list and detail endpoints return redacted URIs;
4. text content endpoint validates workspace, run, kind, sha256, and size;
5. content endpoint does not return large unsafe provider refs without metadata;
6. download descriptor returns a local API URL and never returns signed provider
   query strings;
7. local-inline cleanup expires and removes expired artifacts;
8. Aliyun OSS cleanup records lifecycle-only intent and never calls OSS delete;
9. log window behavior remains unchanged;
10. desktop renders artifacts from the manifest and opens text previews.

Verification should include:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "artifact_manifest or artifact_plane or cleanup_expired"
pytest apps/api/tests/test_cloud_object_storage.py -q
pytest apps/api/tests -q
pnpm --filter @ai-scdc/desktop test -- App.test.tsx client.test.ts
pnpm typecheck
git diff --check
```

## Documentation Updates

Update:

- `README.md` with a Phase 12D artifact plane note and smoke commands;
- `docs/architecture.md` to expand the Phase 12 boundary and completed roadmap;
- `docs/superpowers/status.md` with Phase 12D completion once implemented;
- `STATUS.md` with final verification results.

## Acceptance Criteria

Phase 12D is complete when:

- API clients can fetch a cloud-run artifact manifest and locate diff, log,
  command-result, and test-result entries;
- artifact refs include safe metadata and redacted display URIs;
- text artifact content can be read only after scope and integrity validation;
- download descriptors are provider-neutral and expose no raw signed provider
  URL;
- retention metadata appears in the manifest;
- cleanup-expired handles local-inline objects and reports lifecycle-only
  external-provider cleanup intent;
- desktop can show manifest artifacts and open text previews;
- existing log window and provider sync tests still pass;
- final status docs record real verification output.
