import subprocess
from pathlib import Path

import pytest

from ai_company_worker.local_runner import (
    LocalRunnerError,
    LocalRunnerRequest,
    ensure_changed_files_allowed,
    run_local_task,
)


def run_git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    run_git(repo_path, "init")
    (repo_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    run_git(repo_path, "add", "README.md")
    run_git(
        repo_path,
        "-c",
        "user.email=dev@example.com",
        "-c",
        "user.name=Dev User",
        "commit",
        "-m",
        "initial commit",
    )
    return repo_path


def test_local_runner_rejects_non_git_path(tmp_path: Path) -> None:
    with pytest.raises(LocalRunnerError, match="git repository"):
        run_local_task(
            LocalRunnerRequest(
                task_id="task_123",
                run_id="run_123",
                repo_path=tmp_path,
                title="Update README",
                allowed_paths=["README.md"],
            )
        )


def test_local_runner_creates_worktree_and_captures_diff(git_repo: Path) -> None:
    result = run_local_task(
        LocalRunnerRequest(
            task_id="task_123",
            run_id="run_123",
            repo_path=git_repo,
            title="Update README",
            allowed_paths=["README.md"],
            required_tests=["pytest apps/worker/tests/test_local_runner.py -v"],
        )
    )

    assert result.status == "patch_ready"
    assert result.files_changed == ["README.md"]
    assert result.tests_run == ["pytest apps/worker/tests/test_local_runner.py -v"]
    assert result.test_result == "not_run"
    assert "README.md" in result.diff_text
    assert result.base_sha
    assert result.head_sha == result.base_sha
    assert Path(result.worktree_path).is_dir()
    assert Path(result.worktree_path).is_relative_to(git_repo / ".worktrees")


def test_local_runner_does_not_modify_source_checkout(git_repo: Path) -> None:
    original_readme = (git_repo / "README.md").read_text(encoding="utf-8")

    run_local_task(
        LocalRunnerRequest(
            task_id="task_123",
            run_id="run_456",
            repo_path=git_repo,
            title="Update README",
            allowed_paths=["README.md"],
        )
    )

    assert (git_repo / "README.md").read_text(encoding="utf-8") == original_readme
    assert run_git(git_repo, "status", "--porcelain", "--untracked-files=no") == ""


def test_local_runner_can_create_note_under_allowed_directory_glob(
    git_repo: Path,
) -> None:
    result = run_local_task(
        LocalRunnerRequest(
            task_id="task_123",
            run_id="run_789",
            repo_path=git_repo,
            title="Write API note",
            allowed_paths=["apps/api/**"],
        )
    )

    assert result.files_changed == ["apps/api/local-runner-note.md"]
    assert "apps/api/local-runner-note.md" in result.diff_text


def test_local_runner_rejects_when_no_safe_allowed_path(git_repo: Path) -> None:
    with pytest.raises(LocalRunnerError, match="No safe allowed path"):
        run_local_task(
            LocalRunnerRequest(
                task_id="task_123",
                run_id="run_no_path",
                repo_path=git_repo,
                title="Update generated files",
                allowed_paths=["*.py"],
            )
        )


def test_local_runner_rejects_changed_files_outside_allowed_paths() -> None:
    with pytest.raises(LocalRunnerError, match="outside allowed_paths"):
        ensure_changed_files_allowed(["README.md"], ["docs/**"])
