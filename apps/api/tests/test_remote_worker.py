from pathlib import Path
import sys

import pytest

from ai_company_api.services.remote_worker import (
    HttpRemoteWorkerClient,
    RemoteWorkerConfig,
    config_from_env,
    run_deterministic_remote_worker_once,
    run_remote_worker_once,
)


class FakeWorkerClient:
    def __init__(
        self,
        *,
        cancel_on_first_heartbeat: bool = False,
        cancel_on_second_heartbeat: bool = False,
    ) -> None:
        self.claimed_config: RemoteWorkerConfig | None = None
        self.heartbeats: list[dict] = []
        self.uploaded: list[dict] = []
        self.completed: dict | None = None
        self.cancel_on_first_heartbeat = cancel_on_first_heartbeat
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
            "cancel_requested": self.cancel_on_first_heartbeat
            or (self.cancel_on_second_heartbeat and len(self.heartbeats) >= 2),
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
            "callback_token": "[redacted]",
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


class SecretBearingCommandRunner:
    def run(self, payload: dict, repo_path: str) -> dict:
        clone_token = payload["clone_token"]
        callback_token = "callback-token-1"
        env_secret = payload["env"]["SAFE_REMOTE_ENV"]
        return {
            "status": "failed",
            "runner_kind": "aliyun_eci",
            "base_sha": "base123",
            "head_sha": "head456",
            "worktree_ref": (
                f"remote-worker://{clone_token}/{callback_token}/{env_secret}"
            ),
            "summary": "Remote worker failed while collecting artifacts.",
            "files_changed": [
                f"AI_SCDC_CLOUD_RUN_{clone_token}.md",
                f"AI_SCDC_CLOUD_RUN_{callback_token}.md",
                f"AI_SCDC_CLOUD_RUN_{env_secret}.md",
            ],
            "tests_run": [
                f"pytest -q --token {clone_token}",
                f"echo {callback_token}",
                f"echo {env_secret}",
            ],
            "test_result": f"failed {clone_token} {callback_token} {env_secret}",
            "risks": [
                f"clone risk {clone_token}",
                f"callback risk {callback_token}",
                f"env risk {env_secret}",
            ],
            "diff_text": (
                "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n"
                f"+{clone_token}\n"
                f"+{callback_token}\n"
                f"+{env_secret}\n"
            ),
            "command_results": [
                {
                    "command": f"python patch.py {clone_token}",
                    "exit_code": 1,
                    "stdout": f"patched {env_secret}",
                    "stderr": f"failed {callback_token}",
                    "duration_ms": 12,
                    "timed_out": False,
                }
            ],
            "test_command_results": [
                {
                    "command": f"pytest -q --token {callback_token}",
                    "exit_code": 1,
                    "stdout": f"test stdout {clone_token}",
                    "stderr": f"test stderr {env_secret}",
                    "duration_ms": 15,
                    "timed_out": False,
                }
            ],
            "failure_reason": f"failed with {clone_token} {callback_token} {env_secret}",
        }


class FailingCheckout:
    def checkout(self, payload: dict) -> str:
        raise RuntimeError("repo_checkout_failed")


class ExplodingCommandRunner:
    def run(self, payload: dict, repo_path: str) -> dict:
        raise ValueError("unexpected runner crash")


def fake_completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return type(
        "FakeCompletedProcess",
        (),
        {"returncode": returncode, "stdout": stdout, "stderr": stderr},
    )()


def test_remote_worker_dockerfile_installs_git() -> None:
    dockerfile = Path("apps/api/Dockerfile.remote-worker").read_text(
        encoding="utf-8",
    )

    assert "apt-get update" in dockerfile
    assert "apt-get install" in dockerfile
    assert "git" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile


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

    result = run_deterministic_remote_worker_once(config, client=client)

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


def test_run_remote_worker_once_uses_real_executor_when_components_are_supplied() -> None:
    client = FakeWorkerClient()
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="callback-token-1",
    )

    result = run_remote_worker_once(
        config,
        client=client,
        checkout=FakeCheckout(),
        command_runner=FakeCommandRunner(),
    )

    assert result["cloud_run"]["status"] == "patch_ready"
    assert client.completed is not None
    assert client.completed["result"]["artifact_refs"][0]["kind"] == "diff"


def test_run_remote_worker_once_uses_real_executor_with_default_components(
    monkeypatch,
) -> None:
    from ai_company_api.services import remote_worker

    created: dict[str, object] = {}

    class RecordingHttpRemoteWorkerClient(FakeWorkerClient):
        def __init__(self, api_base_url: str) -> None:
            super().__init__()
            self.api_base_url = api_base_url
            created["client"] = self

    class RecordingCheckout:
        def __init__(self) -> None:
            created["checkout"] = self

        def checkout(self, payload: dict) -> str:
            return "/tmp/repo"

    class RecordingCommandRunner:
        def __init__(self) -> None:
            created["command_runner"] = self

        def run(self, payload: dict, repo_path: str) -> dict:
            return FakeCommandRunner().run(payload, repo_path)

    monkeypatch.setattr(
        remote_worker,
        "HttpRemoteWorkerClient",
        RecordingHttpRemoteWorkerClient,
    )
    monkeypatch.setattr(remote_worker, "RemoteWorkerGitCheckout", RecordingCheckout)
    monkeypatch.setattr(
        remote_worker,
        "RemoteWorkerCommandRunnerImpl",
        RecordingCommandRunner,
    )
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="callback-token-1",
    )

    result = remote_worker.run_remote_worker_once(config)

    assert result["cloud_run"]["status"] == "patch_ready"
    assert isinstance(created["client"], RecordingHttpRemoteWorkerClient)
    assert isinstance(created["checkout"], RecordingCheckout)
    assert isinstance(created["command_runner"], RecordingCommandRunner)
    client = created["client"]
    assert isinstance(client, RecordingHttpRemoteWorkerClient)
    assert client.api_base_url == "https://api.example.test"
    assert len(client.heartbeats) == 2
    assert client.completed is not None
    assert client.completed["result"]["worktree_ref"] == "remote-worker://cloud_run_1"


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


def test_remote_worker_git_checkout_uses_askpass_without_token_in_command(
    tmp_path: Path,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerGitCheckout

    calls: list[dict] = []

    def fake_run(args, cwd=None, env=None, timeout=None):
        calls.append({"args": args, "cwd": cwd, "env": env, "timeout": timeout})
        return type(
            "FakeCompletedProcess",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()

    checkout = RemoteWorkerGitCheckout(
        workspace_root=tmp_path,
        process_run=fake_run,
    )
    payload = {
        "cloud_run_id": "cloud_run_1",
        "repo_url": "https://github.com/example/demo",
        "base_branch": "main",
        "head_branch": "ai-scdc/cloud-run",
        "clone_token": "ghp_private_clone_token1234",
    }

    repo_path = checkout.checkout(payload)

    clone_call = calls[0]
    assert Path(repo_path).name == "repo"
    assert clone_call["args"] == [
        "git",
        "clone",
        "--",
        "https://github.com/example/demo",
        ".",
    ]
    assert clone_call["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_ASKPASS" in clone_call["env"]
    assert "ghp_private_clone_token1234" not in str(calls)


@pytest.mark.parametrize(
    "cloud_run_id",
    [".", "..", "", "../outside", "nested/../../outside"],
)
def test_remote_worker_git_checkout_keeps_cloud_run_workspace_inside_root(
    tmp_path: Path,
    cloud_run_id: str,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerGitCheckout

    workspace_root = tmp_path / "parent" / "workspace"
    workspace_root.mkdir(parents=True)
    outside_workspace = tmp_path / "parent" / "outside.txt"
    inside_workspace = workspace_root / "inside.txt"
    outside_workspace.write_text("outside", encoding="utf-8")
    inside_workspace.write_text("inside", encoding="utf-8")

    def fake_run(args, cwd=None, env=None, timeout=None):
        return fake_completed()

    checkout = RemoteWorkerGitCheckout(
        workspace_root=workspace_root,
        process_run=fake_run,
    )

    repo_path = Path(
        checkout.checkout(
            {
                "cloud_run_id": cloud_run_id,
                "repo_url": "https://github.com/example/demo",
                "base_branch": "main",
                "head_branch": "ai-scdc/cloud-run",
                "clone_token": "ghp_private_clone_token1234",
            }
        )
    )

    assert repo_path.resolve().is_relative_to(workspace_root.resolve())
    assert repo_path.name == "repo"
    assert workspace_root.exists()
    assert outside_workspace.read_text(encoding="utf-8") == "outside"
    assert inside_workspace.read_text(encoding="utf-8") == "inside"


def test_remote_worker_git_checkout_sanitizes_management_secret_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerGitCheckout

    monkeypatch.setenv("AI_SCDC_CALLBACK_TOKEN", "callback-token-1")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "aliyun-secret-1")
    monkeypatch.setenv("PATH", "platform-path")
    calls: list[dict] = []

    def fake_run(args, cwd=None, env=None, timeout=None):
        calls.append({"args": args, "cwd": cwd, "env": env, "timeout": timeout})
        return fake_completed()

    checkout = RemoteWorkerGitCheckout(
        workspace_root=tmp_path,
        process_run=fake_run,
    )

    checkout.checkout(
        {
            "cloud_run_id": "cloud_run_1",
            "repo_url": "https://github.com/example/demo",
            "base_branch": "main",
            "head_branch": "ai-scdc/cloud-run",
            "clone_token": "ghp_private_clone_token1234",
        }
    )

    env = calls[0]["env"]
    assert env["PATH"] == "platform-path"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_ASKPASS" in env
    assert "AI_SCDC_CALLBACK_TOKEN" not in env
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in env


def test_remote_worker_git_checkout_cleans_credentials_on_success(
    tmp_path: Path,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerGitCheckout

    def fake_run(args, cwd=None, env=None, timeout=None):
        return fake_completed()

    checkout = RemoteWorkerGitCheckout(
        workspace_root=tmp_path,
        process_run=fake_run,
    )

    checkout.checkout(
        {
            "cloud_run_id": "cloud_run_1",
            "repo_url": "https://github.com/example/demo",
            "base_branch": "main",
            "head_branch": "ai-scdc/cloud-run",
            "clone_token": "ghp_private_clone_token1234",
        }
    )

    assert list(tmp_path.rglob(".git-credentials-*")) == []
    assert "ghp_private_clone_token1234" not in str(
        [path.read_text(encoding="utf-8") for path in tmp_path.rglob("*") if path.is_file()]
    )


def test_remote_worker_git_checkout_cleans_credentials_on_clone_failure(
    tmp_path: Path,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerGitCheckout

    def fake_run(args, cwd=None, env=None, timeout=None):
        return fake_completed(returncode=1, stderr="clone failed")

    checkout = RemoteWorkerGitCheckout(
        workspace_root=tmp_path,
        process_run=fake_run,
    )

    with pytest.raises(RuntimeError, match="repo_checkout_failed"):
        checkout.checkout(
            {
                "cloud_run_id": "cloud_run_1",
                "repo_url": "https://github.com/example/demo",
                "base_branch": "main",
                "head_branch": "ai-scdc/cloud-run",
                "clone_token": "ghp_private_clone_token1234",
            }
        )

    assert list(tmp_path.rglob(".git-credentials-*")) == []
    assert "ghp_private_clone_token1234" not in str(
        [path.read_text(encoding="utf-8") for path in tmp_path.rglob("*") if path.is_file()]
    )


def test_remote_worker_git_checkout_cleans_credentials_when_setup_fails(
    tmp_path: Path,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerGitCheckout

    class PartiallyFailingCheckout(RemoteWorkerGitCheckout):
        def _create_askpass_files(self, run_dir: Path, clone_token: str) -> Path:
            credentials_dir = run_dir / ".git-credentials-partial"
            credentials_dir.mkdir()
            (credentials_dir / "credential").write_text(
                clone_token,
                encoding="utf-8",
            )
            raise RuntimeError("askpass setup failed")

    checkout = PartiallyFailingCheckout(
        workspace_root=tmp_path,
        process_run=lambda *args, **kwargs: fake_completed(),
    )

    with pytest.raises(RuntimeError, match="askpass setup failed"):
        checkout.checkout(
            {
                "cloud_run_id": "cloud_run_1",
                "repo_url": "https://github.com/example/demo",
                "base_branch": "main",
                "head_branch": "ai-scdc/cloud-run",
                "clone_token": "ghp_private_clone_token1234",
            }
        )

    assert list(tmp_path.rglob(".git-credentials-*")) == []


def test_remote_worker_command_runner_maps_patch_and_test_results(
    tmp_path: Path,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerCommandRunnerImpl

    calls: list[dict] = []

    def fake_run(args, cwd=None, env=None, timeout=None):
        calls.append({"args": args, "cwd": cwd, "env": env, "timeout": timeout})
        command = args[-1]
        stdout = "ok"
        if "diff --name-only" in command:
            stdout = "AI_SCDC_CLOUD_RUN.md\n"
        elif "git diff --no-ext-diff" in command:
            stdout = (
                "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n"
                "+ok\n"
            )
        elif "rev-parse" in command:
            stdout = "abc123\n"
        return type(
            "FakeCompletedProcess",
            (),
            {"returncode": 0, "stdout": stdout, "stderr": ""},
        )()

    runner = RemoteWorkerCommandRunnerImpl(process_run=fake_run)
    payload = {
        "cloud_run_id": "cloud_run_1",
        "base_branch": "main",
        "patch_command": {
            "key": "patch",
            "command": "python patch.py",
            "timeout_seconds": 120,
        },
        "test_commands": [
            {
                "key": "test",
                "command": "pytest -q",
                "timeout_seconds": 300,
            }
        ],
        "allowed_paths": ["AI_SCDC_CLOUD_RUN.md"],
        "env": {"SAFE_REMOTE_ENV": "value"},
    }

    result = runner.run(payload, str(tmp_path))

    assert result["status"] == "patch_ready"
    assert result["files_changed"] == ["AI_SCDC_CLOUD_RUN.md"]
    assert result["test_result"] == "passed"
    assert result["diff_text"].startswith("diff --git")
    assert [entry["command"] for entry in result["command_results"]] == [
        "python patch.py",
        "git add -N .",
        "git diff --name-only",
        "git diff --no-ext-diff",
        "git rev-parse origin/main",
        "git rev-parse HEAD",
    ]


def test_remote_worker_command_runner_sanitizes_process_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerCommandRunnerImpl

    monkeypatch.setenv("AI_SCDC_CALLBACK_TOKEN", "callback-token-1")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "aliyun-secret-1")
    monkeypatch.setenv("PATH", "platform-path")
    calls: list[dict] = []

    def fake_run(args, cwd=None, env=None, timeout=None):
        calls.append({"args": args, "cwd": cwd, "env": env, "timeout": timeout})
        command = args[-1]
        stdout = "ok"
        if "diff --name-only" in command:
            stdout = "AI_SCDC_CLOUD_RUN.md\n"
        elif "git diff --no-ext-diff" in command:
            stdout = "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+ok\n"
        elif "rev-parse" in command:
            stdout = "abc123\n"
        return fake_completed(stdout=stdout)

    runner = RemoteWorkerCommandRunnerImpl(process_run=fake_run)

    runner.run(
        {
            "cloud_run_id": "cloud_run_1",
            "base_branch": "main",
            "patch_command": {
                "key": "patch",
                "command": "python patch.py",
                "timeout_seconds": 120,
            },
            "test_commands": [
                {
                    "key": "test",
                    "command": "pytest -q",
                    "timeout_seconds": 300,
                }
            ],
            "allowed_paths": ["AI_SCDC_CLOUD_RUN.md"],
            "env": {"SAFE_REMOTE_ENV": "value"},
        },
        str(tmp_path),
    )

    for call in calls:
        env = call["env"]
        assert env["PATH"] == "platform-path"
        assert env["SAFE_REMOTE_ENV"] == "value"
        assert "AI_SCDC_CALLBACK_TOKEN" not in env
        assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in env


def test_remote_worker_command_runner_allows_changed_files_by_glob(
    tmp_path: Path,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerCommandRunnerImpl

    def fake_run(args, cwd=None, env=None, timeout=None):
        command = args[-1]
        stdout = "ok"
        if "diff --name-only" in command:
            stdout = "apps/api/app.py\n"
        elif "git diff --no-ext-diff" in command:
            stdout = "diff --git a/apps/api/app.py b/apps/api/app.py\n+ok\n"
        elif "rev-parse" in command:
            stdout = "abc123\n"
        return fake_completed(stdout=stdout)

    runner = RemoteWorkerCommandRunnerImpl(process_run=fake_run)

    result = runner.run(
        {
            "cloud_run_id": "cloud_run_1",
            "base_branch": "main",
            "patch_command": {"key": "patch", "command": "python patch.py"},
            "test_commands": [],
            "allowed_paths": ["apps/api/**"],
            "env": {},
        },
        str(tmp_path),
    )

    assert result["status"] == "patch_ready"
    assert result["files_changed"] == ["apps/api/app.py"]
    assert result["failure_reason"] is None


def test_remote_worker_command_runner_rejects_empty_allowed_paths(
    tmp_path: Path,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerCommandRunnerImpl

    def fake_run(args, cwd=None, env=None, timeout=None):
        command = args[-1]
        stdout = "ok"
        if "diff --name-only" in command:
            stdout = "README.md\n"
        elif "git diff --no-ext-diff" in command:
            stdout = "diff --git a/README.md b/README.md\n+ok\n"
        elif "rev-parse" in command:
            stdout = "abc123\n"
        return fake_completed(stdout=stdout)

    runner = RemoteWorkerCommandRunnerImpl(process_run=fake_run)

    result = runner.run(
        {
            "cloud_run_id": "cloud_run_1",
            "base_branch": "main",
            "patch_command": {"key": "patch", "command": "python patch.py"},
            "test_commands": [],
            "allowed_paths": [],
            "env": {},
        },
        str(tmp_path),
    )

    assert result["status"] == "failed"
    assert result["failure_reason"] == "artifact_capture_failed"
    assert result["files_changed"] == ["README.md"]


def test_remote_worker_allowed_paths_does_not_import_ai_company_worker(
    monkeypatch,
) -> None:
    from ai_company_api.services import remote_worker

    class BlockWorkerImport:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "ai_company_worker" or fullname.startswith(
                "ai_company_worker."
            ):
                raise AssertionError(f"unexpected import: {fullname}")
            return None

    for module_name in list(sys.modules):
        if module_name == "ai_company_worker" or module_name.startswith(
            "ai_company_worker."
        ):
            monkeypatch.delitem(sys.modules, module_name, raising=False)
    blocker = BlockWorkerImport()
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])

    remote_worker._ensure_files_allowed(["apps/api/app.py"], ["apps/api/**"])


def test_remote_worker_allowed_paths_reject_unsafe_patterns() -> None:
    from ai_company_api.services import remote_worker

    with pytest.raises(RuntimeError, match="outside allowed_paths"):
        remote_worker._ensure_files_allowed(
            ["apps/api/app.py"],
            ["", "/apps/api/**", "C:/repo/apps/api/**", "../apps/api/**"],
        )


def test_remote_worker_command_runner_quotes_base_branch_for_rev_parse(
    tmp_path: Path,
) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerCommandRunnerImpl

    calls: list[dict] = []

    def fake_run(args, cwd=None, env=None, timeout=None):
        calls.append({"args": args, "cwd": cwd, "env": env, "timeout": timeout})
        command = args[-1]
        stdout = "ok"
        if "diff --name-only" in command:
            stdout = "AI_SCDC_CLOUD_RUN.md\n"
        elif "git diff --no-ext-diff" in command:
            stdout = "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+ok\n"
        elif "rev-parse" in command:
            stdout = "abc123\n"
        return fake_completed(stdout=stdout)

    runner = RemoteWorkerCommandRunnerImpl(process_run=fake_run)

    runner.run(
        {
            "cloud_run_id": "cloud_run_1",
            "base_branch": "main; echo injected",
            "patch_command": {"key": "patch", "command": "python patch.py"},
            "test_commands": [],
            "allowed_paths": ["AI_SCDC_CLOUD_RUN.md"],
            "env": {},
        },
        str(tmp_path),
    )

    rev_parse_commands = [
        call["args"][-1] for call in calls if "rev-parse" in call["args"][-1]
    ]
    assert "git rev-parse 'origin/main; echo injected'" in rev_parse_commands


def test_remote_worker_command_runner_cancels_before_tests_with_real_runner(
    tmp_path: Path,
) -> None:
    from ai_company_api.services.remote_worker import (
        RemoteWorkerCommandRunnerImpl,
        RemoteWorkerExecutor,
    )

    calls: list[str] = []

    def fake_run(args, cwd=None, env=None, timeout=None):
        command = args[-1]
        calls.append(command)
        stdout = "ok"
        if "diff --name-only" in command:
            stdout = "AI_SCDC_CLOUD_RUN.md\n"
        elif "git diff --no-ext-diff" in command:
            stdout = (
                "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n"
                "+ok\n"
            )
        elif "rev-parse" in command:
            stdout = "abc123\n"
        return fake_completed(stdout=stdout)

    client = FakeWorkerClient(cancel_on_second_heartbeat=True)
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
        command_runner=RemoteWorkerCommandRunnerImpl(process_run=fake_run),
    )

    result = executor.run_once(config)

    assert "pytest -q" not in calls
    assert len(client.heartbeats) == 2
    assert result["cloud_run"]["status"] == "failed"
    assert client.completed is not None
    completion = client.completed["result"]
    assert completion["failure_reason"] == "cancelled"
    assert completion["test_result"] == "not_run"
    assert completion["files_changed"] == ["AI_SCDC_CLOUD_RUN.md"]


def test_remote_worker_first_heartbeat_cancel_uploads_failed_execution_artifacts() -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerExecutor

    class SpyCheckout:
        def __init__(self) -> None:
            self.called = False

        def checkout(self, payload: dict) -> str:
            self.called = True
            return "/tmp/repo"

    class SpyCommandRunner:
        def __init__(self) -> None:
            self.called = False

        def run(self, payload: dict, repo_path: str) -> dict:
            self.called = True
            return FakeCommandRunner().run(payload, repo_path)

    client = FakeWorkerClient(cancel_on_first_heartbeat=True)
    checkout = SpyCheckout()
    command_runner = SpyCommandRunner()
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
        checkout=checkout,
        command_runner=command_runner,
    )

    result = executor.run_once(config)

    assert result["cloud_run"]["status"] == "failed"
    assert checkout.called is False
    assert command_runner.called is False
    assert [upload["ref"]["kind"] for upload in client.uploaded] == [
        "diff",
        "command_result",
        "test_result",
        "log",
        "manifest",
    ]
    assert client.completed is not None
    completion = client.completed["result"]
    assert completion["failure_reason"] == "cancelled"
    assert completion["artifact_refs"] == [upload["ref"] for upload in client.uploaded]
    assert completion["diff_text"] == ""


def test_remote_worker_stops_at_command_boundary_when_cancel_requested() -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerExecutor

    client = FakeWorkerClient(cancel_on_second_heartbeat=True)
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

    assert result["cloud_run"]["status"] == "failed"
    assert client.completed is not None
    assert client.completed["result"]["failure_reason"] == "cancelled"


def test_remote_worker_executor_completes_repo_checkout_failure() -> None:
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
        checkout=FailingCheckout(),
        command_runner=FakeCommandRunner(),
    )

    result = executor.run_once(config)

    assert result["cloud_run"]["status"] == "failed"
    assert client.completed is not None
    completion = client.completed["result"]
    assert completion["failure_reason"] == "repo_checkout_failed"
    assert completion["status"] == "failed"
    assert completion["test_result"] == "not_run"
    assert [upload["ref"]["kind"] for upload in client.uploaded] == [
        "diff",
        "command_result",
        "test_result",
        "log",
        "manifest",
    ]


def test_remote_worker_executor_completes_unexpected_command_runner_failure() -> None:
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
        command_runner=ExplodingCommandRunner(),
    )

    result = executor.run_once(config)

    assert result["cloud_run"]["status"] == "failed"
    assert client.completed is not None
    completion = client.completed["result"]
    assert completion["failure_reason"] == "worker_execution_failed"
    assert completion["status"] == "failed"
    assert completion["test_result"] == "not_run"


def test_remote_worker_redacts_secret_bearing_execution_fields() -> None:
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
        command_runner=SecretBearingCommandRunner(),
    )

    result = executor.run_once(config)

    assert result["cloud_run"]["status"] == "failed"
    assert client.completed is not None
    secret_values = [
        "ghp_private_clone_token1234",
        "callback-token-1",
        "env-secret-value",
    ]
    for secret in secret_values:
        assert secret not in str(client.uploaded)
        assert secret not in str(client.completed)
    diff_upload = next(
        upload for upload in client.uploaded if upload["ref"]["kind"] == "diff"
    )
    assert "[redacted]" in diff_upload["content"]
    manifest_upload = next(
        upload for upload in client.uploaded if upload["ref"]["kind"] == "manifest"
    )
    assert '"failure_reason": "failed with [redacted] [redacted] [redacted]"' in (
        manifest_upload["content"]
    )
    completion = client.completed["result"]
    assert completion["files_changed"] == [
        "AI_SCDC_CLOUD_RUN_[redacted].md",
        "AI_SCDC_CLOUD_RUN_[redacted].md",
        "AI_SCDC_CLOUD_RUN_[redacted].md",
    ]
    assert completion["worktree_ref"] == (
        "remote-worker://[redacted]/[redacted]/[redacted]"
    )
    assert completion["tests_run"] == [
        "pytest -q --token [redacted]",
        "echo [redacted]",
        "echo [redacted]",
    ]
    assert completion["risks"] == [
        "clone risk [redacted]",
        "callback risk [redacted]",
        "env risk [redacted]",
    ]
    assert completion["test_result"] == "failed [redacted] [redacted] [redacted]"
    assert completion["failure_reason"] == (
        "failed with [redacted] [redacted] [redacted]"
    )


def test_remote_worker_marks_failed_and_uploads_artifacts_when_cancelled_after_execution() -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerExecutor

    client = FakeWorkerClient(cancel_on_second_heartbeat=True)
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

    assert len(client.heartbeats) == 2
    assert result["cloud_run"]["status"] == "failed"
    uploaded_kinds = [upload["ref"]["kind"] for upload in client.uploaded]
    assert uploaded_kinds == ["diff", "command_result", "test_result", "log", "manifest"]
    assert client.completed is not None
    completion = client.completed["result"]
    assert completion["status"] == "failed"
    assert completion["failure_reason"] == "cancelled"
    assert completion["artifact_refs"] == [upload["ref"] for upload in client.uploaded]
    for secret in [
        "ghp_private_clone_token1234",
        "callback-token-1",
        "env-secret-value",
    ]:
        assert secret not in str(client.uploaded)
        assert secret not in str(client.completed)


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
