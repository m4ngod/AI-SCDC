# AI Company Console Phase 0 Monorepo Design

## Goal

Build the first runnable foundation for **AI Software Company Desktop Console**: a contract-first monorepo with a Vite React desktop shell, FastAPI control-plane API, shared Agent protocol schemas, a mock worker, an LLM gateway interface, architecture documentation, and basic tests.

## Confirmed Decisions

- First slice: Phase 0 monorepo skeleton.
- Implementation style: contract-first foundation.
- Desktop scope: Vite React + TypeScript shell now; Tauri 2/Rust integration is documented and deferred.
- Backend data layer: FastAPI with SQLModel/SQLAlchemy-ready models and Alembic structure; tests use SQLite; `docker-compose.yml` reserves PostgreSQL and Redis for later phases.
- Model scope: no real LLM calls in Phase 0.
- Runner scope: no real git worktree or code patching in Phase 0.
- UI direction: dense, practical desktop console with sidebar, main thread, and right context panel.

## Scope

Phase 0 must prove that the product can run as a coherent engineering system, not merely as separate demos. The expected result is a repository where developers can install dependencies, run tests, start the API, and start the desktop shell.

Phase 0 includes:

- Monorepo structure and workspace commands.
- `apps/desktop`: Vite React + TypeScript console shell.
- `apps/api`: FastAPI API with health check, development identity placeholder, projects, conversations, messages, tasks, task events, and task transition endpoints.
- `apps/worker`: mock worker entry point that can simulate task progress without changing files.
- `packages/agent-protocol`: JSON Schema files for shared Agent/runtime contracts.
- `services/llm-gateway`: provider adapter interfaces and OpenAI-compatible configuration shapes without network calls.
- `docs/architecture.md`: target architecture, phased roadmap, and Phase 0 boundaries.
- Basic tests and a CI workflow that runs the same checks locally.

Phase 0 excludes:

- Real LLM provider calls.
- Real API key vault encryption.
- Real Tauri/Rust build chain.
- Real git worktree patch generation.
- Stripe, Alipay, WeChat Pay, billing collection, and payment flows.
- Real user registration, production auth, and production RBAC.
- Cloud sandbox workers.
- PR creation and merge automation.

## Target File Structure

```text
apps/
  desktop/
    src/
      api/
      components/
      fixtures/
      pages/
      styles/
      test/
    src-tauri/
      README.md
    package.json
    vite.config.ts
  api/
    app/
      api/
      core/
      db/
      models/
      schemas/
      services/
      main.py
    tests/
    alembic/
    pyproject.toml
  worker/
    app/
      main.py
    tests/
    pyproject.toml
packages/
  agent-protocol/
    schemas/
    samples/
    tests/
    package.json
services/
  llm-gateway/
    app/
    tests/
    pyproject.toml
docs/
  architecture.md
  superpowers/
    specs/
```

Each directory has one primary responsibility:

- `apps/desktop` owns UI composition and API calls only.
- `apps/api` owns HTTP API behavior, persistence models, state transitions, and audit event creation.
- `apps/worker` owns simulated background execution behavior.
- `packages/agent-protocol` owns cross-runtime contracts.
- `services/llm-gateway` owns model-provider interface definitions.
- `docs` owns architecture and phase boundaries.

## Component Boundaries

### Desktop Shell

The desktop shell is a browser-delivered Vite React app that represents the future desktop client. It must not embed backend business rules. It displays API data, submits user goals, and renders task state.

Required UI regions:

- Top bar: workspace, project, branch, runner status, cost placeholder, settings entry.
- Left sidebar: workspace, projects, conversations, agents, approvals, settings.
- Main area: project summary, conversation thread, goal input, task creation result, diff/log placeholder tabs.
- Right context panel: Agent status, active tasks, test results, review result, usage/cost placeholder.

The UI should be quiet, dense, and work-focused. Phase 0 does not include a landing page, marketing page, or polished brand system.

### API Control Plane

The API exposes the first control-plane shape:

- `GET /health`
- `GET /me`
- `GET /projects`
- `POST /projects`
- `GET /projects/{project_id}/conversations`
- `POST /projects/{project_id}/conversations`
- `GET /conversations/{conversation_id}/messages`
- `POST /conversations/{conversation_id}/messages`
- `GET /projects/{project_id}/tasks`
- `POST /projects/{project_id}/tasks`
- `GET /tasks/{task_id}`
- `PATCH /tasks/{task_id}`
- `POST /tasks/{task_id}/run`
- `POST /tasks/{task_id}/cancel`
- `GET /tasks/{task_id}/events`

The API uses a fixed development identity such as `dev_user`, `dev_organization`, and `dev_workspace`. This keeps auth out of Phase 0 while preserving the commercial multi-tenant shape.

### Task State Machine

The backend owns all task status transitions. No frontend or worker code may bypass it.

Initial statuses:

```text
CREATED
SPEC_DRAFTED
USER_APPROVED_SPEC
TASKS_CREATED
ASSIGNED
IN_PROGRESS
PATCH_READY
SELF_TESTING
REVIEWING
FIX_REQUESTED
APPROVED
CI_RUNNING
MERGE_READY
HUMAN_APPROVAL
MERGED
CLOSED
CANCELLED
```

Core rules:

- Invalid transitions are rejected.
- Every accepted transition writes a `task_event`.
- `MERGED` requires `HUMAN_APPROVAL`.
- Agents and mock workers cannot transition directly to `MERGED`.
- Cancel is allowed only for active non-terminal statuses.

### Agent Protocol

`packages/agent-protocol` defines JSON Schema contracts for:

- `AgentRole`
- `TaskStatus`
- `TaskSpec`
- `PatchResult`
- `ReviewResult`
- `DebugResult`
- `ToolCall`
- `ToolPermission`

Phase 0 tests validate representative sample payloads. Type generation is deferred; TypeScript and Python can reference schema shape manually until code generation is justified.

### LLM Gateway

`services/llm-gateway` defines interfaces only:

- `ProviderAdapter`
- `ModelProvider`
- `ModelRoute`
- `ModelCredentialRef`
- `UsageRecord`
- `OpenAICompatibleProviderConfig`

The service must not make network calls or read secrets in Phase 0. It should include deterministic fake adapter behavior for tests.

### Mock Worker

`apps/worker` simulates future execution. It can consume a task ID in development mode and request valid state transitions through the API service layer or an equivalent local function.

The mock worker may simulate:

- Planner generated a `TaskSpec`.
- Engineer produced `PatchResult`.
- Reviewer returned approval or changes requested.

It must not modify repository files or create branches in Phase 0.

## Data Flow

Primary Phase 0 path:

```text
User enters goal in desktop shell
  -> desktop API client posts task or conversation message
  -> FastAPI creates a task and initial task_event
  -> optional mock planner creates a TaskSpec payload
  -> state machine validates transitions
  -> task_events record status changes
  -> desktop fetches tasks and events
  -> right panel renders active tasks, Agent status, test placeholders, and review placeholders
```

Persistence is designed for PostgreSQL but tested with SQLite. Redis is reserved for later background queues and streaming.

## Error Handling

- API validation errors return structured FastAPI/Pydantic errors.
- Invalid task transitions return a clear 400 response with current status, requested status, and allowed next statuses.
- Missing resources return 404.
- Mock worker failures create a task event with failure details instead of silently failing.
- Desktop API client shows inline error states in the main thread or right panel.

## Testing Strategy

Backend tests:

```text
pytest apps/api/tests
- health check returns ok
- project, conversation, message, and task endpoints return expected development data
- valid task transitions are accepted
- invalid task transitions are rejected
- every accepted transition creates a task_event
```

Protocol tests:

```text
packages/agent-protocol
- schema files are valid JSON Schema
- sample TaskSpec validates
- sample PatchResult validates
- sample ReviewResult validates
```

Frontend tests:

```text
apps/desktop
- shell renders sidebar, main area, and right context panel
- task board renders task title, status, and assigned agent
- submitting a goal calls the task creation client
```

Workspace commands:

```text
pnpm test
pnpm typecheck
pytest
```

The implementation plan must use TDD for production behavior: write failing tests, verify the failure, implement the minimum code, and then verify the tests pass.

## Acceptance Criteria

Phase 0 is complete when:

- The repository has the target monorepo structure.
- `apps/desktop` starts locally and displays the console shell with realistic mock/API data.
- `apps/api` starts locally and exposes the agreed endpoints.
- Task transitions are validated by tests.
- Accepted task transitions write events.
- Agent protocol schemas and samples validate.
- LLM gateway interfaces exist with fake deterministic behavior and no external calls.
- Mock worker can simulate task progress without touching repository files.
- `docs/architecture.md` documents target architecture, Phase 0 boundaries, and future phases.
- Local test/typecheck commands are documented and pass in the prepared environment.

## Future Phases

Phase 1: Planner-only real model path that creates `TaskSpec` for user approval.

Phase 2: Model router and BYOK foundation with encrypted credential placeholder and usage logging.

Phase 3: Local Runner that reads repositories, creates worktrees, generates diffs, and lets users review patches.

Phase 4: Automated tests, reviewer loop, and debug loop.

Phase 5: Cloud sandbox workers, GitHub/GitLab integration, artifacts, and PR creation.

Phase 6: Commercial beta with users, organizations, subscriptions, credit wallet, usage ledger, rate limits, and billing provider abstraction.

## Open Constraints

- Keep Phase 0 small enough to finish as a foundation branch.
- Prefer plain, stable interfaces over framework-specific magic.
- Do not add a real model SDK until the protocol and API contracts are stable.
- Do not introduce Tauri build requirements until the UI/API loop is useful in Vite.
- Keep commercial data concepts visible from the first schema, even when auth and billing are development placeholders.
