# Phase 13B KMS Production Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe local KMS readiness and explicit live-smoke command for the existing Phase 13B `SecretVault` KMS boundary.

**Architecture:** Keep KMS encryption/decryption behavior in the existing `SecretVault` and `aliyun_kms` adapter. Add a focused readiness service that performs redacted configuration preflight and, only with explicit live mode, one generated-secret `seal -> open -> fingerprint -> delete` round trip. Expose it through a local `python -m ai_company_api.tools.kms_readiness` command, not through HTTP.

**Tech Stack:** Python 3.11, pytest, Pydantic, existing FastAPI service package layout, existing Aliyun config and SecretVault services, root `pnpm` verification scripts.

---

## File Structure

- Create `apps/api/app/ai_company_api/services/kms_readiness.py`: KMS readiness result models, redaction helpers, preflight checks, and live-smoke execution.
- Create `apps/api/app/ai_company_api/tools/__init__.py`: package marker for local API tools.
- Create `apps/api/app/ai_company_api/tools/kms_readiness.py`: CLI entry point that prints readiness JSON and returns stable exit codes.
- Create `apps/api/tests/test_kms_readiness.py`: service and CLI tests using fake KMS clients only.
- Modify `README.md`: add KMS readiness command usage under Aliyun operations.
- Modify `docs/operations/aliyun-operational-runbook.md`: add operator steps for preflight, live smoke, redacted evidence, and failure handling.
- Modify `docs/operations/aliyun-ram-policies.md`: mention the readiness command as the post-policy validation step.
- Modify `STATUS.md`, `docs/superpowers/status.md`, and `docs/architecture.md`: update remaining-work wording to distinguish "local readiness path exists" from "target-account live evidence still operator-run".

---

### Task 1: KMS Readiness Preflight Service

**Files:**
- Create: `apps/api/app/ai_company_api/services/kms_readiness.py`
- Create: `apps/api/tests/test_kms_readiness.py`

- [ ] **Step 1: Write the failing preflight tests**

Create `apps/api/tests/test_kms_readiness.py` with this initial content:

```python
import json

import pytest

from ai_company_api.services.kms_readiness import run_kms_preflight
from ai_company_api.services.secret_vault import (
    SECRET_VAULT_KMS_KEY_ID_ENV,
    SECRET_VAULT_PROVIDER_ENV,
    set_kms_client_for_tests,
    set_secret_vault_for_tests,
)


class FakeKmsClient:
    def __init__(self) -> None:
        self.encrypt_requests: list[tuple[str, str]] = []
        self.decrypt_requests: list[tuple[str, str]] = []
        self.delete_requests: list[tuple[str, str]] = []

    def encrypt(self, key_id: str, plaintext: str) -> str:
        self.encrypt_requests.append((key_id, plaintext))
        return f"ciphertext-for-{key_id}"

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        self.decrypt_requests.append((key_id, ciphertext))
        return "opened-secret"

    def delete(self, key_id: str, ciphertext: str) -> None:
        self.delete_requests.append((key_id, ciphertext))


def set_complete_aliyun_kms_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "kms-key-production")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak-id")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "ak-secret-value")


def clear_kms_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        SECRET_VAULT_PROVIDER_ENV,
        SECRET_VAULT_KMS_KEY_ID_ENV,
        "AI_SCDC_ALIYUN_REGION_ID",
        "AI_SCDC_ALIYUN_ACCESS_KEY_ID",
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)


def result_json(result) -> str:
    return json.dumps(result.model_dump(), sort_keys=True)


def test_kms_preflight_succeeds_for_complete_aliyun_config_without_kms_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient()
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_preflight()
    finally:
        set_kms_client_for_tests(None)

    serialized = result_json(result)
    assert result.status == "ready_for_live_smoke"
    assert result.stage == "preflight"
    assert result.provider == "aliyun_kms"
    assert result.error_code is None
    assert result.key_id_hint.startswith("sha256:")
    assert "kms-key-production" not in serialized
    assert "ak-secret-value" not in serialized
    assert fake_kms.encrypt_requests == []
    assert fake_kms.decrypt_requests == []
    assert fake_kms.delete_requests == []


def test_kms_preflight_fails_when_key_id_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv(SECRET_VAULT_KMS_KEY_ID_ENV, raising=False)

    result = run_kms_preflight()

    assert result.status == "failed"
    assert result.stage == "preflight"
    assert result.error_code == "configuration_error"
    assert SECRET_VAULT_KMS_KEY_ID_ENV in (result.message or "")


def test_kms_preflight_fails_when_region_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_REGION_ID", raising=False)

    result = run_kms_preflight()

    assert result.status == "failed"
    assert result.stage == "preflight"
    assert result.error_code == "configuration_error"
    assert "AI_SCDC_ALIYUN_REGION_ID" in (result.message or "")


def test_kms_preflight_fails_when_access_key_id_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", raising=False)

    result = run_kms_preflight()

    assert result.status == "failed"
    assert result.stage == "preflight"
    assert result.error_code == "configuration_error"
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_ID" in (result.message or "")


def test_kms_preflight_fails_when_access_key_secret_is_missing_without_leaking_name_or_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", raising=False)

    result = run_kms_preflight()
    serialized = result_json(result)

    assert result.status == "failed"
    assert result.error_code == "configuration_error"
    assert "required secret environment variable" in (result.message or "")
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in serialized
    assert "ak-secret-value" not in serialized


def test_kms_preflight_rejects_dev_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "dev")

    result = run_kms_preflight()

    assert result.status == "failed"
    assert result.error_code == "configuration_error"
    assert "aliyun_kms" in (result.message or "")


def test_kms_preflight_allows_generic_kms_only_with_configured_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "generic-key")
    fake_kms = FakeKmsClient()
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_preflight()
    finally:
        set_kms_client_for_tests(None)

    assert result.status == "ready_for_live_smoke"
    assert result.provider == "kms"
    assert fake_kms.encrypt_requests == []
```

- [ ] **Step 2: Run the preflight tests and verify they fail for the missing module**

Run:

```bash
pytest apps/api/tests/test_kms_readiness.py -q
```

Expected: FAIL during collection with:

```text
ModuleNotFoundError: No module named 'ai_company_api.services.kms_readiness'
```

- [ ] **Step 3: Create the preflight service module**

Create `apps/api/app/ai_company_api/services/kms_readiness.py` with this content:

```python
from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import os
import secrets
from typing import Literal

from pydantic import BaseModel, Field

from ai_company_api.services.aliyun_config import (
    AliyunConfigurationError,
    load_aliyun_settings,
    require_aliyun_settings,
)
from ai_company_api.services.secret_vault import (
    SECRET_VAULT_KMS_KEY_ID_ENV,
    SECRET_VAULT_PROVIDER_ENV,
    SecretVault,
    SecretVaultConfigurationError,
    get_secret_vault,
)


KMS_READY_STATUS = "ready_for_live_smoke"
KMS_PASSED_STATUS = "passed"
KMS_FAILED_STATUS = "failed"
_KMS_PROVIDERS = {"kms", "aliyun_kms"}
_ALIYUN_KMS_REQUIRED_NAMES = (
    "access_key_id",
    "access_key_secret",
    "region_id",
)
_SECRET_ENV_NAMES = (
    "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
)


class KmsReadinessCheck(BaseModel):
    name: str
    status: Literal["passed", "failed", "skipped"]
    message: str | None = None


class KmsReadinessResult(BaseModel):
    status: Literal["ready_for_live_smoke", "passed", "failed"]
    stage: Literal["preflight", "live_smoke"]
    provider: str | None = None
    key_id_hint: str | None = None
    fingerprint_hint: str | None = None
    checks: list[KmsReadinessCheck] = Field(default_factory=list)
    error_code: str | None = None
    message: str | None = None

    def exit_code(self) -> int:
        return 1 if self.status == KMS_FAILED_STATUS else 0


def run_kms_readiness(
    *,
    live: bool = False,
    vault_factory: Callable[[], SecretVault] = get_secret_vault,
    secret_factory: Callable[[], str] | None = None,
) -> KmsReadinessResult:
    if live:
        return run_kms_live_smoke(
            vault_factory=vault_factory,
            secret_factory=secret_factory,
        )
    return run_kms_preflight(vault_factory=vault_factory)


def run_kms_preflight(
    *,
    vault_factory: Callable[[], SecretVault] = get_secret_vault,
) -> KmsReadinessResult:
    provider = _configured_provider()
    key_id = _configured_key_id()
    checks: list[KmsReadinessCheck] = []

    if provider not in _KMS_PROVIDERS:
        checks.append(
            KmsReadinessCheck(
                name="provider",
                status="failed",
                message="KMS readiness requires AI_SCDC_SECRET_VAULT_PROVIDER to be kms or aliyun_kms.",
            )
        )
        return _failure(
            stage="preflight",
            provider=provider,
            key_id=key_id,
            checks=checks,
            error_code="configuration_error",
            message=checks[-1].message,
        )
    checks.append(KmsReadinessCheck(name="provider", status="passed"))

    if key_id == "":
        message = (
            f"{SECRET_VAULT_KMS_KEY_ID_ENV} is required for KMS SecretVault provider"
        )
        checks.append(KmsReadinessCheck(name="key_id", status="failed", message=message))
        return _failure(
            stage="preflight",
            provider=provider,
            key_id=key_id,
            checks=checks,
            error_code="configuration_error",
            message=message,
        )
    checks.append(KmsReadinessCheck(name="key_id", status="passed"))

    try:
        if provider == "aliyun_kms":
            require_aliyun_settings(
                provider_name="kms",
                required_names=_ALIYUN_KMS_REQUIRED_NAMES,
                settings=load_aliyun_settings(),
            )
        else:
            vault_factory()
    except (AliyunConfigurationError, SecretVaultConfigurationError) as exc:
        checks.append(
            KmsReadinessCheck(
                name="configuration",
                status="failed",
                message=_redact_message(str(exc), key_id=key_id),
            )
        )
        return _failure(
            stage="preflight",
            provider=provider,
            key_id=key_id,
            checks=checks,
            error_code="configuration_error",
            message=checks[-1].message,
        )

    checks.append(KmsReadinessCheck(name="configuration", status="passed"))
    return KmsReadinessResult(
        status=KMS_READY_STATUS,
        stage="preflight",
        provider=provider,
        key_id_hint=_key_id_hint(key_id),
        checks=checks,
    )


def run_kms_live_smoke(
    *,
    vault_factory: Callable[[], SecretVault] = get_secret_vault,
    secret_factory: Callable[[], str] | None = None,
) -> KmsReadinessResult:
    preflight = run_kms_preflight(vault_factory=vault_factory)
    if preflight.status == KMS_FAILED_STATUS:
        return preflight
    return KmsReadinessResult(
        status=KMS_FAILED_STATUS,
        stage="live_smoke",
        provider=preflight.provider,
        key_id_hint=preflight.key_id_hint,
        checks=[
            *preflight.checks,
            KmsReadinessCheck(
                name="live_smoke",
                status="failed",
                message="Live KMS smoke is guarded until Task 2.",
            ),
        ],
        error_code="kms_error",
        message="Live KMS smoke is guarded until Task 2.",
    )


def _configured_provider() -> str:
    return os.getenv(SECRET_VAULT_PROVIDER_ENV, "dev").strip().lower()


def _configured_key_id() -> str:
    return os.getenv(SECRET_VAULT_KMS_KEY_ID_ENV, "").strip()


def _generated_secret() -> str:
    return f"ai-scdc-kms-smoke-{secrets.token_urlsafe(24)}"


def _key_id_hint(key_id: str) -> str | None:
    if key_id == "":
        return None
    return f"sha256:{sha256(key_id.encode('utf-8')).hexdigest()[:12]}"


def _fingerprint_hint(fingerprint: str | None) -> str | None:
    if not fingerprint:
        return None
    return fingerprint[:19]


def _failure(
    *,
    stage: Literal["preflight", "live_smoke"],
    provider: str | None,
    key_id: str,
    checks: list[KmsReadinessCheck],
    error_code: str,
    message: str | None,
) -> KmsReadinessResult:
    return KmsReadinessResult(
        status=KMS_FAILED_STATUS,
        stage=stage,
        provider=provider,
        key_id_hint=_key_id_hint(key_id),
        checks=checks,
        error_code=error_code,
        message=_redact_message(message or error_code, key_id=key_id),
    )


def _redact_message(
    message: str,
    *,
    key_id: str,
    extra_sensitive_values: list[str] | None = None,
) -> str:
    redacted = message
    for env_name in _SECRET_ENV_NAMES:
        value = os.getenv(env_name, "").strip()
        if value:
            redacted = redacted.replace(value, "[redacted]")
    if key_id:
        redacted = redacted.replace(key_id, _key_id_hint(key_id) or "[redacted-key]")
    for value in extra_sensitive_values or []:
        if value:
            redacted = redacted.replace(value, "[redacted]")
    return redacted
```

This intentionally leaves `run_kms_live_smoke()` failing so Task 2 can add the
live implementation using a red-green test cycle.

- [ ] **Step 4: Run preflight tests and verify they pass**

Run:

```bash
pytest apps/api/tests/test_kms_readiness.py -q -k "preflight"
```

Expected: PASS for the preflight tests, while live/CLI tests are not present yet.

- [ ] **Step 5: Commit the preflight service**

Run:

```bash
git add apps/api/app/ai_company_api/services/kms_readiness.py apps/api/tests/test_kms_readiness.py
git commit -m "feat: add kms readiness preflight"
```

---

### Task 2: KMS Live Smoke Service

**Files:**
- Modify: `apps/api/app/ai_company_api/services/kms_readiness.py`
- Modify: `apps/api/tests/test_kms_readiness.py`

- [ ] **Step 1: Extend the fake KMS client for real round trips**

In `apps/api/tests/test_kms_readiness.py`, replace `FakeKmsClient` with:

```python
class FakeKmsClient:
    def __init__(
        self,
        *,
        fail_on: str | None = None,
        mismatch_open: bool = False,
    ) -> None:
        self.fail_on = fail_on
        self.mismatch_open = mismatch_open
        self.encrypt_requests: list[tuple[str, str]] = []
        self.decrypt_requests: list[tuple[str, str]] = []
        self.delete_requests: list[tuple[str, str]] = []

    def encrypt(self, key_id: str, plaintext: str) -> str:
        if self.fail_on == "encrypt":
            raise RuntimeError("KMS encrypt failed for ak-secret-value")
        self.encrypt_requests.append((key_id, plaintext))
        return f"fake-kms:{key_id}:{plaintext}"

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        if self.fail_on == "decrypt":
            raise RuntimeError("KMS decrypt failed for ak-secret-value")
        self.decrypt_requests.append((key_id, ciphertext))
        if self.mismatch_open:
            return "different-opened-secret"
        return ciphertext.removeprefix(f"fake-kms:{key_id}:")

    def delete(self, key_id: str, ciphertext: str) -> None:
        if self.fail_on == "delete":
            raise RuntimeError("KMS delete failed for ak-secret-value")
        self.delete_requests.append((key_id, ciphertext))
```

- [ ] **Step 2: Add failing live-smoke tests**

In `apps/api/tests/test_kms_readiness.py`, update the service import to include:

```python
from ai_company_api.services.kms_readiness import (
    run_kms_live_smoke,
    run_kms_preflight,
)
```

Append these tests:

```python
def test_kms_live_smoke_uses_secret_vault_protocol_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient()
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_live_smoke(secret_factory=lambda: "temporary-smoke-secret")
    finally:
        set_kms_client_for_tests(None)

    serialized = result_json(result)
    check_names = [check.name for check in result.checks]
    assert result.status == "passed"
    assert result.stage == "live_smoke"
    assert result.provider == "aliyun_kms"
    assert result.error_code is None
    assert check_names == [
        "provider",
        "key_id",
        "configuration",
        "seal",
        "open",
        "fingerprint",
        "delete",
    ]
    assert fake_kms.encrypt_requests == [
        ("kms-key-production", "temporary-smoke-secret")
    ]
    assert len(fake_kms.decrypt_requests) == 2
    assert fake_kms.delete_requests == [
        (
            "kms-key-production",
            "fake-kms:kms-key-production:temporary-smoke-secret",
        )
    ]
    assert "temporary-smoke-secret" not in serialized
    assert "fake-kms:kms-key-production" not in serialized
    assert "kms-key-production" not in serialized
    assert "ak-secret-value" not in serialized
    assert result.fingerprint_hint is not None


def test_kms_live_smoke_returns_kms_error_without_secret_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient(fail_on="encrypt")
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_live_smoke(secret_factory=lambda: "temporary-smoke-secret")
    finally:
        set_kms_client_for_tests(None)

    serialized = result_json(result)
    assert result.status == "failed"
    assert result.stage == "live_smoke"
    assert result.error_code == "kms_error"
    assert "ak-secret-value" not in serialized
    assert "temporary-smoke-secret" not in serialized


def test_kms_live_smoke_returns_roundtrip_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient(mismatch_open=True)
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_live_smoke(secret_factory=lambda: "temporary-smoke-secret")
    finally:
        set_kms_client_for_tests(None)

    serialized = result_json(result)
    assert result.status == "failed"
    assert result.stage == "live_smoke"
    assert result.error_code == "roundtrip_mismatch"
    assert "temporary-smoke-secret" not in serialized
    assert "different-opened-secret" not in serialized
```

- [ ] **Step 3: Run live-smoke tests and verify they fail against the stub**

Run:

```bash
pytest apps/api/tests/test_kms_readiness.py -q -k "live_smoke"
```

Expected: FAIL because `run_kms_live_smoke()` returns the temporary
`kms_error` stub result.

- [ ] **Step 4: Implement live smoke**

In `apps/api/app/ai_company_api/services/kms_readiness.py`, replace the body of
`run_kms_live_smoke()` with:

```python
def run_kms_live_smoke(
    *,
    vault_factory: Callable[[], SecretVault] = get_secret_vault,
    secret_factory: Callable[[], str] | None = None,
) -> KmsReadinessResult:
    preflight = run_kms_preflight(vault_factory=vault_factory)
    if preflight.status == KMS_FAILED_STATUS:
        return preflight

    key_id = _configured_key_id()
    temporary_secret = (secret_factory or _generated_secret)()
    sensitive_values = [temporary_secret]
    checks = list(preflight.checks)
    encrypted_secret = ""
    fingerprint = None

    try:
        vault = vault_factory()

        sealed = vault.seal(temporary_secret)
        encrypted_secret = sealed.encrypted_secret
        sensitive_values.append(encrypted_secret)
        checks.append(KmsReadinessCheck(name="seal", status="passed"))

        opened = vault.open(encrypted_secret)
        sensitive_values.append(opened)
        if opened != temporary_secret:
            checks.append(
                KmsReadinessCheck(
                    name="open",
                    status="failed",
                    message="KMS live smoke opened a different secret value.",
                )
            )
            return _failure(
                stage="live_smoke",
                provider=preflight.provider,
                key_id=key_id,
                checks=checks,
                error_code="roundtrip_mismatch",
                message="KMS live smoke opened a different secret value.",
            )
        checks.append(KmsReadinessCheck(name="open", status="passed"))

        fingerprint = vault.fingerprint(encrypted_secret)
        sensitive_values.append(fingerprint)
        checks.append(KmsReadinessCheck(name="fingerprint", status="passed"))

        vault.delete(encrypted_secret)
        checks.append(KmsReadinessCheck(name="delete", status="passed"))
    except Exception as exc:
        checks.append(
            KmsReadinessCheck(
                name="live_smoke",
                status="failed",
                message=_redact_message(
                    str(exc) or exc.__class__.__name__,
                    key_id=key_id,
                    extra_sensitive_values=sensitive_values,
                ),
            )
        )
        return _failure(
            stage="live_smoke",
            provider=preflight.provider,
            key_id=key_id,
            checks=checks,
            error_code="kms_error",
            message=checks[-1].message,
        )

    return KmsReadinessResult(
        status=KMS_PASSED_STATUS,
        stage="live_smoke",
        provider=preflight.provider,
        key_id_hint=preflight.key_id_hint,
        fingerprint_hint=_fingerprint_hint(fingerprint),
        checks=checks,
    )
```

Also update `_failure()` so it can redact extra values:

```python
def _failure(
    *,
    stage: Literal["preflight", "live_smoke"],
    provider: str | None,
    key_id: str,
    checks: list[KmsReadinessCheck],
    error_code: str,
    message: str | None,
    extra_sensitive_values: list[str] | None = None,
) -> KmsReadinessResult:
    return KmsReadinessResult(
        status=KMS_FAILED_STATUS,
        stage=stage,
        provider=provider,
        key_id_hint=_key_id_hint(key_id),
        checks=checks,
        error_code=error_code,
        message=_redact_message(
            message or error_code,
            key_id=key_id,
            extra_sensitive_values=extra_sensitive_values,
        ),
    )
```

- [ ] **Step 5: Run live and preflight tests**

Run:

```bash
pytest apps/api/tests/test_kms_readiness.py -q
```

Expected: PASS for the preflight and live-smoke service tests.

- [ ] **Step 6: Commit live-smoke service**

Run:

```bash
git add apps/api/app/ai_company_api/services/kms_readiness.py apps/api/tests/test_kms_readiness.py
git commit -m "feat: add kms live smoke readiness"
```

---

### Task 3: KMS Readiness CLI

**Files:**
- Create: `apps/api/app/ai_company_api/tools/__init__.py`
- Create: `apps/api/app/ai_company_api/tools/kms_readiness.py`
- Modify: `apps/api/tests/test_kms_readiness.py`

- [ ] **Step 1: Add failing CLI tests**

Append these tests to `apps/api/tests/test_kms_readiness.py`:

```python
def test_kms_readiness_cli_preflight_outputs_json_and_exit_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    from ai_company_api.tools import kms_readiness as cli

    exit_code = cli.main([])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["status"] == "ready_for_live_smoke"
    assert output["stage"] == "preflight"
    assert "kms-key-production" not in json.dumps(output)


def test_kms_readiness_cli_live_outputs_json_and_exit_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient()
    set_kms_client_for_tests(fake_kms)
    try:
        from ai_company_api.tools import kms_readiness as cli

        exit_code = cli.main(["--live"], secret_factory=lambda: "temporary-smoke-secret")
    finally:
        set_kms_client_for_tests(None)

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["status"] == "passed"
    assert output["stage"] == "live_smoke"
    assert "temporary-smoke-secret" not in json.dumps(output)


def test_kms_readiness_cli_failed_readiness_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clear_kms_env(monkeypatch)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "dev")
    from ai_company_api.tools import kms_readiness as cli

    exit_code = cli.main([])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert output["status"] == "failed"
    assert output["error_code"] == "configuration_error"
```

- [ ] **Step 2: Run CLI tests and verify they fail for the missing package**

Run:

```bash
pytest apps/api/tests/test_kms_readiness.py -q -k "cli"
```

Expected: FAIL with:

```text
ModuleNotFoundError: No module named 'ai_company_api.tools'
```

- [ ] **Step 3: Create the tools package marker**

Create `apps/api/app/ai_company_api/tools/__init__.py` with:

```python
"""Local operator tools for the AI Company API package."""
```

- [ ] **Step 4: Create the CLI module**

Create `apps/api/app/ai_company_api/tools/kms_readiness.py` with:

```python
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import json

from ai_company_api.services.kms_readiness import run_kms_readiness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run AI-SCDC KMS readiness preflight or explicit live smoke."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Perform a live SecretVault seal/open/fingerprint/delete smoke.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    secret_factory: Callable[[], str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    result = run_kms_readiness(live=args.live, secret_factory=secret_factory)
    print(json.dumps(result.model_dump(), indent=2, sort_keys=True))
    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run CLI tests and focused readiness tests**

Run:

```bash
pytest apps/api/tests/test_kms_readiness.py -q
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_kms_readiness.py
```

Expected: PASS for the readiness tests and compileall.

- [ ] **Step 6: Commit CLI**

Run:

```bash
git add apps/api/app/ai_company_api/tools/__init__.py apps/api/app/ai_company_api/tools/kms_readiness.py apps/api/tests/test_kms_readiness.py
git commit -m "feat: add kms readiness cli"
```

---

### Task 4: KMS Readiness Documentation And Status

**Files:**
- Modify: `README.md`
- Modify: `docs/operations/aliyun-operational-runbook.md`
- Modify: `docs/operations/aliyun-ram-policies.md`
- Modify: `STATUS.md`
- Modify: `docs/superpowers/status.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update README KMS operations text**

In `README.md`, extend the Aliyun operations section after the paragraph that
mentions `AI_SCDC_SECRET_VAULT_PROVIDER=aliyun_kms` with:

```markdown
KMS readiness can be checked locally without exposing raw credentials. Run
preflight first; it validates configuration and does not call Aliyun KMS:

```powershell
$env:AI_SCDC_SECRET_VAULT_PROVIDER = "aliyun_kms"
$env:AI_SCDC_KMS_KEY_ID = "<kms-key-id>"
$env:AI_SCDC_ALIYUN_REGION_ID = "cn-hangzhou"
$env:AI_SCDC_ALIYUN_ACCESS_KEY_ID = "<set locally>"
$env:AI_SCDC_ALIYUN_ACCESS_KEY_SECRET = "<set locally>"
python -m ai_company_api.tools.kms_readiness
```

Only after reviewing RAM scope and running in the target account, execute the
live smoke explicitly:

```powershell
python -m ai_company_api.tools.kms_readiness --live
```

The output is JSON with redacted provider/key metadata and step status. It must
not contain plaintext secrets, ciphertext blobs, access-key secrets, callback
tokens, queue receipts, signed URLs, or the full KMS key id. Automated tests use
fake KMS clients; a passing test run does not prove the target Aliyun account
has been live-smoked.
```

- [ ] **Step 2: Update the Aliyun operational runbook**

In `docs/operations/aliyun-operational-runbook.md`, replace the final paragraph
of the `KMS Boundary` section with:

```markdown
Before beta traffic, operators must run the local KMS readiness command in the
target account. First run preflight:

```powershell
python -m ai_company_api.tools.kms_readiness
```

Preflight validates required provider, key, region, and access-key settings
without calling Aliyun KMS. After RAM policy scope and KMS key state are
reviewed, run the live smoke explicitly:

```powershell
python -m ai_company_api.tools.kms_readiness --live
```

The live smoke generates a temporary secret inside the process and verifies the
existing `SecretVault` protocol with `seal`, `open`, `fingerprint`, and
`delete`. Save only the redacted JSON result in operational records. Do not
paste plaintext access keys, callback tokens, queue receipts, signed URLs,
GitHub tokens, raw KMS plaintext, ciphertext blobs, or the full KMS key id into
runbooks or incident notes.
```

- [ ] **Step 3: Update RAM policy guidance**

In `docs/operations/aliyun-ram-policies.md`, add this paragraph after the KMS
policy JSON block:

```markdown
After attaching the policy, run `python -m ai_company_api.tools.kms_readiness`
for preflight in the target environment, then run
`python -m ai_company_api.tools.kms_readiness --live` only when the operator is
ready to create live KMS request audit events. The readiness output is redacted
and should be kept with deployment evidence; it is not a substitute for RAM
policy review.
```

- [ ] **Step 4: Update `STATUS.md`**

In `STATUS.md`, update the Phase 13B KMS summary paragraph to append:

```markdown
A local `python -m ai_company_api.tools.kms_readiness` command now provides
redacted KMS configuration preflight and explicit operator-run live smoke for
the configured SecretVault provider.
```

In the Non-Goals section, replace:

```markdown
cloud KMS credential provisioning, cloud KMS smoke validation, production
auth/IdP integration,
```

with:

```markdown
automated cloud KMS credential provisioning, recorded target-account KMS smoke
evidence, production auth/IdP integration,
```

In the verification list, add:

```markdown
- `pytest apps/api/tests/test_kms_readiness.py -q`: KMS readiness preflight,
  live-smoke, and CLI tests passed without contacting Aliyun.
```

- [ ] **Step 5: Update Superpowers status**

In `docs/superpowers/status.md`, update the Phase 13B in-progress KMS bullet to
include:

```markdown
- Local KMS readiness command for redacted preflight and explicit live-smoke
  validation through the configured SecretVault provider.
```

In Known Limits, replace wording that says cloud KMS smoke validation is still
absent with wording that says:

```markdown
The local readiness command exists, but operators still must run and retain
target-account live-smoke evidence before beta traffic.
```

In Recommended Next Phase, replace `cloud KMS credential provisioning, live
cloud KMS smoke validation` with:

```markdown
cloud KMS credential provisioning, retained target-account KMS smoke evidence,
```

- [ ] **Step 6: Update architecture boundary**

In `docs/architecture.md`, in the Phase 13B boundary section, add this
paragraph after the existing KMS adapter paragraph:

```markdown
Phase 13B also has a local KMS readiness command. The command runs redacted
configuration preflight by default and performs a live SecretVault
seal/open/fingerprint/delete smoke only when `--live` is passed. This provides a
safe operator-run validation path without exposing KMS smoke through public
HTTP routes.
```

In the Phase 13B remaining-work paragraph, replace:

```markdown
cloud KMS credential provisioning and cloud KMS smoke validation
```

with:

```markdown
automated cloud KMS credential provisioning, retained target-account KMS smoke
evidence
```

- [ ] **Step 7: Run documentation checks**

Run:

```bash
rg -n "purely manual|No real KMS SDK|cloud KMS smoke validation is still absent" README.md STATUS.md docs/architecture.md docs/superpowers/status.md docs/operations
git diff --check
```

Expected: `rg` returns no matches and `git diff --check` passes. Existing Git
LF-to-CRLF warnings are acceptable if no whitespace error lines are present.

- [ ] **Step 8: Commit documentation updates**

Run:

```bash
git add README.md STATUS.md docs/architecture.md docs/superpowers/status.md docs/operations/aliyun-ram-policies.md docs/operations/aliyun-operational-runbook.md
git commit -m "docs: add kms readiness operator path"
```

---

### Task 5: Final Verification

**Files:**
- Verify: full repository state

- [ ] **Step 1: Run focused KMS tests**

Run:

```bash
pytest apps/api/tests/test_kms_readiness.py apps/api/tests/test_aliyun_kms.py apps/api/tests/test_secret_access_audit.py -q
```

Expected: PASS. Existing Starlette/httpx deprecation warnings are acceptable.

- [ ] **Step 2: Run compileall**

Run:

```bash
python -m compileall -q apps/api/app/ai_company_api apps/api/tests/test_kms_readiness.py apps/api/tests/test_secret_access_audit.py apps/api/tests/test_aliyun_kms.py
```

Expected: PASS with no output.

- [ ] **Step 3: Run API tests**

Run:

```bash
pytest apps/api/tests -q
```

Expected: PASS. Existing Starlette/httpx deprecation warnings are acceptable.

- [ ] **Step 4: Run root JavaScript tests**

Run:

```bash
pnpm test:js
```

Expected: PASS for `packages/agent-protocol` and `apps/desktop`.

- [ ] **Step 5: Run typecheck**

Run:

```bash
pnpm typecheck
```

Expected: PASS for `apps/desktop` and `packages/agent-protocol`.

- [ ] **Step 6: Check whitespace and final state**

Run:

```bash
git diff --check
git status --short --branch
git log --oneline -8
```

Expected: `git diff --check` passes, working tree is clean, and the readiness
service, CLI, docs, and plan commits are at the top of the branch.

---

## Self-Review Checklist

- Spec coverage: Task 1 covers redacted preflight. Task 2 covers explicit live
  smoke through the existing SecretVault protocol. Task 3 covers local CLI and
  exit codes. Task 4 covers operator docs and status/architecture wording.
  Task 5 covers final verification.
- Placeholder scan: This plan contains concrete file paths, code snippets,
  commands, and expected outcomes. It contains no unspecified sections.
- Type consistency: `KmsReadinessResult.status` values match the CLI exit-code
  logic and tests. `run_kms_readiness`, `run_kms_preflight`, and
  `run_kms_live_smoke` use the same `SecretVault` protocol already defined in
  `secret_vault.py`.
