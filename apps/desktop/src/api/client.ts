export type TaskCard = {
  id: string;
  title: string;
  status: string;
  role_required: string;
  assigned_agent: string;
  updated_at: string;
};

export type ConsoleApiClient = {
  createTask: (goal: string) => Promise<TaskCard>;
};

export const fakeApiClient: ConsoleApiClient = {
  async createTask(goal: string) {
    return {
      id: "task_demo_created",
      title: goal,
      status: "CREATED",
      role_required: "frontend",
      assigned_agent: "Frontend Engineer",
      updated_at: "2026-05-29T00:00:00Z"
    };
  }
};
