import { demoTasks } from "../fixtures/demoData";

export type TaskCard = {
  id: string;
  title: string;
  status: string;
  role_required: string;
  assigned_agent: string;
  updated_at: string;
};

export type ConsoleApiClient = {
  listTasks: () => Promise<TaskCard[]>;
  createTask: (goal: string) => Promise<TaskCard>;
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
};

type HttpApiClientOptions = {
  baseUrl: string;
  projectId?: string;
};

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
  return {
    id: task.id,
    title: task.title,
    status: task.status,
    role_required: task.role_required,
    assigned_agent: agentNameForRole(task.role_required),
    updated_at: task.updated_at ?? task.created_at ?? ""
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
