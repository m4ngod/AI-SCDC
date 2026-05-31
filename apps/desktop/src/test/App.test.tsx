import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { ConsoleApiClient, PlannerRunDraft, TaskCard } from "../api/client";
import { fakeApiClient } from "../api/client";

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
      test_result: "not_run",
      diff_text:
        "diff --git a/apps/desktop/src/components/TaskBoard.tsx b/apps/desktop/src/components/TaskBoard.tsx\n+Approve patch"
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

function approvedTaskFixture(): TaskCard {
  return {
    ...reviewingTaskFixture(),
    status: "APPROVED",
    patch_review: {
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
    }
  };
}

function mergeReadyTaskFixture(): TaskCard {
  return {
    ...approvedTaskFixture(),
    status: "MERGE_READY",
    worktree_ref: ".worktrees/task_patch_ready",
    patch_approval: {
      id: "patch_approval_test",
      workspace_id: "workspace_test",
      project_id: "project_demo",
      task_id: "task_patch_ready",
      local_run_id: "local_run_test",
      patch_artifact_id: "patch_test",
      review_id: "review_test",
      status: "approved",
      approved_by: "dev_user",
      merge_instructions:
        "Inspect .worktrees/task_patch_ready before merging. This workflow does not run git merge.",
      created_at: "2026-05-29T00:04:00Z"
    }
  };
}

function cloudRunFixture() {
  return {
    id: "cloud_run_test",
    workspace_id: "workspace_test",
    project_id: "project_demo",
    task_id: "task_cloud",
    repo_id: "repo_github_test",
    local_run_id: "cloud_run_test",
    base_branch: "main",
    head_branch: "ai-scdc/task-cloud",
    status: "patch_ready",
    sandbox_kind: "fake",
    patch_artifact_id: "patch_cloud_test",
    failure_reason: null,
    created_at: "2026-05-29T00:00:00Z",
    updated_at: "2026-05-29T00:00:00Z"
  };
}

function cloudPatchArtifactFixture() {
  return {
    id: "patch_cloud_test",
    task_id: "task_cloud",
    local_run_id: "cloud_run_test",
    summary: "Prepared cloud runner patch.",
    files_changed: ["README.md"],
    tests_run: [],
    test_result: "not_run"
  };
}

function humanApprovalTaskFixture(): TaskCard {
  return {
    ...mergeReadyTaskFixture(),
    status: "HUMAN_APPROVAL",
    cloud_run: cloudRunFixture(),
    patch_artifact: {
      ...mergeReadyTaskFixture().patch_artifact!,
      id: "patch_cloud_test",
      task_id: "task_patch_ready",
      local_run_id: "cloud_run_test"
    },
    patch_approval: {
      ...mergeReadyTaskFixture().patch_approval!,
      id: "patch_approval_test",
      patch_artifact_id: "patch_cloud_test",
      local_run_id: "cloud_run_test"
    }
  };
}

function pullRequestFixture() {
  return {
    id: "pull_request_test",
    workspace_id: "workspace_test",
    project_id: "project_demo",
    task_id: "task_patch_ready",
    repo_id: "repo_github_test",
    patch_artifact_id: "patch_cloud_test",
    patch_approval_id: "patch_approval_test",
    cloud_run_id: "cloud_run_test",
    head_branch: "ai-scdc/task-cloud",
    base_branch: "main",
    github_pr_number: 1,
    github_pr_url: "https://github.com/example/demo/pull/1",
    url: "https://github.com/example/demo/pull/1",
    status: "created",
    created_by: "dev_user",
    created_at: "2026-05-29T00:05:00Z"
  };
}

function secondPatchReadyTaskFixture(): TaskCard {
  return {
    ...patchReadyTaskFixture(),
    id: "task_second_patch_ready",
    title: "Review second desktop patch",
    patch_artifact: {
      ...patchReadyTaskFixture().patch_artifact!,
      id: "patch_second_test",
      task_id: "task_second_patch_ready"
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
    createGitHubCredential: vi.fn().mockResolvedValue({
      id: "github_credential_test",
      workspace_id: "workspace_test",
      display_name: "Example GitHub",
      token_last4: "7890",
      status: "active",
      created_at: "2026-05-29T00:00:00Z",
      updated_at: "2026-05-29T00:00:00Z"
    }),
    listGitHubCredentials: vi.fn().mockResolvedValue([]),
    createGitHubRepository: vi.fn().mockResolvedValue({
      id: "repo_github_test",
      project_id: "project_demo",
      name: "example/demo",
      local_path: "",
      default_branch: "main",
      status: "active",
      provider: "github",
      repo_url: "https://github.com/example/demo",
      github_owner: "example",
      github_repo: "demo",
      github_credential_id: "github_credential_test",
      connection_status: "connected"
    }),
    createSandboxProfile: vi.fn().mockResolvedValue({
      id: "sandbox_profile_test",
      project_id: "project_demo",
      name: "Default Docker profile",
      docker_image: "python:3.11-bookworm",
      patch_commands: [
        {
          key: "write-note",
          label: "Write note",
          command: "python scripts/write_note.py",
          timeout_seconds: 300,
          is_default: true
        }
      ],
      test_commands: [
        {
          key: "python-version",
          label: "Python version",
          command: "python -V",
          timeout_seconds: 300,
          is_default: true
        }
      ],
      allowed_env_vars: ["AI_SCDC_GITHUB_TOKEN"],
      network_enabled: true
    }),
    listSandboxProfiles: vi.fn().mockResolvedValue([]),
    startCloudRun: vi.fn().mockResolvedValue({
      cloud_run: {
        id: "cloud_run_test",
        workspace_id: "workspace_test",
        project_id: "project_demo",
        task_id: "task_created_from_planner",
        repo_id: "repo_github_test",
        local_run_id: "cloud_run_test",
        base_branch: "main",
        head_branch: "codex/task-created-from-planner",
        status: "patch_ready",
        sandbox_kind: "fake",
        patch_artifact_id: "patch_cloud_test",
        failure_reason: null,
        created_at: "2026-05-29T00:00:00Z",
        updated_at: "2026-05-29T00:00:00Z"
      },
      patch_artifact: {
        id: "patch_cloud_test",
        task_id: "task_created_from_planner",
        local_run_id: "cloud_run_test",
        summary: "Prepared cloud runner patch.",
        files_changed: ["README.md"],
        tests_run: [],
        test_result: "not_run"
      }
    }),
    createPullRequest: vi.fn().mockResolvedValue({
      task: {
        ...mergeReadyTaskFixture(),
        status: "PR_CREATED"
      },
      pull_request: {
        id: "pull_request_test",
        workspace_id: "workspace_test",
        project_id: "project_demo",
        task_id: "task_patch_ready",
        repo_id: "repo_github_test",
        patch_artifact_id: "patch_test",
        patch_approval_id: "patch_approval_test",
        cloud_run_id: "cloud_run_test",
        head_branch: "codex/task-patch-ready",
        base_branch: "main",
        github_pr_number: 1,
        github_pr_url: "https://github.com/example/demo/pull/1",
        url: "https://github.com/example/demo/pull/1",
        status: "created",
        created_by: "dev_user",
        created_at: "2026-05-29T00:05:00Z"
      }
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
    approvePatch: vi.fn().mockResolvedValue({
      task: {
        ...mergeReadyTaskFixture(),
        patch_approval: undefined
      },
      patch_artifact: mergeReadyTaskFixture().patch_artifact!,
      review: mergeReadyTaskFixture().patch_review!,
      approval: mergeReadyTaskFixture().patch_approval!
    }),
    requestHumanApproval: vi.fn().mockResolvedValue({
      task: {
        ...mergeReadyTaskFixture(),
        status: "HUMAN_APPROVAL",
        patch_approval: undefined
      },
      patch_artifact: mergeReadyTaskFixture().patch_artifact!,
      review: mergeReadyTaskFixture().patch_review!,
      approval: mergeReadyTaskFixture().patch_approval!
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

  it("keeps the current task identity when fake local-run tests return a demo task", async () => {
    const user = userEvent.setup();
    const originalTask: TaskCard = {
      ...taskCardFixture("Planner-created local patch"),
      id: "task_planner_local_patch"
    };
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([originalTask]),
      startLocalRun: fakeApiClient.startLocalRun,
      runPatchTests: fakeApiClient.runPatchTests
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run local" }));
    await user.click(await within(board).findByRole("button", { name: "Run tests" }));

    expect(await within(board).findByText("Planner-created local patch")).toBeInTheDocument();
    expect(within(board).queryByText("Implement task board UI")).not.toBeInTheDocument();
    expect(within(board).getByText("REVIEWING")).toBeInTheDocument();
  });

  it("disables all test buttons while a patch test run is pending", async () => {
    const user = userEvent.setup();
    const testRun = createDeferred<Awaited<ReturnType<ConsoleApiClient["runPatchTests"]>>>();
    const runPatchTests = vi.fn<ConsoleApiClient["runPatchTests"]>().mockReturnValue(testRun.promise);
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([patchReadyTaskFixture(), secondPatchReadyTaskFixture()]),
      runPatchTests
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    const runButtons = await within(board).findAllByRole("button", { name: "Run tests" });
    await user.click(runButtons[0]);

    await waitFor(() => expect(within(board).getByRole("button", { name: "Testing" })).toBeDisabled());
    expect(within(board).getByRole("button", { name: "Run tests" })).toBeDisabled();
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
    expect(within(board).getByText("Include a patch diff before approval.")).toBeInTheDocument();
    expect(within(board).getByText("requested")).toBeInTheDocument();
    expect(within(board).getByText("deterministic review found missing diff")).toBeInTheDocument();
    expect(
      within(board).getByText("Attach the generated diff to the patch artifact.")
    ).toBeInTheDocument();
  });

  it("renders unified diff preview for patch artifacts", async () => {
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([approvedTaskFixture()])
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    expect(await within(board).findByText("Diff preview")).toBeInTheDocument();
    expect(
      within(board).getByText(/diff --git a\/apps\/desktop\/src\/components\/TaskBoard.tsx/)
    ).toBeInTheDocument();
    const diffPreview = within(board).getByRole("region", { name: "Diff preview" });
    expect(diffPreview).toHaveAttribute("tabindex", "0");
    expect(diffPreview).toHaveAttribute(
      "aria-labelledby",
      "task-task_patch_ready-diff-preview-title"
    );
  });

  it("approves an approved patch and renders merge instructions", async () => {
    const user = userEvent.setup();
    const approvePatch = vi.fn<ConsoleApiClient["approvePatch"]>().mockResolvedValue({
      task: {
        ...mergeReadyTaskFixture(),
        patch_approval: undefined
      },
      patch_artifact: mergeReadyTaskFixture().patch_artifact!,
      review: mergeReadyTaskFixture().patch_review!,
      approval: mergeReadyTaskFixture().patch_approval!
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([approvedTaskFixture()]),
      approvePatch
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Approve patch" }));

    expect(approvePatch).toHaveBeenCalledWith("patch_test");
    expect(await within(board).findByText("MERGE_READY")).toBeInTheDocument();
    expect(within(board).getByText("Patch approval")).toBeInTheDocument();
    expect(within(board).getByText("approved by dev_user")).toBeInTheDocument();
    expect(within(board).getByText(/This workflow does not run git merge/)).toBeInTheDocument();
  });

  it("requests human approval from a merge-ready task", async () => {
    const user = userEvent.setup();
    const requestHumanApproval =
      vi.fn<ConsoleApiClient["requestHumanApproval"]>().mockResolvedValue({
        task: {
          ...mergeReadyTaskFixture(),
          status: "HUMAN_APPROVAL",
          patch_approval: undefined
        },
        patch_artifact: mergeReadyTaskFixture().patch_artifact!,
        review: mergeReadyTaskFixture().patch_review!,
        approval: mergeReadyTaskFixture().patch_approval!
      });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([mergeReadyTaskFixture()]),
      requestHumanApproval
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(
      await within(board).findByRole("button", { name: "Request human approval" })
    );

    expect(requestHumanApproval).toHaveBeenCalledWith("patch_approval_test");
    expect(await within(board).findByText("HUMAN_APPROVAL")).toBeInTheDocument();
  });

  it("registers a github repository from the setup panel", async () => {
    const user = userEvent.setup();
    const createGitHubCredential =
      vi.fn<ConsoleApiClient["createGitHubCredential"]>().mockResolvedValue({
        id: "github_credential_test",
        workspace_id: "workspace_test",
        display_name: "Dev GitHub",
        token_last4: "1234",
        status: "active",
        created_at: "2026-05-29T00:00:00Z",
        updated_at: "2026-05-29T00:00:00Z"
      });
    const createGitHubRepository =
      vi.fn<ConsoleApiClient["createGitHubRepository"]>().mockResolvedValue({
        id: "repo_github_test",
        project_id: "project_demo",
        name: "example/demo",
        local_path: "",
        default_branch: "main",
        status: "active",
        provider: "github",
        repo_url: "https://github.com/example/demo",
        github_owner: "example",
        github_repo: "demo",
        github_credential_id: "github_credential_test",
        connection_status: "connected"
      });
    const createSandboxProfile =
      vi.fn<ConsoleApiClient["createSandboxProfile"]>().mockResolvedValue({
        id: "sandbox_profile_test",
        project_id: "project_demo",
        name: "Default Docker profile",
        docker_image: "python:3.11-bookworm",
        patch_commands: [],
        test_commands: [],
        allowed_env_vars: ["AI_SCDC_GITHUB_TOKEN"],
        network_enabled: true
      });
    const apiClient = createMockApiClient({
      createGitHubCredential,
      createGitHubRepository,
      createSandboxProfile
    });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("GitHub token"), "ghp_test_token_1234");
    await user.click(screen.getByRole("button", { name: "Connect GitHub repo" }));

    expect(createGitHubCredential).toHaveBeenCalledWith({
      display_name: "Dev GitHub",
      token: "ghp_test_token_1234"
    });
    expect(createGitHubRepository).toHaveBeenCalledWith({
      name: "example/demo",
      repo_url: "https://github.com/example/demo",
      github_owner: "example",
      github_repo: "demo",
      default_branch: "main",
      github_credential_id: "github_credential_test"
    });
    expect(createSandboxProfile).toHaveBeenCalledWith("project_demo", {
      name: "Default Docker profile",
      docker_image: "python:3.11-bookworm",
      patch_commands: [
        {
          key: "write-note",
          label: "Write note",
          command: "python scripts/write_note.py",
          timeout_seconds: 300,
          is_default: true
        }
      ],
      test_commands: [
        {
          key: "python-version",
          label: "Python version",
          command: "python -V",
          timeout_seconds: 300,
          is_default: true
        }
      ],
      allowed_env_vars: ["AI_SCDC_GITHUB_TOKEN"],
      network_enabled: true
    });
    expect(await screen.findByText("GitHub repo connected")).toBeInTheDocument();
    expect(
      await screen.findByText("Sandbox profile ready: python:3.11-bookworm")
    ).toBeInTheDocument();
  });

  it("clears the github token after successful setup", async () => {
    const user = userEvent.setup();
    const apiClient = createMockApiClient();

    render(<App apiClient={apiClient} />);

    const tokenInput = screen.getByLabelText("GitHub token");
    await user.type(tokenInput, "ghp_test_token_1234");
    await user.click(screen.getByRole("button", { name: "Connect GitHub repo" }));

    expect(await screen.findByText("GitHub repo connected")).toBeInTheDocument();
    expect(tokenInput).toHaveValue("");
  });

  it("prevents duplicate github setup submits while pending", async () => {
    const user = userEvent.setup();
    const credential = createDeferred<
      Awaited<ReturnType<ConsoleApiClient["createGitHubCredential"]>>
    >();
    const createGitHubCredential =
      vi.fn<ConsoleApiClient["createGitHubCredential"]>().mockReturnValue(credential.promise);
    const createGitHubRepository =
      vi.fn<ConsoleApiClient["createGitHubRepository"]>().mockResolvedValue({
        id: "repo_github_test",
        project_id: "project_demo",
        name: "example/demo",
        local_path: "",
        default_branch: "main",
        status: "active",
        provider: "github",
        repo_url: "https://github.com/example/demo",
        github_owner: "example",
        github_repo: "demo",
        github_credential_id: "github_credential_test",
        connection_status: "connected"
      });
    const apiClient = createMockApiClient({ createGitHubCredential, createGitHubRepository });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("GitHub token"), "ghp_test_token_1234");
    const connectButton = screen.getByRole("button", { name: "Connect GitHub repo" });
    await user.click(connectButton);

    await waitFor(() => expect(connectButton).toBeDisabled());
    await user.click(connectButton);
    expect(createGitHubCredential).toHaveBeenCalledOnce();

    credential.resolve({
      id: "github_credential_test",
      workspace_id: "workspace_test",
      display_name: "Dev GitHub",
      token_last4: "1234",
      status: "active",
      created_at: "2026-05-29T00:00:00Z",
      updated_at: "2026-05-29T00:00:00Z"
    });
    expect(await screen.findByText("GitHub repo connected")).toBeInTheDocument();
  });

  it("runs a cloud task and renders cloud branch metadata", async () => {
    const user = userEvent.setup();
    const task: TaskCard = {
      ...taskCardFixture("Run cloud task"),
      id: "task_cloud"
    };
    const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
      cloud_run: cloudRunFixture(),
      patch_artifact: cloudPatchArtifactFixture()
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([task]),
      startCloudRun
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run cloud" }));

    expect(startCloudRun).toHaveBeenCalledWith("task_cloud");
    expect(await within(board).findByText("PATCH_READY")).toBeInTheDocument();
    expect(
      within(board).getByText("patch_ready via fake on ai-scdc/task-cloud")
    ).toBeInTheDocument();
  });

  it("runs a cloud task with sandbox profile keys after GitHub setup", async () => {
    const user = userEvent.setup();
    const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
      cloud_run: {
        ...cloudRunFixture(),
        sandbox_kind: "docker_local",
        sandbox_profile_id: "sandbox_profile_test",
        patch_command_key: "write-note",
        test_command_keys: ["python-version"],
        command_results: []
      },
      patch_artifact: cloudPatchArtifactFixture()
    });
    const apiClient = createMockApiClient({
      listTasks: vi
        .fn()
        .mockResolvedValue([{ ...taskCardFixture("Profiled cloud task"), id: "task_cloud" }]),
      startCloudRun
    });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("GitHub token"), "ghp_test_token_1234");
    await user.click(screen.getByRole("button", { name: "Connect GitHub repo" }));
    expect(
      await screen.findByText("Sandbox profile ready: python:3.11-bookworm")
    ).toBeInTheDocument();

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run cloud" }));

    expect(startCloudRun).toHaveBeenCalledWith("task_cloud", {
      sandbox_profile_id: "sandbox_profile_test",
      patch_command_key: "write-note",
      test_command_keys: ["python-version"]
    });
    expect(await within(board).findByText("PATCH_READY")).toBeInTheDocument();
  });

  it("renders docker cloud run metadata and failure reason", async () => {
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([
        {
          ...taskCardFixture("Failed docker cloud task"),
          id: "task_cloud_failed",
          status: "CREATED",
          cloud_run: {
            ...cloudRunFixture(),
            id: "cloud_run_failed",
            task_id: "task_cloud_failed",
            status: "failed",
            sandbox_kind: "docker_local",
            head_branch: "ai-scdc/task-cloud-failed-with-a-long-branch-name",
            failure_reason: "Docker image pull failed because authentication expired"
          }
        }
      ])
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    expect(
      await within(board).findByText(
        "failed via docker_local on ai-scdc/task-cloud-failed-with-a-long-branch-name"
      )
    ).toBeInTheDocument();
    expect(
      within(board).getByText("Docker image pull failed because authentication expired")
    ).toBeInTheDocument();
  });

  it("disables cloud run while local run is pending", async () => {
    const user = userEvent.setup();
    const localRun = createDeferred<Awaited<ReturnType<ConsoleApiClient["startLocalRun"]>>>();
    const startLocalRun =
      vi.fn<ConsoleApiClient["startLocalRun"]>().mockReturnValue(localRun.promise);
    const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
      cloud_run: cloudRunFixture(),
      patch_artifact: cloudPatchArtifactFixture()
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([
        {
          ...taskCardFixture("Concurrent run task"),
          id: "task_cloud"
        }
      ]),
      startLocalRun,
      startCloudRun
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run local" }));

    const runCloudButton = within(board).getByRole("button", { name: "Run cloud" });
    await waitFor(() => expect(runCloudButton).toBeDisabled());
    await user.click(runCloudButton);
    expect(startCloudRun).not.toHaveBeenCalled();

    localRun.resolve({
      local_run: {
        id: "local_run_test",
        task_id: "task_cloud",
        repo_id: "repo_test",
        status: "patch_ready",
        base_branch: "main",
        worktree_path: ".worktrees/task_cloud",
        patch_artifact_id: "patch_test",
        failure_reason: null
      },
      patch_artifact: {
        id: "patch_test",
        task_id: "task_cloud",
        local_run_id: "local_run_test",
        summary: "Prepared local runner patch.",
        files_changed: ["README.md"],
        tests_run: [],
        test_result: "not_run"
      }
    });
    expect(await within(board).findByText("PATCH_READY")).toBeInTheDocument();
  });

  it("disables local run while cloud run is pending", async () => {
    const user = userEvent.setup();
    const cloudRun = createDeferred<Awaited<ReturnType<ConsoleApiClient["startCloudRun"]>>>();
    const startCloudRun =
      vi.fn<ConsoleApiClient["startCloudRun"]>().mockReturnValue(cloudRun.promise);
    const startLocalRun = vi.fn<ConsoleApiClient["startLocalRun"]>().mockResolvedValue({
      local_run: {
        id: "local_run_test",
        task_id: "task_cloud",
        repo_id: "repo_test",
        status: "patch_ready",
        base_branch: "main",
        worktree_path: ".worktrees/task_cloud",
        patch_artifact_id: "patch_test",
        failure_reason: null
      },
      patch_artifact: {
        id: "patch_test",
        task_id: "task_cloud",
        local_run_id: "local_run_test",
        summary: "Prepared local runner patch.",
        files_changed: ["README.md"],
        tests_run: [],
        test_result: "not_run"
      }
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([
        {
          ...taskCardFixture("Concurrent cloud task"),
          id: "task_cloud"
        }
      ]),
      startCloudRun,
      startLocalRun
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run cloud" }));

    const runLocalButton = within(board).getByRole("button", { name: "Run local" });
    await waitFor(() => expect(runLocalButton).toBeDisabled());
    await user.click(runLocalButton);
    expect(startLocalRun).not.toHaveBeenCalled();

    cloudRun.resolve({
      cloud_run: cloudRunFixture(),
      patch_artifact: cloudPatchArtifactFixture()
    });
    expect(await within(board).findByText("PATCH_READY")).toBeInTheDocument();
  });

  it("preserves an existing patch artifact when cloud run returns none", async () => {
    const user = userEvent.setup();
    const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
      cloud_run: cloudRunFixture(),
      patch_artifact: undefined
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([
        {
          ...taskCardFixture("Cloud task with existing patch"),
          id: "task_cloud",
          patch_artifact: {
            id: "patch_existing",
            task_id: "task_cloud",
            local_run_id: "local_run_existing",
            summary: "Existing patch.",
            files_changed: ["existing.patch"],
            tests_run: [],
            test_result: "not_run"
          }
        }
      ]),
      startCloudRun
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run cloud" }));

    expect(await within(board).findByText("Cloud run")).toBeInTheDocument();
    expect(within(board).getByText("existing.patch")).toBeInTheDocument();
  });

  it("creates a pull request after human approval", async () => {
    const user = userEvent.setup();
    const humanApprovalTask = humanApprovalTaskFixture();
    const createPullRequest = vi.fn<ConsoleApiClient["createPullRequest"]>().mockResolvedValue({
      task: {
        ...humanApprovalTask,
        status: "PR_CREATED",
        pull_request: undefined
      },
      patch_artifact: humanApprovalTask.patch_artifact!,
      approval: humanApprovalTask.patch_approval!,
      pull_request: pullRequestFixture()
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([humanApprovalTask]),
      createPullRequest
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Create PR" }));

    expect(createPullRequest).toHaveBeenCalledWith("patch_approval_test");
    expect(await within(board).findByText("PR_CREATED")).toBeInTheDocument();
    expect(
      within(board).getByRole("link", {
        name: "https://github.com/example/demo/pull/1"
      })
    ).toHaveAttribute("href", "https://github.com/example/demo/pull/1");
  });
});
