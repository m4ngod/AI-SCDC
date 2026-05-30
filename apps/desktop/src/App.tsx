import { useEffect, useRef, useState } from "react";
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
  const [runningTaskId, setRunningTaskId] = useState<string | null>(null);
  const [runningTestTaskId, setRunningTestTaskId] = useState<string | null>(null);
  const [reviewingTaskId, setReviewingTaskId] = useState<string | null>(null);
  const [localRunErrors, setLocalRunErrors] = useState<Record<string, string>>({});
  const [workflowErrors, setWorkflowErrors] = useState<Record<string, string>>({});
  const plannerRunRef = useRef<PlannerRunDraft | null>(null);

  useEffect(() => {
    plannerRunRef.current = plannerRun;
  }, [plannerRun]);

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
    if (isDecidingPlannerRun) {
      return;
    }

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
    const decidingRunId = plannerRun.id;
    try {
      const decision = await apiClient.approvePlannerRun(decidingRunId);
      if (plannerRunRef.current?.id === decidingRunId) {
        setTasks((currentTasks) => [...decision.created_tasks, ...currentTasks]);
        setPlannerDecisionStatus("Approved");
        setPlannerDecisionError(null);
      }
    } catch (error) {
      if (plannerRunRef.current?.id === decidingRunId) {
        setPlannerDecisionError(errorMessage(error, "Failed to approve planner run"));
      }
    } finally {
      setIsDecidingPlannerRun(false);
    }
  }

  async function handleRejectPlannerRun() {
    if (!plannerRun || isDecidingPlannerRun) {
      return;
    }

    setIsDecidingPlannerRun(true);
    const decidingRunId = plannerRun.id;
    try {
      await apiClient.rejectPlannerRun(decidingRunId, "Rejected from desktop shell.");
      if (plannerRunRef.current?.id === decidingRunId) {
        setPlannerDecisionStatus("Rejected");
        setPlannerDecisionError(null);
      }
    } catch (error) {
      if (plannerRunRef.current?.id === decidingRunId) {
        setPlannerDecisionError(errorMessage(error, "Failed to reject planner run"));
      }
    } finally {
      setIsDecidingPlannerRun(false);
    }
  }

  async function handleStartLocalRun(taskId: string) {
    if (runningTaskId) {
      return;
    }

    setRunningTaskId(taskId);
    setLocalRunErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[taskId];
      return nextErrors;
    });
    try {
      const result = await apiClient.startLocalRun(taskId);
      setTasks((currentTasks) =>
        currentTasks.map((task) =>
          task.id === taskId
            ? {
                ...task,
                status:
                  result.local_run.status === "patch_ready" ? "PATCH_READY" : task.status,
                repo_id: result.local_run.repo_id,
                branch_name: result.local_run.base_branch,
                worktree_ref: result.local_run.worktree_path,
                patch_artifact: result.patch_artifact
              }
            : task
        )
      );
    } catch (error) {
      setLocalRunErrors((currentErrors) => ({
        ...currentErrors,
        [taskId]: errorMessage(error, "Failed to start local run")
      }));
    } finally {
      setRunningTaskId(null);
    }
  }

  async function handleRunPatchTests(task: TaskCard) {
    if (runningTestTaskId || !task.patch_artifact) {
      return;
    }

    setRunningTestTaskId(task.id);
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[task.id];
      return nextErrors;
    });
    try {
      const result = await apiClient.runPatchTests(task.patch_artifact.id);
      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...currentTask,
                ...result.task,
                patch_artifact: result.patch_artifact,
                test_run: result.test_run,
                debug_attempt: result.debug_attempt
              }
            : currentTask
        )
      );
    } catch (error) {
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to run patch tests")
      }));
    } finally {
      setRunningTestTaskId(null);
    }
  }

  async function handleReviewPatch(task: TaskCard) {
    if (reviewingTaskId || !task.patch_artifact) {
      return;
    }

    setReviewingTaskId(task.id);
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[task.id];
      return nextErrors;
    });
    try {
      const result = await apiClient.reviewPatch(task.patch_artifact.id);
      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...currentTask,
                ...result.task,
                patch_artifact: result.patch_artifact,
                test_run: currentTask.test_run,
                patch_review: result.review,
                debug_attempt: result.debug_attempt
              }
            : currentTask
        )
      );
    } catch (error) {
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to review patch")
      }));
    } finally {
      setReviewingTaskId(null);
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
      <TaskBoard
        tasks={tasks}
        runningTaskId={runningTaskId}
        runningTestTaskId={runningTestTaskId}
        reviewingTaskId={reviewingTaskId}
        localRunErrors={localRunErrors}
        workflowErrors={workflowErrors}
        onStartLocalRun={handleStartLocalRun}
        onRunPatchTests={handleRunPatchTests}
        onReviewPatch={handleReviewPatch}
      />
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
        <GoalInput onSubmitGoal={handleSubmitGoal} disabled={isDecidingPlannerRun} />
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
