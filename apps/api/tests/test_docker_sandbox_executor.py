from dataclasses import replace
from pathlib import Path

import pytest

from ai_company_api.services.cloud_sandbox_executor import (
    SandboxCommandSelection,
    SandboxExecutionRequest,
)
from ai_company_api.services import docker_sandbox
from ai_company_api.services.docker_sandbox import (
    DockerLocalSandboxExecutor,
    ProcessResult,
    RedactingProcessRunner,
    SubprocessRunner,
)


class RecordingRunner:
    def __init__(self, results: list[ProcessResult] | None = None) -> None:
        self.calls: list[dict] = []
        self.results = results or []

    def run(self, args, *, cwd=None, env=None, timeout_seconds=30):
        self.calls.append(
            {
                "args": [str(item) for item in args],
                "cwd": cwd,
                "env": env,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.results:
            return self.results.pop(0)
        return ProcessResult(
            args=[str(item) for item in args],
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=1,
        )


def docker_request(tmp_path: Path) -> SandboxExecutionRequest:
    return SandboxExecutionRequest(
        task_id="task_1",
        cloud_run_id="cloud_run_1",
        title="Docker task",
        description="",
        repo_url="https://github.com/example/demo",
        base_branch="main",
        head_branch="ai-scdc/task-task_1-cloud_run_1",
        allowed_paths=["README.md"],
        required_tests=["python -V"],
        docker_image="python:3.11-slim",
        patch_command=SandboxCommandSelection(
            key="write-note",
            label="Write note",
            command="python scripts/write_note.py",
            timeout_seconds=30,
        ),
        test_commands=[
            SandboxCommandSelection(
                key="python-version",
                label="Python version",
                command="python -V",
                timeout_seconds=30,
            )
        ],
        env={"AI_SCDC_GITHUB_TOKEN": "ghp_example1234567890"},
        network_enabled=True,
    )


def docker_success_results(
    test_results: list[ProcessResult] | None = None,
) -> list[ProcessResult]:
    return [
        ProcessResult(
            args=["docker", "version"],
            exit_code=0,
            stdout="Docker",
            stderr="",
            duration_ms=1,
        ),
        ProcessResult(
            args=["docker", "clone"],
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=1,
        ),
        ProcessResult(
            args=["docker", "checkout"],
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=1,
        ),
        ProcessResult(
            args=["docker", "branch"],
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=1,
        ),
        ProcessResult(
            args=["docker", "patch"],
            exit_code=0,
            stdout="patched",
            stderr="",
            duration_ms=2,
        ),
        ProcessResult(
            args=["docker", "intent-to-add"],
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=1,
        ),
        ProcessResult(
            args=["docker", "name-only"],
            exit_code=0,
            stdout="README.md\n",
            stderr="",
            duration_ms=1,
        ),
        ProcessResult(
            args=["docker", "diff"],
            exit_code=0,
            stdout="diff --git a/README.md b/README.md\n+Docker patch\n",
            stderr="",
            duration_ms=1,
        ),
        ProcessResult(
            args=["docker", "base-sha"],
            exit_code=0,
            stdout="abc123\n",
            stderr="",
            duration_ms=1,
        ),
        ProcessResult(
            args=["docker", "head-sha"],
            exit_code=0,
            stdout="def456\n",
            stderr="",
            duration_ms=1,
        ),
        *(test_results or []),
    ]


def test_docker_run_args_do_not_mount_host_home_or_docker_socket(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(docker_request(tmp_path), docker_image=None)

    args = executor.build_docker_run_args(
        request=request,
        workspace_path=Path(tmp_path.anchor) / "ai-scdc-test-workspace",
        artifact_path=Path(tmp_path.anchor) / "ai-scdc-test-artifacts",
        command="python -V",
        timeout_seconds=30,
    )
    joined = " ".join(args).replace("\\", "/")
    home = str(Path.home()).replace("\\", "/")

    assert "python:3.11-bookworm" in args
    assert "/var/run/docker.sock" not in joined
    assert home not in joined
    assert "--network" in args
    assert "bridge" in args
    assert "-v" in args


def test_redacting_process_runner_removes_token_from_result(
    tmp_path: Path,
) -> None:
    base_runner = RecordingRunner(
        [
            ProcessResult(
                args=["git", "ghp_example1234567890"],
                exit_code=1,
                stdout="out ghp_example1234567890",
                stderr="bad ghp_example1234567890",
                duration_ms=5,
            )
        ]
    )
    runner = RedactingProcessRunner(base_runner, ["ghp_example1234567890"])

    result = runner.run(["git", "clone"], timeout_seconds=1)

    assert result.args == ["git", "[redacted]"]
    assert result.stdout == "out [redacted]"
    assert result.stderr == "bad [redacted]"


def test_command_result_redacts_explicit_command_and_process_output() -> None:
    secret = "ghp_example1234567890"
    result = ProcessResult(
        args=["echo", secret],
        exit_code=1,
        stdout=f"stdout {secret}",
        stderr=f"stderr {secret}",
        duration_ms=5,
    )

    command_result = result.to_command_result(
        f"echo {secret}",
        secrets=[secret],
    )

    assert secret not in command_result.command
    assert secret not in command_result.stdout
    assert secret not in command_result.stderr
    assert command_result.command == "echo [redacted]"


def test_command_result_requires_secrets_for_explicit_command() -> None:
    result = ProcessResult(
        args=["echo"],
        exit_code=0,
        stdout="",
        stderr="",
        duration_ms=1,
    )

    with pytest.raises(TypeError):
        result.to_command_result("echo ghp_example1234567890")


def test_subprocess_runner_returns_result_when_command_is_missing(monkeypatch) -> None:
    def raise_missing(*args, **kwargs):
        raise FileNotFoundError("missing docker")

    monkeypatch.setattr(docker_sandbox.subprocess, "run", raise_missing)

    result = SubprocessRunner().run(["docker", "version"], timeout_seconds=1)

    assert result.args == ["docker", "version"]
    assert result.exit_code == 127
    assert result.stdout == ""
    assert "missing docker" in result.stderr
    assert result.duration_ms >= 0
    assert result.timed_out is False


def test_selects_docker_executor_when_enabled(monkeypatch) -> None:
    from ai_company_api.services.cloud_sandbox_executor import (
        select_cloud_sandbox_executor,
    )

    monkeypatch.setenv("AI_SCDC_CLOUD_RUNNER", "docker_local")

    executor = select_cloud_sandbox_executor()

    assert executor.sandbox_kind == "docker_local"


def test_docker_executor_captures_diff_and_test_result(tmp_path: Path) -> None:
    runner = RecordingRunner(
        docker_success_results(
            [
                ProcessResult(
                    args=["docker", "test"],
                    exit_code=0,
                    stdout="Python 3.11\n",
                    stderr="",
                    duration_ms=3,
                )
            ]
        )
    )
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)

    result = executor.run(docker_request(tmp_path))

    assert result.status == "patch_ready"
    assert result.runner_kind == "docker_local"
    assert result.files_changed == ["README.md"]
    assert result.diff_text.startswith("diff --git a/README.md")
    assert result.base_sha == "abc123"
    assert result.head_sha == "def456"
    assert result.tests_run == ["python -V"]
    assert result.test_result == "passed"
    assert result.test_command_results[0].stdout == "Python 3.11\n"
    assert result.failure_reason is None


def test_docker_executor_keeps_host_env_for_docker_cli(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AI_SCDC_HOST_ENV_MARKER", "host-value")
    monkeypatch.setenv("PATH", "host-path")
    monkeypatch.setenv("DOCKER_HOST", "host-docker")
    monkeypatch.setenv("DOCKER_CONFIG", "host-docker-config")
    monkeypatch.setenv("HTTP_PROXY", "http://host-proxy")
    monkeypatch.setenv("CUSTOM_ENV", "host-custom")
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        test_commands=[],
        required_tests=[],
        env={
            **docker_request(tmp_path).env,
            "AI_SCDC_SAFE_SANDBOX_VAR": "sandbox-value",
            "PATH": "sandbox-path",
            "DOCKER_HOST": "sandbox-docker",
            "DOCKER_CONFIG": "sandbox-docker-config",
            "HTTP_PROXY": "http://sandbox-proxy",
            "CUSTOM_ENV": "sandbox-custom",
        },
    )

    executor.run(request)

    docker_run_env = runner.calls[1]["env"]
    docker_run_args = runner.calls[1]["args"]
    container_env_names = [
        docker_run_args[index + 1]
        for index, item in enumerate(docker_run_args[:-1])
        if item == "-e"
    ]
    assert docker_run_env is not request.env
    assert docker_run_env["AI_SCDC_HOST_ENV_MARKER"] == "host-value"
    assert docker_run_env["AI_SCDC_GITHUB_TOKEN"] == "ghp_example1234567890"
    assert docker_run_env["AI_SCDC_SAFE_SANDBOX_VAR"] == "sandbox-value"
    assert docker_run_env["PATH"] == "host-path"
    assert docker_run_env["DOCKER_HOST"] == "host-docker"
    assert docker_run_env["DOCKER_CONFIG"] == "host-docker-config"
    assert docker_run_env["HTTP_PROXY"] == "http://host-proxy"
    assert docker_run_env["CUSTOM_ENV"] == "sandbox-custom"
    assert "AI_SCDC_SAFE_SANDBOX_VAR" in container_env_names
    assert "CUSTOM_ENV" in container_env_names
    assert "PATH" not in container_env_names
    assert "DOCKER_HOST" not in container_env_names
    assert "DOCKER_CONFIG" not in container_env_names
    assert "HTTP_PROXY" not in container_env_names


def test_docker_executor_quotes_fixed_git_setup_commands(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        repo_url="https://github.com/example/demo.git; touch /tmp/repo-pwned",
        base_branch="main; touch /tmp/base-pwned",
        head_branch="feature/$(touch /tmp/head-pwned)",
        test_commands=[],
        required_tests=[],
    )

    executor.run(request)

    commands = [call["args"][-1] for call in runner.calls[1:10]]
    assert commands[0] == (
        "git clone "
        "'https://github.com/example/demo.git; touch /tmp/repo-pwned' ."
    )
    assert commands[1] == "git checkout 'main; touch /tmp/base-pwned'"
    assert commands[2] == "git checkout -B 'feature/$(touch /tmp/head-pwned)'"
    assert commands[7] == "git rev-parse 'origin/main; touch /tmp/base-pwned'"


def test_docker_executor_marks_tests_not_run_when_no_tests_configured(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(docker_request(tmp_path), test_commands=[], required_tests=[])

    result = executor.run(request)

    assert result.status == "patch_ready"
    assert result.tests_run == []
    assert result.test_result == "not_run"
    assert result.test_command_results == []


def test_docker_executor_returns_failure_when_changed_file_not_allowed(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        allowed_paths=["docs/**"],
        test_commands=[],
        required_tests=[],
    )

    result = executor.run(request)

    assert result.status == "failed"
    assert result.failure_reason == "artifact_capture_failed"
    assert result.files_changed == ["README.md"]
    assert result.diff_text.startswith("diff --git a/README.md")


def test_docker_executor_marks_failed_when_test_command_fails(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(
        docker_success_results(
            [
                ProcessResult(
                    args=["docker", "test"],
                    exit_code=1,
                    stdout="",
                    stderr="failed",
                    duration_ms=3,
                )
            ]
        )
    )
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)

    result = executor.run(docker_request(tmp_path))

    assert result.status == "failed"
    assert result.tests_run == ["python -V"]
    assert result.test_result == "failed"
    assert result.failure_reason == "test_failed"
    assert result.files_changed == ["README.md"]
    assert result.diff_text.startswith("diff --git a/README.md")
