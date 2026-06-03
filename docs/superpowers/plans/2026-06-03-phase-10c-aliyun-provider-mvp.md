# Phase 10C Aliyun Provider MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the first concrete Aliyun execution provider stack behind the Phase 10B queue, storage, and runtime contracts.

**Architecture:** Keep the existing cloud-run lifecycle and worker lease API as the source of truth. Add Aliyun MNS, OSS, and ECI adapters behind provider interfaces, route large worker artifacts through the API so remote containers do not receive broad cloud credentials, and keep all automated tests on fake clients with no Aliyun network calls.

**Tech Stack:** FastAPI, SQLModel, pytest, Aliyun MNS Python SDK (`aliyun-mns-sdk`), Aliyun OSS Python SDK V2 (`alibabacloud-oss-v2`), Aliyun ECI Python SDK (`alibabacloud_eci20180808`), Docker/ACR for the remote worker image.

---

## Scope Check

This plan implements one bounded sub-project: Aliyun provider MVP. It does not add live log streaming, SLS, ACK/Kubernetes, billing, model-backed reviewer/debugger agents, automatic PR creation, or automatic merge.

## File Structure

- Create `apps/api/app/ai_company_api/services/aliyun_config.py`
  - Reads Aliyun environment variables, validates required groups, and returns safe error messages.
- Create `apps/api/app/ai_company_api/services/aliyun_clients.py`
  - Defines fake-friendly client protocols and default SDK-backed clients for MNS, OSS, and ECI.
- Modify `apps/api/app/ai_company_api/services/cloud_queue_providers.py`
  - Adds `aliyun_mns`, queue enqueue result types, and configuration validation.
- Modify `apps/api/app/ai_company_api/services/object_storage.py`
  - Adds `aliyun_oss` provider with `oss://` refs and fake-client tests.
- Modify `apps/api/app/ai_company_api/services/remote_runtime.py`
  - Adds `aliyun_eci` runtime provider and ECI submission request building.
- Modify `apps/api/app/ai_company_api/services/cloud_runner.py`
  - Calls queue enqueue providers, accepts `oss://` artifact refs, adds worker artifact upload, and supports claiming a specific cloud run for ECI workers.
- Modify `apps/api/app/ai_company_api/schemas/api.py`
  - Adds optional `cloud_run_id` to lease claims and adds worker artifact upload schemas.
- Modify `apps/api/app/ai_company_api/api/routes.py`
  - Adds worker artifact upload route.
- Create `apps/api/app/ai_company_api/services/remote_worker.py`
  - Implements the first deterministic ECI worker entry point using the API callback contract.
- Create `apps/api/Dockerfile.remote-worker`
  - Builds the remote worker image that will be pushed to ACR.
- Modify `apps/api/pyproject.toml`
  - Adds Aliyun SDK dependencies.
- Create `apps/api/tests/test_aliyun_config.py`
- Create `apps/api/tests/test_aliyun_clients.py`
- Extend `apps/api/tests/test_cloud_object_storage.py`
- Extend `apps/api/tests/test_cloud_run_api.py`
- Create `apps/api/tests/test_remote_worker.py`
- Modify `README.md`
  - Adds Aliyun Phase 10C smoke and cleanup instructions.
- Modify `docs/architecture.md` and `docs/superpowers/status.md`
  - Mark Phase 10C implemented only after verification passes.

---

### Task 1: Aliyun Configuration

**Files:**
- Create: `apps/api/app/ai_company_api/services/aliyun_config.py`
- Create: `apps/api/tests/test_aliyun_config.py`

- [ ] **Step 1: Write failing configuration tests**

Create `apps/api/tests/test_aliyun_config.py`:

```python
import pytest

from ai_company_api.services.aliyun_config import (
    AliyunConfigurationError,
    load_aliyun_settings,
    require_aliyun_settings,
)


def test_load_aliyun_settings_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak-id")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "ak-secret")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_MNS_ENDPOINT",
        "https://123456.mns.cn-hangzhou.aliyuncs.com",
    )
    monkeypatch.setenv("AI_SCDC_ALIYUN_MNS_QUEUE_NAME", "ai-scdc-cloud-runs-dev")
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_BUCKET", "ai-scdc-dev-artifacts")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_VSWITCH_ID", "vsw-demo")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID", "sg-demo")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ECI_IMAGE",
        "registry.cn-hangzhou.aliyuncs.com/ai-scdc/remote-worker:dev",
    )
    monkeypatch.setenv("AI_SCDC_API_PUBLIC_BASE_URL", "https://api.example.test")

    settings = load_aliyun_settings()

    assert settings.region_id == "cn-hangzhou"
    assert settings.access_key_id == "ak-id"
    assert settings.access_key_secret == "ak-secret"
    assert settings.mns_queue_name == "ai-scdc-cloud-runs-dev"
    assert settings.oss_bucket == "ai-scdc-dev-artifacts"
    assert settings.eci_cpu == 1.0
    assert settings.eci_memory_gb == 2.0
    assert settings.eci_container_group_prefix == "ai-scdc-run"
    assert settings.oss_prefix == "ai-scdc/dev"


def test_require_aliyun_settings_reports_missing_names_without_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "super-secret-value")

    with pytest.raises(AliyunConfigurationError) as exc_info:
        require_aliyun_settings(
            provider_name="aliyun_oss",
            required_names=("region_id", "access_key_id", "access_key_secret", "oss_bucket"),
        )

    message = str(exc_info.value)
    assert "Aliyun provider aliyun_oss is missing configuration" in message
    assert "AI_SCDC_ALIYUN_REGION_ID" in message
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_ID" in message
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in message
    assert "super-secret-value" not in message
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest apps/api/tests/test_aliyun_config.py -v
```

Expected: FAIL because `ai_company_api.services.aliyun_config` does not exist.

- [ ] **Step 3: Implement Aliyun configuration**

Create `apps/api/app/ai_company_api/services/aliyun_config.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import os


class AliyunConfigurationError(Exception):
    pass


_ENV_BY_FIELD = {
    "region_id": "AI_SCDC_ALIYUN_REGION_ID",
    "access_key_id": "AI_SCDC_ALIYUN_ACCESS_KEY_ID",
    "access_key_secret": "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
    "mns_endpoint": "AI_SCDC_ALIYUN_MNS_ENDPOINT",
    "mns_queue_name": "AI_SCDC_ALIYUN_MNS_QUEUE_NAME",
    "oss_endpoint": "AI_SCDC_ALIYUN_OSS_ENDPOINT",
    "oss_bucket": "AI_SCDC_ALIYUN_OSS_BUCKET",
    "eci_vswitch_id": "AI_SCDC_ALIYUN_ECI_VSWITCH_ID",
    "eci_security_group_id": "AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID",
    "eci_image": "AI_SCDC_ALIYUN_ECI_IMAGE",
    "api_public_base_url": "AI_SCDC_API_PUBLIC_BASE_URL",
}

_SECRET_FIELDS = {"access_key_secret"}


@dataclass(frozen=True)
class AliyunSettings:
    region_id: str | None
    access_key_id: str | None
    access_key_secret: str | None
    mns_endpoint: str | None
    mns_queue_name: str | None
    oss_endpoint: str | None
    oss_bucket: str | None
    eci_vswitch_id: str | None
    eci_security_group_id: str | None
    eci_image: str | None
    api_public_base_url: str | None
    eci_cpu: float = 1.0
    eci_memory_gb: float = 2.0
    eci_container_group_prefix: str = "ai-scdc-run"
    oss_prefix: str = "ai-scdc/dev"


def load_aliyun_settings() -> AliyunSettings:
    return AliyunSettings(
        region_id=_env("AI_SCDC_ALIYUN_REGION_ID"),
        access_key_id=_env("AI_SCDC_ALIYUN_ACCESS_KEY_ID"),
        access_key_secret=_env("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET"),
        mns_endpoint=_env("AI_SCDC_ALIYUN_MNS_ENDPOINT"),
        mns_queue_name=_env("AI_SCDC_ALIYUN_MNS_QUEUE_NAME"),
        oss_endpoint=_env("AI_SCDC_ALIYUN_OSS_ENDPOINT"),
        oss_bucket=_env("AI_SCDC_ALIYUN_OSS_BUCKET"),
        eci_vswitch_id=_env("AI_SCDC_ALIYUN_ECI_VSWITCH_ID"),
        eci_security_group_id=_env("AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID"),
        eci_image=_env("AI_SCDC_ALIYUN_ECI_IMAGE"),
        api_public_base_url=_env("AI_SCDC_API_PUBLIC_BASE_URL"),
        eci_cpu=_float_env("AI_SCDC_ALIYUN_ECI_CPU", 1.0),
        eci_memory_gb=_float_env("AI_SCDC_ALIYUN_ECI_MEMORY_GB", 2.0),
        eci_container_group_prefix=_env(
            "AI_SCDC_ALIYUN_ECI_CONTAINER_GROUP_PREFIX"
        )
        or "ai-scdc-run",
        oss_prefix=_env("AI_SCDC_ALIYUN_OSS_PREFIX") or "ai-scdc/dev",
    )


def require_aliyun_settings(
    *,
    provider_name: str,
    required_names: Sequence[str],
    settings: AliyunSettings | None = None,
) -> AliyunSettings:
    resolved = settings or load_aliyun_settings()
    missing = [
        name
        for name in required_names
        if not getattr(resolved, name)
    ]
    if missing:
        safe_names = [
            _ENV_BY_FIELD[name]
            for name in missing
            if name not in _SECRET_FIELDS
        ]
        if any(name in _SECRET_FIELDS for name in missing):
            safe_names.append("required secret environment variable")
        joined = ", ".join(safe_names)
        raise AliyunConfigurationError(
            f"Aliyun provider {provider_name} is missing configuration: {joined}"
        )
    return resolved


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _float_env(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    return float(value)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
pytest apps/api/tests/test_aliyun_config.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add apps/api/app/ai_company_api/services/aliyun_config.py apps/api/tests/test_aliyun_config.py
git commit -m "feat: add aliyun provider configuration"
```

---

### Task 2: Aliyun Client Protocols and SDK Dependencies

**Files:**
- Create: `apps/api/app/ai_company_api/services/aliyun_clients.py`
- Create: `apps/api/tests/test_aliyun_clients.py`
- Modify: `apps/api/pyproject.toml`

- [ ] **Step 1: Write failing client protocol tests**

Create `apps/api/tests/test_aliyun_clients.py`:

```python
from ai_company_api.services.aliyun_clients import (
    AliyunClientBundle,
    AliyunEciCreateContainerGroupRequest,
    AliyunMnsSendMessageRequest,
    AliyunOssPutObjectRequest,
    get_aliyun_client_bundle,
    set_aliyun_client_bundle_for_tests,
)


class FakeMnsClient:
    def send_message(self, request: AliyunMnsSendMessageRequest):
        return {"message_id": f"msg-{request.cloud_run_id}"}


class FakeOssClient:
    def put_object(self, request: AliyunOssPutObjectRequest) -> None:
        return None

    def get_object_text(self, bucket: str, object_key: str) -> str:
        return f"{bucket}/{object_key}"


class FakeEciClient:
    def create_container_group(self, request: AliyunEciCreateContainerGroupRequest):
        return {"container_group_id": f"eci-{request.cloud_run_id}"}


def test_client_bundle_override_is_returned_for_tests() -> None:
    bundle = AliyunClientBundle(
        mns=FakeMnsClient(),
        oss=FakeOssClient(),
        eci=FakeEciClient(),
    )
    set_aliyun_client_bundle_for_tests(bundle)
    try:
        assert get_aliyun_client_bundle() is bundle
    finally:
        set_aliyun_client_bundle_for_tests(None)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest apps/api/tests/test_aliyun_clients.py -v
```

Expected: FAIL because `aliyun_clients.py` does not exist.

- [ ] **Step 3: Add SDK dependencies**

Modify `apps/api/pyproject.toml` dependencies:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "pydantic>=2.10.0",
    "sqlmodel>=0.0.22",
    "uvicorn>=0.32.0",
    "aliyun-mns-sdk>=1.1.0",
    "alibabacloud-oss-v2>=1.0.0",
    "alibabacloud_eci20180808>=1.2.0",
]
```

- [ ] **Step 4: Implement client protocols and test override**

Create `apps/api/app/ai_company_api/services/aliyun_clients.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_company_api.services.aliyun_config import AliyunSettings, load_aliyun_settings


@dataclass(frozen=True)
class AliyunMnsSendMessageRequest:
    queue_name: str
    cloud_run_id: str
    workspace_id: str
    project_id: str
    task_id: str
    body: str


@dataclass(frozen=True)
class AliyunOssPutObjectRequest:
    bucket: str
    object_key: str
    content: bytes
    content_type: str


@dataclass(frozen=True)
class AliyunEciCreateContainerGroupRequest:
    region_id: str
    cloud_run_id: str
    container_group_name: str
    image: str
    vswitch_id: str
    security_group_id: str
    cpu: float
    memory_gb: float
    environment: dict[str, str]


class AliyunMnsClient(Protocol):
    def send_message(self, request: AliyunMnsSendMessageRequest) -> dict:
        raise NotImplementedError


class AliyunOssClient(Protocol):
    def put_object(self, request: AliyunOssPutObjectRequest) -> None:
        raise NotImplementedError

    def get_object_text(self, bucket: str, object_key: str) -> str:
        raise NotImplementedError


class AliyunEciClient(Protocol):
    def create_container_group(
        self,
        request: AliyunEciCreateContainerGroupRequest,
    ) -> dict:
        raise NotImplementedError


@dataclass(frozen=True)
class AliyunClientBundle:
    mns: AliyunMnsClient
    oss: AliyunOssClient
    eci: AliyunEciClient


_CLIENT_BUNDLE_OVERRIDE: AliyunClientBundle | None = None


def set_aliyun_client_bundle_for_tests(bundle: AliyunClientBundle | None) -> None:
    global _CLIENT_BUNDLE_OVERRIDE
    _CLIENT_BUNDLE_OVERRIDE = bundle


def get_aliyun_client_bundle(
    settings: AliyunSettings | None = None,
) -> AliyunClientBundle:
    if _CLIENT_BUNDLE_OVERRIDE is not None:
        return _CLIENT_BUNDLE_OVERRIDE
    resolved = settings or load_aliyun_settings()
    return AliyunClientBundle(
        mns=SdkAliyunMnsClient(resolved),
        oss=SdkAliyunOssClient(resolved),
        eci=SdkAliyunEciClient(resolved),
    )


class SdkAliyunMnsClient:
    def __init__(self, settings: AliyunSettings) -> None:
        self._settings = settings

    def send_message(self, request: AliyunMnsSendMessageRequest) -> dict:
        from mns.account import Account
        from mns.queue import Message

        account = Account(
            self._settings.mns_endpoint,
            self._settings.access_key_id,
            self._settings.access_key_secret,
        )
        queue = account.get_queue(request.queue_name)
        result = queue.send_message(Message(request.body))
        message_id = getattr(result, "message_id", None) or result.message_id
        return {"message_id": message_id}


class SdkAliyunOssClient:
    def __init__(self, settings: AliyunSettings) -> None:
        self._settings = settings

    def put_object(self, request: AliyunOssPutObjectRequest) -> None:
        import alibabacloud_oss_v2 as oss

        credentials_provider = oss.credentials.StaticCredentialsProvider(
            self._settings.access_key_id,
            self._settings.access_key_secret,
        )
        cfg = oss.config.load_default()
        cfg.credentials_provider = credentials_provider
        cfg.region = self._settings.region_id
        client = oss.Client(cfg)
        client.put_object(
            oss.PutObjectRequest(
                bucket=request.bucket,
                key=request.object_key,
                body=request.content,
                content_type=request.content_type,
            )
        )

    def get_object_text(self, bucket: str, object_key: str) -> str:
        import alibabacloud_oss_v2 as oss

        credentials_provider = oss.credentials.StaticCredentialsProvider(
            self._settings.access_key_id,
            self._settings.access_key_secret,
        )
        cfg = oss.config.load_default()
        cfg.credentials_provider = credentials_provider
        cfg.region = self._settings.region_id
        client = oss.Client(cfg)
        response = client.get_object(oss.GetObjectRequest(bucket=bucket, key=object_key))
        return response.body.read().decode("utf-8")


class SdkAliyunEciClient:
    def __init__(self, settings: AliyunSettings) -> None:
        self._settings = settings

    def create_container_group(
        self,
        request: AliyunEciCreateContainerGroupRequest,
    ) -> dict:
        from alibabacloud_tea_openapi import models as open_api_models
        from alibabacloud_eci20180808.client import Client
        from alibabacloud_eci20180808 import models as eci_models

        config = open_api_models.Config(
            access_key_id=self._settings.access_key_id,
            access_key_secret=self._settings.access_key_secret,
            region_id=request.region_id,
        )
        client = Client(config)
        env = [
            eci_models.CreateContainerGroupRequestContainerEnvironmentVar(
                key=key,
                value=value,
            )
            for key, value in sorted(request.environment.items())
        ]
        container = eci_models.CreateContainerGroupRequestContainer(
            name="remote-worker",
            image=request.image,
            cpu=request.cpu,
            memory=request.memory_gb,
            environment_var=env,
        )
        response = client.create_container_group(
            eci_models.CreateContainerGroupRequest(
                region_id=request.region_id,
                container_group_name=request.container_group_name,
                vswitch_id=request.vswitch_id,
                security_group_id=request.security_group_id,
                container=[container],
            )
        )
        body = getattr(response, "body", None)
        return {"container_group_id": getattr(body, "container_group_id", None)}
```

- [ ] **Step 5: Install dependencies and run test**

Run:

```powershell
pip install -e apps/api
pytest apps/api/tests/test_aliyun_clients.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add apps/api/pyproject.toml apps/api/app/ai_company_api/services/aliyun_clients.py apps/api/tests/test_aliyun_clients.py
git commit -m "feat: add aliyun client adapters"
```

---

### Task 3: Provider Registration and Configuration Validation

**Files:**
- Modify: `apps/api/app/ai_company_api/services/cloud_queue_providers.py`
- Modify: `apps/api/app/ai_company_api/services/object_storage.py`
- Modify: `apps/api/app/ai_company_api/services/remote_runtime.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing provider registration tests**

Append to `apps/api/tests/test_cloud_run_api.py` near the unknown provider tests:

```python
def test_phase_10c_aliyun_provider_names_are_recognized(
    tmp_path: Path,
    monkeypatch,
) -> None:
    response = _post_fake_cloud_run_with_provider_selection(
        tmp_path,
        monkeypatch,
        {
            "queue_provider": "aliyun_mns",
            "storage_provider": "aliyun_oss",
            "runtime_provider": "aliyun_eci",
        },
    )

    assert response.status_code == 400
    assert "Aliyun provider" in response.json()["detail"]
    assert "missing configuration" in response.json()["detail"]


def test_phase_10c_missing_secret_value_is_not_returned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "very-secret-value")

    response = _post_fake_cloud_run_with_provider_selection(
        tmp_path,
        monkeypatch,
        {"storage_provider": "aliyun_oss"},
    )

    assert response.status_code == 400
    assert "very-secret-value" not in response.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "phase_10c_aliyun_provider_names or missing_secret" -v
```

Expected: FAIL because provider names are unknown.

- [ ] **Step 3: Add provider configuration validation hooks**

In `cloud_queue_providers.py`, add request/result dataclasses and provider methods:

```python
from ai_company_api.services.aliyun_config import (
    AliyunConfigurationError,
    require_aliyun_settings,
)


@dataclass(frozen=True)
class CloudQueueEnqueueRequest:
    workspace_id: str
    project_id: str
    task_id: str
    cloud_run_id: str
    queue_provider: str
    runtime_provider: str | None
    storage_provider: str | None


@dataclass(frozen=True)
class CloudQueueEnqueueResult:
    queue_message_id: str | None = None
    queue_receipt: str | None = None
    external_status: str | None = None
```

Update the protocol:

```python
class CloudQueueProvider(Protocol):
    name: str

    def validate_configuration(self) -> None:
        raise NotImplementedError

    def enqueue(self, request: CloudQueueEnqueueRequest) -> CloudQueueEnqueueResult:
        raise NotImplementedError
```

Add `validate_configuration()` and `enqueue()` to `RegisteredCloudQueueProvider`:

```python
@dataclass(frozen=True)
class RegisteredCloudQueueProvider:
    name: str

    def validate_configuration(self) -> None:
        return None

    def enqueue(self, request: CloudQueueEnqueueRequest) -> CloudQueueEnqueueResult:
        external_status = "queued" if self.name == "external_stub" else None
        return CloudQueueEnqueueResult(external_status=external_status)
```

Add the Aliyun provider:

```python
class AliyunMnsQueueProvider:
    name = "aliyun_mns"

    def validate_configuration(self) -> None:
        require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "mns_endpoint",
                "mns_queue_name",
            ),
        )

    def enqueue(self, request: CloudQueueEnqueueRequest) -> CloudQueueEnqueueResult:
        self.validate_configuration()
        return CloudQueueEnqueueResult(external_status="queued")
```

Register it:

```python
_KNOWN_QUEUE_PROVIDERS = {
    "local_db": RegisteredCloudQueueProvider(name="local_db"),
    "external_stub": RegisteredCloudQueueProvider(name="external_stub"),
    "aliyun_mns": AliyunMnsQueueProvider(),
}
```

In `object_storage.py`, add `validate_configuration()` to `LocalInlineObjectStorageProvider`:

```python
def validate_configuration(self) -> None:
    return None
```

Also add this temporary `AliyunOssObjectStorageProvider` class before
`get_object_storage_provider()`. Task 4 replaces the storage methods with real
OSS operations:

```python
class AliyunOssObjectStorageProvider:
    name = "aliyun_oss"

    def validate_configuration(self) -> None:
        require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "oss_endpoint",
                "oss_bucket",
            ),
        )

    def put_text(
        self,
        session: Session,
        write: ObjectStorageWrite,
    ) -> ObjectStorageRef:
        raise ObjectStorageReadError("Aliyun OSS storage operations are not ready")

    def read_text(
        self,
        session: Session,
        ref: ObjectStorageRef,
    ) -> str:
        raise ObjectStorageReadError("Aliyun OSS storage operations are not ready")
```

Update `get_object_storage_provider()`:

```python
def get_object_storage_provider(name: str | None) -> ObjectStorageProvider:
    if name in (None, "local_inline"):
        return LocalInlineObjectStorageProvider()
    if name == "aliyun_oss":
        return AliyunOssObjectStorageProvider()
    raise ObjectStorageProviderNotFound(f"Unknown object storage provider: {name}")
```

In `remote_runtime.py`, add `validate_configuration()` to
`RemoteStubRuntimeProvider`:

```python
def validate_configuration(self) -> None:
    return None
```

Add this temporary `AliyunEciRuntimeProvider` class before the runtime registry.
Task 7 replaces `submit()` with real ECI submission:

```python
class AliyunEciRuntimeProvider:
    name = "aliyun_eci"

    def validate_configuration(self) -> None:
        require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "eci_vswitch_id",
                "eci_security_group_id",
                "eci_image",
                "api_public_base_url",
            ),
        )

    def submit(
        self,
        session: Session,
        submission: RemoteRuntimeSubmission,
    ) -> RemoteRuntimeSubmissionResult:
        raise RemoteRuntimeProviderNotFound("Aliyun ECI runtime submission is not ready")
```

Register it:

```python
_KNOWN_RUNTIME_PROVIDERS = {
    "remote_stub": RemoteStubRuntimeProvider(),
    "aliyun_eci": AliyunEciRuntimeProvider(),
}
```

- [ ] **Step 4: Validate provider configuration during cloud-run creation**

In `cloud_runner._validate_cloud_run_provider_selection()`, replace the validation body with:

```python
queue_provider = get_cloud_queue_provider(data.queue_provider)
storage_provider = (
    get_object_storage_provider(data.storage_provider)
    if data.storage_provider is not None
    else None
)
runtime_provider = get_remote_runtime_provider(data.runtime_provider)

queue_provider.validate_configuration()
if storage_provider is not None:
    storage_provider.validate_configuration()
if runtime_provider is not None:
    runtime_provider.validate_configuration()
```

Add `AliyunConfigurationError` to the exception tuple and return HTTP 400.

- [ ] **Step 5: Run tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "phase_10c_aliyun_provider_names or missing_secret or unknown_provider" -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add apps/api/app/ai_company_api/services/cloud_queue_providers.py apps/api/app/ai_company_api/services/object_storage.py apps/api/app/ai_company_api/services/remote_runtime.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: register aliyun provider names"
```

---

### Task 4: Aliyun OSS Object Storage Provider

**Files:**
- Modify: `apps/api/app/ai_company_api/services/object_storage.py`
- Modify: `apps/api/tests/test_cloud_object_storage.py`

- [ ] **Step 1: Write failing OSS provider tests**

Append to `apps/api/tests/test_cloud_object_storage.py`:

```python
from ai_company_api.services.aliyun_clients import (
    AliyunClientBundle,
    AliyunOssPutObjectRequest,
)


class FakeAliyunOssClient:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_requests: list[AliyunOssPutObjectRequest] = []

    def put_object(self, request: AliyunOssPutObjectRequest) -> None:
        self.put_requests.append(request)
        self.objects[(request.bucket, request.object_key)] = request.content

    def get_object_text(self, bucket: str, object_key: str) -> str:
        return self.objects[(bucket, object_key)].decode("utf-8")


class UnusedClient:
    pass


def _install_fake_oss(monkeypatch: pytest.MonkeyPatch) -> FakeAliyunOssClient:
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "secret")
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_BUCKET", "ai-scdc-dev-artifacts")
    fake_oss = FakeAliyunOssClient()
    monkeypatch.setattr(
        "ai_company_api.services.aliyun_clients._CLIENT_BUNDLE_OVERRIDE",
        AliyunClientBundle(mns=UnusedClient(), oss=fake_oss, eci=UnusedClient()),
    )
    return fake_oss


def test_aliyun_oss_storage_puts_and_reads_text_ref(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_oss = _install_fake_oss(monkeypatch)
    with _build_storage_session(tmp_path) as session:
        provider = get_object_storage_provider("aliyun_oss")
        text = "diff --git a/app.py b/app.py\n+print('oss')\n"

        ref = provider.put_text(
            session,
            ObjectStorageWrite(
                workspace_id="dev_workspace",
                cloud_run_id="cloud_run_1",
                kind="diff",
                content=text,
                content_type="text/x-diff",
            ),
        )

        assert ref.kind == "diff"
        assert ref.uri.startswith("oss://ai-scdc-dev-artifacts/ai-scdc/dev/")
        assert ref.sha256 == sha256(text.encode("utf-8")).hexdigest()
        assert ref.size_bytes == len(text.encode("utf-8"))
        assert ref.content_type == "text/x-diff"
        assert fake_oss.put_requests[0].content_type == "text/x-diff"
        assert provider.read_text(session, ref) == text


def test_aliyun_oss_storage_rejects_hash_mismatch(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_oss(monkeypatch)
    with _build_storage_session(tmp_path) as session:
        provider = get_object_storage_provider("aliyun_oss")
        ref = provider.put_text(
            session,
            ObjectStorageWrite(
                workspace_id="dev_workspace",
                cloud_run_id="cloud_run_1",
                kind="log",
                content="safe log",
            ),
        )
        ref.sha256 = "0" * 64

        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, ref)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest apps/api/tests/test_cloud_object_storage.py -k aliyun_oss -v
```

Expected: FAIL because `aliyun_oss` is not implemented.

- [ ] **Step 3: Implement `AliyunOssObjectStorageProvider`**

In `object_storage.py`, add imports:

```python
from ai_company_api.services.aliyun_clients import (
    AliyunOssPutObjectRequest,
    get_aliyun_client_bundle,
)
from ai_company_api.services.aliyun_config import require_aliyun_settings
```

Add constants:

```python
ALIYUN_OSS_SCHEME = "oss"
```

Add provider class:

```python
class AliyunOssObjectStorageProvider:
    name = "aliyun_oss"

    def validate_configuration(self) -> None:
        require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "oss_endpoint",
                "oss_bucket",
            ),
        )

    def put_text(
        self,
        session: Session,
        write: ObjectStorageWrite,
    ) -> ObjectStorageRef:
        self.validate_configuration()
        _validate_artifact_kind(write.kind)
        settings = require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "oss_endpoint",
                "oss_bucket",
            ),
        )
        content_bytes = write.content.encode("utf-8")
        digest = sha256(content_bytes).hexdigest()
        suffix = "json" if write.content_type == "application/json" else "txt"
        object_key = (
            f"{settings.oss_prefix.strip('/')}/workspaces/{write.workspace_id}/"
            f"cloud-runs/{write.cloud_run_id}/{write.kind}/{digest}.{suffix}"
        )
        get_aliyun_client_bundle(settings).oss.put_object(
            AliyunOssPutObjectRequest(
                bucket=settings.oss_bucket or "",
                object_key=object_key,
                content=content_bytes,
                content_type=write.content_type,
            )
        )
        return ObjectStorageRef(
            kind=write.kind,
            uri=f"{ALIYUN_OSS_SCHEME}://{settings.oss_bucket}/{object_key}",
            sha256=digest,
            size_bytes=len(content_bytes),
            content_type=write.content_type,
        )

    def read_text(
        self,
        session: Session,
        ref: ObjectStorageRef,
    ) -> str:
        self.validate_configuration()
        _validate_artifact_kind(ref.kind)
        settings = require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "oss_endpoint",
                "oss_bucket",
            ),
        )
        bucket, object_key = _parse_oss_ref(ref.uri)
        if bucket != settings.oss_bucket:
            raise ObjectStorageReadError("Object storage reference bucket mismatch")
        expected_prefix = f"{settings.oss_prefix.strip('/')}/workspaces/"
        if not object_key.startswith(expected_prefix):
            raise ObjectStorageReadError("Object storage reference prefix mismatch")
        content = get_aliyun_client_bundle(settings).oss.get_object_text(bucket, object_key)
        content_bytes = content.encode("utf-8")
        if sha256(content_bytes).hexdigest() != ref.sha256:
            raise ObjectStorageReadError("Object storage content sha256 mismatch")
        if len(content_bytes) != ref.size_bytes:
            raise ObjectStorageReadError("Object storage content size mismatch")
        return content
```

Add `_parse_oss_ref()`:

```python
def _parse_oss_ref(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != ALIYUN_OSS_SCHEME:
        raise ObjectStorageReadError("Object storage reference scheme mismatch")
    if not parsed.netloc:
        raise ObjectStorageReadError("Object storage reference bucket missing")
    object_key = parsed.path.lstrip("/")
    if not object_key:
        raise ObjectStorageReadError("Object storage reference object key missing")
    return parsed.netloc, object_key
```

Update `get_object_storage_provider()`:

```python
def get_object_storage_provider(name: str | None) -> ObjectStorageProvider:
    if name in (None, "local_inline"):
        return LocalInlineObjectStorageProvider()
    if name == "aliyun_oss":
        return AliyunOssObjectStorageProvider()
    raise ObjectStorageProviderNotFound(f"Unknown object storage provider: {name}")
```

Add `validate_configuration()` to `LocalInlineObjectStorageProvider` returning `None`.

- [ ] **Step 4: Run tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_object_storage.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add apps/api/app/ai_company_api/services/object_storage.py apps/api/tests/test_cloud_object_storage.py
git commit -m "feat: add aliyun oss object storage"
```

---

### Task 5: Worker Artifact Upload Endpoint and OSS Artifact Refs

**Files:**
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing worker artifact upload tests**

Append to `apps/api/tests/test_cloud_run_api.py`:

```python
def test_worker_uploads_diff_artifact_ref_through_storage_provider(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "storage_provider": "local_inline"},
    ).json()["cloud_run"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "cloud_run_id": queued["id"],
            "lease_seconds": 60,
        },
    ).json()
    diff_text = "diff --git a/app.py b/app.py\n+print('uploaded')\n"

    upload_response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/artifacts",
        json={
            "worker_id": "remote-worker-1",
            "kind": "diff",
            "content": diff_text,
            "content_type": "text/x-diff",
        },
    )

    assert upload_response.status_code == 201
    ref = upload_response.json()
    assert ref["kind"] == "diff"
    assert ref["uri"].startswith("local-inline://cloud-run-objects/")
    assert ref["sha256"] == sha256(diff_text.encode("utf-8")).hexdigest()

    payload = remote_stub_completion_payload(queued["id"])
    payload["result"]["diff_text"] = ""
    payload["result"]["artifact_refs"] = [ref]
    complete = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=payload,
    )

    assert complete.status_code == 200
    assert complete.json()["patch_artifact"]["diff_text"] == diff_text
```

Also add a rejection test:

```python
def test_worker_artifact_upload_rejects_wrong_worker(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "storage_provider": "local_inline"},
    ).json()["cloud_run"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "cloud_run_id": queued["id"],
            "lease_seconds": 60,
        },
    ).json()

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/artifacts",
        json={
            "worker_id": "remote-worker-2",
            "kind": "diff",
            "content": "diff --git a/app.py b/app.py\n+bad\n",
            "content_type": "text/x-diff",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Cloud run lease is not current"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "worker_uploads_diff_artifact_ref or artifact_upload_rejects_wrong_worker" -v
```

Expected: FAIL because the upload endpoint and specific lease claim field do not exist.

- [ ] **Step 3: Add schemas**

In `schemas/api.py`, add `cloud_run_id` to `CloudRunLeaseCreate`:

```python
class CloudRunLeaseCreate(BaseModel):
    worker_id: str = Field(min_length=1)
    worker_kind: str = Field(default="remote_stub", min_length=1)
    queue_provider: str = Field(default="local_db", min_length=1)
    cloud_run_id: str | None = Field(default=None, min_length=1)
    lease_seconds: int = Field(default=60, ge=1, le=3600)
```

Add artifact upload schema after `CloudRunArtifactRefCreate`:

```python
class CloudRunArtifactUploadCreate(BaseModel):
    worker_id: str = Field(min_length=1)
    kind: Literal["diff", "log", "command_result", "test_result", "manifest"]
    content: str = Field(max_length=2 * 1024 * 1024)
    content_type: str = "text/plain"
```

Import it in `routes.py`.

- [ ] **Step 4: Support specific lease claims**

In `cloud_runner.claim_next_cloud_run_lease()`, pass `data.cloud_run_id` into `_claim_cloud_run_lease()` or the candidate selection helper. Add a filter:

```python
if cloud_run_id is not None:
    statement = statement.where(CloudRun.id == cloud_run_id)
```

Keep the existing queue provider, queued status, cancellation, and max-attempt filters.

- [ ] **Step 5: Implement artifact upload service and route**

Add to `cloud_runner.py`:

```python
def upload_cloud_run_lease_artifact(
    session: Session,
    *,
    lease_id: str,
    data: CloudRunArtifactUploadCreate,
) -> CloudRunArtifactRefCreate:
    cloud_run = _get_current_cloud_run_lease_or_409(
        session,
        lease_id=lease_id,
        worker_id=data.worker_id,
    )
    provider = get_object_storage_provider(cloud_run.storage_provider)
    ref = provider.put_text(
        session,
        ObjectStorageWrite(
            workspace_id=cloud_run.workspace_id,
            cloud_run_id=cloud_run.id,
            kind=data.kind,
            content=data.content,
            content_type=data.content_type,
        ),
    )
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="worker_artifact_uploaded",
        message="Cloud run worker artifact uploaded.",
        payload={
            "kind": ref.kind,
            "uri": _redact_external_uri(ref.uri),
            "size_bytes": ref.size_bytes,
            "content_type": ref.content_type,
        },
    )
    session.add(cloud_run)
    session.commit()
    return CloudRunArtifactRefCreate(
        kind=ref.kind,
        uri=ref.uri,
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        content_type=ref.content_type,
    )
```

Add route in `routes.py`:

```python
@router.post(
    "/cloud-run-worker/leases/{lease_id}/artifacts",
    status_code=status.HTTP_201_CREATED,
    response_model=CloudRunArtifactRefCreate,
)
def post_cloud_run_worker_artifact(
    lease_id: str,
    data: CloudRunArtifactUploadCreate,
    session: SessionDep,
) -> CloudRunArtifactRefCreate:
    return upload_cloud_run_lease_artifact(session, lease_id=lease_id, data=data)
```

Update `_object_storage_provider_name_from_uri()`:

```python
if uri.startswith("oss://"):
    return "aliyun_oss"
```

- [ ] **Step 6: Run tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "worker_uploads_diff_artifact_ref or artifact_upload_rejects_wrong_worker or complete_cloud_run_lease_uses_diff_artifact_ref" -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/api/routes.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: add worker artifact upload endpoint"
```

---

### Task 6: Aliyun MNS Queue Enqueue

**Files:**
- Modify: `apps/api/app/ai_company_api/services/cloud_queue_providers.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing MNS enqueue integration test**

Append to `apps/api/tests/test_cloud_run_api.py`:

```python
from ai_company_api.services.aliyun_clients import (
    AliyunClientBundle,
    AliyunMnsSendMessageRequest,
)


class FakeAliyunMnsClient:
    def __init__(self) -> None:
        self.requests: list[AliyunMnsSendMessageRequest] = []

    def send_message(self, request: AliyunMnsSendMessageRequest) -> dict:
        self.requests.append(request)
        return {"message_id": f"aliyun-mns-message-{request.cloud_run_id}"}


def test_aliyun_mns_queue_provider_sends_message_on_enqueue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FakeExecutorShouldNotRun:
        sandbox_kind = "fake"

        def run(self, _request):
            raise AssertionError("executor should not run during enqueue")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FakeExecutorShouldNotRun(),
    )
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "secret")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_MNS_ENDPOINT",
        "https://123456.mns.cn-hangzhou.aliyuncs.com",
    )
    monkeypatch.setenv("AI_SCDC_ALIYUN_MNS_QUEUE_NAME", "ai-scdc-cloud-runs-dev")
    fake_mns = FakeAliyunMnsClient()
    monkeypatch.setattr(
        "ai_company_api.services.aliyun_clients._CLIENT_BUNDLE_OVERRIDE",
        AliyunClientBundle(mns=fake_mns, oss=UnusedClient(), eci=UnusedClient()),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    response = client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={
            "repo_id": repository.id,
            "queue_provider": "aliyun_mns",
        },
    )

    assert response.status_code == 201
    cloud_run = response.json()["cloud_run"]
    assert cloud_run["queue_provider"] == "aliyun_mns"
    assert cloud_run["queue_message_id"] == f"aliyun-mns-message-{cloud_run['id']}"
    assert cloud_run["external_status"] == "queued"
    assert "queue_receipt" not in cloud_run
    assert len(fake_mns.requests) == 1
    assert fake_mns.requests[0].queue_name == "ai-scdc-cloud-runs-dev"
    assert fake_mns.requests[0].cloud_run_id == cloud_run["id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k aliyun_mns_queue_provider_sends_message -v
```

Expected: FAIL because `aliyun_mns.enqueue()` does not send a message yet.

- [ ] **Step 3: Implement MNS enqueue**

In `cloud_queue_providers.py`, update `AliyunMnsQueueProvider.enqueue()`:

```python
def enqueue(self, request: CloudQueueEnqueueRequest) -> CloudQueueEnqueueResult:
    settings = require_aliyun_settings(
        provider_name=self.name,
        required_names=(
            "region_id",
            "access_key_id",
            "access_key_secret",
            "mns_endpoint",
            "mns_queue_name",
        ),
    )
    body = json.dumps(
        {
            "workspace_id": request.workspace_id,
            "project_id": request.project_id,
            "task_id": request.task_id,
            "cloud_run_id": request.cloud_run_id,
            "queue_provider": request.queue_provider,
            "runtime_provider": request.runtime_provider,
            "storage_provider": request.storage_provider,
        },
        sort_keys=True,
    )
    result = get_aliyun_client_bundle(settings).mns.send_message(
        AliyunMnsSendMessageRequest(
            queue_name=settings.mns_queue_name or "",
            cloud_run_id=request.cloud_run_id,
            workspace_id=request.workspace_id,
            project_id=request.project_id,
            task_id=request.task_id,
            body=body,
        )
    )
    return CloudQueueEnqueueResult(
        queue_message_id=result.get("message_id"),
        external_status="queued",
    )
```

Add imports for `json`, `AliyunMnsSendMessageRequest`, and `get_aliyun_client_bundle`.

In `cloud_runner.enqueue_cloud_run()`, after `cloud_run.local_run_id = local_run.id`, call:

```python
queue_result = get_cloud_queue_provider(data.queue_provider).enqueue(
    CloudQueueEnqueueRequest(
        workspace_id=cloud_run.workspace_id,
        project_id=cloud_run.project_id,
        task_id=cloud_run.task_id,
        cloud_run_id=cloud_run.id,
        queue_provider=cloud_run.queue_provider,
        runtime_provider=cloud_run.runtime_provider,
        storage_provider=cloud_run.storage_provider,
    )
)
cloud_run.queue_message_id = queue_result.queue_message_id
cloud_run.queue_receipt = queue_result.queue_receipt
if queue_result.external_status is not None:
    cloud_run.external_status = queue_result.external_status
```

Remove the previous inline `external_status` assignment from the `CloudRun` constructor.

- [ ] **Step 4: Run tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun_mns_queue_provider_sends_message or external_stub_queue_provider or phase_10b_provider_metadata" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add apps/api/app/ai_company_api/services/cloud_queue_providers.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: enqueue cloud runs through aliyun mns"
```

---

### Task 7: Aliyun ECI Runtime Submission

**Files:**
- Modify: `apps/api/app/ai_company_api/services/remote_runtime.py`
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing ECI runtime submission test**

Append to `apps/api/tests/test_cloud_run_api.py`:

```python
from ai_company_api.services.aliyun_clients import AliyunEciCreateContainerGroupRequest


class FakeAliyunEciClient:
    def __init__(self) -> None:
        self.requests: list[AliyunEciCreateContainerGroupRequest] = []

    def create_container_group(self, request: AliyunEciCreateContainerGroupRequest) -> dict:
        self.requests.append(request)
        return {"container_group_id": f"eci-cg-{request.cloud_run_id}"}


def test_aliyun_eci_runtime_submission_creates_safe_container_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FakeExecutorShouldNotRun:
        sandbox_kind = "fake"

        def run(self, _request):
            raise AssertionError("executor should not run during enqueue")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FakeExecutorShouldNotRun(),
    )
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "secret")
    monkeypatch.setenv("AI_SCDC_ALIYUN_MNS_ENDPOINT", "https://123456.mns.cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("AI_SCDC_ALIYUN_MNS_QUEUE_NAME", "ai-scdc-cloud-runs-dev")
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_BUCKET", "ai-scdc-dev-artifacts")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_VSWITCH_ID", "vsw-demo")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID", "sg-demo")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ECI_IMAGE",
        "registry.cn-hangzhou.aliyuncs.com/ai-scdc/remote-worker:dev",
    )
    monkeypatch.setenv("AI_SCDC_API_PUBLIC_BASE_URL", "https://api.example.test")
    fake_mns = FakeAliyunMnsClient()
    fake_oss = FakeAliyunOssClient()
    fake_eci = FakeAliyunEciClient()
    monkeypatch.setattr(
        "ai_company_api.services.aliyun_clients._CLIENT_BUNDLE_OVERRIDE",
        AliyunClientBundle(mns=fake_mns, oss=fake_oss, eci=fake_eci),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    response = client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={
            "repo_id": repository.id,
            "queue_provider": "aliyun_mns",
            "storage_provider": "aliyun_oss",
            "runtime_provider": "aliyun_eci",
        },
    )

    assert response.status_code == 201
    cloud_run = response.json()["cloud_run"]
    assert cloud_run["runtime_provider"] == "aliyun_eci"
    assert cloud_run["runtime_job_id"] == f"eci-cg-{cloud_run['id']}"
    assert cloud_run["external_status"] == "submitted"
    assert cloud_run["artifact_manifest_uri"].startswith("oss://ai-scdc-dev-artifacts/")
    assert cloud_run["log_stream_uri"].startswith("oss://ai-scdc-dev-artifacts/")
    assert len(fake_eci.requests) == 1
    request = fake_eci.requests[0]
    assert request.cloud_run_id == cloud_run["id"]
    assert request.image.endswith("/remote-worker:dev")
    assert request.environment["AI_SCDC_API_BASE_URL"] == "https://api.example.test"
    assert request.environment["AI_SCDC_CLOUD_RUN_ID"] == cloud_run["id"]
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in request.environment
    assert "secret" not in str(request.environment)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k aliyun_eci_runtime_submission -v
```

Expected: FAIL because `aliyun_eci` runtime submission is not implemented.

- [ ] **Step 3: Implement `AliyunEciRuntimeProvider`**

In `remote_runtime.py`, add imports:

```python
from ai_company_api.services.aliyun_clients import (
    AliyunEciCreateContainerGroupRequest,
    get_aliyun_client_bundle,
)
from ai_company_api.services.aliyun_config import require_aliyun_settings
```

Add provider:

```python
class AliyunEciRuntimeProvider:
    name = "aliyun_eci"

    def validate_configuration(self) -> None:
        require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "eci_vswitch_id",
                "eci_security_group_id",
                "eci_image",
                "api_public_base_url",
            ),
        )

    def submit(
        self,
        session: Session,
        submission: RemoteRuntimeSubmission,
    ) -> RemoteRuntimeSubmissionResult:
        settings = require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "eci_vswitch_id",
                "eci_security_group_id",
                "eci_image",
                "api_public_base_url",
            ),
        )
        container_group_name = (
            f"{settings.eci_container_group_prefix}-{submission.cloud_run_id}"
        )
        environment = {
            "AI_SCDC_API_BASE_URL": settings.api_public_base_url or "",
            "AI_SCDC_CLOUD_RUN_ID": submission.cloud_run_id,
            "AI_SCDC_WORKER_ID": f"aliyun-eci-{submission.cloud_run_id}",
            "AI_SCDC_QUEUE_PROVIDER": submission.queue_provider,
            "AI_SCDC_STORAGE_PROVIDER": submission.storage_provider or "",
        }
        result = get_aliyun_client_bundle(settings).eci.create_container_group(
            AliyunEciCreateContainerGroupRequest(
                region_id=settings.region_id or "",
                cloud_run_id=submission.cloud_run_id,
                container_group_name=container_group_name,
                image=settings.eci_image or "",
                vswitch_id=settings.eci_vswitch_id or "",
                security_group_id=settings.eci_security_group_id or "",
                cpu=settings.eci_cpu,
                memory_gb=settings.eci_memory_gb,
                environment=environment,
            )
        )
        artifact_manifest_uri = None
        log_stream_uri = None
        if submission.storage_provider == "aliyun_oss":
            storage_provider = get_object_storage_provider("aliyun_oss")
            manifest_ref = storage_provider.put_text(
                session,
                ObjectStorageWrite(
                    workspace_id=submission.workspace_id,
                    cloud_run_id=submission.cloud_run_id,
                    kind="manifest",
                    content=json.dumps(
                        {
                            "cloud_run_id": submission.cloud_run_id,
                            "runtime_provider": self.name,
                            "runtime_job_id": result.get("container_group_id"),
                            "status": "submitted",
                        },
                        sort_keys=True,
                    ),
                    content_type="application/json",
                ),
            )
            log_ref = storage_provider.put_text(
                session,
                ObjectStorageWrite(
                    workspace_id=submission.workspace_id,
                    cloud_run_id=submission.cloud_run_id,
                    kind="log",
                    content="Remote runtime submitted via aliyun_eci.\n",
                    content_type="text/plain",
                ),
            )
            artifact_manifest_uri = manifest_ref.uri
            log_stream_uri = log_ref.uri
        return RemoteRuntimeSubmissionResult(
            runtime_job_id=result.get("container_group_id") or container_group_name,
            external_status="submitted",
            artifact_manifest_uri=artifact_manifest_uri,
            log_stream_uri=log_stream_uri,
        )
```

Register it:

```python
_KNOWN_RUNTIME_PROVIDERS = {
    "remote_stub": RemoteStubRuntimeProvider(),
    "aliyun_eci": AliyunEciRuntimeProvider(),
}
```

Update `RemoteStubRuntimeProvider` with `validate_configuration()` returning `None`.

- [ ] **Step 4: Run tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun_eci_runtime_submission or remote_stub_runtime" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add apps/api/app/ai_company_api/services/remote_runtime.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: submit remote runs to aliyun eci"
```

---

### Task 8: Remote Worker Entry Point and Dockerfile

**Files:**
- Create: `apps/api/app/ai_company_api/services/remote_worker.py`
- Create: `apps/api/tests/test_remote_worker.py`
- Create: `apps/api/Dockerfile.remote-worker`

- [ ] **Step 1: Write failing remote worker tests**

Create `apps/api/tests/test_remote_worker.py`:

```python
from ai_company_api.services.remote_worker import (
    RemoteWorkerConfig,
    run_remote_worker_once,
)


class FakeWorkerClient:
    def __init__(self) -> None:
        self.uploaded: list[dict] = []
        self.completed: dict | None = None

    def claim(self, config: RemoteWorkerConfig) -> dict:
        return {
            "lease_id": "lease_1",
            "cloud_run": {
                "id": config.cloud_run_id,
                "task_id": "task_1",
                "status": "running",
            },
        }

    def heartbeat(self, lease_id: str, worker_id: str) -> dict:
        return {"lease_id": lease_id, "cancel_requested": False}

    def upload_artifact(
        self,
        lease_id: str,
        worker_id: str,
        *,
        kind: str,
        content: str,
        content_type: str,
    ) -> dict:
        ref = {
            "kind": kind,
            "uri": f"oss://bucket/{kind}.txt",
            "sha256": "a" * 64,
            "size_bytes": len(content.encode("utf-8")),
            "content_type": content_type,
        }
        self.uploaded.append(ref)
        return ref

    def complete(self, lease_id: str, worker_id: str, result: dict) -> dict:
        self.completed = result
        return {"cloud_run": {"status": "patch_ready"}}


def test_remote_worker_uploads_diff_ref_and_completes() -> None:
    client = FakeWorkerClient()
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
    )

    result = run_remote_worker_once(config, client=client)

    assert result["cloud_run"]["status"] == "patch_ready"
    assert client.uploaded[0]["kind"] == "diff"
    assert client.completed is not None
    assert client.completed["result"]["artifact_refs"] == client.uploaded
    assert client.completed["result"]["diff_text"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest apps/api/tests/test_remote_worker.py -v
```

Expected: FAIL because `remote_worker.py` does not exist.

- [ ] **Step 3: Implement deterministic remote worker**

Create `apps/api/app/ai_company_api/services/remote_worker.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Protocol
from urllib import request as urllib_request


@dataclass(frozen=True)
class RemoteWorkerConfig:
    api_base_url: str
    cloud_run_id: str
    worker_id: str
    queue_provider: str
    storage_provider: str


class RemoteWorkerClient(Protocol):
    def claim(self, config: RemoteWorkerConfig) -> dict:
        raise NotImplementedError

    def heartbeat(self, lease_id: str, worker_id: str) -> dict:
        raise NotImplementedError

    def upload_artifact(
        self,
        lease_id: str,
        worker_id: str,
        *,
        kind: str,
        content: str,
        content_type: str,
    ) -> dict:
        raise NotImplementedError

    def complete(self, lease_id: str, worker_id: str, result: dict) -> dict:
        raise NotImplementedError


class HttpRemoteWorkerClient:
    def claim(self, config: RemoteWorkerConfig) -> dict:
        return _post_json(
            config.api_base_url,
            "/cloud-run-worker/leases",
            {
                "worker_id": config.worker_id,
                "worker_kind": "aliyun_eci",
                "queue_provider": config.queue_provider,
                "cloud_run_id": config.cloud_run_id,
                "lease_seconds": 300,
            },
        )

    def heartbeat(self, lease_id: str, worker_id: str) -> dict:
        return _post_json(
            "",
            f"/cloud-run-worker/leases/{lease_id}/heartbeat",
            {"worker_id": worker_id, "lease_seconds": 300},
        )

    def upload_artifact(
        self,
        lease_id: str,
        worker_id: str,
        *,
        kind: str,
        content: str,
        content_type: str,
    ) -> dict:
        return _post_json(
            "",
            f"/cloud-run-worker/leases/{lease_id}/artifacts",
            {
                "worker_id": worker_id,
                "kind": kind,
                "content": content,
                "content_type": content_type,
            },
        )

    def complete(self, lease_id: str, worker_id: str, result: dict) -> dict:
        return _post_json(
            "",
            f"/cloud-run-worker/leases/{lease_id}/complete",
            {
                "worker_id": worker_id,
                "result": result["result"],
            },
        )


_API_BASE_URL = ""


def run_remote_worker_once(
    config: RemoteWorkerConfig,
    *,
    client: RemoteWorkerClient | None = None,
) -> dict:
    global _API_BASE_URL
    _API_BASE_URL = config.api_base_url.rstrip("/")
    resolved_client = client or HttpRemoteWorkerClient()
    lease = resolved_client.claim(config)
    lease_id = lease["lease_id"]
    resolved_client.heartbeat(lease_id, config.worker_id)
    diff_text = _deterministic_diff(config.cloud_run_id)
    diff_ref = resolved_client.upload_artifact(
        lease_id,
        config.worker_id,
        kind="diff",
        content=diff_text,
        content_type="text/x-diff",
    )
    completion = {
        "result": {
            "status": "patch_ready",
            "runner_kind": "aliyun_eci",
            "base_sha": None,
            "head_sha": None,
            "worktree_ref": f"aliyun-eci://{config.cloud_run_id}",
            "summary": "Aliyun ECI remote worker produced a deterministic smoke patch.",
            "files_changed": ["AI_SCDC_ALIYUN_ECI.md"],
            "tests_run": [],
            "test_result": "not_run",
            "risks": [],
            "diff_text": "",
            "artifact_refs": [diff_ref],
            "command_results": [],
            "test_command_results": [],
            "failure_reason": None,
        }
    }
    return resolved_client.complete(lease_id, config.worker_id, completion)


def config_from_env() -> RemoteWorkerConfig:
    return RemoteWorkerConfig(
        api_base_url=_required_env("AI_SCDC_API_BASE_URL"),
        cloud_run_id=_required_env("AI_SCDC_CLOUD_RUN_ID"),
        worker_id=_required_env("AI_SCDC_WORKER_ID"),
        queue_provider=os.getenv("AI_SCDC_QUEUE_PROVIDER", "aliyun_mns"),
        storage_provider=os.getenv("AI_SCDC_STORAGE_PROVIDER", "aliyun_oss"),
    )


def main() -> None:
    run_remote_worker_once(config_from_env())


def _post_json(api_base_url: str, path: str, payload: dict) -> dict:
    base_url = api_base_url or _API_BASE_URL
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _deterministic_diff(cloud_run_id: str) -> str:
    return (
        "diff --git a/AI_SCDC_ALIYUN_ECI.md b/AI_SCDC_ALIYUN_ECI.md\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/AI_SCDC_ALIYUN_ECI.md\n"
        "@@ -0,0 +1,3 @@\n"
        "+# AI-SCDC Aliyun ECI Smoke\n"
        f"+Cloud run: {cloud_run_id}\n"
        "+Provider: aliyun_eci\n"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create remote worker Dockerfile**

Create `apps/api/Dockerfile.remote-worker`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml /app/apps/api/pyproject.toml
COPY app /app/apps/api/app

RUN pip install --no-cache-dir -e /app/apps/api

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "ai_company_api.services.remote_worker"]
```

- [ ] **Step 5: Run tests**

Run:

```powershell
pytest apps/api/tests/test_remote_worker.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add apps/api/app/ai_company_api/services/remote_worker.py apps/api/tests/test_remote_worker.py apps/api/Dockerfile.remote-worker
git commit -m "feat: add aliyun remote worker entrypoint"
```

---

### Task 9: End-to-End Aliyun Provider API Test

**Files:**
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Add full fake-client Aliyun enqueue test**

Append to `apps/api/tests/test_cloud_run_api.py`:

```python
def test_aliyun_provider_mvp_enqueue_persists_non_sensitive_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FakeExecutorShouldNotRun:
        sandbox_kind = "fake"

        def run(self, _request):
            raise AssertionError("executor should not run during enqueue")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FakeExecutorShouldNotRun(),
    )
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "secret-value")
    monkeypatch.setenv("AI_SCDC_ALIYUN_MNS_ENDPOINT", "https://123456.mns.cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("AI_SCDC_ALIYUN_MNS_QUEUE_NAME", "ai-scdc-cloud-runs-dev")
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_BUCKET", "ai-scdc-dev-artifacts")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_VSWITCH_ID", "vsw-demo")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID", "sg-demo")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ECI_IMAGE",
        "registry.cn-hangzhou.aliyuncs.com/ai-scdc/remote-worker:dev",
    )
    monkeypatch.setenv("AI_SCDC_API_PUBLIC_BASE_URL", "https://api.example.test")
    fake_mns = FakeAliyunMnsClient()
    fake_oss = FakeAliyunOssClient()
    fake_eci = FakeAliyunEciClient()
    monkeypatch.setattr(
        "ai_company_api.services.aliyun_clients._CLIENT_BUNDLE_OVERRIDE",
        AliyunClientBundle(mns=fake_mns, oss=fake_oss, eci=fake_eci),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    response = client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={
            "repo_id": repository.id,
            "queue_provider": "aliyun_mns",
            "storage_provider": "aliyun_oss",
            "runtime_provider": "aliyun_eci",
        },
    )

    assert response.status_code == 201
    cloud_run = response.json()["cloud_run"]
    assert cloud_run["queue_provider"] == "aliyun_mns"
    assert cloud_run["storage_provider"] == "aliyun_oss"
    assert cloud_run["runtime_provider"] == "aliyun_eci"
    assert cloud_run["queue_message_id"].startswith("aliyun-mns-message-")
    assert cloud_run["runtime_job_id"].startswith("eci-cg-")
    assert cloud_run["external_status"] == "submitted"
    assert cloud_run["artifact_manifest_uri"].startswith("oss://ai-scdc-dev-artifacts/")
    assert cloud_run["log_stream_uri"].startswith("oss://ai-scdc-dev-artifacts/")
    assert cloud_run["external_error"] is None
    assert "queue_receipt" not in cloud_run
    assert "secret-value" not in str(response.json())
```

- [ ] **Step 2: Run focused API tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun_provider_mvp or aliyun_mns or aliyun_eci or worker_uploads" -v
```

Expected: PASS.

- [ ] **Step 3: Run broader API regression**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -v
pytest apps/api/tests/test_cloud_object_storage.py -v
pytest apps/api/tests/test_aliyun_config.py apps/api/tests/test_aliyun_clients.py apps/api/tests/test_remote_worker.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```powershell
git add apps/api/tests/test_cloud_run_api.py
git commit -m "test: cover aliyun provider mvp flow"
```

---

### Task 10: README Smoke and Cleanup Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add Aliyun Phase 10C smoke section**

Add after the Phase 10A smoke section:

```markdown
## Phase 10C Aliyun Provider MVP Smoke

Phase 10C can submit a cloud run through Aliyun MNS, store provider artifacts in
Aliyun OSS, and launch a short-lived Aliyun ECI remote worker from an ACR image.
Do not create a long-lived ECI instance manually in the console. The API creates
one short-lived container group when `runtime_provider` is `aliyun_eci`.

Required Aliyun services:

- RAM user or role with narrowly scoped MNS, OSS, and ECI permissions.
- OSS private bucket with lifecycle cleanup for development prefixes.
- MNS queue in queue mode.
- ACR private repository containing the remote worker image.
- ECI enabled in the same region as the selected VPC/VSwitch/security group.
- Outbound network path for the ECI worker if the API URL or GitHub requires
  public network access.

Build and push the worker image:

```powershell
$AcrImage = "<acr-registry>/<namespace>/<repo>:dev"
docker build -f apps/api/Dockerfile.remote-worker -t $AcrImage apps/api
docker push $AcrImage
```

Configure the local API shell:

```powershell
$env:AI_SCDC_ALIYUN_REGION_ID = "cn-hangzhou"
$env:AI_SCDC_ALIYUN_ACCESS_KEY_ID = "<set locally>"
$env:AI_SCDC_ALIYUN_ACCESS_KEY_SECRET = "<set locally>"
$env:AI_SCDC_ALIYUN_MNS_ENDPOINT = "https://<account-id>.mns.cn-hangzhou.aliyuncs.com"
$env:AI_SCDC_ALIYUN_MNS_QUEUE_NAME = "ai-scdc-cloud-runs-dev"
$env:AI_SCDC_ALIYUN_OSS_ENDPOINT = "https://oss-cn-hangzhou.aliyuncs.com"
$env:AI_SCDC_ALIYUN_OSS_BUCKET = "ai-scdc-dev-artifacts"
$env:AI_SCDC_ALIYUN_ECI_VSWITCH_ID = "<vsw-id>"
$env:AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID = "<sg-id>"
$env:AI_SCDC_ALIYUN_ECI_IMAGE = $AcrImage
$env:AI_SCDC_API_PUBLIC_BASE_URL = "<URL reachable from ECI>"
```

Start a cloud run with Aliyun providers:

```powershell
$cloudRun = Invoke-RestMethod `
  -Method Post `
  -Uri "$ApiBase/tasks/$TaskId/cloud-runs" `
  -ContentType "application/json" `
  -Body (JsonBody @{
    repo_id = $RepoId
    queue_provider = "aliyun_mns"
    storage_provider = "aliyun_oss"
    runtime_provider = "aliyun_eci"
  })
```

Expected output includes `queue_provider = aliyun_mns`, `storage_provider =
aliyun_oss`, `runtime_provider = aliyun_eci`, an MNS message ID, an ECI runtime
job ID, and `oss://` artifact/log URIs. Secrets and queue receipts must not
appear in API responses.

Cleanup after smoke:

- Stop or delete any ECI container group left from the smoke run.
- Delete OSS objects under the development prefix if lifecycle cleanup has not
  removed them yet.
- Purge unneeded MNS test messages.
- Release idle NAT gateway or EIP resources that were created only for smoke
  testing.
```

- [ ] **Step 2: Run markdown and whitespace check**

Run:

```powershell
git diff --check
```

Expected: PASS.

- [ ] **Step 3: Commit**

```powershell
git add README.md
git commit -m "docs: add aliyun phase 10c smoke"
```

---

### Task 11: Architecture and Status Update

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`

- [ ] **Step 1: Update architecture**

In `docs/architecture.md`, add a Phase 10C boundary after Phase 10B:

```markdown
## Phase 10C Boundary

Phase 10C adds the first concrete production provider MVP for the Phase 10B
execution-plane contracts. The selected stack is Aliyun MNS for queue messages,
Aliyun OSS for remote artifact refs, Aliyun ECI for short-lived remote worker
containers, and ACR for the worker image.

The public cloud-run lifecycle remains unchanged. Aliyun providers are selected
by provider names (`aliyun_mns`, `aliyun_oss`, and `aliyun_eci`), automated tests
use fake clients, and real cloud calls are opt-in through environment variables
and smoke commands. Worker containers receive API callback metadata, not broad
Aliyun AccessKeys.

Phase 10C does not add live log streaming, SLS, Kubernetes, automatic PR
creation, automatic merge, billing, or model-backed reviewer/debugger agents.
```

Move the roadmap item from Future to Completed:

```markdown
13. Aliyun provider MVP with MNS queue enqueue, OSS artifact refs, ECI remote worker submission, ACR worker image path, fake-client tests, and opt-in smoke documentation.
```

- [ ] **Step 2: Update status**

In `docs/superpowers/status.md`, update current phase:

```markdown
The project is through Phase 10C: Aliyun provider MVP for the remote
execution-plane contracts.
```

Add completed item:

```markdown
13. Phase 10C Aliyun provider MVP: `aliyun_mns` queue enqueue, `aliyun_oss`
    artifact storage refs, `aliyun_eci` remote runtime submission, worker
    artifact upload endpoint, ACR worker image path, fake-client automated tests,
    and opt-in Aliyun smoke documentation.
```

Keep live log streaming and model-backed agents in future work.

- [ ] **Step 3: Run docs check**

Run:

```powershell
git diff --check
```

Expected: PASS.

- [ ] **Step 4: Commit**

```powershell
git add docs/architecture.md docs/superpowers/status.md
git commit -m "docs: update architecture for phase 10c"
```

---

### Task 12: Final Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run focused Phase 10C tests**

Run:

```powershell
pytest apps/api/tests/test_aliyun_config.py apps/api/tests/test_aliyun_clients.py apps/api/tests/test_cloud_object_storage.py apps/api/tests/test_remote_worker.py -v
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun or worker_uploads or artifact_ref or lease" -v
```

Expected: PASS.

- [ ] **Step 2: Run full API tests**

Run:

```powershell
pytest apps/api/tests
```

Expected: PASS.

- [ ] **Step 3: Run desktop regression tests**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts src/test/App.test.tsx
```

Expected: PASS.

- [ ] **Step 4: Run typecheck and diff check**

Run:

```powershell
pnpm typecheck
git diff --check
```

Expected: PASS.

- [ ] **Step 5: Confirm no secrets in git diff**

Run:

```powershell
rg -n "AccessKey|ACCESS_KEY_SECRET|secret-value|ak-secret|very-secret-value|ALIYUN_ACCESS_KEY_SECRET" apps docs README.md
```

Expected: Only environment variable names and fake test values appear. No real credential values appear.

- [ ] **Step 6: Commit any verification-only doc adjustments**

If verification requires documentation wording changes, commit them:

```powershell
git add README.md docs/architecture.md docs/superpowers/status.md
git commit -m "docs: record phase 10c verification"
```

---

## Implementation Notes

- Do not enter Aliyun AccessKey values in tests, docs, commits, or chat.
- Do not click Create on an ECI console instance for this implementation. The provider creates ECI container groups through the API-side runtime adapter.
- Do not run real Aliyun smoke commands until all automated tests pass.
- Keep `local_db`, `external_stub`, `local_inline`, and `remote_stub` working throughout the plan.
- The first remote worker is a deterministic smoke worker. It proves the ECI callback and artifact-ref path without adding model autonomy or automatic PR behavior.
