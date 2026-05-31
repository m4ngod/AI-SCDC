import sys
import types

from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    FakeCloudSandboxExecutor,
    SandboxExecutionRequest,
    repo_url_redaction_secrets,
    redact_secrets,
    select_cloud_sandbox_executor,
)


def test_selects_fake_executor_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AI_SCDC_CLOUD_RUNNER", raising=False)

    executor = select_cloud_sandbox_executor()

    assert isinstance(executor, FakeCloudSandboxExecutor)


def test_selects_fake_executor_with_normalized_env(monkeypatch) -> None:
    monkeypatch.setenv("AI_SCDC_CLOUD_RUNNER", " FAKE ")

    executor = select_cloud_sandbox_executor()

    assert isinstance(executor, FakeCloudSandboxExecutor)


def test_selects_docker_local_executor_from_planned_module(monkeypatch) -> None:
    module_name = "ai_company_api.services.docker_sandbox"
    module = types.ModuleType(module_name)

    class StubDockerLocalSandboxExecutor:
        sandbox_kind = "docker_local"

    module.DockerLocalSandboxExecutor = StubDockerLocalSandboxExecutor
    monkeypatch.setitem(sys.modules, module_name, module)
    monkeypatch.setenv("AI_SCDC_CLOUD_RUNNER", "docker_local")

    executor = select_cloud_sandbox_executor()

    assert isinstance(executor, StubDockerLocalSandboxExecutor)


def test_redact_secrets_replaces_every_secret_value() -> None:
    text = "token ghp_example1234567890 and short ghp_example1234567890"

    assert redact_secrets(text, ["ghp_example1234567890"]) == (
        "token [redacted] and short [redacted]"
    )


def test_repo_url_redaction_secrets_include_raw_and_decoded_userinfo() -> None:
    secrets = repo_url_redaction_secrets(
        "https://user:sec%40ret@github.com/example/demo"
    )
    text = (
        "clone https://user:sec%40ret@github.com/example/demo "
        "raw=sec%40ret decoded=sec@ret user=user segment=user:sec%40ret@"
    )

    redacted = redact_secrets(text, secrets)

    assert "sec%40ret" not in redacted
    assert "sec@ret" not in redacted
    assert "user" not in redacted
    assert "user:sec%40ret@" not in redacted
    assert redacted.count("[redacted]") >= 4


def test_command_result_defaults_timed_out_to_false() -> None:
    result = CommandResult(
        command="python -V",
        exit_code=0,
        stdout="Python",
        stderr="",
        duration_ms=25,
    )

    assert result.timed_out is False


def test_command_result_redacts_secret_from_command_stdout_and_stderr() -> None:
    result = CommandResult(
        command="git clone https://ghp_example1234567890@github.com/example/demo",
        exit_code=1,
        stdout="token ghp_example1234567890",
        stderr="failed ghp_example1234567890",
        duration_ms=25,
        timed_out=False,
    )

    redacted = result.redacted(["ghp_example1234567890"])

    assert redacted.command == "git clone https://[redacted]@github.com/example/demo"
    assert redacted.stdout == "token [redacted]"
    assert redacted.stderr == "failed [redacted]"


def test_command_result_payload_redacts_secret_without_timeout_field() -> None:
    result = CommandResult(
        command="echo ghp_example1234567890",
        exit_code=1,
        stdout="seen ghp_example1234567890",
        stderr="failed ghp_example1234567890",
        duration_ms=25,
        timed_out=True,
    )

    payload = result.as_payload(secrets=["ghp_example1234567890"])

    assert payload == {
        "command": "echo [redacted]",
        "exit_code": 1,
        "stdout": "seen [redacted]",
        "stderr": "failed [redacted]",
        "duration_ms": 25,
    }


def test_fake_executor_keeps_existing_patch_shape() -> None:
    request = SandboxExecutionRequest(
        task_id="task_1",
        cloud_run_id="cloud_run_1",
        title="Fake cloud task",
        description="",
        repo_url="https://github.com/example/demo",
        base_branch="main",
        head_branch="ai-scdc/task-task_1-cloud_run_1",
        allowed_paths=["AI_SCDC_CLOUD_RUN.md"],
        required_tests=["python -V"],
        docker_image=None,
        patch_command=None,
        test_commands=[],
        env={},
        network_enabled=True,
    )

    result = FakeCloudSandboxExecutor().run(request)

    assert result.status == "patch_ready"
    assert result.runner_kind == "cloud_fake"
    assert result.files_changed == ["AI_SCDC_CLOUD_RUN.md"]
    assert result.test_result == "not_run"
    assert result.test_command_results == []
    assert result.failure_reason is None
    assert "Fake cloud task" in result.diff_text
