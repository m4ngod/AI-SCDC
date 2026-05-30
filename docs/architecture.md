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
  -> FakePlanner creates structured TaskSpec drafts
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

## Roadmap

Completed:

1. Phase 0 monorepo foundation with desktop shell, API, agent protocol, deterministic gateway interface, mock worker simulator, and local test infrastructure.
2. Phase 1 planner approval loop with fake planner drafts, human approval or rejection, task creation, and audit trail events.
3. Backend-first model router and BYOK foundation with provider metadata, write-only credential records, role-based route resolution, fake fallback routes, and append-only usage logging.
4. Real model-backed planner vertical slice that uses route resolution to create TaskSpec drafts for human approval, logs token usage, and falls back to fake drafts on provider failures.

Future:

1. Local Runner that reads repositories, creates worktrees, generates diffs, and lets users review patches.
2. Automated tests, reviewer loop, and debug loop.
3. Cloud sandbox workers, GitHub/GitLab integration, artifacts, and PR creation.
4. Commercial beta with users, organizations, subscriptions, credit wallet, usage ledger, rate limits, and billing provider abstraction.
