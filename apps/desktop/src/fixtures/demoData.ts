import type { TaskCard } from "../api/client";

export const demoTasks: TaskCard[] = [
  {
    id: "task_board_ui",
    title: "Implement task board UI",
    status: "PATCH_READY",
    role_required: "frontend",
    assigned_agent: "Frontend Engineer",
    updated_at: "2026-05-29T00:00:00Z"
  },
  {
    id: "task_state_machine",
    title: "Add task state machine",
    status: "REVIEWING",
    role_required: "backend",
    assigned_agent: "Backend Engineer",
    updated_at: "2026-05-29T00:00:00Z"
  }
];
