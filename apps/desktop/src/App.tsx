import { useState } from "react";
import type { ConsoleApiClient, TaskCard } from "./api/client";
import { fakeApiClient } from "./api/client";
import { GoalInput } from "./components/GoalInput";
import { Shell } from "./components/Shell";
import { TaskBoard } from "./components/TaskBoard";
import { demoTasks } from "./fixtures/demoData";
import "./styles/app.css";

type AppProps = {
  apiClient?: ConsoleApiClient;
};

export function App({ apiClient = fakeApiClient }: AppProps) {
  const [tasks, setTasks] = useState<TaskCard[]>(demoTasks);

  async function handleCreateTask(goal: string) {
    const task = await apiClient.createTask(goal);
    setTasks((currentTasks) => [task, ...currentTasks]);
  }

  const contextPanel = (
    <>
      <section className="context-section">
        <h2>Agent status</h2>
        <dl>
          <div>
            <dt>Frontend Engineer</dt>
            <dd>Ready for UI tasks</dd>
          </div>
          <div>
            <dt>Backend Engineer</dt>
            <dd>Reviewing workflow API</dd>
          </div>
        </dl>
      </section>
      <section className="context-section">
        <h2>Test results</h2>
        <p>Vitest pending for current patch.</p>
      </section>
      <section className="context-section">
        <h2>Diff</h2>
        <p>Desktop shell package staged for review.</p>
      </section>
      <section className="context-section logs">
        <h2>Logs</h2>
        <p>Awaiting next task creation event.</p>
      </section>
    </>
  );

  return (
    <Shell contextPanel={contextPanel}>
      <section className="thread-panel" aria-labelledby="thread-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Project command thread</p>
            <h1 id="thread-title">Project command thread</h1>
          </div>
          <span className="run-state">Online</span>
        </div>
        <GoalInput onCreateTask={handleCreateTask} />
        <TaskBoard tasks={tasks} />
      </section>
    </Shell>
  );
}
