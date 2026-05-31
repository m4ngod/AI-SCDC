import os
from dataclasses import dataclass, replace
from importlib import import_module
from urllib.parse import unquote, urlsplit
from typing import Protocol


def redact_secrets(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in sorted((secret for secret in secrets if secret), key=len, reverse=True):
        redacted = redacted.replace(secret, "[redacted]")
    return redacted


def repo_url_redaction_secrets(repo_url: str) -> list[str]:
    try:
        parsed = urlsplit(repo_url)
    except ValueError:
        return []
    if not parsed.netloc or "@" not in parsed.netloc:
        return []

    userinfo = parsed.netloc.rsplit("@", 1)[0]
    secrets = [repo_url, userinfo, f"{userinfo}@"]
    if parsed.username:
        secrets.extend([parsed.username, unquote(parsed.username)])
    if parsed.password:
        secrets.extend([parsed.password, unquote(parsed.password)])
    return list(dict.fromkeys(secret for secret in secrets if secret))


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    def as_payload(self, secrets: list[str] | None = None) -> dict:
        result = self.redacted(secrets or [])
        return {
            "command": result.command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    def redacted(self, secrets: list[str]) -> "CommandResult":
        return replace(
            self,
            command=redact_secrets(self.command, secrets),
            stdout=redact_secrets(self.stdout, secrets),
            stderr=redact_secrets(self.stderr, secrets),
        )


@dataclass(frozen=True)
class SandboxCommandSelection:
    key: str
    label: str
    command: str
    timeout_seconds: int = 300


@dataclass(frozen=True)
class SandboxExecutionRequest:
    task_id: str
    cloud_run_id: str
    title: str
    description: str
    repo_url: str
    base_branch: str
    head_branch: str
    allowed_paths: list[str]
    required_tests: list[str]
    docker_image: str | None
    patch_command: SandboxCommandSelection | None
    test_commands: list[SandboxCommandSelection]
    env: dict[str, str]
    network_enabled: bool


@dataclass(frozen=True)
class SandboxExecutionResult:
    status: str
    runner_kind: str
    base_sha: str | None
    head_sha: str | None
    worktree_ref: str | None
    summary: str
    files_changed: list[str]
    tests_run: list[str]
    test_result: str
    risks: list[str]
    diff_text: str
    command_results: list[CommandResult]
    test_command_results: list[CommandResult]
    failure_reason: str | None = None


class CloudSandboxExecutor(Protocol):
    sandbox_kind: str

    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        ...


class FakeCloudSandboxExecutor:
    sandbox_kind = "fake"

    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        return SandboxExecutionResult(
            status="patch_ready",
            runner_kind="cloud_fake",
            base_sha=None,
            head_sha=None,
            worktree_ref=f"cloud://fake/{request.cloud_run_id}",
            summary="Fake cloud run prepared a deterministic patch artifact.",
            files_changed=["AI_SCDC_CLOUD_RUN.md"],
            tests_run=[],
            test_result="not_run",
            risks=[],
            diff_text=_fake_cloud_diff(request),
            command_results=[],
            test_command_results=[],
            failure_reason=None,
        )


def select_cloud_sandbox_executor() -> CloudSandboxExecutor:
    runner = os.getenv("AI_SCDC_CLOUD_RUNNER", "fake").strip().lower()
    if runner == "docker_local":
        module = import_module("ai_company_api.services.docker_sandbox")
        return module.DockerLocalSandboxExecutor()
    if runner == "fake":
        return FakeCloudSandboxExecutor()
    raise ValueError(f"Unsupported cloud sandbox runner: {runner}")


def _fake_cloud_diff(request: SandboxExecutionRequest) -> str:
    return (
        "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/AI_SCDC_CLOUD_RUN.md\n"
        "@@ -0,0 +1,3 @@\n"
        "+# AI-SCDC Cloud Run\n"
        f"+Task: {request.title}\n"
        f"+Cloud run: {request.cloud_run_id}\n"
    )
