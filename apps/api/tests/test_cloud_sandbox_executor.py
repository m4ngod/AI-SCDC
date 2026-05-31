from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    FakeCloudSandboxExecutor,
    SandboxExecutionRequest,
    redact_secrets,
    select_cloud_sandbox_executor,
)


def test_selects_fake_executor_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AI_SCDC_CLOUD_RUNNER", raising=False)

    executor = select_cloud_sandbox_executor()

    assert isinstance(executor, FakeCloudSandboxExecutor)


def test_redact_secrets_replaces_every_secret_value() -> None:
    text = "token ghp_example1234567890 and short ghp_example1234567890"

    assert redact_secrets(text, ["ghp_example1234567890"]) == (
        "token [redacted] and short [redacted]"
    )


def test_command_result_serialization_redacts_secret() -> None:
    result = CommandResult(
        command="git clone",
        exit_code=1,
        stdout="",
        stderr="failed ghp_example1234567890",
        duration_ms=25,
        timed_out=False,
    )

    assert result.redacted(["ghp_example1234567890"]).stderr == "failed [redacted]"


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
    assert "Fake cloud task" in result.diff_text
