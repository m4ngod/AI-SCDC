import { useEffect, useState } from "react";
import type { ConsoleApiClient, PlannerRunDraft, TaskCard } from "./api/client";
import { createConfiguredApiClient } from "./api/client";
import { GoalInput } from "./components/GoalInput";
import { PlannerDraftPanel } from "./components/PlannerDraftPanel";
import { Shell } from "./components/Shell";
import { TaskBoard } from "./components/TaskBoard";
import "./styles/app.css";

type AppProps = {
  apiClient?: ConsoleApiClient;
};

const defaultApiClient = createConfiguredApiClient();

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

export function App({ apiClient = defaultApiClient }: AppProps) {
  const [tasks, setTasks] = useState<TaskCard[]>([]);
  const [taskLoadError, setTaskLoadError] = useState<string | null>(null);
  const [plannerRun, setPlannerRun] = useState<PlannerRunDraft | null>(null);
  const [plannerDecisionStatus, setPlannerDecisionStatus] = useState<string | null>(null);
  const [plannerDecisionError, setPlannerDecisionError] = useState<string | null>(null);
  const [isDecidingPlannerRun, setIsDecidingPlannerRun] = useState(false);

  useEffect(() => {
    let cancelled = false;

    setTaskLoadError(null);
    void apiClient
      .listTasks()
      .then((initialTasks) => {
        if (!cancelled) {
          setTasks((currentTasks) => (currentTasks.length === 0 ? initialTasks : currentTasks));
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setTaskLoadError(errorMessage(error, "Failed to load tasks"));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [apiClient]);

  async function handleSubmitGoal(goal: string) {
    const run = await apiClient.createPlannerRun(goal);
    setPlannerRun(run);
    setPlannerDecisionStatus(null);
    setPlannerDecisionError(null);
  }

  async function handleApprovePlannerRun() {
    if (!plannerRun || isDecidingPlannerRun) {
      return;
    }

    setIsDecidingPlannerRun(true);
    try {
      const decision = await apiClient.approvePlannerRun(plannerRun.id);
      setTasks((currentTasks) => [...decision.created_tasks, ...currentTasks]);
      setPlannerDecisionStatus("Approved");
      setPlannerDecisionError(null);
    } catch (error) {
      setPlannerDecisionError(errorMessage(error, "Failed to approve planner run"));
    } finally {
      setIsDecidingPlannerRun(false);
    }
  }

  async function handleRejectPlannerRun() {
    if (!plannerRun || isDecidingPlannerRun) {
      return;
    }

    setIsDecidingPlannerRun(true);
    try {
      await apiClient.rejectPlannerRun(plannerRun.id, "Rejected from desktop shell.");
      setPlannerDecisionStatus("Rejected");
      setPlannerDecisionError(null);
    } catch (error) {
      setPlannerDecisionError(errorMessage(error, "Failed to reject planner run"));
    } finally {
      setIsDecidingPlannerRun(false);
    }
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
      <TaskBoard tasks={tasks} />
      {taskLoadError ? (
        <section className="context-section context-error" role="alert">
          <h2>Task loading</h2>
          <p>{taskLoadError}</p>
        </section>
      ) : null}
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
        <GoalInput onSubmitGoal={handleSubmitGoal} />
        <PlannerDraftPanel
          plannerRun={plannerRun}
          decisionStatus={plannerDecisionStatus}
          decisionError={plannerDecisionError}
          isDeciding={isDecidingPlannerRun}
          onApprove={handleApprovePlannerRun}
          onReject={handleRejectPlannerRun}
        />
        <div className="thread-tabs" aria-label="Project command context">
          <section>
            <h2>Diff</h2>
            <p>No patch selected. Created tasks appear in the context panel.</p>
          </section>
          <section>
            <h2>Logs</h2>
            <p>Command thread is ready for the next project goal.</p>
          </section>
        </div>
      </section>
    </Shell>
  );
}
