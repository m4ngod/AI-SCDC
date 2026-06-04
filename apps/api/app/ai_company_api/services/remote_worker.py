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
    callback_token: str


class RemoteWorkerClient(Protocol):
    def claim(self, config: RemoteWorkerConfig) -> dict[str, Any]:
        ...

    def heartbeat(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
    ) -> dict[str, Any]:
        ...

    def payload(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
    ) -> dict[str, Any]:
        ...

    def upload_artifact(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
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
        callback_token: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class RemoteWorkerCheckout(Protocol):
    def checkout(self, payload: dict[str, Any]) -> str:
        ...


class RemoteWorkerCommandRunner(Protocol):
    def run(self, payload: dict[str, Any], repo_path: str) -> dict[str, Any]:
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
                "callback_token": config.callback_token,
                "lease_seconds": 300,
            },
        )

    def heartbeat(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/heartbeat",
            {
                "worker_id": worker_id,
                "callback_token": callback_token,
                "lease_seconds": 300,
            },
        )

    def payload(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/payload",
            {
                "worker_id": worker_id,
                "callback_token": callback_token,
            },
        )

    def upload_artifact(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
        *,
        kind: str,
        content: str,
        content_type: str,
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/artifacts",
            {
                "worker_id": worker_id,
                "callback_token": callback_token,
                "kind": kind,
                "content": content,
                "content_type": content_type,
            },
        )

    def complete(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/complete",
            {
                "worker_id": worker_id,
                "callback_token": callback_token,
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


def _redact_text(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in sorted((secret for secret in secrets if secret), key=len, reverse=True):
        redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _redacted_command_result(
    result: dict[str, Any],
    secrets: list[str],
) -> dict[str, Any]:
    return {
        "command": _redact_text(result.get("command", ""), secrets),
        "exit_code": result.get("exit_code"),
        "stdout": _redact_text(result.get("stdout", ""), secrets),
        "stderr": _redact_text(result.get("stderr", ""), secrets),
        "duration_ms": result.get("duration_ms", 0),
        "timed_out": result.get("timed_out", False),
    }


def _redacted_optional_text(value: Any, secrets: list[str]) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return value


def _redacted_string_list(values: list[Any], secrets: list[str]) -> list[Any]:
    return [
        _redact_text(value, secrets) if isinstance(value, str) else value
        for value in values
    ]


@dataclass
class RemoteWorkerExecutor:
    client: RemoteWorkerClient
    checkout: RemoteWorkerCheckout
    command_runner: RemoteWorkerCommandRunner

    def run_once(self, config: RemoteWorkerConfig) -> dict[str, Any]:
        lease = self.client.claim(config)
        lease_id = lease["lease_id"]
        payload = self.client.payload(lease_id, config.worker_id, config.callback_token)
        first_heartbeat = self.client.heartbeat(
            lease_id,
            config.worker_id,
            config.callback_token,
        )
        if first_heartbeat.get("cancel_requested") is True:
            return self._complete_cancelled(config, lease_id)
        repo_path = self.checkout.checkout(payload)
        execution = self.command_runner.run(payload, repo_path)
        second_heartbeat = self.client.heartbeat(
            lease_id,
            config.worker_id,
            config.callback_token,
        )
        if second_heartbeat.get("cancel_requested") is True:
            execution = {
                **execution,
                "status": "failed",
                "failure_reason": "cancelled",
                "test_result": execution.get("test_result", "not_run"),
            }
        secrets = [
            payload.get("clone_token", ""),
            config.callback_token,
            *[str(value) for value in payload.get("env", {}).values()],
        ]
        artifact_refs = self._upload_artifacts(config, lease_id, execution, secrets)
        completion = self._completion_payload(execution, artifact_refs, secrets)
        return self.client.complete(
            lease_id,
            config.worker_id,
            config.callback_token,
            {"result": completion},
        )

    def _complete_cancelled(
        self,
        config: RemoteWorkerConfig,
        lease_id: str,
    ) -> dict[str, Any]:
        return self.client.complete(
            lease_id,
            config.worker_id,
            config.callback_token,
            {
                "result": {
                    "status": "failed",
                    "runner_kind": "aliyun_eci",
                    "base_sha": None,
                    "head_sha": None,
                    "worktree_ref": None,
                    "summary": "Remote worker cancelled before checkout.",
                    "files_changed": [],
                    "tests_run": [],
                    "test_result": "not_run",
                    "risks": [],
                    "diff_text": "",
                    "artifact_refs": [],
                    "command_results": [],
                    "test_command_results": [],
                    "failure_reason": "cancelled",
                }
            },
        )

    def _upload_artifacts(
        self,
        config: RemoteWorkerConfig,
        lease_id: str,
        execution: dict[str, Any],
        secrets: list[str],
    ) -> list[dict[str, Any]]:
        command_results = [
            _redacted_command_result(result, secrets)
            for result in execution.get("command_results", [])
        ]
        test_results = [
            _redacted_command_result(result, secrets)
            for result in execution.get("test_command_results", [])
        ]
        uploads = [
            (
                "diff",
                _redact_text(execution.get("diff_text") or "", secrets),
                "text/x-diff",
            ),
            (
                "command_result",
                json.dumps(command_results, sort_keys=True),
                "application/json",
            ),
            (
                "test_result",
                json.dumps(test_results, sort_keys=True),
                "application/json",
            ),
            (
                "log",
                _redact_text(execution.get("summary", ""), secrets),
                "text/plain",
            ),
        ]
        artifact_refs: list[dict[str, Any]] = []
        for kind, content, content_type in uploads:
            artifact_refs.append(
                self.client.upload_artifact(
                    lease_id,
                    config.worker_id,
                    config.callback_token,
                    kind=kind,
                    content=content,
                    content_type=content_type,
                )
            )
        manifest = {
            "cloud_run_id": config.cloud_run_id,
            "artifacts": artifact_refs,
            "status": execution.get("status"),
            "failure_reason": _redacted_optional_text(
                execution.get("failure_reason"),
                secrets,
            ),
        }
        artifact_refs.append(
            self.client.upload_artifact(
                lease_id,
                config.worker_id,
                config.callback_token,
                kind="manifest",
                content=json.dumps(manifest, sort_keys=True),
                content_type="application/json",
            )
        )
        return artifact_refs

    def _completion_payload(
        self,
        execution: dict[str, Any],
        artifact_refs: list[dict[str, Any]],
        secrets: list[str],
    ) -> dict[str, Any]:
        return {
            "status": execution.get("status", "failed"),
            "runner_kind": execution.get("runner_kind", "aliyun_eci"),
            "base_sha": execution.get("base_sha"),
            "head_sha": execution.get("head_sha"),
            "worktree_ref": _redacted_optional_text(
                execution.get("worktree_ref"),
                secrets,
            ),
            "summary": _redact_text(execution.get("summary", ""), secrets),
            "files_changed": execution.get("files_changed", []),
            "tests_run": _redacted_string_list(
                execution.get("tests_run", []),
                secrets,
            ),
            "test_result": execution.get("test_result", "not_run"),
            "risks": _redacted_string_list(execution.get("risks", []), secrets),
            "diff_text": "",
            "artifact_refs": artifact_refs,
            "command_results": [
                _redacted_command_result(result, secrets)
                for result in execution.get("command_results", [])
            ],
            "test_command_results": [
                _redacted_command_result(result, secrets)
                for result in execution.get("test_command_results", [])
            ],
            "failure_reason": _redacted_optional_text(
                execution.get("failure_reason"),
                secrets,
            ),
        }


def run_remote_worker_once(
    config: RemoteWorkerConfig,
    *,
    client: RemoteWorkerClient | None = None,
    checkout: RemoteWorkerCheckout | None = None,
    command_runner: RemoteWorkerCommandRunner | None = None,
) -> dict[str, Any]:
    resolved_client = client or HttpRemoteWorkerClient(config.api_base_url)
    if checkout is not None and command_runner is not None:
        return RemoteWorkerExecutor(
            client=resolved_client,
            checkout=checkout,
            command_runner=command_runner,
        ).run_once(config)
    lease = resolved_client.claim(config)
    lease_id = lease["lease_id"]
    resolved_client.heartbeat(lease_id, config.worker_id, config.callback_token)
    diff_text = _deterministic_diff(config.cloud_run_id)
    diff_ref = resolved_client.upload_artifact(
        lease_id,
        config.worker_id,
        config.callback_token,
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
    return resolved_client.complete(
        lease_id,
        config.worker_id,
        config.callback_token,
        completion,
    )


def config_from_env() -> RemoteWorkerConfig:
    return RemoteWorkerConfig(
        api_base_url=_required_env("AI_SCDC_API_BASE_URL"),
        cloud_run_id=_required_env("AI_SCDC_CLOUD_RUN_ID"),
        worker_id=_required_env("AI_SCDC_WORKER_ID"),
        queue_provider=os.getenv("AI_SCDC_QUEUE_PROVIDER", "aliyun_mns"),
        storage_provider=os.getenv("AI_SCDC_STORAGE_PROVIDER", "aliyun_oss"),
        callback_token=_required_env("AI_SCDC_CALLBACK_TOKEN"),
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
