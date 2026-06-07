# Phase 13B KMS SecretVault Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the not-configured KMS secret-vault stub with a test-backed, injectable KMS provider boundary while keeping real Aliyun KMS SDK integration out of scope.

**Architecture:** Keep the existing `SecretVault` protocol and business-service call sites stable. Add a `KmsClient` protocol plus `KmsSecretVault` envelope implementation in `secret_vault.py`; the factory fails closed unless KMS mode has both `AI_SCDC_KMS_KEY_ID` and a configured KMS client. Tests use a deterministic fake KMS client to exercise KMS seal/open/rotate/delete/fingerprint and credential create/delete paths.

**Tech Stack:** Python 3.11, FastAPI service layer, SQLModel tests, Pydantic, pytest, existing root `pnpm` verification scripts.

---

## File Structure

- Modify `apps/api/app/ai_company_api/services/secret_vault.py`: add KMS client protocol, envelope parsing, factory configuration, and test client seam.
- Modify `apps/api/tests/test_secret_access_audit.py`: add fake KMS client and KMS provider tests that cover direct vault behavior plus model/GitHub credential storage and delete paths.
- Modify `STATUS.md`: mark the KMS provider boundary complete and keep real SDK/cloud readiness as remaining work.
- Modify `docs/architecture.md`: update the Phase 13B boundary language from KMS stub to test-backed KMS boundary.
- Modify `docs/superpowers/status.md`: update in-progress, known-limits, verification, and next-step language after implementation.

---

### Task 1: KMS Boundary Tests

**Files:**
- Modify: `apps/api/tests/test_secret_access_audit.py`

- [ ] **Step 1: Update test imports**

Change the top of `apps/api/tests/test_secret_access_audit.py` from:

```python
from hashlib import sha256
```

to:

```python
from base64 import b64decode, urlsafe_b64encode
import json
from hashlib import sha256
```

Change the entity import from:

```python
from ai_company_api.models.entities import SecretAccessAuditLog
```

to:

```python
from ai_company_api.models.entities import (
    GitHubCredential,
    ModelCredential,
    SecretAccessAuditLog,
)
```

Change the secret-vault import block to include the new key-id env constant and
test KMS-client seam:

```python
from ai_company_api.services.secret_vault import (
    SECRET_VAULT_KMS_KEY_ID_ENV,
    SECRET_VAULT_PROVIDER_ENV,
    DevSecretVault,
    KmsSecretVault,
    SealedSecret,
    SecretVaultConfigurationError,
    get_secret_vault,
    set_kms_client_for_tests,
    set_secret_vault_for_tests,
)
```

- [ ] **Step 2: Add fake KMS helpers**

Add these helpers after `FakeSecretVault`:

```python
class FakeKmsClient:
    def __init__(self) -> None:
        self.encrypt_requests: list[tuple[str, str]] = []
        self.decrypt_requests: list[tuple[str, str]] = []
        self.delete_requests: list[tuple[str, str]] = []

    def encrypt(self, key_id: str, plaintext: str) -> str:
        self.encrypt_requests.append((key_id, plaintext))
        encoded = urlsafe_b64encode(plaintext.encode("utf-8")).decode("ascii")
        return f"fake-kms:{key_id}:{encoded}"

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        self.decrypt_requests.append((key_id, ciphertext))
        prefix = f"fake-kms:{key_id}:"
        if not ciphertext.startswith(prefix):
            raise ValueError("Fake KMS ciphertext key mismatch")
        encoded = ciphertext.removeprefix(prefix)
        return b64decode(
            encoded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")

    def delete(self, key_id: str, ciphertext: str) -> None:
        self.delete_requests.append((key_id, ciphertext))


def kms_test_payload(payload: dict[str, str]) -> str:
    encoded = urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    return f"kms-vault:v1:{encoded}"
```

- [ ] **Step 3: Replace the old KMS not-configured test**

Remove the existing `test_secret_vault_factory_uses_kms_placeholder_without_dev_fallback`
test and add these tests in its place:

```python
def test_secret_vault_factory_fails_closed_for_kms_without_key_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(FakeKmsClient())
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")
    monkeypatch.delenv(SECRET_VAULT_KMS_KEY_ID_ENV, raising=False)
    try:
        with pytest.raises(SecretVaultConfigurationError) as exc_info:
            get_secret_vault()
    finally:
        set_kms_client_for_tests(None)

    assert SECRET_VAULT_KMS_KEY_ID_ENV in str(exc_info.value)


def test_secret_vault_factory_fails_closed_for_kms_without_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")

    with pytest.raises(SecretVaultConfigurationError) as exc_info:
        get_secret_vault()

    assert "KMS SecretVault provider is not configured" in str(exc_info.value)


@pytest.mark.parametrize("provider", ["kms", "aliyun_kms"])
def test_secret_vault_factory_uses_configured_kms_client(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    fake_kms = FakeKmsClient()
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(fake_kms)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, provider)
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")
    try:
        vault = get_secret_vault()
        sealed = vault.seal("sk-production-secret")
        opened = vault.open(sealed.encrypted_secret)
    finally:
        set_kms_client_for_tests(None)

    assert isinstance(vault, KmsSecretVault)
    assert not isinstance(vault, DevSecretVault)
    assert sealed.encrypted_secret.startswith("kms-vault:v1:")
    assert sealed.secret_last4 == "cret"
    assert opened == "sk-production-secret"
    assert fake_kms.encrypt_requests == [("key-production", "sk-production-secret")]
    assert fake_kms.decrypt_requests[0][0] == "key-production"
```

- [ ] **Step 4: Add direct KMS vault behavior tests**

Add these tests after the factory tests:

```python
def test_kms_secret_vault_rotates_deletes_and_fingerprints() -> None:
    fake_kms = FakeKmsClient()
    vault = KmsSecretVault(
        client=fake_kms,
        key_id="key-a",
        provider="aliyun_kms",
    )
    sealed = vault.seal("sk-example1234")

    rotated = vault.rotate(sealed.encrypted_secret, "sk-rotated5678")

    assert sealed.encrypted_secret.startswith("kms-vault:v1:")
    assert "sk-example1234" not in sealed.encrypted_secret
    assert vault.open(rotated.encrypted_secret) == "sk-rotated5678"
    assert rotated.secret_last4 == "5678"
    assert vault.fingerprint(rotated.encrypted_secret) == (
        "sha256:" + sha256("sk-rotated5678".encode("utf-8")).hexdigest()
    )
    assert vault.delete(rotated.encrypted_secret) is None
    assert fake_kms.encrypt_requests == [
        ("key-a", "sk-example1234"),
        ("key-a", "sk-rotated5678"),
    ]
    assert fake_kms.delete_requests[-1][0] == "key-a"


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "dev-vault:v2:c2stdGVzdA==",
        "kms-vault:v1:",
        "kms-vault:v1:not-base64",
        kms_test_payload(
            {
                "provider": "kms",
                "key_id": "key-a",
                "ciphertext": "fake-kms:key-a:c2stdGVzdA==",
            }
        ),
        kms_test_payload(
            {
                "provider": "aliyun_kms",
                "key_id": "key-b",
                "ciphertext": "fake-kms:key-a:c2stdGVzdA==",
            }
        ),
        kms_test_payload(
            {
                "provider": "aliyun_kms",
                "key_id": "key-a",
                "ciphertext": "",
            }
        ),
    ],
)
def test_kms_secret_vault_rejects_invalid_payloads(payload: str) -> None:
    vault = KmsSecretVault(
        client=FakeKmsClient(),
        key_id="key-a",
        provider="aliyun_kms",
    )

    with pytest.raises(ValueError):
        vault.open(payload)
```

- [ ] **Step 5: Add model credential KMS storage test**

Add this test after `test_model_credential_delete_uses_configured_secret_vault`:

```python
def test_model_credential_create_uses_configured_kms_secret_vault(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "app.db"
    secret_value = "sk-kms-model-1234"
    fake_kms = FakeKmsClient()
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(fake_kms)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "model-key")
    try:
        with build_client(database_path) as client:
            provider = client.post(
                "/model-providers",
                json={"name": "deepseek-kms", "provider_type": "deepseek"},
            ).json()
            credential = client.post(
                "/model-credentials",
                json={
                    "provider_id": provider["id"],
                    "display_name": "DeepSeek KMS key",
                    "secret_value": secret_value,
                },
            ).json()
    finally:
        set_kms_client_for_tests(None)

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        stored = session.exec(select(ModelCredential)).one()

    assert credential["secret_last4"] == "1234"
    assert stored.encrypted_secret.startswith("kms-vault:v1:")
    assert secret_value not in stored.encrypted_secret
    assert secret_value not in str(credential)
    assert fake_kms.encrypt_requests == [("model-key", secret_value)]
```

- [ ] **Step 6: Add GitHub credential KMS delete test**

Add this test after `test_github_credential_create_and_delete_records_secret_audit`:

```python
def test_github_credential_delete_uses_configured_kms_secret_vault(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "app.db"
    token = "ghp_kms_delete_1234"
    fake_kms = FakeKmsClient()
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(fake_kms)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "github-key")
    try:
        with build_client(database_path) as client:
            credential = client.post(
                "/github-credentials",
                json={"display_name": "GitHub KMS token", "token": token},
            ).json()
            response = client.delete(f"/github-credentials/{credential['id']}")
    finally:
        set_kms_client_for_tests(None)

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        stored = session.exec(select(GitHubCredential)).one()

    assert response.status_code == 200
    assert stored.encrypted_token.startswith("kms-vault:v1:")
    assert token not in stored.encrypted_token
    assert token not in str(response.json())
    assert fake_kms.encrypt_requests == [("github-key", token)]
    assert len(fake_kms.delete_requests) == 1
    assert fake_kms.delete_requests[0][0] == "github-key"
```

- [ ] **Step 7: Run tests to verify RED**

Run:

```powershell
pytest apps/api/tests/test_secret_access_audit.py -q
```

Expected: collection or selected tests fail because
`SECRET_VAULT_KMS_KEY_ID_ENV`, `set_kms_client_for_tests`, and the configured
`KmsSecretVault` constructor do not exist yet.

- [ ] **Step 8: Commit RED tests**

Run:

```powershell
git add apps/api/tests/test_secret_access_audit.py
git commit -m "test: cover kms secret vault boundary"
```

Expected: Git creates a test-only commit. It is acceptable that the focused
test command is red at this checkpoint.

---

### Task 2: KMS SecretVault Implementation

**Files:**
- Modify: `apps/api/app/ai_company_api/services/secret_vault.py`

- [ ] **Step 1: Replace `secret_vault.py` with the KMS-capable provider**

Replace the file content with:

```python
from base64 import b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from hashlib import sha256
import json
from json import JSONDecodeError
import os
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError


class SealedSecret(BaseModel):
    encrypted_secret: str = Field(min_length=1)
    secret_last4: str


class SecretVault(Protocol):
    def seal(self, secret_value: str) -> SealedSecret:
        ...

    def open(self, encrypted_secret: str) -> str:
        ...

    def rotate(self, encrypted_secret: str, new_secret_value: str) -> SealedSecret:
        ...

    def delete(self, encrypted_secret: str) -> None:
        ...

    def fingerprint(self, encrypted_secret: str) -> str:
        ...


class KmsClient(Protocol):
    def encrypt(self, key_id: str, plaintext: str) -> str:
        ...

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        ...

    def delete(self, key_id: str, ciphertext: str) -> None:
        ...


class SecretVaultConfigurationError(RuntimeError):
    pass


class _KmsVaultEnvelope(BaseModel):
    provider: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    ciphertext: str = Field(min_length=1)


class DevSecretVault:
    _prefix = "dev-vault:v2:"

    def seal(self, secret_value: str) -> SealedSecret:
        encoded = urlsafe_b64encode(secret_value.encode("utf-8")).decode("ascii")
        return SealedSecret(
            encrypted_secret=f"{self._prefix}{encoded}",
            secret_last4=secret_value[-4:] if len(secret_value) >= 4 else secret_value,
        )

    def open(self, encrypted_secret: str) -> str:
        if not encrypted_secret.startswith(self._prefix):
            raise ValueError("Unsupported dev vault payload")
        encoded = encrypted_secret.removeprefix(self._prefix)
        if encoded == "":
            raise ValueError("Invalid dev vault payload")
        try:
            encoded_bytes = encoded.encode("ascii")
            return b64decode(encoded_bytes, altchars=b"-_", validate=True).decode("utf-8")
        except (BinasciiError, UnicodeEncodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid dev vault payload") from exc

    def rotate(self, encrypted_secret: str, new_secret_value: str) -> SealedSecret:
        self.open(encrypted_secret)
        return self.seal(new_secret_value)

    def delete(self, encrypted_secret: str) -> None:
        self.open(encrypted_secret)

    def fingerprint(self, encrypted_secret: str) -> str:
        secret = self.open(encrypted_secret)
        return f"sha256:{sha256(secret.encode('utf-8')).hexdigest()}"


class KmsSecretVault:
    _prefix = "kms-vault:v1:"

    def __init__(self, *, client: KmsClient | None, key_id: str, provider: str) -> None:
        if client is None:
            raise SecretVaultConfigurationError(
                "KMS SecretVault provider is not configured"
            )
        normalized_key_id = key_id.strip()
        if normalized_key_id == "":
            raise SecretVaultConfigurationError(
                f"{SECRET_VAULT_KMS_KEY_ID_ENV} is required for KMS SecretVault provider"
            )
        normalized_provider = provider.strip().lower()
        if normalized_provider == "":
            raise SecretVaultConfigurationError(
                "KMS SecretVault provider name is required"
            )
        self._client = client
        self.key_id = normalized_key_id
        self.provider = normalized_provider

    def seal(self, secret_value: str) -> SealedSecret:
        ciphertext = self._client.encrypt(self.key_id, secret_value)
        if ciphertext == "":
            raise ValueError("Invalid KMS vault payload")
        envelope = _KmsVaultEnvelope(
            provider=self.provider,
            key_id=self.key_id,
            ciphertext=ciphertext,
        )
        return SealedSecret(
            encrypted_secret=f"{self._prefix}{self._encode_envelope(envelope)}",
            secret_last4=secret_value[-4:] if len(secret_value) >= 4 else secret_value,
        )

    def open(self, encrypted_secret: str) -> str:
        envelope = self._decode_envelope(encrypted_secret)
        return self._client.decrypt(self.key_id, envelope.ciphertext)

    def rotate(self, encrypted_secret: str, new_secret_value: str) -> SealedSecret:
        self.open(encrypted_secret)
        return self.seal(new_secret_value)

    def delete(self, encrypted_secret: str) -> None:
        envelope = self._decode_envelope(encrypted_secret)
        self._client.delete(self.key_id, envelope.ciphertext)

    def fingerprint(self, encrypted_secret: str) -> str:
        secret = self.open(encrypted_secret)
        return f"sha256:{sha256(secret.encode('utf-8')).hexdigest()}"

    def _encode_envelope(self, envelope: _KmsVaultEnvelope) -> str:
        payload = envelope.model_dump()
        encoded = urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).decode("ascii")
        return encoded

    def _decode_envelope(self, encrypted_secret: str) -> _KmsVaultEnvelope:
        if not encrypted_secret.startswith(self._prefix):
            raise ValueError("Unsupported KMS vault payload")
        encoded = encrypted_secret.removeprefix(self._prefix)
        if encoded == "":
            raise ValueError("Invalid KMS vault payload")
        try:
            decoded = b64decode(
                encoded.encode("ascii"),
                altchars=b"-_",
                validate=True,
            ).decode("utf-8")
            payload = json.loads(decoded)
            envelope = _KmsVaultEnvelope.model_validate(payload)
        except (
            BinasciiError,
            JSONDecodeError,
            TypeError,
            UnicodeEncodeError,
            UnicodeDecodeError,
            ValidationError,
        ) as exc:
            raise ValueError("Invalid KMS vault payload") from exc

        if envelope.provider != self.provider or envelope.key_id != self.key_id:
            raise ValueError("Invalid KMS vault payload")
        return envelope


SECRET_VAULT_PROVIDER_ENV = "AI_SCDC_SECRET_VAULT_PROVIDER"
SECRET_VAULT_KMS_KEY_ID_ENV = "AI_SCDC_KMS_KEY_ID"
_SECRET_VAULT_OVERRIDE: SecretVault | None = None
_KMS_CLIENT_OVERRIDE: KmsClient | None = None


def set_secret_vault_for_tests(vault: SecretVault | None) -> None:
    global _SECRET_VAULT_OVERRIDE
    _SECRET_VAULT_OVERRIDE = vault


def set_kms_client_for_tests(client: KmsClient | None) -> None:
    global _KMS_CLIENT_OVERRIDE
    _KMS_CLIENT_OVERRIDE = client


def get_secret_vault() -> SecretVault:
    if _SECRET_VAULT_OVERRIDE is not None:
        return _SECRET_VAULT_OVERRIDE

    provider = os.getenv(SECRET_VAULT_PROVIDER_ENV, "dev").strip().lower()
    if provider in {"", "dev", "development"}:
        return DevSecretVault()
    if provider in {"kms", "aliyun_kms"}:
        key_id = os.getenv(SECRET_VAULT_KMS_KEY_ID_ENV, "").strip()
        if key_id == "":
            raise SecretVaultConfigurationError(
                f"{SECRET_VAULT_KMS_KEY_ID_ENV} is required for KMS SecretVault provider"
            )
        return KmsSecretVault(
            client=_KMS_CLIENT_OVERRIDE,
            key_id=key_id,
            provider=provider,
        )
    raise SecretVaultConfigurationError(
        f"Secret vault provider {provider!r} is not configured"
    )
```

- [ ] **Step 2: Run focused tests to verify GREEN**

Run:

```powershell
pytest apps/api/tests/test_secret_access_audit.py -q
```

Expected: all tests in `test_secret_access_audit.py` pass.

- [ ] **Step 3: Run import and syntax verification**

Run:

```powershell
python -m compileall -q apps/api/app/ai_company_api/services/secret_vault.py apps/api/tests/test_secret_access_audit.py
```

Expected: command exits with status 0.

- [ ] **Step 4: Commit implementation**

Run:

```powershell
git add apps/api/app/ai_company_api/services/secret_vault.py apps/api/tests/test_secret_access_audit.py
git commit -m "feat: add kms secret vault boundary"
```

Expected: Git creates a commit containing the KMS provider boundary and the now-passing tests.

---

### Task 3: Status Documentation

**Files:**
- Modify: `STATUS.md`
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`

- [ ] **Step 1: Update `STATUS.md` scope text**

In `STATUS.md`, replace this paragraph:

```markdown
A follow-on Phase 13B slice extends the `SecretVault` protocol with
`rotate`, `delete`, and `fingerprint`, adds a fail-closed provider factory,
adds a not-configured `KmsSecretVault` placeholder for `kms`/`aliyun_kms`, and
records `SecretAccessAuditLog` rows for model/GitHub credential create/delete
plus model planner, GitHub pull-request, Docker cloud-run, and remote-worker
payload credential opens. Audit rows record actor/scope metadata, secret
kind/id, reason, operation, and success status, never raw or encrypted secret
payloads.
```

with:

```markdown
A follow-on Phase 13B slice extends the `SecretVault` protocol with
`rotate`, `delete`, and `fingerprint`, adds a fail-closed provider factory,
adds a test-backed `KmsSecretVault` provider boundary for `kms`/`aliyun_kms`,
and records `SecretAccessAuditLog` rows for model/GitHub credential
create/delete plus model planner, GitHub pull-request, Docker cloud-run, and
remote-worker payload credential opens. Audit rows record actor/scope
metadata, secret kind/id, reason, operation, and success status, never raw or
encrypted secret payloads. KMS mode requires `AI_SCDC_KMS_KEY_ID` and an
explicitly configured KMS client seam; it never falls back to development
storage.
```

- [ ] **Step 2: Update `STATUS.md` remaining work text**

In `STATUS.md`, replace this sentence:

```markdown
work includes full login/session issuance, production auth/IdP integration,
real KMS-backed `SecretVault` provider integration, a full operator console,
public destructive OSS cleanup, broader audit coverage, and a complete role
permission matrix.
```

with:

```markdown
work includes full login/session issuance, production auth/IdP integration,
the real Aliyun KMS SDK adapter and cloud smoke path, a full operator console,
public destructive OSS cleanup, broader audit coverage, and a complete role
permission matrix.
```

Replace this sentence:

```markdown
added. No real KMS SDK is wired yet.
```

with:

```markdown
added. No real KMS SDK or cloud KMS credential path is wired yet.
```

- [ ] **Step 3: Update `docs/architecture.md` Phase 13B text**

In `docs/architecture.md`, replace this paragraph:

```markdown
The next Phase 13B slice extends the `SecretVault` protocol with rotate,
delete, and fingerprint operations, adds a fail-closed provider factory, and
returns a not-configured `KmsSecretVault` placeholder for `kms`/`aliyun_kms`
configuration instead of falling back to development storage. It also adds
`SecretAccessAuditLog` records for model and GitHub credential create/delete,
plus model planner, GitHub pull-request, Docker cloud-run, and remote-worker
payload credential opens. Audit records capture scope, actor, secret kind/id,
reason, operation, and success status without storing raw or encrypted secret
payloads.
```

with:

```markdown
The next Phase 13B slice extends the `SecretVault` protocol with rotate,
delete, and fingerprint operations, adds a fail-closed provider factory, and
adds a test-backed `KmsSecretVault` provider boundary for `kms`/`aliyun_kms`
configuration instead of falling back to development storage. KMS mode wraps
provider, key id, and ciphertext metadata in a `kms-vault:v1:` envelope,
requires `AI_SCDC_KMS_KEY_ID` plus an explicitly configured KMS client seam,
and keeps the real Aliyun KMS SDK adapter for a later slice. It also adds
`SecretAccessAuditLog` records for model and GitHub credential create/delete,
plus model planner, GitHub pull-request, Docker cloud-run, and remote-worker
payload credential opens. Audit records capture scope, actor, secret kind/id,
reason, operation, and success status without storing raw or encrypted secret
payloads.
```

In the Phase 13B incomplete-work paragraph, replace:

```markdown
issuance, production IdP integration, real KMS-backed `SecretVault` provider
integration, billing, a full operator console, public destructive OSS cleanup,
complete audit logging, or a complete role-by-route permission matrix.
```

with:

```markdown
issuance, production IdP integration, the real Aliyun KMS SDK adapter and cloud
KMS smoke path, billing, a full operator console, public destructive OSS
cleanup, complete audit logging, or a complete role-by-route permission matrix.
```

In the roadmap in-progress item for Phase 13B, replace:

```markdown
   factory, and secret-access audit logs. Remaining work includes real
   KMS-backed secrets, full sessions, broader/full organization-scoped operator
   controls or operator console coverage, broader audit coverage, and a
   complete permission matrix.
```

with:

```markdown
   factory, a test-backed KMS provider boundary, and secret-access audit logs.
   Remaining work includes the real Aliyun KMS SDK adapter, full sessions,
   broader/full organization-scoped operator controls or operator console
   coverage, broader audit coverage, and a complete permission matrix.
```

- [ ] **Step 4: Update `docs/superpowers/status.md`**

In `docs/superpowers/status.md`, replace this in-progress bullet:

```markdown
- `SecretVault` protocol coverage for seal/open/rotate/delete/fingerprint with
  a fail-closed provider factory and not-configured KMS placeholder.
```

with:

```markdown
- `SecretVault` protocol coverage for seal/open/rotate/delete/fingerprint with
  a fail-closed provider factory and test-backed KMS provider boundary.
```

Replace this remaining-work sentence:

```markdown
auth/IdP integration, real KMS-backed `SecretVault` provider integration,
a full operator console, public destructive OSS cleanup, broader audit
coverage, a complete role-specific permission matrix, real provider price
tables, payment integration, invoices, and desktop billing UI.
```

with:

```markdown
auth/IdP integration, the real Aliyun KMS SDK adapter and cloud KMS smoke path,
a full operator console, public destructive OSS cleanup, broader audit
coverage, a complete role-specific permission matrix, real provider price
tables, payment integration, invoices, and desktop billing UI.
```

Replace this known-limit bullet:

```markdown
- Phase 13B now has request identity, workspace scope, and secret-open audit
  foundations, but it does not yet add full login/session issuance, production
  IdP integration, real KMS, billing, subscriptions, complete audit logging,
  WebSockets/SSE, or a second cloud provider.
```

with:

```markdown
- Phase 13B now has request identity, workspace scope, secret-open audit
  foundations, and a test-backed KMS SecretVault boundary, but it does not yet
  add full login/session issuance, production IdP integration, the real Aliyun
  KMS SDK adapter, billing, subscriptions, complete audit logging,
  WebSockets/SSE, or a second cloud provider.
```

Replace this known-limit sentence:

```markdown
- Authentication, organization RBAC, subscriptions, billing collection, and
  production KMS are still development placeholders.
```

with:

```markdown
- Authentication, organization RBAC, subscriptions, billing collection, and the
  production Aliyun KMS SDK adapter are still development boundaries.
```

Replace the recommended next phase:

```markdown
The next production step should continue Phase 13B with real KMS-backed
`SecretVault` provider integration plus broader/full organization-scoped
operator controls and operator console coverage before commercial beta.
```

with:

```markdown
The next production step should continue Phase 13B with the real Aliyun KMS SDK
adapter and cloud KMS smoke path, plus broader/full organization-scoped
operator controls and operator console coverage before commercial beta.
```

- [ ] **Step 5: Add current verification line**

After the existing `pytest apps/api/tests/test_secret_access_audit.py -q`
verification line in `docs/superpowers/status.md`, add:

```markdown
- `pytest apps/api/tests/test_secret_access_audit.py -q`: KMS provider boundary
  tests included; passed in this slice.
```

- [ ] **Step 6: Run documentation checks**

Run:

```powershell
rg -n "not-configured KMS placeholder|real KMS-backed `SecretVault` provider integration|real KMS-backed" STATUS.md docs/architecture.md docs/superpowers/status.md
git diff --check
```

Expected: `rg` returns no remaining stale phrases, and `git diff --check`
exits with status 0 apart from possible Git LF-to-CRLF warnings.

- [ ] **Step 7: Commit documentation**

Run:

```powershell
git add STATUS.md docs/architecture.md docs/superpowers/status.md
git commit -m "docs: record kms secret vault boundary"
```

Expected: Git creates a docs-only commit.

---

### Task 4: Final Verification

**Files:**
- Read: `apps/api/app/ai_company_api/services/secret_vault.py`
- Read: `apps/api/tests/test_secret_access_audit.py`
- Read: `STATUS.md`
- Read: `docs/architecture.md`
- Read: `docs/superpowers/status.md`

- [ ] **Step 1: Run focused secret-vault tests**

Run:

```powershell
pytest apps/api/tests/test_secret_access_audit.py -q
```

Expected: all tests in the file pass. The expected count is the previous 9
tests plus the newly added KMS tests.

- [ ] **Step 2: Run related credential and planner tests**

Run:

```powershell
pytest apps/api/tests/test_secret_access_audit.py apps/api/tests/test_model_settings_api.py apps/api/tests/test_github_repository_api.py apps/api/tests/test_model_planner.py apps/api/tests/test_planner_endpoints.py apps/api/tests/test_pull_request_api.py -q
```

Expected: all selected tests pass with no secret leakage assertion failures.

- [ ] **Step 3: Run Python compile check**

Run:

```powershell
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_secret_access_audit.py apps/api/tests/test_model_planner.py
```

Expected: command exits with status 0.

- [ ] **Step 4: Run full repository tests**

Run:

```powershell
pnpm test
```

Expected: JavaScript and Python tests pass. The known Starlette/httpx
deprecation warning can remain.

- [ ] **Step 5: Run typecheck**

Run:

```powershell
pnpm typecheck
```

Expected: workspace typecheck passes.

- [ ] **Step 6: Run diff hygiene**

Run:

```powershell
git diff --check
git status --short --branch
```

Expected: `git diff --check` passes. `git status` shows the implementation
branch ahead by the new commits and no unstaged changes.

- [ ] **Step 7: Summarize verification evidence**

Record the focused and full verification results in the final response. Include
any remaining warnings exactly, especially the known Starlette/httpx warning or
Git LF-to-CRLF working-copy warnings.
