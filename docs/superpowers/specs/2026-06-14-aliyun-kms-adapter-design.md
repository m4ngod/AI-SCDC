# Phase 13B Aliyun KMS Adapter Design

## Purpose

Phase 13B now has a tested `KmsSecretVault` boundary, but the production
`aliyun_kms` provider still has no real Aliyun SDK adapter. This slice adds the
adapter code path, configuration wiring, fake-SDK regression tests, and
documentation updates needed to make `AI_SCDC_SECRET_VAULT_PROVIDER=aliyun_kms`
usable without relying on a test-only injected `KmsClient`.

This is intentionally not the cloud smoke slice. Tests must not call Aliyun,
and the documentation must continue to state that live cloud validation,
production credential provisioning, and CI secret setup remain open.

## Scope

Implement the Aliyun KMS adapter seam:

- add a dedicated `ai_company_api.services.aliyun_kms` module
- implement `SdkAliyunKmsClient` for the existing `KmsClient` protocol
- wire `get_secret_vault()` so `aliyun_kms` creates `SdkAliyunKmsClient`
- keep generic `kms` as an injected-client provider
- require explicit KMS key id and Aliyun SDK credentials for `aliyun_kms`
- add fake-SDK tests for request mapping, response mapping, and invalid SDK
  responses
- update README, status, architecture, and operations docs to reflect that the
  real adapter exists while live smoke remains pending

Out of scope:

- real Aliyun network calls in automated tests
- CI cloud credentials or live smoke execution
- historical `dev-vault:v2:` to `kms-vault:v1:` migration
- RAM role credential providers beyond the current access-key settings
- UI/operator controls for key rotation
- broad auth/session/IdP work

## Architecture

`secret_vault.py` remains responsible for the `SecretVault` protocol,
`KmsClient` protocol, `KmsSecretVault`, envelope validation, and provider
factory behavior. It should not import Aliyun SDK request models directly.

Create `apps/api/app/ai_company_api/services/aliyun_kms.py` with a focused
adapter:

```python
@dataclass(frozen=True)
class SdkAliyunKmsClient:
    settings: AliyunSettings

    def encrypt(self, key_id: str, plaintext: str) -> str: ...
    def decrypt(self, key_id: str, ciphertext: str) -> str: ...
    def delete(self, key_id: str, ciphertext: str) -> None: ...
```

The adapter imports SDK modules lazily inside `encrypt()` and `decrypt()`, like
the existing MNS/OSS/ECI adapters. This keeps module import safe in development
and test environments that do not configure the production provider.

`get_secret_vault()` behavior becomes:

- empty, `dev`, or `development`: return `DevSecretVault`
- `kms`: require `AI_SCDC_KMS_KEY_ID` and `_KMS_CLIENT_OVERRIDE`
- `aliyun_kms`: require `AI_SCDC_KMS_KEY_ID`, create `SdkAliyunKmsClient`,
  and return `KmsSecretVault(provider="aliyun_kms", client=client, key_id=key)`
- unknown provider: raise `SecretVaultConfigurationError`

The existing `set_kms_client_for_tests()` override still wins for both KMS
providers. This preserves deterministic tests and gives future integration
tests a narrow injection point.

## Configuration

`aliyun_kms` uses the existing Aliyun settings loader:

- `AI_SCDC_KMS_KEY_ID`
- `AI_SCDC_ALIYUN_REGION_ID`
- `AI_SCDC_ALIYUN_ACCESS_KEY_ID`
- `AI_SCDC_ALIYUN_ACCESS_KEY_SECRET`

`AI_SCDC_KMS_KEY_ID` stays in `secret_vault.py` because it is provider-neutral
vault configuration. Region and access-key values stay in `aliyun_config.py`
because they are shared Aliyun SDK credentials.

`require_aliyun_settings(provider_name="kms", required_names=(...))` should be
used so missing secret fields are reported as `required secret environment
variable` instead of printing `AI_SCDC_ALIYUN_ACCESS_KEY_SECRET` or its value.

## Data Flow

Sealing a secret:

1. The caller obtains a vault from `get_secret_vault()`.
2. `KmsSecretVault.seal(secret)` calls
   `SdkAliyunKmsClient.encrypt(key_id, secret)`.
3. The adapter maps the call to the Aliyun KMS SDK `Encrypt` request.
4. The SDK response ciphertext blob is returned to `KmsSecretVault`.
5. `KmsSecretVault` writes the existing strict `kms-vault:v1:` envelope.

Opening a secret:

1. `KmsSecretVault.open(encrypted_secret)` validates the prefix and envelope.
2. Provider and key id must match the configured vault.
3. The adapter maps `decrypt(key_id, ciphertext)` to the Aliyun KMS SDK
   `Decrypt` request.
4. The SDK plaintext is returned to the caller.

Deleting a secret:

Aliyun KMS does not delete a ciphertext blob. `SdkAliyunKmsClient.delete()`
therefore remains a no-op. `KmsSecretVault.delete()` still validates the
envelope before delegating, preserving the current safety check.

## Error Handling And Security

The provider remains fail-closed:

- missing `AI_SCDC_KMS_KEY_ID` raises `SecretVaultConfigurationError`
- missing Aliyun region/access-key configuration raises `AliyunConfigurationError`
- missing SDK package or SDK request failure is allowed to propagate
- empty SDK ciphertext from `encrypt()` raises `ValueError`
- empty SDK plaintext from `decrypt()` raises `ValueError`
- malformed envelopes, extra envelope fields, wrong provider, or wrong key id
  continue to raise `ValueError`

No code path falls back from `aliyun_kms` to the development vault. No error
message may include access-key secret values.

## SDK Shape

Use the official Classic KMS Python SDK shape documented by Alibaba Cloud:

- package: `alibabacloud_kms20160120`
- client: `alibabacloud_kms20160120.client.Client`
- models: `alibabacloud_kms20160120.models`
- OpenAPI config: `alibabacloud_tea_openapi.models.Config`

The exact request/response field names must be verified during implementation
against the installed SDK package and covered through fake module tests. The
design should not require a live cloud account to validate request construction.

Reference:
https://www.alibabacloud.com/help/en/kms/key-management-service/developer-reference/classic-kms-sdkclassic-kms-sdk/

## Testing

Add or extend `apps/api/tests/test_secret_access_audit.py`:

- `AI_SCDC_SECRET_VAULT_PROVIDER=aliyun_kms` creates a `KmsSecretVault` backed
  by `SdkAliyunKmsClient`
- the test KMS client override still works for `aliyun_kms`
- missing key id fails closed
- missing Aliyun region/access key/access secret fails closed
- secret configuration values are not leaked in error messages

Add `apps/api/tests/test_aliyun_kms.py`:

- fake `alibabacloud_kms20160120.client.Client`
- fake `alibabacloud_kms20160120.models`
- fake `alibabacloud_tea_openapi.models.Config`
- verify encrypt request receives the configured key id and plaintext
- verify decrypt request receives the configured ciphertext blob
- verify returned ciphertext and plaintext are read from SDK response bodies
- verify empty ciphertext/plaintext are rejected
- verify `delete()` performs no SDK call and raises no error

Verification commands for the implementation plan:

```bash
pnpm test
pnpm typecheck
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_secret_access_audit.py apps/api/tests/test_aliyun_kms.py
git diff --check
```

## Documentation

Update:

- `README.md`
- `STATUS.md`
- `docs/architecture.md`
- `docs/superpowers/status.md`
- `docs/operations/aliyun-ram-policies.md`
- `docs/operations/aliyun-operational-runbook.md`

The docs should say:

- `aliyun_kms` now has a real SDK adapter code path
- local automated tests use fake SDK modules and do not contact Aliyun
- production still needs RAM policy review, credential provisioning, and live
  smoke validation before beta
- the adapter uses `AI_SCDC_KMS_KEY_ID` plus existing Aliyun region/access-key
  settings

## Acceptance Criteria

- `AI_SCDC_SECRET_VAULT_PROVIDER=aliyun_kms` no longer depends on a test-only
  KMS client override
- `kms` continues to fail closed without an injected KMS client
- `aliyun_kms` fails closed when key id or Aliyun credentials are missing
- fake-SDK tests prove request and response mapping without network access
- invalid empty KMS SDK responses are rejected
- docs no longer describe the Aliyun KMS adapter as a placeholder
- docs still identify live cloud smoke and production credential setup as
  unfinished
