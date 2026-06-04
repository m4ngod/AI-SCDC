from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Protocol, Sequence
from urllib import request as urllib_request


@dataclass(frozen=True)
class RemoteWorkerConfig:
    api_base_url: str
    cloud_run_id: str
    worker_id: str
    queue_provider: str
    storage_provider: str
    callback_token: str


@dataclass(frozen=True)
class RemoteProcessResult:
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
        }


ProcessRun = Callable[..., Any]
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CHILD_PROCESS_ENV_ALLOWLIST = {
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "PATHEXT",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
    "WINDIR",
}


class RemoteWorkerClient(Protocol):
    def claim(self, config: RemoteWorkerConfig) -> dict[str, Any]:
        ...

    def heartbeat(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
    ) -> dict[str, Any]:
        ...

    def payload(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
    ) -> dict[str, Any]:
        ...

    def upload_artifact(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
        *,
        kind: str,
        content: str,
        content_type: str,
    ) -> dict[str, Any]:
        ...

    def complete(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class RemoteWorkerCheckout(Protocol):
    def checkout(self, payload: dict[str, Any]) -> str:
        ...


class RemoteWorkerCommandRunner(Protocol):
    def run(self, payload: dict[str, Any], repo_path: str) -> dict[str, Any]:
        ...


def _run_process(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class HttpRemoteWorkerClient:
    def __init__(self, api_base_url: str) -> None:
        self._api_base_url = api_base_url.rstrip("/")

    def claim(self, config: RemoteWorkerConfig) -> dict[str, Any]:
        return self._post_json(
            "/cloud-run-worker/leases",
            {
                "worker_id": config.worker_id,
                "worker_kind": "aliyun_eci",
                "queue_provider": config.queue_provider,
                "cloud_run_id": config.cloud_run_id,
                "callback_token": config.callback_token,
                "lease_seconds": 300,
            },
        )

    def heartbeat(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/heartbeat",
            {
                "worker_id": worker_id,
                "callback_token": callback_token,
                "lease_seconds": 300,
            },
        )

    def payload(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/payload",
            {
                "worker_id": worker_id,
                "callback_token": callback_token,
            },
        )

    def upload_artifact(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
        *,
        kind: str,
        content: str,
        content_type: str,
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/artifacts",
            {
                "worker_id": worker_id,
                "callback_token": callback_token,
                "kind": kind,
                "content": content,
                "content_type": content_type,
            },
        )

    def complete(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/complete",
            {
                "worker_id": worker_id,
                "callback_token": callback_token,
                "result": result["result"],
            },
        )

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(
            f"{self._api_base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


class RemoteWorkerGitCheckout:
    def __init__(
        self,
        *,
        workspace_root: Path | str | None = None,
        process_run: ProcessRun = _run_process,
    ) -> None:
        self._workspace_root = Path(
            workspace_root
            if workspace_root is not None
            else Path(tempfile.gettempdir()) / "ai-scdc-remote-worker"
        )
        self._process_run = process_run

    def checkout(self, payload: dict[str, Any]) -> str:
        cloud_run_id = _safe_path_segment(payload.get("cloud_run_id", "cloud-run"))
        repo_url = str(payload["repo_url"])
        base_branch = str(payload.get("base_branch") or "main")
        head_branch = str(payload["head_branch"])
        clone_token = str(payload.get("clone_token") or "")
        workspace_root = self._workspace_root.resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        run_dir = _safe_workspace_child(workspace_root, cloud_run_id)
        repo_dir = run_dir / "repo"

        shutil.rmtree(run_dir, ignore_errors=True)
        repo_dir.mkdir(parents=True, exist_ok=True)

        credentials_dir: Path | None = None
        try:
            credentials_dir = self._create_askpass_files(run_dir, clone_token)
            env = self._checkout_env(credentials_dir / "askpass.sh")
            self._must_run(
                ["git", "clone", "--", repo_url, "."],
                cwd=repo_dir,
                env=env,
                timeout=_timeout_seconds(payload.get("clone_timeout_seconds"), 600),
            )
            self._must_run(
                ["git", "checkout", base_branch],
                cwd=repo_dir,
                env=env,
                timeout=_timeout_seconds(payload.get("checkout_timeout_seconds"), 120),
            )
            self._must_run(
                ["git", "checkout", "-B", head_branch],
                cwd=repo_dir,
                env=env,
                timeout=_timeout_seconds(payload.get("checkout_timeout_seconds"), 120),
            )
        finally:
            if credentials_dir is not None:
                shutil.rmtree(credentials_dir, ignore_errors=True)
            _cleanup_credential_dirs(run_dir)

        return str(repo_dir)

    def _create_askpass_files(self, run_dir: Path, clone_token: str) -> Path:
        credentials_dir = Path(
            tempfile.mkdtemp(prefix=".git-credentials-", dir=str(run_dir))
        )
        token_path = credentials_dir / "credential"
        askpass_path = credentials_dir / "askpass.sh"
        token_path.write_text(clone_token, encoding="utf-8")
        askpass_path.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    'case "$1" in',
                    '  *Username*|*username*) printf "%s\\n" "x-access-token" ;;',
                    f"  *) cat {shlex.quote(str(token_path))} ;;",
                    "esac",
                    "",
                ]
            ),
            encoding="utf-8",
            newline="\n",
        )
        try:
            os.chmod(token_path, 0o600)
            os.chmod(askpass_path, 0o700)
        except OSError:
            pass
        return credentials_dir

    def _checkout_env(
        self,
        askpass_path: Path,
    ) -> dict[str, str]:
        env = _base_child_process_env()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = str(askpass_path)
        return env

    def _must_run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int | float | None,
    ) -> None:
        try:
            result = self._process_run(args, cwd=cwd, env=env, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("repo_checkout_failed") from exc
        if result.returncode != 0:
            raise RuntimeError("repo_checkout_failed")


class RemoteWorkerCommandRunnerImpl:
    def __init__(self, *, process_run: ProcessRun = _run_process) -> None:
        self._process_run = process_run

    def run(self, payload: dict[str, Any], repo_path: str) -> dict[str, Any]:
        repo = Path(repo_path)
        process_env = self._process_env(payload.get("env") or {})
        patch_command = payload.get("patch_command") or {}
        patch_text = str(patch_command.get("command") or "").strip()
        command_results: list[dict[str, Any]] = []
        test_command_results: list[dict[str, Any]] = []
        tests_run: list[str] = []
        risks: list[str] = []
        files_changed: list[str] = []
        diff_text = ""
        base_sha: str | None = None
        head_sha: str | None = None
        failure_reason: str | None = None
        test_result = "not_run"

        if not patch_text:
            failure_reason = "patch_command_failed"
        else:
            patch_result = self._run_shell(
                patch_text,
                cwd=repo,
                env=process_env,
                timeout=_timeout_seconds(patch_command.get("timeout_seconds"), None),
            )
            command_results.append(patch_result.as_dict())
            if patch_result.timed_out or patch_result.exit_code != 0:
                failure_reason = "patch_command_failed"

        artifact_results: dict[str, RemoteProcessResult] = {}
        if failure_reason is None:
            artifact_results = self._capture_artifacts(repo, process_env, payload)
            command_results.extend(
                result.as_dict() for result in artifact_results.values()
            )
            if any(
                result.timed_out or result.exit_code != 0
                for result in artifact_results.values()
            ):
                failure_reason = "artifact_capture_failed"
            else:
                files_changed = _parse_lines(
                    artifact_results["diff_name_only"].stdout
                )
                diff_text = artifact_results["diff_text"].stdout
                base_sha = artifact_results["base_sha"].stdout.strip() or None
                head_sha = artifact_results["head_sha"].stdout.strip() or None
                if not files_changed or not diff_text.strip():
                    failure_reason = "no_patch_produced"
                else:
                    try:
                        _ensure_files_allowed(
                            files_changed,
                            [str(path) for path in payload.get("allowed_paths") or []],
                        )
                    except RuntimeError as exc:
                        failure_reason = "artifact_capture_failed"
                        risks.append(str(exc))

        if failure_reason is None:
            test_commands = list(payload.get("test_commands") or [])
            if _cancel_requested(payload):
                failure_reason = "cancelled"
            elif test_commands:
                for test_command in test_commands:
                    command = str(test_command.get("command") or "").strip()
                    if not command:
                        continue
                    tests_run.append(command)
                    result = self._run_shell(
                        command,
                        cwd=repo,
                        env=process_env,
                        timeout=_timeout_seconds(
                            test_command.get("timeout_seconds"),
                            None,
                        ),
                    )
                    test_command_results.append(result.as_dict())
                if any(
                    result.get("timed_out") or result.get("exit_code") != 0
                    for result in test_command_results
                ):
                    failure_reason = "test_failed"
                    test_result = "failed"
                else:
                    test_result = "passed"

        status = "patch_ready" if failure_reason is None else "failed"
        summary = self._summary(status, failure_reason, test_result)
        return {
            "status": status,
            "runner_kind": "aliyun_eci",
            "base_sha": base_sha,
            "head_sha": head_sha,
            "worktree_ref": f"remote-worker://{payload.get('cloud_run_id')}",
            "summary": summary,
            "files_changed": files_changed,
            "tests_run": tests_run,
            "test_result": test_result,
            "risks": risks,
            "diff_text": diff_text,
            "command_results": command_results,
            "test_command_results": test_command_results,
            "failure_reason": failure_reason,
        }

    def _capture_artifacts(
        self,
        repo: Path,
        env: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, RemoteProcessResult]:
        base_branch = str(payload.get("base_branch") or "main")
        base_ref = shlex.quote(f"origin/{base_branch}")
        commands = {
            "add_intent": "git add -N .",
            "diff_name_only": "git diff --name-only",
            "diff_text": "git diff --no-ext-diff",
            "base_sha": f"git rev-parse {base_ref}",
            "head_sha": "git rev-parse HEAD",
        }
        return {
            key: self._run_shell(command, cwd=repo, env=env, timeout=60)
            for key, command in commands.items()
        }

    def _run_shell(
        self,
        command: str,
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int | float | None,
    ) -> RemoteProcessResult:
        started_at = time.monotonic()
        try:
            result = self._process_run(
                ["sh", "-lc", command],
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
            return RemoteProcessResult(
                command=command,
                exit_code=result.returncode,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                duration_ms=_duration_ms(started_at),
            )
        except subprocess.TimeoutExpired as exc:
            return RemoteProcessResult(
                command=command,
                exit_code=None,
                stdout=_timeout_output(exc.stdout),
                stderr=_timeout_output(exc.stderr),
                duration_ms=_duration_ms(started_at),
                timed_out=True,
            )

    def _process_env(self, payload_env: dict[str, Any]) -> dict[str, str]:
        env = _base_child_process_env()
        for key, value in payload_env.items():
            if value is not None and _is_safe_env_name(str(key)):
                env[str(key)] = str(value)
        return env

    def _summary(
        self,
        status: str,
        failure_reason: str | None,
        test_result: str,
    ) -> str:
        if status == "patch_ready" and test_result == "passed":
            return "Remote worker produced a patch and tests passed."
        if status == "patch_ready":
            return "Remote worker produced a patch."
        return f"Remote worker failed: {failure_reason}."


def _safe_path_segment(value: Any) -> str:
    text = str(value or "cloud-run").strip()
    segment = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return segment or "cloud-run"


def _safe_workspace_child(workspace_root: Path, segment: str) -> Path:
    candidate = (workspace_root / segment).resolve()
    try:
        candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise RuntimeError("repo_checkout_failed") from exc
    if candidate == workspace_root:
        raise RuntimeError("repo_checkout_failed")
    return candidate


def _cleanup_credential_dirs(run_dir: Path) -> None:
    if not run_dir.exists():
        return
    for path in run_dir.glob(".git-credentials-*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _base_child_process_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if value and key.upper() in _CHILD_PROCESS_ENV_ALLOWLIST
    }


def _is_safe_env_name(name: str) -> bool:
    return _ENV_NAME_RE.fullmatch(name) is not None


def _ensure_files_allowed(files_changed: list[str], allowed_paths: list[str]) -> None:
    if not allowed_paths:
        raise RuntimeError("Task has no allowed_paths for remote worker changes")

    for file_changed in files_changed:
        normalized_file = _normalize_path_pattern(file_changed)
        if not any(
            _path_matches_allowed(normalized_file, allowed_path)
            for allowed_path in allowed_paths
        ):
            raise RuntimeError(
                f"Changed file is outside allowed_paths: {normalized_file}"
            )


def _normalize_path_pattern(path_pattern: Any) -> str:
    return str(path_pattern).replace("\\", "/").strip()


def _is_safe_relative_pattern(path_pattern: str) -> bool:
    if path_pattern == "":
        return False
    if path_pattern.startswith("/") or re.match(r"^[A-Za-z]:", path_pattern):
        return False
    parts = [part for part in path_pattern.split("/") if part not in {"", "."}]
    return ".." not in parts


def _path_matches_allowed(file_changed: str, allowed_path: str) -> bool:
    normalized_allowed = _normalize_path_pattern(allowed_path)
    if not _is_safe_relative_pattern(normalized_allowed):
        return False
    if normalized_allowed.endswith("/**"):
        prefix = normalized_allowed.removesuffix("/**")
        return file_changed == prefix or file_changed.startswith(f"{prefix}/")
    return fnmatch.fnmatchcase(file_changed, normalized_allowed)


def _cancel_requested(payload: dict[str, Any]) -> bool:
    callback = payload.get("_remote_worker_cancel_requested")
    if not callable(callback):
        return False
    return callback() is True


def _timeout_seconds(
    value: Any,
    default: int | float | None,
) -> int | float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _duration_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def _timeout_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _parse_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _redact_text(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in sorted((secret for secret in secrets if secret), key=len, reverse=True):
        redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _redacted_command_result(
    result: dict[str, Any],
    secrets: list[str],
) -> dict[str, Any]:
    return {
        "command": _redact_text(result.get("command", ""), secrets),
        "exit_code": result.get("exit_code"),
        "stdout": _redact_text(result.get("stdout", ""), secrets),
        "stderr": _redact_text(result.get("stderr", ""), secrets),
        "duration_ms": result.get("duration_ms", 0),
        "timed_out": result.get("timed_out", False),
    }


def _redacted_optional_text(value: Any, secrets: list[str]) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return value


def _redacted_string_list(values: list[Any], secrets: list[str]) -> list[Any]:
    return [
        _redact_text(value, secrets) if isinstance(value, str) else value
        for value in values
    ]


def _payload_secrets(
    payload: dict[str, Any],
    callback_token: str,
) -> list[str]:
    payload_env = payload.get("env") or {}
    return [
        str(secret)
        for secret in [
            payload.get("clone_token", ""),
            callback_token,
            *list(payload_env.values()),
        ]
        if secret
    ]


def _failed_execution(failure_reason: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "runner_kind": "aliyun_eci",
        "base_sha": None,
        "head_sha": None,
        "worktree_ref": None,
        "summary": f"Remote worker failed: {failure_reason}.",
        "files_changed": [],
        "tests_run": [],
        "test_result": "not_run",
        "risks": [],
        "diff_text": "",
        "command_results": [],
        "test_command_results": [],
        "failure_reason": failure_reason,
    }


@dataclass
class RemoteWorkerExecutor:
    client: RemoteWorkerClient
    checkout: RemoteWorkerCheckout
    command_runner: RemoteWorkerCommandRunner

    def run_once(self, config: RemoteWorkerConfig) -> dict[str, Any]:
        lease = self.client.claim(config)
        lease_id = lease["lease_id"]
        payload = self.client.payload(lease_id, config.worker_id, config.callback_token)
        secrets = _payload_secrets(payload, config.callback_token)
        first_heartbeat = self.client.heartbeat(
            lease_id,
            config.worker_id,
            config.callback_token,
        )
        if first_heartbeat.get("cancel_requested") is True:
            return self._complete_cancelled(config, lease_id)
        try:
            repo_path = self.checkout.checkout(payload)
        except RuntimeError as exc:
            failure_reason = (
                "repo_checkout_failed"
                if str(exc) == "repo_checkout_failed"
                else "worker_execution_failed"
            )
            execution = _failed_execution(failure_reason)
            return self._complete_execution(config, lease_id, execution, secrets)
        except Exception:
            execution = _failed_execution("worker_execution_failed")
            return self._complete_execution(config, lease_id, execution, secrets)
        try:
            runner_payload = {
                **payload,
                "_remote_worker_cancel_requested": lambda: self._cancel_requested(
                    config,
                    lease_id,
                ),
            }
            execution = self.command_runner.run(runner_payload, repo_path)
        except Exception:
            execution = _failed_execution("worker_execution_failed")
            return self._complete_execution(config, lease_id, execution, secrets)
        if execution.get("failure_reason") == "cancelled":
            return self._complete_execution(config, lease_id, execution, secrets)
        second_heartbeat = self.client.heartbeat(
            lease_id,
            config.worker_id,
            config.callback_token,
        )
        if second_heartbeat.get("cancel_requested") is True:
            execution = {
                **execution,
                "status": "failed",
                "failure_reason": "cancelled",
                "test_result": execution.get("test_result", "not_run"),
            }
        return self._complete_execution(config, lease_id, execution, secrets)

    def _cancel_requested(
        self,
        config: RemoteWorkerConfig,
        lease_id: str,
    ) -> bool:
        heartbeat = self.client.heartbeat(
            lease_id,
            config.worker_id,
            config.callback_token,
        )
        return heartbeat.get("cancel_requested") is True

    def _complete_execution(
        self,
        config: RemoteWorkerConfig,
        lease_id: str,
        execution: dict[str, Any],
        secrets: list[str],
    ) -> dict[str, Any]:
        artifact_refs = self._upload_artifacts(config, lease_id, execution, secrets)
        completion = self._completion_payload(execution, artifact_refs, secrets)
        return self.client.complete(
            lease_id,
            config.worker_id,
            config.callback_token,
            {"result": completion},
        )

    def _complete_cancelled(
        self,
        config: RemoteWorkerConfig,
        lease_id: str,
    ) -> dict[str, Any]:
        return self.client.complete(
            lease_id,
            config.worker_id,
            config.callback_token,
            {
                "result": {
                    "status": "failed",
                    "runner_kind": "aliyun_eci",
                    "base_sha": None,
                    "head_sha": None,
                    "worktree_ref": None,
                    "summary": "Remote worker cancelled before checkout.",
                    "files_changed": [],
                    "tests_run": [],
                    "test_result": "not_run",
                    "risks": [],
                    "diff_text": "",
                    "artifact_refs": [],
                    "command_results": [],
                    "test_command_results": [],
                    "failure_reason": "cancelled",
                }
            },
        )

    def _upload_artifacts(
        self,
        config: RemoteWorkerConfig,
        lease_id: str,
        execution: dict[str, Any],
        secrets: list[str],
    ) -> list[dict[str, Any]]:
        command_results = [
            _redacted_command_result(result, secrets)
            for result in execution.get("command_results", [])
        ]
        test_results = [
            _redacted_command_result(result, secrets)
            for result in execution.get("test_command_results", [])
        ]
        uploads = [
            (
                "diff",
                _redact_text(execution.get("diff_text") or "", secrets),
                "text/x-diff",
            ),
            (
                "command_result",
                json.dumps(command_results, sort_keys=True),
                "application/json",
            ),
            (
                "test_result",
                json.dumps(test_results, sort_keys=True),
                "application/json",
            ),
            (
                "log",
                _redact_text(execution.get("summary", ""), secrets),
                "text/plain",
            ),
        ]
        artifact_refs: list[dict[str, Any]] = []
        for kind, content, content_type in uploads:
            artifact_refs.append(
                self.client.upload_artifact(
                    lease_id,
                    config.worker_id,
                    config.callback_token,
                    kind=kind,
                    content=content,
                    content_type=content_type,
                )
            )
        manifest = {
            "cloud_run_id": config.cloud_run_id,
            "artifacts": artifact_refs,
            "status": execution.get("status"),
            "failure_reason": _redacted_optional_text(
                execution.get("failure_reason"),
                secrets,
            ),
        }
        artifact_refs.append(
            self.client.upload_artifact(
                lease_id,
                config.worker_id,
                config.callback_token,
                kind="manifest",
                content=json.dumps(manifest, sort_keys=True),
                content_type="application/json",
            )
        )
        return artifact_refs

    def _completion_payload(
        self,
        execution: dict[str, Any],
        artifact_refs: list[dict[str, Any]],
        secrets: list[str],
    ) -> dict[str, Any]:
        return {
            "status": execution.get("status", "failed"),
            "runner_kind": execution.get("runner_kind", "aliyun_eci"),
            "base_sha": execution.get("base_sha"),
            "head_sha": execution.get("head_sha"),
            "worktree_ref": _redacted_optional_text(
                execution.get("worktree_ref"),
                secrets,
            ),
            "summary": _redact_text(execution.get("summary", ""), secrets),
            "files_changed": _redacted_string_list(
                execution.get("files_changed", []),
                secrets,
            ),
            "tests_run": _redacted_string_list(
                execution.get("tests_run", []),
                secrets,
            ),
            "test_result": _redacted_optional_text(
                execution.get("test_result", "not_run"),
                secrets,
            ),
            "risks": _redacted_string_list(execution.get("risks", []), secrets),
            "diff_text": "",
            "artifact_refs": artifact_refs,
            "command_results": [
                _redacted_command_result(result, secrets)
                for result in execution.get("command_results", [])
            ],
            "test_command_results": [
                _redacted_command_result(result, secrets)
                for result in execution.get("test_command_results", [])
            ],
            "failure_reason": _redacted_optional_text(
                execution.get("failure_reason"),
                secrets,
            ),
        }


def run_remote_worker_once(
    config: RemoteWorkerConfig,
    *,
    client: RemoteWorkerClient | None = None,
    checkout: RemoteWorkerCheckout | None = None,
    command_runner: RemoteWorkerCommandRunner | None = None,
) -> dict[str, Any]:
    resolved_client = client or HttpRemoteWorkerClient(config.api_base_url)
    resolved_checkout = checkout or RemoteWorkerGitCheckout()
    resolved_command_runner = command_runner or RemoteWorkerCommandRunnerImpl()
    return RemoteWorkerExecutor(
        client=resolved_client,
        checkout=resolved_checkout,
        command_runner=resolved_command_runner,
    ).run_once(config)


def run_deterministic_remote_worker_once(
    config: RemoteWorkerConfig,
    *,
    client: RemoteWorkerClient | None = None,
) -> dict[str, Any]:
    resolved_client = client or HttpRemoteWorkerClient(config.api_base_url)
    lease = resolved_client.claim(config)
    lease_id = lease["lease_id"]
    resolved_client.heartbeat(lease_id, config.worker_id, config.callback_token)
    diff_text = _deterministic_diff(config.cloud_run_id)
    diff_ref = resolved_client.upload_artifact(
        lease_id,
        config.worker_id,
        config.callback_token,
        kind="diff",
        content=diff_text,
        content_type="text/x-diff",
    )
    completion = {
        "result": {
            "status": "patch_ready",
            "runner_kind": "aliyun_eci",
            "base_sha": None,
            "head_sha": None,
            "worktree_ref": f"aliyun-eci://{config.cloud_run_id}",
            "summary": (
                "Aliyun ECI remote worker produced a deterministic smoke patch."
            ),
            "files_changed": ["AI_SCDC_ALIYUN_ECI.md"],
            "tests_run": [],
            "test_result": "not_run",
            "risks": [],
            "diff_text": "",
            "artifact_refs": [diff_ref],
            "command_results": [],
            "test_command_results": [],
            "failure_reason": None,
        }
    }
    return resolved_client.complete(
        lease_id,
        config.worker_id,
        config.callback_token,
        completion,
    )


def config_from_env() -> RemoteWorkerConfig:
    return RemoteWorkerConfig(
        api_base_url=_required_env("AI_SCDC_API_BASE_URL"),
        cloud_run_id=_required_env("AI_SCDC_CLOUD_RUN_ID"),
        worker_id=_required_env("AI_SCDC_WORKER_ID"),
        queue_provider=os.getenv("AI_SCDC_QUEUE_PROVIDER", "aliyun_mns"),
        storage_provider=os.getenv("AI_SCDC_STORAGE_PROVIDER", "aliyun_oss"),
        callback_token=_required_env("AI_SCDC_CALLBACK_TOKEN"),
    )


def main() -> None:
    run_remote_worker_once(config_from_env())


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _deterministic_diff(cloud_run_id: str) -> str:
    return (
        "diff --git a/AI_SCDC_ALIYUN_ECI.md b/AI_SCDC_ALIYUN_ECI.md\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/AI_SCDC_ALIYUN_ECI.md\n"
        "@@ -0,0 +1,3 @@\n"
        "+# AI-SCDC Aliyun ECI Smoke\n"
        f"+Cloud run: {cloud_run_id}\n"
        "+Provider: aliyun_eci\n"
    )


if __name__ == "__main__":
    main()
