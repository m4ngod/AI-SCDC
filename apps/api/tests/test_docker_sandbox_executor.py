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
        self.env_file_snapshots: list[tuple[str, str]] = []

    def run(self, args, *, cwd=None, env=None, timeout_seconds=30):
        command_args = [str(item) for item in args]
        self.calls.append(
            {
                "args": command_args,
                "cwd": cwd,
                "env": env,
                "timeout_seconds": timeout_seconds,
            }
        )
        for index, item in enumerate(command_args[:-1]):
            if item == "--env-file":
                path = Path(command_args[index + 1])
                self.env_file_snapshots.append(
                    (command_args[index + 1], path.read_text(encoding="utf-8"))
                )
        if self.results:
            return self.results.pop(0)
        return ProcessResult(
            args=command_args,
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
        github_owner="example",
        github_repo="demo",
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
    assert "--name" in args


def test_docker_run_args_use_host_user_on_posix_bind_mounts(
    tmp_path: Path,
) -> None:
    def getuid() -> int:
        return 1001

    def getgid() -> int:
        return 1002

    user_args = docker_sandbox._docker_user_args(
        platform_name="posix",
        getuid=getuid,
        getgid=getgid,
    )

    assert user_args == ["--user", "1001:1002"]


def test_docker_run_args_omit_host_user_on_windows_bind_mounts() -> None:
    user_args = docker_sandbox._docker_user_args(
        platform_name="nt",
        getuid=lambda: 1001,
        getgid=lambda: 1002,
    )

    assert user_args == []


def test_docker_run_args_include_host_user_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        docker_sandbox,
        "_docker_user_args",
        lambda: ["--user", "1001:1002"],
    )
    executor = DockerLocalSandboxExecutor(workspace_root=tmp_path)

    args = executor.build_docker_run_args(
        request=docker_request(tmp_path),
        workspace_path=Path(tmp_path.anchor) / "ai-scdc-test-workspace",
        artifact_path=Path(tmp_path.anchor) / "ai-scdc-test-artifacts",
        command="python -V",
        timeout_seconds=30,
    )

    assert "--user" in args
    assert args[args.index("--user") + 1] == "1001:1002"


def test_docker_executor_rejects_option_like_docker_image(tmp_path: Path) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        docker_image="--volume=/var/run/docker.sock:/var/run/docker.sock",
    )

    result = executor.run(request)

    assert result.status == "failed"
    assert result.failure_reason == "invalid_docker_image"
    assert runner.calls == []


def test_docker_executor_accepts_normal_docker_image(tmp_path: Path) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        docker_image="python:3.11-bookworm",
        test_commands=[],
        required_tests=[],
    )

    result = executor.run(request)

    docker_run_images = [call["args"][-4] for call in runner.calls[1:10]]
    assert result.status == "patch_ready"
    assert set(docker_run_images) == {"python:3.11-bookworm"}


def test_docker_executor_separates_git_clone_options_from_repo_url(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        repo_url="-c core.sshCommand=touch /tmp/repo-pwned",
        test_commands=[],
        required_tests=[],
    )

    result = executor.run(request)

    clone_command = runner.calls[1]["args"][-1]
    assert result.status == "patch_ready"
    assert clone_command == (
        "git clone -- '-c core.sshCommand=touch /tmp/repo-pwned' ."
    )


def test_docker_executor_redacts_credentials_embedded_in_repo_url(
    tmp_path: Path,
) -> None:
    credentialed_url = "https://user:secret-token@github.com/example/demo"
    results = docker_success_results()
    results[1] = ProcessResult(
        args=["docker", "clone"],
        exit_code=0,
        stdout=f"cloned {credentialed_url}",
        stderr=f"warning {credentialed_url}",
        duration_ms=1,
    )
    runner = RecordingRunner(results)
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        repo_url=credentialed_url,
        env={},
        test_commands=[],
        required_tests=[],
    )

    result = executor.run(request)

    assert result.status == "patch_ready"
    assert "secret-token" not in str(result.command_results)
    assert "user:secret-token@" not in str(result.command_results)
    assert "[redacted]" in result.command_results[1].command
    assert "[redacted]" in result.command_results[1].stdout
    assert "[redacted]" in result.command_results[1].stderr


def test_docker_executor_uses_askpass_for_github_token_without_persisting_secret(
    tmp_path: Path,
) -> None:
    token = "ghp_private_clone_token1234"
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        github_token=token,
        env={},
        test_commands=[],
        required_tests=[],
    )

    result = executor.run(request)

    clone_command = runner.calls[1]["args"][-1]
    command_payloads = "\n".join(
        "\n".join([item.command, item.stdout, item.stderr])
        for item in result.command_results
    )
    assert result.status == "patch_ready"
    assert "GIT_ASKPASS=/artifacts/git-askpass.sh" in clone_command
    assert "GIT_TERMINAL_PROMPT=0" in clone_command
    assert token not in clone_command
    assert token not in command_payloads
    assert token not in "\n".join(snapshot for _path, snapshot in runner.env_file_snapshots)


def test_docker_executor_rejects_authenticated_clone_for_non_github_url(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        repo_url="https://evil.example/example/demo",
        github_token="ghp_private_clone_token1234",
    )

    result = executor.run(request)

    assert result.status == "failed"
    assert result.failure_reason == "invalid_github_repository_url"
    assert runner.calls == []


def test_docker_executor_rejects_authenticated_clone_for_userinfo_url(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        repo_url="https://user:secret@github.com/example/demo",
        github_token="ghp_private_clone_token1234",
    )

    result = executor.run(request)

    assert result.status == "failed"
    assert result.failure_reason == "invalid_github_repository_url"
    assert runner.calls == []


def test_docker_executor_rejects_authenticated_clone_for_mismatched_github_repo(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        repo_url="https://github.com/example/other",
        github_token="ghp_private_clone_token1234",
    )

    result = executor.run(request)

    assert result.status == "failed"
    assert result.failure_reason == "invalid_github_repository_url"
    assert runner.calls == []


def test_docker_executor_rejects_authenticated_clone_for_encoded_slash_repo(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        repo_url="https://github.com/example/demo%2Fsecret",
        github_repo="demo%2Fsecret",
        github_token="ghp_private_clone_token1234",
    )

    result = executor.run(request)

    assert result.status == "failed"
    assert result.failure_reason == "invalid_github_repository_url"
    assert runner.calls == []


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("base_branch", "-c core.sshCommand=touch /tmp/base-pwned"),
        ("head_branch", "--orphan=malicious"),
    ],
)
def test_docker_executor_rejects_option_like_git_branch_names(
    tmp_path: Path,
    field_name: str,
    field_value: str,
) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        **{field_name: field_value},
    )

    result = executor.run(request)

    assert result.status == "failed"
    assert result.failure_reason == "invalid_git_reference"
    assert runner.calls == []


def test_docker_executor_excludes_invalid_env_names(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        test_commands=[],
        required_tests=[],
        env={
            **docker_request(tmp_path).env,
            "VALID_SANDBOX_NAME": "valid",
            "BAD=NAME": "bad",
            "BAD\nNAME": "bad",
        },
    )

    executor.run(request)

    docker_run_env = runner.calls[1]["env"]
    docker_run_args = runner.calls[1]["args"]
    env_file_texts = [snapshot for _path, snapshot in runner.env_file_snapshots]
    assert docker_run_env is not request.env
    assert docker_run_env.get("VALID_SANDBOX_NAME") != "valid"
    assert any("VALID_SANDBOX_NAME=valid\n" in text for text in env_file_texts)
    assert "BAD=NAME" not in docker_run_env
    assert "BAD\nNAME" not in docker_run_env
    assert "--env-file" in docker_run_args
    assert all("BAD=NAME" not in text for text in env_file_texts)
    assert all("BAD\nNAME" not in text for text in env_file_texts)


def test_docker_executor_delivers_sandbox_env_via_env_file_without_host_env_overlay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LD_PRELOAD", "host-ld-preload")
    monkeypatch.setenv("HOME", "host-home")
    monkeypatch.setenv("API_KEY", "host-api-key")
    runner = RecordingRunner(docker_success_results())
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(
        docker_request(tmp_path),
        test_commands=[],
        required_tests=[],
        env={
            **docker_request(tmp_path).env,
            "LD_PRELOAD": "/tmp/sandbox.so",
            "HOME": "/sandbox/home",
            "API_KEY": "sandbox-secret-api-key",
            "PATH": "sandbox-path",
            "DOCKER_HOST": "sandbox-docker",
            "HTTP_PROXY": "http://sandbox-proxy",
        },
    )

    result = executor.run(request)

    docker_version_env = runner.calls[0]["env"]
    docker_run_env = runner.calls[1]["env"]
    docker_run_args = runner.calls[1]["args"]
    env_file_text = runner.env_file_snapshots[0][1]
    all_argv = "\n".join(" ".join(call["args"]) for call in runner.calls)
    command_payloads = "\n".join(
        "\n".join(
            [
                command_result.command,
                command_result.stdout,
                command_result.stderr,
            ]
        )
        for command_result in result.command_results
    )

    assert result.status == "patch_ready"
    assert "LD_PRELOAD" not in docker_version_env
    assert "HOME" not in docker_version_env
    assert "API_KEY" not in docker_version_env
    assert "LD_PRELOAD" not in docker_run_env
    assert "HOME" not in docker_run_env
    assert "API_KEY" not in docker_run_env
    assert "--env-file" in docker_run_args
    assert "-e" not in docker_run_args
    assert "LD_PRELOAD=/tmp/sandbox.so\n" in env_file_text
    assert "HOME=/sandbox/home\n" in env_file_text
    assert "API_KEY=sandbox-secret-api-key\n" in env_file_text
    assert "PATH=sandbox-path\n" not in env_file_text
    assert "DOCKER_HOST=sandbox-docker\n" not in env_file_text
    assert "HTTP_PROXY=http://sandbox-proxy\n" not in env_file_text
    assert "sandbox-secret-api-key" not in all_argv
    assert "sandbox-secret-api-key" not in command_payloads


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


def test_docker_executor_uses_minimal_host_env_for_docker_cli(
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
    env_file_text = runner.env_file_snapshots[0][1]
    assert docker_run_env is not request.env
    assert "AI_SCDC_HOST_ENV_MARKER" not in docker_run_env
    assert docker_run_env.get("AI_SCDC_GITHUB_TOKEN") != "ghp_example1234567890"
    assert docker_run_env.get("AI_SCDC_SAFE_SANDBOX_VAR") != "sandbox-value"
    assert docker_run_env["PATH"] == "host-path"
    assert "DOCKER_HOST" not in docker_run_env
    assert "DOCKER_CONFIG" not in docker_run_env
    assert "HTTP_PROXY" not in docker_run_env
    assert "CUSTOM_ENV" not in docker_run_env
    assert "AI_SCDC_SAFE_SANDBOX_VAR=sandbox-value\n" in env_file_text
    assert "CUSTOM_ENV=sandbox-custom\n" in env_file_text
    assert "PATH=sandbox-path\n" not in env_file_text
    assert "DOCKER_HOST=sandbox-docker\n" not in env_file_text
    assert "DOCKER_CONFIG=sandbox-docker-config\n" not in env_file_text
    assert "HTTP_PROXY=http://sandbox-proxy\n" not in env_file_text


def test_docker_executor_removes_timed_out_container(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            ProcessResult(
                args=["docker", "version"],
                exit_code=0,
                stdout="Docker",
                stderr="",
                duration_ms=1,
            ),
            ProcessResult(
                args=["docker", "clone"],
                exit_code=None,
                stdout="",
                stderr="timeout",
                duration_ms=300000,
                timed_out=True,
            ),
            ProcessResult(
                args=["docker", "rm", "-f", "container"],
                exit_code=0,
                stdout="container",
                stderr="",
                duration_ms=1,
            ),
        ]
    )
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)
    request = replace(docker_request(tmp_path), test_commands=[], required_tests=[])

    result = executor.run(request)

    docker_run_args = runner.calls[1]["args"]
    name_index = docker_run_args.index("--name")
    container_name = docker_run_args[name_index + 1]
    assert result.status == "failed"
    assert result.failure_reason == "repo_checkout_failed"
    assert result.command_results[1].timed_out is True
    assert runner.calls[2]["args"] == ["docker", "rm", "-f", container_name]


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
        "git clone -- "
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
