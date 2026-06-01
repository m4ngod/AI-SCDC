import { type FormEvent, useEffect, useRef, useState } from "react";
import type {
  ConsoleApiClient,
  PlannerRunDraft,
  SandboxProfileInput,
  TaskCard
} from "./api/client";
import { createConfiguredApiClient } from "./api/client";
import { GoalInput } from "./components/GoalInput";
import { PlannerDraftPanel } from "./components/PlannerDraftPanel";
import { Shell } from "./components/Shell";
import { TaskBoard } from "./components/TaskBoard";
import "./styles/app.css";

type AppProps = {
  apiClient?: ConsoleApiClient;
};

type GitHubSetupInput = {
  token: string;
  repo_url: string;
  github_owner: string;
  github_repo: string;
  default_branch: string;
};

const defaultApiClient = createConfiguredApiClient();

const defaultDockerSandboxProfile: Omit<SandboxProfileInput, "repo_id"> = {
  name: "Default Docker profile",
  docker_image: "python:3.11-bookworm",
  patch_commands: [
    {
      key: "write-note",
      label: "Write note",
      command: "python scripts/write_note.py",
      timeout_seconds: 300,
      is_default: true
    }
  ],
  test_commands: [
    {
      key: "python-version",
      label: "Python version",
      command: "python -V",
      timeout_seconds: 300,
      is_default: true
    }
  ],
  allowed_env_vars: [],
  network_enabled: true
};

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function mergeWorkflowTask(currentTask: TaskCard, resultTask: TaskCard): TaskCard {
  return {
    ...currentTask,
    status: resultTask.status,
    updated_at: resultTask.updated_at || currentTask.updated_at,
    repo_id: resultTask.repo_id !== undefined ? resultTask.repo_id : currentTask.repo_id,
    branch_name:
      resultTask.branch_name !== undefined ? resultTask.branch_name : currentTask.branch_name,
    worktree_ref:
      resultTask.worktree_ref !== undefined ? resultTask.worktree_ref : currentTask.worktree_ref
  };
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
  const [approvingPatchTaskId, setApprovingPatchTaskId] = useState<string | null>(null);
  const [requestingHumanApprovalTaskId, setRequestingHumanApprovalTaskId] =
    useState<string | null>(null);
  const [githubSetupInput, setGithubSetupInput] = useState<GitHubSetupInput>({
    token: "",
    repo_url: "https://github.com/example/demo",
    github_owner: "example",
    github_repo: "demo",
    default_branch: "main"
  });
  const [githubSetupStatus, setGithubSetupStatus] = useState<string | null>(null);
  const [githubSetupError, setGithubSetupError] = useState<string | null>(null);
  const [sandboxProfileId, setSandboxProfileId] = useState<string | null>(null);
  const [sandboxProfileRepoId, setSandboxProfileRepoId] = useState<string | null>(null);
  const [sandboxProfileStatus, setSandboxProfileStatus] = useState<string | null>(null);
  const [isConnectingGitHubRepo, setIsConnectingGitHubRepo] = useState(false);
  const [runningCloudTaskId, setRunningCloudTaskId] = useState<string | null>(null);
  const [creatingPullRequestTaskId, setCreatingPullRequestTaskId] = useState<string | null>(null);
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
    if (runningTaskId || runningCloudTaskId) {
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

  async function handleConnectGitHubRepo(input: GitHubSetupInput) {
    if (isConnectingGitHubRepo) {
      return;
    }

    setIsConnectingGitHubRepo(true);
    let createdCredentialId: string | null = null;
    try {
      setGithubSetupStatus(null);
      setGithubSetupError(null);
      setSandboxProfileId(null);
      setSandboxProfileRepoId(null);
      setSandboxProfileStatus(null);
      const credential = await apiClient.createGitHubCredential({
        display_name: "Dev GitHub",
        token: input.token
      });
      createdCredentialId = credential.id;
      const repository = await apiClient.createGitHubRepository({
        name: `${input.github_owner}/${input.github_repo}`,
        repo_url: input.repo_url,
        github_owner: input.github_owner,
        github_repo: input.github_repo,
        default_branch: input.default_branch,
        github_credential_id: credential.id
      });
      if (!repository.project_id) {
        throw new Error("GitHub repository response did not include project id");
      }
      const sandboxProfile = await apiClient.createSandboxProfile(
        repository.project_id,
        {
          ...defaultDockerSandboxProfile,
          repo_id: repository.id
        }
      );
      setSandboxProfileId(sandboxProfile.id);
      setSandboxProfileRepoId(repository.id);
      setSandboxProfileStatus(`Sandbox profile ready: ${sandboxProfile.docker_image}`);
      setGithubSetupStatus("GitHub repo connected");
      setGithubSetupInput((currentInput) => ({ ...currentInput, token: "" }));
    } catch (error) {
      if (createdCredentialId) {
        try {
          await apiClient.deleteGitHubCredential(createdCredentialId);
        } catch {
          // Preserve the setup error; cleanup failure is still visible in server logs.
        }
      }
      setGithubSetupError(errorMessage(error, "Failed to connect GitHub repo"));
    } finally {
      setIsConnectingGitHubRepo(false);
    }
  }

  async function handleSubmitGitHubSetup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await handleConnectGitHubRepo(githubSetupInput);
  }

  async function handleStartCloudRun(taskId: string) {
    if (runningTaskId || runningCloudTaskId) {
      return;
    }

    setRunningCloudTaskId(taskId);
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[taskId];
      return nextErrors;
    });
    try {
      const result = sandboxProfileId && sandboxProfileRepoId
        ? await apiClient.startCloudRun(taskId, {
            repo_id: sandboxProfileRepoId,
            sandbox_profile_id: sandboxProfileId,
            patch_command_key: "write-note",
            test_command_keys: ["python-version"]
          })
        : await apiClient.startCloudRun(taskId);
      setTasks((currentTasks) =>
        currentTasks.map((task) =>
          task.id === taskId
            ? {
                ...task,
                status: result.patch_artifact ? "PATCH_READY" : task.status,
                repo_id: result.cloud_run.repo_id,
                branch_name: result.cloud_run.head_branch,
                patch_artifact: result.patch_artifact ?? task.patch_artifact,
                cloud_run: result.cloud_run
              }
            : task
        )
      );
    } catch (error) {
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [taskId]: errorMessage(error, "Failed to start cloud run")
      }));
    } finally {
      setRunningCloudTaskId(null);
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
                ...mergeWorkflowTask(currentTask, result.task),
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
                ...mergeWorkflowTask(currentTask, result.task),
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

  async function handleApprovePatch(task: TaskCard) {
    if (approvingPatchTaskId || !task.patch_artifact) {
      return;
    }

    setApprovingPatchTaskId(task.id);
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[task.id];
      return nextErrors;
    });
    try {
      const result = await apiClient.approvePatch(task.patch_artifact.id);
      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...mergeWorkflowTask(currentTask, result.task),
                patch_artifact: result.patch_artifact,
                patch_review: result.review,
                patch_approval: result.approval
              }
            : currentTask
        )
      );
    } catch (error) {
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to approve patch")
      }));
    } finally {
      setApprovingPatchTaskId(null);
    }
  }

  async function handleRequestHumanApproval(task: TaskCard) {
    if (requestingHumanApprovalTaskId || !task.patch_approval) {
      return;
    }

    setRequestingHumanApprovalTaskId(task.id);
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[task.id];
      return nextErrors;
    });
    try {
      const result = await apiClient.requestHumanApproval(task.patch_approval.id);
      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...mergeWorkflowTask(currentTask, result.task),
                patch_artifact: result.patch_artifact,
                patch_review: result.review,
                patch_approval: result.approval
              }
            : currentTask
        )
      );
    } catch (error) {
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to request human approval")
      }));
    } finally {
      setRequestingHumanApprovalTaskId(null);
    }
  }

  async function handleCreatePullRequest(task: TaskCard) {
    if (creatingPullRequestTaskId || !task.patch_approval) {
      return;
    }

    setCreatingPullRequestTaskId(task.id);
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[task.id];
      return nextErrors;
    });
    try {
      const result = await apiClient.createPullRequest(task.patch_approval.id);
      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...mergeWorkflowTask(currentTask, result.task),
                patch_artifact: result.patch_artifact ?? currentTask.patch_artifact,
                patch_approval: result.approval ?? currentTask.patch_approval,
                cloud_run: currentTask.cloud_run,
                pull_request: result.pull_request
              }
            : currentTask
        )
      );
    } catch (error) {
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to create pull request")
      }));
    } finally {
      setCreatingPullRequestTaskId(null);
    }
  }

  const contextPanel = (
    <>
      <section className="context-section">
        <h2>GitHub setup</h2>
        <form className="github-setup-form" onSubmit={handleSubmitGitHubSetup}>
          <label>
            <span>GitHub token</span>
            <input
              type="password"
              value={githubSetupInput.token}
              onChange={(event) =>
                setGithubSetupInput((currentInput) => ({
                  ...currentInput,
                  token: event.target.value
                }))
              }
            />
          </label>
          <label>
            <span>Repository URL</span>
            <input
              type="url"
              value={githubSetupInput.repo_url}
              onChange={(event) =>
                setGithubSetupInput((currentInput) => ({
                  ...currentInput,
                  repo_url: event.target.value
                }))
              }
            />
          </label>
          <div className="github-setup-grid">
            <label>
              <span>Owner</span>
              <input
                type="text"
                value={githubSetupInput.github_owner}
                onChange={(event) =>
                  setGithubSetupInput((currentInput) => ({
                    ...currentInput,
                    github_owner: event.target.value
                  }))
                }
              />
            </label>
            <label>
              <span>Repository</span>
              <input
                type="text"
                value={githubSetupInput.github_repo}
                onChange={(event) =>
                  setGithubSetupInput((currentInput) => ({
                    ...currentInput,
                    github_repo: event.target.value
                  }))
                }
              />
            </label>
          </div>
          <label>
            <span>Default branch</span>
            <input
              type="text"
              value={githubSetupInput.default_branch}
              onChange={(event) =>
                setGithubSetupInput((currentInput) => ({
                  ...currentInput,
                  default_branch: event.target.value
                }))
              }
            />
          </label>
          <button type="submit" disabled={isConnectingGitHubRepo}>
            {isConnectingGitHubRepo ? "Connecting GitHub repo" : "Connect GitHub repo"}
          </button>
          {githubSetupStatus ? <p>{githubSetupStatus}</p> : null}
          {sandboxProfileStatus ? <p>{sandboxProfileStatus}</p> : null}
          {githubSetupError ? (
            <p className="github-setup-error" role="alert">
              {githubSetupError}
            </p>
          ) : null}
        </form>
      </section>
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
        approvingPatchTaskId={approvingPatchTaskId}
        requestingHumanApprovalTaskId={requestingHumanApprovalTaskId}
        runningCloudTaskId={runningCloudTaskId}
        creatingPullRequestTaskId={creatingPullRequestTaskId}
        localRunErrors={localRunErrors}
        workflowErrors={workflowErrors}
        onStartLocalRun={handleStartLocalRun}
        onStartCloudRun={handleStartCloudRun}
        onRunPatchTests={handleRunPatchTests}
        onReviewPatch={handleReviewPatch}
        onApprovePatch={handleApprovePatch}
        onRequestHumanApproval={handleRequestHumanApproval}
        onCreatePullRequest={handleCreatePullRequest}
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
