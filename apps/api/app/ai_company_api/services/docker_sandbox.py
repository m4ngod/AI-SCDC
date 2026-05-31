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
        command_results: list[CommandResult] = []
        test_results: list[CommandResult] = []
        secrets = list(request.env.values())
        runner = RedactingProcessRunner(self._process_runner, secrets)

        self._workspace_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="ai-scdc-docker-",
            dir=str(self._workspace_root),
        ) as tmp:
            root = Path(tmp)
            workspace_path = root / "workspace"
            artifact_path = root / "artifacts"
            workspace_path.mkdir(parents=True, exist_ok=True)
            artifact_path.mkdir(parents=True, exist_ok=True)

            docker_version = runner.run(["docker", "version"], timeout_seconds=15)
            command_results.append(
                docker_version.to_command_result("docker version", secrets=secrets)
            )
            if docker_version.exit_code != 0 or docker_version.timed_out:
                return _failed_result(
                    "docker_unavailable",
                    "docker_local",
                    command_results,
                    test_results,
                )

            patch_command = request.patch_command.command if request.patch_command else ""
            patch_timeout = (
                request.patch_command.timeout_seconds if request.patch_command else 300
            )
            steps = [
                ("clone", f"git clone {request.repo_url} .", 300),
                ("checkout", f"git checkout {request.base_branch}", 60),
                ("branch", f"git checkout -B {request.head_branch}", 60),
                ("patch", patch_command, patch_timeout),
                ("intent-to-add", "git add -N .", 60),
                ("name-only", "git diff --name-only", 60),
                ("diff", "git diff --no-ext-diff", 60),
                ("base-sha", f"git rev-parse origin/{request.base_branch}", 60),
                ("head-sha", "git rev-parse HEAD", 60),
            ]

            captured: dict[str, ProcessResult] = {}
            for label, command, timeout_seconds in steps:
                process_result = runner.run(
                    self.build_docker_run_args(
                        request=request,
                        workspace_path=workspace_path,
                        artifact_path=artifact_path,
                        command=command,
                        timeout_seconds=timeout_seconds,
                    ),
                    env=request.env,
                    timeout_seconds=timeout_seconds,
                )
                captured[label] = process_result
                command_results.append(
                    process_result.to_command_result(command, secrets=secrets)
                )
                if process_result.exit_code != 0 or process_result.timed_out:
                    failure_reason = {
                        "clone": "repo_checkout_failed",
                        "checkout": "repo_checkout_failed",
                        "branch": "repo_checkout_failed",
                        "patch": "patch_command_failed",
                        "intent-to-add": "artifact_capture_failed",
                        "name-only": "artifact_capture_failed",
                        "diff": "artifact_capture_failed",
                        "base-sha": "artifact_capture_failed",
                        "head-sha": "artifact_capture_failed",
                    }[label]
                    return _failed_result(
                        failure_reason,
                        "docker_local",
                        command_results,
                        test_results,
                    )

            files_changed = sorted(
                line.strip()
                for line in captured["name-only"].stdout.splitlines()
                if line.strip()
            )
            diff_text = captured["diff"].stdout
            if not files_changed or diff_text.strip() == "":
                return _failed_result(
                    "no_patch_produced",
                    "docker_local",
                    command_results,
                    test_results,
                )

            _ensure_files_allowed(files_changed, request.allowed_paths)

            test_status = "passed"
            for command in request.test_commands:
                process_result = runner.run(
                    self.build_docker_run_args(
                        request=request,
                        workspace_path=workspace_path,
                        artifact_path=artifact_path,
                        command=command.command,
                        timeout_seconds=command.timeout_seconds,
                    ),
                    env=request.env,
                    timeout_seconds=command.timeout_seconds,
                )
                test_results.append(
                    process_result.to_command_result(
                        command.command,
                        secrets=secrets,
                    )
                )
                if process_result.exit_code != 0 or process_result.timed_out:
                    test_status = "failed"

            failure_reason = "test_failed" if test_status == "failed" else None
            return SandboxExecutionResult(
                status="failed" if failure_reason else "patch_ready",
                runner_kind="docker_local",
                base_sha=captured["base-sha"].stdout.strip() or None,
                head_sha=captured["head-sha"].stdout.strip() or None,
                worktree_ref=f"cloud://docker-local/{request.cloud_run_id}",
                summary="Docker local sandbox produced a patch artifact.",
                files_changed=files_changed,
                tests_run=[command.key for command in request.test_commands],
                test_result=test_status,
                risks=[],
                diff_text=diff_text,
                command_results=command_results,
                test_command_results=test_results,
                failure_reason=failure_reason,
            )


def _output_to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _failed_result(
    failure_reason: str,
    runner_kind: str,
    command_results: list[CommandResult],
    test_results: list[CommandResult],
) -> SandboxExecutionResult:
    return SandboxExecutionResult(
        status="failed",
        runner_kind=runner_kind,
        base_sha=None,
        head_sha=None,
        worktree_ref=None,
        summary="",
        files_changed=[],
        tests_run=[],
        test_result="not_run",
        risks=[],
        diff_text="",
        command_results=command_results,
        test_command_results=test_results,
        failure_reason=failure_reason,
    )


def _ensure_files_allowed(files_changed: list[str], allowed_paths: list[str]) -> None:
    from ai_company_worker.local_runner import (
        LocalRunnerError,
        ensure_changed_files_allowed,
    )

    try:
        ensure_changed_files_allowed(files_changed, allowed_paths)
    except LocalRunnerError as exc:
        raise DockerSandboxError(str(exc)) from exc


class DockerSandboxError(RuntimeError):
    pass
