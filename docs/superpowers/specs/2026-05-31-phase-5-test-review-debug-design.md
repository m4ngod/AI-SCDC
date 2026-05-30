# Phase 5 Test, Review, and Debug Loop Design

## Purpose

Phase 5 turns a Phase 4 `PATCH_READY` task into a tested and reviewed patch workflow. The goal is to move a task through local self-testing, deterministic review, and either `APPROVED` or `FIX_REQUESTED`, while keeping the existing human approval boundary for subsequent merge or PR work.

This phase is deterministic. It does not call reviewer/debugger models, edit code automatically, commit, push, merge, create pull requests, or add cloud sandbox behavior.

## Current Baseline

Phase 4 already provides:

- Local repository registration for a project.
- A local runner that creates a git worktree under `.worktrees`.
- A patch artifact with `diff_text`, `files_changed`, `tests_run`, `test_result`, and `risks`.
- Task state transitions up to `PATCH_READY`.
- Preserved `allowed_paths` and `required_tests` on tasks created from approved planner drafts.
- Desktop controls to start a local run and display patch metadata.

The task state machine already reserves `SELF_TESTING`, `REVIEWING`, `FIX_REQUESTED`, and `APPROVED`. Phase 5 uses those states instead of adding a parallel workflow.

## Selected Approach

Use a deterministic local MVP:

1. Run configured local test commands from `task.required_tests` inside the local runner worktree.
2. Store structured test run records with command output, exit codes, duration, and aggregate result.
3. Review patch artifacts with deterministic rules based on changed files, allowed paths, test results, and diff availability.
4. Store structured review result records.
5. Move failed test or review outcomes to `FIX_REQUESTED`.
6. Create a debug attempt record explaining why the task needs another implementation pass.

This makes the product loop reliable before adding model-backed reviewer and debugger agents.

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
- `status`: `running`, `passed`, or `failed`
- `commands`: list of command strings
- `command_results`: list of `{command, exit_code, stdout, stderr, duration_ms}`
- `failure_reason`
- `started_at`
- `completed_at`
- `created_at`

The first implementation runs commands synchronously through the API service layer, matching the existing Local Runner pattern. A future queue can move execution out of the request path without changing the API contract.

### PatchReview

Represents one deterministic review pass for a patch artifact.

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
- Request changes if there is no linked passing test run.
- Request changes if the linked test run failed.
- Request changes if a changed file is outside `task.allowed_paths`.
- Approve when the patch has a diff, changed files are allowed, and a linked test run passed.

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

In this deterministic MVP, the debugger does not edit files. It records why the task moved to `FIX_REQUESTED` and gives the UI a concrete object to display.

## API Design

Add endpoints:

- `POST /patch-artifacts/{patch_artifact_id}/test-runs`
- `GET /patch-artifacts/{patch_artifact_id}/test-runs`
- `GET /test-runs/{test_run_id}`
- `POST /patch-artifacts/{patch_artifact_id}/reviews`
- `GET /patch-artifacts/{patch_artifact_id}/reviews`
- `GET /patch-reviews/{review_id}`
- `GET /tasks/{task_id}/debug-attempts`

The test endpoint:

1. Loads patch artifact, local run, task, and repository.
2. Requires task status `PATCH_READY`.
3. Transitions `PATCH_READY -> SELF_TESTING`.
4. Runs `task.required_tests` in the local runner worktree.
5. Updates patch artifact `tests_run` and `test_result`.
6. Stores a `LocalTestRun`.
7. If tests pass, transitions `SELF_TESTING -> REVIEWING`.
8. If tests fail, transitions `SELF_TESTING -> FIX_REQUESTED` and creates a `DebugAttempt`.

The review endpoint:

1. Loads patch artifact, task, and latest passing test run for the artifact.
2. Requires task status `REVIEWING`.
3. Runs deterministic review rules.
4. Stores a `PatchReview`.
5. If approved, transitions `REVIEWING -> APPROVED`.
6. If changes are requested, transitions `REVIEWING -> FIX_REQUESTED` and creates a `DebugAttempt`.

## Worker Test Runner

Add a worker-side command runner in `apps/worker`.

Input:

- `worktree_path`
- `commands`
- `timeout_seconds`

Output:

- `status`: `passed` or `failed`
- `command_results`

Each command runs inside the worktree with a per-command timeout. The runner captures stdout, stderr, exit code, and duration. It stops on the first failed command.

Safety boundaries for this phase:

- Commands come only from `task.required_tests`.
- Commands run only inside the local runner worktree.
- No secret injection is added.
- No network sandboxing is attempted in this phase.
- Missing worktree paths are rejected before command execution.

## Desktop Design

Extend the task board and API client:

- Show patch summary, changed files, and current `test_result`.
- Add `Run tests` for `PATCH_READY` tasks.
- Add `Review patch` for `REVIEWING` tasks.
- Display test command results with pass/fail status and short stderr/stdout snippets.
- Display review verdict, issue count, and required changes.
- Display debug attempt details when the task is `FIX_REQUESTED`.

The UI remains compact and operational. It does not add a full diff viewer, marketing copy, or tutorial text.

## Events and Audit Trail

Add task events:

- `test_run_started`
- `test_run_completed`
- `patch_review_created`
- `debug_attempt_created`
- existing `task_transitioned`

Event payloads include relevant ids and status/verdict. Full stdout, stderr, and diff text stay on their own records, not inside event payloads.

## Error Handling

- Missing patch artifact returns 404.
- Starting tests for a task not in `PATCH_READY` returns 400 with current and expected status details.
- Starting review for a task not in `REVIEWING` returns 400.
- Missing worktree path returns 400 and does not transition the task.
- Test command timeout marks the command failed and moves the task to `FIX_REQUESTED`.
- Review requires a linked passing test run for the patch artifact.

## Test Strategy

Backend API tests:

- A passing test run moves `PATCH_READY -> SELF_TESTING -> REVIEWING`, updates the patch artifact to `passed`, and stores command output.
- A failing test run moves `PATCH_READY -> SELF_TESTING -> FIX_REQUESTED`, updates the patch artifact to `failed`, and creates a debug attempt.
- Review approval moves `REVIEWING -> APPROVED`.
- Review change request moves `REVIEWING -> FIX_REQUESTED` and creates a debug attempt.
- Invalid state calls return 400 and do not create records.

Worker tests:

- Passing command returns `passed`.
- Failing command returns `failed` and captures stdout/stderr.
- Timeout returns `failed`.
- Missing worktree path is rejected.

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
