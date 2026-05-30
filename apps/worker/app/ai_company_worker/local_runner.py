import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LocalRunnerError(RuntimeError):
    """Raised when the local runner cannot safely produce a patch."""


class LocalRunnerRequest(BaseModel):
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    repo_path: Path
    title: str = Field(min_length=1)
    description: str = ""
    allowed_paths: list[str] = Field(default_factory=list)
    required_tests: list[str] = Field(default_factory=list)


class LocalRunnerResult(BaseModel):
    status: Literal["patch_ready"]
    summary: str
    files_changed: list[str]
    tests_run: list[str]
    test_result: Literal["not_run"]
    risks: list[str]
    diff_text: str
    worktree_path: str
    base_sha: str
    head_sha: str


def run_local_task(request: LocalRunnerRequest) -> LocalRunnerResult:
    repo_path = request.repo_path.resolve()
    _validate_repo_root(repo_path)
    _ensure_no_tracked_source_changes(repo_path)
    base_sha = _git(repo_path, "rev-parse", "HEAD")

    worktree_root = (repo_path / ".worktrees").resolve()
    worktree_path = (worktree_root / _worktree_name(request)).resolve()
    _ensure_inside(worktree_path, worktree_root)
    if worktree_path.exists():
        raise LocalRunnerError(f"Worktree already exists: {worktree_path}")

    worktree_root.mkdir(exist_ok=True)
    _git(repo_path, "worktree", "add", "--detach", str(worktree_path), base_sha)

    patch_path = _select_patch_path(worktree_path, request.allowed_paths)
    _write_runner_note(patch_path, request)
    _intent_to_add_if_untracked(worktree_path, patch_path)

    files_changed = _changed_files(worktree_path)
    ensure_changed_files_allowed(files_changed, request.allowed_paths)
    diff_text = _git(worktree_path, "diff", "--no-ext-diff")
    if diff_text.strip() == "":
        raise LocalRunnerError("Local runner did not produce a diff")

    head_sha = _git(worktree_path, "rev-parse", "HEAD")
    return LocalRunnerResult(
        status="patch_ready",
        summary=f"Prepared local runner patch for {request.title}.",
        files_changed=files_changed,
        tests_run=list(request.required_tests),
        test_result="not_run",
        risks=[
            "Phase 4 local runner produced a deterministic patch; tests were not executed.",
        ],
        diff_text=diff_text,
        worktree_path=str(worktree_path),
        base_sha=base_sha,
        head_sha=head_sha,
    )


def ensure_changed_files_allowed(
    files_changed: list[str],
    allowed_paths: list[str],
) -> None:
    if not allowed_paths:
        raise LocalRunnerError("Task has no allowed_paths for local runner changes")

    for file_changed in files_changed:
        normalized_file = _normalize_path_pattern(file_changed)
        if not any(
            _path_matches_allowed(normalized_file, allowed_path)
            for allowed_path in allowed_paths
        ):
            raise LocalRunnerError(
                f"Changed file is outside allowed_paths: {normalized_file}"
            )


def _validate_repo_root(repo_path: Path) -> None:
    if not repo_path.exists():
        raise LocalRunnerError(f"Repository path does not exist: {repo_path}")

    try:
        root = Path(_git(repo_path, "rev-parse", "--show-toplevel")).resolve()
    except LocalRunnerError as exc:
        raise LocalRunnerError(f"Path is not a git repository: {repo_path}") from exc

    if root != repo_path:
        raise LocalRunnerError(f"Repository path must be the git root: {repo_path}")


def _ensure_no_tracked_source_changes(repo_path: Path) -> None:
    status = _git(repo_path, "status", "--porcelain", "--untracked-files=no")
    if status.strip():
        raise LocalRunnerError("Repository has tracked uncommitted changes")


def _select_patch_path(worktree_path: Path, allowed_paths: list[str]) -> Path:
    for allowed_path in allowed_paths:
        normalized = _normalize_path_pattern(allowed_path)
        if not _is_safe_relative_pattern(normalized):
            continue

        if normalized.endswith("/**"):
            directory = normalized.removesuffix("/**")
            return _safe_worktree_path(worktree_path, f"{directory}/local-runner-note.md")

        if not _contains_glob(normalized):
            return _safe_worktree_path(worktree_path, normalized)

    raise LocalRunnerError("No safe allowed path is available for local runner patch")


def _write_runner_note(file_path: Path, request: LocalRunnerRequest) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    existing = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
    separator = "" if existing == "" or existing.endswith("\n") else "\n"
    note = (
        f"{separator}\n"
        "<!-- ai-company-local-runner -->\n"
        f"Local runner prepared task `{request.task_id}`: {request.title}\n"
    )
    file_path.write_text(f"{existing}{note}", encoding="utf-8")


def _intent_to_add_if_untracked(worktree_path: Path, file_path: Path) -> None:
    relative_path = file_path.relative_to(worktree_path).as_posix()
    tracked = _git(
        worktree_path,
        "ls-files",
        "--error-unmatch",
        relative_path,
        allow_failure=True,
    )
    if tracked == "":
        _git(worktree_path, "add", "-N", "--", relative_path)


def _changed_files(worktree_path: Path) -> list[str]:
    output = _git(worktree_path, "diff", "--name-only")
    return sorted(line.strip() for line in output.splitlines() if line.strip())


def _git(
    cwd: Path,
    *args: str,
    timeout_seconds: float = 30.0,
    allow_failure: bool = False,
) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalRunnerError(f"git command failed: {' '.join(args)}") from exc

    if result.returncode != 0:
        if allow_failure:
            return ""
        detail = (result.stderr or result.stdout).strip()
        raise LocalRunnerError(
            f"git command failed ({' '.join(args)}): {detail}"
        )
    return result.stdout.strip()


def _safe_worktree_path(worktree_path: Path, relative_path: str) -> Path:
    candidate = (worktree_path / relative_path).resolve()
    _ensure_inside(candidate, worktree_path)
    return candidate


def _ensure_inside(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise LocalRunnerError(f"Path escapes expected root: {path}") from exc


def _worktree_name(request: LocalRunnerRequest) -> str:
    task = _safe_segment(request.task_id)
    run = _safe_segment(request.run_id)
    return f"{task}-{run}"


def _safe_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return segment or "run"


def _contains_glob(path_pattern: str) -> bool:
    return any(character in path_pattern for character in "*?[")


def _normalize_path_pattern(path_pattern: str) -> str:
    return path_pattern.replace("\\", "/").strip()


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
