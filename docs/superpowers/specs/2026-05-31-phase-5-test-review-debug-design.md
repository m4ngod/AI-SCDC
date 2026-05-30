# Phase 5 Test, Review, and Debug Loop Design

## Purpose

Phase 5 turns the Phase 4 patch artifact into a tested and reviewed development loop. The goal is to move a task from `PATCH_READY` through local self-testing, deterministic review, and either `APPROVED` or `FIX_REQUESTED`, while preserving an audit trail that can later support model-backed reviewers, debugger agents, cloud workers, and human merge approval.

This phase uses a deterministic local implementation first. It does not introduce real autonomous code repair, cloud sandboxes, pull requests, or automatic merge behavior.

## Current Baseline

Phase 4 already provides:

- Local repository registration for a project.
- A Local Runner that creates a git worktree under `.worktrees`.
- A patch artifact with `diff_text`, `files_changed`, `tests_run`, `test_result`, and `risks`.
- Task state transitions up to `PATCH_READY`.
- Desktop controls to start a local run and display patch metadata.

The task state machine already reserves `SELF_TESTING`, `REVIEWING`, `FIX_REQUESTED`, and `APPROVED`. Phase 5 should use those states instead of inventing a parallel workflow.

## Selected Approach

Use a deterministic MVP:

1. Run configured local test commands from `task.required_tests`.
2. Store structured test run records with command output, exit codes, duration, and aggregate result.
3. Review patch artifacts with deterministic rules based on changed files, allowed paths, test results, and diff availability.
4. Store structured review result records.
5. Move failed test or review outcomes to `FIX_REQUESTED`.
6. Add a debug attempt record to explain what must be fixed and allow a later rerun from `FIX_REQUESTED`.

This keeps the product loop reliable before adding model-backed reviewer and debugger agents.

## Non-Goals

Phase 5 does not:

- Call real LLMs for reviewer or debugger behavior.
- Modify source code automatically in the debugger loop.
- Commit, push, merge, or create pull requests.
- Build a cloud sandbox.
- Add production auth, billing, or organization-level permissions.
- Add a full Monaco side-by-side diff viewer. The desktop will show enough diff/test/review data to validate the workflow.

## Data Model

Add three records.

### LocalTestRun

Represents one self-testing pass for a patch artifact.

Fields:

- `id`
- `workspace_id`
- `project_id`
- `task_id`
- `local_run_id`
- `patch_artifact_id`
- `status`: `running`, `passed`, `failed`
- `commands`: list of command strings
- `command_results`: list of `{command, exit_code, stdout, stderr, duration_ms}`
- `started_at`
- `completed_at`
- `created_at`

The first implementation runs commands synchronously through the API service layer, matching the existing Local Runner pattern. A future queue can move this to a background worker without changing the API contract.

### PatchReview

Represents one review pass for a patch artifact.

Fields:

- `id`
- `workspace_id`
- `project_id`
- `task_id`
- `local_run_id`
- `patch_artifact_id`
- `test_run_id`
- `reviewer_kind`: `deterministic`
- `verdict`: `approved` or `changes_requested`
- `issues`: list of structured review issues
- `required_changes`: list of strings
- `created_at`

Deterministic review rules:

- Request changes if no diff is present.
- Request changes if no files changed.
- Request changes if the linked test run failed.
- Request changes if a changed file is outside `task.allowed_paths`.
- Approve when the patch has a diff, files changed are allowed, and tests pass.

### DebugAttempt

Represents a requested repair pass after tests or review fail.

Fields:

- `id`
- `workspace_id`
- `project_id`
- `task_id`
- `patch_artifact_id`
- `review_id`
- `test_run_id`
- `status`: `requested`
- `root_cause`
- `fix_summary`
- `created_at`

In this deterministic MVP, the debugger does not edit files. It records why the task moved to `FIX_REQUESTED` and gives the UI a concrete object to display. The next phase can replace this with a model-backed repair runner.

## API Design

Add endpoints:

- `POST /patch-artifacts/{patch_artifact_id}/test-runs`
- `GET /patch-artifacts/{patch_artifact_id}/test-runs`
- `GET /test-runs/{test_run_id}`
- `POST /patch-artifacts/{patch_artifact_id}/reviews`
- `GET /patch-artifacts/{patch_artifact_id}/reviews`
- `GET /patch-reviews/{review_id}`
- `GET /tasks/{task_id}/debug-attempts`

The test endpoint performs:

1. Load patch artifact, local run, task, and repository.
2. Validate the task is in `PATCH_READY`.
3. Transition `PATCH_READY -> SELF_TESTING`.
4. Run `task.required_tests` in the Local Runner worktree.
5. Update patch artifact `tests_run` and `test_result`.
6. If tests pass, transition `SELF_TESTING -> REVIEWING`.
7. If tests fail, transition `SELF_TESTING -> FIX_REQUESTED` and create a debug attempt.

The review endpoint performs:

1. Load patch artifact and task.
2. Validate the task is in `REVIEWING`.
3. Run deterministic review rules.
4. Store a patch review.
5. If approved, transition `REVIEWING -> APPROVED`.
6. If changes are requested, transition `REVIEWING -> FIX_REQUESTED` and create a debug attempt.

## Worker Test Runner

Add a small command runner in `apps/worker`:

- Input: worktree path and command list.
- Run each command with `shell=True` on Windows-compatible local development only.
- Set a per-command timeout.
- Capture stdout, stderr, exit code, and duration.
- Stop at first failed command.
- Return aggregate `passed` or `failed`.

Safety boundaries for this phase:

- Commands come only from `task.required_tests`.
- Commands run only inside the Local Runner worktree.
- No secret injection is added.
- No network sandboxing is attempted in this phase.

## Desktop Design

Extend the task board and client contract:

- Show patch summary, changed files, and `test_result`.
- Add a `Run tests` action for `PATCH_READY` tasks.
- Add a `Review patch` action for `REVIEWING` tasks.
- Display test commands with pass/fail status and stderr snippets.
- Display review verdict, issue count, and required changes.
- Display debug request status when the task is `FIX_REQUESTED`.

The UI should remain compact and operational. It should not become a marketing page or a tutorial surface.

## Events and Audit Trail

Add task events for:

- `test_run_started`
- `test_run_completed`
- `patch_review_created`
- `debug_attempt_created`
- existing `task_transitioned`

Event payloads should include the relevant ids and the status/verdict. Full stdout, stderr, and diff text stay on their own records, not inside event payloads.

## Error Handling

- Missing patch artifact returns 404.
- Starting tests for a task not in `PATCH_READY` returns a 400 with allowed next statuses.
- Starting review for a task not in `REVIEWING` returns a 400.
- Missing worktree path returns 400 and does not transition the task.
- Test command timeout marks the command failed and moves the task to `FIX_REQUESTED`.
- Review may proceed only after a test run exists for the patch artifact.

## Test Strategy

Backend API tests:

- A passing test run moves `PATCH_READY -> SELF_TESTING -> REVIEWING`, updates the patch artifact to `passed`, and stores command output.
- A failing test run moves `PATCH_READY -> SELF_TESTING -> FIX_REQUESTED`, updates the patch artifact to `failed`, and creates a debug attempt.
- A review approval moves `REVIEWING -> APPROVED`.
- A review change request moves `REVIEWING -> FIX_REQUESTED` and creates a debug attempt.
- Invalid state transitions return 400 and do not create records.

Worker tests:

- Passing command returns `passed`.
- Failing command returns `failed` and captures stderr/stdout.
- Timeout returns `failed`.
- The runner rejects a missing worktree path.

Desktop tests:

- `Run tests` calls the API client and updates task status/test metadata.
- `Review patch` calls the API client and shows review verdict.
- `FIX_REQUESTED` shows debug attempt information.

## Acceptance Criteria

- A task with a patch artifact can run local tests from `required_tests`.
- Passing tests move the task to `REVIEWING`.
- Failing tests move the task to `FIX_REQUESTED` and create a debug attempt.
- A deterministic review can approve a tested patch and move it to `APPROVED`.
- A deterministic review can request changes and move it to `FIX_REQUESTED`.
- Test logs and review results are available from API responses and visible in the desktop task board.
- Existing Phase 0 through Phase 4 tests continue to pass.

## Future Extensions

- Replace deterministic review with a model-backed Reviewer Agent.
- Replace deterministic debug attempt creation with a model-backed Debugger Agent that edits the worktree and reruns tests.
- Move test and review execution to a background queue.
- Add a full diff viewer and review comments.
- Add merge approval and PR creation after `APPROVED`.
