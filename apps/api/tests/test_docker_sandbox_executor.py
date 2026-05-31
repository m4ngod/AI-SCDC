from pathlib import Path

from ai_company_api.services.cloud_sandbox_executor import (
    SandboxCommandSelection,
    SandboxExecutionRequest,
)
from ai_company_api.services.docker_sandbox import (
    DockerLocalSandboxExecutor,
    ProcessResult,
    RedactingProcessRunner,
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


def test_docker_run_args_do_not_mount_host_home_or_docker_socket(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)

    args = executor.build_docker_run_args(
        request=docker_request(tmp_path),
        workspace_path=tmp_path / "workspace",
        artifact_path=tmp_path / "artifacts",
        command="python -V",
        timeout_seconds=30,
    )
    joined = " ".join(args)

    assert "python:3.11-slim" in args
    assert "/var/run/docker.sock" not in joined
    assert str(Path.home()) not in joined
    assert "--network" in args
    assert "bridge" in args
    assert "-v" in args


def test_redacting_process_runner_removes_token_from_output(tmp_path: Path) -> None:
    base_runner = RecordingRunner(
        [
            ProcessResult(
                args=["git"],
                exit_code=1,
                stdout="",
                stderr="bad ghp_example1234567890",
                duration_ms=5,
            )
        ]
    )
    runner = RedactingProcessRunner(base_runner, ["ghp_example1234567890"])

    result = runner.run(["git", "clone"], timeout_seconds=1)

    assert result.stderr == "bad [redacted]"


def test_selects_docker_executor_when_enabled(monkeypatch) -> None:
    from ai_company_api.services.cloud_sandbox_executor import (
        select_cloud_sandbox_executor,
    )

    monkeypatch.setenv("AI_SCDC_CLOUD_RUNNER", "docker_local")

    executor = select_cloud_sandbox_executor()

    assert executor.sandbox_kind == "docker_local"
