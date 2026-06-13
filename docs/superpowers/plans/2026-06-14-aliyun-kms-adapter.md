# Phase 13B Aliyun KMS Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the real Aliyun Classic KMS SDK adapter for the existing `KmsSecretVault` boundary while keeping live cloud smoke and CI cloud credentials out of scope.

**Architecture:** Keep `secret_vault.py` responsible for the vault protocol, KMS envelope, and provider selection. Add a focused `aliyun_kms.py` adapter that maps the internal `KmsClient` protocol to the Aliyun Classic KMS SDK, including base64 encoding for SDK plaintext and fail-closed Aliyun configuration validation. Tests use fake SDK modules and never contact Aliyun.

**Tech Stack:** Python 3.11, pytest, Pydantic, FastAPI service layer, Aliyun Classic KMS SDK package `alibabacloud_kms20160120>=2.4.0`, existing root `pnpm` verification scripts.

---

## File Structure

- Create `apps/api/app/ai_company_api/services/aliyun_kms.py`: Aliyun KMS adapter implementing `encrypt`, `decrypt`, `delete`, and a validated `get_aliyun_kms_client()` factory.
- Create `apps/api/tests/test_aliyun_kms.py`: fake-SDK adapter tests for config validation, request mapping, response mapping, base64 encoding/decoding, invalid responses, and no-op delete.
- Modify `apps/api/app/ai_company_api/services/secret_vault.py`: wire `AI_SCDC_SECRET_VAULT_PROVIDER=aliyun_kms` to `get_aliyun_kms_client()` while keeping generic `kms` injected-client-only.
- Modify `apps/api/tests/test_secret_access_audit.py`: add factory tests proving the real adapter path is selected for `aliyun_kms`, missing Aliyun config fails closed, and secret config values are not leaked.
- Modify `apps/api/pyproject.toml`: add the KMS SDK dependency.
- Modify `README.md`, `STATUS.md`, `docs/architecture.md`, `docs/superpowers/status.md`, `docs/operations/aliyun-ram-policies.md`, and `docs/operations/aliyun-operational-runbook.md`: update the KMS readiness wording.

---

### Task 1: Aliyun KMS Adapter Tests And Module

**Files:**
- Create: `apps/api/tests/test_aliyun_kms.py`
- Create: `apps/api/app/ai_company_api/services/aliyun_kms.py`

- [ ] **Step 1: Write the failing adapter test file**

Create `apps/api/tests/test_aliyun_kms.py` with this content:

```python
from base64 import b64decode, b64encode
import sys
from types import ModuleType, SimpleNamespace

import pytest

from ai_company_api.services.aliyun_config import (
    AliyunConfigurationError,
    AliyunSettings,
)
from ai_company_api.services.aliyun_kms import (
    SdkAliyunKmsClient,
    get_aliyun_kms_client,
)


def test_get_aliyun_kms_client_requires_region() -> None:
    settings = _aliyun_settings(region_id=None)

    with pytest.raises(
        AliyunConfigurationError,
        match="AI_SCDC_ALIYUN_REGION_ID",
    ):
        get_aliyun_kms_client(settings=settings)


def test_get_aliyun_kms_client_requires_access_key_id() -> None:
    settings = _aliyun_settings(access_key_id=None)

    with pytest.raises(
        AliyunConfigurationError,
        match="AI_SCDC_ALIYUN_ACCESS_KEY_ID",
    ):
        get_aliyun_kms_client(settings=settings)


def test_get_aliyun_kms_client_requires_access_key_secret_without_leaking_value() -> None:
    settings = _aliyun_settings(access_key_secret=None)

    with pytest.raises(AliyunConfigurationError) as exc_info:
        get_aliyun_kms_client(settings=settings)

    message = str(exc_info.value)
    assert "required secret environment variable" in message
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in message
    assert "ak-secret" not in message


def test_sdk_aliyun_kms_encrypt_encodes_plaintext_and_maps_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_kms_sdk(
        monkeypatch,
        encrypt_ciphertext_blob="kms-ciphertext-1",
    )

    ciphertext = SdkAliyunKmsClient(_aliyun_settings()).encrypt(
        "kms-key-1",
        "secret-value",
    )

    assert ciphertext == "kms-ciphertext-1"
    config = captured["client_config"]
    assert config.region_id == "cn-hangzhou"
    assert config.access_key_id == "ak-id"
    assert config.access_key_secret == "ak-secret"
    request = captured["encrypt_request"]
    assert request.key_id == "kms-key-1"
    decoded_plaintext = b64decode(
        request.plaintext.encode("ascii"),
        validate=True,
    ).decode("utf-8")
    assert decoded_plaintext == "secret-value"


def test_sdk_aliyun_kms_decrypt_decodes_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_kms_sdk(
        monkeypatch,
        decrypt_plaintext_blob=b64encode("opened-secret".encode("utf-8")).decode(
            "ascii"
        ),
    )

    plaintext = SdkAliyunKmsClient(_aliyun_settings()).decrypt(
        "kms-key-1",
        "kms-ciphertext-1",
    )

    assert plaintext == "opened-secret"
    request = captured["decrypt_request"]
    assert request.ciphertext_blob == "kms-ciphertext-1"


def test_sdk_aliyun_kms_encrypt_rejects_empty_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_kms_sdk(monkeypatch, encrypt_ciphertext_blob="")

    with pytest.raises(ValueError, match="Invalid KMS encrypt response"):
        SdkAliyunKmsClient(_aliyun_settings()).encrypt("kms-key-1", "secret-value")


def test_sdk_aliyun_kms_decrypt_rejects_empty_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_kms_sdk(monkeypatch, decrypt_plaintext_blob="")

    with pytest.raises(ValueError, match="Invalid KMS decrypt response"):
        SdkAliyunKmsClient(_aliyun_settings()).decrypt(
            "kms-key-1",
            "kms-ciphertext-1",
        )


def test_sdk_aliyun_kms_decrypt_rejects_malformed_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_kms_sdk(monkeypatch, decrypt_plaintext_blob="not base64")

    with pytest.raises(ValueError, match="Invalid KMS decrypt response"):
        SdkAliyunKmsClient(_aliyun_settings()).decrypt(
            "kms-key-1",
            "kms-ciphertext-1",
        )


def test_sdk_aliyun_kms_delete_is_noop_without_sdk_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_kms_sdk(monkeypatch)

    SdkAliyunKmsClient(_aliyun_settings()).delete("kms-key-1", "kms-ciphertext-1")

    assert "client_config" not in captured
    assert "encrypt_request" not in captured
    assert "decrypt_request" not in captured


def _install_fake_kms_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    encrypt_ciphertext_blob: str = "kms-ciphertext-1",
    decrypt_plaintext_blob: str | None = None,
) -> dict[str, object]:
    if decrypt_plaintext_blob is None:
        decrypt_plaintext_blob = b64encode("secret-value".encode("utf-8")).decode(
            "ascii"
        )
    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(
            self,
            *,
            access_key_id: str,
            access_key_secret: str,
            region_id: str,
        ) -> None:
            self.access_key_id = access_key_id
            self.access_key_secret = access_key_secret
            self.region_id = region_id

    class FakeEncryptRequest:
        def __init__(self, *, key_id: str, plaintext: str) -> None:
            self.key_id = key_id
            self.plaintext = plaintext

    class FakeDecryptRequest:
        def __init__(self, *, ciphertext_blob: str) -> None:
            self.ciphertext_blob = ciphertext_blob

    class FakeClient:
        def __init__(self, config: FakeConfig) -> None:
            captured["client_config"] = config

        def encrypt(self, request: FakeEncryptRequest):
            captured["encrypt_request"] = request
            body = SimpleNamespace(ciphertext_blob=encrypt_ciphertext_blob)
            return SimpleNamespace(body=body)

        def decrypt(self, request: FakeDecryptRequest):
            captured["decrypt_request"] = request
            body = SimpleNamespace(plaintext=decrypt_plaintext_blob)
            return SimpleNamespace(body=body)

    kms_package = ModuleType("alibabacloud_kms20160120")
    kms_client_module = ModuleType("alibabacloud_kms20160120.client")
    kms_models_module = ModuleType("alibabacloud_kms20160120.models")
    kms_client_module.Client = FakeClient
    kms_models_module.EncryptRequest = FakeEncryptRequest
    kms_models_module.DecryptRequest = FakeDecryptRequest
    kms_package.models = kms_models_module

    openapi_package = ModuleType("alibabacloud_tea_openapi")
    openapi_models_module = ModuleType("alibabacloud_tea_openapi.models")
    openapi_models_module.Config = FakeConfig
    openapi_package.models = openapi_models_module

    monkeypatch.setitem(sys.modules, "alibabacloud_kms20160120", kms_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_kms20160120.client",
        kms_client_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_kms20160120.models",
        kms_models_module,
    )
    monkeypatch.setitem(sys.modules, "alibabacloud_tea_openapi", openapi_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi.models",
        openapi_models_module,
    )
    return captured


def _aliyun_settings(
    *,
    region_id: str | None = "cn-hangzhou",
    access_key_id: str | None = "ak-id",
    access_key_secret: str | None = "ak-secret",
) -> AliyunSettings:
    return AliyunSettings(
        region_id=region_id,
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        mns_endpoint="https://123456.mns.cn-hangzhou.aliyuncs.com",
        mns_queue_name="ai-scdc-cloud-runs-dev",
        oss_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
        oss_bucket="ai-scdc-dev-artifacts",
        eci_vswitch_id="vsw-demo",
        eci_security_group_id="sg-demo",
        eci_image="registry.cn-hangzhou.aliyuncs.com/ai-scdc/remote-worker:dev",
        api_public_base_url="https://api.example.test",
    )
```

- [ ] **Step 2: Run the adapter tests and verify they fail for the missing module**

Run:

```bash
pytest apps/api/tests/test_aliyun_kms.py -q
```

Expected: FAIL during collection with:

```text
ModuleNotFoundError: No module named 'ai_company_api.services.aliyun_kms'
```

- [ ] **Step 3: Create the Aliyun KMS adapter module**

Create `apps/api/app/ai_company_api/services/aliyun_kms.py` with this content:

```python
from __future__ import annotations

from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from dataclasses import dataclass

from ai_company_api.services.aliyun_config import (
    AliyunSettings,
    load_aliyun_settings,
    require_aliyun_settings,
)


_REQUIRED_KMS_SETTING_NAMES = (
    "access_key_id",
    "access_key_secret",
    "region_id",
)


@dataclass(frozen=True)
class SdkAliyunKmsClient:
    settings: AliyunSettings

    def encrypt(self, key_id: str, plaintext: str) -> str:
        from alibabacloud_kms20160120 import models as kms_models

        plaintext_blob = b64encode(plaintext.encode("utf-8")).decode("ascii")
        result = self._client().encrypt(
            kms_models.EncryptRequest(
                key_id=key_id,
                plaintext=plaintext_blob,
            )
        )
        ciphertext = str(getattr(getattr(result, "body", None), "ciphertext_blob", "") or "")
        if ciphertext == "":
            raise ValueError("Invalid KMS encrypt response")
        return ciphertext

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        from alibabacloud_kms20160120 import models as kms_models

        result = self._client().decrypt(
            kms_models.DecryptRequest(ciphertext_blob=ciphertext)
        )
        plaintext_blob = str(getattr(getattr(result, "body", None), "plaintext", "") or "")
        if plaintext_blob == "":
            raise ValueError("Invalid KMS decrypt response")
        try:
            return b64decode(
                plaintext_blob.encode("ascii"),
                validate=True,
            ).decode("utf-8")
        except (BinasciiError, UnicodeEncodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid KMS decrypt response") from exc

    def delete(self, key_id: str, ciphertext: str) -> None:
        _ = (key_id, ciphertext)

    def _client(self):
        from alibabacloud_kms20160120.client import Client
        from alibabacloud_tea_openapi import models as openapi_models

        settings = require_aliyun_settings(
            provider_name="kms",
            required_names=_REQUIRED_KMS_SETTING_NAMES,
            settings=self.settings,
        )
        return Client(
            openapi_models.Config(
                access_key_id=settings.access_key_id,
                access_key_secret=settings.access_key_secret,
                region_id=settings.region_id,
            )
        )


def get_aliyun_kms_client(
    settings: AliyunSettings | None = None,
) -> SdkAliyunKmsClient:
    resolved = require_aliyun_settings(
        provider_name="kms",
        required_names=_REQUIRED_KMS_SETTING_NAMES,
        settings=settings or load_aliyun_settings(),
    )
    return SdkAliyunKmsClient(resolved)
```

- [ ] **Step 4: Run adapter tests and verify they pass**

Run:

```bash
pytest apps/api/tests/test_aliyun_kms.py -q
```

Expected: PASS with 8 tests.

- [ ] **Step 5: Commit adapter module and tests**

Run:

```bash
git add apps/api/app/ai_company_api/services/aliyun_kms.py apps/api/tests/test_aliyun_kms.py
git commit -m "feat: add aliyun kms adapter"
```

---

### Task 2: SecretVault Factory Wiring And Dependency

**Files:**
- Modify: `apps/api/app/ai_company_api/services/secret_vault.py`
- Modify: `apps/api/tests/test_secret_access_audit.py`
- Modify: `apps/api/pyproject.toml`

- [ ] **Step 1: Add failing factory tests**

In `apps/api/tests/test_secret_access_audit.py`, add this import after the
existing `sqlmodel` import block:

```python
from ai_company_api.services.aliyun_config import AliyunConfigurationError
from ai_company_api.services.aliyun_kms import SdkAliyunKmsClient
```

Add this helper after `kms_test_payload()`:

```python
def set_complete_aliyun_kms_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak-id")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "ak-secret")
```

Add these tests after `test_secret_vault_factory_uses_configured_kms_client`:

```python
def test_secret_vault_factory_uses_aliyun_kms_sdk_client_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")
    set_complete_aliyun_kms_env(monkeypatch)

    vault = get_secret_vault()

    assert isinstance(vault, KmsSecretVault)
    assert vault.provider == "aliyun_kms"
    assert vault.key_id == "key-production"
    assert isinstance(getattr(vault, "_client"), SdkAliyunKmsClient)


def test_secret_vault_factory_fails_closed_for_aliyun_kms_missing_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_REGION_ID", raising=False)

    with pytest.raises(
        AliyunConfigurationError,
        match="AI_SCDC_ALIYUN_REGION_ID",
    ):
        get_secret_vault()


def test_secret_vault_factory_fails_closed_for_aliyun_kms_missing_access_key_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", raising=False)

    with pytest.raises(
        AliyunConfigurationError,
        match="AI_SCDC_ALIYUN_ACCESS_KEY_ID",
    ):
        get_secret_vault()


def test_secret_vault_factory_fails_closed_for_aliyun_kms_missing_secret_without_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", raising=False)

    with pytest.raises(AliyunConfigurationError) as exc_info:
        get_secret_vault()

    message = str(exc_info.value)
    assert "required secret environment variable" in message
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in message
    assert "ak-secret" not in message
```

- [ ] **Step 2: Run the new factory tests and verify they fail**

Run:

```bash
pytest apps/api/tests/test_secret_access_audit.py -q -k "aliyun_kms_sdk_client or aliyun_kms_missing"
```

Expected: FAIL because `get_secret_vault()` still requires `_KMS_CLIENT_OVERRIDE`
for `aliyun_kms`.

- [ ] **Step 3: Wire the `aliyun_kms` provider in `secret_vault.py`**

In `apps/api/app/ai_company_api/services/secret_vault.py`, replace the current
`if provider in {"kms", "aliyun_kms"}:` block in `get_secret_vault()` with:

```python
    if provider in {"kms", "aliyun_kms"}:
        key_id = os.getenv(SECRET_VAULT_KMS_KEY_ID_ENV, "").strip()
        if key_id == "":
            raise SecretVaultConfigurationError(
                f"{SECRET_VAULT_KMS_KEY_ID_ENV} is required for KMS SecretVault provider"
            )
        if _KMS_CLIENT_OVERRIDE is not None:
            return KmsSecretVault(
                client=_KMS_CLIENT_OVERRIDE,
                key_id=key_id,
                provider=provider,
            )
        if provider == "aliyun_kms":
            from ai_company_api.services.aliyun_kms import get_aliyun_kms_client

            return KmsSecretVault(
                client=get_aliyun_kms_client(),
                key_id=key_id,
                provider=provider,
            )
        return KmsSecretVault(
            client=None,
            key_id=key_id,
            provider=provider,
        )
```

- [ ] **Step 4: Add the KMS SDK dependency**

In `apps/api/pyproject.toml`, add the dependency directly after the ECI SDK
dependency:

```toml
    "alibabacloud_kms20160120>=2.4.0",
```

The dependency block should contain this Aliyun group:

```toml
dependencies = [
    "alibabacloud-oss-v2>=1.0.0",
    "alibabacloud_eci20180808>=1.2.0",
    "alibabacloud_kms20160120>=2.4.0",
    "aliyun-mns-sdk>=1.1.0",
    "fastapi>=0.115.0",
    "pydantic>=2.10.0",
    "sqlmodel>=0.0.22",
    "uvicorn>=0.32.0",
]
```

- [ ] **Step 5: Refresh the editable API environment**

Run:

```bash
python -m pip install -e "apps/api[test]"
```

Expected: PASS and the output includes `alibabacloud_kms20160120`.

- [ ] **Step 6: Run factory and adapter tests**

Run:

```bash
pytest apps/api/tests/test_aliyun_kms.py apps/api/tests/test_secret_access_audit.py -q
```

Expected: PASS with all adapter tests and the existing secret access audit tests.

- [ ] **Step 7: Commit factory wiring and dependency**

Run:

```bash
git add apps/api/app/ai_company_api/services/secret_vault.py apps/api/tests/test_secret_access_audit.py apps/api/pyproject.toml
git commit -m "feat: wire aliyun kms secret vault provider"
```

---

### Task 3: Documentation Updates

**Files:**
- Modify: `README.md`
- Modify: `STATUS.md`
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`
- Modify: `docs/operations/aliyun-ram-policies.md`
- Modify: `docs/operations/aliyun-operational-runbook.md`

- [ ] **Step 1: Update README Aliyun Operations KMS wording**

In `README.md`, replace this paragraph:

```markdown
Use OSS lifecycle rules for development object retention. Do not add broad
API-side OSS deletion until authenticated organization-scoped operator controls
exist. `DevSecretVault` remains development-only; the API now has a fail-closed
secret-vault provider factory, a not-configured KMS adapter placeholder, and
secret create/open/delete audit records, but commercial production still must
provide a real KMS-backed `SecretVault` implementation before beta traffic.
```

with:

```markdown
Use OSS lifecycle rules for development object retention. Do not add broad
API-side OSS deletion until authenticated organization-scoped operator controls
exist. `DevSecretVault` remains development-only. When
`AI_SCDC_SECRET_VAULT_PROVIDER=aliyun_kms`, the API uses a real Aliyun Classic
KMS SDK adapter with `AI_SCDC_KMS_KEY_ID` plus the existing Aliyun
region/access-key settings. Automated tests use fake SDK modules and do not
contact Aliyun; commercial production still needs RAM policy review, credential
provisioning, and live KMS smoke validation before beta traffic.
```

- [ ] **Step 2: Update `STATUS.md` KMS summary**

In `STATUS.md`, replace this paragraph:

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

with:

```markdown
A follow-on Phase 13B slice extends the `SecretVault` protocol with
`rotate`, `delete`, and `fingerprint`, adds a fail-closed provider factory,
adds a test-backed `KmsSecretVault` provider boundary for `kms`, wires
`aliyun_kms` to a real Aliyun Classic KMS SDK adapter, and records
`SecretAccessAuditLog` rows for model/GitHub credential create/delete plus
model planner, GitHub pull-request, Docker cloud-run, and remote-worker payload
credential opens. Audit rows record actor/scope metadata, secret kind/id,
reason, operation, and success status, never raw or encrypted secret payloads.
KMS mode requires `AI_SCDC_KMS_KEY_ID`; `aliyun_kms` also requires the existing
Aliyun region/access-key settings and never falls back to development storage.
```

In the `Non-Goals` section, replace:

```markdown
the real Aliyun KMS SDK adapter, cloud KMS credential path, and cloud KMS smoke
path, a full operator console, public destructive OSS cleanup, broader audit
coverage, and a complete role permission matrix.
```

with:

```markdown
cloud KMS credential provisioning, cloud KMS smoke validation, production
auth/IdP integration, a full operator console, public destructive OSS cleanup,
broader audit coverage, and a complete role permission matrix.
```

Replace:

```markdown
added. No real KMS SDK or cloud KMS credential path is wired yet.
```

with:

```markdown
added. The real Aliyun KMS SDK adapter is wired, but no live cloud KMS smoke or
production cloud credential path was added.
```

- [ ] **Step 3: Update architecture Phase 13B KMS wording**

In `docs/architecture.md`, replace the paragraph beginning with
`The next Phase 13B slice extends the SecretVault protocol` with:

```markdown
The next Phase 13B slice extends the `SecretVault` protocol with rotate,
delete, and fingerprint semantics, adds a fail-closed provider factory, adds a
test-backed `KmsSecretVault` provider boundary for generic `kms`, and wires
`aliyun_kms` to a real Aliyun Classic KMS SDK adapter. KMS mode wraps provider,
key id, and ciphertext metadata in a `kms-vault:v1:` envelope, requires
`AI_SCDC_KMS_KEY_ID`, and never falls back to development storage. The Aliyun
adapter uses the existing Aliyun region/access-key settings and encodes
plaintext through the SDK-required base64 request field. It also adds
`SecretAccessAuditLog` records for model and GitHub credential create/delete,
plus model planner, GitHub pull-request, Docker cloud-run, and remote-worker
payload credential opens.
```

In the remaining-work paragraph, replace:

```markdown
issuance, production IdP integration, the real Aliyun KMS SDK adapter and cloud
KMS credential path or cloud KMS smoke path, billing, a full operator console,
```

with:

```markdown
issuance, production IdP integration, cloud KMS credential provisioning and
cloud KMS smoke validation, billing, a full operator console,
```

In the roadmap summary item that contains `Remaining work includes`, replace:

```markdown
   the real Aliyun KMS SDK adapter, cloud KMS credential path, cloud KMS smoke
   path,
```

with:

```markdown
   cloud KMS credential provisioning, cloud KMS smoke validation,
```

- [ ] **Step 4: Update Superpowers status**

In `docs/superpowers/status.md`, update the Phase 13B KMS bullet from:

```markdown
- `SecretVault` protocol coverage for seal/open/rotate/delete/fingerprint with
  a fail-closed provider factory and test-backed `KmsSecretVault` provider
  boundary for `kms`/`aliyun_kms`. KMS mode requires `AI_SCDC_KMS_KEY_ID` plus
  an explicitly configured KMS client seam and never falls back to development
  storage.
```

to:

```markdown
- `SecretVault` protocol coverage for seal/open/rotate/delete/fingerprint with
  a fail-closed provider factory, a test-backed `KmsSecretVault` provider
  boundary for generic `kms`, and a real Aliyun Classic KMS SDK adapter for
  `aliyun_kms`. KMS mode requires `AI_SCDC_KMS_KEY_ID`; `aliyun_kms` also
  requires existing Aliyun region/access-key settings and never falls back to
  development storage.
```

In the remaining commercial trust work paragraph, replace `the real Aliyun KMS
SDK adapter, cloud KMS credential path, and cloud KMS smoke path` with:

```markdown
cloud KMS credential provisioning and cloud KMS smoke validation
```

In the Known Limits bullet that starts with `Phase 13B now has request
identity`, replace the clause:

```markdown
  KMS SDK adapter, cloud KMS credential path, cloud KMS smoke path, billing,
```

with:

```markdown
  cloud KMS credential provisioning, cloud KMS smoke validation, billing,
```

In the final Recommended Next Phase section, replace:

```markdown
The next production step should continue Phase 13B with the real Aliyun KMS SDK
adapter, cloud KMS credential path, and cloud KMS smoke path, plus broader/full
organization-scoped operator controls and operator console coverage before
commercial beta.
```

with:

```markdown
The next production step should continue Phase 13B with cloud KMS credential
provisioning, live cloud KMS smoke validation, and broader/full
organization-scoped operator controls and operator console coverage before
commercial beta.
```

- [ ] **Step 5: Update Aliyun RAM policy KMS section**

In `docs/operations/aliyun-ram-policies.md`, replace the `Production KMS
Boundary` section with:

````markdown
## Production KMS Boundary

`DevSecretVault` is development-only. For production-style secret sealing, set
`AI_SCDC_SECRET_VAULT_PROVIDER=aliyun_kms`, `AI_SCDC_KMS_KEY_ID`,
`AI_SCDC_ALIYUN_REGION_ID`, `AI_SCDC_ALIYUN_ACCESS_KEY_ID`, and
`AI_SCDC_ALIYUN_ACCESS_KEY_SECRET`. The API process uses the Aliyun Classic KMS
SDK `Encrypt` and `Decrypt` actions; automated tests use fake SDK modules and
do not contact Aliyun.

Add KMS permissions only to the API control-plane role that seals and opens
stored model/GitHub credentials:

```json
{
  "Effect": "Allow",
  "Action": [
    "kms:Encrypt",
    "kms:Decrypt"
  ],
  "Resource": [
    "acs:kms:cn-hangzhou:1234567890123456:key/<kms-key-id>"
  ]
}
```

Validate the resource form in the Aliyun RAM policy simulator for the selected
KMS key. The pull worker role and assigned ECI worker must not receive KMS
decrypt permission or the API process's Aliyun access key secret.
````

- [ ] **Step 6: Update Aliyun operational runbook KMS section**

In `docs/operations/aliyun-operational-runbook.md`, replace the `KMS Boundary`
section with:

```markdown
## KMS Boundary

The codebase uses `DevSecretVault` by default for local development. For
production-style secret sealing, start the API with
`AI_SCDC_SECRET_VAULT_PROVIDER=aliyun_kms`, `AI_SCDC_KMS_KEY_ID`, and the
existing Aliyun region/access-key environment variables. The adapter calls
Aliyun Classic KMS `Encrypt` and `Decrypt`; it does not perform a remote delete
for ciphertext blobs.

Local automated tests use fake SDK modules and do not validate live Aliyun
credentials. Before beta traffic, operators must run a live KMS smoke in the
target account and verify RAM policy scope, KMS key state, request audit logs,
and failure behavior without pasting plaintext access keys, callback tokens,
queue receipts, signed URLs, GitHub tokens, or raw KMS plaintext into
operational records.
```

- [ ] **Step 7: Run documentation checks**

Run:

```bash
rg -n "not-configured KMS|No real KMS SDK|real Aliyun KMS SDK adapter, cloud KMS credential path|KMS adapter placeholder" README.md STATUS.md docs/architecture.md docs/superpowers/status.md docs/operations
git diff --check
```

Expected: `rg` returns no matches; `git diff --check` passes.

- [ ] **Step 8: Commit documentation updates**

Run:

```bash
git add README.md STATUS.md docs/architecture.md docs/superpowers/status.md docs/operations/aliyun-ram-policies.md docs/operations/aliyun-operational-runbook.md
git commit -m "docs: update aliyun kms adapter status"
```

---

### Task 4: Final Verification

**Files:**
- Verify: full repository state

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
pytest apps/api/tests/test_aliyun_kms.py apps/api/tests/test_secret_access_audit.py -q
```

Expected: PASS. Existing Starlette/httpx deprecation warnings are acceptable.

- [ ] **Step 2: Run full test suite**

Run:

```bash
pnpm test
```

Expected: PASS for JavaScript and Python test suites. Existing Starlette/httpx
deprecation warnings are acceptable.

- [ ] **Step 3: Run typecheck**

Run:

```bash
pnpm typecheck
```

Expected: PASS for `apps/desktop` and `packages/agent-protocol`.

- [ ] **Step 4: Compile Python modules**

Run:

```bash
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_secret_access_audit.py apps/api/tests/test_aliyun_kms.py
```

Expected: PASS with no output.

- [ ] **Step 5: Check whitespace**

Run:

```bash
git diff --check
```

Expected: PASS. Git LF-to-CRLF working-copy warnings are acceptable if there
are no whitespace error lines.

- [ ] **Step 6: Inspect final git state**

Run:

```bash
git status --short --branch
git log --oneline -5
```

Expected: working tree clean, with the adapter, factory, and docs commits at
the top of the branch.

---

## Self-Review Checklist

- Spec coverage: Tasks 1 and 2 implement the real adapter, provider factory,
  fail-closed config behavior, dependency, and fake-SDK tests. Task 3 updates
  all requested docs while keeping live cloud smoke and credential provisioning
  unfinished. Task 4 verifies the completed slice.
- Placeholder scan: This plan contains concrete file paths, exact snippets,
  commands, and expected outcomes.
- Type consistency: `SdkAliyunKmsClient.encrypt/decrypt/delete` matches the
  existing `KmsClient` protocol in `secret_vault.py`; `get_aliyun_kms_client`
  returns `SdkAliyunKmsClient`; tests use the SDK field names verified from
  `alibabacloud_kms20160120==2.4.0`.
