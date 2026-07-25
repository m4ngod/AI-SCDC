# Phase 13B KMS SecretVault Boundary Design

## Purpose

Phase 13B still lists real KMS-backed `SecretVault` integration as incomplete.
The current API has a `SecretVault` protocol and a fail-closed `KmsSecretVault`
placeholder, but there is no testable KMS boundary for production adapters.
This slice replaces the placeholder with an injectable KMS provider boundary
while deliberately deferring the real Aliyun KMS SDK adapter.

## Scope

Build a KMS-capable `SecretVault` implementation behind the existing protocol:

- keep `SecretVault` method signatures unchanged:
  `seal`, `open`, `rotate`, `delete`, and `fingerprint`
- add a narrow KMS client protocol for encryption, decryption, and delete
  notification
- add a wrapped KMS payload format with a stable `kms-vault:v1:` prefix
- require explicit KMS configuration when
  `AI_SCDC_SECRET_VAULT_PROVIDER` is `kms` or `aliyun_kms`
- preserve the existing dev provider for local development and tests
- add regression tests that use a fake KMS client instead of cloud SDK calls

This slice does not add the Aliyun KMS SDK, cloud credentials, production KMS
network calls, historical secret migration, bulk key rotation, or operator UI.

## Architecture

`DevSecretVault` remains the local provider and continues to emit
`dev-vault:v2:` payloads. `KmsSecretVault` becomes a real provider class that
depends on a `KmsClient` protocol instead of importing any cloud SDK directly.
The first concrete KMS client will be a deterministic fake used by tests. A
future Aliyun adapter can implement the same protocol without changing
model-credential, GitHub-credential, or secret-open call sites.

The KMS payload is an envelope encoded as URL-safe base64 JSON:

```text
kms-vault:v1:<base64url-json>
```

The decoded JSON contains only provider routing metadata and ciphertext:

```json
{
  "provider": "aliyun_kms",
  "key_id": "test-key",
  "ciphertext": "kms-ciphertext"
}
```

`KmsSecretVault.open()` validates the prefix, parses the envelope, checks that
the stored provider and key id match the configured vault, and then delegates
to `KmsClient.decrypt()`. Malformed envelopes, wrong providers, wrong keys, and
empty ciphertext are rejected before decryption.

## Configuration

`get_secret_vault()` keeps its current fail-closed behavior:

- empty, `dev`, or `development` returns `DevSecretVault`
- unknown providers raise `SecretVaultConfigurationError`
- `kms` and `aliyun_kms` require both a configured key id and a configured KMS
  client

The key id is read from `AI_SCDC_KMS_KEY_ID`. Because this slice does not ship
the real cloud adapter, production KMS mode remains unavailable unless a caller
explicitly installs a KMS client through a test or integration seam. Missing
KMS configuration raises `SecretVaultConfigurationError` and never falls back
to the dev provider.

For tests, add a helper such as `set_kms_client_for_tests(client)` alongside
`set_secret_vault_for_tests(vault)`. The full-vault override still wins first,
which keeps existing tests and service injection patterns stable.

## Data Flow

Sealing a secret:

1. Business service calls `(vault or get_secret_vault()).seal(secret_value)`.
2. `KmsSecretVault.seal()` calls `KmsClient.encrypt(secret_value)`.
3. The vault wraps the returned ciphertext with provider and key metadata.
4. The returned `SealedSecret` stores the wrapped encrypted payload plus the
   same `secret_last4` behavior already used by the dev vault.

Opening a secret:

1. Secret-access code calls `vault.open(encrypted_secret)`.
2. `KmsSecretVault` validates and unwraps the KMS envelope.
3. The vault calls `KmsClient.decrypt(ciphertext)`.
4. The plaintext is returned to existing audited access paths.

Rotating a secret validates the existing envelope through `open()` and seals
the new plaintext. Deleting a secret validates the envelope and calls
`KmsClient.delete(ciphertext)` so tests can assert delete propagation. A future
cloud adapter can implement delete as provider-specific cleanup or as a no-op
when KMS ciphertext deletion is not meaningful.

## Error Handling

KMS provider construction errors raise `SecretVaultConfigurationError`.
Payload problems raise `ValueError` with generic messages such as
`Unsupported KMS vault payload` or `Invalid KMS vault payload`. These errors
must not include plaintext, ciphertext, key material, cloud access keys, or raw
provider exception bodies.

`KmsClient` implementation errors are allowed to propagate as operational
failures, but tests should verify that the vault does not add sensitive values
to those errors. The fake client will keep errors deterministic and local.

## Testing

Tests are written before implementation in `apps/api/tests/test_secret_access_audit.py`.
Coverage includes:

- KMS mode with missing key id fails closed
- KMS mode with missing client fails closed
- fake KMS client round-trips `seal()` and `open()`
- fake KMS client supports `rotate()`, `delete()`, and `fingerprint()`
- malformed KMS payloads and wrong key/provider envelopes are rejected
- factory `kms` and `aliyun_kms` modes create `KmsSecretVault` when configured
- model credential creation stores a `kms-vault:v1:` payload and never stores
  the raw secret
- GitHub credential deletion uses the configured KMS delete path

Run focused tests first:

```powershell
pnpm --dir apps/api test tests/test_secret_access_audit.py
```

Then run the repo checks used by the previous Phase 13B slice:

```powershell
pnpm test
pnpm typecheck
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_secret_access_audit.py
git diff --check
```

## Documentation

After implementation, update `STATUS.md`, `docs/architecture.md`, and
`docs/superpowers/status.md` to say Phase 13B now has a test-backed KMS
SecretVault provider boundary. The docs must still state that the real Aliyun
KMS SDK adapter, cloud credential configuration, production smoke testing,
historical secret migration, and full commercial readiness remain future work.
