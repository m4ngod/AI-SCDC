# Phase 0 Monorepo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable, testable Phase 0 monorepo foundation for AI Software Company Desktop Console.

**Architecture:** Use a contract-first layout: shared JSON schemas define Agent/runtime payloads, FastAPI owns API behavior and task transitions, the Vite React desktop shell consumes API-shaped data, and worker/LLM services expose deterministic fake behavior only. Real LLM calls, real Tauri builds, real git patching, production auth, and billing flows remain outside Phase 0.

**Tech Stack:** pnpm workspaces, TypeScript, Vite, React, Vitest, Testing Library, Ajv, Python 3.11+, FastAPI, SQLModel, Pydantic, pytest, httpx, SQLite for tests, docker-compose reservations for PostgreSQL and Redis.

---

## File Structure

Create or modify these files:

- Create: `.github/workflows/ci.yml` for CI checks.
- Create: `.editorconfig` for basic editor consistency.
- Modify: `.gitignore` to keep generated/runtime files out of git.
- Create: `package.json` root workspace scripts.
- Create: `pnpm-workspace.yaml` workspace package list.
- Create: `pytest.ini` root pytest discovery and Python path.
- Create: `docker-compose.yml` reserved PostgreSQL and Redis services.
- Create: `docs/architecture.md` target architecture and Phase 0 boundaries.
- Create: `packages/agent-protocol/package.json`.
- Create: `packages/agent-protocol/schemas/*.schema.json`.
- Create: `packages/agent-protocol/samples/*.json`.
- Create: `packages/agent-protocol/tests/schema.test.ts`.
- Create: `apps/api/pyproject.toml`.
- Create: `apps/api/app/ai_company_api/**/*.py`.
- Create: `apps/api/alembic/README.md`.
- Create: `apps/api/tests/*.py`.
- Create: `services/llm-gateway/pyproject.toml`.
- Create: `services/llm-gateway/app/ai_company_llm_gateway/**/*.py`.
- Create: `services/llm-gateway/tests/*.py`.
- Create: `apps/worker/pyproject.toml`.
- Create: `apps/worker/app/ai_company_worker/**/*.py`.
- Create: `apps/worker/tests/*.py`.
- Create: `apps/desktop/package.json`.
- Create: `apps/desktop/index.html`.
- Create: `apps/desktop/vite.config.ts`.
- Create: `apps/desktop/tsconfig.json`.
- Create: `apps/desktop/src/**/*.tsx`.
- Create: `apps/desktop/src/**/*.ts`.
- Create: `apps/desktop/src-tauri/README.md`.

---

## Task 1: Root Workspace Baseline

**Files:**
- Create: `.editorconfig`
- Modify: `.gitignore`
- Create: `package.json`
- Create: `pnpm-workspace.yaml`
- Create: `pytest.ini`
- Create: `docker-compose.yml`
- Create: `.github/workflows/ci.yml`

This task creates project configuration only. It has no production behavior, so the verification is command discovery and workspace script execution rather than TDD.

- [ ] **Step 1: Create root workspace files**

Create `.editorconfig`:

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
indent_style = space
indent_size = 2

[*.py]
indent_size = 4
```

Create `pnpm-workspace.yaml`:

```yaml
packages:
  - "apps/desktop"
  - "packages/agent-protocol"
```

Create `package.json`:

```json
{
  "name": "ai-scdc",
  "private": true,
  "packageManager": "pnpm@9.15.4",
  "scripts": {
    "dev:desktop": "pnpm --filter @ai-scdc/desktop dev",
    "dev:api": "python -m uvicorn ai_company_api.main:app --app-dir apps/api/app --reload",
    "test": "pnpm test:js && pnpm test:python",
    "test:js": "pnpm -r test",
    "test:python": "pytest apps/api/tests apps/worker/tests services/llm-gateway/tests",
    "typecheck": "pnpm -r typecheck",
    "lint": "pnpm -r lint"
  }
}
```

Create `pytest.ini`:

```ini
[pytest]
testpaths =
    apps/api/tests
    apps/worker/tests
    services/llm-gateway/tests
pythonpath =
    apps/api/app
    apps/worker/app
    services/llm-gateway/app
```

Update `.gitignore` so it contains:

```gitignore
.superpowers/
.venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
node_modules/
dist/
coverage/
dev.db
*.pyc
```

Create `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ai_scdc
      POSTGRES_USER: ai_scdc
      POSTGRES_PASSWORD: ai_scdc
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

volumes:
  postgres_data:
```

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
  pull_request:

jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with:
          version: 9.15.4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: pnpm
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pnpm install
      - run: python -m pip install -e "apps/api[test]" -e "apps/worker[test]" -e "services/llm-gateway[test]"
      - run: pnpm typecheck
      - run: pnpm test:js
      - run: pnpm test:python
```

- [ ] **Step 2: Verify workspace scripts are discoverable**

Run:

```bash
pnpm --version
python --version
pnpm -r list --depth -1
```

Expected: pnpm and Python versions print. The recursive list may show no packages until Tasks 2 and 6 create package manifests.

- [ ] **Step 3: Commit root baseline**

```bash
git add .editorconfig .gitignore package.json pnpm-workspace.yaml pytest.ini docker-compose.yml .github/workflows/ci.yml
git commit -m "chore: add root workspace baseline"
```

---

## Task 2: Agent Protocol Schemas

**Files:**
- Create: `packages/agent-protocol/package.json`
- Create: `packages/agent-protocol/tsconfig.json`
- Create: `packages/agent-protocol/schemas/agent-role.schema.json`
- Create: `packages/agent-protocol/schemas/task-status.schema.json`
- Create: `packages/agent-protocol/schemas/task-spec.schema.json`
- Create: `packages/agent-protocol/schemas/patch-result.schema.json`
- Create: `packages/agent-protocol/schemas/review-result.schema.json`
- Create: `packages/agent-protocol/schemas/debug-result.schema.json`
- Create: `packages/agent-protocol/schemas/tool-call.schema.json`
- Create: `packages/agent-protocol/schemas/tool-permission.schema.json`
- Create: `packages/agent-protocol/samples/task-spec.sample.json`
- Create: `packages/agent-protocol/samples/patch-result.sample.json`
- Create: `packages/agent-protocol/samples/review-result.sample.json`
- Create: `packages/agent-protocol/tests/schema.test.ts`

- [ ] **Step 1: Write the failing schema validation tests**

Create `packages/agent-protocol/package.json`:

```json
{
  "name": "@ai-scdc/agent-protocol",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "vitest run",
    "typecheck": "tsc --noEmit",
    "lint": "tsc --noEmit"
  },
  "devDependencies": {
    "@types/node": "^22.10.2",
    "ajv": "^8.17.1",
    "typescript": "^5.7.2",
    "vitest": "^2.1.8"
  }
}
```

Create `packages/agent-protocol/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "resolveJsonModule": true,
    "esModuleInterop": true,
    "skipLibCheck": true
  },
  "include": ["tests/**/*.ts"]
}
```

Create `packages/agent-protocol/tests/schema.test.ts`:

```ts
import Ajv from "ajv";
import { describe, expect, test } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname;

function readJson(path: string) {
  return JSON.parse(readFileSync(join(root, path), "utf8"));
}

function validatorFor(schemaName: string) {
  const ajv = new Ajv({ allErrors: true, strict: true });
  ajv.addSchema(readJson("schemas/agent-role.schema.json"), "agent-role.schema.json");
  ajv.addSchema(readJson("schemas/task-status.schema.json"), "task-status.schema.json");
  const validate = ajv.compile(readJson(`schemas/${schemaName}`));
  return validate;
}

describe("agent protocol schemas", () => {
  test("TaskSpec sample validates", () => {
    const validate = validatorFor("task-spec.schema.json");
    const sample = readJson("samples/task-spec.sample.json");
    expect(validate(sample), JSON.stringify(validate.errors)).toBe(true);
  });

  test("PatchResult sample validates", () => {
    const validate = validatorFor("patch-result.schema.json");
    const sample = readJson("samples/patch-result.sample.json");
    expect(validate(sample), JSON.stringify(validate.errors)).toBe(true);
  });

  test("ReviewResult sample validates", () => {
    const validate = validatorFor("review-result.schema.json");
    const sample = readJson("samples/review-result.sample.json");
    expect(validate(sample), JSON.stringify(validate.errors)).toBe(true);
  });

  test("TaskSpec requires acceptance criteria", () => {
    const validate = validatorFor("task-spec.schema.json");
    const invalid = {
      title: "Implement task board UI",
      role_required: "frontend",
      objective: "Show active agent tasks.",
      allowed_paths: ["apps/desktop/**"],
      required_tests: ["TaskBoard renders"]
    };
    expect(validate(invalid)).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail because schemas are missing**

Run:

```bash
pnpm install
pnpm --filter @ai-scdc/agent-protocol test
```

Expected: FAIL with file-not-found errors for `schemas/agent-role.schema.json`.

- [ ] **Step 3: Create schemas and samples**

Create `packages/agent-protocol/schemas/agent-role.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "agent-role.schema.json",
  "type": "string",
  "enum": ["planner", "frontend", "backend", "reviewer", "debugger", "security", "product", "documentation"]
}
```

Create `packages/agent-protocol/schemas/task-status.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "task-status.schema.json",
  "type": "string",
  "enum": [
    "CREATED",
    "SPEC_DRAFTED",
    "USER_APPROVED_SPEC",
    "TASKS_CREATED",
    "ASSIGNED",
    "IN_PROGRESS",
    "PATCH_READY",
    "SELF_TESTING",
    "REVIEWING",
    "FIX_REQUESTED",
    "APPROVED",
    "CI_RUNNING",
    "MERGE_READY",
    "HUMAN_APPROVAL",
    "MERGED",
    "CLOSED",
    "CANCELLED"
  ]
}
```

Create `packages/agent-protocol/schemas/task-spec.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "task-spec.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["title", "role_required", "objective", "acceptance_criteria", "allowed_paths", "required_tests", "risk_level"],
  "properties": {
    "title": { "type": "string", "minLength": 1 },
    "role_required": { "$ref": "agent-role.schema.json" },
    "objective": { "type": "string", "minLength": 1 },
    "acceptance_criteria": { "type": "array", "minItems": 1, "items": { "type": "string", "minLength": 1 } },
    "allowed_paths": { "type": "array", "minItems": 1, "items": { "type": "string", "minLength": 1 } },
    "required_tests": { "type": "array", "items": { "type": "string", "minLength": 1 } },
    "risk_level": { "type": "string", "enum": ["low", "medium", "high"] }
  }
}
```

Create `packages/agent-protocol/schemas/patch-result.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "patch-result.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["status", "summary", "files_changed", "tests_run", "test_result", "risks"],
  "properties": {
    "status": { "type": "string", "enum": ["patch_ready", "changes_requested", "failed"] },
    "summary": { "type": "string" },
    "files_changed": { "type": "array", "items": { "type": "string" } },
    "tests_run": { "type": "array", "items": { "type": "string" } },
    "test_result": { "type": "string", "enum": ["passed", "failed", "not_run"] },
    "diff_artifact_id": { "type": "string" },
    "risks": { "type": "array", "items": { "type": "string" } }
  }
}
```

Create `packages/agent-protocol/schemas/review-result.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "review-result.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["verdict", "issues", "required_changes"],
  "properties": {
    "verdict": { "type": "string", "enum": ["approved", "changes_requested", "rejected"] },
    "issues": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["severity", "category", "file", "problem", "recommendation"],
        "properties": {
          "severity": { "type": "string", "enum": ["low", "medium", "high"] },
          "category": { "type": "string" },
          "file": { "type": "string" },
          "problem": { "type": "string" },
          "recommendation": { "type": "string" }
        }
      }
    },
    "required_changes": { "type": "array", "items": { "type": "string" } }
  }
}
```

Create `packages/agent-protocol/schemas/debug-result.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "debug-result.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["root_cause", "fix_summary", "tests_run", "status"],
  "properties": {
    "root_cause": { "type": "string" },
    "fix_summary": { "type": "string" },
    "tests_run": { "type": "array", "items": { "type": "string" } },
    "status": { "type": "string", "enum": ["fixed", "not_fixed"] }
  }
}
```

Create `packages/agent-protocol/schemas/tool-call.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "tool-call.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["tool_name", "input_json", "status", "risk_level"],
  "properties": {
    "tool_name": { "type": "string" },
    "input_json": { "type": "object" },
    "output_json": { "type": "object" },
    "status": { "type": "string", "enum": ["pending", "running", "succeeded", "failed", "awaiting_approval"] },
    "risk_level": { "type": "string", "enum": ["low", "medium", "high"] }
  }
}
```

Create `packages/agent-protocol/schemas/tool-permission.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "tool-permission.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["tool_name", "permission_level", "requires_approval", "risk_level"],
  "properties": {
    "tool_name": { "type": "string" },
    "permission_level": { "type": "string", "enum": ["none", "read", "write", "admin"] },
    "requires_approval": { "type": "boolean" },
    "risk_level": { "type": "string", "enum": ["low", "medium", "high"] }
  }
}
```

Create `packages/agent-protocol/samples/task-spec.sample.json`:

```json
{
  "title": "Implement task board UI",
  "role_required": "frontend",
  "objective": "Show all active agent tasks in the right panel.",
  "acceptance_criteria": [
    "Display task title, status, assigned agent, and updated time.",
    "Support opening task details from the task board."
  ],
  "allowed_paths": ["apps/desktop/**", "packages/agent-protocol/**"],
  "required_tests": ["TaskBoard component renders task list"],
  "risk_level": "medium"
}
```

Create `packages/agent-protocol/samples/patch-result.sample.json`:

```json
{
  "status": "patch_ready",
  "summary": "Added TaskBoard component and task stream hook.",
  "files_changed": ["apps/desktop/src/components/TaskBoard.tsx"],
  "tests_run": ["pnpm --filter @ai-scdc/desktop test"],
  "test_result": "passed",
  "diff_artifact_id": "artifact_123",
  "risks": ["Reconnect behavior is not implemented in Phase 0."]
}
```

Create `packages/agent-protocol/samples/review-result.sample.json`:

```json
{
  "verdict": "changes_requested",
  "issues": [
    {
      "severity": "medium",
      "category": "reliability",
      "file": "apps/desktop/src/components/TaskBoard.tsx",
      "problem": "Task status labels do not distinguish blocked tasks.",
      "recommendation": "Add a visible status treatment for FIX_REQUESTED."
    }
  ],
  "required_changes": ["Render FIX_REQUESTED with a warning treatment."]
}
```

- [ ] **Step 4: Run tests and typecheck**

Run:

```bash
pnpm --filter @ai-scdc/agent-protocol test
pnpm --filter @ai-scdc/agent-protocol typecheck
```

Expected: PASS.

- [ ] **Step 5: Commit protocol package**

```bash
git add packages/agent-protocol
git commit -m "feat: add agent protocol schemas"
```

---

## Task 3: API Task State Machine

**Files:**
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/app/ai_company_api/__init__.py`
- Create: `apps/api/app/ai_company_api/services/task_state.py`
- Create: `apps/api/tests/test_task_state.py`

- [ ] **Step 1: Create Python package manifest**

Create `apps/api/pyproject.toml`:

```toml
[project]
name = "ai-company-api"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115.0",
  "httpx>=0.28.0",
  "pydantic>=2.10.0",
  "sqlmodel>=0.0.22",
  "uvicorn>=0.32.0"
]

[project.optional-dependencies]
test = ["pytest>=8.3.0"]

[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["app"]
```

Create `apps/api/app/ai_company_api/__init__.py`:

```python
"""AI Company API package."""
```

- [ ] **Step 2: Write failing state machine tests**

Create `apps/api/tests/test_task_state.py`:

```python
import pytest

from ai_company_api.services.task_state import (
    InvalidTaskTransition,
    TaskStatus,
    allowed_next_statuses,
    validate_transition,
)


def test_created_task_can_move_to_spec_drafted() -> None:
    result = validate_transition(TaskStatus.CREATED, TaskStatus.SPEC_DRAFTED, actor_type="system")
    assert result == TaskStatus.SPEC_DRAFTED


def test_created_task_cannot_move_directly_to_merged() -> None:
    with pytest.raises(InvalidTaskTransition) as exc:
        validate_transition(TaskStatus.CREATED, TaskStatus.MERGED, actor_type="system")

    assert "CREATED -> MERGED" in str(exc.value)


def test_merged_requires_human_approval_previous_status() -> None:
    with pytest.raises(InvalidTaskTransition):
        validate_transition(TaskStatus.MERGE_READY, TaskStatus.MERGED, actor_type="system")

    assert validate_transition(TaskStatus.HUMAN_APPROVAL, TaskStatus.MERGED, actor_type="system") == TaskStatus.MERGED


def test_agent_cannot_merge_even_from_human_approval() -> None:
    with pytest.raises(InvalidTaskTransition) as exc:
        validate_transition(TaskStatus.HUMAN_APPROVAL, TaskStatus.MERGED, actor_type="agent")

    assert "actor_type=agent" in str(exc.value)


def test_active_task_can_be_cancelled() -> None:
    assert validate_transition(TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED, actor_type="user") == TaskStatus.CANCELLED


def test_terminal_task_cannot_be_cancelled() -> None:
    with pytest.raises(InvalidTaskTransition):
        validate_transition(TaskStatus.MERGED, TaskStatus.CANCELLED, actor_type="user")


def test_allowed_next_statuses_are_sorted_strings() -> None:
    assert allowed_next_statuses(TaskStatus.CREATED) == ["ASSIGNED", "CANCELLED", "SPEC_DRAFTED"]
```

- [ ] **Step 3: Run test to verify it fails**

Run:

```bash
python -m pip install -e "apps/api[test]"
pytest apps/api/tests/test_task_state.py -v
```

Expected: FAIL with `ModuleNotFoundError` or import errors for `ai_company_api.services.task_state`.

- [ ] **Step 4: Implement minimal state machine**

Create `apps/api/app/ai_company_api/services/task_state.py`:

```python
from enum import StrEnum


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    SPEC_DRAFTED = "SPEC_DRAFTED"
    USER_APPROVED_SPEC = "USER_APPROVED_SPEC"
    TASKS_CREATED = "TASKS_CREATED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    PATCH_READY = "PATCH_READY"
    SELF_TESTING = "SELF_TESTING"
    REVIEWING = "REVIEWING"
    FIX_REQUESTED = "FIX_REQUESTED"
    APPROVED = "APPROVED"
    CI_RUNNING = "CI_RUNNING"
    MERGE_READY = "MERGE_READY"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    MERGED = "MERGED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = {TaskStatus.MERGED, TaskStatus.CLOSED, TaskStatus.CANCELLED}

TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.SPEC_DRAFTED, TaskStatus.ASSIGNED, TaskStatus.CANCELLED},
    TaskStatus.SPEC_DRAFTED: {TaskStatus.USER_APPROVED_SPEC, TaskStatus.CANCELLED},
    TaskStatus.USER_APPROVED_SPEC: {TaskStatus.TASKS_CREATED, TaskStatus.CANCELLED},
    TaskStatus.TASKS_CREATED: {TaskStatus.ASSIGNED, TaskStatus.CANCELLED},
    TaskStatus.ASSIGNED: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.PATCH_READY, TaskStatus.FIX_REQUESTED, TaskStatus.CANCELLED},
    TaskStatus.PATCH_READY: {TaskStatus.SELF_TESTING, TaskStatus.REVIEWING, TaskStatus.CANCELLED},
    TaskStatus.SELF_TESTING: {TaskStatus.REVIEWING, TaskStatus.FIX_REQUESTED, TaskStatus.CANCELLED},
    TaskStatus.REVIEWING: {TaskStatus.APPROVED, TaskStatus.FIX_REQUESTED, TaskStatus.CANCELLED},
    TaskStatus.FIX_REQUESTED: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.APPROVED: {TaskStatus.CI_RUNNING, TaskStatus.MERGE_READY, TaskStatus.CANCELLED},
    TaskStatus.CI_RUNNING: {TaskStatus.MERGE_READY, TaskStatus.FIX_REQUESTED, TaskStatus.CANCELLED},
    TaskStatus.MERGE_READY: {TaskStatus.HUMAN_APPROVAL, TaskStatus.CANCELLED},
    TaskStatus.HUMAN_APPROVAL: {TaskStatus.MERGED, TaskStatus.CLOSED},
    TaskStatus.MERGED: set(),
    TaskStatus.CLOSED: set(),
    TaskStatus.CANCELLED: set(),
}


class InvalidTaskTransition(ValueError):
    """Raised when a task status transition is not allowed."""


def allowed_next_statuses(current: TaskStatus) -> list[str]:
    return sorted(status.value for status in TRANSITIONS[current])


def validate_transition(current: TaskStatus, requested: TaskStatus, actor_type: str) -> TaskStatus:
    if current in TERMINAL_STATUSES:
        raise InvalidTaskTransition(f"{current.value} -> {requested.value} is not allowed from terminal status")

    if requested == TaskStatus.MERGED and (current != TaskStatus.HUMAN_APPROVAL or actor_type != "system"):
        raise InvalidTaskTransition(
            f"{current.value} -> {requested.value} requires HUMAN_APPROVAL and actor_type=system, got actor_type={actor_type}"
        )

    if requested not in TRANSITIONS[current]:
        allowed = ", ".join(allowed_next_statuses(current))
        raise InvalidTaskTransition(f"{current.value} -> {requested.value} is not allowed. Allowed: {allowed}")

    return requested
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest apps/api/tests/test_task_state.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit API state machine**

```bash
git add apps/api/pyproject.toml apps/api/app/ai_company_api apps/api/tests/test_task_state.py
git commit -m "feat: add task state machine"
```

---

## Task 4: API Persistence and HTTP Endpoints

**Files:**
- Create: `apps/api/app/ai_company_api/db/session.py`
- Create: `apps/api/app/ai_company_api/models/entities.py`
- Create: `apps/api/app/ai_company_api/schemas/api.py`
- Create: `apps/api/app/ai_company_api/services/repository.py`
- Create: `apps/api/app/ai_company_api/api/routes.py`
- Create: `apps/api/app/ai_company_api/api/__init__.py`
- Create: `apps/api/app/ai_company_api/main.py`
- Create: `apps/api/alembic/README.md`
- Create: `apps/api/tests/test_api_endpoints.py`

- [ ] **Step 1: Write failing API endpoint tests**

Create `apps/api/tests/test_api_endpoints.py`:

```python
from fastapi.testclient import TestClient

from ai_company_api.main import create_app


def client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


def test_health_check_returns_ok() -> None:
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_me_returns_development_identity() -> None:
    response = client().get("/me")
    assert response.status_code == 200
    assert response.json()["user_id"] == "dev_user"
    assert response.json()["workspace_id"] == "dev_workspace"


def test_project_conversation_and_task_flow() -> None:
    api = client()

    project_response = api.post("/projects", json={"name": "Demo Project", "description": "Demo repo"})
    assert project_response.status_code == 201
    project = project_response.json()
    assert project["name"] == "Demo Project"

    conversation_response = api.post(
        f"/projects/{project['id']}/conversations",
        json={"title": "Build task board", "conversation_type": "planning"},
    )
    assert conversation_response.status_code == 201
    conversation = conversation_response.json()

    message_response = api.post(
        f"/conversations/{conversation['id']}/messages",
        json={"sender_type": "user", "content": "Create a task board UI"},
    )
    assert message_response.status_code == 201
    assert message_response.json()["content"] == "Create a task board UI"

    task_response = api.post(
        f"/projects/{project['id']}/tasks",
        json={
            "conversation_id": conversation["id"],
            "title": "Implement task board",
            "description": "Show active agent tasks.",
            "role_required": "frontend",
        },
    )
    assert task_response.status_code == 201
    task = task_response.json()
    assert task["status"] == "CREATED"

    events_response = api.get(f"/tasks/{task['id']}/events")
    assert events_response.status_code == 200
    assert events_response.json()[0]["event_type"] == "task_created"


def test_task_run_creates_transition_event() -> None:
    api = client()
    project = api.post("/projects", json={"name": "Demo Project"}).json()
    task = api.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Implement API", "description": "Create endpoints.", "role_required": "backend"},
    ).json()

    run_response = api.post(f"/tasks/{task['id']}/run")
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "ASSIGNED"

    events = api.get(f"/tasks/{task['id']}/events").json()
    assert [event["event_type"] for event in events] == ["task_created", "task_transitioned"]


def test_invalid_patch_to_merged_is_rejected() -> None:
    api = client()
    project = api.post("/projects", json={"name": "Demo Project"}).json()
    task = api.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Merge directly", "description": "Invalid transition.", "role_required": "backend"},
    ).json()

    response = api.patch(f"/tasks/{task['id']}", json={"status": "MERGED"})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["current_status"] == "CREATED"
    assert body["detail"]["requested_status"] == "MERGED"
```

- [ ] **Step 2: Run endpoint tests to verify failure**

Run:

```bash
pytest apps/api/tests/test_api_endpoints.py -v
```

Expected: FAIL because `ai_company_api.main` does not exist.

- [ ] **Step 3: Implement models, repository, routes, and app factory**

Create `apps/api/app/ai_company_api/models/entities.py`:

```python
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("project"), primary_key=True)
    workspace_id: str = "dev_workspace"
    name: str
    description: str = ""
    created_by: str = "dev_user"
    created_at: datetime = Field(default_factory=now_utc)


class Conversation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("conversation"), primary_key=True)
    project_id: str
    user_id: str = "dev_user"
    title: str
    conversation_type: str = "planning"
    created_at: datetime = Field(default_factory=now_utc)


class Message(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("message"), primary_key=True)
    conversation_id: str
    sender_type: str
    sender_id: str = "dev_user"
    content: str
    structured_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now_utc)


class Task(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("task"), primary_key=True)
    project_id: str
    conversation_id: str | None = None
    parent_task_id: str | None = None
    title: str
    description: str
    role_required: str
    status: str = "CREATED"
    priority: int = 0
    risk_level: str = "medium"
    acceptance_criteria: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    assigned_agent_profile_id: str | None = None
    repo_id: str | None = None
    branch_name: str | None = None
    worktree_ref: str | None = None
    budget_limit: int | None = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class TaskEvent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("event"), primary_key=True)
    task_id: str
    actor_type: str
    actor_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now_utc)
```

Create `apps/api/app/ai_company_api/db/session.py`:

```python
from collections.abc import Generator

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


def build_engine(database_url: str):
    if database_url == "sqlite://":
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    if database_url.startswith("sqlite"):
        return create_engine(database_url, connect_args={"check_same_thread": False})
    return create_engine(database_url)


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)


def session_generator(engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def get_session_dependency() -> Generator[Session, None, None]:
    raise RuntimeError("Session dependency was not configured")
```

Create `apps/api/app/ai_company_api/schemas/api.py`:

```python
from pydantic import BaseModel, Field

from ai_company_api.services.task_state import TaskStatus


class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ConversationCreate(BaseModel):
    title: str
    conversation_type: str = "planning"


class MessageCreate(BaseModel):
    sender_type: str
    content: str
    structured_payload: dict = Field(default_factory=dict)


class TaskCreate(BaseModel):
    title: str
    description: str
    role_required: str
    conversation_id: str | None = None
    risk_level: str = "medium"
    acceptance_criteria: list[str] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    status: TaskStatus
```

Create `apps/api/app/ai_company_api/services/repository.py`:

```python
from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import Conversation, Message, Project, Task, TaskEvent, now_utc
from ai_company_api.services.task_state import InvalidTaskTransition, TaskStatus, allowed_next_statuses, validate_transition


def create_project(session: Session, name: str, description: str = "") -> Project:
    project = Project(name=name, description=description)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def list_projects(session: Session) -> list[Project]:
    return list(session.exec(select(Project)).all())


def get_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def get_task(session: Session, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def create_task_event(session: Session, task_id: str, event_type: str, actor_type: str, actor_id: str, payload: dict) -> TaskEvent:
    event = TaskEvent(task_id=task_id, event_type=event_type, actor_type=actor_type, actor_id=actor_id, payload=payload)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def transition_task(session: Session, task_id: str, requested_status: TaskStatus, actor_type: str, actor_id: str) -> Task:
    task = get_task(session, task_id)
    current = TaskStatus(task.status)
    try:
        next_status = validate_transition(current, requested_status, actor_type=actor_type)
    except InvalidTaskTransition as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "current_status": current.value,
                "requested_status": requested_status.value,
                "allowed_next_statuses": allowed_next_statuses(current),
            },
        ) from exc
    task.status = next_status.value
    task.updated_at = now_utc()
    session.add(task)
    session.commit()
    session.refresh(task)
    create_task_event(
        session,
        task_id=task.id,
        event_type="task_transitioned",
        actor_type=actor_type,
        actor_id=actor_id,
        payload={"from_status": current.value, "to_status": next_status.value},
    )
    return task
```

Create `apps/api/app/ai_company_api/api/routes.py`:

```python
from fastapi import APIRouter, Depends, status
from sqlmodel import Session, select

from ai_company_api.db.session import get_session_dependency
from ai_company_api.models.entities import Conversation, Message, Task, TaskEvent
from ai_company_api.schemas.api import ConversationCreate, MessageCreate, ProjectCreate, TaskCreate, TaskUpdate
from ai_company_api.services.repository import (
    create_project,
    create_task_event,
    get_project,
    get_task,
    list_projects,
    transition_task,
)
from ai_company_api.services.task_state import TaskStatus

router = APIRouter()


@router.get("/projects")
def get_projects(session: Session = Depends(get_session_dependency)):
    return list_projects(session)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def post_project(payload: ProjectCreate, session: Session = Depends(get_session_dependency)):
    return create_project(session, name=payload.name, description=payload.description)


@router.get("/projects/{project_id}/conversations")
def get_conversations(project_id: str, session: Session = Depends(get_session_dependency)):
    get_project(session, project_id)
    return list(session.exec(select(Conversation).where(Conversation.project_id == project_id)).all())


@router.post("/projects/{project_id}/conversations", status_code=status.HTTP_201_CREATED)
def post_conversation(project_id: str, payload: ConversationCreate, session: Session = Depends(get_session_dependency)):
    get_project(session, project_id)
    conversation = Conversation(
        project_id=project_id,
        title=payload.title,
        conversation_type=payload.conversation_type,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@router.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: str, session: Session = Depends(get_session_dependency)):
    return list(session.exec(select(Message).where(Message.conversation_id == conversation_id)).all())


@router.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
def post_message(conversation_id: str, payload: MessageCreate, session: Session = Depends(get_session_dependency)):
    message = Message(
        conversation_id=conversation_id,
        sender_type=payload.sender_type,
        content=payload.content,
        structured_payload=payload.structured_payload,
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


@router.get("/projects/{project_id}/tasks")
def get_project_tasks(project_id: str, session: Session = Depends(get_session_dependency)):
    get_project(session, project_id)
    return list(session.exec(select(Task).where(Task.project_id == project_id)).all())


@router.post("/projects/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
def post_task(project_id: str, payload: TaskCreate, session: Session = Depends(get_session_dependency)):
    get_project(session, project_id)
    task = Task(
        project_id=project_id,
        conversation_id=payload.conversation_id,
        title=payload.title,
        description=payload.description,
        role_required=payload.role_required,
        risk_level=payload.risk_level,
        acceptance_criteria=payload.acceptance_criteria,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    create_task_event(
        session,
        task_id=task.id,
        event_type="task_created",
        actor_type="user",
        actor_id="dev_user",
        payload={"status": task.status},
    )
    return task


@router.get("/tasks/{task_id}")
def get_task_by_id(task_id: str, session: Session = Depends(get_session_dependency)):
    return get_task(session, task_id)


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, payload: TaskUpdate, session: Session = Depends(get_session_dependency)):
    return transition_task(session, task_id, payload.status, actor_type="system", actor_id="dev_system")


@router.post("/tasks/{task_id}/run")
def run_task(task_id: str, session: Session = Depends(get_session_dependency)):
    return transition_task(session, task_id, TaskStatus.ASSIGNED, actor_type="system", actor_id="dev_system")


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, session: Session = Depends(get_session_dependency)):
    return transition_task(session, task_id, TaskStatus.CANCELLED, actor_type="user", actor_id="dev_user")


@router.get("/tasks/{task_id}/events")
def get_task_events(task_id: str, session: Session = Depends(get_session_dependency)):
    get_task(session, task_id)
    return list(session.exec(select(TaskEvent).where(TaskEvent.task_id == task_id)).all())
```

Create `apps/api/app/ai_company_api/main.py`:

```python
from collections.abc import Generator

from fastapi import Depends, FastAPI
from sqlmodel import Session

from ai_company_api.api.routes import router
from ai_company_api.db.session import build_engine, get_session_dependency, init_db, session_generator


def create_app(database_url: str = "sqlite:///./dev.db") -> FastAPI:
    app = FastAPI(title="AI Company Console API")
    engine = build_engine(database_url)
    init_db(engine)

    def get_session() -> Generator[Session, None, None]:
        yield from session_generator(engine)

    app.dependency_overrides[get_session_dependency] = get_session

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/me")
    def me() -> dict[str, str]:
        return {
            "user_id": "dev_user",
            "organization_id": "dev_organization",
            "workspace_id": "dev_workspace",
        }

    app.include_router(router)
    return app


app = create_app()
```

Create `apps/api/app/ai_company_api/api/__init__.py`:

```python
"""API route modules."""
```

Create `apps/api/alembic/README.md`:

```markdown
# Alembic

Phase 0 reserves this directory for database migrations. The first implementation uses SQLModel metadata creation for local development and SQLite tests. Introduce generated Alembic revisions when the schema stabilizes enough to require migration history.
```

- [ ] **Step 4: Run API tests**

Run:

```bash
pytest apps/api/tests/test_api_endpoints.py apps/api/tests/test_task_state.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit API endpoints**

```bash
git add apps/api
git commit -m "feat: add phase 0 api endpoints"
```

---

## Task 5: LLM Gateway Interface

**Files:**
- Create: `services/llm-gateway/pyproject.toml`
- Create: `services/llm-gateway/app/ai_company_llm_gateway/__init__.py`
- Create: `services/llm-gateway/app/ai_company_llm_gateway/models.py`
- Create: `services/llm-gateway/app/ai_company_llm_gateway/adapters.py`
- Create: `services/llm-gateway/tests/test_fake_adapter.py`

- [ ] **Step 1: Create package manifest**

Create `services/llm-gateway/pyproject.toml`:

```toml
[project]
name = "ai-company-llm-gateway"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.10.0"]

[project.optional-dependencies]
test = ["pytest>=8.3.0"]

[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["app"]
```

Create `services/llm-gateway/app/ai_company_llm_gateway/__init__.py`:

```python
"""AI Company LLM gateway interfaces."""
```

- [ ] **Step 2: Write failing fake adapter tests**

Create `services/llm-gateway/tests/test_fake_adapter.py`:

```python
from ai_company_llm_gateway.adapters import FakeProviderAdapter
from ai_company_llm_gateway.models import ModelRoute, ProviderRequest


def test_fake_adapter_returns_deterministic_response() -> None:
    adapter = FakeProviderAdapter()
    route = ModelRoute(agent_role="planner", primary_model="fake-planner", fallback_models=["fake-general"])
    request = ProviderRequest(route=route, prompt="Create a TaskSpec")

    response = adapter.complete(request)

    assert response.model_name == "fake-planner"
    assert response.content == "fake response for planner: Create a TaskSpec"
    assert response.usage.total_tokens == 8


def test_fake_adapter_records_provider_name() -> None:
    adapter = FakeProviderAdapter(provider_name="openai-compatible-dev")
    assert adapter.provider_name == "openai-compatible-dev"
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
python -m pip install -e "services/llm-gateway[test]"
pytest services/llm-gateway/tests/test_fake_adapter.py -v
```

Expected: FAIL because gateway modules do not exist.

- [ ] **Step 4: Implement interface and fake adapter**

Create `services/llm-gateway/app/ai_company_llm_gateway/models.py`:

```python
from pydantic import BaseModel, Field


class ModelProvider(BaseModel):
    name: str
    provider_type: str
    base_url: str | None = None


class ModelRoute(BaseModel):
    agent_role: str
    primary_model: str
    fallback_models: list[str] = Field(default_factory=list)


class ModelCredentialRef(BaseModel):
    credential_id: str
    provider_name: str


class UsageRecord(BaseModel):
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ProviderRequest(BaseModel):
    route: ModelRoute
    prompt: str


class ProviderResponse(BaseModel):
    provider_name: str
    model_name: str
    content: str
    usage: UsageRecord


class OpenAICompatibleProviderConfig(BaseModel):
    provider_name: str
    base_url: str
    default_headers: dict[str, str] = Field(default_factory=dict)
```

Create `services/llm-gateway/app/ai_company_llm_gateway/adapters.py`:

```python
from typing import Protocol

from ai_company_llm_gateway.models import ProviderRequest, ProviderResponse, UsageRecord


class ProviderAdapter(Protocol):
    provider_name: str

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Return a model response for a provider request."""


class FakeProviderAdapter:
    def __init__(self, provider_name: str = "fake") -> None:
        self.provider_name = provider_name

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=request.route.primary_model,
            content=f"fake response for {request.route.agent_role}: {request.prompt}",
            usage=UsageRecord(prompt_tokens=3, completion_tokens=5),
        )
```

- [ ] **Step 5: Run gateway tests**

Run:

```bash
pytest services/llm-gateway/tests/test_fake_adapter.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit LLM gateway interface**

```bash
git add services/llm-gateway
git commit -m "feat: add llm gateway interface"
```

---

## Task 6: Mock Worker

**Files:**
- Create: `apps/worker/pyproject.toml`
- Create: `apps/worker/app/ai_company_worker/__init__.py`
- Create: `apps/worker/app/ai_company_worker/simulator.py`
- Create: `apps/worker/app/ai_company_worker/main.py`
- Create: `apps/worker/tests/test_simulator.py`

- [ ] **Step 1: Create worker package manifest**

Create `apps/worker/pyproject.toml`:

```toml
[project]
name = "ai-company-worker"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.10.0"]

[project.optional-dependencies]
test = ["pytest>=8.3.0"]

[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["app"]
```

Create `apps/worker/app/ai_company_worker/__init__.py`:

```python
"""AI Company mock worker package."""
```

- [ ] **Step 2: Write failing simulator tests**

Create `apps/worker/tests/test_simulator.py`:

```python
from ai_company_worker.simulator import MockWorkerSimulator


def test_mock_worker_returns_phase_0_progression() -> None:
    simulator = MockWorkerSimulator()

    result = simulator.simulate(task_id="task_123", role_required="frontend")

    assert result.task_id == "task_123"
    assert result.transitions == ["ASSIGNED", "IN_PROGRESS", "PATCH_READY", "REVIEWING"]
    assert result.patch_result["status"] == "patch_ready"
    assert result.review_result["verdict"] == "approved"


def test_mock_worker_does_not_report_file_mutations() -> None:
    simulator = MockWorkerSimulator()

    result = simulator.simulate(task_id="task_123", role_required="backend")

    assert result.files_changed == []
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
python -m pip install -e "apps/worker[test]"
pytest apps/worker/tests/test_simulator.py -v
```

Expected: FAIL because `ai_company_worker.simulator` does not exist.

- [ ] **Step 4: Implement deterministic worker simulator**

Create `apps/worker/app/ai_company_worker/simulator.py`:

```python
from pydantic import BaseModel, Field


class MockWorkerResult(BaseModel):
    task_id: str
    transitions: list[str]
    patch_result: dict
    review_result: dict
    files_changed: list[str] = Field(default_factory=list)


class MockWorkerSimulator:
    def simulate(self, task_id: str, role_required: str) -> MockWorkerResult:
        return MockWorkerResult(
            task_id=task_id,
            transitions=["ASSIGNED", "IN_PROGRESS", "PATCH_READY", "REVIEWING"],
            patch_result={
                "status": "patch_ready",
                "summary": f"Simulated {role_required} implementation for Phase 0.",
                "files_changed": [],
                "tests_run": [],
                "test_result": "not_run",
                "risks": ["Mock worker does not modify repository files in Phase 0."],
            },
            review_result={"verdict": "approved", "issues": [], "required_changes": []},
            files_changed=[],
        )
```

Create `apps/worker/app/ai_company_worker/main.py`:

```python
from ai_company_worker.simulator import MockWorkerSimulator


def main() -> None:
    result = MockWorkerSimulator().simulate(task_id="task_dev", role_required="frontend")
    print(result.model_dump_json())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run worker tests**

Run:

```bash
pytest apps/worker/tests/test_simulator.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit mock worker**

```bash
git add apps/worker
git commit -m "feat: add mock worker simulator"
```

---

## Task 7: Desktop Shell

**Files:**
- Create: `apps/desktop/package.json`
- Create: `apps/desktop/index.html`
- Create: `apps/desktop/tsconfig.json`
- Create: `apps/desktop/vite.config.ts`
- Create: `apps/desktop/src/main.tsx`
- Create: `apps/desktop/src/App.tsx`
- Create: `apps/desktop/src/api/client.ts`
- Create: `apps/desktop/src/fixtures/demoData.ts`
- Create: `apps/desktop/src/components/Shell.tsx`
- Create: `apps/desktop/src/components/TaskBoard.tsx`
- Create: `apps/desktop/src/components/GoalInput.tsx`
- Create: `apps/desktop/src/styles/app.css`
- Create: `apps/desktop/src/test/App.test.tsx`
- Create: `apps/desktop/src/test/setup.ts`
- Create: `apps/desktop/src-tauri/README.md`

- [ ] **Step 1: Create desktop package manifest and config**

Create `apps/desktop/package.json`:

```json
{
  "name": "@ai-scdc/desktop",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "test": "vitest run",
    "typecheck": "tsc --noEmit",
    "lint": "tsc --noEmit"
  },
  "dependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "vite": "^6.0.3",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^19.0.1",
    "@types/react-dom": "^19.0.2",
    "jsdom": "^25.0.1",
    "typescript": "^5.7.2",
    "vitest": "^2.1.8"
  }
}
```

Create `apps/desktop/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"]
}
```

Create `apps/desktop/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts"
  }
});
```

Create `apps/desktop/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>AI Company Console</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `apps/desktop/src/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 2: Write failing UI tests**

Create `apps/desktop/src/test/App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import { App } from "../App";
import type { ConsoleApiClient } from "../api/client";

function createClient(): ConsoleApiClient {
  return {
    createTask: vi.fn(async () => ({
      id: "task_new",
      title: "Build task board",
      status: "CREATED",
      role_required: "frontend",
      assigned_agent: "Frontend Engineer",
      updated_at: "2026-05-29T00:00:00Z"
    }))
  };
}

describe("desktop shell", () => {
  test("renders sidebar, main thread, and right context panel", () => {
    render(<App apiClient={createClient()} />);

    expect(screen.getByRole("banner")).toHaveTextContent("AI Company");
    expect(screen.getByRole("navigation", { name: "Primary" })).toHaveTextContent("Projects");
    expect(screen.getByRole("main")).toHaveTextContent("Project command thread");
    expect(screen.getByLabelText("Task context panel")).toHaveTextContent("Agent status");
  });

  test("task board renders task title status and agent", () => {
    render(<App apiClient={createClient()} />);

    expect(screen.getByText("Implement task board UI")).toBeInTheDocument();
    expect(screen.getByText("PATCH_READY")).toBeInTheDocument();
    expect(screen.getByText("Frontend Engineer")).toBeInTheDocument();
  });

  test("submitting a goal calls task creation client", async () => {
    const apiClient = createClient();
    const user = userEvent.setup();
    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("Goal"), "Create a task board");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    expect(apiClient.createTask).toHaveBeenCalledWith("Create a task board");
    expect(await screen.findByText("Build task board")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run UI tests to verify failure**

Run:

```bash
pnpm install
pnpm --filter @ai-scdc/desktop test
```

Expected: FAIL because `src/App.tsx` and API client do not exist.

- [ ] **Step 4: Implement desktop shell**

Create `apps/desktop/src/api/client.ts`:

```ts
export type TaskCard = {
  id: string;
  title: string;
  status: string;
  role_required: string;
  assigned_agent: string;
  updated_at: string;
};

export type ConsoleApiClient = {
  createTask(goal: string): Promise<TaskCard>;
};

export const fakeApiClient: ConsoleApiClient = {
  async createTask(goal: string) {
    return {
      id: "task_created_from_goal",
      title: goal.length > 0 ? "Build task board" : "Untitled task",
      status: "CREATED",
      role_required: "frontend",
      assigned_agent: "Frontend Engineer",
      updated_at: new Date("2026-05-29T00:00:00Z").toISOString()
    };
  }
};
```

Create `apps/desktop/src/fixtures/demoData.ts`:

```ts
import type { TaskCard } from "../api/client";

export const demoTasks: TaskCard[] = [
  {
    id: "task_001",
    title: "Implement task board UI",
    status: "PATCH_READY",
    role_required: "frontend",
    assigned_agent: "Frontend Engineer",
    updated_at: "2026-05-29T00:00:00Z"
  },
  {
    id: "task_002",
    title: "Add task state machine",
    status: "REVIEWING",
    role_required: "backend",
    assigned_agent: "Backend Engineer",
    updated_at: "2026-05-29T00:05:00Z"
  }
];
```

Create `apps/desktop/src/components/TaskBoard.tsx`:

```tsx
import type { TaskCard } from "../api/client";

type TaskBoardProps = {
  tasks: TaskCard[];
};

export function TaskBoard({ tasks }: TaskBoardProps) {
  return (
    <section className="panel" aria-label="Task board">
      <h2>Active tasks</h2>
      <div className="task-list">
        {tasks.map((task) => (
          <article className="task-card" key={task.id}>
            <div>
              <h3>{task.title}</h3>
              <p>{task.assigned_agent}</p>
            </div>
            <span className="status-pill">{task.status}</span>
          </article>
        ))}
      </div>
    </section>
  );
}
```

Create `apps/desktop/src/components/GoalInput.tsx`:

```tsx
import { FormEvent, useState } from "react";

type GoalInputProps = {
  onSubmit(goal: string): Promise<void>;
};

export function GoalInput({ onSubmit }: GoalInputProps) {
  const [goal, setGoal] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = goal.trim();
    if (trimmed.length === 0) return;
    await onSubmit(trimmed);
    setGoal("");
  }

  return (
    <form className="goal-form" onSubmit={handleSubmit}>
      <label htmlFor="goal">Goal</label>
      <textarea id="goal" value={goal} onChange={(event) => setGoal(event.target.value)} />
      <button type="submit">Create task</button>
    </form>
  );
}
```

Create `apps/desktop/src/components/Shell.tsx`:

```tsx
import { ReactNode } from "react";

type ShellProps = {
  children: ReactNode;
  rightPanel: ReactNode;
};

export function Shell({ children, rightPanel }: ShellProps) {
  return (
    <div className="app-shell">
      <header className="topbar" role="banner">
        <strong>AI Company</strong>
        <span>Demo Workspace / Demo Project / main</span>
        <span>Local Runner: Mock | Cost: $0.00</span>
      </header>
      <nav className="sidebar" aria-label="Primary">
        <a>Workspace</a>
        <a>Projects</a>
        <a>Conversations</a>
        <a>Agents</a>
        <a>Approvals</a>
        <a>Settings</a>
      </nav>
      <main className="main-thread">{children}</main>
      <aside className="context-panel" aria-label="Task context panel">
        {rightPanel}
      </aside>
    </div>
  );
}
```

Create `apps/desktop/src/App.tsx`:

```tsx
import { useState } from "react";

import type { ConsoleApiClient, TaskCard } from "./api/client";
import { fakeApiClient } from "./api/client";
import { GoalInput } from "./components/GoalInput";
import { Shell } from "./components/Shell";
import { TaskBoard } from "./components/TaskBoard";
import { demoTasks } from "./fixtures/demoData";
import "./styles/app.css";

type AppProps = {
  apiClient?: ConsoleApiClient;
};

export function App({ apiClient = fakeApiClient }: AppProps) {
  const [tasks, setTasks] = useState<TaskCard[]>(demoTasks);

  async function createTask(goal: string) {
    const created = await apiClient.createTask(goal);
    setTasks((current) => [created, ...current]);
  }

  return (
    <Shell
      rightPanel={
        <>
          <section className="panel">
            <h2>Agent status</h2>
            <p>Planner idle, Frontend active, Reviewer waiting.</p>
          </section>
          <TaskBoard tasks={tasks} />
          <section className="panel">
            <h2>Test results</h2>
            <p>No test run attached in Phase 0.</p>
          </section>
        </>
      }
    >
      <section className="thread-section">
        <h1>Project command thread</h1>
        <p>Describe the work. The Phase 0 mock flow creates a task without calling a model.</p>
        <GoalInput onSubmit={createTask} />
      </section>
      <section className="thread-section">
        <h2>Diff and logs</h2>
        <p>Diff viewer and terminal logs are reserved as empty surfaces in Phase 0.</p>
      </section>
    </Shell>
  );
}
```

Create `apps/desktop/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

Create `apps/desktop/src/styles/app.css` with stable layout:

```css
body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #f4f6f8;
  color: #17202a;
}

.app-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 180px minmax(420px, 1fr) 320px;
  grid-template-rows: 48px 1fr;
}

.topbar {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 16px;
  border-bottom: 1px solid #d8dee4;
  background: #ffffff;
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border-right: 1px solid #d8dee4;
  background: #ffffff;
}

.main-thread {
  padding: 20px;
  overflow: auto;
}

.context-panel {
  padding: 16px;
  border-left: 1px solid #d8dee4;
  background: #ffffff;
  overflow: auto;
}

.thread-section,
.panel,
.task-card {
  border: 1px solid #d8dee4;
  border-radius: 8px;
  background: #ffffff;
  padding: 16px;
}

.thread-section + .thread-section,
.panel + .panel {
  margin-top: 12px;
}

.goal-form {
  display: grid;
  gap: 8px;
}

.goal-form textarea {
  min-height: 96px;
  resize: vertical;
}

.goal-form button {
  justify-self: start;
}

.task-list {
  display: grid;
  gap: 8px;
}

.task-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.task-card h3 {
  margin: 0 0 4px;
  font-size: 14px;
}

.task-card p {
  margin: 0;
  color: #57606a;
  font-size: 13px;
}

.status-pill {
  white-space: nowrap;
  border-radius: 999px;
  background: #edf2f7;
  padding: 4px 8px;
  font-size: 12px;
  font-weight: 600;
}
```

Create `apps/desktop/src-tauri/README.md`:

```markdown
# Tauri Integration

Phase 0 runs the desktop shell as a Vite React application. Tauri 2 integration is deferred until the UI/API loop is useful. This directory reserves the future desktop-native boundary.
```

- [ ] **Step 5: Run UI tests and typecheck**

Run:

```bash
pnpm --filter @ai-scdc/desktop test
pnpm --filter @ai-scdc/desktop typecheck
```

Expected: PASS.

- [ ] **Step 6: Commit desktop shell**

```bash
git add apps/desktop
git commit -m "feat: add desktop console shell"
```

---

## Task 8: Architecture Documentation and Final Verification

**Files:**
- Create: `docs/architecture.md`
- Create: `README.md`

- [ ] **Step 1: Create architecture documentation**

Create `docs/architecture.md`:

````markdown
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
2. Model router and BYOK foundation with encrypted credential references and usage logging.
3. Local Runner for repository reads, git worktrees, diffs, and manual patch review.
4. Automated tests, reviewer loop, and debug loop.
5. Cloud sandbox workers, Git hosting integration, artifacts, and pull requests.
6. Commercial beta with users, organizations, subscriptions, credit wallet, usage ledger, rate limits, and billing provider abstraction.
````

Create `README.md` if missing:

````markdown
# AI Software Company Desktop Console

Phase 0 is a contract-first monorepo foundation for a desktop multi-agent software engineering console.

## Local Commands

```bash
pnpm install
python -m pip install -e "apps/api[test]" -e "apps/worker[test]" -e "services/llm-gateway[test]"
pnpm test
pnpm typecheck
pnpm dev:api
pnpm dev:desktop
```

See `docs/architecture.md` for architecture and phase boundaries.
````

- [ ] **Step 2: Run full verification**

Run:

```bash
pnpm install
python -m pip install -e "apps/api[test]" -e "apps/worker[test]" -e "services/llm-gateway[test]"
pnpm test
pnpm typecheck
```

Expected: all JavaScript tests, Python tests, and TypeScript checks pass.

- [ ] **Step 3: Run API smoke test**

Run in one terminal:

```bash
pnpm dev:api
```

Run in another terminal:

```bash
python - <<'PY'
import json
import urllib.request
print(json.loads(urllib.request.urlopen("http://127.0.0.1:8000/health").read()))
PY
```

Expected: `{'status': 'ok'}`.

- [ ] **Step 4: Run desktop smoke test**

Run:

```bash
pnpm dev:desktop
```

Expected: Vite prints a local URL. Open it and verify the page displays the top bar, sidebar, main thread, and right context panel.

- [ ] **Step 5: Commit docs and verification fixes**

```bash
git add README.md docs/architecture.md
git commit -m "docs: add phase 0 architecture"
```

---

## Final Review Checklist

Before marking the implementation complete:

- [ ] `git status --short` shows only intentional changes.
- [ ] `pnpm test` passes.
- [ ] `pnpm typecheck` passes.
- [ ] `pytest apps/api/tests apps/worker/tests services/llm-gateway/tests` passes.
- [ ] `docs/architecture.md` matches the Phase 0 boundary in `docs/superpowers/specs/2026-05-29-phase-0-monorepo-design.md`.
- [ ] No real model provider call exists.
- [ ] No real secret reading exists.
- [ ] No worker code modifies repository files.
- [ ] Tauri is documented as deferred.
- [ ] SQLite is used for tests; PostgreSQL/Redis are reserved through Docker Compose only.

After all tasks pass, use `superpowers:finishing-a-development-branch`.
