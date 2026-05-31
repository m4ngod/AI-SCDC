# AI Software Company Desktop Console

This repo includes the Phase 0 monorepo foundation, Phase 1 planner approval loop, Phase 2 backend-first model routing and BYOK foundation, Phase 3 real planner vertical slice, Phase 4 local runner vertical slice, Phase 5 deterministic test/review/debug workflow, and Phase 6 human patch approval and diff viewer workflow for a desktop multi-agent software engineering console.

## Local Commands

```bash
pnpm install
python -m pip install -e "apps/api[test]" -e "apps/worker[test]" -e "services/llm-gateway[test]"
pnpm test
pnpm typecheck
pnpm dev:api
pnpm dev:desktop
```

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

Phase 7 can create a real GitHub pull request after an approved patch reaches `HUMAN_APPROVAL`. Automated tests use the fake GitHub adapter and do not require network access.

Start the API:

```powershell
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

$artifact = $cloudRun.patch_artifact

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
  cloud_run_status = $cloudRun.cloud_run.status
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

Do not commit GitHub PATs or paste them into chat. The API returns only credential metadata.

Focused verification commands used for Phase 7 and its Phase 6/Phase 5 prerequisite workflow:

```bash
pytest apps/api/tests/test_github_repository_api.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_pull_request_api.py -v
pytest apps/worker/tests/test_test_runner.py -v
pytest apps/api/tests/test_test_review_debug_api.py -v
pytest apps/api/tests/test_patch_approval_api.py -v
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts src/test/App.test.tsx
pnpm --filter @ai-scdc/desktop typecheck
git diff --check
```

See `docs/architecture.md` for architecture and phase boundaries.
