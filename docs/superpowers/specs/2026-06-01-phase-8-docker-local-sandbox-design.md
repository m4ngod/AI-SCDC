# Phase 8 Docker Local Sandbox Executor Design

## Purpose

Phase 8 replaces the Phase 7 fake cloud worker with a first real sandbox execution path. The goal is to prove that AI-SCDC can run a task against a GitHub repository inside an isolated Docker container, produce a reviewable patch, capture test results, and then reuse the existing reviewer, approval, and GitHub pull request flow.

The core product goal is:

```text
GitHub task -> Docker sandbox run -> patch artifact + test result -> review -> approval -> PR
```

This phase is intentionally local-first. It uses Docker on the developer machine as the real executor, but it does not add remote cloud infrastructure, queues, object storage, automatic merge, or model-driven autonomous patch generation.

## Current Baseline

The system already has:

- GitHub credential storage and GitHub repository registration.
- `CloudRun` records with a deterministic fake sandbox worker.
- `LocalTaskRun` and `PatchArtifact` records that can feed the Phase 5 reviewer/debugger and Phase 6 approval flow.
- Real GitHub pull request creation after explicit human approval.
- Required test command storage on tasks and command-result capture through `LocalTestRun`.
- Allowed-path validation in the local runner.

The missing boundary is real sandbox execution. Today `sandbox_kind=fake` creates deterministic patch artifacts without cloning a remote repository or running commands in an isolated runtime.

## Selected Approach

Add `docker_local` as the first real `CloudRun` backend.

1. Keep `CloudRun` as the API-visible control-plane record.
2. Introduce a small executor boundary whose implementations return the same result shape: status, patch artifact data, command logs, test result, and failure reason.
3. Preserve the Phase 7 fake executor as the default for automated tests and environments without Docker.
4. Add a project/repository sandbox profile that whitelists Docker image, patch commands, test commands, allowed environment variables, and command timeouts.
5. Execute the selected whitelisted patch command and test commands inside a Docker container.
6. Capture `git diff`, changed files, base/head sha, command results, and redacted logs.
7. Reuse the existing reviewer, debugger, approval, human approval, and PR creation flow.

This keeps the phase focused on the execution boundary. The container produces artifacts; it does not create pull requests or perform remote publishing actions.

## Non-Goals

Phase 8 does not:

- Add a remote cloud VM/container service.
- Add a queue worker or background job scheduler.
- Mount the host Docker socket into the sandbox.
- Mount the host home directory, SSH directory, credential stores, or project root into the sandbox.
- Let the model autonomously write code inside the container.
- Let users submit arbitrary one-off shell commands at run time.
- Create PRs from inside the container.
- Merge pull requests, push to the default branch, or deploy code.
- Add GitLab or Bitbucket support.
- Add production-grade multi-tenant resource isolation.

## Architecture

Phase 8 adds a `CloudSandboxExecutor` boundary used by the existing cloud-run service:

- `FakeCloudSandboxExecutor` keeps the current deterministic fake behavior.
- `DockerLocalSandboxExecutor` runs the real local Docker workflow.

The API selects the executor with an explicit configuration value such as:

```text
AI_SCDC_CLOUD_RUNNER=fake
AI_SCDC_CLOUD_RUNNER=docker_local
```

The default remains `fake` so normal development and CI do not require Docker. When `docker_local` is enabled, `POST /tasks/{task_id}/cloud-runs` creates the same `CloudRun` response shape but records `sandbox_kind=docker_local`.

The Docker executor owns only sandbox execution:

- Create a temporary host workspace and artifact directory.
- Run Docker with the configured image.
- Clone/fetch the GitHub repository inside the container.
- Checkout the base branch and create the AI-SCDC head branch.
- Run the selected whitelisted patch command.
- Run the selected whitelisted test commands.
- Export diff, changed file list, shas, command logs, and test results.
- Clean up the temporary workspace after results are captured.

The PR service remains API-side and runs only after explicit human approval.

## Sandbox Profile

Add a small project/repository-level sandbox profile rather than accepting arbitrary command text on each run.

Fields:

- `id`
- `workspace_id`
- `project_id`
- `repo_id`
- `name`
- `docker_image`, defaulting to `python:3.11-bookworm` for examples and generated defaults because the Docker executor performs `git clone` inside the container
- `patch_commands`: JSON list of whitelisted commands with key, label, command, and timeout seconds
- `test_commands`: JSON list of whitelisted commands with key, label, command, and timeout seconds
- `allowed_env_vars`: JSON list of environment variable names allowed to enter the container
- `network_enabled`: default `true`
- `status`: `active` or `disabled`
- `created_at`
- `updated_at`

`CloudRunCreate` may accept:

- `repo_id`
- `sandbox_profile_id`
- `patch_command_key`
- `test_command_keys`

If the command keys are omitted, the API uses the profile defaults. The first UI can keep this simple by creating one default profile per GitHub repo and running its default patch/test commands.

The profile is the trust boundary. Users can edit the profile intentionally, but individual task runs cannot inject ad hoc shell commands.

## Data Model

Extend existing records rather than adding a separate remote-run model.

### CloudRun

Use existing fields and add or standardize:

- `sandbox_kind`: `fake` or `docker_local`
- `status`: `queued`, `running`, `patch_ready`, or `failed`
- `failure_reason`: structured failure code string
- `command_results`: JSON list for clone, checkout, patch command, diff capture, and other non-test execution steps
- `sandbox_profile_id`
- `patch_command_key`
- `test_command_keys`: JSON list

Failure codes are:

- `docker_unavailable`
- `repo_checkout_failed`
- `patch_command_failed`
- `no_patch_produced`
- `test_failed`
- `artifact_capture_failed`

### LocalTaskRun

Create a companion `LocalTaskRun` for Docker runs with:

- `runner_kind=docker_local`
- `worktree_path` set to an opaque sandbox reference or temporary path while available
- `base_sha` and `head_sha` captured from the container workspace
- `patch_artifact_id` set after diff capture

This preserves compatibility with `PatchArtifact.local_run_id` and the existing review/test services.

### PatchArtifact

Continue to store:

- summary
- files changed
- tests run
- test result
- risks
- unified diff text

For Docker runs, the diff comes from `git diff` inside the sandbox after the patch command completes. Changed files must pass the task allowed-path check before the artifact is marked usable.

### LocalTestRun

Continue to store command-level test output:

- commands
- command results with stdout, stderr, exit code, duration, and timeout flags
- status: `passed` or `failed`
- failure reason

Docker test commands run inside the sandbox but are persisted through the same `LocalTestRun` shape so reviewer/debugger behavior does not need a parallel test model.

## Workflow

The Docker happy path is:

```text
User registers GitHub repo and sandbox profile
  -> user starts cloud run
  -> API creates CloudRun queued
  -> API selects DockerLocalSandboxExecutor
  -> executor marks CloudRun running
  -> Docker container clones GitHub repo
  -> container checks out base branch
  -> container creates AI-SCDC head branch
  -> container runs whitelisted patch command
  -> executor captures diff and validates allowed paths
  -> container runs whitelisted test commands
  -> API stores PatchArtifact and LocalTestRun
  -> CloudRun moves to patch_ready
  -> existing review/debug/approval flow continues
  -> existing human-approved PR flow creates GitHub PR
```

The head branch naming pattern remains deterministic and collision-resistant:

```text
ai-scdc/task-{task_id}-{cloud_run_id}
```

The container does not push this branch. The approved PR endpoint later materializes/pushes the branch through the existing GitHub pull request adapter.

## Docker Execution Rules

Docker runs use these constraints:

- Network is enabled by default so package managers and tests can reach the internet.
- The host only mounts a temporary workspace and an artifact output directory.
- The host home, SSH configuration, credential stores, project root, and Docker socket are not mounted.
- Only environment variables named by the sandbox profile may enter the container.
- Sandbox environment values are written to a Docker `--env-file`; they are not injected into or used to mutate the host Docker CLI environment.
- GitHub token material is redacted from stdout, stderr, exception messages, API responses, and task events.
- Repository URL credentials are also redacted from command strings and logs before persistence.
- Every Docker operation and command has a timeout.
- Containers are removed after execution unless an explicit debug flag keeps them for local troubleshooting.
- The executor fails closed when Docker is unavailable or the selected profile is missing/disabled.

The first implementation may use the Docker CLI through a process runner instead of introducing a Docker SDK dependency. The command runner should still be abstracted so unit tests can verify behavior without invoking Docker.

## API Design

Add or extend endpoints:

- `POST /projects/{project_id}/sandbox-profiles`
- `GET /projects/{project_id}/sandbox-profiles`
- `GET /sandbox-profiles/{sandbox_profile_id}`
- `POST /tasks/{task_id}/cloud-runs`

`POST /tasks/{task_id}/cloud-runs` validates:

1. The task exists.
2. The repository exists, belongs to the task project, and uses provider `github`.
3. The GitHub credential is active.
4. The selected sandbox profile exists, is active, belongs to the same project/repo, and has the requested command keys.
5. Docker execution is enabled when `AI_SCDC_CLOUD_RUNNER=docker_local`.
6. Task allowed paths and required tests are compatible with the selected commands.

The response remains `CloudRunResultRead` with:

- `cloud_run`
- `patch_artifact`, when one was produced

If Docker fails before patch capture, the response includes a failed `cloud_run` and no patch artifact. If tests fail after a patch is produced, the response includes the patch artifact and failed test data, but the task does not become merge-ready without later successful review/approval.

## Desktop Design

Keep the desktop UI compact and operational.

Add minimal controls for:

- Selecting or creating a sandbox profile for a GitHub repository.
- Showing the selected Docker image and command labels.
- Starting a cloud run with the active profile.
- Showing `docker_local` as the sandbox kind.
- Showing failure reason codes and redacted command output summaries.
- Showing test results through the same patch/test/review area used by local runs.

No separate cloud dashboard is required in Phase 8. The task board remains the primary workflow surface.

## Error Handling

Errors are recorded on `CloudRun.failure_reason` and exposed in API/UI as safe strings.

- `docker_unavailable`: Docker CLI missing, daemon stopped, image pull failed, or Docker command cannot start.
- `repo_checkout_failed`: GitHub clone/fetch, token, repository, network, or base branch failure.
- `patch_command_failed`: selected patch command exits non-zero or times out.
- `no_patch_produced`: patch command succeeds but `git diff` is empty.
- `test_failed`: patch exists, but one or more test commands fail or time out.
- `artifact_capture_failed`: diff, changed-file list, shas, logs, or test result cannot be captured.

Rules:

- Docker setup failures leave the task in its previous useful state and mark the cloud run failed.
- Patch command failures do not create a patch artifact.
- Empty diffs do not create a patch artifact.
- Test failures may still create a patch artifact and `LocalTestRun`, but reviewer approval must fail or remain blocked.
- Docker `not_run`, passed, and failed test results bridge through the existing local test/review/debug endpoints rather than a separate cloud-only test model.
- All failure paths emit task events with ids, status, and safe metadata only.
- Secrets are redacted before persistence or logging.

## Test Strategy

Unit tests:

- Executor selection chooses fake by default and Docker only when configured.
- Docker command construction uses the selected image, mounts only temporary paths, and does not mount the Docker socket or host home.
- Sandbox profile validation rejects missing, disabled, cross-project, cross-repo, or unknown command keys.
- Token redaction removes GitHub token values from stdout, stderr, exceptions, and command result payloads.
- Allowed-path validation rejects changed files outside task allowed paths.
- Docker/process failures map to the correct `failure_reason`.
- Timeouts terminate the process and produce safe failed results.

Integration tests without real Docker:

- Stubbed Docker process runner drives `queued -> running -> patch_ready`.
- Stubbed patch command output creates `LocalTaskRun`, `PatchArtifact`, `CloudRun`, task events, and `PATCH_READY`.
- Stubbed test failure stores patch and failed `LocalTestRun` without reaching merge-ready.
- Docker unavailable returns failed `CloudRun` and no patch artifact.
- Fake runner behavior remains unchanged.

Local smoke test with real Docker:

- Requires Docker Desktop, a GitHub PAT, a GitHub repo, and a sandbox profile.
- Runs from PowerShell using documented environment variables.
- Clones the GitHub repo inside Docker.
- Runs a whitelisted patch command that produces a small deterministic diff.
- Runs a whitelisted test command.
- Confirms the UI/API show patch artifact, logs, test status, review, approval, and PR creation.

Full verification:

- `pnpm test`
- `pnpm typecheck`
- `pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v`
- `git diff --check`

## Acceptance Criteria

- `AI_SCDC_CLOUD_RUNNER=fake` keeps the current Phase 7 fake cloud run behavior.
- `AI_SCDC_CLOUD_RUNNER=docker_local` triggers real Docker sandbox execution.
- Users can define or use a project/repository sandbox profile with whitelisted patch/test commands.
- Docker unavailable failures are visible and do not leave a task stuck in `running`.
- A successful Docker run creates a `CloudRun`, companion `LocalTaskRun`, `PatchArtifact`, test result, and task events.
- Changed files outside task allowed paths fail the run.
- Test command failure is captured with logs and does not silently approve the patch.
- Logs shown through API/UI are redacted.
- The existing reviewer, debugger, patch approval, human approval, and GitHub PR creation flow works with Docker-produced patch artifacts.
- README includes a PowerShell smoke test path for real Docker execution.

## Future Extensions

- Move `DockerLocalSandboxExecutor` behind a queue worker.
- Add remote VM/container sandbox providers.
- Add object storage for large logs and artifacts.
- Add cancellation and live log streaming.
- Add per-run CPU, memory, and disk limits.
- Add GitHub App authentication.
- Add model-backed patch generation inside the sandbox after the execution boundary is stable.
- Add GitLab support after GitHub plus Docker execution stabilizes.
