import type { TaskCard } from "../api/client";

type TaskBoardProps = {
  tasks: TaskCard[];
  runningTaskId?: string | null;
  runningTestTaskId?: string | null;
  reviewingTaskId?: string | null;
  approvingPatchTaskId?: string | null;
  requestingHumanApprovalTaskId?: string | null;
  localRunErrors?: Record<string, string>;
  workflowErrors?: Record<string, string>;
  onStartLocalRun?: (taskId: string) => void;
  onRunPatchTests?: (task: TaskCard) => void;
  onReviewPatch?: (task: TaskCard) => void;
  onApprovePatch?: (task: TaskCard) => void;
  onRequestHumanApproval?: (task: TaskCard) => void;
};

export function TaskBoard({
  tasks,
  runningTaskId = null,
  runningTestTaskId = null,
  reviewingTaskId = null,
  approvingPatchTaskId = null,
  requestingHumanApprovalTaskId = null,
  localRunErrors = {},
  workflowErrors = {},
  onStartLocalRun,
  onRunPatchTests,
  onReviewPatch,
  onApprovePatch,
  onRequestHumanApproval
}: TaskBoardProps) {
  return (
    <section className="task-board" aria-label="Task board">
      <div className="section-heading">
        <h2>Task board</h2>
        <span>{tasks.length} active</span>
      </div>
      <div className="task-list">
        {tasks.map((task) => (
          <article className="task-row" key={task.id}>
            <div className="task-main">
              <h3>{task.title}</h3>
              <p>{task.assigned_agent}</p>
            </div>
            <div className="task-row-meta">
              <span className="status-pill">{task.status}</span>
              {onStartLocalRun && task.status === "CREATED" ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={runningTaskId !== null}
                  onClick={() => onStartLocalRun(task.id)}
                >
                  {runningTaskId === task.id ? "Running" : "Run local"}
                </button>
              ) : null}
              {onRunPatchTests && task.status === "PATCH_READY" && task.patch_artifact ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={runningTestTaskId !== null}
                  onClick={() => onRunPatchTests(task)}
                >
                  {runningTestTaskId === task.id ? "Testing" : "Run tests"}
                </button>
              ) : null}
              {onReviewPatch && task.status === "REVIEWING" && task.patch_artifact ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={reviewingTaskId !== null}
                  onClick={() => onReviewPatch(task)}
                >
                  {reviewingTaskId === task.id ? "Reviewing" : "Review patch"}
                </button>
              ) : null}
              {onApprovePatch && task.status === "APPROVED" && task.patch_artifact ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={approvingPatchTaskId !== null}
                  onClick={() => onApprovePatch(task)}
                >
                  {approvingPatchTaskId === task.id ? "Approving" : "Approve patch"}
                </button>
              ) : null}
              {onRequestHumanApproval && task.status === "MERGE_READY" && task.patch_approval ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={requestingHumanApprovalTaskId !== null}
                  onClick={() => onRequestHumanApproval(task)}
                >
                  {requestingHumanApprovalTaskId === task.id
                    ? "Requesting"
                    : "Request human approval"}
                </button>
              ) : null}
            </div>
            {task.patch_artifact || task.test_run || task.patch_review || task.debug_attempt ? (
              <dl className="task-patch-meta">
                {task.patch_artifact ? (
                  <>
                    <div>
                      <dt>Files</dt>
                      <dd>{task.patch_artifact.files_changed.join(", ")}</dd>
                    </div>
                    <div>
                      <dt>Tests</dt>
                      <dd>{task.patch_artifact.test_result}</dd>
                    </div>
                  </>
                ) : null}
                {task.patch_approval ? (
                  <div>
                    <dt>Patch approval</dt>
                    <dd>{`${task.patch_approval.status} by ${task.patch_approval.approved_by}`}</dd>
                  </div>
                ) : null}
                {task.worktree_ref ? (
                  <div>
                    <dt>Worktree</dt>
                    <dd>{task.worktree_ref}</dd>
                  </div>
                ) : null}
                {task.patch_approval?.merge_instructions ? (
                  <div>
                    <dt>Merge instructions</dt>
                    <dd>{task.patch_approval.merge_instructions}</dd>
                  </div>
                ) : null}
                {task.test_run ? (
                  <div>
                    <dt>Test run</dt>
                    <dd>{task.test_run.status}</dd>
                  </div>
                ) : null}
                {task.patch_review ? (
                  <div>
                    <dt>Review</dt>
                    <dd>{task.patch_review.verdict}</dd>
                  </div>
                ) : null}
                {task.patch_review?.required_changes.length ? (
                  <div>
                    <dt>Required changes</dt>
                    <dd>{task.patch_review.required_changes.join("; ")}</dd>
                  </div>
                ) : null}
                {task.debug_attempt ? (
                  <div>
                    <dt>Debug</dt>
                    <dd>{task.debug_attempt.status}</dd>
                  </div>
                ) : null}
                {task.debug_attempt?.root_cause ? (
                  <div>
                    <dt>Root cause</dt>
                    <dd>{task.debug_attempt.root_cause}</dd>
                  </div>
                ) : null}
                {task.debug_attempt?.fix_summary ? (
                  <div>
                    <dt>Fix summary</dt>
                    <dd>{task.debug_attempt.fix_summary}</dd>
                  </div>
                ) : null}
              </dl>
            ) : null}
            {task.patch_artifact?.diff_text ? (
              <div className="task-diff-preview">
                <h4 id={`task-${task.id}-diff-preview-title`}>Diff preview</h4>
                <pre
                  aria-labelledby={`task-${task.id}-diff-preview-title`}
                  role="region"
                  tabIndex={0}
                >{task.patch_artifact.diff_text}</pre>
              </div>
            ) : null}
            {localRunErrors[task.id] ? (
              <p className="task-run-error" role="alert">
                {localRunErrors[task.id]}
              </p>
            ) : null}
            {workflowErrors[task.id] ? (
              <p className="task-run-error" role="alert">
                {workflowErrors[task.id]}
              </p>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}
