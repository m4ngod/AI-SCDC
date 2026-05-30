# Phase 4 Local Runner Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first local runner path from approved task to reviewable patch artifact.

**Architecture:** Add repository registration and local-run records to the API, preserve planner execution constraints on approved tasks, invoke a worker-side local runner in-process for the development vertical slice, create git worktrees under `.worktrees`, capture diffs as patch artifacts, and surface patch-ready state in the desktop without auto-merging.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, Pydantic v2, pytest, subprocess git commands, Vite React, Vitest, pnpm workspace verification.

---

## File Structure

- Modify: `apps/api/app/ai_company_api/models/entities.py`
  - Add `Repository`, `LocalTaskRun`, and `PatchArtifact` tables.
  - Add `allowed_paths` and `required_tests` to `Task`.
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
  - Add repository, local run, and patch artifact schemas.
  - Add `allowed_paths` and `required_tests` to task create/read schemas.
- Modify: `apps/api/app/ai_company_api/services/repository.py`
  - Preserve planner draft execution constraints on approval.
  - Add repository CRUD, local run creation, artifact reads, and task event wiring.
- Modify: `apps/api/app/ai_company_api/api/routes.py`
  - Add repository and local-run endpoints.
- Modify: `apps/api/app/ai_company_api/db/session.py`
  - Extend SQLite dev-schema migration helpers for new tables/columns if needed.
- Create: `apps/api/app/ai_company_api/services/local_runner.py`
  - API orchestration service that calls the worker local runner.
- Create: `apps/api/tests/test_local_runner_api.py`
  - API-level tests for repository registration, local run execution, task transitions, artifacts, and ownership checks.
- Modify: `apps/api/tests/test_planner_endpoints.py`
  - Assert approval copies `allowed_paths` and `required_tests`.
- Create: `apps/worker/app/ai_company_worker/local_runner.py`
  - Worker-side git worktree runner.
- Create: `apps/worker/tests/test_local_runner.py`
  - Deterministic git fixture tests.
- Modify: `apps/worker/tests/test_simulator.py`
  - Keep simulator behavior separate from real local runner behavior.
- Modify: `apps/desktop/src/api/client.ts`
  - Add repository/local-run client calls and response types.
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
  - Add run-local action and patch-ready metadata.
- Modify: `apps/desktop/src/test/*.test.tsx`
  - Cover run-local UI state.
- Modify: `README.md`
  - Add local runner manual smoke instructions after implementation.
- Modify: `docs/architecture.md`
  - Add Phase 4 boundary and roadmap update after implementation.

## Implementation Notes

- Do not run model-generated shell commands.
- Do not edit the source checkout.
- Do not auto-commit, push, merge, or open PRs.
- Do not add cloud sandbox behavior.
- Do not implement reviewer/debugger loops in Phase 4.
- Keep the first patch strategy deterministic and testable.
- Use subprocess argument arrays for git commands.
- Before any recursive worktree cleanup, verify the resolved path is inside the intended `.worktrees` root.
- Tests must create temporary git repositories and must not depend on external network calls.

---

## Task 1: Preserve Task Execution Constraints

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/services/repository.py`
- Modify: `apps/api/tests/test_planner_endpoints.py`

- [ ] **Step 1: Add failing approval-copy tests**

Update planner approval tests to assert that tasks created from planner drafts include:

```python
assert task["allowed_paths"] == draft["allowed_paths"]
assert task["required_tests"] == draft["required_tests"]
```

Also update task create/read schema tests to prove API-created tasks can carry these fields.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
pytest apps/api/tests/test_planner_endpoints.py apps/api/tests/test_api_endpoints.py -v
```

Expected: FAIL because `Task` does not yet expose or persist `allowed_paths` and `required_tests`.

- [ ] **Step 3: Add task fields**

Add JSON-backed fields to `Task`:

```python
allowed_paths: list[str] = Field(default_factory=list, sa_column=Column(JSON))
required_tests: list[str] = Field(default_factory=list, sa_column=Column(JSON))
```

Add matching fields to `TaskCreate` and `TaskRead`.

- [ ] **Step 4: Copy draft constraints during approval**

In `approve_planner_run()`, copy:

```python
allowed_paths=draft.allowed_paths
required_tests=draft.required_tests
```

When `create_task()` receives direct task creation requests, persist the provided values.

- [ ] **Step 5: Add dev SQLite migration coverage**

If `init_db()` has existing add-column compatibility helpers, extend them for the two new columns and add a regression test for an existing SQLite database.

- [ ] **Step 6: Run affected API tests**

Run:

```bash
pytest apps/api/tests/test_api_endpoints.py apps/api/tests/test_planner_endpoints.py -v
```

Expected: PASS.

---

## Task 2: Worker Local Runner Git Core

**Files:**
- Create: `apps/worker/app/ai_company_worker/local_runner.py`
- Create: `apps/worker/tests/test_local_runner.py`

- [ ] **Step 1: Add failing worker tests**

Cover:

- non-git paths are rejected,
- source checkout remains unchanged,
- worktree is created below `.worktrees`,
- diff text is captured,
- changed files are reported,
- changes outside `allowed_paths` are rejected.

Use temporary git repositories in tests:

```bash
pytest apps/worker/tests/test_local_runner.py -v
```

Expected: FAIL because the local runner does not exist.

- [ ] **Step 2: Define worker models**

Add Pydantic models:

- `LocalRunnerRequest`
- `LocalRunnerResult`
- `LocalRunnerError`

The result should include:

- `status`
- `summary`
- `files_changed`
- `tests_run`
- `test_result`
- `risks`
- `diff_text`
- `worktree_path`
- `base_sha`
- `head_sha`

- [ ] **Step 3: Add safe git command wrapper**

Implement a helper that runs git commands with:

- argument lists,
- explicit `cwd`,
- timeout,
- captured stdout/stderr,
- typed failure.

Do not pass generated strings to the shell.

- [ ] **Step 4: Implement worktree creation**

Create worktrees under:

```text
<repo>/.worktrees/<task_id>-<run_id>
```

Validate the resolved path is inside `<repo>/.worktrees`.

- [ ] **Step 5: Implement deterministic patch strategy**

For Phase 4, create a bounded patch:

- prefer the first allowed explicit text path that exists,
- otherwise create a note file under the first allowed directory/glob that can safely contain one,
- write a short task-local runner note,
- capture `git diff --no-ext-diff`.

Reject if no safe allowed path exists.

- [ ] **Step 6: Run worker tests**

Run:

```bash
pytest apps/worker/tests/test_local_runner.py apps/worker/tests/test_simulator.py -v
```

Expected: PASS.

---

## Task 3: Repository Registry API

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/services/repository.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Create/modify: `apps/api/tests/test_local_runner_api.py`

- [ ] **Step 1: Add failing repository API tests**

Cover:

- create repository for a project,
- list repositories for a project,
- reject missing project,
- reject non-git local path,
- reject cross-project repository usage later.

- [ ] **Step 2: Add repository model and schemas**

Add:

- `RepositoryCreate`
- `RepositoryRead`

Use local path validation in the service layer, not only schema validation.

- [ ] **Step 3: Add endpoints**

Add:

```text
POST /projects/{project_id}/repositories
GET /projects/{project_id}/repositories
GET /repositories/{repo_id}
```

- [ ] **Step 4: Run repository API tests**

Run:

```bash
pytest apps/api/tests/test_local_runner_api.py -v
```

Expected: repository tests PASS.

---

## Task 4: Local Run API and Patch Artifacts

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Create: `apps/api/app/ai_company_api/services/local_runner.py`
- Modify: `apps/api/app/ai_company_api/services/repository.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Modify: `apps/api/tests/test_local_runner_api.py`

- [ ] **Step 1: Add failing local run tests**

Cover:

- starting a local run for an eligible task,
- creating `LocalTaskRun`,
- creating `PatchArtifact`,
- transitioning task to `PATCH_READY`,
- writing task events,
- rejecting cross-project repo/task combinations,
- recording failure reason when worker fails.

- [ ] **Step 2: Add local run and patch artifact models**

Add tables:

- `LocalTaskRun`
- `PatchArtifact`

Keep the API read models secret-free and path-conscious. Full local paths can be returned in dev mode if they are already user-provided repository paths, but no credential values should ever be included.

- [ ] **Step 3: Add local-run orchestration service**

Create `services/local_runner.py` in API:

- validate task exists,
- validate repository belongs to task project,
- create local run record,
- transition task to `ASSIGNED`,
- transition task to `IN_PROGRESS`,
- call worker local runner,
- persist artifact,
- transition task to `PATCH_READY`,
- record events throughout.

On worker failure, persist failed run and event; do not invent a patch artifact.

- [ ] **Step 4: Add endpoints**

Add:

```text
POST /tasks/{task_id}/local-runs
GET /tasks/{task_id}/local-runs
GET /local-runs/{local_run_id}
GET /patch-artifacts/{patch_artifact_id}
```

- [ ] **Step 5: Run API local runner tests**

Run:

```bash
pytest apps/api/tests/test_local_runner_api.py apps/api/tests/test_task_state.py -v
```

Expected: PASS.

---

## Task 5: Desktop Local Run Controls

**Files:**
- Modify: `apps/desktop/src/api/client.ts`
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
- Modify: `apps/desktop/src/fixtures/demoData.ts`
- Modify: `apps/desktop/src/test/client.test.ts`
- Modify: `apps/desktop/src/test/App.test.tsx`

- [ ] **Step 1: Add failing desktop client tests**

Cover client methods for:

- repository creation/listing if exposed in the UI,
- starting a local run,
- reading patch artifact metadata.

- [ ] **Step 2: Add task board UI tests**

Cover:

- eligible tasks show a local-run action in API-backed mode,
- starting a run updates visible task status/patch metadata,
- failures show inline error text.

- [ ] **Step 3: Implement client calls**

Add typed functions in `client.ts` for local-run endpoints and patch artifacts.

- [ ] **Step 4: Implement UI**

Keep the task board dense and operational:

- icon button or compact action for run-local,
- patch status label,
- files changed list,
- test result badge,
- no marketing copy or large explanatory panels.

- [ ] **Step 5: Run desktop tests**

Run:

```bash
pnpm --filter @ai-scdc/desktop test
pnpm --filter @ai-scdc/desktop typecheck
```

Expected: PASS.

---

## Task 6: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Add local runner manual smoke test docs**

Document:

- registering a local repo,
- approving planner tasks,
- starting a local run,
- fetching patch artifact,
- confirming no source checkout files changed.

- [ ] **Step 2: Update architecture**

After implementation, add `## Phase 4 Boundary` and move Local Runner from Future to Completed.

- [ ] **Step 3: Run full workspace verification**

Run:

```bash
pnpm test
pnpm typecheck
pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v
git diff --check
```

Expected: all commands PASS.

---

## Final Review Checklist

- [ ] Approved tasks persist `allowed_paths`.
- [ ] Approved tasks persist `required_tests`.
- [ ] Repository registration rejects non-git paths.
- [ ] Local runner creates worktrees under `.worktrees`.
- [ ] Local runner does not modify the source checkout.
- [ ] Local runner captures a diff artifact.
- [ ] Patches are constrained to task `allowed_paths`.
- [ ] API records local run lifecycle events.
- [ ] Task reaches `PATCH_READY` after a successful local run.
- [ ] Worker failures are auditable and do not create fake patch artifacts.
- [ ] Desktop can start a local run and show patch-ready metadata.
- [ ] No auto-commit, push, merge, or PR creation was added.
- [ ] No cloud sandbox behavior was added.
- [ ] `pnpm test` passes.
- [ ] `pnpm typecheck` passes.
- [ ] `pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v` passes.
- [ ] `git diff --check` passes.
