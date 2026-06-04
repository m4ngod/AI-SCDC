# Phase 10C-H Aliyun Provider Hardening Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fake-client hardening tests and minimal cleanup behavior for Aliyun Phase 10C provider failure cases.

**Architecture:** Keep the existing Phase 10C provider-neutral API unchanged. Extend the Aliyun ECI fake-client contract with best-effort container cleanup, tighten OSS ref validation, and add regression tests that assert safe redaction and durable failure state.

**Tech Stack:** FastAPI, SQLModel, pytest, Aliyun fake client protocols, existing cloud-run service modules.

---

## File Structure

- Modify `apps/api/app/ai_company_api/services/aliyun_clients.py`
  - Add `delete_container_group()` to the ECI protocol and SDK client.
- Modify `apps/api/app/ai_company_api/services/remote_runtime.py`
  - Attempt ECI cleanup if OSS manifest/log seed writes fail after ECI creation.
- Modify `apps/api/app/ai_company_api/services/object_storage.py`
  - Validate the artifact kind segment in `oss://` object keys.
- Modify `apps/api/tests/test_cloud_run_api.py`
  - Add ECI-created-then-OSS-fails cleanup/failure-state regression.
  - Add API read redaction regression for signed Aliyun-style query data.
- Modify `apps/api/tests/test_cloud_object_storage.py`
  - Add wrong bucket, wrong prefix, wrong size, and wrong kind ref tests.
- Modify `apps/api/tests/test_aliyun_clients.py`
  - Add SDK ECI delete request construction test with monkeypatched SDK modules.

---

### Task 1: Baseline Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run current focused tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun or worker_uploads or artifact_ref or lease" -q
pytest apps/api/tests/test_aliyun_config.py apps/api/tests/test_aliyun_clients.py apps/api/tests/test_cloud_object_storage.py apps/api/tests/test_remote_worker.py -q
```

Expected: PASS. If this fails, stop and investigate before adding hardening tests.

---

### Task 2: ECI Cleanup After OSS Seed Failure

**Files:**
- Modify `apps/api/tests/test_cloud_run_api.py`
- Modify `apps/api/app/ai_company_api/services/aliyun_clients.py`
- Modify `apps/api/app/ai_company_api/services/remote_runtime.py`

- [ ] **Step 1: Add failing fake-client cleanup test**

Add a fake OSS client and ECI cleanup assertion near the existing Aliyun fake clients:

```python
class FailingAliyunOssClient(FakeAliyunOssClient):
    def put_object(self, request: AliyunOssPutObjectRequest) -> None:
        self.put_requests.append(request)
        raise RuntimeError("oss write failed with Signature=secret-token")


class CleanupRecordingAliyunEciClient(FakeAliyunEciClient):
    def __init__(self) -> None:
        super().__init__()
        self.deleted_container_group_ids: list[str] = []

    def delete_container_group(self, *, region_id: str, container_group_id: str) -> None:
        self.deleted_container_group_ids.append(container_group_id)
```

Then add:

```python
def test_aliyun_eci_submission_cleans_up_when_oss_manifest_seed_fails(
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
    _set_complete_aliyun_env(monkeypatch)
    fake_eci = CleanupRecordingAliyunEciClient()
    monkeypatch.setattr(
        "ai_company_api.services.aliyun_clients._CLIENT_BUNDLE_OVERRIDE",
        AliyunClientBundle(
            mns=FakeAliyunMnsClient(),
            oss=FailingAliyunOssClient(),
            eci=fake_eci,
        ),
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

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Cloud runtime provider aliyun_eci failed to submit container group"
    )
    assert "Signature" not in response.text
    assert "secret-token" not in response.text
    assert len(fake_eci.requests) == 1
    assert fake_eci.deleted_container_group_ids == [
        f"eci-cg-{fake_eci.requests[0].cloud_run_id}"
    ]

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.exec(select(CloudRun)).one()
        assert cloud_run.status == "failed"
        assert cloud_run.failure_reason == "runtime_submission_failed"
        assert cloud_run.external_status == "failed"
        assert cloud_run.external_error == (
            "Cloud runtime provider aliyun_eci failed to submit container group"
        )
        assert "secret" not in (cloud_run.external_error or "")
```

- [ ] **Step 2: Run the new test and confirm it fails**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py::test_aliyun_eci_submission_cleans_up_when_oss_manifest_seed_fails -v
```

Expected: FAIL because `AliyunEciClient` does not expose `delete_container_group()` and runtime cleanup is not implemented.

- [ ] **Step 3: Extend the ECI client contract**

In `apps/api/app/ai_company_api/services/aliyun_clients.py`, extend `AliyunEciClient`:

```python
class AliyunEciClient(Protocol):
    def create_container_group(
        self,
        request: AliyunEciCreateContainerGroupRequest,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def delete_container_group(
        self,
        *,
        region_id: str,
        container_group_id: str,
    ) -> None:
        raise NotImplementedError
```

Add SDK implementation:

```python
    def delete_container_group(
        self,
        *,
        region_id: str,
        container_group_id: str,
    ) -> None:
        from alibabacloud_eci20180808.client import Client
        from alibabacloud_eci20180808 import models as eci_models
        from alibabacloud_tea_openapi import models as openapi_models

        settings = require_aliyun_settings(
            provider_name="eci",
            required_names=(
                "access_key_id",
                "access_key_secret",
                "region_id",
            ),
            settings=self.settings,
        )
        client = Client(
            openapi_models.Config(
                access_key_id=settings.access_key_id,
                access_key_secret=settings.access_key_secret,
                region_id=region_id,
            )
        )
        client.delete_container_group(
            eci_models.DeleteContainerGroupRequest(
                region_id=region_id,
                container_group_id=container_group_id,
            )
        )
```

- [ ] **Step 4: Implement best-effort cleanup**

In `AliyunEciRuntimeProvider.submit()`, keep `runtime_job_id` after create and
call cleanup when later artifact seeding raises. Replace the single client-bundle
call in the existing `try` block with this structure while preserving the current
request fields:

```python
        runtime_job_id: str | None = None
        try:
            bundle = get_aliyun_client_bundle(settings)
            result = bundle.eci.create_container_group(
                AliyunEciCreateContainerGroupRequest(
                    region_id=settings.region_id or "",
                    cloud_run_id=submission.cloud_run_id,
                    container_group_name=container_group_name,
                    image=settings.eci_image or "",
                    vswitch_id=settings.eci_vswitch_id or "",
                    security_group_id=settings.eci_security_group_id or "",
                    cpu=settings.eci_cpu,
                    memory_gb=settings.eci_memory_gb,
                    restart_policy="Never",
                    client_token=_eci_client_token(submission.cloud_run_id),
                    environment=environment,
                    auto_create_eip=settings.eci_auto_create_eip,
                    eip_bandwidth=settings.eci_eip_bandwidth,
                )
            )
            runtime_job_id = result.get("container_group_id") or container_group_name
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
                                "queue_provider": submission.queue_provider,
                                "runtime_job_id": runtime_job_id,
                                "runtime_provider": self.name,
                                "status": "submitted",
                                "storage_provider": submission.storage_provider,
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
        except Exception:
            if runtime_job_id:
                try:
                    get_aliyun_client_bundle(settings).eci.delete_container_group(
                        region_id=settings.region_id or "",
                        container_group_id=runtime_job_id,
                    )
                except Exception:
                    pass
            raise RemoteRuntimeSubmissionError(
                "Cloud runtime provider aliyun_eci failed to submit container group"
            ) from None
```

The two `put_text()` calls keep their existing `ObjectStorageWrite` payloads from
the current implementation.

- [ ] **Step 5: Run cleanup test**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py::test_aliyun_eci_submission_cleans_up_when_oss_manifest_seed_fails -v
```

Expected: PASS.

---

### Task 3: OSS Ref Hardening

**Files:**
- Modify `apps/api/tests/test_cloud_object_storage.py`
- Modify `apps/api/app/ai_company_api/services/object_storage.py`

- [ ] **Step 1: Add failing OSS validation tests**

Append to `apps/api/tests/test_cloud_object_storage.py`:

```python
def test_aliyun_oss_storage_rejects_bucket_prefix_size_and_kind_mismatch(
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

        wrong_bucket = copy.copy(ref)
        wrong_bucket.uri = wrong_bucket.uri.replace(
            "oss://ai-scdc-dev-artifacts/",
            "oss://other-bucket/",
            1,
        )
        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, wrong_bucket)

        wrong_prefix = copy.copy(ref)
        wrong_prefix.uri = wrong_prefix.uri.replace(
            "/workspaces/dev_workspace/",
            "/other/dev_workspace/",
            1,
        )
        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, wrong_prefix)

        wrong_size = copy.copy(ref)
        wrong_size.size_bytes += 1
        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, wrong_size)

        wrong_kind = copy.copy(ref)
        wrong_kind.kind = "diff"
        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, wrong_kind)
```

Add `import copy` at the top if missing.

- [ ] **Step 2: Run the test and confirm failure**

Run:

```powershell
pytest apps/api/tests/test_cloud_object_storage.py::test_aliyun_oss_storage_rejects_bucket_prefix_size_and_kind_mismatch -v
```

Expected: FAIL on the wrong-kind case.

- [ ] **Step 3: Validate kind segment in OSS object key**

In `AliyunOssObjectStorageProvider.read_text()`, after the prefix check:

```python
        if f"/{ref.kind}/" not in f"/{object_key}":
            raise ObjectStorageReadError("Object storage reference kind mismatch")
```

Leave the existing hash and size checks after this new kind check.

- [ ] **Step 4: Run OSS storage tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_object_storage.py -v
```

Expected: PASS.

---

### Task 4: SDK Delete Request Regression

**Files:**
- Modify `apps/api/tests/test_aliyun_clients.py`

- [ ] **Step 1: Add SDK ECI delete test**

Write `test_sdk_aliyun_eci_client_delete_container_group_builds_request` beside
the existing ECI SDK create test. It monkeypatches
`alibabacloud_eci20180808.client.Client` with a fake class whose
`delete_container_group()` method records its request. It monkeypatches
`alibabacloud_eci20180808.models.DeleteContainerGroupRequest` with a simple class
that stores `region_id` and `container_group_id`. Then it calls:

```python
SdkAliyunEciClient(_aliyun_settings()).delete_container_group(
    region_id="cn-hangzhou",
    container_group_id="eci-cg-1",
)
```

Assert the captured request has `region_id == "cn-hangzhou"` and
`container_group_id == "eci-cg-1"`.

- [ ] **Step 2: Run Aliyun client tests**

Run:

```powershell
pytest apps/api/tests/test_aliyun_clients.py -v
```

Expected: PASS.

---

### Task 5: Focused Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run Phase 10C-H focused tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun or worker_uploads or artifact_ref or lease" -v
pytest apps/api/tests/test_cloud_object_storage.py -v
pytest apps/api/tests/test_aliyun_clients.py -v
```

Expected: PASS.

- [ ] **Step 2: Run formatting and secret scan**

Run:

```powershell
git diff --check
rg -n "AccessKey|ACCESS_KEY_SECRET|secret-value|ak-secret|very-secret-value|ALIYUN_ACCESS_KEY_SECRET" apps docs README.md
```

Expected: diff check passes. Secret scan only reports environment variable names,
README placeholders, plan examples, and fake test values.

- [ ] **Step 3: Commit**

Run:

```powershell
git add apps/api/app/ai_company_api/services/aliyun_clients.py apps/api/app/ai_company_api/services/remote_runtime.py apps/api/app/ai_company_api/services/object_storage.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_cloud_object_storage.py apps/api/tests/test_aliyun_clients.py docs/superpowers/specs/2026-06-04-phase-10c-h-aliyun-hardening-tests-design.md docs/superpowers/plans/2026-06-04-phase-10c-h-aliyun-hardening-tests.md
git commit -m "test: harden aliyun provider failure handling"
```

Expected: commit succeeds.
