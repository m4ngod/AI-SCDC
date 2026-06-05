# AI Software Company Desktop Console

This repo includes the Phase 0 monorepo foundation, Phase 1 planner approval loop, Phase 2 backend-first model routing and BYOK foundation, Phase 3 real planner vertical slice, Phase 4 local runner vertical slice, Phase 5 deterministic test/review/debug workflow, Phase 6 human patch approval and diff viewer workflow, Phase 7 GitHub-only cloud-run and pull-request boundary, Phase 8 Docker local sandbox executor, Phase 9 local cloud-run queue worker boundary, Phase 10A remote worker control-plane contract, Phase 10B provider-neutral remote execution-plane contract, Phase 10C Aliyun provider MVP, Phase 10D run-scoped remote worker callback token hardening, Phase 11 real remote worker execution skeleton, Phase 12A bounded cloud-run log polling and safe remote log-stream reads, Phase 12B optional provider-native log sync, Phase 12C Aliyun MNS pull-worker receipt handling, and Phase 13A Aliyun operational hardening for a desktop multi-agent software engineering console.

## Local Commands

```bash
pnpm install
python -m pip install -e "apps/api[test]" -e "apps/worker[test]" -e "services/llm-gateway[test]"
pnpm test
pnpm typecheck
pnpm dev:api
pnpm dev:desktop
```

## Aliyun Operations

Phase 13A adds service-level operational seams for Aliyun MNS receipt recovery
and Aliyun ECI terminal cleanup. These helpers are intentionally not exposed as
public destructive HTTP routes before auth/RBAC.

Operator references:

- `docs/operations/aliyun-operational-runbook.md`
- `docs/operations/aliyun-ram-policies.md`

Use OSS lifecycle rules for development object retention. Do not add broad
API-side OSS deletion until authenticated organization-scoped operator controls
exist. `DevSecretVault` remains development-only; commercial production must
provide a KMS-backed `SecretVault` implementation before beta traffic.

The desktop runs in deterministic mock mode by default. Set
`VITE_API_BASE_URL=http://127.0.0.1:8000` before `pnpm dev:desktop` to enable
the minimal FastAPI planner approval path; `VITE_DEMO_PROJECT_ID` can pin the
demo project, otherwise the client creates or reuses one.

Phase 2 was backend-only. It added model providers, write-only BYOK credential metadata, role-based model routes, resolved fake fallback routes, and append-only usage ledger entries without making real provider calls. Credential responses remain metadata-only and never include raw or encrypted secrets.

## Phase 3 Local Real Planner Smoke Test

Phase 3 can call an OpenAI-compatible provider for planner drafts when the API has a configured planner route. DeepSeek can be configured as an OpenAI-compatible provider through the existing backend API. Do not paste API keys into chat, docs, or commits.

These Bash/curl and PowerShell examples are local smoke-test convenience commands. Local shell history may retain commands, so avoid shared shells and clear history if needed.

Example local setup:

```bash
pnpm dev:api
```

In another shell, set `DEEPSEEK_API_KEY` only for the local shell session, create a provider, create a credential, and create an active `planner` route:

```bash
export DEEPSEEK_API_KEY="<YOUR_LOCAL_API_KEY>"

curl -X POST http://127.0.0.1:8000/model-providers \
  -H "Content-Type: application/json" \
  -d '{"name":"Local DeepSeek","provider_type":"deepseek","base_url":"https://api.deepseek.com"}'

curl -X POST http://127.0.0.1:8000/model-credentials \
  -H "Content-Type: application/json" \
  -d "{\"provider_id\":\"<PROVIDER_ID>\",\"display_name\":\"Local key\",\"secret_value\":\"$DEEPSEEK_API_KEY\"}"

curl -X POST http://127.0.0.1:8000/model-routes \
  -H "Content-Type: application/json" \
  -d '{"agent_role":"planner","provider_id":"<PROVIDER_ID>","credential_id":"<CREDENTIAL_ID>","model_name":"deepseek-chat"}'
```

PowerShell equivalent:

```powershell
$base = "http://127.0.0.1:8000"
$secureKey = Read-Host "DeepSeek API key" -AsSecureString
$deepseekApiKey = [System.Net.NetworkCredential]::new("", $secureKey).Password
$oldRoute = $null
$route = $null
$credential = $null

function JsonBody($value) {
  $value | ConvertTo-Json -Depth 8 -Compress
}

try {
  $routes = @(Invoke-RestMethod -Uri "$base/model-routes" -Method Get)
  $oldRoute = $routes |
    Where-Object { $_.agent_role -eq "planner" -and $_.status -eq "active" } |
    Select-Object -First 1

  if ($oldRoute) {
    Invoke-RestMethod `
      -Uri "$base/model-routes/$($oldRoute.id)" `
      -Method Patch `
      -ContentType "application/json" `
      -Body (JsonBody @{ status = "disabled" }) | Out-Null
  }

  $provider = Invoke-RestMethod `
    -Uri "$base/model-providers" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      name = "Local DeepSeek"
      provider_type = "deepseek"
      base_url = "https://api.deepseek.com"
    })

  $credential = Invoke-RestMethod `
    -Uri "$base/model-credentials" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      provider_id = $provider.id
      display_name = "Local key"
      secret_value = $deepseekApiKey
    })

  $route = Invoke-RestMethod `
    -Uri "$base/model-routes" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      agent_role = "planner"
      provider_id = $provider.id
      credential_id = $credential.id
      model_name = "deepseek-chat"
      fallback_models = @()
    })

  $project = Invoke-RestMethod `
    -Uri "$base/projects" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      name = "Phase 3 smoke"
      description = "Local real planner smoke test"
    })

  $plannerRun = Invoke-RestMethod `
    -Uri "$base/projects/$($project.id)/planner-runs" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      goal = "Draft a small implementation plan for a README-only change."
    })

  $usage = @(Invoke-RestMethod `
    -Uri "$base/usage-ledger?planner_run_id=$($plannerRun.id)" `
    -Method Get)
  $modelUsage = @($usage | Where-Object { $_.usage_type -eq "model_tokens" })
  $totalTokens = ($modelUsage | ForEach-Object { $_.total_tokens } | Measure-Object -Sum).Sum

  [ordered]@{
    planner_run_id = $plannerRun.id
    planner_kind = $plannerRun.planner_kind
    fallback_reason = $plannerRun.fallback_reason
    draft_count = $plannerRun.draft_count
    usage_entries = $modelUsage.Count
    total_tokens = $totalTokens
  }
}
finally {
  if ($route) {
    Invoke-RestMethod `
      -Uri "$base/model-routes/$($route.id)" `
      -Method Patch `
      -ContentType "application/json" `
      -Body (JsonBody @{ status = "disabled" }) | Out-Null
  }
  if ($credential) {
    Invoke-RestMethod `
      -Uri "$base/model-credentials/$($credential.id)" `
      -Method Delete | Out-Null
  }
  if ($oldRoute) {
    Invoke-RestMethod `
      -Uri "$base/model-routes/$($oldRoute.id)" `
      -Method Patch `
      -ContentType "application/json" `
      -Body (JsonBody @{ status = "active" }) | Out-Null
  }
  Remove-Variable deepseekApiKey, secureKey -ErrorAction SilentlyContinue
}
```

`<PROVIDER_ID>` and `<CREDENTIAL_ID>` come from the JSON responses of the previous requests. You can run the normal desktop planner flow with `VITE_API_BASE_URL=http://127.0.0.1:8000`, or perform the smoke test directly through the API:

```bash
curl -X POST http://127.0.0.1:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"name":"Phase 3 smoke","description":"Local real planner smoke test"}'

curl -X POST http://127.0.0.1:8000/projects/<PROJECT_ID>/planner-runs \
  -H "Content-Type: application/json" \
  -d '{"goal":"Draft a small implementation plan for a README-only change."}'

curl http://127.0.0.1:8000/planner-runs/<PLANNER_RUN_ID>

curl "http://127.0.0.1:8000/usage-ledger?planner_run_id=<PLANNER_RUN_ID>"
```

`<PROJECT_ID>` comes from the project response, and `<PLANNER_RUN_ID>` comes from the planner run response. Verify the created planner run used the real model path, because fallback also creates drafts: the planner run JSON must have `planner_kind == "model"` and `fallback_reason == null`, and the usage ledger response must contain a `usage_type == "model_tokens"` entry for `<PLANNER_RUN_ID>`.

Do not commit or share the `DEEPSEEK_API_KEY` value (`<YOUR_LOCAL_API_KEY>` in the example). Credential responses remain metadata-only; the API does not return raw or encrypted secrets.

## Phase 4 Local Runner Smoke Test

Phase 4 can run an approved task against a local git repository by creating a worktree under `.worktrees`, applying a bounded local patch, and storing a patch artifact for review. It does not commit, push, merge, or open a PR.

Start the API:

```powershell
pnpm dev:api
```

In another PowerShell session, create a temporary git repository, register it, create a task, and start a local run:

```powershell
$base = "http://127.0.0.1:8000"
$smokeRoot = Join-Path $env:TEMP ("ai-scdc-local-runner-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $smokeRoot | Out-Null
git -C $smokeRoot init | Out-Null
git -C $smokeRoot branch -M main | Out-Null
Set-Content -Path (Join-Path $smokeRoot "README.md") -Value "# Local Runner Smoke"
git -C $smokeRoot add README.md | Out-Null
git -C $smokeRoot -c user.email=dev@example.com -c user.name="Dev User" commit -m "initial commit" | Out-Null

function JsonBody($value) {
  $value | ConvertTo-Json -Depth 8 -Compress
}

$project = Invoke-RestMethod `
  -Uri "$base/projects" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{ name = "Phase 4 smoke"; description = "Local runner smoke test" })

$repository = Invoke-RestMethod `
  -Uri "$base/projects/$($project.id)/repositories" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    name = "Smoke repo"
    local_path = $smokeRoot
    default_branch = "main"
  })

$task = Invoke-RestMethod `
  -Uri "$base/projects/$($project.id)/tasks" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    title = "Update README"
    role_required = "documentation"
    allowed_paths = @("README.md")
    required_tests = @("Manual patch artifact review")
  })

$run = Invoke-RestMethod `
  -Uri "$base/tasks/$($task.id)/local-runs" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{ repo_id = $repository.id })

$artifact = Invoke-RestMethod -Uri "$base/patch-artifacts/$($run.patch_artifact_id)" -Method Get

[ordered]@{
  task_id = $task.id
  local_run_status = $run.status
  worktree_path = $run.worktree_path
  files_changed = $artifact.files_changed -join ", "
  test_result = $artifact.test_result
  source_checkout_tracked_status = git -C $smokeRoot status --porcelain --untracked-files=no
}
```

The smoke output should show `local_run_status` as `patch_ready`, `files_changed` as `README.md`, and an empty `source_checkout_tracked_status`. Review the generated worktree path before deleting the temporary smoke repository.

## Phase 5 Local Test, Review, and Debug Workflow

Phase 5 extends the Phase 4 patch artifact with deterministic local verification. It starts after the desktop `Run local` action or `POST /tasks/{task_id}/local-runs` has already moved the task through the Phase 4 local-run path from `CREATED -> ASSIGNED -> IN_PROGRESS -> PATCH_READY` and created a patch artifact. Local-run failures can still move a task to `FIX_REQUESTED` before Phase 5 starts.

Once the task is `PATCH_READY` and a patch artifact exists, the Phase 5 flow is:

```text
PATCH_READY -> SELF_TESTING -> REVIEWING -> APPROVED
                  |              |
                  v              v
             FIX_REQUESTED  FIX_REQUESTED
```

Use the desktop `Run tests` action, or call `POST /patch-artifacts/{patch_artifact_id}/test-runs`, to execute the task `required_tests` commands inside the local runner worktree. The API first rejects invalid preconditions, such as a task that is not `PATCH_READY` or a local run without `worktree_path`, with HTTP 400 and without creating a `LocalTestRun` or `DebugAttempt`. Once a test run starts, passing tests store a `LocalTestRun`, update the patch artifact test result, and move the task from `PATCH_READY` through `SELF_TESTING` to `REVIEWING`. Started test command failures store a failed `LocalTestRun`, update the patch artifact test result, move the task to `FIX_REQUESTED`, and create a `DebugAttempt`.

Use the desktop `Review patch` action, or call `POST /patch-artifacts/{patch_artifact_id}/reviews`, once the task is `REVIEWING`. The deterministic review stores one `PatchReview` per patch artifact and reviewer kind, checks that the diff exists, changed files stay inside `allowed_paths`, and the latest local test run passed. An approved review moves the task to `APPROVED`; review findings move it to `FIX_REQUESTED` and create a `DebugAttempt`.

Related read endpoints are `GET /patch-artifacts/{patch_artifact_id}/test-runs`, `GET /test-runs/{test_run_id}`, `GET /patch-artifacts/{patch_artifact_id}/reviews`, `GET /patch-reviews/{review_id}`, and `GET /tasks/{task_id}/debug-attempts`.

### Phase 6 Patch Approval Smoke

After a patch reaches `APPROVED`, approve it without merging:

```powershell
$approval = Invoke-RestMethod `
  -Method Post `
  -Uri "$base/patch-artifacts/$($artifact.id)/approvals"

$approval.task.status
$approval.approval.merge_instructions
```

Expected:

```text
MERGE_READY
```

Then request human approval:

```powershell
$humanApproval = Invoke-RestMethod `
  -Method Post `
  -Uri "$base/patch-approvals/$($approval.approval.id)/request-human-approval"

$humanApproval.task.status
```

Expected:

```text
HUMAN_APPROVAL
```

This workflow records approval intent only. It does not run `git commit`, `git merge`, `git push`, `git apply`, or create a PR.

## Phase 7 GitHub PR Smoke Test

Phase 7 can create a GitHub pull request after an approved patch reaches `HUMAN_APPROVAL`. By default, local/dev mode uses the fake GitHub adapter: the workflow records a `PullRequestRecord` and returns a GitHub-shaped URL, but it does not push a branch or create a remote PR. Automated tests also use this fake adapter and do not require network access.

To run a real local smoke test, start the API with `AI_SCDC_GITHUB_PR_ADAPTER=real` and provide a real GitHub PAT with appropriate permissions for the target repository. Do not paste PATs into docs, commits, logs, or chat.

Start the API:

```powershell
$env:AI_SCDC_GITHUB_PR_ADAPTER = "real"
pnpm dev:api
```

In another PowerShell session:

```powershell
$base = "http://127.0.0.1:8000"
$secureToken = Read-Host "GitHub PAT" -AsSecureString
$githubToken = [System.Net.NetworkCredential]::new("", $secureToken).Password

function JsonBody($value) {
  $value | ConvertTo-Json -Depth 8 -Compress
}

$credential = Invoke-RestMethod `
  -Uri "$base/github-credentials" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    display_name = "Local GitHub"
    token = $githubToken
  })

$githubOwner = Read-Host "GitHub owner"
$githubRepo = Read-Host "GitHub repo"

$project = Invoke-RestMethod `
  -Uri "$base/projects" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{ name = "Phase 7 smoke"; description = "GitHub PR smoke test" })

$repository = Invoke-RestMethod `
  -Uri "$base/projects/$($project.id)/github-repositories" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    name = "$githubOwner/$githubRepo"
    repo_url = "https://github.com/$githubOwner/$githubRepo"
    github_owner = $githubOwner
    github_repo = $githubRepo
    default_branch = "main"
    github_credential_id = $credential.id
  })

$task = Invoke-RestMethod `
  -Uri "$base/projects/$($project.id)/tasks" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    title = "Update cloud smoke file"
    description = "Create a fake cloud sandbox patch for Phase 7 smoke testing."
    role_required = "backend"
    acceptance_criteria = @("Fake cloud patch is produced and reviewed.")
    allowed_paths = @("AI_SCDC_CLOUD_RUN.md")
    required_tests = @("cloud fake test")
    repo_id = $repository.id
    branch_name = $repository.default_branch
  })

$cloudRun = Invoke-RestMethod `
  -Uri "$base/tasks/$($task.id)/cloud-runs" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{ repo_id = $repository.id })

$processedCloudRun = Invoke-RestMethod `
  -Uri "$base/cloud-runs/$($cloudRun.cloud_run.id)/process" `
  -Method Post

$artifact = $processedCloudRun.patch_artifact

$testRun = Invoke-RestMethod `
  -Uri "$base/patch-artifacts/$($artifact.id)/test-runs" `
  -Method Post

$review = Invoke-RestMethod `
  -Uri "$base/patch-artifacts/$($artifact.id)/reviews" `
  -Method Post

$approval = Invoke-RestMethod `
  -Uri "$base/patch-artifacts/$($artifact.id)/approvals" `
  -Method Post

$humanApproval = Invoke-RestMethod `
  -Uri "$base/patch-approvals/$($approval.approval.id)/request-human-approval" `
  -Method Post

$pr = Invoke-RestMethod `
  -Uri "$base/patch-approvals/$($approval.approval.id)/pull-requests" `
  -Method Post

[ordered]@{
  cloud_run_enqueue_status = $cloudRun.cloud_run.status
  cloud_run_status = $processedCloudRun.cloud_run.status
  test_status = $testRun.test_run.status
  review_verdict = $review.review.verdict
  approved_status = $approval.task.status
  human_approval_status = $humanApproval.task.status
  pr_status = $pr.task.status
  pr_url = $pr.pull_request.github_pr_url
}

Remove-Variable githubToken, secureToken -ErrorAction SilentlyContinue
```

Expected task status:

```text
PR_CREATED
```

When the API is not started with `AI_SCDC_GITHUB_PR_ADAPTER=real`, the final `Create PR` request stays in fake adapter mode and no remote GitHub PR is created. The API returns only credential metadata.

## Phase 9 Queued Docker Local Sandbox PowerShell Smoke Test

Phase 9 keeps the fake cloud runner as the default and processes cloud runs through an explicit worker endpoint. Start the API with `AI_SCDC_CLOUD_RUNNER=docker_local` only when you want the local worker to clone a GitHub repository and run whitelisted profile commands in Docker. Keep the default fake PR adapter for this smoke test; set `AI_SCDC_GITHUB_PR_ADAPTER=real` only for a final, intentional real GitHub PR creation after human approval.

Do not paste PATs into docs, commits, logs, chat, or shell history, and do not put PATs in repository URLs. The GitHub repository `repo_url` in this smoke test is a normal non-token URL. For `docker_local` runs, the API opens the registered GitHub credential only for the clone step and passes it through a temporary container-local `GIT_ASKPASS` helper; it is redacted from command payloads and is not added to the sandbox profile environment.

Prerequisites:

- Docker Desktop is running.
- The PAT stored in the API GitHub credential has clone access to the target GitHub repository.
- The sandbox profile patch command exists in the target repo or is self-contained.

Start the API:

```powershell
$env:AI_SCDC_CLOUD_RUNNER = "docker_local"
Remove-Item Env:\AI_SCDC_GITHUB_PR_ADAPTER -ErrorAction SilentlyContinue
pnpm dev:api
```

Keep `allowed_env_vars` empty unless a sandbox profile command explicitly needs a server-process environment variable. Private repository cloning uses the registered GitHub credential and does not require whitelisting a token into the patch or test command environment.

In another PowerShell session, create the credential, GitHub repository, sandbox profile, task, enqueue a cloud run, and then explicitly process it:

```powershell
$base = "http://127.0.0.1:8000"
$secureToken = Read-Host "GitHub PAT" -AsSecureString
$githubToken = [System.Net.NetworkCredential]::new("", $secureToken).Password

function JsonBody($value) {
  $value | ConvertTo-Json -Depth 12 -Compress
}

try {
  $credential = Invoke-RestMethod `
    -Uri "$base/github-credentials" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      display_name = "Local Docker GitHub"
      token = $githubToken
    })

  $githubOwner = Read-Host "GitHub owner"
  $githubRepo = Read-Host "GitHub repo"
  $repoUrl = "https://github.com/$githubOwner/$githubRepo"

  $project = Invoke-RestMethod `
    -Uri "$base/projects" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      name = "Phase 8 Docker smoke"
      description = "Docker local sandbox smoke test"
    })

  $repository = Invoke-RestMethod `
    -Uri "$base/projects/$($project.id)/github-repositories" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      name = "$githubOwner/$githubRepo"
      repo_url = $repoUrl
      github_owner = $githubOwner
      github_repo = $githubRepo
      default_branch = "main"
      github_credential_id = $credential.id
    })

  $profile = Invoke-RestMethod `
    -Uri "$base/projects/$($project.id)/sandbox-profiles" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      repo_id = $repository.id
      name = "Default Docker profile"
      docker_image = "python:3.11-bookworm"
      patch_commands = @(
        @{
          key = "write-note"
          label = "Write smoke note"
          command = 'python -c "from pathlib import Path; Path(''AI_SCDC_DOCKER_SMOKE.md'').write_text(''# AI-SCDC Docker Smoke\n\nDocker local sandbox wrote this file.\n'')"'
          timeout_seconds = 300
          is_default = $true
        }
      )
      test_commands = @(
        @{
          key = "python-version"
          label = "Python version"
          command = "python -V"
          timeout_seconds = 300
          is_default = $true
        }
      )
      allowed_env_vars = @()
      network_enabled = $true
    })

  $task = Invoke-RestMethod `
    -Uri "$base/projects/$($project.id)/tasks" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      title = "Write Docker smoke note"
      description = "Create a deterministic Docker local sandbox patch."
      role_required = "documentation"
      acceptance_criteria = @("Docker local sandbox produces a patch artifact.")
      allowed_paths = @("AI_SCDC_DOCKER_SMOKE.md")
      required_tests = @("python -V")
      repo_id = $repository.id
      branch_name = $repository.default_branch
    })

  $cloudRun = Invoke-RestMethod `
    -Uri "$base/tasks/$($task.id)/cloud-runs" `
    -Method Post `
    -ContentType "application/json" `
    -Body (JsonBody @{
      repo_id = $repository.id
      sandbox_profile_id = $profile.id
      patch_command_key = "write-note"
      test_command_keys = @("python-version")
    })

  $processedCloudRun = Invoke-RestMethod `
    -Uri "$base/cloud-runs/$($cloudRun.cloud_run.id)/process" `
    -Method Post

  $cloudRunLogs = Invoke-RestMethod `
    -Uri "$base/cloud-runs/$($cloudRun.cloud_run.id)/logs" `
    -Method Get

  [ordered]@{
    cloud_run_enqueue_status = $cloudRun.cloud_run.status
    cloud_run_status = $processedCloudRun.cloud_run.status
    sandbox_kind = $processedCloudRun.cloud_run.sandbox_kind
    failure_reason = $processedCloudRun.cloud_run.failure_reason
    log_events = @($cloudRunLogs | ForEach-Object { $_.event }) -join ", "
    command_result_count = @($processedCloudRun.cloud_run.command_results).Count
    files_changed = if ($processedCloudRun.patch_artifact) { $processedCloudRun.patch_artifact.files_changed -join ", " } else { "" }
    test_result = if ($processedCloudRun.patch_artifact) { $processedCloudRun.patch_artifact.test_result } else { "" }
  }
}
finally {
  Remove-Variable githubToken, secureToken, repoUrl -ErrorAction SilentlyContinue
}
```

Expected successful smoke output shows `cloud_run_enqueue_status` as `queued`, `cloud_run_status` as `patch_ready`, `sandbox_kind` as `docker_local`, `files_changed` as `AI_SCDC_DOCKER_SMOKE.md`, and `test_result` as `passed`. If setup fails before an artifact is captured, inspect `failure_reason`, `log_events`, and the redacted cloud run command results:

```powershell
$processedCloudRun.cloud_run.command_results | ConvertTo-Json -Depth 8
```

## Phase 10A Remote Worker Lease API Smoke

Phase 10A keeps the default `local_db` queue provider and adds worker lease
endpoints for remote-worker control-plane testing. First run the Phase 9 or
Phase 8 enqueue setup through the `$cloudRun = Invoke-RestMethod ...` step, but
do not run the `$processedCloudRun = ... /process` step. With one cloud run
still queued, claim a lease, heartbeat it, and complete it with a `remote_stub`
result:

```powershell
$ApiBase = "http://127.0.0.1:8000"

function JsonBody($value) {
  $value | ConvertTo-Json -Depth 12 -Compress
}

$lease = Invoke-RestMethod `
  -Method Post `
  -Uri "$ApiBase/cloud-run-worker/leases" `
  -ContentType "application/json" `
  -Body (JsonBody @{
    worker_id = "remote-worker-smoke"
    worker_kind = "remote_stub"
    lease_seconds = 60
  })

if (-not $lease) { throw "No queued cloud run was available to lease." }

$heartbeat = Invoke-RestMethod `
  -Method Post `
  -Uri "$ApiBase/cloud-run-worker/leases/$($lease.lease_id)/heartbeat" `
  -ContentType "application/json" `
  -Body (JsonBody @{
    worker_id = "remote-worker-smoke"
    lease_seconds = 60
  })

$completion = Invoke-RestMethod `
  -Method Post `
  -Uri "$ApiBase/cloud-run-worker/leases/$($lease.lease_id)/complete" `
  -ContentType "application/json" `
  -Body (JsonBody @{
    worker_id = "remote-worker-smoke"
    result = @{
      status = "patch_ready"
      runner_kind = "remote_stub"
      base_sha = $null
      head_sha = $null
      worktree_ref = "remote-stub://$($lease.cloud_run.id)"
      summary = "Remote stub smoke patch."
      files_changed = @("AI_SCDC_REMOTE_STUB.md")
      tests_run = @()
      test_result = "not_run"
      risks = @()
      diff_text = "diff --git a/AI_SCDC_REMOTE_STUB.md b/AI_SCDC_REMOTE_STUB.md`n+remote smoke`n"
      command_results = @()
      test_command_results = @()
      failure_reason = $null
    }
  })
```

Focused verification commands used for Phase 10A and prerequisite workflows:

```bash
pytest apps/api/tests/test_github_repository_api.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_pull_request_api.py -v
pytest apps/api/tests/test_api_endpoints.py -v
pytest apps/worker/tests/test_test_runner.py -v
pytest apps/api/tests/test_test_review_debug_api.py -v
pytest apps/api/tests/test_patch_approval_api.py -v
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts src/test/App.test.tsx
pnpm --filter @ai-scdc/desktop typecheck
git diff --check
```

## Phase 10C Aliyun Provider MVP Smoke

Phase 10C can submit a cloud run through Aliyun MNS, store provider artifacts in
Aliyun OSS, and launch a short-lived Aliyun ECI remote worker from an ACR image.
Do not create a long-lived ECI instance manually in the console. The API creates
one short-lived container group when `runtime_provider` is `aliyun_eci`.

Required Aliyun services:

- RAM user or role with narrowly scoped MNS, OSS, and ECI permissions.
- OSS private bucket with lifecycle cleanup for development prefixes.
- MNS queue in queue mode.
- ACR private repository containing the remote worker image.
- ECI enabled in the same region as the selected VPC/VSwitch/security group.
- Outbound network path for the ECI worker if the API URL or GitHub requires
  public network access.

Build and push the worker image:

```powershell
$AcrImage = "<acr-registry>/<namespace>/<repo>:dev"
docker build -f apps/api/Dockerfile.remote-worker -t $AcrImage apps/api
docker push $AcrImage
```

Configure the local API shell:

```powershell
$env:AI_SCDC_ALIYUN_REGION_ID = "cn-hangzhou"
$env:AI_SCDC_ALIYUN_ACCESS_KEY_ID = "<set locally>"
$env:AI_SCDC_ALIYUN_ACCESS_KEY_SECRET = "<set locally>"
$env:AI_SCDC_ALIYUN_MNS_ENDPOINT = "https://<account-id>.mns.cn-hangzhou.aliyuncs.com"
$env:AI_SCDC_ALIYUN_MNS_QUEUE_NAME = "ai-scdc-cloud-runs-dev"
$env:AI_SCDC_ALIYUN_OSS_ENDPOINT = "https://oss-cn-hangzhou.aliyuncs.com"
$env:AI_SCDC_ALIYUN_OSS_BUCKET = "ai-scdc-dev-artifacts"
$env:AI_SCDC_ALIYUN_ECI_VSWITCH_ID = "<vsw-id>"
$env:AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID = "<sg-id>"
$env:AI_SCDC_ALIYUN_ECI_IMAGE = $AcrImage
$env:AI_SCDC_API_PUBLIC_BASE_URL = "<URL reachable from ECI>"
```

Phase 12C adds the MNS pull-worker capability; it does not replace the
existing assigned-run launch contract for Aliyun ECI workers. A cloud run
started with `queue_provider=aliyun_mns` and no remote runtime creates a
token-bearing MNS assignment for an external pull worker. A cloud run started
with both `queue_provider=aliyun_mns` and `runtime_provider=aliyun_eci` uses
the protected ECI assigned-run path and does not create an extra MNS delivery.

MNS pull mode is activated only for worker processes started without
`AI_SCDC_CLOUD_RUN_ID`. In that mode, the queue and storage providers are set
explicitly. Queue-only MNS pull cloud-run submissions must also provide a
`storage_provider`, typically `aliyun_oss`, so the queued worker message is
consumable:

```text
AI_SCDC_QUEUE_PROVIDER=aliyun_mns
AI_SCDC_STORAGE_PROVIDER=aliyun_oss
AI_SCDC_MNS_WAIT_SECONDS=3
```

Aliyun ECI launch still supports and currently uses assigned-run mode when the
protected worker identity and callback environment variables are provided. In
that mode the API injects:

```text
AI_SCDC_CLOUD_RUN_ID
AI_SCDC_WORKER_ID
AI_SCDC_CALLBACK_TOKEN
```

For protected Aliyun pull-mode claims, the worker sends queue metadata on
claim, including the MNS message ID and receipt plus the callback token. The
API accepts the receipt only when the claimed MNS message ID matches the
persisted enqueue result for that cloud run. The API owns post-terminal MNS
receipt deletion or acknowledgement after successful lease completion, so the
default worker launch path does not double-delete receipts. The API stores
only the callback token hash, the raw token appears only on controlled
delivery surfaces, and `queue_receipt` remains internal-only.

Start a cloud run with Aliyun providers:

```powershell
$cloudRun = Invoke-RestMethod `
  -Method Post `
  -Uri "$ApiBase/tasks/$TaskId/cloud-runs" `
  -ContentType "application/json" `
  -Body (JsonBody @{
    repo_id = $RepoId
    queue_provider = "aliyun_mns"
    storage_provider = "aliyun_oss"
    runtime_provider = "aliyun_eci"
  })
```

Expected output includes `queue_provider = aliyun_mns`, `storage_provider =
aliyun_oss`, `runtime_provider = aliyun_eci`, no MNS message ID or receipt, an
ECI runtime job ID, and `oss://` artifact/log URIs. The API generates a run-scoped
`AI_SCDC_CALLBACK_TOKEN`, injects it into the ECI worker environment, and
requires the worker to send it on lease, heartbeat, artifact-upload, and
completion callbacks. The raw callback token, token hash, Aliyun secrets, and
queue receipts must not appear in API responses.

Phase 11 remote workers fetch a protected execution payload after claiming a
lease. The payload includes the selected sandbox profile commands and the
repository's active clone credential, returned only through the
callback-token-protected worker payload. Outside that protected payload
response, the clone credential must not appear in API responses, logs,
artifacts, or completion payloads.

Phase 12A adds `GET /cloud-runs/{cloud_run_id}/logs/window` for bounded log
polling. The endpoint accepts an opaque `after` cursor, `limit`, and
`include_stream`; it returns persisted control-plane log entries and, when
complete object metadata is present, redacted remote log-stream lines. Phase
12B adds optional `sync_stream=true`, which refreshes persisted log-stream
metadata before polling for providers that support it. The deterministic
`remote_stub` refreshes a bounded object-storage log snapshot, and
`aliyun_eci` uses the `DescribeContainerLog` seam. This remains a polling API,
not live WebSocket or SSE streaming. The legacy `/logs` endpoint remains
available for full-list compatibility.

Cleanup after smoke:

- Stop or delete any ECI container group left from the smoke run, including a
  container group created before an API-side artifact write failure.
- Delete OSS objects under the development prefix if lifecycle cleanup has not
  removed them yet.
- Purge unneeded MNS test messages from queue-only pull-worker smoke runs.
- Release idle NAT gateway or EIP resources that were created only for smoke
  testing.

See `docs/architecture.md` for architecture and phase boundaries.
