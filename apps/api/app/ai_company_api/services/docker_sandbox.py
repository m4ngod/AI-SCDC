from dataclasses import dataclass, replace
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Protocol, Sequence

from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    redact_secrets,
)


@dataclass(frozen=True)
class ProcessResult:
    args: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    def redacted(self, secrets: list[str]) -> "ProcessResult":
        return replace(
            self,
            args=[redact_secrets(arg, secrets) for arg in self.args],
            stdout=redact_secrets(self.stdout, secrets),
            stderr=redact_secrets(self.stderr, secrets),
        )

    def to_command_result(
        self,
        command: str,
        *,
        secrets: list[str],
    ) -> CommandResult:
        safe_result = self.redacted(secrets)
        safe_command = redact_secrets(command, secrets)
        return CommandResult(
            command=safe_command,
            exit_code=safe_result.exit_code,
            stdout=safe_result.stdout,
            stderr=safe_result.stderr,
            duration_ms=safe_result.duration_ms,
            timed_out=safe_result.timed_out,
        )


class ProcessRunner(Protocol):
    def run(
        self,
        args: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> ProcessResult:
        ...


class SubprocessRunner:
    def run(
        self,
        args: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> ProcessResult:
        command_args = [str(item) for item in args]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command_args,
                cwd=cwd,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return ProcessResult(
                args=command_args,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return ProcessResult(
                args=command_args,
                exit_code=None,
                stdout=_output_to_text(exc.stdout),
                stderr=_output_to_text(exc.stderr),
                duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=True,
            )
        except OSError as exc:
            return ProcessResult(
                args=command_args,
                exit_code=127,
                stdout="",
                stderr=f"failed to start process: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )


class RedactingProcessRunner:
    def __init__(self, base_runner: ProcessRunner, secrets: list[str]) -> None:
        self._base_runner = base_runner
        self._secrets = secrets

    def run(
        self,
        args: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> ProcessResult:
        result = self._base_runner.run(
            args,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        return result.redacted(self._secrets)


class DockerLocalSandboxExecutor:
    sandbox_kind = "docker_local"

    def __init__(
        self,
        *,
        process_runner: ProcessRunner | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._process_runner = process_runner or SubprocessRunner()
        self._workspace_root = workspace_root or (
            Path(tempfile.gettempdir()) / "ai-scdc-docker-sandbox"
        )

    def build_docker_run_args(
        self,
        *,
        request: SandboxExecutionRequest,
        workspace_path: Path,
        artifact_path: Path,
        command: str,
        timeout_seconds: int,
    ) -> list[str]:
        network_args = (
            ["--network", "bridge"] if request.network_enabled else ["--network", "none"]
        )
        env_args: list[str] = []
        for name in sorted(request.env):
            env_args.extend(["-e", name])

        return [
            "docker",
            "run",
            "--rm",
            *network_args,
            "-v",
            f"{workspace_path.as_posix()}:/workspace",
            "-v",
            f"{artifact_path.as_posix()}:/artifacts",
            "-w",
            "/workspace/repo",
            *env_args,
            request.docker_image or "python:3.11-slim",
            "sh",
            "-lc",
            command,
        ]

    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        raise NotImplementedError("Docker execution workflow is added in Task 4")


def _output_to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value
