# Phase 6 Patch Approval and Diff Viewer Design

## Purpose

Phase 6 closes the local patch review loop without performing a real merge. After Phase 5, a task can move from `PATCH_READY` through local tests and deterministic review to `APPROVED`. Phase 6 adds the human patch approval boundary, a compact diff viewer, and merge-ready state tracking so the product can show exactly what a user is approving before any future merge or pull request automation exists.

This phase records approval intent and advances task state. It does not run `git commit`, `git merge`, `git push`, or create pull requests.

## Current Baseline

The system already has:

- Patch artifacts with `diff_text`, `files_changed`, test metadata, and risks.
- Local test runs that update patch artifact test status.
- Deterministic patch reviews that move passing tasks to `APPROVED`.
- Debug attempts for failed tests or review findings.
- Task states for `APPROVED`, `MERGE_READY`, `HUMAN_APPROVAL`, and `MERGED`.
- Desktop controls for `Run local`, `Run tests`, and `Review patch`.

The missing boundary is user approval of a reviewed patch before merge work begins.

## Selected Approach

Use a human approval boundary without automatic merge:

1. Add durable patch approval records.
2. Allow approval only for patch artifacts whose task is currently `APPROVED`.
3. Make patch approval idempotent for a patch artifact.
4. Move task status from `APPROVED` to `MERGE_READY` when approval is recorded.
5. Allow an explicit user action to move `MERGE_READY` to `HUMAN_APPROVAL`.
6. Display the patch diff, tests, review verdict, approval state, and merge-ready instructions in the desktop task board.

This keeps the safety boundary clear: the user can approve the patch, but the application does not modify the base branch.

## Non-Goals

Phase 6 does not:

- Execute `git commit`, `git merge`, `git push`, or `git apply`.
- Resolve merge conflicts.
- Create GitHub or GitLab pull requests.
- Upload artifacts to object storage.
- Add a full Monaco side-by-side diff editor.
- Add model-backed reviewer or debugger behavior.
- Add billing, production auth, or team permissions.

## Data Model

Add `PatchApproval`.

Fields:

- `id`
- `workspace_id`
- `project_id`
- `task_id`
- `local_run_id`
- `patch_artifact_id`
- `review_id`
- `status`: `approved`
- `approved_by`: development user id for now
- `merge_instructions`: string shown to the user
- `created_at`

Constraints:

- `patch_artifact_id` is unique for normal approvals.
- Approval is append-safe at the workflow level because repeated approval attempts return the existing approval.
- `review_id` links the approval to the latest deterministic review for traceability.

The existing planner `Approval` table remains scoped to planner-run decisions. `PatchApproval` is separate because patch approval has different lifecycle, state checks, and traceability needs.

## API Design

Add endpoints:

- `POST /patch-artifacts/{patch_artifact_id}/approvals`
- `GET /patch-artifacts/{patch_artifact_id}/approvals`
- `GET /patch-approvals/{approval_id}`
- `POST /patch-approvals/{approval_id}/request-human-approval`

### Approve Patch

`POST /patch-artifacts/{patch_artifact_id}/approvals` performs:

1. Load patch artifact, task, local run, and latest patch review.
2. If an approval already exists for the artifact, return it.
3. Validate task status is `APPROVED`.
4. Validate latest review verdict is `approved`.
5. Create `PatchApproval`.
6. Transition task `APPROVED -> MERGE_READY`.
7. Emit `patch_approval_created` and `task_transitioned` events.
8. Return task, patch artifact, latest review, and approval.

### Request Human Approval

`POST /patch-approvals/{approval_id}/request-human-approval` performs:

1. Load patch approval and task.
2. Validate task status is `MERGE_READY`.
3. Transition task `MERGE_READY -> HUMAN_APPROVAL`.
4. Emit `human_approval_requested` and `task_transitioned` events.
5. Return task, patch artifact, review, and approval.

The endpoint name is intentionally explicit. It records that the system is ready for a human-controlled merge step, but it does not perform the merge.

## Desktop Design

Extend the existing compact task board instead of building a separate page.

For tasks with a patch artifact, show:

- Changed file list.
- Unified diff preview from `patch_artifact.diff_text`.
- Test result and test command summary.
- Review verdict and required changes.
- Approval status when present.
- Worktree path and merge instructions after approval.

Controls:

- `Approve patch` appears for `APPROVED` tasks with a reviewed patch.
- `Request human approval` appears for `MERGE_READY` tasks with a patch approval.

Diff preview behavior:

- Render as preformatted text.
- Limit height with scroll.
- Keep line wrapping off so unified diff remains readable.
- Do not add syntax highlighting in this phase.

## Events and Audit Trail

Add task events:

- `patch_approval_created`
- `human_approval_requested`
- existing `task_transitioned`

Event payloads include ids and lightweight state:

- `patch_approval_id`
- `patch_artifact_id`
- `review_id`
- `status`

Large fields such as full diff text stay on the patch artifact record, not inside task events.

## Error Handling

- Missing patch artifact returns 404.
- Approving a task not in `APPROVED` returns 400 with the current status.
- Approving a patch without an approved review returns 400.
- Re-approving an already approved patch returns the existing approval and does not create duplicate events.
- Requesting human approval for a task not in `MERGE_READY` returns 400.
- These endpoints never modify git state.

## Test Strategy

Backend API tests:

- Approved reviewed patch creates a patch approval and moves task to `MERGE_READY`.
- Duplicate approval returns the existing approval and emits no duplicate approval event.
- Patch approval fails when the task is not `APPROVED`.
- Patch approval fails when latest review is not approved.
- Requesting human approval moves `MERGE_READY -> HUMAN_APPROVAL`.
- Requesting human approval from another state returns 400.

Desktop tests:

- Task board renders diff preview for a patch artifact.
- `Approve patch` calls the client and shows approval state.
- `Request human approval` calls the client and shows `HUMAN_APPROVAL`.
- Merge instructions are visible after patch approval.

Client tests:

- Fake client supports patch approval and human approval request.
- HTTP client posts to the new endpoints and maps approval result fields.

## Acceptance Criteria

- Users can inspect unified diff text before approving a patch.
- Only reviewed and task-approved patches can be patch-approved.
- Patch approval is idempotent for each patch artifact.
- Patch approval moves a task to `MERGE_READY`.
- A separate explicit action moves `MERGE_READY` to `HUMAN_APPROVAL`.
- The desktop shows diff, test, review, approval, worktree, and merge instruction context.
- No endpoint in this phase runs git merge, commit, push, apply, or PR creation.
- Existing Phase 0 through Phase 5 tests continue to pass.

## Future Extensions

- Add a full side-by-side diff viewer with file navigation.
- Add inline review comments.
- Add a real local merge endpoint with dirty-worktree and conflict handling.
- Add patch file export or `git apply` support.
- Add GitHub or GitLab pull request creation from approved patches.
- Add organization-aware approval policies and reviewer assignments.
