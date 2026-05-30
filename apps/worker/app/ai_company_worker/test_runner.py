import os
import signal
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
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=worktree_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_process_group_options(),
        )
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
        else:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        duration_ms = int((time.monotonic() - started) * 1000)
        return CommandResult(
            command=command,
            exit_code=None,
            stdout=stdout or "",
            stderr=((stderr or "") + "\nCommand timed out").strip(),
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
        exit_code=process.returncode if process is not None else None,
        stdout=stdout or "",
        stderr=stderr or "",
        duration_ms=duration_ms,
    )


def _process_group_options() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
