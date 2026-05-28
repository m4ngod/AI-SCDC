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
  -> desktop shell calls task creation client
  -> API creates task
  -> task state machine validates transitions
  -> task events capture audit trail
  -> desktop right panel shows task state
```

## Future Phases

1. Planner-only real model path that creates TaskSpec for user approval.
2. Model router and BYOK foundation with encrypted credential placeholder and usage logging.
3. Local Runner that reads repositories, creates worktrees, generates diffs, and lets users review patches.
4. Automated tests, reviewer loop, and debug loop.
5. Cloud sandbox workers, GitHub/GitLab integration, artifacts, and PR creation.
6. Commercial beta with users, organizations, subscriptions, credit wallet, usage ledger, rate limits, and billing provider abstraction.
