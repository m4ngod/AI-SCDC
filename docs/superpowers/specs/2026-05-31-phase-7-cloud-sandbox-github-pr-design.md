# Phase 7 Cloud Sandbox and GitHub PR Design

## Purpose

Phase 7 extends the reviewed patch workflow from a local approval boundary to a GitHub pull request boundary. After Phase 6, a patch can reach `HUMAN_APPROVAL`, but the product still does not have a first-class way to publish that approved patch for collaboration. Phase 7 adds GitHub credentials, GitHub repository registration, cloud-run tracking, a fake sandbox worker path for deterministic development, and an explicit `Create PR` action.

The core product goal is:

```text
approved patch -> explicit human approval -> explicit Create PR -> GitHub pull request
```

This phase creates pull requests, but it does not merge pull requests, write to the default branch, deploy code, or add production-grade GitHub OAuth.

## Current Baseline

The system already has:

- Project repositories for local git paths.
- Local task runs that create `PatchArtifact` records.
- Local test runs, deterministic patch reviews, and debug attempts.
- Patch approvals that move tasks to `MERGE_READY`.
- Human approval requests that move tasks to `HUMAN_APPROVAL`.
- A development secret vault pattern used for model BYOK credentials.
- Desktop task-board controls for local run, tests, review, patch approval, and human approval.

The missing boundary is publishing an approved patch as a pull request in a remote repository.

## Selected Approach

Use GitHub only for Phase 7.

1. Add manually entered GitHub personal access tokens using the existing development secret-vault pattern.
2. Register GitHub repositories separately from local-only repositories, while extending the existing repository concept instead of adding a full provider abstraction.
3. Add `CloudRun` records to model a remote/sandbox execution attempt.
4. Use a fake cloud sandbox worker for the first vertical slice so tests and demos remain deterministic.
5. Reuse `PatchArtifact`, Phase 5 test/review, and Phase 6 approval records.
6. Add an explicit `Create PR` action that is allowed only after `HUMAN_APPROVAL`.
7. Record a durable pull request record and transition the task to `PR_CREATED`.

This deliberately separates three human-visible decisions:

- Review/approval of patch content.
- Request for human approval to publish.
- Actual remote PR creation.

## Non-Goals

Phase 7 does not:

- Support GitLab, Bitbucket, or generic git hosting.
- Add GitHub OAuth, GitHub App installation, or browser-based login.
- Run a real cloud container worker.
- Add object storage for large artifacts.
- Automatically merge pull requests.
- Push directly to the repository default branch.
- Deploy code.
- Add production billing, organization RBAC, or team approval policies.
- Replace the deterministic reviewer/debugger with model-backed agents.

## Workflow

The Phase 7 happy path is:

```text
User enters GitHub PAT
  -> API stores encrypted credential metadata
  -> user registers a GitHub repository
  -> user starts a cloud run for an approved task
  -> fake cloud worker creates a PatchArtifact
  -> existing Phase 5 tests/review approve the patch
  -> existing Phase 6 patch approval moves task to MERGE_READY
  -> existing Phase 6 human approval moves task to HUMAN_APPROVAL
  -> user clicks Create PR
  -> API materializes/pushes a branch through GitHub integration
  -> API creates a pull request
  -> API records PullRequestRecord and moves task to PR_CREATED
```

The fake cloud worker is intentionally small. Its job is to prove control-plane behavior, artifact wiring, and UI/API contracts. A future phase can replace it with Docker or a real cloud worker without changing the approval and PR boundaries.

## Data Model

### GitHubCredential

Add a GitHub-specific credential table rather than reusing model credentials.

Fields:

- `id`
- `workspace_id`
- `display_name`
- `encrypted_token`
- `token_last4`
- `status`: `active` or `deleted`
- `created_at`
- `updated_at`

The API never returns `encrypted_token` or raw PAT values. Delete is a soft delete so prior PR records remain auditable.

### Repository

Extend the existing repository concept to support GitHub repositories.

Additional fields:

- `provider`: `local` or `github`
- `repo_url`
- `github_owner`
- `github_repo`
- `github_credential_id`
- `connection_status`

Rules:

- Local repositories continue to require `local_path`.
- GitHub repositories require owner, repo name, repo URL, default branch, and active GitHub credential.
- Phase 7 does not add a GitLab-ready abstraction. The fields are GitHub-specific by design.

### CloudRun

Add a durable cloud/sandbox execution record.

Fields:

- `id`
- `workspace_id`
- `project_id`
- `task_id`
- `repo_id`
- `base_branch`
- `head_branch`
- `status`: `queued`, `running`, `patch_ready`, `failed`
- `patch_artifact_id`
- `sandbox_kind`: `fake`
- `failure_reason`
- `created_at`
- `updated_at`

`head_branch` should be deterministic and collision-resistant, for example:

```text
ai-scdc/task-{task_id}-{cloud_run_id}
```

### PullRequestRecord

Add a durable pull request record.

Fields:

- `id`
- `workspace_id`
- `project_id`
- `task_id`
- `repo_id`
- `patch_artifact_id`
- `patch_approval_id`
- `cloud_run_id`
- `head_branch`
- `base_branch`
- `github_pr_number`
- `github_pr_url`
- `status`: `created`
- `created_by`
- `created_at`

Constraints:

- `patch_approval_id` is unique for created pull requests.
- Repeated PR creation for the same approval returns the existing record.

## Task State

Add `PR_CREATED` to the shared task status set.

Allowed transition:

```text
HUMAN_APPROVAL -> PR_CREATED
```

Phase 7 does not move tasks to `MERGED`. Future merge tracking can add:

```text
PR_CREATED -> MERGED
```

only after explicit merge detection or a separate approved merge action.

## API Design

### GitHub Credentials

Add endpoints:

- `POST /github-credentials`
- `GET /github-credentials`
- `DELETE /github-credentials/{credential_id}`

`POST /github-credentials` accepts:

- `display_name`
- `token`

It returns only metadata:

- `id`
- `display_name`
- `token_last4`
- `status`
- `created_at`

### GitHub Repositories

Add endpoint:

- `POST /projects/{project_id}/github-repositories`

The request includes:

- `name`
- `repo_url`
- `github_owner`
- `github_repo`
- `default_branch`
- `github_credential_id`

The endpoint validates that the project exists and the credential is active. In fake tests, it does not call GitHub. In real smoke runs, a lightweight GitHub adapter can validate repository access.

### Cloud Runs

Add endpoints:

- `POST /tasks/{task_id}/cloud-runs`
- `GET /tasks/{task_id}/cloud-runs`
- `GET /cloud-runs/{cloud_run_id}`

`POST /tasks/{task_id}/cloud-runs` performs:

1. Load task and GitHub repository.
2. Validate repository belongs to the same project as the task.
3. Validate task is ready for execution according to the same safety rules as local runs.
4. Create a `CloudRun` with `queued`, then move it through `running` to `patch_ready` or `failed`.
5. Use the fake cloud worker to create a patch artifact.
6. Move the task to `PATCH_READY`.
7. Emit task events for cloud run start, patch artifact creation, and transition.

The response mirrors local run responses where possible:

- `cloud_run`
- `patch_artifact`

### Pull Requests

Add endpoints:

- `POST /patch-approvals/{approval_id}/pull-requests`
- `GET /patch-artifacts/{patch_artifact_id}/pull-requests`
- `GET /pull-requests/{pull_request_id}`

`POST /patch-approvals/{approval_id}/pull-requests` performs:

1. Load patch approval, task, patch artifact, cloud run, and GitHub repository.
2. If a pull request record already exists for the approval, return it.
3. Validate task status is `HUMAN_APPROVAL`.
4. Validate repository provider is `github`.
5. Validate the GitHub credential is active.
6. Materialize the patch branch and push it through the GitHub integration.
7. Create a GitHub pull request from `head_branch` to `base_branch`.
8. Store `PullRequestRecord`.
9. Transition the task from `HUMAN_APPROVAL` to `PR_CREATED`.
10. Emit `pull_request_created` and `task_transitioned` events.

The endpoint is intentionally separate from `request-human-approval`; remote writes happen only after a visible `Create PR` action.

## GitHub Integration

Create a small GitHub integration service with two implementations:

- `FakeGitHubPullRequestAdapter` for automated tests and demo mode.
- `GitHubPullRequestAdapter` for real local smoke tests.

The adapter boundary should expose:

- validate repository access
- push or publish a branch from the prepared patch context
- create pull request

The real adapter may use `git` CLI for branch materialization/push and GitHub's HTTP API for PR creation. Secrets must be redacted from command output, exceptions, logs, and API responses.

## Fake Cloud Worker

The first cloud worker is fake and deterministic.

Responsibilities:

- Accept a task and GitHub repository.
- Create a `CloudRun`.
- Produce a `PatchArtifact` with deterministic summary, changed files, test commands, risks, and unified diff text.
- Return no network-dependent data in tests.

The fake worker should produce a patch artifact that is realistic enough for Phase 5 review and Phase 6 approval. Real patch materialization is owned by the PR service boundary, where the explicit remote-write action happens.

## Desktop Design

Extend the current task board and compact controls.

New visible capabilities:

- GitHub credential and repository setup in a minimal configuration section.
- `Run cloud` button for tasks that can use a GitHub repository.
- Cloud run status and head branch display.
- Existing test/review/approval controls continue to work with cloud-run patch artifacts.
- `Create PR` button appears only when task status is `HUMAN_APPROVAL` and there is a patch approval.
- Created PR URL appears after success.

The first UI should remain operational-tooling style: compact fields, clear status labels, and no marketing page.

## Events and Audit Trail

Add task events:

- `github_credential_created`
- `github_repository_registered`
- `cloud_run_started`
- `cloud_run_patch_ready`
- `cloud_run_failed`
- `pull_request_created`
- existing `task_transitioned`

Event payloads include ids and safe metadata only:

- credential id and display name, never token data
- repository id, owner, repo, default branch
- cloud run id, sandbox kind, status
- pull request id, number, URL, head branch, base branch

Full diff text remains on `PatchArtifact`.

## Error Handling

- Missing GitHub credential returns 404.
- Deleted GitHub credential returns 400.
- Credential responses never include raw token or encrypted token.
- GitHub repository registration with a missing project returns 404.
- Cloud run with a non-GitHub repository returns 400.
- Cloud run with a cross-project repository returns 400.
- PR creation before `HUMAN_APPROVAL` returns 400 with current and expected statuses.
- PR creation without a patch approval returns 404.
- PR creation without a cloud run returns 400.
- Duplicate PR creation returns the existing pull request record.
- GitHub adapter failures mark the PR attempt as failed without moving the task to `PR_CREATED`.

## Test Strategy

Backend API tests:

- GitHub credential creation stores encrypted token metadata and never returns secret fields.
- GitHub credential delete soft-deletes the credential.
- GitHub repository registration requires an active credential.
- Cloud run creates a `CloudRun`, patch artifact, task events, and `PATCH_READY` transition.
- Cloud run rejects repositories from another project.
- PR creation requires `HUMAN_APPROVAL`.
- PR creation uses fake GitHub adapter and stores `PullRequestRecord`.
- Duplicate PR creation is idempotent.
- PR creation transitions task to `PR_CREATED`.
- GitHub adapter failure does not transition task to `PR_CREATED`.

Desktop client tests:

- Fake client supports GitHub credential, GitHub repository, cloud run, and PR creation methods.
- HTTP client posts to new endpoints and maps result fields.
- `listTasks()` hydrates cloud run and pull request metadata for persisted tasks.

Desktop App tests:

- GitHub repository setup renders and calls the client.
- `Run cloud` calls the client and displays patch artifact context.
- Existing test/review/approval controls work after a cloud run.
- `Create PR` appears only after `HUMAN_APPROVAL`.
- Successful PR creation displays PR URL and `PR_CREATED`.

Full verification:

- `pnpm test`
- `pnpm typecheck`
- `pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v`
- `git diff --check`

## Acceptance Criteria

- User can store a GitHub PAT without the API returning secret material.
- User can register a GitHub repository for a project.
- User can start a fake cloud run that creates a reviewable patch artifact.
- Existing Phase 5 test/review and Phase 6 approval workflows work with cloud-run patch artifacts.
- `Create PR` is visible only after `HUMAN_APPROVAL`.
- `Create PR` creates or returns a durable pull request record.
- PR creation is idempotent per patch approval.
- PR creation moves the task to `PR_CREATED`.
- Automated tests use fake GitHub behavior and do not require network access.
- No Phase 7 endpoint merges PRs, writes to the default branch, deploys code, or leaks PAT values.

## Future Extensions

- Replace fake cloud worker with Docker sandbox execution.
- Add real cloud worker queue, logs, artifact storage, and cancellation.
- Add GitHub OAuth or GitHub App installation.
- Add GitLab support after the GitHub flow stabilizes.
- Add PR status polling and merge detection.
- Add explicit merge action with approval and branch protection checks.
- Add model-backed reviewer/debugger for richer findings and automated fix attempts.
