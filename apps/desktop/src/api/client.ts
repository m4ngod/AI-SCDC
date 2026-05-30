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
};

export type RepositoryCard = {
  id: string;
  name: string;
  local_path: string;
  default_branch: string;
  status: string;
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
};

export type LocalRunResult = {
  local_run: LocalTaskRunCard;
  patch_artifact?: PatchArtifactCard;
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
  startLocalRun: (taskId: string) => Promise<LocalRunResult>;
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

type ApiLocalTaskRun = LocalTaskRunCard;

type ApiPatchArtifact = PatchArtifactCard & {
  diff_text: string;
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
  async startLocalRun(taskId: string) {
    return {
      local_run: {
        id: "local_run_demo",
        task_id: taskId,
        repo_id: "repo_demo",
        status: "patch_ready",
        base_branch: "main",
        worktree_path: ".worktrees/task_demo",
        patch_artifact_id: "patch_demo",
        failure_reason: null
      },
      patch_artifact: {
        id: "patch_demo",
        task_id: taskId,
        local_run_id: "local_run_demo",
        summary: "Prepared local runner patch.",
        files_changed: ["README.md"],
        tests_run: [],
        test_result: "not_run"
      }
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

export function createHttpApiClient(options: HttpApiClientOptions): ConsoleApiClient {
  let resolvedProjectId = options.projectId;

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

  return {
    async listTasks() {
      const projectId = await getProjectId();
      const response = await fetch(apiUrl(options.baseUrl, `/projects/${projectId}/tasks`));
      const tasks = await readJsonResponse<ApiTask[]>(
        response,
        `GET /projects/${projectId}/tasks`
      );
      return tasks.map(mapTaskCard);
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
        patchArtifact = await readJsonResponse<ApiPatchArtifact>(
          artifactResponse,
          `GET /patch-artifacts/${localRun.patch_artifact_id}`
        );
      }
      return { local_run: localRun, patch_artifact: patchArtifact };
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
