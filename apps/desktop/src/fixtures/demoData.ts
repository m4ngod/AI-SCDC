import type { TaskCard } from "../api/client";

export const demoTasks: TaskCard[] = [
  {
    id: "task_board_ui",
    title: "Implement task board UI",
    status: "PATCH_READY",
    role_required: "frontend",
    assigned_agent: "Frontend Engineer",
    updated_at: "2026-05-29T00:00:00Z",
    patch_artifact: {
      id: "patch_demo",
      task_id: "task_board_ui",
      local_run_id: "local_run_demo",
      summary: "Prepared local runner patch.",
      files_changed: ["apps/desktop/src/components/TaskBoard.tsx"],
      tests_run: ["pnpm --filter @ai-scdc/desktop test"],
      test_result: "not_run",
      diff_text:
        "diff --git a/apps/desktop/src/components/TaskBoard.tsx b/apps/desktop/src/components/TaskBoard.tsx\n+Local runner prepared patch"
    }
  },
  {
    id: "task_patch_approved",
    title: "Approve reviewed README patch",
    status: "APPROVED",
    role_required: "documentation",
    assigned_agent: "Documentation Agent",
    updated_at: "2026-05-29T00:04:00Z",
    worktree_ref: ".worktrees/task_patch_approved-local_run_demo",
    patch_artifact: {
      id: "patch_approved_demo",
      task_id: "task_patch_approved",
      local_run_id: "local_run_demo",
      summary: "Prepared README patch.",
      files_changed: ["README.md"],
      tests_run: ["python -V"],
      test_result: "passed",
      diff_text: "diff --git a/README.md b/README.md\n+Approved demo patch"
    },
    patch_review: {
      id: "review_approved_demo",
      task_id: "task_patch_approved",
      local_run_id: "local_run_demo",
      patch_artifact_id: "patch_approved_demo",
      test_run_id: "test_run_demo",
      reviewer_kind: "deterministic",
      verdict: "approved",
      issues: [],
      required_changes: [],
      created_at: "2026-05-29T00:03:00Z"
    }
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
