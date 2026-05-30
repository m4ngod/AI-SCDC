import type { TaskCard } from "../api/client";

type TaskBoardProps = {
  tasks: TaskCard[];
  runningTaskId?: string | null;
  localRunErrors?: Record<string, string>;
  onStartLocalRun?: (taskId: string) => void;
};

export function TaskBoard({
  tasks,
  runningTaskId = null,
  localRunErrors = {},
  onStartLocalRun
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
                  disabled={runningTaskId === task.id}
                  onClick={() => onStartLocalRun(task.id)}
                >
                  {runningTaskId === task.id ? "Running" : "Run local"}
                </button>
              ) : null}
            </div>
            {task.patch_artifact ? (
              <dl className="task-patch-meta">
                <div>
                  <dt>Files</dt>
                  <dd>{task.patch_artifact.files_changed.join(", ")}</dd>
                </div>
                <div>
                  <dt>Tests</dt>
                  <dd>{task.patch_artifact.test_result}</dd>
                </div>
              </dl>
            ) : null}
            {localRunErrors[task.id] ? (
              <p className="task-run-error" role="alert">
                {localRunErrors[task.id]}
              </p>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}
