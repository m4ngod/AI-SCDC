import time
from pathlib import Path

from ai_company_worker.test_runner import (
    TestRunnerError,
    TestRunnerRequest,
    run_tests,
)


def test_run_tests_passes_all_commands(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "README.md").write_text("# Demo\n", encoding="utf-8")

    result = run_tests(
        TestRunnerRequest(
            worktree_path=worktree,
            commands=[
                "python -c \"from pathlib import Path; assert Path('README.md').exists()\"",
                "python -c \"print('ok')\"",
            ],
        )
    )

    assert result.status == "passed"
    assert [item.command for item in result.command_results] == [
        "python -c \"from pathlib import Path; assert Path('README.md').exists()\"",
        "python -c \"print('ok')\"",
    ]
    assert [item.exit_code for item in result.command_results] == [0, 0]
    assert "ok" in result.command_results[1].stdout


def test_run_tests_stops_on_first_failed_command(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = run_tests(
        TestRunnerRequest(
            worktree_path=worktree,
            commands=[
                "python -c \"import sys; print('bad'); sys.exit(7)\"",
                "python -c \"print('should not run')\"",
            ],
        )
    )

    assert result.status == "failed"
    assert len(result.command_results) == 1
    assert result.command_results[0].exit_code == 7
    assert "bad" in result.command_results[0].stdout


def test_run_tests_marks_timeout_as_failed(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    started = time.monotonic()
    result = run_tests(
        TestRunnerRequest(
            worktree_path=worktree,
            commands=["python -c \"import time; time.sleep(2)\""],
            timeout_seconds=0.1,
        )
    )
    elapsed_seconds = time.monotonic() - started

    assert result.status == "failed"
    assert result.command_results[0].exit_code is None
    assert "timed out" in result.command_results[0].stderr.lower()
    assert elapsed_seconds < 1


def test_run_tests_rejects_missing_worktree(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    try:
        run_tests(TestRunnerRequest(worktree_path=missing, commands=["python -V"]))
    except TestRunnerError as exc:
        assert "Worktree path does not exist" in str(exc)
    else:
        raise AssertionError("Expected TestRunnerError")


def test_run_tests_rejects_empty_commands(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    try:
        run_tests(TestRunnerRequest(worktree_path=worktree, commands=[]))
    except TestRunnerError as exc:
        assert "No test commands configured" in str(exc)
    else:
        raise AssertionError("Expected TestRunnerError")
