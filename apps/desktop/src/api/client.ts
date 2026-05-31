import { demoTasks } from "../fixtures/demoData";

export type TaskCard = {
  id: string;
  title: string;
  status: string;
  role_required: string;
  assigned_agent: string;
  updated_at: string;
  repo_id?: string | null;
  branch_name?: string | null;
  worktree_ref?: string | null;
  patch_artifact?: PatchArtifactCard;
  test_run?: LocalTestRunCard;
  patch_review?: PatchReviewCard;
  patch_approval?: PatchApprovalCard;
  debug_attempt?: DebugAttemptCard | null;
  cloud_run?: CloudRunCard;
  pull_request?: PullRequestCard;
};

export type RepositoryCard = {
  id: string;
  project_id?: string;
  name: string;
  local_path: string;
  default_branch: string;
  status: string;
  provider?: string;
  repo_url?: string;
  github_owner?: string | null;
  github_repo?: string | null;
  github_credential_id?: string | null;
  connection_status?: string;
};

export type GitHubCredentialCard = {
  id: string;
  workspace_id?: string;
  display_name: string;
  token_last4: string;
  status: string;
  created_at?: string;
  updated_at?: string;
};

export type GitHubCredentialInput = {
  display_name: string;
  token: string;
};

export type GitHubRepositoryInput = {
  project_id?: string;
  name?: string;
  repo_url: string;
  github_owner: string;
  github_repo: string;
  default_branch?: string;
  github_credential_id: string;
};

export type SandboxCommandCard = {
  key: string;
  label: string;
  command: string;
  timeout_seconds: number;
  is_default: boolean;
};

export type SandboxProfileCard = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  name: string;
  docker_image: string;
  patch_commands: SandboxCommandCard[];
  test_commands: SandboxCommandCard[];
  allowed_env_vars: string[];
  network_enabled: boolean;
  created_at?: string;
  updated_at?: string;
};

export type SandboxProfileInput = {
  name: string;
  docker_image: string;
  patch_commands: SandboxCommandCard[];
  test_commands: SandboxCommandCard[];
  allowed_env_vars: string[];
  network_enabled: boolean;
};

export type CloudRunInput = {
  sandbox_profile_id?: string;
  patch_command_key?: string;
  test_command_keys?: string[];
};

export type LocalTaskRunCard = {
  id: string;
  task_id: string;
  repo_id: string;
  status: string;
  base_branch: string;
  worktree_path?: string | null;
  patch_artifact_id?: string | null;
  failure_reason?: string | null;
};

export type PatchArtifactCard = {
  id: string;
  task_id: string;
  local_run_id: string;
  summary: string;
  files_changed: string[];
  tests_run: string[];
  test_result: string;
  diff_text?: string;
};

export type CommandResultCard = {
  command: string;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  duration_ms: number;
};

export type LocalTestRunCard = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  task_id: string;
  local_run_id: string;
  patch_artifact_id: string | null;
  status: string;
  commands: string[];
  command_results: CommandResultCard[];
  failure_reason?: string | null;
  started_at: string;
  completed_at?: string | null;
  created_at: string;
};

export type PatchReviewCard = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  task_id: string;
  local_run_id: string;
  patch_artifact_id: string;
  test_run_id?: string | null;
  reviewer_kind: string;
  verdict: string;
  issues: Record<string, unknown>[];
  required_changes: string[];
  created_at: string;
};

export type PatchApprovalCard = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  task_id: string;
  local_run_id: string;
  patch_artifact_id: string;
  review_id: string;
  status: string;
  approved_by: string;
  merge_instructions: string;
  created_at: string;
};

export type DebugAttemptCard = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  task_id: string;
  patch_artifact_id: string;
  review_id?: string | null;
  test_run_id?: string | null;
  status: string;
  root_cause: string;
  fix_summary: string;
  created_at: string;
};

export type CloudRunCard = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  task_id: string;
  repo_id: string;
  local_run_id?: string | null;
  base_branch?: string;
  head_branch: string;
  status: string;
  sandbox_kind?: string | null;
  sandbox_profile_id?: string | null;
  patch_command_key?: string | null;
  test_command_keys?: string[];
  command_results?: CommandResultCard[];
  patch_artifact_id?: string | null;
  failure_reason?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type PullRequestCard = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  task_id: string;
  repo_id?: string;
  patch_artifact_id: string;
  patch_approval_id?: string;
  approval_id?: string;
  cloud_run_id?: string | null;
  head_branch?: string;
  base_branch?: string;
  github_pr_number?: number;
  number?: number;
  github_pr_url?: string;
  url: string;
  status: string;
  created_by?: string;
  created_at?: string;
};

export type LocalRunResult = {
  local_run: LocalTaskRunCard;
  patch_artifact?: PatchArtifactCard;
};

export type CloudRunResult = {
  cloud_run: CloudRunCard;
  patch_artifact?: PatchArtifactCard;
};

export type PullRequestResult = {
  task: TaskCard;
  patch_artifact?: PatchArtifactCard;
  approval?: PatchApprovalCard;
  pull_request: PullRequestCard;
};

export type PatchTestRunResult = {
  task: TaskCard;
  patch_artifact: PatchArtifactCard;
  test_run: LocalTestRunCard;
  debug_attempt: DebugAttemptCard | null;
};

export type PatchReviewResult = {
  task: TaskCard;
  patch_artifact: PatchArtifactCard;
  review: PatchReviewCard;
  debug_attempt: DebugAttemptCard | null;
};

export type PatchApprovalResult = {
  task: TaskCard;
  patch_artifact: PatchArtifactCard;
  review: PatchReviewCard;
  approval: PatchApprovalCard;
};

export type PlannerTaskDraftCard = {
  id: string;
  sequence: number;
  title: string;
  role_required: string;
  objective: string;
  acceptance_criteria: string[];
  allowed_paths: string[];
  required_tests: string[];
  risk_level: string;
};

export type PlannerRunDraft = {
  id: string;
  project_id: string;
  conversation_id?: string | null;
  goal: string;
  status: string;
  planner_kind: string;
  draft_count: number;
  drafts: PlannerTaskDraftCard[];
};

export type PlannerRunDecision = {
  planner_run_id: string;
  approval_id: string;
  status: string;
  created_tasks: TaskCard[];
};

export type ConsoleApiClient = {
  listTasks: () => Promise<TaskCard[]>;
  createTask: (goal: string) => Promise<TaskCard>;
  createPlannerRun: (goal: string) => Promise<PlannerRunDraft>;
  approvePlannerRun: (plannerRunId: string) => Promise<PlannerRunDecision>;
  rejectPlannerRun: (plannerRunId: string, reason?: string) => Promise<PlannerRunDecision>;
  createGitHubCredential: (input: GitHubCredentialInput) => Promise<GitHubCredentialCard>;
  listGitHubCredentials: () => Promise<GitHubCredentialCard[]>;
  createGitHubRepository: (input: GitHubRepositoryInput) => Promise<RepositoryCard>;
  createSandboxProfile: (
    projectId: string,
    input: SandboxProfileInput
  ) => Promise<SandboxProfileCard>;
  listSandboxProfiles: (projectId: string) => Promise<SandboxProfileCard[]>;
  startCloudRun: (taskId: string, input?: CloudRunInput) => Promise<CloudRunResult>;
  createPullRequest: (approvalId: string) => Promise<PullRequestResult>;
  startLocalRun: (taskId: string) => Promise<LocalRunResult>;
  runPatchTests: (patchArtifactId: string) => Promise<PatchTestRunResult>;
  reviewPatch: (patchArtifactId: string) => Promise<PatchReviewResult>;
  approvePatch: (patchArtifactId: string) => Promise<PatchApprovalResult>;
  requestHumanApproval: (approvalId: string) => Promise<PatchApprovalResult>;
};

type ApiProject = {
  id: string;
};

type ApiTask = {
  id: string;
  title: string;
  status: string;
  role_required: string;
  created_at?: string;
  updated_at?: string;
  repo_id?: string | null;
  branch_name?: string | null;
  worktree_ref?: string | null;
};

type ApiRepository = RepositoryCard;

type ApiGitHubCredential = GitHubCredentialCard;

type ApiSandboxProfile = SandboxProfileCard;

type ApiLocalTaskRun = LocalTaskRunCard;

type ApiCloudRun = CloudRunCard;

type ApiPatchArtifact = PatchArtifactCard & {
  workspace_id?: string;
  project_id?: string;
  risks?: string[];
  diff_text?: string;
  created_at?: string;
};

type ApiLocalTestRun = LocalTestRunCard;

type ApiPatchReview = PatchReviewCard;

type ApiPatchApproval = PatchApprovalCard;

type ApiDebugAttempt = DebugAttemptCard;

type ApiPullRequest = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  task_id: string;
  repo_id: string;
  patch_artifact_id: string;
  patch_approval_id: string;
  cloud_run_id: string | null;
  head_branch: string;
  base_branch: string;
  github_pr_number: number;
  github_pr_url: string;
  status: string;
  created_by: string;
  created_at: string;
};

type ApiPatchTestRunResult = {
  task: ApiTask;
  patch_artifact: ApiPatchArtifact;
  test_run: ApiLocalTestRun;
  debug_attempt: ApiDebugAttempt | null;
};

type ApiPatchReviewResult = {
  task: ApiTask;
  patch_artifact: ApiPatchArtifact;
  review: ApiPatchReview;
  debug_attempt: ApiDebugAttempt | null;
};

type ApiPatchApprovalResult = {
  task: ApiTask;
  patch_artifact: ApiPatchArtifact;
  review: ApiPatchReview;
  approval: ApiPatchApproval;
};

type ApiCloudRunResult = {
  cloud_run: ApiCloudRun;
  patch_artifact?: ApiPatchArtifact | null;
};

type ApiPullRequestResult = {
  task: ApiTask;
  patch_artifact?: ApiPatchArtifact;
  approval?: ApiPatchApproval;
  pull_request: ApiPullRequest;
};

type ApiPlannerRunDecision = {
  planner_run_id: string;
  approval_id: string;
  status: string;
  created_tasks: ApiTask[];
};

type HttpApiClientOptions = {
  baseUrl: string;
  projectId?: string;
};

function demoPlannerDrafts(goal: string): PlannerTaskDraftCard[] {
  return [
    {
      id: "planner_draft_frontend",
      sequence: 1,
      title: "Design desktop flow for planner approval",
      role_required: "frontend",
      objective: `Design the desktop planner approval flow for ${goal}.`,
      acceptance_criteria: ["Planner drafts are visible before task creation."],
      allowed_paths: ["apps/desktop/**"],
      required_tests: ["Desktop planner draft client contract is covered."],
      risk_level: "medium"
    },
    {
      id: "planner_draft_backend",
      sequence: 2,
      title: "Implement planner approval API",
      role_required: "backend",
      objective: `Implement the planner approval API for ${goal}.`,
      acceptance_criteria: ["Approved planner drafts create task cards."],
      allowed_paths: ["apps/api/**"],
      required_tests: ["Planner approval API creates tasks."],
      risk_level: "medium"
    }
  ];
}

function fakePatchArtifactId(taskId: string) {
  return `patch_${taskId}`;
}

function fakeLocalRunId(taskId: string) {
  return `local_run_${taskId}`;
}

const fakeSandboxProfilesByProject = new Map<string, SandboxProfileCard[]>();

function fakeTaskFromPatchArtifact(patchArtifactId: string) {
  const demoTask = demoTasks.find((item) => item.patch_artifact?.id === patchArtifactId);
  if (demoTask?.patch_artifact) {
    return {
      task: demoTask,
      patchArtifact: demoTask.patch_artifact
    };
  }

  const taskId = patchArtifactId.startsWith("patch_cloud_")
    ? patchArtifactId.slice("patch_cloud_".length)
    : patchArtifactId.startsWith("patch_")
    ? patchArtifactId.slice("patch_".length)
    : "task_demo_created";
  const localRunId = patchArtifactId.startsWith("patch_cloud_")
    ? `cloud_run_${taskId}`
    : fakeLocalRunId(taskId);
  const task: TaskCard = {
    id: taskId,
    title: "Prepared local runner patch",
    status: "PATCH_READY",
    role_required: "frontend",
    assigned_agent: "Frontend Engineer",
    updated_at: "2026-05-29T00:00:00Z"
  };
  return {
    task,
    patchArtifact: {
      id: patchArtifactId,
      task_id: taskId,
      local_run_id: localRunId,
      summary: "Prepared local runner patch.",
      files_changed: ["README.md"],
      tests_run: ["pnpm --filter @ai-scdc/desktop test"],
      test_result: "not_run"
    }
  };
}

export const fakeApiClient: ConsoleApiClient = {
  async listTasks() {
    return [...demoTasks];
  },
  async createTask() {
    return {
      id: "task_demo_created",
      title: "Build task board",
      status: "CREATED",
      role_required: "frontend",
      assigned_agent: "Frontend Engineer",
      updated_at: "2026-05-29T00:00:00Z"
    };
  },
  async createPlannerRun(goal: string) {
    const drafts = demoPlannerDrafts(goal);
    return {
      id: "planner_run_demo",
      project_id: "project_demo",
      conversation_id: null,
      goal,
      status: "DRAFTED",
      planner_kind: "fake",
      draft_count: drafts.length,
      drafts
    };
  },
  async approvePlannerRun(plannerRunId: string) {
    return {
      planner_run_id: plannerRunId,
      approval_id: "approval_demo",
      status: "APPROVED",
      created_tasks: [
        {
          id: "task_planner_frontend",
          title: "Design desktop flow for planner approval",
          status: "CREATED",
          role_required: "frontend",
          assigned_agent: "Frontend Engineer",
          updated_at: "2026-05-29T00:00:00Z"
        },
        {
          id: "task_planner_backend",
          title: "Implement planner approval API",
          status: "CREATED",
          role_required: "backend",
          assigned_agent: "Backend Engineer",
          updated_at: "2026-05-29T00:00:00Z"
        }
      ]
    };
  },
  async rejectPlannerRun(plannerRunId: string) {
    return {
      planner_run_id: plannerRunId,
      approval_id: "approval_demo",
      status: "REJECTED",
      created_tasks: []
    };
  },
  async createGitHubCredential(input: GitHubCredentialInput) {
    return {
      id: "github_credential_demo",
      workspace_id: "workspace_demo",
      display_name: input.display_name,
      token_last4: input.token.slice(-4),
      status: "active",
      created_at: "2026-05-29T00:00:00Z",
      updated_at: "2026-05-29T00:00:00Z"
    };
  },
  async listGitHubCredentials() {
    return [
      {
        id: "github_credential_demo",
        workspace_id: "workspace_demo",
        display_name: "Example GitHub",
        token_last4: "7890",
        status: "active",
        created_at: "2026-05-29T00:00:00Z",
        updated_at: "2026-05-29T00:00:00Z"
      }
    ];
  },
  async createGitHubRepository(input: GitHubRepositoryInput) {
    return {
      id: "repo_github_demo",
      project_id: input.project_id ?? "project_demo",
      name: input.name ?? `${input.github_owner}/${input.github_repo}`,
      local_path: "",
      default_branch: input.default_branch ?? "main",
      status: "active",
      provider: "github",
      repo_url: input.repo_url,
      github_owner: input.github_owner,
      github_repo: input.github_repo,
      github_credential_id: input.github_credential_id,
      connection_status: "connected"
    };
  },
  async createSandboxProfile(projectId: string, input: SandboxProfileInput) {
    const profiles = fakeSandboxProfilesByProject.get(projectId) ?? [];
    const profile: SandboxProfileCard = {
      id: `sandbox_profile_${projectId}_${profiles.length + 1}`,
      project_id: projectId,
      ...input,
      created_at: "2026-05-29T00:00:00Z",
      updated_at: "2026-05-29T00:00:00Z"
    };
    fakeSandboxProfilesByProject.set(projectId, [...profiles, profile]);
    return profile;
  },
  async listSandboxProfiles(projectId: string) {
    return [...(fakeSandboxProfilesByProject.get(projectId) ?? [])];
  },
  async startCloudRun(taskId: string, input: CloudRunInput = {}) {
    const cloudRunId = `cloud_run_${taskId}`;
    const patchArtifactId = `patch_cloud_${taskId}`;
    const usesSandboxProfile = Boolean(input.sandbox_profile_id);
    return {
      cloud_run: {
        id: cloudRunId,
        workspace_id: "workspace_demo",
        project_id: "project_demo",
        task_id: taskId,
        repo_id: "repo_github_demo",
        local_run_id: cloudRunId,
        base_branch: "main",
        head_branch: `ai-scdc/${taskId}`,
        status: "patch_ready",
        sandbox_kind: usesSandboxProfile ? "docker_local" : "fake",
        sandbox_profile_id: input.sandbox_profile_id ?? null,
        patch_command_key: input.patch_command_key ?? null,
        test_command_keys: input.test_command_keys ?? [],
        command_results: [],
        patch_artifact_id: patchArtifactId,
        failure_reason: null,
        created_at: "2026-05-29T00:00:00Z",
        updated_at: "2026-05-29T00:00:00Z"
      },
      patch_artifact: {
        id: patchArtifactId,
        task_id: taskId,
        local_run_id: cloudRunId,
        summary: "Prepared cloud runner patch.",
        files_changed: ["README.md"],
        tests_run: [],
        test_result: "not_run"
      }
    };
  },
  async createPullRequest(approvalId: string) {
    const patchArtifactId = approvalId.replace(/^patch_approval_/, "");
    const { task, patchArtifact } = fakeTaskFromPatchArtifact(patchArtifactId);
    return {
      task: {
        ...task,
        status: "PR_CREATED",
        patch_artifact: patchArtifact
      },
      patch_artifact: patchArtifact,
      approval: {
        id: approvalId,
        workspace_id: "workspace_demo",
        project_id: "project_demo",
        task_id: task.id,
        local_run_id: patchArtifact.local_run_id,
        patch_artifact_id: patchArtifact.id,
        review_id: `review_${patchArtifact.id}`,
        status: "approved",
        approved_by: "dev_user",
        merge_instructions:
          "Inspect the pull request before merging. This workflow does not run git merge.",
        created_at: "2026-05-29T00:04:00Z"
      },
      pull_request: {
        id: "pull_request_demo",
        workspace_id: "workspace_demo",
        project_id: "project_demo",
        task_id: task.id,
        repo_id: "repo_github_demo",
        patch_artifact_id: patchArtifact.id,
        patch_approval_id: approvalId,
        approval_id: approvalId,
        cloud_run_id: patchArtifact.local_run_id,
        head_branch: `ai-scdc/${task.id}`,
        base_branch: "main",
        github_pr_number: 1,
        number: 1,
        github_pr_url: "https://github.com/example/demo/pull/1",
        url: "https://github.com/example/demo/pull/1",
        status: "created",
        created_by: "dev_user",
        created_at: "2026-05-29T00:05:00Z"
      }
    };
  },
  async startLocalRun(taskId: string) {
    const patchArtifactId = fakePatchArtifactId(taskId);
    const localRunId = fakeLocalRunId(taskId);
    return {
      local_run: {
        id: localRunId,
        task_id: taskId,
        repo_id: "repo_demo",
        status: "patch_ready",
        base_branch: "main",
        worktree_path: `.worktrees/${taskId}`,
        patch_artifact_id: patchArtifactId,
        failure_reason: null
      },
      patch_artifact: {
        id: patchArtifactId,
        task_id: taskId,
        local_run_id: localRunId,
        summary: "Prepared local runner patch.",
        files_changed: ["README.md"],
        tests_run: [],
        test_result: "not_run"
      }
    };
  },
  async runPatchTests(patchArtifactId: string) {
    const { task, patchArtifact: basePatchArtifact } = fakeTaskFromPatchArtifact(patchArtifactId);
    const patchArtifact = {
      ...basePatchArtifact,
      test_result: "passed"
    };
    const firstCommand =
      patchArtifact.tests_run[0] ?? "pnpm --filter @ai-scdc/desktop test";
    const testRun: LocalTestRunCard = {
      id: "test_run_demo",
      workspace_id: "workspace_demo",
      project_id: "project_demo",
      task_id: task.id,
      local_run_id: patchArtifact.local_run_id,
      patch_artifact_id: patchArtifact.id,
      status: "passed",
      commands: patchArtifact.tests_run.length > 0 ? patchArtifact.tests_run : [firstCommand],
      command_results: [
        {
          command: firstCommand,
          exit_code: 0,
          stdout: "1 passed",
          stderr: "",
          duration_ms: 1000
        }
      ],
      failure_reason: null,
      started_at: "2026-05-29T00:01:00Z",
      completed_at: "2026-05-29T00:02:00Z",
      created_at: "2026-05-29T00:01:00Z"
    };
    return {
      task: {
        ...task,
        status: "REVIEWING",
        patch_artifact: patchArtifact,
        test_run: testRun,
        debug_attempt: null
      },
      patch_artifact: patchArtifact,
      test_run: testRun,
      debug_attempt: null
    };
  },
  async reviewPatch(patchArtifactId: string) {
    const { task, patchArtifact: basePatchArtifact } = fakeTaskFromPatchArtifact(patchArtifactId);
    const patchArtifact = {
      ...basePatchArtifact,
      test_result: "passed"
    };
    const review: PatchReviewCard = {
      id: "review_demo",
      workspace_id: "workspace_demo",
      project_id: "project_demo",
      task_id: task.id,
      local_run_id: patchArtifact.local_run_id,
      patch_artifact_id: patchArtifact.id,
      test_run_id: task.test_run?.id ?? "test_run_demo",
      reviewer_kind: "deterministic",
      verdict: "approved",
      issues: [],
      required_changes: [],
      created_at: "2026-05-29T00:03:00Z"
    };
    return {
      task: {
        ...task,
        status: "APPROVED",
        patch_artifact: patchArtifact,
        patch_review: review,
        debug_attempt: null
      },
      patch_artifact: patchArtifact,
      review,
      debug_attempt: null
    };
  },
  async approvePatch(patchArtifactId: string) {
    const { task, patchArtifact: basePatchArtifact } = fakeTaskFromPatchArtifact(patchArtifactId);
    const patchArtifact = {
      ...basePatchArtifact,
      test_result: "passed"
    };
    const review: PatchReviewCard = {
      id: `review_${patchArtifact.id}`,
      workspace_id: "workspace_demo",
      project_id: "project_demo",
      task_id: task.id,
      local_run_id: patchArtifact.local_run_id,
      patch_artifact_id: patchArtifact.id,
      test_run_id: task.test_run?.id ?? "test_run_demo",
      reviewer_kind: "deterministic",
      verdict: "approved",
      issues: [],
      required_changes: [],
      created_at: "2026-05-29T00:03:00Z"
    };
    const approval: PatchApprovalCard = {
      id: `patch_approval_${patchArtifact.id}`,
      workspace_id: "workspace_demo",
      project_id: "project_demo",
      task_id: task.id,
      local_run_id: patchArtifact.local_run_id,
      patch_artifact_id: patchArtifact.id,
      review_id: review.id,
      status: "approved",
      approved_by: "dev_user",
      merge_instructions:
        "Inspect the worktree before merging. This workflow does not run git merge.",
      created_at: "2026-05-29T00:04:00Z"
    };
    return {
      task: {
        ...task,
        status: "MERGE_READY",
        patch_artifact: patchArtifact,
        patch_review: review,
        patch_approval: approval
      },
      patch_artifact: patchArtifact,
      review,
      approval
    };
  },
  async requestHumanApproval(approvalId: string) {
    const patchArtifactId = approvalId.replace(/^patch_approval_/, "");
    const result = await this.approvePatch(patchArtifactId);
    const approval = {
      ...result.approval,
      id: approvalId
    };
    return {
      ...result,
      task: {
        ...result.task,
        status: "HUMAN_APPROVAL",
        patch_approval: approval
      },
      approval
    };
  }
};

function apiUrl(baseUrl: string, path: string) {
  return `${baseUrl.replace(/\/+$/, "")}${path}`;
}

async function readJsonResponse<T>(response: Response, context: string): Promise<T> {
  if (response.ok) {
    return (await response.json()) as T;
  }

  const detail = await readErrorDetail(response);
  throw new Error(
    `${context} failed with ${response.status} ${response.statusText}${detail ? `: ${detail}` : ""}`
  );
}

async function readErrorDetail(response: Response) {
  const contentType = response.headers.get("Content-Type") ?? "";
  if (!contentType.includes("application/json")) {
    return response.text();
  }

  const body = (await response.json().catch(() => undefined)) as
    | { detail?: unknown }
    | undefined;
  if (typeof body?.detail === "string") {
    return body.detail;
  }
  if (
    typeof body?.detail === "object" &&
    body.detail !== null &&
    "message" in body.detail &&
    typeof body.detail.message === "string"
  ) {
    return body.detail.message;
  }
  if (Array.isArray(body?.detail)) {
    return body.detail
      .map((item) =>
        typeof item === "object" && item !== null && "msg" in item ? item.msg : undefined
      )
      .filter((message): message is string => typeof message === "string")
      .join("; ");
  }

  return body ? JSON.stringify(body) : "";
}

function agentNameForRole(role: string) {
  if (role === "frontend") {
    return "Frontend Engineer";
  }
  if (role === "backend") {
    return "Backend Engineer";
  }
  return "Planner";
}

function mapTaskCard(task: ApiTask): TaskCard {
  const card: TaskCard = {
    id: task.id,
    title: task.title,
    status: task.status,
    role_required: task.role_required,
    assigned_agent: agentNameForRole(task.role_required),
    updated_at: task.updated_at ?? task.created_at ?? ""
  };
  if (task.repo_id !== undefined) {
    card.repo_id = task.repo_id;
  }
  if (task.branch_name !== undefined) {
    card.branch_name = task.branch_name;
  }
  if (task.worktree_ref !== undefined) {
    card.worktree_ref = task.worktree_ref;
  }
  return card;
}

function mapPlannerRunDecision(decision: ApiPlannerRunDecision): PlannerRunDecision {
  return {
    planner_run_id: decision.planner_run_id,
    approval_id: decision.approval_id,
    status: decision.status,
    created_tasks: decision.created_tasks.map(mapTaskCard)
  };
}

function mapPatchArtifactCard(artifact: ApiPatchArtifact): PatchArtifactCard {
  return {
    id: artifact.id,
    task_id: artifact.task_id,
    local_run_id: artifact.local_run_id,
    summary: artifact.summary,
    files_changed: artifact.files_changed,
    tests_run: artifact.tests_run,
    test_result: artifact.test_result,
    diff_text: artifact.diff_text
  };
}

function mapLocalTestRunCard(testRun: ApiLocalTestRun): LocalTestRunCard {
  return {
    id: testRun.id,
    workspace_id: testRun.workspace_id,
    project_id: testRun.project_id,
    task_id: testRun.task_id,
    local_run_id: testRun.local_run_id,
    patch_artifact_id: testRun.patch_artifact_id,
    status: testRun.status,
    commands: testRun.commands,
    command_results: testRun.command_results,
    failure_reason: testRun.failure_reason,
    started_at: testRun.started_at,
    completed_at: testRun.completed_at,
    created_at: testRun.created_at
  };
}

function mapSandboxProfileCard(profile: ApiSandboxProfile): SandboxProfileCard {
  return {
    id: profile.id,
    workspace_id: profile.workspace_id,
    project_id: profile.project_id,
    name: profile.name,
    docker_image: profile.docker_image,
    patch_commands: profile.patch_commands,
    test_commands: profile.test_commands,
    allowed_env_vars: profile.allowed_env_vars,
    network_enabled: profile.network_enabled,
    created_at: profile.created_at,
    updated_at: profile.updated_at
  };
}

function mapPatchReviewCard(review: ApiPatchReview): PatchReviewCard {
  return {
    id: review.id,
    workspace_id: review.workspace_id,
    project_id: review.project_id,
    task_id: review.task_id,
    local_run_id: review.local_run_id,
    patch_artifact_id: review.patch_artifact_id,
    test_run_id: review.test_run_id,
    reviewer_kind: review.reviewer_kind,
    verdict: review.verdict,
    issues: review.issues,
    required_changes: review.required_changes,
    created_at: review.created_at
  };
}

function mapPatchApprovalCard(approval: ApiPatchApproval): PatchApprovalCard {
  return {
    id: approval.id,
    workspace_id: approval.workspace_id,
    project_id: approval.project_id,
    task_id: approval.task_id,
    local_run_id: approval.local_run_id,
    patch_artifact_id: approval.patch_artifact_id,
    review_id: approval.review_id,
    status: approval.status,
    approved_by: approval.approved_by,
    merge_instructions: approval.merge_instructions,
    created_at: approval.created_at
  };
}

function mapDebugAttemptCard(debugAttempt: ApiDebugAttempt): DebugAttemptCard {
  return {
    id: debugAttempt.id,
    workspace_id: debugAttempt.workspace_id,
    project_id: debugAttempt.project_id,
    task_id: debugAttempt.task_id,
    patch_artifact_id: debugAttempt.patch_artifact_id,
    review_id: debugAttempt.review_id,
    test_run_id: debugAttempt.test_run_id,
    status: debugAttempt.status,
    root_cause: debugAttempt.root_cause,
    fix_summary: debugAttempt.fix_summary,
    created_at: debugAttempt.created_at
  };
}

function mapCloudRunCard(cloudRun: ApiCloudRun): CloudRunCard {
  return {
    id: cloudRun.id,
    workspace_id: cloudRun.workspace_id,
    project_id: cloudRun.project_id,
    task_id: cloudRun.task_id,
    repo_id: cloudRun.repo_id,
    local_run_id: cloudRun.local_run_id,
    base_branch: cloudRun.base_branch,
    head_branch: cloudRun.head_branch,
    status: cloudRun.status,
    sandbox_kind: cloudRun.sandbox_kind,
    sandbox_profile_id: cloudRun.sandbox_profile_id,
    patch_command_key: cloudRun.patch_command_key,
    test_command_keys: cloudRun.test_command_keys,
    command_results: cloudRun.command_results,
    patch_artifact_id: cloudRun.patch_artifact_id,
    failure_reason: cloudRun.failure_reason,
    created_at: cloudRun.created_at,
    updated_at: cloudRun.updated_at
  };
}

function mapPullRequestCard(pullRequest: ApiPullRequest): PullRequestCard {
  return {
    id: pullRequest.id,
    workspace_id: pullRequest.workspace_id,
    project_id: pullRequest.project_id,
    task_id: pullRequest.task_id,
    repo_id: pullRequest.repo_id,
    patch_artifact_id: pullRequest.patch_artifact_id,
    patch_approval_id: pullRequest.patch_approval_id,
    approval_id: pullRequest.patch_approval_id,
    cloud_run_id: pullRequest.cloud_run_id,
    head_branch: pullRequest.head_branch,
    base_branch: pullRequest.base_branch,
    github_pr_number: pullRequest.github_pr_number,
    number: pullRequest.github_pr_number,
    github_pr_url: pullRequest.github_pr_url,
    url: pullRequest.github_pr_url,
    status: pullRequest.status,
    created_by: pullRequest.created_by,
    created_at: pullRequest.created_at
  };
}

function mapPatchTestRunResult(result: ApiPatchTestRunResult): PatchTestRunResult {
  return {
    task: mapTaskCard(result.task),
    patch_artifact: mapPatchArtifactCard(result.patch_artifact),
    test_run: mapLocalTestRunCard(result.test_run),
    debug_attempt: result.debug_attempt ? mapDebugAttemptCard(result.debug_attempt) : null
  };
}

function mapPatchReviewResult(result: ApiPatchReviewResult): PatchReviewResult {
  return {
    task: mapTaskCard(result.task),
    patch_artifact: mapPatchArtifactCard(result.patch_artifact),
    review: mapPatchReviewCard(result.review),
    debug_attempt: result.debug_attempt ? mapDebugAttemptCard(result.debug_attempt) : null
  };
}

function mapPatchApprovalResult(result: ApiPatchApprovalResult): PatchApprovalResult {
  return {
    task: mapTaskCard(result.task),
    patch_artifact: mapPatchArtifactCard(result.patch_artifact),
    review: mapPatchReviewCard(result.review),
    approval: mapPatchApprovalCard(result.approval)
  };
}

function mapCloudRunResult(result: ApiCloudRunResult): CloudRunResult {
  return {
    cloud_run: mapCloudRunCard(result.cloud_run),
    patch_artifact: result.patch_artifact ? mapPatchArtifactCard(result.patch_artifact) : undefined
  };
}

function mapPullRequestResult(result: ApiPullRequestResult): PullRequestResult {
  return {
    task: mapTaskCard(result.task),
    patch_artifact: result.patch_artifact
      ? mapPatchArtifactCard(result.patch_artifact)
      : undefined,
    approval: result.approval ? mapPatchApprovalCard(result.approval) : undefined,
    pull_request: mapPullRequestCard(result.pull_request)
  };
}

export function createHttpApiClient(options: HttpApiClientOptions): ConsoleApiClient {
  let resolvedProjectId = options.projectId;
  const workflowStatuses = new Set([
    "PATCH_READY",
    "SELF_TESTING",
    "REVIEWING",
    "FIX_REQUESTED",
    "APPROVED",
    "MERGE_READY",
    "HUMAN_APPROVAL",
    "PR_CREATED"
  ]);

  async function getProjectId() {
    if (resolvedProjectId) {
      return resolvedProjectId;
    }

    const projectsResponse = await fetch(apiUrl(options.baseUrl, "/projects"));
    const projects = await readJsonResponse<ApiProject[]>(projectsResponse, "GET /projects");
    if (projects[0]?.id) {
      resolvedProjectId = projects[0].id;
      return resolvedProjectId;
    }

    const createProjectResponse = await fetch(apiUrl(options.baseUrl, "/projects"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "Demo Project",
        description: "Phase 0 desktop integration project"
      })
    });
    const project = await readJsonResponse<ApiProject>(createProjectResponse, "POST /projects");
    resolvedProjectId = project.id;
    return resolvedProjectId;
  }

  function shouldHydrateWorkflow(task: ApiTask) {
    return workflowStatuses.has(task.status);
  }

  async function listJson<T>(path: string, context: string): Promise<T[]> {
    const response = await fetch(apiUrl(options.baseUrl, path));
    return readJsonResponse<T[]>(response, context);
  }

  function latest<T>(items: T[]): T | undefined {
    return items[items.length - 1];
  }

  async function hydrateTaskWorkflow(task: ApiTask): Promise<TaskCard> {
    const taskCard = mapTaskCard(task);
    if (!shouldHydrateWorkflow(task)) {
      return taskCard;
    }

    const [localRuns, cloudRuns] = await Promise.all([
      listJson<ApiLocalTaskRun>(
        `/tasks/${task.id}/local-runs`,
        `GET /tasks/${task.id}/local-runs`
      ),
      listJson<ApiCloudRun>(
        `/tasks/${task.id}/cloud-runs`,
        `GET /tasks/${task.id}/cloud-runs`
      )
    ]);
    const localRun = [...localRuns].reverse().find((item) => item.patch_artifact_id);
    const cloudRun = [...cloudRuns].reverse().find((item) => item.patch_artifact_id);
    const cloudPatchArtifactId = cloudRun?.patch_artifact_id;
    const localPatchArtifactId = localRun?.patch_artifact_id;
    const selectedRun = cloudPatchArtifactId
      ? { source: "cloud" as const, run: cloudRun, patchArtifactId: cloudPatchArtifactId }
      : localPatchArtifactId
      ? { source: "local" as const, run: localRun, patchArtifactId: localPatchArtifactId }
      : undefined;
    if (!selectedRun) {
      return taskCard;
    }

    const artifactResponse = await fetch(
      apiUrl(options.baseUrl, `/patch-artifacts/${selectedRun.patchArtifactId}`)
    );
    const artifact = await readJsonResponse<ApiPatchArtifact>(
      artifactResponse,
      `GET /patch-artifacts/${selectedRun.patchArtifactId}`
    );
    const [testRuns, reviews, approvals, pullRequests] = await Promise.all([
      listJson<ApiLocalTestRun>(
        `/patch-artifacts/${artifact.id}/test-runs`,
        `GET /patch-artifacts/${artifact.id}/test-runs`
      ),
      listJson<ApiPatchReview>(
        `/patch-artifacts/${artifact.id}/reviews`,
        `GET /patch-artifacts/${artifact.id}/reviews`
      ),
      listJson<ApiPatchApproval>(
        `/patch-artifacts/${artifact.id}/approvals`,
        `GET /patch-artifacts/${artifact.id}/approvals`
      ),
      listJson<ApiPullRequest>(
        `/patch-artifacts/${artifact.id}/pull-requests`,
        `GET /patch-artifacts/${artifact.id}/pull-requests`
      )
    ]);
    const latestTestRun = latest(testRuns);
    const latestReview = latest(reviews);
    const latestApproval = latest(approvals);
    const latestPullRequest = latest(pullRequests);

    return {
      ...taskCard,
      repo_id: selectedRun.run.repo_id,
      branch_name:
        selectedRun.source === "cloud"
          ? selectedRun.run.head_branch
          : selectedRun.run.base_branch,
      worktree_ref:
        selectedRun.source === "local" ? selectedRun.run.worktree_path : undefined,
      patch_artifact: mapPatchArtifactCard(artifact),
      test_run: latestTestRun ? mapLocalTestRunCard(latestTestRun) : undefined,
      patch_review: latestReview ? mapPatchReviewCard(latestReview) : undefined,
      patch_approval: latestApproval ? mapPatchApprovalCard(latestApproval) : undefined,
      cloud_run:
        selectedRun.source === "cloud" ? mapCloudRunCard(selectedRun.run) : undefined,
      pull_request: latestPullRequest ? mapPullRequestCard(latestPullRequest) : undefined
    };
  }

  return {
    async listTasks() {
      const projectId = await getProjectId();
      const response = await fetch(apiUrl(options.baseUrl, `/projects/${projectId}/tasks`));
      const tasks = await readJsonResponse<ApiTask[]>(
        response,
        `GET /projects/${projectId}/tasks`
      );
      return Promise.all(tasks.map(hydrateTaskWorkflow));
    },
    async createTask(goal: string) {
      const projectId = await getProjectId();
      const response = await fetch(apiUrl(options.baseUrl, `/projects/${projectId}/tasks`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: goal,
          description: "Created from desktop shell.",
          role_required: "frontend",
          risk_level: "medium",
          acceptance_criteria: ["Task is visible in the desktop context panel."]
        })
      });
      const task = await readJsonResponse<ApiTask>(
        response,
        `POST /projects/${projectId}/tasks`
      );
      return mapTaskCard(task);
    },
    async createPlannerRun(goal: string) {
      const projectId = await getProjectId();
      const response = await fetch(apiUrl(options.baseUrl, `/projects/${projectId}/planner-runs`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal })
      });
      return readJsonResponse<PlannerRunDraft>(
        response,
        `POST /projects/${projectId}/planner-runs`
      );
    },
    async approvePlannerRun(plannerRunId: string) {
      const response = await fetch(apiUrl(options.baseUrl, `/planner-runs/${plannerRunId}/approve`), {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
      const decision = await readJsonResponse<ApiPlannerRunDecision>(
        response,
        `POST /planner-runs/${plannerRunId}/approve`
      );
      return mapPlannerRunDecision(decision);
    },
    async rejectPlannerRun(plannerRunId: string, reason = "") {
      const response = await fetch(apiUrl(options.baseUrl, `/planner-runs/${plannerRunId}/reject`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason })
      });
      const decision = await readJsonResponse<ApiPlannerRunDecision>(
        response,
        `POST /planner-runs/${plannerRunId}/reject`
      );
      return mapPlannerRunDecision(decision);
    },
    async createGitHubCredential(input: GitHubCredentialInput) {
      const response = await fetch(apiUrl(options.baseUrl, "/github-credentials"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input)
      });
      return readJsonResponse<ApiGitHubCredential>(response, "POST /github-credentials");
    },
    async listGitHubCredentials() {
      const response = await fetch(apiUrl(options.baseUrl, "/github-credentials"));
      return readJsonResponse<ApiGitHubCredential[]>(response, "GET /github-credentials");
    },
    async createGitHubRepository(input: GitHubRepositoryInput) {
      const projectId = input.project_id ?? (await getProjectId());
      const body = {
        project_id: projectId,
        name: input.name ?? `${input.github_owner}/${input.github_repo}`,
        github_credential_id: input.github_credential_id,
        repo_url: input.repo_url,
        github_owner: input.github_owner,
        github_repo: input.github_repo,
        default_branch: input.default_branch ?? "main"
      };
      const response = await fetch(
        apiUrl(options.baseUrl, `/projects/${projectId}/github-repositories`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        }
      );
      return readJsonResponse<ApiRepository>(
        response,
        `POST /projects/${projectId}/github-repositories`
      );
    },
    async createSandboxProfile(projectId: string, input: SandboxProfileInput) {
      const response = await fetch(
        apiUrl(options.baseUrl, `/projects/${projectId}/sandbox-profiles`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(input)
        }
      );
      const profile = await readJsonResponse<ApiSandboxProfile>(
        response,
        `POST /projects/${projectId}/sandbox-profiles`
      );
      return mapSandboxProfileCard(profile);
    },
    async listSandboxProfiles(projectId: string) {
      const response = await fetch(
        apiUrl(options.baseUrl, `/projects/${projectId}/sandbox-profiles`)
      );
      const profiles = await readJsonResponse<ApiSandboxProfile[]>(
        response,
        `GET /projects/${projectId}/sandbox-profiles`
      );
      return profiles.map(mapSandboxProfileCard);
    },
    async startCloudRun(taskId: string, input: CloudRunInput = {}) {
      const projectId = await getProjectId();
      const repositoriesResponse = await fetch(
        apiUrl(options.baseUrl, `/projects/${projectId}/repositories`)
      );
      const repositories = await readJsonResponse<ApiRepository[]>(
        repositoriesResponse,
        `GET /projects/${projectId}/repositories`
      );
      const repository =
        repositories.find((item) => item.provider === "github" && item.status === "active") ??
        repositories.find((item) => item.provider === "github") ??
        repositories[0];
      if (!repository) {
        throw new Error("No repository registered for project");
      }

      const response = await fetch(apiUrl(options.baseUrl, `/tasks/${taskId}/cloud-runs`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_id: repository.id, ...input })
      });
      const result = await readJsonResponse<ApiCloudRunResult>(
        response,
        `POST /tasks/${taskId}/cloud-runs`
      );
      return mapCloudRunResult(result);
    },
    async createPullRequest(approvalId: string) {
      const response = await fetch(
        apiUrl(options.baseUrl, `/patch-approvals/${approvalId}/pull-requests`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        }
      );
      const result = await readJsonResponse<ApiPullRequestResult>(
        response,
        `POST /patch-approvals/${approvalId}/pull-requests`
      );
      return mapPullRequestResult(result);
    },
    async startLocalRun(taskId: string) {
      const projectId = await getProjectId();
      const repositoriesResponse = await fetch(
        apiUrl(options.baseUrl, `/projects/${projectId}/repositories`)
      );
      const repositories = await readJsonResponse<ApiRepository[]>(
        repositoriesResponse,
        `GET /projects/${projectId}/repositories`
      );
      const repository = repositories.find((item) => item.status === "active") ?? repositories[0];
      if (!repository) {
        throw new Error("No repository registered for project");
      }

      const runResponse = await fetch(apiUrl(options.baseUrl, `/tasks/${taskId}/local-runs`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_id: repository.id })
      });
      const localRun = await readJsonResponse<ApiLocalTaskRun>(
        runResponse,
        `POST /tasks/${taskId}/local-runs`
      );
      let patchArtifact: PatchArtifactCard | undefined;
      if (localRun.patch_artifact_id) {
        const artifactResponse = await fetch(
          apiUrl(options.baseUrl, `/patch-artifacts/${localRun.patch_artifact_id}`)
        );
        const artifact = await readJsonResponse<ApiPatchArtifact>(
          artifactResponse,
          `GET /patch-artifacts/${localRun.patch_artifact_id}`
        );
        patchArtifact = mapPatchArtifactCard(artifact);
      }
      return { local_run: localRun, patch_artifact: patchArtifact };
    },
    async runPatchTests(patchArtifactId: string) {
      const response = await fetch(
        apiUrl(options.baseUrl, `/patch-artifacts/${patchArtifactId}/test-runs`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        }
      );
      const result = await readJsonResponse<ApiPatchTestRunResult>(
        response,
        `POST /patch-artifacts/${patchArtifactId}/test-runs`
      );
      return mapPatchTestRunResult(result);
    },
    async reviewPatch(patchArtifactId: string) {
      const response = await fetch(
        apiUrl(options.baseUrl, `/patch-artifacts/${patchArtifactId}/reviews`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        }
      );
      const result = await readJsonResponse<ApiPatchReviewResult>(
        response,
        `POST /patch-artifacts/${patchArtifactId}/reviews`
      );
      return mapPatchReviewResult(result);
    },
    async approvePatch(patchArtifactId: string) {
      const response = await fetch(
        apiUrl(options.baseUrl, `/patch-artifacts/${patchArtifactId}/approvals`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        }
      );
      const result = await readJsonResponse<ApiPatchApprovalResult>(
        response,
        `POST /patch-artifacts/${patchArtifactId}/approvals`
      );
      return mapPatchApprovalResult(result);
    },
    async requestHumanApproval(approvalId: string) {
      const response = await fetch(
        apiUrl(options.baseUrl, `/patch-approvals/${approvalId}/request-human-approval`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        }
      );
      const result = await readJsonResponse<ApiPatchApprovalResult>(
        response,
        `POST /patch-approvals/${approvalId}/request-human-approval`
      );
      return mapPatchApprovalResult(result);
    }
  };
}

export function createConfiguredApiClient(): ConsoleApiClient {
  const baseUrl = import.meta.env.VITE_API_BASE_URL;
  if (!baseUrl) {
    return fakeApiClient;
  }

  return createHttpApiClient({
    baseUrl,
    projectId: import.meta.env.VITE_DEMO_PROJECT_ID
  });
}
