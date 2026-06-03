from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Protocol
from urllib import request as urllib_request


@dataclass(frozen=True)
class RemoteWorkerConfig:
    api_base_url: str
    cloud_run_id: str
    worker_id: str
    queue_provider: str
    storage_provider: str


class RemoteWorkerClient(Protocol):
    def claim(self, config: RemoteWorkerConfig) -> dict[str, Any]:
        ...

    def heartbeat(self, lease_id: str, worker_id: str) -> dict[str, Any]:
        ...

    def upload_artifact(
        self,
        lease_id: str,
        worker_id: str,
        *,
        kind: str,
        content: str,
        content_type: str,
    ) -> dict[str, Any]:
        ...

    def complete(
        self,
        lease_id: str,
        worker_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class HttpRemoteWorkerClient:
    def __init__(self, api_base_url: str) -> None:
        self._api_base_url = api_base_url.rstrip("/")

    def claim(self, config: RemoteWorkerConfig) -> dict[str, Any]:
        return self._post_json(
            "/cloud-run-worker/leases",
            {
                "worker_id": config.worker_id,
                "worker_kind": "aliyun_eci",
                "queue_provider": config.queue_provider,
                "cloud_run_id": config.cloud_run_id,
                "lease_seconds": 300,
            },
        )

    def heartbeat(self, lease_id: str, worker_id: str) -> dict[str, Any]:
        return self._post_json(
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
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/artifacts",
            {
                "worker_id": worker_id,
                "kind": kind,
                "content": content,
                "content_type": content_type,
            },
        )

    def complete(
        self,
        lease_id: str,
        worker_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/complete",
            {
                "worker_id": worker_id,
                "result": result["result"],
            },
        )

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(
            f"{self._api_base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def run_remote_worker_once(
    config: RemoteWorkerConfig,
    *,
    client: RemoteWorkerClient | None = None,
) -> dict[str, Any]:
    resolved_client = client or HttpRemoteWorkerClient(config.api_base_url)
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
            "summary": (
                "Aliyun ECI remote worker produced a deterministic smoke patch."
            ),
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
