import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { ConsoleApiClient, PlannerRunDraft, TaskCard } from "../api/client";

function plannerRunFixture(goal = "Build model route settings"): PlannerRunDraft {
  return {
    id: "planner_run_test",
    project_id: "project_demo",
    conversation_id: null,
    goal,
    status: "DRAFTED",
    planner_kind: "fake",
    draft_count: 2,
    drafts: [
      {
        id: "planner_draft_frontend",
        sequence: 1,
        title: "Design desktop flow",
        role_required: "frontend",
        objective: `Create UI for ${goal}.`,
        acceptance_criteria: ["Draft is visible"],
        allowed_paths: ["apps/desktop/**"],
        required_tests: ["App renders planner draft preview"],
        risk_level: "medium"
      },
      {
        id: "planner_draft_backend",
        sequence: 2,
        title: "Implement planner API",
        role_required: "backend",
        objective: `Persist data for ${goal}.`,
        acceptance_criteria: ["Approval creates tasks"],
        allowed_paths: ["apps/api/**"],
        required_tests: ["Planner approval endpoint creates tasks"],
        risk_level: "medium"
      }
    ]
  };
}

function taskCardFixture(title = "Design desktop flow"): TaskCard {
  return {
    id: "task_created_from_planner",
    title,
    status: "CREATED",
    role_required: "frontend",
    assigned_agent: "Frontend Engineer",
    updated_at: "2026-05-29T00:00:00Z"
  };
}

function patchReadyTaskFixture(): TaskCard {
  return {
    ...taskCardFixture("Review desktop patch"),
    id: "task_patch_ready",
    status: "PATCH_READY",
    patch_artifact: {
      id: "patch_test",
      task_id: "task_patch_ready",
      local_run_id: "local_run_test",
      summary: "Prepared local runner patch.",
      files_changed: ["apps/desktop/src/components/TaskBoard.tsx"],
      tests_run: ["pnpm --filter @ai-scdc/desktop test"],
      test_result: "not_run"
    }
  };
}

function reviewingTaskFixture(): TaskCard {
  return {
    ...patchReadyTaskFixture(),
    status: "REVIEWING",
    patch_artifact: {
      ...patchReadyTaskFixture().patch_artifact!,
      test_result: "passed"
    },
    test_run: {
      id: "test_run_test",
      workspace_id: "workspace_test",
      project_id: "project_demo",
      task_id: "task_patch_ready",
      local_run_id: "local_run_test",
      patch_artifact_id: "patch_test",
      status: "passed",
      commands: ["pnpm --filter @ai-scdc/desktop test"],
      command_results: [
        {
          command: "pnpm --filter @ai-scdc/desktop test",
          exit_code: 0,
          stdout: "passed",
          stderr: "",
          duration_ms: 1000
        }
      ],
      failure_reason: null,
      started_at: "2026-05-29T00:01:00Z",
      completed_at: "2026-05-29T00:02:00Z",
      created_at: "2026-05-29T00:01:00Z"
    }
  };
}

function createMockApiClient(overrides: Partial<ConsoleApiClient> = {}): ConsoleApiClient {
  return {
    listTasks: vi.fn().mockResolvedValue([]),
    createTask: vi.fn(),
    createPlannerRun: vi.fn().mockResolvedValue(plannerRunFixture()),
    approvePlannerRun: vi.fn().mockResolvedValue({
      planner_run_id: "planner_run_test",
      approval_id: "approval_test",
      status: "APPROVED",
      created_tasks: []
    }),
    rejectPlannerRun: vi.fn().mockResolvedValue({
      planner_run_id: "planner_run_test",
      approval_id: "approval_test",
      status: "REJECTED",
      created_tasks: []
    }),
    startLocalRun: vi.fn().mockResolvedValue({
      local_run: {
        id: "local_run_test",
        task_id: "task_created_from_planner",
        repo_id: "repo_test",
        status: "patch_ready",
        base_branch: "main",
        worktree_path: ".worktrees/task_created_from_planner",
        patch_artifact_id: "patch_test",
        failure_reason: null
      },
      patch_artifact: {
        id: "patch_test",
        task_id: "task_created_from_planner",
        local_run_id: "local_run_test",
        summary: "Prepared local runner patch.",
        files_changed: ["README.md"],
        tests_run: [],
        test_result: "not_run"
      }
    }),
    runPatchTests: vi.fn().mockResolvedValue({
      task: {
        ...reviewingTaskFixture(),
        test_run: undefined
      },
      patch_artifact: reviewingTaskFixture().patch_artifact!,
      test_run: reviewingTaskFixture().test_run!,
      debug_attempt: null
    }),
    reviewPatch: vi.fn().mockResolvedValue({
      task: {
        ...reviewingTaskFixture(),
        status: "APPROVED",
        test_run: undefined
      },
      patch_artifact: reviewingTaskFixture().patch_artifact!,
      review: {
        id: "review_test",
        workspace_id: "workspace_test",
        project_id: "project_demo",
        task_id: "task_patch_ready",
        local_run_id: "local_run_test",
        patch_artifact_id: "patch_test",
        test_run_id: "test_run_test",
        reviewer_kind: "deterministic",
        verdict: "approved",
        issues: [],
        required_changes: [],
        created_at: "2026-05-29T00:03:00Z"
      },
      debug_attempt: null
    }),
    ...overrides
  };
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

describe("App", () => {
  it("renders sidebar, main thread, and right context panel", () => {
    render(<App />);

    const topbar = screen.getByRole("banner");
    for (const item of [
      "AI Company",
      "Demo Workspace",
      "Demo Project",
      "main",
      "Local Runner: Mock",
      "Cost: $0.00",
      "Settings"
    ]) {
      expect(topbar).toHaveTextContent(item);
    }
    const primaryNav = screen.getByRole("navigation", { name: "Primary" });
    for (const item of [
      "Workspace",
      "Projects",
      "Conversations",
      "Agents",
      "Approvals",
      "Settings"
    ]) {
      expect(within(primaryNav).getByText(item)).toBeInTheDocument();
    }
    expect(within(primaryNav).queryByText("Runs")).not.toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveTextContent("Project command thread");
    expect(screen.getByLabelText("Task context panel")).toHaveTextContent("Agent status");
  });

  it("renders task title, status, and agent", async () => {
    render(<App />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    expect(await within(board).findByText("Implement task board UI")).toBeInTheDocument();
    expect(await within(board).findByText("PATCH_READY")).toBeInTheDocument();
    expect(await within(board).findByText("Frontend Engineer")).toBeInTheDocument();
    expect(within(screen.getByRole("main")).queryByLabelText("Task board")).not.toBeInTheDocument();
  });

  it("loads the initial task board from the API client", async () => {
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([
        {
          id: "task_api_persisted",
          title: "Persisted API task",
          status: "REVIEWING",
          role_required: "backend",
          assigned_agent: "Backend Engineer",
          updated_at: "2026-05-29T01:00:00Z"
        }
      ])
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    expect(await within(contextPanel).findByText("Persisted API task")).toBeInTheDocument();
    expect(within(contextPanel).queryByText("Implement task board UI")).not.toBeInTheDocument();
    expect(apiClient.listTasks).toHaveBeenCalledOnce();
  });

  it("shows initial task loading errors in the context panel", async () => {
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockRejectedValue(new Error("API unavailable"))
    });

    render(<App apiClient={apiClient} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("API unavailable");
  });

  it("submitting a goal renders planner draft preview", async () => {
    const user = userEvent.setup();
    const createPlannerRun = vi
      .fn<ConsoleApiClient["createPlannerRun"]>()
      .mockImplementation(async (goal) => plannerRunFixture(goal));
    const apiClient = createMockApiClient({ createPlannerRun });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("Goal"), "Build model route settings");
    await user.click(screen.getByRole("button", { name: "Plan tasks" }));

    expect(createPlannerRun).toHaveBeenCalledWith("Build model route settings");
    expect(await screen.findAllByText("Planner draft")).not.toHaveLength(0);
    expect(screen.getByText("Design desktop flow")).toBeInTheDocument();
    expect(screen.getByText("Implement planner API")).toBeInTheDocument();
  });

  it("approving planner drafts adds created tasks to the task board", async () => {
    const user = userEvent.setup();
    const approvePlannerRun = vi.fn<ConsoleApiClient["approvePlannerRun"]>().mockResolvedValue({
      planner_run_id: "planner_run_test",
      approval_id: "approval_test",
      status: "APPROVED",
      created_tasks: [taskCardFixture("Design desktop flow")]
    });
    const apiClient = createMockApiClient({
      createPlannerRun: vi.fn().mockResolvedValue(plannerRunFixture()),
      approvePlannerRun
    });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("Goal"), "Build model route settings");
    await user.click(screen.getByRole("button", { name: "Plan tasks" }));
    await user.click(await screen.findByRole("button", { name: "Approve drafts" }));

    expect(approvePlannerRun).toHaveBeenCalledWith("planner_run_test");
    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    expect(await within(board).findByText("Design desktop flow")).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
  });

  it("disables goal planning while planner approval is pending", async () => {
    const user = userEvent.setup();
    const approval = createDeferred<Awaited<ReturnType<ConsoleApiClient["approvePlannerRun"]>>>();
    const createPlannerRun = vi
      .fn<ConsoleApiClient["createPlannerRun"]>()
      .mockResolvedValue(plannerRunFixture("Build model route settings"));
    const approvePlannerRun = vi
      .fn<ConsoleApiClient["approvePlannerRun"]>()
      .mockReturnValue(approval.promise);
    const apiClient = createMockApiClient({
      createPlannerRun,
      approvePlannerRun
    });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("Goal"), "Build model route settings");
    await user.click(screen.getByRole("button", { name: "Plan tasks" }));
    await user.click(await screen.findByRole("button", { name: "Approve drafts" }));

    const planButton = screen.getByRole("button", { name: "Plan tasks" });
    await waitFor(() => expect(planButton).toBeDisabled());
    await user.type(screen.getByLabelText("Goal"), "Build second goal");
    await user.click(planButton);

    expect(createPlannerRun).toHaveBeenCalledOnce();

    approval.resolve({
      planner_run_id: "planner_run_test",
      approval_id: "approval_test",
      status: "APPROVED",
      created_tasks: [taskCardFixture("Design desktop flow")]
    });
    expect(await screen.findByText("Approved")).toBeInTheDocument();
  });

  it("rejecting planner drafts does not add tasks", async () => {
    const user = userEvent.setup();
    const rejectPlannerRun = vi.fn<ConsoleApiClient["rejectPlannerRun"]>().mockResolvedValue({
      planner_run_id: "planner_run_test",
      approval_id: "approval_test",
      status: "REJECTED",
      created_tasks: []
    });
    const apiClient = createMockApiClient({
      createPlannerRun: vi.fn().mockResolvedValue(plannerRunFixture()),
      rejectPlannerRun
    });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("Goal"), "Build model route settings");
    await user.click(screen.getByRole("button", { name: "Plan tasks" }));
    await user.click(await screen.findByRole("button", { name: "Reject drafts" }));

    expect(rejectPlannerRun).toHaveBeenCalledWith(
      "planner_run_test",
      "Rejected from desktop shell."
    );
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    expect(within(board).queryByText("Design desktop flow")).not.toBeInTheDocument();
  });

  it("shows planner approval errors inline", async () => {
    const user = userEvent.setup();
    const approvePlannerRun = vi
      .fn<ConsoleApiClient["approvePlannerRun"]>()
      .mockRejectedValue(new Error("Planner run has already been decided"));
    const apiClient = createMockApiClient({
      createPlannerRun: vi.fn().mockResolvedValue(plannerRunFixture()),
      approvePlannerRun
    });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("Goal"), "Build model route settings");
    await user.click(screen.getByRole("button", { name: "Plan tasks" }));
    await user.click(await screen.findByRole("button", { name: "Approve drafts" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Planner run has already been decided"
    );
  });

  it("shows planner creation errors inline and clears them after retry", async () => {
    const user = userEvent.setup();
    const createPlannerRun = vi
      .fn<ConsoleApiClient["createPlannerRun"]>()
      .mockRejectedValueOnce(new Error("API unavailable"))
      .mockImplementationOnce(async (goal) => plannerRunFixture(goal));
    const apiClient = createMockApiClient({ createPlannerRun });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("Goal"), "Build while offline");
    await user.click(screen.getByRole("button", { name: "Plan tasks" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("API unavailable");

    await user.clear(screen.getByLabelText("Goal"));
    await user.type(screen.getByLabelText("Goal"), "Build after recovery");
    await user.click(screen.getByRole("button", { name: "Plan tasks" }));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await screen.findAllByText("Planner draft")).not.toHaveLength(0);
    expect(screen.getByText("Design desktop flow")).toBeInTheDocument();
  });

  it("starts a local run from the task board and renders patch metadata", async () => {
    const user = userEvent.setup();
    const startLocalRun = vi.fn<ConsoleApiClient["startLocalRun"]>().mockResolvedValue({
      local_run: {
        id: "local_run_test",
        task_id: "task_created_from_planner",
        repo_id: "repo_test",
        status: "patch_ready",
        base_branch: "main",
        worktree_path: ".worktrees/task_created_from_planner",
        patch_artifact_id: "patch_test",
        failure_reason: null
      },
      patch_artifact: {
        id: "patch_test",
        task_id: "task_created_from_planner",
        local_run_id: "local_run_test",
        summary: "Prepared local runner patch.",
        files_changed: ["README.md"],
        tests_run: ["pytest apps/worker/tests/test_local_runner.py -v"],
        test_result: "not_run"
      }
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([taskCardFixture("Design desktop flow")]),
      startLocalRun
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run local" }));

    expect(startLocalRun).toHaveBeenCalledWith("task_created_from_planner");
    expect(await within(board).findByText("PATCH_READY")).toBeInTheDocument();
    expect(within(board).getByText("README.md")).toBeInTheDocument();
    expect(within(board).getByText("not_run")).toBeInTheDocument();
  });

  it("shows local run errors inline on the task card", async () => {
    const user = userEvent.setup();
    const startLocalRun = vi
      .fn<ConsoleApiClient["startLocalRun"]>()
      .mockRejectedValue(new Error("No repository registered for project"));
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([taskCardFixture("Design desktop flow")]),
      startLocalRun
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run local" }));

    expect(await within(board).findByRole("alert")).toHaveTextContent(
      "No repository registered for project"
    );
  });

  it("runs patch tests from the task board and renders the passed test run", async () => {
    const user = userEvent.setup();
    const testedTask = reviewingTaskFixture();
    const runPatchTests = vi.fn<ConsoleApiClient["runPatchTests"]>().mockResolvedValue({
      task: {
        ...testedTask,
        test_run: undefined
      },
      patch_artifact: testedTask.patch_artifact!,
      test_run: testedTask.test_run!,
      debug_attempt: null
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([patchReadyTaskFixture()]),
      runPatchTests
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run tests" }));

    expect(runPatchTests).toHaveBeenCalledWith("patch_test");
    expect(await within(board).findByText("REVIEWING")).toBeInTheDocument();
    expect(within(board).getByText("Test run")).toBeInTheDocument();
    expect(within(board).getAllByText("passed")).not.toHaveLength(0);
  });

  it("reviews a patch from the task board and renders the approved verdict", async () => {
    const user = userEvent.setup();
    const reviewedTask = reviewingTaskFixture();
    const reviewPatch = vi.fn<ConsoleApiClient["reviewPatch"]>().mockResolvedValue({
      task: {
        ...reviewedTask,
        status: "APPROVED",
        test_run: undefined
      },
      patch_artifact: reviewedTask.patch_artifact!,
      review: {
        id: "review_test",
        workspace_id: "workspace_test",
        project_id: "project_demo",
        task_id: "task_patch_ready",
        local_run_id: "local_run_test",
        patch_artifact_id: "patch_test",
        test_run_id: "test_run_test",
        reviewer_kind: "deterministic",
        verdict: "approved",
        issues: [],
        required_changes: [],
        created_at: "2026-05-29T00:03:00Z"
      },
      debug_attempt: null
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([reviewingTaskFixture()]),
      reviewPatch
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Review patch" }));

    expect(reviewPatch).toHaveBeenCalledWith("patch_test");
    expect(await within(board).findByText("APPROVED")).toBeInTheDocument();
    expect(within(board).getByText("Test run")).toBeInTheDocument();
    expect(within(board).getByText("Review")).toBeInTheDocument();
    expect(within(board).getByText("approved")).toBeInTheDocument();
  });

  it("renders debug root cause when review requests changes", async () => {
    const user = userEvent.setup();
    const reviewedTask = reviewingTaskFixture();
    const reviewPatch = vi.fn<ConsoleApiClient["reviewPatch"]>().mockResolvedValue({
      task: {
        ...reviewedTask,
        status: "FIX_REQUESTED",
        test_run: undefined
      },
      patch_artifact: reviewedTask.patch_artifact!,
      review: {
        id: "review_test",
        workspace_id: "workspace_test",
        project_id: "project_demo",
        task_id: "task_patch_ready",
        local_run_id: "local_run_test",
        patch_artifact_id: "patch_test",
        test_run_id: "test_run_test",
        reviewer_kind: "deterministic",
        verdict: "changes_requested",
        issues: [{ code: "missing_diff", message: "Diff is missing" }],
        required_changes: ["Include a patch diff before approval."],
        created_at: "2026-05-29T00:03:00Z"
      },
      debug_attempt: {
        id: "debug_test",
        workspace_id: "workspace_test",
        project_id: "project_demo",
        task_id: "task_patch_ready",
        patch_artifact_id: "patch_test",
        review_id: "review_test",
        test_run_id: "test_run_test",
        status: "requested",
        root_cause: "deterministic review found missing diff",
        fix_summary: "Attach the generated diff to the patch artifact.",
        created_at: "2026-05-29T00:03:01Z"
      }
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([reviewingTaskFixture()]),
      reviewPatch
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Review patch" }));

    expect(await within(board).findByText("FIX_REQUESTED")).toBeInTheDocument();
    expect(within(board).getByText("changes_requested")).toBeInTheDocument();
    expect(within(board).getByText("deterministic review found missing diff")).toBeInTheDocument();
    expect(within(board).getByText("Attach the generated diff to the patch artifact.")).toBeInTheDocument();
  });
});
