from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
from typing import Protocol, Sequence
from urllib.parse import urlsplit

from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    repo_url_redaction_secrets,
    redact_secrets,
)


_DOCKER_CLI_ENV_DENYLIST = {
    "ALL_PROXY",
    "DOCKER_CERT_PATH",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "PATH",
    "PATHEXT",
}
_DOCKER_CLI_ENV_ALLOWLIST = {
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
}
_DEFAULT_DOCKER_IMAGE = "python:3.11-bookworm"
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOCKER_IMAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*")


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


@dataclass(frozen=True)
class GitAuthFiles:
    askpass_path: Path
    token_path: Path


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
        env_file_path: Path | None = None,
        container_name: str | None = None,
    ) -> list[str]:
        network_args = (
            ["--network", "bridge"] if request.network_enabled else ["--network", "none"]
        )
        env_args: list[str] = []
        if env_file_path is not None:
            env_args.extend(["--env-file", env_file_path.as_posix()])
        docker_image = _validated_docker_image(request.docker_image)
        if docker_image is None:
            raise DockerSandboxError("invalid_docker_image")
        safe_container_name = container_name or _docker_container_name(
            request.cloud_run_id,
            "manual",
        )

        return [
            "docker",
            "run",
            "--rm",
            "--name",
            safe_container_name,
            *network_args,
            "-v",
            f"{workspace_path.as_posix()}:/workspace",
            "-v",
            f"{artifact_path.as_posix()}:/artifacts",
            "-w",
            "/workspace/repo",
            *env_args,
            docker_image,
            "sh",
            "-lc",
            command,
        ]

    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        command_results: list[CommandResult] = []
        test_results: list[CommandResult] = []
        secrets = [
            *request.env.values(),
            request.github_token,
            *repo_url_redaction_secrets(request.repo_url),
        ]
        secrets = [secret for secret in secrets if secret]
        runner = RedactingProcessRunner(self._process_runner, secrets)
        docker_cli_env = _docker_cli_env()
        docker_image = _validated_docker_image(request.docker_image)
        if docker_image is None:
            return _failed_result(
                "invalid_docker_image",
                "docker_local",
                command_results,
                test_results,
            )
        if not _valid_git_reference_inputs(request):
            return _failed_result(
                "invalid_git_reference",
                "docker_local",
                command_results,
                test_results,
            )
        if request.github_token and not _is_safe_authenticated_github_url(
            request.repo_url,
        ):
            return _failed_result(
                "invalid_github_repository_url",
                "docker_local",
                command_results,
                test_results,
            )
        request = replace(request, docker_image=docker_image)

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
            env_file_path = _write_docker_env_file(
                artifact_path / "container.env",
                request.env,
            )

            docker_version = runner.run(
                ["docker", "version"],
                env=docker_cli_env,
                timeout_seconds=15,
            )
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

            git_auth_files = _write_git_auth_files(
                artifact_path,
                request.github_token,
            )
            patch_command = request.patch_command.command if request.patch_command else ""
            patch_timeout = (
                request.patch_command.timeout_seconds if request.patch_command else 300
            )
            steps = [
                (
                    "clone",
                    _git_clone_command(
                        request.repo_url,
                        use_askpass=git_auth_files is not None,
                    ),
                    300,
                ),
                ("checkout", f"git checkout {_shell_quote(request.base_branch)}", 60),
                ("branch", f"git checkout -B {_shell_quote(request.head_branch)}", 60),
                ("patch", patch_command, patch_timeout),
                ("intent-to-add", "git add -N .", 60),
                ("name-only", "git diff --name-only", 60),
                ("diff", "git diff --no-ext-diff", 60),
                (
                    "base-sha",
                    f"git rev-parse {_shell_quote(f'origin/{request.base_branch}')}",
                    60,
                ),
                ("head-sha", "git rev-parse HEAD", 60),
            ]

            captured: dict[str, ProcessResult] = {}
            for label, command, timeout_seconds in steps:
                process_result = self._run_docker_container(
                    runner,
                    request=request,
                    workspace_path=workspace_path,
                    artifact_path=artifact_path,
                    env_file_path=env_file_path,
                    docker_cli_env=docker_cli_env,
                    label=label,
                    command=command,
                    timeout_seconds=timeout_seconds,
                )
                if label == "clone" and git_auth_files is not None:
                    _remove_git_auth_files(git_auth_files)
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

            try:
                _ensure_files_allowed(files_changed, request.allowed_paths)
            except DockerSandboxError:
                return _failed_result(
                    "artifact_capture_failed",
                    "docker_local",
                    command_results,
                    test_results,
                    files_changed=files_changed,
                    diff_text=diff_text,
                    base_sha=captured["base-sha"].stdout.strip() or None,
                    head_sha=captured["head-sha"].stdout.strip() or None,
                    worktree_ref=f"cloud://docker-local/{request.cloud_run_id}",
                )

            test_status = "passed" if request.test_commands else "not_run"
            for command in request.test_commands:
                process_result = self._run_docker_container(
                    runner,
                    request=request,
                    workspace_path=workspace_path,
                    artifact_path=artifact_path,
                    env_file_path=env_file_path,
                    docker_cli_env=docker_cli_env,
                    label=f"test-{command.key}",
                    command=command.command,
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
                tests_run=[command.command for command in request.test_commands],
                test_result=test_status,
                risks=[],
                diff_text=diff_text,
                command_results=command_results,
                test_command_results=test_results,
                failure_reason=failure_reason,
            )

    def _run_docker_container(
        self,
        runner: ProcessRunner,
        *,
        request: SandboxExecutionRequest,
        workspace_path: Path,
        artifact_path: Path,
        env_file_path: Path | None,
        docker_cli_env: dict[str, str],
        label: str,
        command: str,
        timeout_seconds: int,
    ) -> ProcessResult:
        container_name = _docker_container_name(request.cloud_run_id, label)
        process_result = runner.run(
            self.build_docker_run_args(
                request=request,
                workspace_path=workspace_path,
                artifact_path=artifact_path,
                env_file_path=env_file_path,
                command=command,
                timeout_seconds=timeout_seconds,
                container_name=container_name,
            ),
            env=docker_cli_env,
            timeout_seconds=timeout_seconds,
        )
        if process_result.timed_out:
            runner.run(
                ["docker", "rm", "-f", container_name],
                env=docker_cli_env,
                timeout_seconds=15,
            )
        return process_result


def _output_to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _docker_cli_env() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if name.upper() in _DOCKER_CLI_ENV_ALLOWLIST and value
    }


def _safe_container_env_names(container_env: dict[str, str]) -> list[str]:
    return sorted(name for name in container_env if _is_safe_sandbox_env_name(name))


def _write_docker_env_file(path: Path, container_env: dict[str, str]) -> Path | None:
    items = [
        (name, container_env[name])
        for name in _safe_container_env_names(container_env)
        if _is_safe_docker_env_value(container_env[name])
    ]
    if not items:
        return None

    path.write_text(
        "".join(f"{name}={value}\n" for name, value in items),
        encoding="utf-8",
    )
    return path


def _write_git_auth_files(artifact_path: Path, github_token: str | None) -> GitAuthFiles | None:
    if not github_token:
        return None

    askpass_path = artifact_path / "git-askpass.sh"
    token_path = artifact_path / "github-token"
    token_path.write_text(github_token, encoding="utf-8")
    askpass_path.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "case \"$1\" in",
                "*Username*) printf '%s\\n' 'x-access-token' ;;",
                "*) cat /artifacts/github-token ;;",
                "esac",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return GitAuthFiles(askpass_path=askpass_path, token_path=token_path)


def _remove_git_auth_files(auth_files: GitAuthFiles) -> None:
    for path in (auth_files.askpass_path, auth_files.token_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _git_clone_command(repo_url: str, *, use_askpass: bool) -> str:
    clone_command = f"git clone -- {_shell_quote(repo_url)} ."
    if not use_askpass:
        return clone_command
    return (
        "chmod 700 /artifacts/git-askpass.sh; "
        "GIT_ASKPASS=/artifacts/git-askpass.sh "
        "GIT_TERMINAL_PROMPT=0 "
        f"{clone_command}; "
        "status=$?; "
        "rm -f /artifacts/git-askpass.sh /artifacts/github-token; "
        "exit $status"
    )


def _is_safe_sandbox_env_name(name: str) -> bool:
    normalized = name.upper()
    return (
        bool(_ENV_NAME_RE.fullmatch(name))
        and normalized not in _DOCKER_CLI_ENV_DENYLIST
    )


def _is_safe_docker_env_value(value: str) -> bool:
    return "\n" not in value and "\r" not in value


def _validated_docker_image(image: str | None) -> str | None:
    if image is None:
        return _DEFAULT_DOCKER_IMAGE
    candidate = image.strip()
    if (
        candidate == ""
        or candidate.startswith("-")
        or _DOCKER_IMAGE_RE.fullmatch(candidate) is None
    ):
        return None
    return candidate


def validate_docker_image(image: str | None) -> str | None:
    return _validated_docker_image(image)


def _valid_git_reference_inputs(request: SandboxExecutionRequest) -> bool:
    return (
        _valid_git_branch_name(request.base_branch)
        and _valid_git_branch_name(request.head_branch)
    )


def _valid_git_branch_name(branch_name: str) -> bool:
    candidate = branch_name.strip()
    return candidate != "" and not candidate.startswith("-")


def _is_safe_authenticated_github_url(repo_url: str) -> bool:
    try:
        parsed = urlsplit(repo_url)
    except ValueError:
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return False
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 2 or path_parts[0] == "" or path_parts[1] == "":
        return False
    if parsed.query or parsed.fragment:
        return False
    return True


def _shell_quote(value: str) -> str:
    return shlex.quote(value)


def _docker_container_name(cloud_run_id: str, label: str) -> str:
    return f"ai-scdc-{_docker_name_part(cloud_run_id)}-{_docker_name_part(label)}"[:120]


def _docker_name_part(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    return candidate or "run"


def _failed_result(
    failure_reason: str,
    runner_kind: str,
    command_results: list[CommandResult],
    test_results: list[CommandResult],
    *,
    base_sha: str | None = None,
    head_sha: str | None = None,
    worktree_ref: str | None = None,
    files_changed: list[str] | None = None,
    diff_text: str = "",
) -> SandboxExecutionResult:
    return SandboxExecutionResult(
        status="failed",
        runner_kind=runner_kind,
        base_sha=base_sha,
        head_sha=head_sha,
        worktree_ref=worktree_ref,
        summary="",
        files_changed=files_changed or [],
        tests_run=[],
        test_result="not_run",
        risks=[],
        diff_text=diff_text,
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
