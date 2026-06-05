# Phase 12C MNS Worker Pull Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Aliyun ECI workers to pull protected work from Aliyun MNS, claim the run with the short-lived callback token delivered in the MNS message, execute the run, and acknowledge the MNS delivery only after the API reaches a terminal run state.

**Architecture:** MNS becomes the secret-bearing delivery channel for cloud worker assignment. The API generates the callback token before MNS enqueue when a protected remote runtime is requested, stores only the token hash, sends the raw token in the MNS message body, accepts MNS delivery metadata during lease claim, and deletes the MNS message after successful terminal processing. API responses and logs never expose raw callback tokens or queue receipts.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, Aliyun MNS SDK seam, existing `ai_company_api.services.cloud_runner`, `cloud_queue_providers`, `aliyun_clients`, and `remote_worker` modules.

---

## File Structure

Create or update these files:

```text
apps/api/app/ai_company_api/services/aliyun_clients.py
apps/api/app/ai_company_api/services/cloud_queue_providers.py
apps/api/app/ai_company_api/services/cloud_runner.py
apps/api/app/ai_company_api/services/remote_worker.py
apps/api/app/ai_company_api/schemas/api.py
apps/api/tests/test_aliyun_clients.py
apps/api/tests/test_cloud_run_api.py
apps/api/tests/test_remote_worker.py
docs/architecture.md
README.md
STATUS.md
```

Do not create a new worker service package in this phase. The pull worker behavior belongs in the existing `remote_worker.py` module and remains callable from tests without launching an ECI container.

---

## Task 1: Add Aliyun MNS Receive/Delete Client Seam

**Purpose:** Give the API a tested SDK boundary for receiving and deleting MNS messages without tying queue-provider tests to the Aliyun SDK.

### Tests First

- [ ] Open `apps/api/tests/test_aliyun_clients.py`.
- [ ] Extend imports from `aliyun_clients.py` with:

```python
AliyunMnsDeleteMessageRequest,
AliyunMnsReceiveMessageRequest,
AliyunMnsReceivedMessage,
```

- [ ] Update `FakeMnsClient` in `test_aliyun_clients.py` so it implements:

```python
def receive_message(self, request: AliyunMnsReceiveMessageRequest) -> AliyunMnsReceivedMessage | None:
    self.receive_requests.append(request)
    return self.next_received_message

def delete_message(self, request: AliyunMnsDeleteMessageRequest) -> dict[str, str]:
    self.delete_requests.append(request)
    return {"deleted": "true"}
```

- [ ] Add `receive_requests`, `delete_requests`, and `next_received_message` fields to the fake constructor.
- [ ] Add this SDK receive test:

```python
def test_sdk_mns_receive_message_maps_sdk_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_complete_aliyun_env(monkeypatch)
    captured: dict[str, object] = {}

    class FakeSdkMessage:
        message_id = "msg-1"
        receipt_handle = "receipt-1"
        message_body = '{"cloud_run_id":"cloud_run_1"}'

    class FakeQueue:
        def receive_message(self, wait_seconds: int | None = None) -> FakeSdkMessage:
            captured["wait_seconds"] = wait_seconds
            return FakeSdkMessage()

    class FakeAccount:
        def __init__(self, endpoint: str, access_key_id: str, access_key_secret: str) -> None:
            captured["endpoint"] = endpoint
            captured["access_key_id"] = access_key_id
            captured["access_key_secret"] = access_key_secret

        def get_queue(self, queue_name: str) -> FakeQueue:
            captured["queue_name"] = queue_name
            return FakeQueue()

    account_module = types.ModuleType("mns.account")
    account_module.Account = FakeAccount
    monkeypatch.setitem(sys.modules, "mns.account", account_module)

    client = SdkAliyunMnsClient()
    result = client.receive_message(
        AliyunMnsReceiveMessageRequest(queue_name="phase12c-queue", wait_seconds=7)
    )

    assert result == AliyunMnsReceivedMessage(
        message_id="msg-1",
        receipt_handle="receipt-1",
        body='{"cloud_run_id":"cloud_run_1"}',
    )
    assert captured["queue_name"] == "phase12c-queue"
    assert captured["wait_seconds"] == 7
```

- [ ] Add this SDK delete test:

```python
def test_sdk_mns_delete_message_uses_receipt_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_complete_aliyun_env(monkeypatch)
    captured: dict[str, object] = {}

    class FakeQueue:
        def delete_message(self, receipt_handle: str) -> dict[str, str]:
            captured["receipt_handle"] = receipt_handle
            return {"ok": "true"}

    class FakeAccount:
        def __init__(self, endpoint: str, access_key_id: str, access_key_secret: str) -> None:
            captured["endpoint"] = endpoint

        def get_queue(self, queue_name: str) -> FakeQueue:
            captured["queue_name"] = queue_name
            return FakeQueue()

    account_module = types.ModuleType("mns.account")
    account_module.Account = FakeAccount
    monkeypatch.setitem(sys.modules, "mns.account", account_module)

    client = SdkAliyunMnsClient()
    result = client.delete_message(
        AliyunMnsDeleteMessageRequest(queue_name="phase12c-queue", receipt_handle="receipt-1")
    )

    assert result == {"ok": "true"}
    assert captured["queue_name"] == "phase12c-queue"
    assert captured["receipt_handle"] == "receipt-1"
```

- [ ] Run the focused failing tests:

```powershell
pytest apps/api/tests/test_aliyun_clients.py -q
```

Expected output before implementation: import or attribute failures for the new MNS receive/delete types and methods.

### Implementation

- [ ] Open `apps/api/app/ai_company_api/services/aliyun_clients.py`.
- [ ] Add dataclasses near `AliyunMnsSendMessageRequest`:

```python
@dataclass(frozen=True)
class AliyunMnsReceiveMessageRequest:
    queue_name: str
    wait_seconds: int = 3


@dataclass(frozen=True)
class AliyunMnsDeleteMessageRequest:
    queue_name: str
    receipt_handle: str


@dataclass(frozen=True)
class AliyunMnsReceivedMessage:
    message_id: str
    receipt_handle: str
    body: str
```

- [ ] Extend the MNS client protocol with:

```python
def receive_message(self, request: AliyunMnsReceiveMessageRequest) -> AliyunMnsReceivedMessage | None:
    pass

def delete_message(self, request: AliyunMnsDeleteMessageRequest) -> dict[str, str]:
    pass
```

- [ ] Implement `SdkAliyunMnsClient.receive_message()`:

```python
def receive_message(self, request: AliyunMnsReceiveMessageRequest) -> AliyunMnsReceivedMessage | None:
    from mns.account import Account

    settings = _require_aliyun_settings()
    queue = Account(
        settings.mns_endpoint,
        settings.access_key_id,
        settings.access_key_secret,
    ).get_queue(request.queue_name)
    result = queue.receive_message(wait_seconds=request.wait_seconds)
    if result is None:
        return None
    body = getattr(result, "message_body", None) or getattr(result, "body", "")
    return AliyunMnsReceivedMessage(
        message_id=str(getattr(result, "message_id", "")),
        receipt_handle=str(getattr(result, "receipt_handle", "")),
        body=str(body),
    )
```

- [ ] Implement `SdkAliyunMnsClient.delete_message()`:

```python
def delete_message(self, request: AliyunMnsDeleteMessageRequest) -> dict[str, str]:
    from mns.account import Account

    settings = _require_aliyun_settings()
    queue = Account(
        settings.mns_endpoint,
        settings.access_key_id,
        settings.access_key_secret,
    ).get_queue(request.queue_name)
    result = queue.delete_message(request.receipt_handle)
    return result if isinstance(result, dict) else {"deleted": "true"}
```

- [ ] Run:

```powershell
pytest apps/api/tests/test_aliyun_clients.py -q
```

Expected output: all tests in `test_aliyun_clients.py` pass.

- [ ] Commit:

```powershell
git add apps/api/app/ai_company_api/services/aliyun_clients.py apps/api/tests/test_aliyun_clients.py
git commit -m "Add MNS receive delete client seam"
```

---

## Task 2: Add Queue Provider Receive/Delete Contract and Message Parsing

**Purpose:** Represent MNS delivery as a typed internal queue message and keep raw token handling inside the queue-provider boundary.

### Tests First

- [ ] Open `apps/api/tests/test_cloud_run_api.py`.
- [ ] Extend `FakeAliyunMnsClient` with in-memory receive/delete behavior:

```python
self.received_messages: list[AliyunMnsReceivedMessage] = []
self.receive_requests: list[AliyunMnsReceiveMessageRequest] = []
self.delete_requests: list[AliyunMnsDeleteMessageRequest] = []
self.delete_error: Exception | None = None

def receive_message(self, request: AliyunMnsReceiveMessageRequest) -> AliyunMnsReceivedMessage | None:
    self.receive_requests.append(request)
    if not self.received_messages:
        return None
    return self.received_messages.pop(0)

def delete_message(self, request: AliyunMnsDeleteMessageRequest) -> dict[str, str]:
    self.delete_requests.append(request)
    if self.delete_error is not None:
        raise self.delete_error
    return {"deleted": "true"}
```

- [ ] Import `CloudQueueReceivedMessage` from `cloud_queue_providers.py` and the new MNS client dataclasses from `aliyun_clients.py`.
- [ ] Add a provider receive test:

```python
def test_aliyun_mns_queue_provider_receives_token_bearing_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_complete_aliyun_env(monkeypatch)
    fake_mns = FakeAliyunMnsClient()
    fake_mns.received_messages.append(
        AliyunMnsReceivedMessage(
            message_id="msg-1",
            receipt_handle="receipt-1",
            body=json.dumps(
                {
                    "workspace_id": "workspace-1",
                    "project_id": "project-1",
                    "task_id": "task-1",
                    "cloud_run_id": "cloud-run-1",
                    "queue_provider": "aliyun_mns",
                    "runtime_provider": "aliyun_eci",
                    "storage_provider": "aliyun_oss",
                    "worker_id": "worker-1",
                    "callback_token": "token-1",
                    "callback_token_expires_at": "2026-06-05T10:00:00+00:00",
                }
            ),
        )
    )
    set_aliyun_client_bundle_for_tests(
        AliyunClientBundle(mns=fake_mns, oss=FakeAliyunOssClient(), eci=FakeAliyunEciClient())
    )

    provider = AliyunMnsQueueProvider()
    received = provider.receive(wait_seconds=5)

    assert received == CloudQueueReceivedMessage(
        queue_message_id="msg-1",
        queue_receipt="receipt-1",
        workspace_id="workspace-1",
        project_id="project-1",
        task_id="task-1",
        cloud_run_id="cloud-run-1",
        queue_provider="aliyun_mns",
        runtime_provider="aliyun_eci",
        storage_provider="aliyun_oss",
        worker_id="worker-1",
        callback_token="token-1",
        callback_token_expires_at="2026-06-05T10:00:00+00:00",
    )
    assert fake_mns.receive_requests[0].wait_seconds == 5
```

- [ ] Add a no-message test:

```python
def test_aliyun_mns_queue_provider_receive_returns_none_for_empty_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_complete_aliyun_env(monkeypatch)
    fake_mns = FakeAliyunMnsClient()
    set_aliyun_client_bundle_for_tests(
        AliyunClientBundle(mns=fake_mns, oss=FakeAliyunOssClient(), eci=FakeAliyunEciClient())
    )

    assert AliyunMnsQueueProvider().receive(wait_seconds=1) is None
```

- [ ] Add a malformed-message test:

```python
def test_aliyun_mns_queue_provider_rejects_malformed_received_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_complete_aliyun_env(monkeypatch)
    fake_mns = FakeAliyunMnsClient()
    fake_mns.received_messages.append(
        AliyunMnsReceivedMessage(message_id="msg-1", receipt_handle="receipt-1", body='{"cloud_run_id":42}')
    )
    set_aliyun_client_bundle_for_tests(
        AliyunClientBundle(mns=fake_mns, oss=FakeAliyunOssClient(), eci=FakeAliyunEciClient())
    )

    with pytest.raises(CloudQueueProviderError, match="invalid MNS message"):
        AliyunMnsQueueProvider().receive(wait_seconds=1)
```

- [ ] Add a provider delete test:

```python
def test_aliyun_mns_queue_provider_deletes_received_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_complete_aliyun_env(monkeypatch)
    fake_mns = FakeAliyunMnsClient()
    set_aliyun_client_bundle_for_tests(
        AliyunClientBundle(mns=fake_mns, oss=FakeAliyunOssClient(), eci=FakeAliyunEciClient())
    )

    AliyunMnsQueueProvider().delete(queue_receipt="receipt-1")

    assert fake_mns.delete_requests[0].receipt_handle == "receipt-1"
```

- [ ] Run the focused failing tests:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -q -k "mns_queue_provider"
```

Expected output before implementation: missing class or method failures.

### Implementation

- [ ] Open `apps/api/app/ai_company_api/services/cloud_queue_providers.py`.
- [ ] Extend imports from `aliyun_clients.py` with:

```python
AliyunMnsDeleteMessageRequest,
AliyunMnsReceiveMessageRequest,
```

- [ ] Add optional token-bearing fields to `CloudQueueEnqueueRequest`:

```python
worker_id: str | None = None
callback_token: str | None = None
callback_token_expires_at: str | None = None
```

- [ ] Add `CloudQueueReceivedMessage`:

```python
@dataclass(frozen=True)
class CloudQueueReceivedMessage:
    queue_message_id: str
    queue_receipt: str
    workspace_id: str
    project_id: str
    task_id: str
    cloud_run_id: str
    queue_provider: str
    runtime_provider: str | None
    storage_provider: str
    worker_id: str
    callback_token: str
    callback_token_expires_at: str
```

- [ ] Extend `CloudQueueProvider` protocol with:

```python
def receive(self, *, wait_seconds: int = 3) -> CloudQueueReceivedMessage | None:
    pass

def delete(self, *, queue_receipt: str) -> None:
    pass
```

- [ ] In `RegisteredCloudQueueProvider`, add receive/delete methods that raise `CloudQueueProviderError` because `local_db` and `external_stub` do not expose provider-level MNS polling.
- [ ] In `AliyunMnsQueueProvider.enqueue()`, add token-bearing fields only when all protected-worker values are present:

```python
if request.worker_id and request.callback_token and request.callback_token_expires_at:
    message_body.update(
        {
            "worker_id": request.worker_id,
            "callback_token": request.callback_token,
            "callback_token_expires_at": request.callback_token_expires_at,
        }
    )
```

- [ ] Implement `AliyunMnsQueueProvider.receive()`:

```python
def receive(self, *, wait_seconds: int = 3) -> CloudQueueReceivedMessage | None:
    settings = get_aliyun_settings()
    if not settings.is_queue_ready:
        raise CloudQueueProviderError("Aliyun MNS queue is not configured")
    received = get_aliyun_client_bundle().mns.receive_message(
        AliyunMnsReceiveMessageRequest(queue_name=settings.mns_queue_name, wait_seconds=wait_seconds)
    )
    if received is None:
        return None
    try:
        payload = json.loads(received.body)
    except json.JSONDecodeError as exc:
        raise CloudQueueProviderError("invalid MNS message: body is not JSON") from exc
    return _parse_mns_received_message(received.message_id, received.receipt_handle, payload)
```

- [ ] Add `_parse_mns_received_message()` helper in the same module. Validate every required field is a non-empty string and reject malformed messages with `CloudQueueProviderError("invalid MNS message: <reason>")`. Required fields are:

```python
required_fields = (
    "workspace_id",
    "project_id",
    "task_id",
    "cloud_run_id",
    "queue_provider",
    "storage_provider",
    "worker_id",
    "callback_token",
    "callback_token_expires_at",
)
```

- [ ] Treat `runtime_provider` as optional but require it to be either `None` or a string when present.
- [ ] Implement `AliyunMnsQueueProvider.delete()`:

```python
def delete(self, *, queue_receipt: str) -> None:
    settings = get_aliyun_settings()
    if not settings.is_queue_ready:
        raise CloudQueueProviderError("Aliyun MNS queue is not configured")
    get_aliyun_client_bundle().mns.delete_message(
        AliyunMnsDeleteMessageRequest(queue_name=settings.mns_queue_name, receipt_handle=queue_receipt)
    )
```

- [ ] Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -q -k "mns_queue_provider"
pytest apps/api/tests/test_aliyun_clients.py -q
```

Expected output: all focused tests pass.

- [ ] Commit:

```powershell
git add apps/api/app/ai_company_api/services/cloud_queue_providers.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_aliyun_clients.py
git commit -m "Add MNS queue receive delete provider contract"
```

---

## Task 3: Generate Protected Callback Token Before MNS Enqueue

**Purpose:** Make the MNS message self-sufficient for protected remote worker pull while preserving the existing queue-only no-secret behavior.

### Tests First

- [ ] Open `apps/api/tests/test_cloud_run_api.py`.
- [ ] Keep the existing queue-provider enqueue test and strengthen it so queue-only `aliyun_mns` messages still do not contain secrets:

```python
assert "callback_token" not in payload
assert "worker_id" not in payload
assert "callback_token_expires_at" not in payload
```

- [ ] Add a protected ECI enqueue test:

```python
def test_aliyun_mns_enqueue_for_eci_includes_callback_token_without_api_leak(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_complete_aliyun_env(monkeypatch)
    fake_mns = FakeAliyunMnsClient()
    fake_eci = FakeAliyunEciClient()
    set_aliyun_client_bundle_for_tests(
        AliyunClientBundle(mns=fake_mns, oss=FakeAliyunOssClient(), eci=fake_eci)
    )
    workspace_id, project_id, task_id = _create_cloud_task(client)

    response = client.post(
        f"/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}/cloud-runs",
        json={
            "queue_provider": "aliyun_mns",
            "runtime_provider": "aliyun_eci",
            "storage_provider": "aliyun_oss",
        },
    )

    assert response.status_code == 201
    response_text = response.text
    queued_payload = json.loads(fake_mns.requests[0].body)
    eci_env = {
        env["key"]: env["value"]
        for env in fake_eci.create_requests[0].environment_variables
    }
    assert queued_payload["worker_id"] == eci_env["AI_SCDC_WORKER_ID"]
    assert queued_payload["callback_token"] == eci_env["AI_SCDC_CALLBACK_TOKEN"]
    assert queued_payload["callback_token_expires_at"]
    assert queued_payload["callback_token"] not in response_text
    assert "callback_token" not in response.json()
```

- [ ] Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns_enqueue"
```

Expected output before implementation: the new protected enqueue assertion fails because the MNS message does not contain callback token fields.

### Implementation

- [ ] Open `apps/api/app/ai_company_api/services/cloud_runner.py`.
- [ ] In `start_cloud_run()`, resolve the remote runtime provider before queue enqueue:

```python
remote_runtime_provider = get_remote_runtime_provider(data.runtime_provider)
```

- [ ] Before creating `CloudQueueEnqueueRequest`, compute protected worker credentials only when `remote_runtime_provider` is not `None`:

```python
worker_id: str | None = None
callback_token: str | None = None
callback_token_expires_at_text: str | None = None
if remote_runtime_provider is not None:
    worker_id = f"{data.runtime_provider}-{cloud_run.id}"
    callback_token = generate_callback_token()
    callback_token_expires_at = utcnow() + timedelta(minutes=15)
    cloud_run.worker_id = worker_id
    cloud_run.callback_token_hash = hash_callback_token(cloud_run.id, worker_id, callback_token)
    cloud_run.callback_token_expires_at = callback_token_expires_at
    cloud_run.callback_token_used_at = None
    callback_token_expires_at_text = callback_token_expires_at.isoformat()
```

- [ ] Pass the protected fields into `CloudQueueEnqueueRequest`:

```python
worker_id=worker_id,
callback_token=callback_token,
callback_token_expires_at=callback_token_expires_at_text,
```

- [ ] Reuse the same `worker_id`, `callback_token`, and `callback_token_expires_at` values when calling the ECI runtime provider. Remove the second duplicate token generation block so MNS and ECI receive the same token.
- [ ] Keep API read schemas unchanged so response bodies do not include raw token values.
- [ ] Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns_enqueue or aliyun_eci_runtime_submission"
```

Expected output: all focused enqueue/runtime tests pass.

- [ ] Commit:

```powershell
git add apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "Include protected worker token in MNS assignments"
```

---

## Task 4: Persist MNS Delivery Metadata Only After Successful Lease Claim

**Purpose:** Let pulled workers attach `queue_message_id` and `queue_receipt` to a claimed run while keeping receipts internal-only and rejecting wrong-token deliveries without storing receipt data.

### Tests First

- [ ] Open `apps/api/app/ai_company_api/schemas/api.py` and `apps/api/tests/test_cloud_run_api.py`.
- [ ] Add this API test:

```python
def test_aliyun_mns_claim_persists_receipt_without_exposing_it(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    _set_complete_aliyun_env(monkeypatch)
    fake_mns = FakeAliyunMnsClient()
    fake_eci = FakeAliyunEciClient()
    set_aliyun_client_bundle_for_tests(
        AliyunClientBundle(mns=fake_mns, oss=FakeAliyunOssClient(), eci=fake_eci)
    )
    workspace_id, project_id, task_id = _create_cloud_task(client)
    create_response = client.post(
        f"/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}/cloud-runs",
        json={
            "queue_provider": "aliyun_mns",
            "runtime_provider": "aliyun_eci",
            "storage_provider": "aliyun_oss",
        },
    )
    cloud_run_id = create_response.json()["id"]
    payload = json.loads(fake_mns.requests[0].body)

    claim_response = client.post(
        "/cloud-runs/leases",
        json={
            "worker_id": payload["worker_id"],
            "worker_kind": "aliyun_eci",
            "queue_provider": "aliyun_mns",
            "cloud_run_id": cloud_run_id,
            "callback_token": payload["callback_token"],
            "queue_message_id": "msg-1",
            "queue_receipt": "receipt-1",
        },
    )

    assert claim_response.status_code == 200
    assert "queue_receipt" not in claim_response.text
    db_session.expire_all()
    cloud_run = db_session.get(CloudRun, cloud_run_id)
    assert cloud_run.queue_message_id == "msg-1"
    assert cloud_run.queue_receipt == "receipt-1"
```

- [ ] Add wrong-token receipt rejection:

```python
def test_aliyun_mns_claim_with_wrong_token_does_not_store_receipt(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    _set_complete_aliyun_env(monkeypatch)
    fake_mns = FakeAliyunMnsClient()
    fake_eci = FakeAliyunEciClient()
    set_aliyun_client_bundle_for_tests(
        AliyunClientBundle(mns=fake_mns, oss=FakeAliyunOssClient(), eci=fake_eci)
    )
    workspace_id, project_id, task_id = _create_cloud_task(client)
    create_response = client.post(
        f"/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}/cloud-runs",
        json={
            "queue_provider": "aliyun_mns",
            "runtime_provider": "aliyun_eci",
            "storage_provider": "aliyun_oss",
        },
    )
    cloud_run_id = create_response.json()["id"]
    payload = json.loads(fake_mns.requests[0].body)

    claim_response = client.post(
        "/cloud-runs/leases",
        json={
            "worker_id": payload["worker_id"],
            "worker_kind": "aliyun_eci",
            "queue_provider": "aliyun_mns",
            "cloud_run_id": cloud_run_id,
            "callback_token": "wrong-token",
            "queue_message_id": "msg-1",
            "queue_receipt": "receipt-1",
        },
    )

    assert claim_response.status_code == 403
    db_session.expire_all()
    cloud_run = db_session.get(CloudRun, cloud_run_id)
    assert cloud_run.queue_message_id is None
    assert cloud_run.queue_receipt is None
```

- [ ] Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns_claim"
```

Expected output before implementation: request validation ignores or rejects new delivery metadata and DB assertions fail.

### Implementation

- [ ] In `apps/api/app/ai_company_api/schemas/api.py`, add these optional fields to `CloudRunLeaseCreate`:

```python
queue_message_id: str | None = None
queue_receipt: str | None = None
```

- [ ] Do not add `queue_receipt` to `CloudRunRead` or `CloudRunLeaseRead`.
- [ ] In the lease route handler, pass `queue_message_id` and `queue_receipt` into `claim_next_cloud_run_lease()`.
- [ ] In `cloud_runner.py`, extend `claim_next_cloud_run_lease()` signature:

```python
queue_message_id: str | None = None,
queue_receipt: str | None = None,
```

- [ ] Keep callback-token validation before `_claim_cloud_run_lease()`.
- [ ] After `_claim_cloud_run_lease()` succeeds, store delivery metadata only for `queue_provider == "aliyun_mns"` and only when both values are present:

```python
if queue_provider == "aliyun_mns" and queue_message_id and queue_receipt:
    cloud_run.queue_message_id = queue_message_id
    cloud_run.queue_receipt = queue_receipt
    cloud_run.external_status = "mns_message_claimed"
```

- [ ] Preserve existing `external_stub` behavior.
- [ ] Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns_claim or protected_worker_claim or external_stub"
```

Expected output: focused claim tests pass and existing external-stub lease tests still pass.

- [ ] Commit:

```powershell
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "Store MNS delivery metadata on protected lease claim"
```

---

## Task 5: Delete MNS Message After Terminal Completion

**Purpose:** Acknowledge pulled MNS work only after the API accepts the terminal worker callback. Delete failures are recorded without leaking receipts and without undoing the terminal run state.

### Tests First

- [ ] Open `apps/api/tests/test_cloud_run_api.py`.
- [ ] Add a helper in tests if one does not already exist:

```python
def _start_claimed_aliyun_mns_run(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> tuple[str, str, FakeAliyunMnsClient]:
    _set_complete_aliyun_env(monkeypatch)
    fake_mns = FakeAliyunMnsClient()
    fake_eci = FakeAliyunEciClient()
    set_aliyun_client_bundle_for_tests(
        AliyunClientBundle(mns=fake_mns, oss=FakeAliyunOssClient(), eci=fake_eci)
    )
    workspace_id, project_id, task_id = _create_cloud_task(client)
    create_response = client.post(
        f"/workspaces/{workspace_id}/projects/{project_id}/tasks/{task_id}/cloud-runs",
        json={
            "queue_provider": "aliyun_mns",
            "runtime_provider": "aliyun_eci",
            "storage_provider": "aliyun_oss",
        },
    )
    cloud_run_id = create_response.json()["id"]
    payload = json.loads(fake_mns.requests[0].body)
    claim_response = client.post(
        "/cloud-runs/leases",
        json={
            "worker_id": payload["worker_id"],
            "worker_kind": "aliyun_eci",
            "queue_provider": "aliyun_mns",
            "cloud_run_id": cloud_run_id,
            "callback_token": payload["callback_token"],
            "queue_message_id": "msg-1",
            "queue_receipt": "receipt-1",
        },
    )
    assert claim_response.status_code == 200
    lease_id = claim_response.json()["lease_id"]
    db_session.expire_all()
    return cloud_run_id, lease_id, fake_mns
```

- [ ] Add completion delete success test:

```python
def test_aliyun_mns_completion_deletes_receipt_and_clears_internal_receipt(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    cloud_run_id, lease_id, fake_mns = _start_claimed_aliyun_mns_run(client, monkeypatch, db_session)

    response = client.post(
        f"/cloud-runs/leases/{lease_id}/complete",
        json={"status": "succeeded", "result": {"summary": "ok"}},
    )

    assert response.status_code == 200
    assert fake_mns.delete_requests[0].receipt_handle == "receipt-1"
    assert "queue_receipt" not in response.text
    db_session.expire_all()
    cloud_run = db_session.get(CloudRun, cloud_run_id)
    assert cloud_run.status == "succeeded"
    assert cloud_run.queue_receipt is None
```

- [ ] Add delete failure redaction test:

```python
def test_aliyun_mns_completion_delete_failure_keeps_terminal_state_and_redacts_receipt(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    cloud_run_id, lease_id, fake_mns = _start_claimed_aliyun_mns_run(client, monkeypatch, db_session)
    fake_mns.delete_error = RuntimeError("delete failed for receipt-1")

    response = client.post(
        f"/cloud-runs/leases/{lease_id}/complete",
        json={"status": "succeeded", "result": {"summary": "ok"}},
    )

    assert response.status_code == 200
    assert "receipt-1" not in response.text
    db_session.expire_all()
    cloud_run = db_session.get(CloudRun, cloud_run_id)
    assert cloud_run.status == "succeeded"
    assert cloud_run.queue_receipt == "receipt-1"
    assert cloud_run.external_status == "mns_message_delete_failed"
```

- [ ] Add duplicate-delivery protection test:

```python
def test_aliyun_mns_duplicate_delivery_cannot_claim_second_active_lease(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    cloud_run_id, lease_id, fake_mns = _start_claimed_aliyun_mns_run(client, monkeypatch, db_session)
    payload = json.loads(fake_mns.requests[0].body)

    duplicate_response = client.post(
        "/cloud-runs/leases",
        json={
            "worker_id": payload["worker_id"],
            "worker_kind": "aliyun_eci",
            "queue_provider": "aliyun_mns",
            "cloud_run_id": cloud_run_id,
            "callback_token": payload["callback_token"],
            "queue_message_id": "msg-2",
            "queue_receipt": "receipt-2",
        },
    )

    assert duplicate_response.status_code == 409
    db_session.expire_all()
    cloud_run = db_session.get(CloudRun, cloud_run_id)
    assert cloud_run.queue_message_id == "msg-1"
    assert cloud_run.queue_receipt == "receipt-1"
```

- [ ] Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns_completion or duplicate_delivery"
```

Expected output before implementation: completion does not call provider delete and receipt remains uncleared.

### Implementation

- [ ] Open `apps/api/app/ai_company_api/services/cloud_runner.py`.
- [ ] Add a helper near other queue cleanup helpers:

```python
def _delete_mns_queue_receipt_after_terminal(cloud_run: CloudRun) -> None:
    if cloud_run.queue_provider != "aliyun_mns" or not cloud_run.queue_receipt:
        return
    try:
        get_cloud_queue_provider("aliyun_mns").delete(queue_receipt=cloud_run.queue_receipt)
    except Exception:
        cloud_run.external_status = "mns_message_delete_failed"
        return
    cloud_run.queue_receipt = None
    cloud_run.external_status = "mns_message_deleted"
```

- [ ] Call `_delete_mns_queue_receipt_after_terminal(cloud_run)` after the worker completion payload has been accepted and `cloud_run.status` has been set to a terminal state.
- [ ] Preserve existing `external_stub` queue receipt clearing behavior by leaving its branch in place or routing it through a separate helper.
- [ ] Do not include exception text, receipt handle, raw message body, or callback token in API responses or persisted external status.
- [ ] Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns_completion or duplicate_delivery or external_stub"
```

Expected output: focused completion and regression tests pass.

- [ ] Commit:

```powershell
git add apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "Acknowledge MNS messages after terminal completion"
```

---

## Task 6: Add Remote Worker MNS Pull Mode

**Purpose:** Let a worker started with queue credentials and no assigned `AI_SCDC_CLOUD_RUN_ID` receive one MNS message, claim it with the embedded token and delivery receipt, run the existing worker flow, and exit cleanly when no message is available.

### Tests First

- [ ] Open `apps/api/tests/test_remote_worker.py`.
- [ ] Extend `RemoteWorkerConfig` assertions in existing tests with `queue_message_id is None` and `queue_receipt is None`.
- [ ] Add this HTTP claim metadata test:

```python
def test_http_remote_worker_client_sends_queue_delivery_metadata_on_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_request(method: str, url: str, **kwargs: object) -> FakeResponse:
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return FakeResponse(200, {"lease_id": "lease-1", "cloud_run": {"id": "cloud-run-1"}})

    monkeypatch.setattr(remote_worker.requests, "request", fake_request)
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud-run-1",
        worker_id="worker-1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="token-1",
        queue_message_id="msg-1",
        queue_receipt="receipt-1",
    )

    HttpRemoteWorkerClient(config).claim()

    assert captured["json"]["queue_message_id"] == "msg-1"
    assert captured["json"]["queue_receipt"] == "receipt-1"
```

- [ ] Add a fake queue consumer:

```python
class FakeQueueConsumer:
    def __init__(self, message: RemoteWorkerQueueMessage | None) -> None:
        self.message = message
        self.receive_calls = 0
        self.delete_receipts: list[str] = []

    def receive(self, *, wait_seconds: int = 3) -> RemoteWorkerQueueMessage | None:
        self.receive_calls += 1
        return self.message

    def delete(self, *, queue_receipt: str) -> None:
        self.delete_receipts.append(queue_receipt)
```

- [ ] Add config resolution test for pull mode:

```python
def test_config_from_env_pulls_mns_message_when_cloud_run_id_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_SCDC_API_BASE_URL", "https://api.example.test")
    monkeypatch.delenv("AI_SCDC_CLOUD_RUN_ID", raising=False)
    monkeypatch.delenv("AI_SCDC_WORKER_ID", raising=False)
    monkeypatch.delenv("AI_SCDC_CALLBACK_TOKEN", raising=False)
    monkeypatch.setenv("AI_SCDC_QUEUE_PROVIDER", "aliyun_mns")
    monkeypatch.setenv("AI_SCDC_STORAGE_PROVIDER", "aliyun_oss")
    consumer = FakeQueueConsumer(
        RemoteWorkerQueueMessage(
            cloud_run_id="cloud-run-1",
            worker_id="worker-1",
            callback_token="token-1",
            queue_message_id="msg-1",
            queue_receipt="receipt-1",
            storage_provider="aliyun_oss",
        )
    )

    config = config_from_env(queue_consumer=consumer)

    assert config.cloud_run_id == "cloud-run-1"
    assert config.worker_id == "worker-1"
    assert config.callback_token == "token-1"
    assert config.queue_message_id == "msg-1"
    assert config.queue_receipt == "receipt-1"
    assert consumer.receive_calls == 1
```

- [ ] Add no-work test:

```python
def test_run_remote_worker_from_env_exits_successfully_when_mns_queue_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_SCDC_API_BASE_URL", "https://api.example.test")
    monkeypatch.delenv("AI_SCDC_CLOUD_RUN_ID", raising=False)
    monkeypatch.delenv("AI_SCDC_WORKER_ID", raising=False)
    monkeypatch.delenv("AI_SCDC_CALLBACK_TOKEN", raising=False)
    monkeypatch.setenv("AI_SCDC_QUEUE_PROVIDER", "aliyun_mns")
    consumer = FakeQueueConsumer(None)

    result = run_remote_worker_from_env(queue_consumer=consumer)

    assert result == {"status": "no_work"}
    assert consumer.receive_calls == 1
```

- [ ] Add delete-after-success test:

```python
def test_run_remote_worker_from_env_deletes_mns_message_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_SCDC_API_BASE_URL", "https://api.example.test")
    monkeypatch.delenv("AI_SCDC_CLOUD_RUN_ID", raising=False)
    monkeypatch.delenv("AI_SCDC_WORKER_ID", raising=False)
    monkeypatch.delenv("AI_SCDC_CALLBACK_TOKEN", raising=False)
    monkeypatch.setenv("AI_SCDC_QUEUE_PROVIDER", "aliyun_mns")
    consumer = FakeQueueConsumer(
        RemoteWorkerQueueMessage(
            cloud_run_id="cloud-run-1",
            worker_id="worker-1",
            callback_token="token-1",
            queue_message_id="msg-1",
            queue_receipt="receipt-1",
            storage_provider="aliyun_oss",
        )
    )
    fake_client = FakeWorkerClient()

    result = run_remote_worker_from_env(queue_consumer=consumer, worker_client=fake_client)

    assert result["status"] == "succeeded"
    assert consumer.delete_receipts == ["receipt-1"]
```

- [ ] Run:

```powershell
pytest apps/api/tests/test_remote_worker.py -q
```

Expected output before implementation: missing queue-message types and pull-mode helpers.

### Implementation

- [ ] Open `apps/api/app/ai_company_api/services/remote_worker.py`.
- [ ] Add fields to `RemoteWorkerConfig`:

```python
queue_message_id: str | None = None
queue_receipt: str | None = None
```

- [ ] Add a queue message dataclass:

```python
@dataclass(frozen=True)
class RemoteWorkerQueueMessage:
    cloud_run_id: str
    worker_id: str
    callback_token: str
    queue_message_id: str
    queue_receipt: str
    storage_provider: str
```

- [ ] Add a protocol for testable queue consumption:

```python
class RemoteWorkerQueueConsumer(Protocol):
    def receive(self, *, wait_seconds: int = 3) -> RemoteWorkerQueueMessage | None:
        pass

    def delete(self, *, queue_receipt: str) -> None:
        pass
```

- [ ] Implement an Aliyun MNS consumer using `get_cloud_queue_provider("aliyun_mns")`:

```python
class AliyunMnsRemoteWorkerQueueConsumer:
    def receive(self, *, wait_seconds: int = 3) -> RemoteWorkerQueueMessage | None:
        received = get_cloud_queue_provider("aliyun_mns").receive(wait_seconds=wait_seconds)
        if received is None:
            return None
        return RemoteWorkerQueueMessage(
            cloud_run_id=received.cloud_run_id,
            worker_id=received.worker_id,
            callback_token=received.callback_token,
            queue_message_id=received.queue_message_id,
            queue_receipt=received.queue_receipt,
            storage_provider=received.storage_provider,
        )

    def delete(self, *, queue_receipt: str) -> None:
        get_cloud_queue_provider("aliyun_mns").delete(queue_receipt=queue_receipt)
```

- [ ] Update `HttpRemoteWorkerClient.claim()` so it includes `queue_message_id` and `queue_receipt` only when both config values are present.
- [ ] Update `config_from_env()` signature:

```python
def config_from_env(
    *,
    queue_consumer: RemoteWorkerQueueConsumer | None = None,
) -> RemoteWorkerConfig:
```

- [ ] Keep assigned-run mode unchanged when `AI_SCDC_CLOUD_RUN_ID` is present. It must still require `AI_SCDC_WORKER_ID` and `AI_SCDC_CALLBACK_TOKEN`.
- [ ] When `AI_SCDC_CLOUD_RUN_ID` is missing and `AI_SCDC_QUEUE_PROVIDER == "aliyun_mns"`, receive one message:

```python
consumer = queue_consumer or AliyunMnsRemoteWorkerQueueConsumer()
message = consumer.receive(wait_seconds=int(os.getenv("AI_SCDC_MNS_WAIT_SECONDS", "3")))
if message is None:
    raise NoRemoteWorkAvailable()
return RemoteWorkerConfig(
    api_base_url=api_base_url,
    cloud_run_id=message.cloud_run_id,
    worker_id=message.worker_id,
    queue_provider="aliyun_mns",
    storage_provider=message.storage_provider,
    callback_token=message.callback_token,
    queue_message_id=message.queue_message_id,
    queue_receipt=message.queue_receipt,
)
```

- [ ] Add `NoRemoteWorkAvailable(RuntimeError)` in the same module.
- [ ] Add `run_remote_worker_from_env()`:

```python
def run_remote_worker_from_env(
    *,
    queue_consumer: RemoteWorkerQueueConsumer | None = None,
    worker_client: RemoteWorkerClient | None = None,
) -> dict[str, Any]:
    try:
        config = config_from_env(queue_consumer=queue_consumer)
    except NoRemoteWorkAvailable:
        return {"status": "no_work"}
    client = worker_client or HttpRemoteWorkerClient(config)
    result = run_remote_worker_once(config=config, client=client)
    if (
        queue_consumer is not None
        and config.queue_provider == "aliyun_mns"
        and config.queue_receipt
        and result.get("status") in {"succeeded", "failed", "cancelled"}
    ):
        queue_consumer.delete(queue_receipt=config.queue_receipt)
    return result
```

- [ ] Update `main()` so it calls `run_remote_worker_from_env()` instead of calling `config_from_env()` and `run_remote_worker_once()` separately.
- [ ] Do not log MNS message body, callback token, or receipt handle.
- [ ] Run:

```powershell
pytest apps/api/tests/test_remote_worker.py -q
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns_claim or aliyun_mns_completion"
```

Expected output: all focused worker and MNS API tests pass.

- [ ] Commit:

```powershell
git add apps/api/app/ai_company_api/services/remote_worker.py apps/api/tests/test_remote_worker.py
git commit -m "Add remote worker MNS pull mode"
```

---

## Task 7: Update Docs, Run Full Verification, and Prepare Handoff

**Purpose:** Document the new pull-worker contract and verify the phase across API, desktop, typecheck, and whitespace checks.

### Documentation

- [ ] Update `docs/architecture.md` with a short Phase 12C section:

```markdown
### Phase 12C: Aliyun MNS worker pull

Aliyun MNS assignments for protected ECI runs include worker identity, the short-lived callback token, cloud run identity, storage provider, and MNS delivery metadata. The API stores only the callback token hash. Workers claim the run through `/cloud-runs/leases` with the token and the MNS message id/receipt. The receipt is stored internally and is deleted from MNS after terminal completion.
```

- [ ] Update `README.md` Aliyun worker notes with these environment variables:

```text
AI_SCDC_QUEUE_PROVIDER=aliyun_mns
AI_SCDC_STORAGE_PROVIDER=aliyun_oss
AI_SCDC_MNS_WAIT_SECONDS=3
```

- [ ] State that assigned-run mode still supports:

```text
AI_SCDC_CLOUD_RUN_ID
AI_SCDC_WORKER_ID
AI_SCDC_CALLBACK_TOKEN
```

- [ ] Update `STATUS.md` with Phase 12C completed scope and verification commands.

### Verification

- [ ] Run focused API tests:

```powershell
pytest apps/api/tests/test_aliyun_clients.py -q
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns or protected_worker_claim or external_stub"
pytest apps/api/tests/test_remote_worker.py -q
```

Expected output: all focused tests pass.

- [ ] Run full API tests:

```powershell
pytest apps/api/tests -v
```

Expected output: all tests pass. The existing Starlette/httpx warning can remain if it is the same known warning from Phase 12B.

- [ ] Run desktop tests:

```powershell
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

Expected output: `34 passed`.

- [ ] Run typecheck:

```powershell
pnpm typecheck
```

Expected output: typecheck succeeds.

- [ ] Run whitespace check:

```powershell
git diff --check
```

Expected output: no output.

- [ ] Inspect final diff:

```powershell
git status --short
git diff --stat master..HEAD
git log --oneline --decorate -8
```

Expected output: branch contains the Phase 12C design commit, this implementation-plan commit, and the implementation commits from the tasks above.

- [ ] Commit docs:

```powershell
git add docs/architecture.md README.md STATUS.md
git commit -m "Document MNS worker pull operations"
```

---

## Acceptance Criteria

- [ ] Queue-only `aliyun_mns` enqueue messages continue to exclude callback tokens.
- [ ] Protected `aliyun_mns` plus `aliyun_eci` runs enqueue MNS messages containing the raw callback token, worker id, and callback-token expiry.
- [ ] API persists only the callback token hash and never returns raw callback tokens.
- [ ] Worker lease claim accepts `queue_message_id` and `queue_receipt` after callback-token validation succeeds.
- [ ] Wrong callback token claims do not persist MNS delivery metadata.
- [ ] API read schemas do not expose `queue_receipt`.
- [ ] Completion deletes the MNS message and clears internal receipt on delete success.
- [ ] Delete failure keeps terminal state, keeps receipt for operator recovery, and records only redacted status.
- [ ] Duplicate MNS deliveries cannot create a second active lease for the same run.
- [ ] Remote worker assigned-run mode continues to work with explicit `AI_SCDC_CLOUD_RUN_ID`, `AI_SCDC_WORKER_ID`, and `AI_SCDC_CALLBACK_TOKEN`.
- [ ] Remote worker pull mode exits successfully with `{"status": "no_work"}` when MNS has no message.
- [ ] Remote worker pull mode passes queue metadata into lease claim and deletes the MNS message after successful terminal worker execution.
- [ ] Full verification commands in Task 7 pass.

## Implementation Notes

- Keep `queue_receipt` internal-only. It may exist in request payloads from workers and database fields, but it must not appear in API read models, response text assertions, docs examples with real values, or logs.
- The raw callback token exists only in the MNS message body, ECI environment variables, and worker claim request. It is not stored raw in the API database.
- Do not log MNS message bodies. Log only event names, provider names, and redacted status strings.
- MNS message delete is an acknowledgement. Perform it after terminal state persistence is accepted by the API path.
- Preserve existing `local_db`, `external_stub`, `aliyun_oss`, and `aliyun_eci` tests while adding Phase 12C behavior.
