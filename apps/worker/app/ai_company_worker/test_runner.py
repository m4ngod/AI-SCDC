import subprocess
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class TestRunnerError(RuntimeError):
    """Raised when tests cannot be run safely."""

    __test__ = False


class CommandResult(BaseModel):
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int


class TestRunnerRequest(BaseModel):
    __test__ = False

    worktree_path: Path
    commands: list[str] = Field(default_factory=list)
    timeout_seconds: float = 120.0


class TestRunnerResult(BaseModel):
    __test__ = False

    status: Literal["passed", "failed"]
    command_results: list[CommandResult]


def run_tests(request: TestRunnerRequest) -> TestRunnerResult:
    worktree_path = request.worktree_path.resolve()
    if not worktree_path.exists() or not worktree_path.is_dir():
        raise TestRunnerError(f"Worktree path does not exist: {worktree_path}")
    if not request.commands:
        raise TestRunnerError("No test commands configured")

    command_results: list[CommandResult] = []
    aggregate_status: Literal["passed", "failed"] = "passed"

    for command in request.commands:
        result = _run_command(worktree_path, command, request.timeout_seconds)
        command_results.append(result)
        if result.exit_code != 0:
            aggregate_status = "failed"
            break

    return TestRunnerResult(
        status=aggregate_status,
        command_results=command_results,
    )


def _run_command(
    worktree_path: Path,
    command: str,
    timeout_seconds: float,
) -> CommandResult:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=worktree_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(
            command=command,
            exit_code=None,
            stdout=stdout,
            stderr=(stderr + "\nCommand timed out").strip(),
            duration_ms=duration_ms,
        )
    except OSError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return CommandResult(
            command=command,
            exit_code=None,
            stdout="",
            stderr=str(exc),
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    return CommandResult(
        command=command,
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=duration_ms,
    )
