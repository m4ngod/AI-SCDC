import { afterEach, describe, expect, it, vi } from "vitest";
import { createHttpApiClient, fakeApiClient } from "../api/client";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    ...init
  });
}

describe("desktop API clients", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fake client creates the deterministic demo task", async () => {
    await expect(fakeApiClient.createTask("Ship the real goal")).resolves.toMatchObject({
      id: "task_demo_created",
      title: "Build task board",
      status: "CREATED",
      role_required: "frontend",
      assigned_agent: "Frontend Engineer",
      updated_at: "2026-05-29T00:00:00Z"
    });
  });

  it("fake client lists the deterministic demo board", async () => {
    await expect(fakeApiClient.listTasks()).resolves.toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "task_board_ui",
          title: "Implement task board UI",
          status: "PATCH_READY"
        })
      ])
    );
  });

  it("fake client runs tests for a demo patch", async () => {
    await expect(fakeApiClient.runPatchTests("patch_demo")).resolves.toMatchObject({
      task: {
        id: "task_board_ui",
        status: "REVIEWING"
      },
      patch_artifact: {
        id: "patch_demo",
        test_result: "passed"
      },
      test_run: {
        patch_artifact_id: "patch_demo",
        status: "passed"
      },
      debug_attempt: null
    });
  });

  it("fake client reviews a demo patch", async () => {
    await expect(fakeApiClient.reviewPatch("patch_demo")).resolves.toMatchObject({
      task: {
        id: "task_board_ui",
        status: "APPROVED"
      },
      review: {
        patch_artifact_id: "patch_demo",
        verdict: "approved"
      },
      debug_attempt: null
    });
  });

  it("fake client creates deterministic planner drafts", async () => {
    const plannerRun = await fakeApiClient.createPlannerRun("Build model route settings");

    expect(plannerRun).toMatchObject({
      id: "planner_run_demo",
      status: "DRAFTED",
      planner_kind: "fake",
      draft_count: 2
    });
    expect(plannerRun.drafts.map((draft) => draft.role_required)).toEqual([
      "frontend",
      "backend"
    ]);
    expect(plannerRun.drafts[0].objective).toContain("Build model route settings");
  });

  it("fake client approves planner drafts into task cards", async () => {
    const decision = await fakeApiClient.approvePlannerRun("planner_run_demo");

    expect(decision.status).toBe("APPROVED");
    expect(decision.created_tasks).toEqual([
      expect.objectContaining({
        id: "task_planner_frontend",
        title: "Design desktop flow for planner approval",
        role_required: "frontend"
      }),
      expect.objectContaining({
        id: "task_planner_backend",
        title: "Implement planner approval API",
        role_required: "backend"
      })
    ]);
  });

  it("HTTP client lists mapped tasks for the resolved project", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse([{ id: "project_demo" }]))
      .mockResolvedValueOnce(
        jsonResponse([
          {
            id: "task_api_persisted",
            title: "Persisted API task",
            status: "REVIEWING",
            role_required: "backend",
            created_at: "2026-05-29T01:00:00Z"
          }
        ])
      );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({ baseUrl: "http://127.0.0.1:8000/" });
    const tasks = await client.listTasks();

    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:8000/projects");
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8000/projects/project_demo/tasks"
    );
    expect(tasks).toEqual([
      {
        id: "task_api_persisted",
        title: "Persisted API task",
        status: "REVIEWING",
        role_required: "backend",
        assigned_agent: "Backend Engineer",
        updated_at: "2026-05-29T01:00:00Z"
      }
    ]);
  });

  it("HTTP client formats FastAPI JSON detail errors", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse(
        {
          detail: {
            message: "Project not found"
          }
        },
        { status: 404, statusText: "Not Found" }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_missing"
    });

    await expect(client.listTasks()).rejects.toThrow(
      "GET /projects/project_missing/tasks failed with 404 Not Found: Project not found"
    );
  });

  it("HTTP client creates a demo project when none exists and posts a mapped task", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: "project_demo",
            name: "Demo Project",
            description: "Phase 0 desktop integration project"
          },
          { status: 201 }
        )
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: "task_api_1",
            title: "Connect the desktop",
            status: "CREATED",
            role_required: "frontend",
            created_at: "2026-05-29T01:00:00Z",
            updated_at: "2026-05-29T01:05:00Z"
          },
          { status: 201 }
        )
      );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({ baseUrl: "http://127.0.0.1:8000/" });
    const task = await client.createTask("Connect the desktop");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:8000/projects");
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8000/projects",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          name: "Demo Project",
          description: "Phase 0 desktop integration project"
        })
      })
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://127.0.0.1:8000/projects/project_demo/tasks",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          title: "Connect the desktop",
          description: "Created from desktop shell.",
          role_required: "frontend",
          risk_level: "medium",
          acceptance_criteria: ["Task is visible in the desktop context panel."]
        })
      })
    );
    expect(task).toEqual({
      id: "task_api_1",
      title: "Connect the desktop",
      status: "CREATED",
      role_required: "frontend",
      assigned_agent: "Frontend Engineer",
      updated_at: "2026-05-29T01:05:00Z"
    });
  });

  it("HTTP client creates a planner run for the resolved project", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse([{ id: "project_demo" }]))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: "planner_run_api",
            project_id: "project_demo",
            goal: "Build model route settings",
            status: "DRAFTED",
            planner_kind: "fake",
            draft_count: 1,
            drafts: [
              {
                id: "planner_draft_api",
                sequence: 1,
                title: "Design model route settings UI",
                role_required: "frontend",
                objective: "Create the desktop UI.",
                acceptance_criteria: ["Draft is visible"],
                allowed_paths: ["apps/desktop/**"],
                required_tests: ["App renders planner draft preview"],
                risk_level: "medium"
              }
            ]
          },
          { status: 201 }
        )
      );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({ baseUrl: "http://127.0.0.1:8000/" });
    const plannerRun = await client.createPlannerRun("Build model route settings");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:8000/projects");
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8000/projects/project_demo/planner-runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ goal: "Build model route settings" })
      })
    );
    expect(plannerRun.drafts[0].title).toBe("Design model route settings UI");
  });

  it("HTTP client approves planner runs and maps created tasks", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({
        planner_run_id: "planner_run_api",
        approval_id: "approval_api",
        status: "APPROVED",
        created_tasks: [
          {
            id: "task_api",
            title: "Design model route settings UI",
            status: "CREATED",
            role_required: "frontend",
            created_at: "2026-05-29T01:00:00Z"
          }
        ]
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_demo"
    });
    const decision = await client.approvePlannerRun("planner_run_api");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/planner-runs/planner_run_api/approve",
      expect.objectContaining({ method: "POST" })
    );
    expect(decision.created_tasks[0]).toMatchObject({
      id: "task_api",
      assigned_agent: "Frontend Engineer"
    });
  });

  it("HTTP client rejects planner runs with a reason and maps empty created tasks", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({
        planner_run_id: "planner_run_api",
        approval_id: "approval_api",
        status: "REJECTED",
        created_tasks: []
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_demo"
    });
    const decision = await client.rejectPlannerRun("planner_run_api", "Needs smaller tasks");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/planner-runs/planner_run_api/reject",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ reason: "Needs smaller tasks" })
      })
    );
    expect(decision).toMatchObject({
      planner_run_id: "planner_run_api",
      status: "REJECTED",
      created_tasks: []
    });
  });

  it("HTTP client starts a local run with the first active repository", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse([
          {
            id: "repo_api",
            name: "API Repo",
            local_path: "T:/repo",
            default_branch: "main",
            status: "active"
          }
        ])
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: "local_run_api",
            task_id: "task_api",
            repo_id: "repo_api",
            status: "patch_ready",
            base_branch: "main",
            worktree_path: "T:/repo/.worktrees/task_api-local_run_api",
            patch_artifact_id: "patch_api",
            failure_reason: null
          },
          { status: 201 }
        )
      )
      .mockResolvedValueOnce(
        jsonResponse({
          id: "patch_api",
          task_id: "task_api",
          local_run_id: "local_run_api",
          summary: "Prepared local runner patch.",
          files_changed: ["README.md"],
          tests_run: ["pytest apps/worker/tests/test_local_runner.py -v"],
          test_result: "not_run",
          diff_text: "diff --git a/README.md b/README.md"
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_demo"
    });
    const result = await client.startLocalRun("task_api");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:8000/projects/project_demo/repositories"
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8000/tasks/task_api/local-runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ repo_id: "repo_api" })
      })
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://127.0.0.1:8000/patch-artifacts/patch_api"
    );
    expect(result.patch_artifact?.files_changed).toEqual(["README.md"]);
  });

  it("HTTP client posts patch test runs and maps result cards", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse(
        {
          task: {
            id: "task_api",
            title: "Review persisted patch",
            status: "REVIEWING",
            role_required: "backend",
            updated_at: "2026-05-29T02:00:00Z"
          },
          patch_artifact: {
            id: "patch_api",
            workspace_id: "workspace_api",
            project_id: "project_demo",
            task_id: "task_api",
            local_run_id: "local_run_api",
            summary: "Prepared local runner patch.",
            files_changed: ["apps/api/routes.py"],
            tests_run: ["pytest apps/api/tests/test_test_review_debug_api.py -v"],
            test_result: "passed",
            risks: [],
            diff_text: "diff --git a/apps/api/routes.py b/apps/api/routes.py",
            created_at: "2026-05-29T01:55:00Z"
          },
          test_run: {
            id: "test_run_api",
            workspace_id: "workspace_api",
            project_id: "project_demo",
            task_id: "task_api",
            local_run_id: "local_run_api",
            patch_artifact_id: "patch_api",
            status: "passed",
            commands: ["pytest apps/api/tests/test_test_review_debug_api.py -v"],
            command_results: [
              {
                command: "pytest apps/api/tests/test_test_review_debug_api.py -v",
                exit_code: 0,
                stdout: "1 passed",
                stderr: "",
                duration_ms: 1200
              }
            ],
            failure_reason: null,
            started_at: "2026-05-29T02:00:00Z",
            completed_at: "2026-05-29T02:00:02Z",
            created_at: "2026-05-29T02:00:00Z"
          },
          debug_attempt: null
        },
        { status: 201 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_demo"
    });
    const result = await client.runPatchTests("patch_api");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/patch-artifacts/patch_api/test-runs",
      expect.objectContaining({ method: "POST" })
    );
    expect(result).toMatchObject({
      task: {
        id: "task_api",
        status: "REVIEWING",
        assigned_agent: "Backend Engineer"
      },
      patch_artifact: {
        id: "patch_api",
        test_result: "passed"
      },
      test_run: {
        id: "test_run_api",
        status: "passed",
        command_results: [
          {
            command: "pytest apps/api/tests/test_test_review_debug_api.py -v",
            exit_code: 0
          }
        ]
      },
      debug_attempt: null
    });
  });

  it("HTTP client posts patch reviews and maps debug attempts", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse(
        {
          task: {
            id: "task_api",
            title: "Review persisted patch",
            status: "FIX_REQUESTED",
            role_required: "backend",
            updated_at: "2026-05-29T02:05:00Z"
          },
          patch_artifact: {
            id: "patch_api",
            workspace_id: "workspace_api",
            project_id: "project_demo",
            task_id: "task_api",
            local_run_id: "local_run_api",
            summary: "Prepared local runner patch.",
            files_changed: ["apps/api/routes.py"],
            tests_run: ["pytest apps/api/tests/test_test_review_debug_api.py -v"],
            test_result: "passed",
            risks: [],
            diff_text: "",
            created_at: "2026-05-29T01:55:00Z"
          },
          review: {
            id: "review_api",
            workspace_id: "workspace_api",
            project_id: "project_demo",
            task_id: "task_api",
            local_run_id: "local_run_api",
            patch_artifact_id: "patch_api",
            test_run_id: "test_run_api",
            reviewer_kind: "deterministic",
            verdict: "changes_requested",
            issues: [{ code: "missing_diff", message: "Diff is missing" }],
            required_changes: ["Include a patch diff before approval."],
            created_at: "2026-05-29T02:05:00Z"
          },
          debug_attempt: {
            id: "debug_api",
            workspace_id: "workspace_api",
            project_id: "project_demo",
            task_id: "task_api",
            patch_artifact_id: "patch_api",
            review_id: "review_api",
            test_run_id: "test_run_api",
            status: "requested",
            root_cause: "deterministic review found missing diff",
            fix_summary: "Attach the generated diff to the patch artifact.",
            created_at: "2026-05-29T02:05:01Z"
          }
        },
        { status: 201 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_demo"
    });
    const result = await client.reviewPatch("patch_api");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/patch-artifacts/patch_api/reviews",
      expect.objectContaining({ method: "POST" })
    );
    expect(result).toMatchObject({
      task: {
        id: "task_api",
        status: "FIX_REQUESTED",
        assigned_agent: "Backend Engineer"
      },
      review: {
        id: "review_api",
        verdict: "changes_requested",
        required_changes: ["Include a patch diff before approval."]
      },
      debug_attempt: {
        id: "debug_api",
        root_cause: "deterministic review found missing diff",
        fix_summary: "Attach the generated diff to the patch artifact."
      }
    });
  });
});
