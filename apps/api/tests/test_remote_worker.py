from ai_company_api.services.remote_worker import (
    HttpRemoteWorkerClient,
    RemoteWorkerConfig,
    config_from_env,
    run_remote_worker_once,
)


class FakeWorkerClient:
    def __init__(self, *, cancel_on_second_heartbeat: bool = False) -> None:
        self.claimed_config: RemoteWorkerConfig | None = None
        self.heartbeats: list[dict] = []
        self.uploaded: list[dict] = []
        self.completed: dict | None = None
        self.cancel_on_second_heartbeat = cancel_on_second_heartbeat

    def claim(self, config: RemoteWorkerConfig) -> dict:
        self.claimed_config = config
        return {"lease_id": "lease_1", "cloud_run": {"id": config.cloud_run_id}}

    def payload(self, lease_id: str, worker_id: str, callback_token: str) -> dict:
        return {
            "cloud_run_id": "cloud_run_1",
            "task_id": "task_1",
            "title": "Run remote worker",
            "description": "Create a real patch",
            "repo_url": "https://github.com/example/demo",
            "github_owner": "example",
            "github_repo": "demo",
            "base_branch": "main",
            "head_branch": "ai-scdc/cloud-run",
            "allowed_paths": ["AI_SCDC_CLOUD_RUN.md"],
            "required_tests": ["pytest -q"],
            "patch_command": {
                "key": "patch",
                "label": "Patch",
                "command": "python patch.py",
                "timeout_seconds": 120,
            },
            "test_commands": [
                {
                    "key": "test",
                    "label": "Test",
                    "command": "pytest -q",
                    "timeout_seconds": 300,
                }
            ],
            "env": {"SAFE_REMOTE_ENV": "env-secret-value"},
            "network_enabled": True,
            "clone_token": "ghp_private_clone_token1234",
        }

    def heartbeat(self, lease_id: str, worker_id: str, callback_token: str) -> dict:
        self.heartbeats.append(
            {
                "lease_id": lease_id,
                "worker_id": worker_id,
                "callback_token": callback_token,
            }
        )
        return {
            "lease_id": lease_id,
            "cancel_requested": self.cancel_on_second_heartbeat
            and len(self.heartbeats) >= 2,
        }

    def upload_artifact(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
        *,
        kind: str,
        content: str,
        content_type: str,
    ) -> dict:
        ref = {
            "kind": kind,
            "uri": f"oss://bucket/{kind}-{len(self.uploaded)}.txt",
            "sha256": "a" * 64,
            "size_bytes": len(content.encode("utf-8")),
            "content_type": content_type,
        }
        self.uploaded.append({"ref": ref, "content": content, "token": "[redacted]"})
        return ref

    def complete(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
        result: dict,
    ) -> dict:
        self.completed = {
            "lease_id": lease_id,
            "worker_id": worker_id,
            "callback_token": callback_token,
            **result,
        }
        return {"cloud_run": {"status": result["result"]["status"]}}


class FakeCheckout:
    def checkout(self, payload: dict) -> str:
        return "/tmp/repo"


class FakeCommandRunner:
    def run(self, payload: dict, repo_path: str) -> dict:
        return {
            "status": "patch_ready",
            "runner_kind": "aliyun_eci",
            "base_sha": "base123",
            "head_sha": "head456",
            "worktree_ref": "remote-worker://cloud_run_1",
            "summary": "Remote worker produced a patch artifact.",
            "files_changed": ["AI_SCDC_CLOUD_RUN.md"],
            "tests_run": ["pytest -q"],
            "test_result": "passed",
            "risks": [],
            "diff_text": (
                "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n"
                "+ok\n"
            ),
            "command_results": [
                {
                    "command": "python patch.py ghp_private_clone_token1234",
                    "exit_code": 0,
                    "stdout": "patched env-secret-value",
                    "stderr": "",
                    "duration_ms": 12,
                    "timed_out": False,
                }
            ],
            "test_command_results": [
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "stdout": "passed",
                    "stderr": "",
                    "duration_ms": 15,
                    "timed_out": False,
                }
            ],
            "failure_reason": None,
        }


def test_remote_worker_uploads_diff_ref_and_completes() -> None:
    client = FakeWorkerClient()
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="callback-token-1",
    )

    result = run_remote_worker_once(config, client=client)

    assert result["cloud_run"]["status"] == "patch_ready"
    assert client.claimed_config == config
    assert client.heartbeats == [
        {
            "lease_id": "lease_1",
            "worker_id": "worker_1",
            "callback_token": "callback-token-1",
        }
    ]
    assert client.uploaded[0]["ref"]["kind"] == "diff"
    assert client.uploaded[0]["ref"]["content_type"] == "text/x-diff"
    assert client.completed is not None
    completion = client.completed["result"]
    assert completion["artifact_refs"] == [upload["ref"] for upload in client.uploaded]
    assert completion["diff_text"] == ""
    assert completion["runner_kind"] == "aliyun_eci"
    assert completion["worktree_ref"] == "aliyun-eci://cloud_run_1"
    assert completion["files_changed"] == ["AI_SCDC_ALIYUN_ECI.md"]


def test_remote_worker_fetches_payload_runs_components_uploads_artifacts_and_completes() -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerExecutor

    client = FakeWorkerClient()
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="callback-token-1",
    )
    executor = RemoteWorkerExecutor(
        client=client,
        checkout=FakeCheckout(),
        command_runner=FakeCommandRunner(),
    )

    result = executor.run_once(config)

    assert result["cloud_run"]["status"] == "patch_ready"
    assert len(client.heartbeats) == 2
    uploaded_kinds = [upload["ref"]["kind"] for upload in client.uploaded]
    assert uploaded_kinds == ["diff", "command_result", "test_result", "log", "manifest"]
    assert client.completed is not None
    completion = client.completed["result"]
    assert completion["diff_text"] == ""
    assert completion["artifact_refs"][0]["kind"] == "diff"
    assert completion["command_results"][0]["command"] == "python patch.py [redacted]"
    assert "ghp_private_clone_token1234" not in str(client.uploaded)
    assert "env-secret-value" not in str(client.uploaded)
    assert "callback-token-1" not in str(client.uploaded)


def test_remote_worker_config_from_env_reads_provider_contract(monkeypatch) -> None:
    monkeypatch.setenv("AI_SCDC_API_BASE_URL", "https://api.example.test/")
    monkeypatch.setenv("AI_SCDC_CLOUD_RUN_ID", "cloud_run_1")
    monkeypatch.setenv("AI_SCDC_WORKER_ID", "worker_1")
    monkeypatch.setenv("AI_SCDC_QUEUE_PROVIDER", "aliyun_mns")
    monkeypatch.setenv("AI_SCDC_STORAGE_PROVIDER", "aliyun_oss")
    monkeypatch.setenv("AI_SCDC_CALLBACK_TOKEN", "callback-token-1")

    config = config_from_env()

    assert config == RemoteWorkerConfig(
        api_base_url="https://api.example.test/",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="callback-token-1",
    )


def test_http_remote_worker_client_sends_callback_token() -> None:
    class RecordingHttpRemoteWorkerClient(HttpRemoteWorkerClient):
        def __init__(self) -> None:
            super().__init__("https://api.example.test")
            self.requests: list[tuple[str, dict]] = []

        def _post_json(self, path: str, payload: dict) -> dict:
            self.requests.append((path, payload))
            if path == "/cloud-run-worker/leases":
                return {"lease_id": "lease_1"}
            return {"ok": True}

    client = RecordingHttpRemoteWorkerClient()
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="callback-token-1",
    )

    client.claim(config)
    client.heartbeat("lease_1", config.worker_id, config.callback_token)
    client.upload_artifact(
        "lease_1",
        config.worker_id,
        config.callback_token,
        kind="diff",
        content="diff",
        content_type="text/x-diff",
    )
    client.complete(
        "lease_1",
        config.worker_id,
        config.callback_token,
        {"result": {"status": "patch_ready"}},
    )

    assert [payload["callback_token"] for _path, payload in client.requests] == [
        "callback-token-1",
        "callback-token-1",
        "callback-token-1",
        "callback-token-1",
    ]


def test_http_remote_worker_client_fetches_payload_with_callback_token() -> None:
    class RecordingHttpRemoteWorkerClient(HttpRemoteWorkerClient):
        def __init__(self) -> None:
            super().__init__("https://api.example.test")
            self.requests: list[tuple[str, dict]] = []

        def _post_json(self, path: str, payload: dict) -> dict:
            self.requests.append((path, payload))
            return {
                "cloud_run_id": "cloud_run_1",
                "task_id": "task_1",
                "title": "Task",
                "description": "Description",
                "repo_url": "https://github.com/example/demo",
                "github_owner": "example",
                "github_repo": "demo",
                "base_branch": "main",
                "head_branch": "ai-scdc/cloud-run",
                "allowed_paths": ["AI_SCDC_CLOUD_RUN.md"],
                "required_tests": ["pytest -q"],
                "patch_command": {
                    "key": "patch",
                    "label": "Patch",
                    "command": "python patch.py",
                    "timeout_seconds": 120,
                },
                "test_commands": [],
                "env": {},
                "network_enabled": True,
                "clone_token": "ghp_private_clone_token1234",
            }

    client = RecordingHttpRemoteWorkerClient()
    payload = client.payload("lease_1", "worker_1", "callback-token-1")

    assert payload["clone_token"] == "ghp_private_clone_token1234"
    assert client.requests == [
        (
            "/cloud-run-worker/leases/lease_1/payload",
            {"worker_id": "worker_1", "callback_token": "callback-token-1"},
        )
    ]
