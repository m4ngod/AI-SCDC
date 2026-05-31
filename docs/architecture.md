# AI Software Company Desktop Console Architecture

## Product Shape

AI Software Company Desktop Console is a commercial multi-agent software engineering control plane. It is not a collection of chat windows. The long-term architecture is a desktop client, cloud control plane, model router, agent orchestrator, execution plane, and approval/audit system.

## Target Architecture

```text
Tauri Desktop Client
  -> Cloud Control Plane
  -> Agent Orchestrator
  -> LLM Gateway / Model Router
  -> Sandbox Workers / Local Runner
  -> Git Worktree / Tests / Review / PR
```

## Phase 0 Boundary

Phase 0 builds the runnable monorepo foundation:

- Vite React desktop shell.
- FastAPI control-plane API.
- JSON Schema Agent protocol.
- Deterministic LLM gateway interface.
- Mock worker simulator.
- SQLite-backed tests.
- PostgreSQL and Redis reservations in Docker Compose.

Phase 0 does not perform real model calls, real Tauri builds, real code patching, real billing, production auth, or cloud sandbox execution.

## First Runtime Flow

```text
User enters goal
  -> desktop shell requests a planner run
  -> FakePlanner creates structured TaskSpec drafts by default/no-route fallback,
     or a configured model-backed planner creates drafts
  -> user approves or rejects the batch
  -> approved drafts become normal tasks
  -> task events capture audit trail
  -> desktop right panel shows created tasks
```

The desktop task client defaults to mock mode when `VITE_API_BASE_URL` is unset
so demos and tests stay deterministic. Setting
`VITE_API_BASE_URL=http://127.0.0.1:8000` enables the minimal HTTP integration:
the desktop resolves or creates a demo project, creates planner runs, approves
or rejects generated drafts, and maps approved tasks into the right-panel task
board.

## Phase 2 Boundary

Phase 2 adds backend control-plane records for model providers, BYOK credentials, model routes, and usage ledger entries. Route resolution is metadata-only: if no planner route is configured, the API returns a deterministic fake planner route so the Phase 1 planner approval flow keeps working.

Credentials are write-only through the API. The server stores a development encrypted-secret placeholder and returns only credential metadata such as `secret_last4`. Phase 2 does not make real OpenAI-compatible or DeepSeek network calls.

## Phase 3 Boundary

Phase 3 adds the first real model-backed planner path. The API resolves the configured planner model route, opens the reversible development-only BYOK credential internally, calls an OpenAI-compatible chat completions provider through the gateway package, validates JSON TaskSpec drafts, and persists those drafts for human approval.

The existing approval boundary remains intact: model output creates planner drafts only, and tasks are created only after a human approves the planner run. If the route, credential, provider request, or model output is unavailable, the API falls back to `FakePlanner` and records a fallback reason on the planner run.

Phase 3 keeps the gateway in-process, does not add desktop model settings UI, does not use production KMS, and does not calculate real model pricing.

## Phase 4 Boundary

Phase 4 adds the Local Runner vertical slice. A developer can register an existing local git repository, run an approved task in a git worktree under `.worktrees`, capture a reviewable diff artifact, and move the task to `PATCH_READY`.

The review boundary remains intact: Phase 4 does not auto-commit, push, merge, create PRs, or run reviewer/debugger loops. It is a local execution and patch-review foundation for later automation. Patches are constrained by task `allowed_paths`, and approved planner drafts now preserve `allowed_paths` and `required_tests` on created tasks.

## Phase 5 Boundary

Phase 5 adds the deterministic local test, review, and debug-attempt workflow on top of Phase 4 patch artifacts. A patch-ready task now moves through `PATCH_READY -> SELF_TESTING -> REVIEWING -> APPROVED` when tests and deterministic review pass, or to `FIX_REQUESTED` when tests fail or review requests changes. The desktop exposes this as `Run local`, `Run tests`, and `Review patch` controls.

The Local test runner executes each task's `required_tests` commands inside the local runner worktree and records stdout, stderr, exit code, command timing, and failure reasons in `LocalTestRun`. It updates the patch artifact's test metadata and keeps command execution local.

Phase 5 adds durable `LocalTestRun`, `PatchReview`, and `DebugAttempt` records. `LocalTestRun` belongs to a project, task, local run, and patch artifact. `PatchReview` belongs to the same patch boundary, links to the latest test run when available, stores deterministic verdicts and required changes, and has an idempotency uniqueness constraint on `(patch_artifact_id, reviewer_kind)`. Re-running the deterministic review for the same artifact returns the existing review result rather than creating duplicate reviewer output. `DebugAttempt` records root cause and fix summary for failed tests or deterministic review findings; it does not edit files.

The deterministic review rules are intentionally small and auditable: require non-empty diff text, require at least one changed file, verify changed files stay inside task `allowed_paths`, and require the latest local test run to have passed. The workflow remains local and deterministic; Phase 5 does not auto-commit, push, merge, create PRs, call reviewer/debugger models, or automatically modify the worktree during debug.

## Phase 6 Boundary

Phase 6 adds the human patch approval boundary and a compact unified diff viewer. A task that has passed deterministic review can be patch-approved, which records a durable `PatchApproval`, moves the task to `MERGE_READY`, and exposes merge instructions without modifying git state.

The desktop shows changed files, unified diff text, test result, review verdict, patch approval state, worktree path, and merge instructions. A separate human-approval request moves `MERGE_READY -> HUMAN_APPROVAL`; Phase 6 still does not commit, merge, push, apply patches, create branches, or open pull requests.

## Phase 7 Boundary

Phase 7 adds a GitHub-only pull request publishing boundary. A task can run through a deterministic fake cloud sandbox, produce a normal patch artifact, pass the existing local verification/review workflow, receive patch approval, move to `HUMAN_APPROVAL`, and then create a GitHub pull request only after the user clicks `Create PR`.

The first cloud sandbox is a control-plane fake worker, not a real container service. The API stores GitHub PAT metadata through the development secret vault, registers GitHub repositories, records `CloudRun` and `PullRequestRecord` rows, and moves tasks to `PR_CREATED` after successful PR creation. Phase 7 does not merge pull requests, write to default branches, deploy code, add GitHub OAuth, or add GitLab support.

## Roadmap

Completed:

1. Phase 0 monorepo foundation with desktop shell, API, agent protocol, deterministic gateway interface, mock worker simulator, and local test infrastructure.
2. Phase 1 planner approval loop with fake planner drafts, human approval or rejection, task creation, and audit trail events.
3. Backend-first model router and BYOK foundation with provider metadata, write-only credential records, role-based route resolution, fake fallback routes, and append-only usage logging.
4. Real model-backed planner vertical slice that uses route resolution to create TaskSpec drafts for human approval, logs token usage, and falls back to fake drafts on provider failures.
5. Local Runner vertical slice with repository registration, git worktree execution, patch artifact capture, task events, and desktop run controls.
6. Deterministic local test, patch review, and debug-attempt workflow with desktop controls, durable verification records, and idempotent review results.
7. Human patch approval boundary with compact diff preview, durable approval records, `MERGE_READY` and `HUMAN_APPROVAL` transitions, and no automatic git merge.
8. GitHub-only cloud-run and pull-request boundary with PAT metadata, fake cloud sandbox artifacts, explicit `Create PR`, durable PR records, and no automatic merge.

Future:

1. Model-backed reviewer/debugger agents that can propose or apply fixes within explicit approval boundaries.
2. Commercial beta with users, organizations, subscriptions, credit wallet, usage ledger, rate limits, and billing provider abstraction.
