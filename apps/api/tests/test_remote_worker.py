from ai_company_api.services.remote_worker import (
    RemoteWorkerConfig,
    config_from_env,
    run_remote_worker_once,
)


class FakeWorkerClient:
    def __init__(self) -> None:
        self.claimed_config: RemoteWorkerConfig | None = None
        self.heartbeats: list[dict] = []
        self.uploaded: list[dict] = []
        self.completed: dict | None = None

    def claim(self, config: RemoteWorkerConfig) -> dict:
        self.claimed_config = config
        return {
            "lease_id": "lease_1",
            "cloud_run": {
                "id": config.cloud_run_id,
                "task_id": "task_1",
                "status": "running",
            },
        }

    def heartbeat(self, lease_id: str, worker_id: str) -> dict:
        self.heartbeats.append({"lease_id": lease_id, "worker_id": worker_id})
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
        self.completed = {
            "lease_id": lease_id,
            "worker_id": worker_id,
            **result,
        }
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
    assert client.claimed_config == config
    assert client.heartbeats == [{"lease_id": "lease_1", "worker_id": "worker_1"}]
    assert client.uploaded[0]["kind"] == "diff"
    assert client.uploaded[0]["content_type"] == "text/x-diff"
    assert client.completed is not None
    completion = client.completed["result"]
    assert completion["artifact_refs"] == client.uploaded
    assert completion["diff_text"] == ""
    assert completion["runner_kind"] == "aliyun_eci"
    assert completion["worktree_ref"] == "aliyun-eci://cloud_run_1"
    assert completion["files_changed"] == ["AI_SCDC_ALIYUN_ECI.md"]


def test_remote_worker_config_from_env_reads_provider_contract(monkeypatch) -> None:
    monkeypatch.setenv("AI_SCDC_API_BASE_URL", "https://api.example.test/")
    monkeypatch.setenv("AI_SCDC_CLOUD_RUN_ID", "cloud_run_1")
    monkeypatch.setenv("AI_SCDC_WORKER_ID", "worker_1")
    monkeypatch.setenv("AI_SCDC_QUEUE_PROVIDER", "aliyun_mns")
    monkeypatch.setenv("AI_SCDC_STORAGE_PROVIDER", "aliyun_oss")

    config = config_from_env()

    assert config == RemoteWorkerConfig(
        api_base_url="https://api.example.test/",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
    )
