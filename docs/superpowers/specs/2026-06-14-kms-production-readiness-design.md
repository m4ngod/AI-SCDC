# Phase 13B KMS Production Readiness Design

## Purpose

Phase 13B now has a real `aliyun_kms` SecretVault adapter, but operators still
do not have a safe, repeatable way to prove that a target deployment has all
KMS configuration in place and can complete a live KMS round trip before beta
traffic.

This slice adds a local operator-run readiness and smoke path for the existing
KMS SecretVault boundary. It turns "cloud KMS smoke validation" from a manual
runbook note into a test-backed command and service module while keeping real
cloud calls out of automated tests.

This is intentionally not a public operator API or full credential-management
system. It does not require the user to share real Aliyun credentials in chat,
docs, commits, or test fixtures.

## Scope

Build a KMS production-readiness path:

- add a focused `ai_company_api.services.kms_readiness` module
- add a local command entry point at `ai_company_api.tools.kms_readiness`
- support a preflight mode that validates configuration without making cloud
  calls
- support an explicit live-smoke mode that performs one temporary
  `seal -> open -> fingerprint -> delete` round trip through the configured
  `SecretVault`
- return structured, redacted JSON output suitable for operator records
- add fake-client tests for preflight, live success, live failures, and CLI exit
  codes
- update README, architecture, status, and Aliyun operations docs with safe
  operator instructions

Out of scope:

- public HTTP operator routes
- desktop UI for KMS readiness
- production IdP, full login/session issuance, or broad auth work
- provisioning cloud credentials automatically
- storing live smoke results in the database
- real Aliyun network calls in automated tests
- CI-hosted cloud credentials
- raw plaintext, ciphertext, access keys, or full KMS key IDs in output

## Architecture

Create `apps/api/app/ai_company_api/services/kms_readiness.py` as the single
service module for KMS readiness checks. It should depend on the existing
SecretVault provider factory and Aliyun settings validation rather than
duplicating adapter logic.

The module exposes small, testable functions:

```python
def run_kms_readiness(*, live: bool = False) -> KmsReadinessResult:
    ...

def run_kms_preflight() -> KmsReadinessResult:
    ...

def run_kms_live_smoke() -> KmsReadinessResult:
    ...
```

`KmsReadinessResult` is a Pydantic model or dataclass with JSON-safe fields:

- `status`: `ready_for_live_smoke`, `passed`, or `failed`
- `stage`: `preflight` or `live_smoke`
- `provider`: configured provider name
- `key_id_hint`: a redacted hint such as `sha256:<12 hex chars>` or
  `...<last4>`, never the full key id
- `checks`: ordered check results for config, seal, open, fingerprint, and
  delete
- `error_code`: optional stable error code
- `message`: optional redacted operator message

Add `apps/api/app/ai_company_api/tools/kms_readiness.py` with:

```bash
python -m ai_company_api.tools.kms_readiness
python -m ai_company_api.tools.kms_readiness --live
```

The default command runs preflight only. `--live` is required for real KMS
calls, so operators do not accidentally create cloud-side KMS audit events while
checking command wiring.

## Configuration

The preflight validates the current environment:

- `AI_SCDC_SECRET_VAULT_PROVIDER` must be `aliyun_kms` or `kms`
- `AI_SCDC_KMS_KEY_ID` must be set
- for `aliyun_kms`, the existing Aliyun SDK settings must be present:
  - `AI_SCDC_ALIYUN_REGION_ID`
  - `AI_SCDC_ALIYUN_ACCESS_KEY_ID`
  - `AI_SCDC_ALIYUN_ACCESS_KEY_SECRET`

The generic `kms` provider is allowed only when an injected KMS client is
present, which is useful for tests and future provider-specific adapters. In a
normal process without an injected client, generic `kms` remains fail-closed.

Configuration failures must reuse existing safe error behavior where possible.
Messages must not include access-key secret values, raw key material, or full
KMS key IDs.

## Data Flow

Preflight mode:

1. Read provider and key id from environment.
2. Validate provider is a KMS-capable provider.
3. Validate key id is present.
4. For `aliyun_kms`, validate region/access-key settings through
   `require_aliyun_settings(provider_name="kms", ...)`.
5. Return `ready_for_live_smoke` with redacted provider and key-id metadata.

Live-smoke mode:

1. Run the same preflight checks.
2. Generate a high-entropy temporary secret inside the process.
3. Build the configured `SecretVault` through `get_secret_vault()`.
4. Call `seal(temporary_secret)`.
5. Call `open(encrypted_secret)` and verify it matches the temporary secret.
6. Call `fingerprint(encrypted_secret)` and return only a short fingerprint
   hint.
7. Call `delete(encrypted_secret)` through the protocol.
8. Return `passed` with ordered check results.

The command never accepts a user-provided plaintext. The generated temporary
secret is never printed, logged, written to disk, or returned in test failure
messages.

## Failure Semantics

The readiness path returns a structured failure instead of leaking raw
exceptions.

Configuration failure:

- `status=failed`
- `stage=preflight`
- `error_code=configuration_error`
- `message` contains only safe env names or generic secret-env wording

SDK or vault failure during live smoke:

- `status=failed`
- `stage=live_smoke`
- `error_code=kms_error`
- `message` is redacted and stable

Round-trip mismatch:

- `status=failed`
- `stage=live_smoke`
- `error_code=roundtrip_mismatch`
- no plaintext, ciphertext, or full key id in output

Unexpected exceptions:

- converted to `status=failed`
- stable `error_code=unexpected_error`
- redacted message

CLI exit codes:

- exit `0` for `ready_for_live_smoke` and `passed`
- exit `1` for `failed`

## Security And Redaction

The output must not contain:

- `AI_SCDC_ALIYUN_ACCESS_KEY_SECRET` values
- raw access key IDs when they are not needed for action
- plaintext test secret
- encrypted secret envelope
- raw KMS ciphertext
- callback tokens or token hashes
- full KMS key id

The output may contain:

- provider name
- redacted key-id hint
- safe missing environment variable names, except the access-key secret field
  should follow existing `required secret environment variable` wording
- stable step names and status values

The live-smoke command should not log through application loggers unless later
operator logging is explicitly designed. JSON written to stdout is the
operator-facing result.

## Testing

Add `apps/api/tests/test_kms_readiness.py`.

Core service tests:

- preflight succeeds for complete `aliyun_kms` configuration without creating
  an SDK client or invoking KMS
- preflight fails closed when `AI_SCDC_KMS_KEY_ID` is missing
- preflight fails closed when Aliyun region or access-key ID is missing
- preflight fails closed when access-key secret is missing without printing the
  secret env value or secret value
- live smoke succeeds with a fake KMS client and records ordered
  seal/open/fingerprint/delete checks
- live smoke output does not contain temporary plaintext, ciphertext, or full
  key id
- live smoke failure from the fake KMS client returns `kms_error`
- live smoke round-trip mismatch returns `roundtrip_mismatch`

CLI tests:

- default command emits JSON and exits `0` when preflight is ready
- `--live` emits JSON and exits `0` when live smoke passes
- failed readiness emits JSON and exits `1`

Focused verification commands:

```bash
pytest apps/api/tests/test_kms_readiness.py apps/api/tests/test_aliyun_kms.py apps/api/tests/test_secret_access_audit.py -q
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_kms_readiness.py
git diff --check
```

Final verification should include the root project tests and typecheck when the
implementation is complete.

## Documentation

Update:

- `README.md`
- `STATUS.md`
- `docs/architecture.md`
- `docs/superpowers/status.md`
- `docs/operations/aliyun-operational-runbook.md`
- `docs/operations/aliyun-ram-policies.md`

The docs should say:

- a local KMS readiness command exists
- automated tests use fake clients and do not contact Aliyun
- preflight is safe to run without cloud KMS calls
- live smoke is operator-run only and must be executed in the target account
- successful local command support does not mean this repository has already
  validated a real cloud account
- operators must still provision credentials, review RAM scope, and retain
  their own live-smoke evidence before beta traffic

## Acceptance Criteria

- Operators have a documented command for KMS preflight and explicit live smoke.
- The default command performs no cloud KMS call.
- `--live` exercises the existing `SecretVault` protocol end to end.
- Missing KMS or Aliyun config fails closed.
- JSON output is structured, stable, and redacted.
- CLI exit code is `0` for ready/passed and `1` for failed.
- Automated tests do not contact Aliyun.
- Docs no longer describe live KMS smoke validation as a purely manual gap.
- Docs still state that a real target-account smoke must be run by operators
  before beta traffic.
