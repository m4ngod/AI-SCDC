import type { TaskCard } from "../api/client";

type TaskBoardProps = {
  tasks: TaskCard[];
};

export function TaskBoard({ tasks }: TaskBoardProps) {
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
            <span className="status-pill">{task.status}</span>
          </article>
        ))}
      </div>
    </section>
  );
}
