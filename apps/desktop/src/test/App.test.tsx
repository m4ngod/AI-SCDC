import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type {
  CloudRunArtifactContentCard,
  CloudRunArtifactManifestCard,
  CloudRunCard,
  ConsoleApiClient,
  ConsoleIdentity,
  PlannerRunDraft,
  TaskCard
} from "../api/client";
import {
  WebConsoleAuthenticationError,
  fakeApiClient
} from "../api/client";

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

function cloudRunFixture(): CloudRunCard {
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
    cancel_requested: false,
    cancel_requested_at: null,
    cancelled_at: null,
    worker_id: "desktop_test_worker",
    claimed_at: "2026-05-29T00:00:00Z",
    completed_at: "2026-05-29T00:00:00Z",
    queue_provider: "local_db",
    remote_worker_kind: null,
    lease_id: null,
    lease_expires_at: null,
    heartbeat_at: null,
    attempt_count: 0,
    max_attempts: 3,
    last_queue_error: null,
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

function queuedCloudRunFixture() {
  return {
    ...cloudRunFixture(),
    status: "queued",
    patch_artifact_id: null,
    worker_id: null,
    claimed_at: null,
    completed_at: null
  };
}

function cloudRunArtifactManifestFixture(): CloudRunArtifactManifestCard {
  return {
    version: 1,
    cloud_run_id: "cloud_run_test",
    workspace_id: "workspace_test",
    generated_at: "2026-06-06T00:00:00Z",
    retention: {
      policy: "development_default",
      expires_at: "2026-06-13T00:00:00Z",
      cleanup_supported: true
    },
    artifacts: [
      {
        id: "diff_artifact_test",
        cloud_run_id: "cloud_run_test",
        kind: "diff",
        label: "Unified diff",
        provider: "local_inline",
        uri: "local-inline://cloud-run-objects/diff_artifact_test",
        redacted_uri: "local-inline://cloud-run-objects/diff_artifact_test",
        sha256: "a".repeat(64),
        size_bytes: 44,
        content_type: "text/x-diff",
        created_at: "2026-06-06T00:00:00Z",
        expires_at: "2026-06-13T00:00:00Z",
        retention_policy: "development_default",
        download_url: "/cloud-runs/cloud_run_test/artifacts/diff_artifact_test/content"
      },
      {
        id: "log_artifact_test",
        cloud_run_id: "cloud_run_test",
        kind: "log",
        label: "Log stream",
        provider: "local_inline",
        uri: "local-inline://cloud-run-objects/log_artifact_test",
        redacted_uri: "local-inline://cloud-run-objects/log_artifact_test",
        sha256: "b".repeat(64),
        size_bytes: 20,
        content_type: "text/plain",
        created_at: "2026-06-06T00:00:00Z",
        expires_at: "2026-06-13T00:00:00Z",
        retention_policy: "development_default",
        download_url: "/cloud-runs/cloud_run_test/artifacts/log_artifact_test/content"
      },
      {
        id: "binary_artifact_test",
        cloud_run_id: "cloud_run_test",
        kind: "command_result",
        label: "Binary artifact",
        provider: "local_inline",
        uri: "local-inline://cloud-run-objects/binary_artifact_test",
        redacted_uri: "local-inline://cloud-run-objects/binary_artifact_test",
        sha256: "c".repeat(64),
        size_bytes: 10,
        content_type: "application/octet-stream",
        created_at: "2026-06-06T00:00:00Z",
        expires_at: "2026-06-13T00:00:00Z",
        retention_policy: "development_default",
        download_url: "/cloud-runs/cloud_run_test/artifacts/binary_artifact_test/content"
      }
    ]
  };
}

function cloudRunArtifactContentFixture(): CloudRunArtifactContentCard {
  const artifact = cloudRunArtifactManifestFixture().artifacts[0];
  return {
    artifact,
    content: "artifact preview\nwith diff context"
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

function signedInIdentityFixture(): ConsoleIdentity {
  return {
    user_id: "user_sign_out",
    workspace_id: "workspace_sign_out",
    organization_id: "account_sign_out",
    roles: ["owner"],
    auth_mode: "user_session",
    selection_state: "selected",
    accounts: [
      {
        id: "account_sign_out",
        name: "Sign Out Account",
        kind: "personal",
        workspaces: [
          {
            id: "workspace_sign_out",
            name: "Sign Out Workspace",
            role: "owner"
          }
        ]
      }
    ],
    current_account: {
      id: "account_sign_out",
      name: "Sign Out Account",
      kind: "personal"
    },
    current_workspace: {
      id: "workspace_sign_out",
      name: "Sign Out Workspace"
    }
  };
}

function createMockApiClient(overrides: Partial<ConsoleApiClient> = {}): ConsoleApiClient {
  return {
    selectWorkspace: vi.fn().mockResolvedValue(undefined),
    signOut: vi.fn().mockResolvedValue({ redirect_to: null }),
    listDeviceSessions: vi.fn().mockResolvedValue([]),
    revokeDeviceSession: vi.fn().mockResolvedValue(undefined),
    revokeOtherDeviceSessions: vi.fn().mockResolvedValue(undefined),
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
    deleteGitHubCredential: vi.fn().mockResolvedValue({
      id: "github_credential_test",
      workspace_id: "workspace_test",
      display_name: "Example GitHub",
      token_last4: "7890",
      status: "deleted",
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
      connection_status: "active"
    }),
    deleteRepository: vi.fn().mockResolvedValue({
      id: "repo_github_test",
      project_id: "project_demo",
      name: "example/demo",
      local_path: "",
      default_branch: "main",
      status: "deleted",
      provider: "github",
      repo_url: "https://github.com/example/demo",
      github_owner: "example",
      github_repo: "demo",
      github_credential_id: "github_credential_test",
      connection_status: "inactive"
    }),
    createSandboxProfile: vi.fn().mockResolvedValue({
      id: "sandbox_profile_test",
      project_id: "project_demo",
      repo_id: "repo_github_test",
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
      allowed_env_vars: [],
      network_enabled: true,
      status: "active"
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
        cancel_requested: false,
        cancel_requested_at: null,
        cancelled_at: null,
        worker_id: "desktop_test_worker",
        claimed_at: "2026-05-29T00:00:00Z",
        completed_at: "2026-05-29T00:00:00Z",
        queue_provider: "local_db",
        remote_worker_kind: null,
        lease_id: null,
        lease_expires_at: null,
        heartbeat_at: null,
        attempt_count: 0,
        max_attempts: 3,
        last_queue_error: null,
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
    processCloudRun: vi.fn().mockResolvedValue({
      cloud_run: cloudRunFixture(),
      patch_artifact: cloudPatchArtifactFixture()
    }),
    cancelCloudRun: vi.fn().mockResolvedValue({
      ...cloudRunFixture(),
      status: "cancelled",
      cancel_requested: true,
      cancel_requested_at: "2026-05-29T00:01:00Z",
      cancelled_at: "2026-05-29T00:01:00Z",
      patch_artifact_id: null
    }),
    listCloudRunLogs: vi.fn().mockResolvedValue([]),
    listCloudRunLogWindow: vi.fn().mockResolvedValue({
      entries: [],
      nextCursor: null,
      hasMore: false
    }),
    getCloudRunArtifactManifest: vi.fn().mockResolvedValue(cloudRunArtifactManifestFixture()),
    getCloudRunArtifactContent: vi.fn().mockResolvedValue(cloudRunArtifactContentFixture()),
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
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/");
  });

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

  it("offers email sign-in without loading workspace data when no session exists", async () => {
    const apiClient = createMockApiClient({
      getCurrentIdentity: vi.fn().mockResolvedValue(null),
      getLoginUrl: vi
        .fn()
        .mockReturnValue("https://testserver/auth/login?return_to=%2F")
    });

    render(<App apiClient={apiClient} />);

    const signIn = await screen.findByRole("link", { name: "Sign in with email" });
    expect(signIn).toHaveAttribute(
      "href",
      "https://testserver/auth/login?return_to=%2F"
    );
    expect(apiClient.listTasks).not.toHaveBeenCalled();
  });

  it("shows the signed-in Personal Account and default Workspace", async () => {
    const apiClient = createMockApiClient({
      getCurrentIdentity: vi.fn().mockResolvedValue({
        user_id: "user_personal",
        workspace_id: "workspace_personal",
        organization_id: "account_personal",
        roles: ["owner"],
        auth_mode: "user_session",
        selection_state: "selected",
        accounts: [
          {
            id: "account_personal",
            name: "Personal Account",
            kind: "personal",
            workspaces: [
              {
                id: "workspace_personal",
                name: "Default Workspace",
                role: "owner"
              }
            ]
          }
        ],
        current_account: {
          id: "account_personal",
          name: "Personal Account",
          kind: "personal"
        },
        current_workspace: {
          id: "workspace_personal",
          name: "Default Workspace"
        }
      })
    });

    render(<App apiClient={apiClient} />);

    const accountContext = await screen.findByRole("region", {
      name: "Account context"
    });
    expect(within(accountContext).getByText("Personal Account")).toBeInTheDocument();
    expect(within(accountContext).getByText("Default Workspace")).toBeInTheDocument();
    expect(within(accountContext).queryByText("Organization")).not.toBeInTheDocument();
    const topbar = screen.getByRole("banner");
    expect(topbar).toHaveTextContent("Personal Account");
    expect(topbar).toHaveTextContent("Default Workspace");
    expect(topbar).not.toHaveTextContent("Demo Workspace");
  });

  it("shows active Device Sessions and refreshes after revoking another device", async () => {
    const user = userEvent.setup();
    const listDeviceSessions = vi
      .fn<ConsoleApiClient["listDeviceSessions"]>()
      .mockResolvedValueOnce([
        {
          id: "device_session_current",
          device_description: "Firefox on macOS",
          created_at: "2026-07-27T12:01:00",
          last_seen_at: "2026-07-27T13:01:00",
          status: "active",
          is_current: true
        },
        {
          id: "device_session_other",
          device_description: "Chrome on Windows",
          created_at: "2026-07-26T10:00:00",
          last_seen_at: "2026-07-27T12:30:00",
          status: "active",
          is_current: false
        }
      ])
      .mockResolvedValueOnce([
        {
          id: "device_session_current",
          device_description: "Firefox on macOS",
          created_at: "2026-07-27T12:01:00",
          last_seen_at: "2026-07-27T13:01:00",
          status: "active",
          is_current: true
        }
      ]);
    const revokeDeviceSession = vi
      .fn<ConsoleApiClient["revokeDeviceSession"]>()
      .mockResolvedValue(undefined);
    const apiClient = createMockApiClient({
      getCurrentIdentity: vi.fn().mockResolvedValue(signedInIdentityFixture()),
      listDeviceSessions,
      revokeDeviceSession
    });

    render(<App apiClient={apiClient} />);

    const panel = await screen.findByRole("region", {
      name: "Device sessions"
    });
    expect(
      await within(panel).findByText("Firefox on macOS")
    ).toBeInTheDocument();
    expect(within(panel).getByText("Chrome on Windows")).toBeInTheDocument();
    expect(within(panel).getByText("This device")).toBeInTheDocument();
    expect(within(panel).getAllByText("Active")).toHaveLength(2);
    expect(
      within(panel).getAllByText("Created", { selector: "dt" })
    ).toHaveLength(2);
    expect(
      within(panel).getAllByText("Recent activity", { selector: "dt" })
    ).toHaveLength(2);
    expect(
      within(panel).queryByRole("button", {
        name: "Revoke Firefox on macOS"
      })
    ).not.toBeInTheDocument();

    await user.click(
      within(panel).getByRole("button", {
        name: "Revoke Chrome on Windows"
      })
    );

    expect(revokeDeviceSession).toHaveBeenCalledWith("device_session_other");
    expect(listDeviceSessions).toHaveBeenCalledTimes(2);
    expect(
      await within(panel).findByText("Firefox on macOS")
    ).toBeInTheDocument();
    expect(within(panel).queryByText("Chrome on Windows")).not.toBeInTheDocument();
  });

  it("does not report zero active Device Sessions when the list cannot be loaded", async () => {
    const listDeviceSessions = vi
      .fn<ConsoleApiClient["listDeviceSessions"]>()
      .mockRejectedValue(new Error("session_list_unavailable"));
    const apiClient = createMockApiClient({
      getCurrentIdentity: vi.fn().mockResolvedValue(signedInIdentityFixture()),
      listDeviceSessions
    });

    render(<App apiClient={apiClient} />);

    const panel = await screen.findByRole("region", {
      name: "Device sessions"
    });
    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "session_list_unavailable"
    );
    expect(within(panel).queryByText("0 active")).not.toBeInTheDocument();
    expect(
      within(panel).queryByText("No active Device Sessions.")
    ).not.toBeInTheDocument();
  });

  it("keeps the Device Session visible when revocation is rejected", async () => {
    const user = userEvent.setup();
    const listDeviceSessions = vi
      .fn<ConsoleApiClient["listDeviceSessions"]>()
      .mockResolvedValue([
        {
          id: "device_session_other",
          device_description: "Chrome on Windows",
          created_at: "2026-07-26T10:00:00",
          last_seen_at: "2026-07-27T12:30:00",
          status: "active",
          is_current: false
        }
      ]);
    const revokeDeviceSession = vi
      .fn<ConsoleApiClient["revokeDeviceSession"]>()
      .mockRejectedValue(new Error("csrf_token_mismatch"));
    const apiClient = createMockApiClient({
      getCurrentIdentity: vi.fn().mockResolvedValue(signedInIdentityFixture()),
      listDeviceSessions,
      revokeDeviceSession
    });

    render(<App apiClient={apiClient} />);

    const panel = await screen.findByRole("region", {
      name: "Device sessions"
    });
    await user.click(
      await within(panel).findByRole("button", {
        name: "Revoke Chrome on Windows"
      })
    );

    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "csrf_token_mismatch"
    );
    expect(within(panel).getByText("Chrome on Windows")).toBeInTheDocument();
    expect(listDeviceSessions).toHaveBeenCalledOnce();
    expect(revokeDeviceSession).toHaveBeenCalledWith("device_session_other");
  });

  it("does not restore a revoked Device Session when list refresh fails", async () => {
    const user = userEvent.setup();
    const listDeviceSessions = vi
      .fn<ConsoleApiClient["listDeviceSessions"]>()
      .mockResolvedValueOnce([
        {
          id: "device_session_other",
          device_description: "Chrome on Windows",
          created_at: "2026-07-26T10:00:00",
          last_seen_at: "2026-07-27T12:30:00",
          status: "active",
          is_current: false
        }
      ])
      .mockRejectedValueOnce(new Error("session_list_unavailable"));
    const revokeDeviceSession = vi
      .fn<ConsoleApiClient["revokeDeviceSession"]>()
      .mockResolvedValue(undefined);
    const apiClient = createMockApiClient({
      getCurrentIdentity: vi.fn().mockResolvedValue(signedInIdentityFixture()),
      listDeviceSessions,
      revokeDeviceSession
    });

    render(<App apiClient={apiClient} />);

    const panel = await screen.findByRole("region", {
      name: "Device sessions"
    });
    await user.click(
      await within(panel).findByRole("button", {
        name: "Revoke Chrome on Windows"
      })
    );

    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "Device Session was revoked, but the list could not be refreshed"
    );
    expect(within(panel).queryByText("Chrome on Windows")).not.toBeInTheDocument();
    expect(revokeDeviceSession).toHaveBeenCalledWith("device_session_other");
    expect(listDeviceSessions).toHaveBeenCalledTimes(2);
  });

  it("starts Recent Authentication before signing out other devices", async () => {
    const user = userEvent.setup();
    const assign = vi.fn();
    vi.stubGlobal("location", {
      assign,
      pathname: "/",
      search: ""
    });
    const listDeviceSessions = vi
      .fn<ConsoleApiClient["listDeviceSessions"]>()
      .mockResolvedValue([
        {
          id: "device_session_current",
          device_description: "Firefox on Windows",
          created_at: "2026-07-27T12:01:00",
          last_seen_at: "2026-07-27T13:01:00",
          status: "active",
          is_current: true
        },
        {
          id: "device_session_other",
          device_description: "Chrome on macOS",
          created_at: "2026-07-26T10:00:00",
          last_seen_at: "2026-07-27T12:30:00",
          status: "active",
          is_current: false
        }
      ]);
    const revokeOtherDeviceSessions = vi
      .fn<ConsoleApiClient["revokeOtherDeviceSessions"]>()
      .mockRejectedValue(
        new WebConsoleAuthenticationError(
          "reauthentication_required",
          403,
          "reauthentication_required"
        )
      );
    const getRecentAuthenticationUrl = vi
      .fn()
      .mockReturnValue(
        "https://app.example.test/auth/reauthenticate?return_to=%2Freauthentication%2Frevoke-other-sessions"
      );
    const apiClient = createMockApiClient({
      getCurrentIdentity: vi.fn().mockResolvedValue(
        signedInIdentityFixture()
      ),
      listDeviceSessions,
      revokeOtherDeviceSessions,
      getRecentAuthenticationUrl
    });

    render(<App apiClient={apiClient} />);

    const panel = await screen.findByRole("region", {
      name: "Device sessions"
    });
    await user.click(
      await within(panel).findByRole("button", {
        name: "Sign out other devices"
      })
    );

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(
        "https://app.example.test/auth/reauthenticate?return_to=%2Freauthentication%2Frevoke-other-sessions"
      )
    );
    expect(getRecentAuthenticationUrl).toHaveBeenCalledWith(
      "/reauthentication/revoke-other-sessions"
    );
    expect(revokeOtherDeviceSessions).toHaveBeenCalledOnce();
    expect(listDeviceSessions).toHaveBeenCalledOnce();
    expect(within(panel).getByText("Chrome on macOS")).toBeInTheDocument();
  });

  it("requires explicit confirmation after Recent Authentication", async () => {
    window.history.replaceState(
      {},
      "",
      "/reauthentication/revoke-other-sessions?reauthentication=confirmed"
    );
    const user = userEvent.setup();
    const listDeviceSessions = vi
      .fn<ConsoleApiClient["listDeviceSessions"]>()
      .mockResolvedValueOnce([
        {
          id: "device_session_current",
          device_description: "Firefox on Windows",
          created_at: "2026-07-27T12:01:00",
          last_seen_at: "2026-07-27T13:01:00",
          status: "active",
          is_current: true
        },
        {
          id: "device_session_other",
          device_description: "Chrome on macOS",
          created_at: "2026-07-26T10:00:00",
          last_seen_at: "2026-07-27T12:30:00",
          status: "active",
          is_current: false
        }
      ])
      .mockResolvedValueOnce([
        {
          id: "device_session_current",
          device_description: "Firefox on Windows",
          created_at: "2026-07-27T12:01:00",
          last_seen_at: "2026-07-27T13:01:00",
          status: "active",
          is_current: true
        }
      ]);
    const revokeOtherDeviceSessions = vi
      .fn<ConsoleApiClient["revokeOtherDeviceSessions"]>()
      .mockResolvedValue(undefined);
    const apiClient = createMockApiClient({
      getCurrentIdentity: vi.fn().mockResolvedValue(
        signedInIdentityFixture()
      ),
      listDeviceSessions,
      revokeOtherDeviceSessions
    });

    render(<App apiClient={apiClient} />);

    const panel = await screen.findByRole("region", {
      name: "Device sessions"
    });
    expect(
      await within(panel).findByRole("heading", {
        name: "Sign out other devices?"
      })
    ).toBeInTheDocument();
    expect(revokeOtherDeviceSessions).not.toHaveBeenCalled();
    expect(
      screen.getByRole("heading", { name: "GitHub setup" })
    ).toBeInTheDocument();

    await user.click(
      within(panel).getByRole("button", {
        name: "Confirm sign out other devices"
      })
    );

    await waitFor(() =>
      expect(revokeOtherDeviceSessions).toHaveBeenCalledOnce()
    );
    expect(listDeviceSessions).toHaveBeenCalledTimes(2);
    expect(within(panel).queryByText("Chrome on macOS")).not.toBeInTheDocument();
    expect(within(panel).getByText("Firefox on Windows")).toBeInTheDocument();
  });

  it.each([
    [
      "cancelled",
      "Email verification was cancelled. No other device was signed out."
    ],
    [
      "provider_unavailable",
      "Email verification is temporarily unavailable. No other device was signed out."
    ],
    [
      "failed",
      "Email verification could not be completed. No other device was signed out."
    ]
  ])(
    "keeps every session and offers recovery when bulk sign-out is %s",
    async (result, message) => {
      window.history.replaceState(
        {},
        "",
        `/reauthentication/revoke-other-sessions?reauthentication=${result}`
      );
      const listDeviceSessions = vi
        .fn<ConsoleApiClient["listDeviceSessions"]>()
        .mockResolvedValue([
          {
            id: "device_session_current",
            device_description: "Firefox on Windows",
            created_at: "2026-07-27T12:01:00",
            last_seen_at: "2026-07-27T13:01:00",
            status: "active",
            is_current: true
          },
          {
            id: "device_session_other",
            device_description: "Chrome on macOS",
            created_at: "2026-07-26T10:00:00",
            last_seen_at: "2026-07-27T12:30:00",
            status: "active",
            is_current: false
          }
        ]);
      const revokeOtherDeviceSessions = vi
        .fn<ConsoleApiClient["revokeOtherDeviceSessions"]>();
      const getRecentAuthenticationUrl = vi
        .fn()
        .mockReturnValue(
          "https://app.example.test/auth/reauthenticate?return_to=%2Freauthentication%2Frevoke-other-sessions"
        );
      const apiClient = createMockApiClient({
        getCurrentIdentity: vi.fn().mockResolvedValue(
          signedInIdentityFixture()
        ),
        listDeviceSessions,
        revokeOtherDeviceSessions,
        getRecentAuthenticationUrl
      });

      render(<App apiClient={apiClient} />);

      const panel = await screen.findByRole("region", {
        name: "Device sessions"
      });
      expect(await within(panel).findByRole("alert")).toHaveTextContent(
        message
      );
      expect(
        within(panel).getByRole("link", {
          name: "Try email verification again"
        })
      ).toHaveAttribute(
        "href",
        "https://app.example.test/auth/reauthenticate?return_to=%2Freauthentication%2Frevoke-other-sessions"
      );
      expect(within(panel).getByText("Chrome on macOS")).toBeInTheDocument();
      expect(revokeOtherDeviceSessions).not.toHaveBeenCalled();
      expect(listDeviceSessions).toHaveBeenCalledOnce();
    }
  );

  it("keeps every session visible when bulk sign-out fails", async () => {
    window.history.replaceState(
      {},
      "",
      "/reauthentication/revoke-other-sessions?reauthentication=confirmed"
    );
    const user = userEvent.setup();
    const listDeviceSessions = vi
      .fn<ConsoleApiClient["listDeviceSessions"]>()
      .mockResolvedValue([
        {
          id: "device_session_current",
          device_description: "Firefox on Windows",
          created_at: "2026-07-27T12:01:00",
          last_seen_at: "2026-07-27T13:01:00",
          status: "active",
          is_current: true
        },
        {
          id: "device_session_other",
          device_description: "Chrome on macOS",
          created_at: "2026-07-26T10:00:00",
          last_seen_at: "2026-07-27T12:30:00",
          status: "active",
          is_current: false
        }
      ]);
    const revokeOtherDeviceSessions = vi
      .fn<ConsoleApiClient["revokeOtherDeviceSessions"]>()
      .mockRejectedValue(new Error("device_session_revocation_failed"));
    const apiClient = createMockApiClient({
      getCurrentIdentity: vi.fn().mockResolvedValue(
        signedInIdentityFixture()
      ),
      listDeviceSessions,
      revokeOtherDeviceSessions
    });

    render(<App apiClient={apiClient} />);

    const panel = await screen.findByRole("region", {
      name: "Device sessions"
    });
    await user.click(
      await within(panel).findByRole("button", {
        name: "Confirm sign out other devices"
      })
    );

    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "device_session_revocation_failed"
    );
    expect(within(panel).getByText("Chrome on macOS")).toBeInTheDocument();
    expect(listDeviceSessions).toHaveBeenCalledOnce();
  });

  it("signs out locally before a best-effort provider redirect", async () => {
    const user = userEvent.setup();
    const redirectTo =
      "https://fake-idp.example.test/logout?post_logout_redirect_uri=https%3A%2F%2Fapp.example.test%2F";
    const signOut = vi
      .fn<ConsoleApiClient["signOut"]>()
      .mockResolvedValue({ redirect_to: redirectTo });
    const assign = vi.fn(() => {
      throw new Error("Browser navigation failed");
    });
    vi.stubGlobal("location", { assign });
    const apiClient = createMockApiClient({
      signOut,
      getCurrentIdentity: vi.fn().mockResolvedValue(signedInIdentityFixture())
    });

    render(<App apiClient={apiClient} />);

    await user.click(
      await screen.findByRole("button", { name: "Sign out" })
    );

    expect(signOut).toHaveBeenCalledOnce();
    expect(
      await screen.findByRole("link", { name: "Sign in with email" })
    ).toBeInTheDocument();
    expect(assign).toHaveBeenCalledWith(redirectTo);
  });

  it("keeps the signed-in view when the protected Sign Out request is rejected", async () => {
    const user = userEvent.setup();
    const signOut = vi
      .fn<ConsoleApiClient["signOut"]>()
      .mockRejectedValue(new Error("csrf_token_mismatch"));
    const apiClient = createMockApiClient({
      signOut,
      getCurrentIdentity: vi.fn().mockResolvedValue(signedInIdentityFixture())
    });

    render(<App apiClient={apiClient} />);

    await user.click(
      await screen.findByRole("button", { name: "Sign out" })
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "csrf_token_mismatch"
    );
    expect(screen.getByRole("banner")).toHaveTextContent("Sign Out Account");
    expect(
      screen.queryByRole("link", { name: "Sign in with email" })
    ).not.toBeInTheDocument();
    expect(signOut).toHaveBeenCalledOnce();
  });

  it("requires an explicit workspace selection before loading workspace data", async () => {
    const user = userEvent.setup();
    const selectionRequired = {
      user_id: "user_multi",
      workspace_id: null,
      organization_id: null,
      roles: [],
      auth_mode: "user_session",
      selection_state: "selection_required" as const,
      accounts: [
        {
          id: "account_alpha",
          name: "Alpha Account",
          kind: "legacy" as const,
          workspaces: [
            {
              id: "workspace_alpha_review",
              name: "Review",
              role: "reviewer"
            }
          ]
        },
        {
          id: "account_beta",
          name: "Beta Account",
          kind: "legacy" as const,
          workspaces: [
            {
              id: "workspace_beta_build",
              name: "Build",
              role: "developer"
            }
          ]
        }
      ],
      current_account: null,
      current_workspace: null
    };
    const selectedIdentity = {
      ...selectionRequired,
      workspace_id: "workspace_beta_build",
      organization_id: "account_beta",
      roles: ["developer"],
      selection_state: "selected" as const,
      current_account: {
        id: "account_beta",
        name: "Beta Account",
        kind: "legacy" as const
      },
      current_workspace: {
        id: "workspace_beta_build",
        name: "Build"
      }
    };
    const getCurrentIdentity = vi
      .fn<NonNullable<ConsoleApiClient["getCurrentIdentity"]>>()
      .mockResolvedValueOnce(selectionRequired)
      .mockResolvedValueOnce(selectedIdentity);
    const selectWorkspace = vi
      .fn<NonNullable<ConsoleApiClient["selectWorkspace"]>>()
      .mockResolvedValue(undefined);
    const listTasks = vi.fn().mockResolvedValue([]);
    const apiClient = createMockApiClient({
      getCurrentIdentity,
      selectWorkspace,
      listTasks
    });

    render(<App apiClient={apiClient} />);

    const workspaceSelect = await screen.findByRole("combobox", {
      name: "Workspace"
    });
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Choose a workspace to continue"
      })
    ).toBeInTheDocument();
    expect(listTasks).not.toHaveBeenCalled();

    await user.selectOptions(workspaceSelect, "workspace_beta_build");

    await waitFor(() => {
      expect(selectWorkspace).toHaveBeenCalledWith("workspace_beta_build");
      expect(getCurrentIdentity).toHaveBeenCalledTimes(2);
      expect(listTasks).toHaveBeenCalledOnce();
    });
    expect(screen.getByRole("banner")).toHaveTextContent("Beta Account");
    expect(screen.getByRole("banner")).toHaveTextContent("Build");
  });

  it("returns to safe selection when access is lost during the first load after a switch", async () => {
    const user = userEvent.setup();
    const selectionRequired = {
      user_id: "user_switch_loss",
      workspace_id: null,
      organization_id: null,
      roles: [],
      auth_mode: "user_session",
      selection_state: "selection_required" as const,
      accounts: [
        {
          id: "account_alpha",
          name: "Alpha Account",
          kind: "legacy" as const,
          workspaces: [
            {
              id: "workspace_alpha",
              name: "Alpha",
              role: "developer"
            }
          ]
        }
      ],
      current_account: null,
      current_workspace: null
    };
    const selectedIdentity = {
      ...selectionRequired,
      workspace_id: "workspace_alpha",
      organization_id: "account_alpha",
      roles: ["developer"],
      selection_state: "selected" as const,
      current_account: {
        id: "account_alpha",
        name: "Alpha Account",
        kind: "legacy" as const
      },
      current_workspace: {
        id: "workspace_alpha",
        name: "Alpha"
      }
    };
    const getCurrentIdentity = vi
      .fn<NonNullable<ConsoleApiClient["getCurrentIdentity"]>>()
      .mockResolvedValueOnce(selectionRequired)
      .mockResolvedValueOnce(selectedIdentity)
      .mockResolvedValueOnce(selectionRequired);
    const selectWorkspace = vi
      .fn<NonNullable<ConsoleApiClient["selectWorkspace"]>>()
      .mockResolvedValue(undefined);
    const listTasks = vi
      .fn<ConsoleApiClient["listTasks"]>()
      .mockRejectedValue(
        new WebConsoleAuthenticationError(
          "workspace_access_lost",
          409,
          "workspace_access_lost"
        )
      );
    const apiClient = createMockApiClient({
      getCurrentIdentity,
      selectWorkspace,
      listTasks
    });

    render(<App apiClient={apiClient} />);

    const workspaceSelect = await screen.findByRole("combobox", {
      name: "Workspace"
    });
    await user.selectOptions(workspaceSelect, "workspace_alpha");

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Choose a workspace to continue"
      })
    ).toBeInTheDocument();
    expect(selectWorkspace).toHaveBeenCalledOnce();
    expect(listTasks).toHaveBeenCalledOnce();
    expect(getCurrentIdentity).toHaveBeenCalledTimes(3);
  });

  it("enters safe selection state without retrying a failed workspace write", async () => {
    const user = userEvent.setup();
    const accounts = [
      {
        id: "account_alpha",
        name: "Alpha Account",
        kind: "legacy" as const,
        workspaces: [
          {
            id: "workspace_alpha",
            name: "Alpha",
            role: "developer"
          },
          {
            id: "workspace_review",
            name: "Review",
            role: "reviewer"
          }
        ]
      }
    ];
    const selectedIdentity = {
      user_id: "user_runtime_loss",
      workspace_id: "workspace_alpha",
      organization_id: "account_alpha",
      roles: ["developer"],
      auth_mode: "user_session",
      selection_state: "selected" as const,
      accounts,
      current_account: {
        id: "account_alpha",
        name: "Alpha Account",
        kind: "legacy" as const
      },
      current_workspace: {
        id: "workspace_alpha",
        name: "Alpha"
      }
    };
    const selectionRequired = {
      ...selectedIdentity,
      workspace_id: null,
      organization_id: null,
      roles: [],
      selection_state: "selection_required" as const,
      current_account: null,
      current_workspace: null
    };
    const getCurrentIdentity = vi
      .fn<NonNullable<ConsoleApiClient["getCurrentIdentity"]>>()
      .mockResolvedValueOnce(selectedIdentity)
      .mockResolvedValueOnce(selectionRequired);
    const createPlannerRun = vi
      .fn<ConsoleApiClient["createPlannerRun"]>()
      .mockRejectedValue(
        new WebConsoleAuthenticationError(
          "workspace_access_lost",
          409,
          "workspace_access_lost"
        )
      );
    const listTasks = vi.fn().mockResolvedValue([]);
    const apiClient = createMockApiClient({
      getCurrentIdentity,
      createPlannerRun,
      listTasks
    });

    render(<App apiClient={apiClient} />);
    await waitFor(() => expect(listTasks).toHaveBeenCalledOnce());

    await user.type(screen.getByLabelText("Goal"), "Do not replay this write");
    await user.click(screen.getByRole("button", { name: "Plan tasks" }));

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Choose a workspace to continue"
      })
    ).toBeInTheDocument();
    expect(createPlannerRun).toHaveBeenCalledOnce();
    expect(getCurrentIdentity).toHaveBeenCalledTimes(2);
    expect(listTasks).toHaveBeenCalledOnce();
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

  it("starts Recent Authentication without retaining credential plaintext", async () => {
    const user = userEvent.setup();
    const assign = vi.fn();
    vi.stubGlobal("location", {
      assign,
      pathname: "/",
      search: ""
    });
    const createGitHubCredential = vi
      .fn<ConsoleApiClient["createGitHubCredential"]>()
      .mockRejectedValue(
        new WebConsoleAuthenticationError(
          "reauthentication_required",
          403,
          "reauthentication_required"
        )
      );
    const createGitHubRepository = vi.fn();
    const getRecentAuthenticationUrl = vi
      .fn()
      .mockReturnValue(
        "https://app.example.test/auth/reauthenticate?return_to=%2Freauthentication%2Fconfirm"
      );
    const apiClient = createMockApiClient({
      createGitHubCredential,
      createGitHubRepository,
      getRecentAuthenticationUrl
    });

    render(<App apiClient={apiClient} />);

    const tokenInput = screen.getByLabelText("GitHub token");
    await user.type(tokenInput, "ghp_plaintext_must_not_survive_redirect");
    await user.click(
      screen.getByRole("button", { name: "Connect GitHub repo" })
    );

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(
        "https://app.example.test/auth/reauthenticate?return_to=%2Freauthentication%2Fconfirm"
      )
    );
    expect(tokenInput).toHaveValue("");
    expect(createGitHubRepository).not.toHaveBeenCalled();
  });

  it("shows an explicit confirmation screen after Recent Authentication", async () => {
    window.history.replaceState(
      {},
      "",
      "/reauthentication/confirm?reauthentication=confirmed"
    );
    const createGitHubCredential = vi.fn();
    const apiClient = createMockApiClient({
      createGitHubCredential
    });

    render(<App apiClient={apiClient} />);

    expect(
      await screen.findByRole("heading", {
        name: "Confirm credential change"
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Your email was verified. Review the details and submit the credential change again."
      )
    ).toBeInTheDocument();
    expect(screen.getByLabelText("GitHub token")).toHaveValue("");
    expect(
      screen.getByRole("button", {
        name: "Confirm and connect GitHub repo"
      })
    ).toBeInTheDocument();
    expect(createGitHubCredential).not.toHaveBeenCalled();
  });

  it.each([
    [
      "cancelled",
      "Email verification was cancelled. No credential was changed."
    ],
    [
      "provider_unavailable",
      "Email verification is temporarily unavailable. No credential was changed."
    ],
    [
      "failed",
      "Email verification could not be completed. No credential was changed."
    ]
  ])(
    "shows a recoverable credential screen when Recent Authentication is %s",
    async (result, message) => {
      window.history.replaceState(
        {},
        "",
        `/reauthentication/confirm?reauthentication=${result}`
      );
      const getRecentAuthenticationUrl = vi
        .fn()
        .mockReturnValue(
          "https://app.example.test/auth/reauthenticate?return_to=%2Freauthentication%2Fconfirm"
        );
      const createGitHubCredential = vi.fn();
      const apiClient = createMockApiClient({
        createGitHubCredential,
        getRecentAuthenticationUrl
      });

      render(<App apiClient={apiClient} />);

      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      expect(
        screen.getByRole("link", {
          name: "Try email verification again"
        })
      ).toHaveAttribute(
        "href",
        "https://app.example.test/auth/reauthenticate?return_to=%2Freauthentication%2Fconfirm"
      );
      expect(screen.getByLabelText("GitHub token")).toHaveValue("");
      expect(createGitHubCredential).not.toHaveBeenCalled();
    }
  );

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
        connection_status: "active"
      });
    const createSandboxProfile =
      vi.fn<ConsoleApiClient["createSandboxProfile"]>().mockResolvedValue({
        id: "sandbox_profile_test",
        project_id: "project_demo",
        repo_id: "repo_github_test",
        name: "Default Docker profile",
        docker_image: "python:3.11-bookworm",
        patch_commands: [],
        test_commands: [],
        allowed_env_vars: [],
        network_enabled: true,
        status: "active"
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
      repo_id: "repo_github_test",
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
      allowed_env_vars: [],
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
        connection_status: "active"
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

  it("deletes a created github credential when repository setup fails", async () => {
    const user = userEvent.setup();
    const deleteGitHubCredential =
      vi.fn<ConsoleApiClient["deleteGitHubCredential"]>().mockResolvedValue({
        id: "github_credential_test",
        workspace_id: "workspace_test",
        display_name: "Example GitHub",
        token_last4: "1234",
        status: "deleted",
        created_at: "2026-05-29T00:00:00Z",
        updated_at: "2026-05-29T00:00:00Z"
      });
    const apiClient = createMockApiClient({
      createGitHubRepository: vi.fn().mockRejectedValue(new Error("Invalid GitHub URL")),
      deleteGitHubCredential
    });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("GitHub token"), "ghp_test_token_1234");
    await user.click(screen.getByRole("button", { name: "Connect GitHub repo" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid GitHub URL");
    expect(deleteGitHubCredential).toHaveBeenCalledWith("github_credential_test");
  });

  it("deletes a created github repository and credential when sandbox profile setup fails", async () => {
    const user = userEvent.setup();
    const deleteRepository = vi.fn<ConsoleApiClient["deleteRepository"]>().mockResolvedValue({
      id: "repo_github_test",
      project_id: "project_demo",
      name: "example/demo",
      local_path: "",
      default_branch: "main",
      status: "deleted",
      provider: "github",
      repo_url: "https://github.com/example/demo",
      github_owner: "example",
      github_repo: "demo",
      github_credential_id: "github_credential_test",
      connection_status: "inactive"
    });
    const deleteGitHubCredential =
      vi.fn<ConsoleApiClient["deleteGitHubCredential"]>().mockResolvedValue({
        id: "github_credential_test",
        workspace_id: "workspace_test",
        display_name: "Example GitHub",
        token_last4: "1234",
        status: "deleted",
        created_at: "2026-05-29T00:00:00Z",
        updated_at: "2026-05-29T00:00:00Z"
      });
    const apiClient = createMockApiClient({
      createSandboxProfile: vi.fn().mockRejectedValue(new Error("Invalid sandbox profile")),
      deleteRepository,
      deleteGitHubCredential
    });

    render(<App apiClient={apiClient} />);

    await user.type(screen.getByLabelText("GitHub token"), "ghp_test_token_1234");
    await user.click(screen.getByRole("button", { name: "Connect GitHub repo" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid sandbox profile");
    expect(deleteRepository).toHaveBeenCalledWith("repo_github_test");
    expect(deleteGitHubCredential).toHaveBeenCalledWith("github_credential_test");
    expect(deleteRepository.mock.invocationCallOrder[0]).toBeLessThan(
      deleteGitHubCredential.mock.invocationCallOrder[0]
    );
  });

  it("returns to safe selection when access is lost during setup cleanup", async () => {
    const user = userEvent.setup();
    const selectedIdentity = {
      user_id: "user_cleanup_loss",
      workspace_id: "workspace_test",
      organization_id: "account_test",
      roles: ["developer"],
      auth_mode: "user_session",
      selection_state: "selected" as const,
      accounts: [
        {
          id: "account_test",
          name: "Test Account",
          kind: "legacy" as const,
          workspaces: [
            {
              id: "workspace_test",
              name: "Test Workspace",
              role: "developer"
            }
          ]
        }
      ],
      current_account: {
        id: "account_test",
        name: "Test Account",
        kind: "legacy" as const
      },
      current_workspace: {
        id: "workspace_test",
        name: "Test Workspace"
      }
    };
    const selectionRequired = {
      ...selectedIdentity,
      workspace_id: null,
      organization_id: null,
      roles: [],
      selection_state: "selection_required" as const,
      current_account: null,
      current_workspace: null
    };
    const getCurrentIdentity = vi
      .fn<NonNullable<ConsoleApiClient["getCurrentIdentity"]>>()
      .mockResolvedValueOnce(selectedIdentity)
      .mockResolvedValueOnce(selectionRequired);
    const createSandboxProfile = vi
      .fn<ConsoleApiClient["createSandboxProfile"]>()
      .mockRejectedValue(new Error("Invalid sandbox profile"));
    const deleteRepository = vi
      .fn<ConsoleApiClient["deleteRepository"]>()
      .mockRejectedValue(
        new WebConsoleAuthenticationError(
          "workspace_access_lost",
          409,
          "workspace_access_lost"
        )
      );
    const deleteGitHubCredential =
      vi.fn<ConsoleApiClient["deleteGitHubCredential"]>();
    const apiClient = createMockApiClient({
      getCurrentIdentity,
      createSandboxProfile,
      deleteRepository,
      deleteGitHubCredential
    });

    render(<App apiClient={apiClient} />);
    await waitFor(() => expect(apiClient.listTasks).toHaveBeenCalledOnce());

    await user.type(screen.getByLabelText("GitHub token"), "ghp_test_token_1234");
    await user.click(screen.getByRole("button", { name: "Connect GitHub repo" }));

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Choose a workspace to continue"
      })
    ).toBeInTheDocument();
    expect(createSandboxProfile).toHaveBeenCalledOnce();
    expect(deleteRepository).toHaveBeenCalledOnce();
    expect(deleteGitHubCredential).not.toHaveBeenCalled();
    expect(getCurrentIdentity).toHaveBeenCalledTimes(2);
  });

  it("runs a cloud task and renders cloud branch metadata", async () => {
    const user = userEvent.setup();
    const task: TaskCard = {
      ...taskCardFixture("Run cloud task"),
      id: "task_cloud"
    };
    const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
      cloud_run: queuedCloudRunFixture(),
      patch_artifact: undefined
    });
    const listCloudRunLogs = vi.fn<ConsoleApiClient["listCloudRunLogs"]>().mockResolvedValue([
      {
        id: "log_queued",
        cloud_run_id: "cloud_run_test",
        level: "info",
        event: "queued",
        message: "Cloud run queued.",
        payload: { repo_id: "repo_github_test" },
        created_at: "2026-05-29T00:00:00Z"
      }
    ]);
    const processCloudRun = vi.fn<ConsoleApiClient["processCloudRun"]>().mockResolvedValue({
      cloud_run: cloudRunFixture(),
      patch_artifact: cloudPatchArtifactFixture()
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([task]),
      startCloudRun,
      processCloudRun,
      listCloudRunLogs
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run cloud" }));

    expect(startCloudRun).toHaveBeenCalledWith("task_cloud");
    expect(listCloudRunLogs).toHaveBeenCalledWith("cloud_run_test");
    expect(
      await within(board).findByText("queued via fake on ai-scdc/task-cloud")
    ).toBeInTheDocument();
    expect(within(board).getByText("queued: Cloud run queued.")).toBeInTheDocument();
    await user.click(within(board).getByRole("button", { name: "Process" }));

    expect(processCloudRun).toHaveBeenCalledWith("cloud_run_test");
    expect(await within(board).findByText("PATCH_READY")).toBeInTheDocument();
    expect(
      within(board).getByText("patch_ready via fake on ai-scdc/task-cloud")
    ).toBeInTheDocument();
  });

  it("renders cloud run artifacts from manifest and surfaces preview errors", async () => {
    const user = userEvent.setup();
    const task: TaskCard = {
      ...taskCardFixture("Browse cloud artifacts"),
      id: "task_cloud"
    };
    const getCloudRunArtifactManifest = vi
      .fn<ConsoleApiClient["getCloudRunArtifactManifest"]>()
      .mockResolvedValue(cloudRunArtifactManifestFixture());
    const getCloudRunArtifactContent = vi
      .fn<ConsoleApiClient["getCloudRunArtifactContent"]>()
      .mockResolvedValueOnce(cloudRunArtifactContentFixture())
      .mockRejectedValueOnce(new Error("Unsupported preview"));
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([task]),
      startCloudRun: vi.fn().mockResolvedValue({
        cloud_run: queuedCloudRunFixture(),
        patch_artifact: undefined
      }),
      processCloudRun: vi.fn().mockResolvedValue({
        cloud_run: cloudRunFixture(),
        patch_artifact: cloudPatchArtifactFixture()
      }),
      getCloudRunArtifactManifest,
      getCloudRunArtifactContent
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run cloud" }));
    await user.click(await within(board).findByRole("button", { name: "Process" }));

    expect(getCloudRunArtifactManifest).toHaveBeenCalledWith("cloud_run_test");
    expect(await within(board).findByText("Artifacts")).toBeInTheDocument();
    expect(within(board).getByText("development_default")).toBeInTheDocument();
    expect(
      within(board).getByRole("button", { name: "Unified diff" })
    ).toBeInTheDocument();
    expect(within(board).getByText("Log stream")).toBeInTheDocument();
    expect(
      within(board).getByRole("button", { name: "Binary artifact" })
    ).toBeInTheDocument();

    await user.click(within(board).getByRole("button", { name: "Unified diff" }));

    expect(getCloudRunArtifactContent).toHaveBeenCalledWith(
      "cloud_run_test",
      "diff_artifact_test"
    );
    expect(await within(board).findByText(/artifact preview/)).toBeInTheDocument();

    await user.click(within(board).getByRole("button", { name: "Binary artifact" }));

    expect(getCloudRunArtifactContent).toHaveBeenCalledWith(
      "cloud_run_test",
      "binary_artifact_test"
    );
    expect(await within(board).findByRole("alert")).toHaveTextContent(
      "Unsupported preview"
    );
    expect(within(board).queryByText(/artifact preview/)).not.toBeInTheDocument();
  });

  it("keeps newer cloud artifact previews when older requests finish later", async () => {
    const user = userEvent.setup();
    const manifest = cloudRunArtifactManifestFixture();
    const diffArtifact = manifest.artifacts[0];
    const logArtifact = manifest.artifacts[1];
    const task: TaskCard = {
      ...taskCardFixture("Race cloud artifact previews"),
      id: "task_cloud",
      cloud_run: cloudRunFixture(),
      cloud_run_artifact_manifest: manifest
    };
    const firstPreview =
      createDeferred<Awaited<ReturnType<ConsoleApiClient["getCloudRunArtifactContent"]>>>();
    const secondPreview =
      createDeferred<Awaited<ReturnType<ConsoleApiClient["getCloudRunArtifactContent"]>>>();
    const getCloudRunArtifactContent = vi
      .fn<ConsoleApiClient["getCloudRunArtifactContent"]>()
      .mockReturnValueOnce(firstPreview.promise)
      .mockReturnValueOnce(secondPreview.promise);
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([task]),
      getCloudRunArtifactContent
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Unified diff" }));
    await user.click(within(board).getByRole("button", { name: "Log stream" }));

    secondPreview.resolve({
      artifact: logArtifact,
      content: "newer log preview"
    });
    expect(await within(board).findByText("newer log preview")).toBeInTheDocument();

    firstPreview.resolve({
      artifact: diffArtifact,
      content: "older diff preview"
    });

    await waitFor(() =>
      expect(within(board).queryByText("older diff preview")).not.toBeInTheDocument()
    );
    expect(within(board).getByText("newer log preview")).toBeInTheDocument();
  });

  it("cancels queued cloud runs and refreshes cloud logs", async () => {
    const user = userEvent.setup();
    const queuedRun = queuedCloudRunFixture();
    const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
      cloud_run: queuedRun,
      patch_artifact: undefined
    });
    const cancelCloudRun = vi.fn<ConsoleApiClient["cancelCloudRun"]>().mockResolvedValue({
      ...queuedRun,
      status: "cancelled",
      cancel_requested: true,
      cancel_requested_at: "2026-05-29T00:01:00Z",
      cancelled_at: "2026-05-29T00:01:00Z"
    });
    const listCloudRunLogs = vi
      .fn<ConsoleApiClient["listCloudRunLogs"]>()
      .mockResolvedValueOnce([
        {
          id: "log_queued",
          cloud_run_id: "cloud_run_test",
          level: "info",
          event: "queued",
          message: "Cloud run queued.",
          payload: null,
          created_at: "2026-05-29T00:00:00Z"
        }
      ])
      .mockResolvedValueOnce([
        {
          id: "log_queued",
          cloud_run_id: "cloud_run_test",
          level: "info",
          event: "queued",
          message: "Cloud run queued.",
          payload: null,
          created_at: "2026-05-29T00:00:00Z"
        },
        {
          id: "log_cancelled",
          cloud_run_id: "cloud_run_test",
          level: "info",
          event: "cancelled",
          message: "Cloud run cancelled.",
          payload: null,
          created_at: "2026-05-29T00:01:00Z"
        }
      ]);
    const apiClient = createMockApiClient({
      listTasks: vi
        .fn()
        .mockResolvedValue([{ ...taskCardFixture("Cancel cloud task"), id: "task_cloud" }]),
      startCloudRun,
      cancelCloudRun,
      listCloudRunLogs
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run cloud" }));
    await user.click(await within(board).findByRole("button", { name: "Cancel" }));

    expect(cancelCloudRun).toHaveBeenCalledWith("cloud_run_test");
    expect(listCloudRunLogs).toHaveBeenCalledTimes(2);
    expect(
      await within(board).findByText("cancelled via fake on ai-scdc/task-cloud")
    ).toBeInTheDocument();
    expect(within(board).getByText("cancelled: Cloud run cancelled.")).toBeInTheDocument();
  });

  it("keeps successful cloud run updates when log refresh fails", async () => {
    const user = userEvent.setup();
    const queuedRun = queuedCloudRunFixture();
    const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
      cloud_run: queuedRun,
      patch_artifact: undefined
    });
    const processCloudRun = vi.fn<ConsoleApiClient["processCloudRun"]>().mockResolvedValue({
      cloud_run: cloudRunFixture(),
      patch_artifact: cloudPatchArtifactFixture()
    });
    const cancelCloudRun = vi.fn<ConsoleApiClient["cancelCloudRun"]>().mockResolvedValue({
      ...queuedRun,
      status: "cancelled",
      cancel_requested: true,
      cancel_requested_at: "2026-05-29T00:01:00Z",
      cancelled_at: "2026-05-29T00:01:00Z"
    });
    const listCloudRunLogs = vi
      .fn<ConsoleApiClient["listCloudRunLogs"]>()
      .mockRejectedValue(new Error("Log refresh failed"));
    const apiClient = createMockApiClient({
      listTasks: vi
        .fn()
        .mockResolvedValue([{ ...taskCardFixture("Cloud log failure task"), id: "task_cloud" }]),
      startCloudRun,
      processCloudRun,
      cancelCloudRun,
      listCloudRunLogs
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run cloud" }));
    expect(
      await within(board).findByText("queued via fake on ai-scdc/task-cloud")
    ).toBeInTheDocument();

    await user.click(within(board).getByRole("button", { name: "Process" }));
    expect(await within(board).findByText("PATCH_READY")).toBeInTheDocument();
    expect(
      within(board).getByText("patch_ready via fake on ai-scdc/task-cloud")
    ).toBeInTheDocument();

    const cancelTask: TaskCard = {
      ...taskCardFixture("Cloud cancel log failure task"),
      id: "task_cancel_cloud",
      cloud_run: queuedRun
    };
    const cancelOnlyClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([cancelTask]),
      cancelCloudRun,
      listCloudRunLogs
    });
    render(<App apiClient={cancelOnlyClient} />);

    const boards = screen.getAllByLabelText("Task board");
    const cancelBoard = boards[boards.length - 1];
    await user.click(await within(cancelBoard).findByRole("button", { name: "Cancel" }));
    expect(
      await within(cancelBoard).findByText("cancelled via fake on ai-scdc/task-cloud")
    ).toBeInTheDocument();
  });

  it("keeps existing cloud run artifacts when manifest refresh fails", async () => {
    const user = userEvent.setup();
    const existingManifest = cloudRunArtifactManifestFixture();
    const task: TaskCard = {
      ...taskCardFixture("Cloud artifact fallback task"),
      id: "task_cloud",
      cloud_run: queuedCloudRunFixture(),
      cloud_run_artifact_manifest: existingManifest
    };
    const getCloudRunArtifactManifest = vi
      .fn<ConsoleApiClient["getCloudRunArtifactManifest"]>()
      .mockRejectedValue(new Error("Manifest refresh failed"));
    const processCloudRun = vi.fn<ConsoleApiClient["processCloudRun"]>().mockResolvedValue({
      cloud_run: cloudRunFixture(),
      patch_artifact: cloudPatchArtifactFixture()
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([task]),
      processCloudRun,
      getCloudRunArtifactManifest
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Process" }));

    expect(processCloudRun).toHaveBeenCalledWith("cloud_run_test");
    expect(getCloudRunArtifactManifest).toHaveBeenCalledWith("cloud_run_test");
    expect(await within(board).findByText("PATCH_READY")).toBeInTheDocument();
    expect(within(board).getByText("3 objects")).toBeInTheDocument();
    expect(within(board).getByText("development_default")).toBeInTheDocument();
    expect(within(board).getByRole("button", { name: "Unified diff" })).toBeInTheDocument();
    expect(
      within(board).getByText("local-inline://cloud-run-objects/diff_artifact_test")
    ).toBeInTheDocument();
  });

  it("runs a cloud task with sandbox profile keys after GitHub setup", async () => {
    const user = userEvent.setup();
    const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
      cloud_run: {
        ...queuedCloudRunFixture(),
        sandbox_kind: "docker_local",
        sandbox_profile_id: "sandbox_profile_test",
        patch_command_key: "write-note",
        test_command_keys: ["python-version"],
        command_results: []
      },
      patch_artifact: undefined
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
      repo_id: "repo_github_test",
      sandbox_profile_id: "sandbox_profile_test",
      patch_command_key: "write-note",
      test_command_keys: ["python-version"]
    });
    expect(
      await within(board).findByText("queued via docker_local on ai-scdc/task-cloud")
    ).toBeInTheDocument();
    expect(within(board).getByRole("button", { name: "Process" })).toBeInTheDocument();
  });

  it("clears stale sandbox profile state when replacement profile creation fails", async () => {
    const user = userEvent.setup();
    const createSandboxProfile = vi
      .fn<ConsoleApiClient["createSandboxProfile"]>()
      .mockResolvedValueOnce({
        id: "sandbox_profile_first",
        project_id: "project_demo",
        repo_id: "repo_github_test",
        name: "Default Docker profile",
        docker_image: "python:3.11-bookworm",
        patch_commands: [],
        test_commands: [],
        allowed_env_vars: [],
        network_enabled: true,
        status: "active"
      })
      .mockRejectedValueOnce(new Error("Profile create failed"));
    const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
      cloud_run: cloudRunFixture(),
      patch_artifact: cloudPatchArtifactFixture()
    });
    const apiClient = createMockApiClient({
      listTasks: vi
        .fn()
        .mockResolvedValue([
          { ...taskCardFixture("Cloud task after failed profile"), id: "task_cloud" }
        ]),
      createSandboxProfile,
      startCloudRun
    });

    render(<App apiClient={apiClient} />);

    const tokenInput = screen.getByLabelText("GitHub token");
    const connectButton = screen.getByRole("button", { name: "Connect GitHub repo" });

    await user.type(tokenInput, "ghp_first_token_1234");
    await user.click(connectButton);
    expect(
      await screen.findByText("Sandbox profile ready: python:3.11-bookworm")
    ).toBeInTheDocument();

    await user.type(tokenInput, "ghp_second_token_1234");
    await user.click(connectButton);
    expect(await screen.findByRole("alert")).toHaveTextContent("Profile create failed");
    expect(
      screen.queryByText("Sandbox profile ready: python:3.11-bookworm")
    ).not.toBeInTheDocument();

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run cloud" }));

    expect(startCloudRun).toHaveBeenCalledWith("task_cloud");
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
