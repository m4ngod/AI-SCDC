import { type FormEvent, useEffect, useRef, useState } from "react";
import type {
  CloudRunArtifactCard,
  CloudRunArtifactManifestCard,
  ConsoleApiClient,
  ConsoleIdentity,
  PlannerRunDraft,
  SandboxProfileInput,
  TaskCard
} from "./api/client";
import {
  WebConsoleAuthenticationError,
  createConfiguredApiClient
} from "./api/client";
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

function isWorkspaceAccessLost(error: unknown) {
  return (
    error instanceof WebConsoleAuthenticationError &&
    error.state === "workspace_access_lost"
  );
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
  const [identityState, setIdentityState] = useState<
    "checking" | "authenticated" | "signed_out" | "error"
  >(apiClient.getCurrentIdentity ? "checking" : "authenticated");
  const [currentIdentity, setCurrentIdentity] =
    useState<ConsoleIdentity | null>(null);
  const [isSwitchingWorkspace, setIsSwitchingWorkspace] = useState(false);
  const [workspaceSelectionError, setWorkspaceSelectionError] =
    useState<string | null>(null);
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
  const [processingCloudRunTaskId, setProcessingCloudRunTaskId] = useState<string | null>(null);
  const [cancellingCloudRunTaskId, setCancellingCloudRunTaskId] = useState<string | null>(null);
  const [creatingPullRequestTaskId, setCreatingPullRequestTaskId] = useState<string | null>(null);
  const [localRunErrors, setLocalRunErrors] = useState<Record<string, string>>({});
  const [workflowErrors, setWorkflowErrors] = useState<Record<string, string>>({});
  const plannerRunRef = useRef<PlannerRunDraft | null>(null);
  const cloudArtifactPreviewRequestsRef = useRef<Record<string, string>>({});
  const cloudArtifactPreviewRequestSequenceRef = useRef(0);
  const workspaceAccessRecoveryRef = useRef<Promise<void> | null>(null);

  useEffect(() => {
    plannerRunRef.current = plannerRun;
  }, [plannerRun]);

  useEffect(() => {
    let cancelled = false;

    setTaskLoadError(null);
    async function loadAuthenticatedConsole() {
      if (apiClient.getCurrentIdentity) {
        try {
          const identity = await apiClient.getCurrentIdentity();
          if (cancelled) {
            return;
          }
          if (!identity) {
            setCurrentIdentity(null);
            setIdentityState("signed_out");
            return;
          }
          setCurrentIdentity(identity);
          setIdentityState("authenticated");
          if (identity.selection_state === "selection_required") {
            setTasks([]);
            return;
          }
        } catch (error) {
          if (!cancelled) {
            setIdentityState("error");
            setTaskLoadError(errorMessage(error, "Failed to check sign-in"));
          }
          return;
        }
      }

      try {
        const initialTasks = await apiClient.listTasks();
        if (!cancelled) {
          setTasks((currentTasks) =>
            currentTasks.length === 0 ? initialTasks : currentTasks
          );
        }
      } catch (error) {
        if (!cancelled) {
          if (await enterSafeWorkspaceSelection(error)) {
            return;
          }
          setTaskLoadError(errorMessage(error, "Failed to load tasks"));
        }
      }
    }

    void loadAuthenticatedConsole();

    return () => {
      cancelled = true;
    };
  }, [apiClient]);

  async function enterSafeWorkspaceSelection(error: unknown) {
    if (!isWorkspaceAccessLost(error)) {
      return false;
    }
    if (!workspaceAccessRecoveryRef.current) {
      workspaceAccessRecoveryRef.current = (async () => {
        setIdentityState("checking");
        setTasks([]);
        setPlannerRun(null);
        setPlannerDecisionStatus(null);
        setPlannerDecisionError(null);
        setTaskLoadError(null);
        setWorkspaceSelectionError(null);
        setGithubSetupStatus(null);
        setGithubSetupError(null);
        setGithubSetupInput((currentInput) => ({
          ...currentInput,
          token: ""
        }));
        setSandboxProfileId(null);
        setSandboxProfileRepoId(null);
        setSandboxProfileStatus(null);
        setLocalRunErrors({});
        setWorkflowErrors({});
        cloudArtifactPreviewRequestsRef.current = {};

        try {
          const identity = await apiClient.getCurrentIdentity?.();
          if (!identity) {
            setCurrentIdentity(null);
            setIdentityState("signed_out");
            return;
          }
          setCurrentIdentity(identity);
          setIdentityState("authenticated");
        } catch (identityError) {
          setCurrentIdentity(null);
          setIdentityState("error");
          setTaskLoadError(
            errorMessage(
              identityError,
              "Failed to recover workspace selection"
            )
          );
        }
      })().finally(() => {
        workspaceAccessRecoveryRef.current = null;
      });
    }
    await workspaceAccessRecoveryRef.current;
    return true;
  }

  async function refreshCloudRunLogs(
    cloudRunId: string,
    fallbackLogs: TaskCard["cloud_run_logs"] = []
  ) {
    try {
      return await apiClient.listCloudRunLogs(cloudRunId);
    } catch (error) {
      if (isWorkspaceAccessLost(error)) {
        throw error;
      }
      return fallbackLogs;
    }
  }

  async function refreshCloudRunArtifacts(
    cloudRunId: string,
    fallbackManifest?: CloudRunArtifactManifestCard
  ) {
    try {
      return await apiClient.getCloudRunArtifactManifest(cloudRunId);
    } catch (error) {
      if (isWorkspaceAccessLost(error)) {
        throw error;
      }
      return fallbackManifest;
    }
  }

  async function handleSubmitGoal(goal: string) {
    if (isDecidingPlannerRun) {
      return;
    }

    try {
      const run = await apiClient.createPlannerRun(goal);
      setPlannerRun(run);
      setPlannerDecisionStatus(null);
      setPlannerDecisionError(null);
    } catch (error) {
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
      throw error;
    }
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
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
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
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
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
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
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
    let createdRepositoryId: string | null = null;
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
      createdRepositoryId = repository.id;
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
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
      if (createdRepositoryId) {
        try {
          await apiClient.deleteRepository(createdRepositoryId);
        } catch (cleanupError) {
          if (await enterSafeWorkspaceSelection(cleanupError)) {
            return;
          }
          // Preserve the setup error; cleanup failure is still visible in server logs.
        }
      }
      if (createdCredentialId) {
        try {
          await apiClient.deleteGitHubCredential(createdCredentialId);
        } catch (cleanupError) {
          if (await enterSafeWorkspaceSelection(cleanupError)) {
            return;
          }
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
    if (
      runningTaskId ||
      runningCloudTaskId ||
      processingCloudRunTaskId ||
      cancellingCloudRunTaskId
    ) {
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
      const fallbackTask = tasks.find((task) => task.id === taskId);
      const [cloudRunLogs, cloudRunArtifactManifest] = await Promise.all([
        refreshCloudRunLogs(result.cloud_run.id, fallbackTask?.cloud_run_logs),
        refreshCloudRunArtifacts(
          result.cloud_run.id,
          fallbackTask?.cloud_run_artifact_manifest
        )
      ]);
      setTasks((currentTasks) =>
        currentTasks.map((task) =>
          task.id === taskId
            ? {
                ...task,
                status: result.patch_artifact ? "PATCH_READY" : task.status,
                repo_id: result.cloud_run.repo_id,
                branch_name: result.cloud_run.head_branch,
                patch_artifact: result.patch_artifact ?? task.patch_artifact,
                cloud_run: result.cloud_run,
                cloud_run_logs: cloudRunLogs,
                cloud_run_artifact_manifest: cloudRunArtifactManifest
              }
            : task
        )
      );
    } catch (error) {
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [taskId]: errorMessage(error, "Failed to start cloud run")
      }));
    } finally {
      setRunningCloudTaskId(null);
    }
  }

  async function handleProcessCloudRun(task: TaskCard) {
    if (processingCloudRunTaskId || !task.cloud_run) {
      return;
    }

    setProcessingCloudRunTaskId(task.id);
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[task.id];
      return nextErrors;
    });
    try {
      const result = await apiClient.processCloudRun(task.cloud_run.id);
      const [cloudRunLogs, cloudRunArtifactManifest] = await Promise.all([
        refreshCloudRunLogs(result.cloud_run.id, task.cloud_run_logs),
        refreshCloudRunArtifacts(result.cloud_run.id, task.cloud_run_artifact_manifest)
      ]);
      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...currentTask,
                status: result.patch_artifact ? "PATCH_READY" : currentTask.status,
                repo_id: result.cloud_run.repo_id,
                branch_name: result.cloud_run.head_branch,
                patch_artifact: result.patch_artifact ?? currentTask.patch_artifact,
                cloud_run: result.cloud_run,
                cloud_run_logs: cloudRunLogs,
                cloud_run_artifact_manifest: cloudRunArtifactManifest
              }
            : currentTask
        )
      );
    } catch (error) {
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to process cloud run")
      }));
    } finally {
      setProcessingCloudRunTaskId(null);
    }
  }

  async function handleCancelCloudRun(task: TaskCard) {
    if (cancellingCloudRunTaskId || !task.cloud_run) {
      return;
    }

    setCancellingCloudRunTaskId(task.id);
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[task.id];
      return nextErrors;
    });
    try {
      const cloudRun = await apiClient.cancelCloudRun(task.cloud_run.id);
      const [cloudRunLogs, cloudRunArtifactManifest] = await Promise.all([
        refreshCloudRunLogs(cloudRun.id, task.cloud_run_logs),
        refreshCloudRunArtifacts(cloudRun.id, task.cloud_run_artifact_manifest)
      ]);
      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...currentTask,
                repo_id: cloudRun.repo_id,
                branch_name: cloudRun.head_branch,
                cloud_run: cloudRun,
                cloud_run_logs: cloudRunLogs,
                cloud_run_artifact_manifest: cloudRunArtifactManifest
              }
            : currentTask
        )
      );
    } catch (error) {
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to cancel cloud run")
      }));
    } finally {
      setCancellingCloudRunTaskId(null);
    }
  }

  async function handleOpenCloudRunArtifact(
    task: TaskCard,
    artifact: CloudRunArtifactCard
  ) {
    if (!task.cloud_run) {
      return;
    }

    cloudArtifactPreviewRequestSequenceRef.current += 1;
    const requestToken = `${artifact.id}:${cloudArtifactPreviewRequestSequenceRef.current}`;
    cloudArtifactPreviewRequestsRef.current = {
      ...cloudArtifactPreviewRequestsRef.current,
      [task.id]: requestToken
    };
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[task.id];
      return nextErrors;
    });
    setTasks((currentTasks) =>
      currentTasks.map((currentTask) =>
        currentTask.id === task.id
          ? {
              ...currentTask,
              cloud_run_artifact_preview: undefined
            }
          : currentTask
      )
    );
    try {
      const content = await apiClient.getCloudRunArtifactContent(
        task.cloud_run.id,
        artifact.id
      );
      if (cloudArtifactPreviewRequestsRef.current[task.id] !== requestToken) {
        return;
      }

      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...currentTask,
                cloud_run_artifact_preview: content
              }
            : currentTask
        )
      );
    } catch (error) {
      if (cloudArtifactPreviewRequestsRef.current[task.id] !== requestToken) {
        return;
      }
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }

      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...currentTask,
                cloud_run_artifact_preview: undefined
              }
            : currentTask
        )
      );
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to load cloud artifact")
      }));
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
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
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
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
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
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
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
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
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
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to create pull request")
      }));
    } finally {
      setCreatingPullRequestTaskId(null);
    }
  }

  async function handleWorkspaceSelection(workspaceId: string) {
    if (
      isSwitchingWorkspace ||
      !apiClient.selectWorkspace ||
      !apiClient.getCurrentIdentity
    ) {
      return;
    }
    setIsSwitchingWorkspace(true);
    setWorkspaceSelectionError(null);
    setTaskLoadError(null);
    setTasks([]);
    try {
      await apiClient.selectWorkspace(workspaceId);
      const identity = await apiClient.getCurrentIdentity();
      if (!identity) {
        setCurrentIdentity(null);
        setIdentityState("signed_out");
        return;
      }
      setCurrentIdentity(identity);
      if (identity.selection_state === "selection_required") {
        return;
      }
      const initialTasks = await apiClient.listTasks();
      setTasks(initialTasks);
    } catch (error) {
      if (await enterSafeWorkspaceSelection(error)) {
        return;
      }
      setWorkspaceSelectionError(
        errorMessage(error, "Failed to select workspace")
      );
    } finally {
      setIsSwitchingWorkspace(false);
    }
  }

  if (identityState === "checking") {
    return (
      <main className="sign-in-shell" aria-busy="true">
        <p>Checking your session…</p>
      </main>
    );
  }

  if (identityState === "signed_out") {
    const loginUrl =
      apiClient.getLoginUrl?.("/") ?? "/auth/login?return_to=%2F";
    return (
      <main className="sign-in-shell">
        <section aria-labelledby="sign-in-title">
          <p className="eyebrow">AI Company</p>
          <h1 id="sign-in-title">Continue to your workspace</h1>
          <p>Sign in with the email linked to your account.</p>
          <a className="primary-link" href={loginUrl}>
            Sign in with email
          </a>
        </section>
      </main>
    );
  }

  if (identityState === "error") {
    return (
      <main className="sign-in-shell">
        <section role="alert">
          <h1>We could not check your session</h1>
          <p>{taskLoadError}</p>
        </section>
      </main>
    );
  }

  const workspaceOptions =
    currentIdentity?.accounts.flatMap((account) =>
      account.workspaces.map((workspace) => ({
        ...workspace,
        accountId: account.id,
        accountName: account.name
      }))
    ) ?? [];
  const workspaceSelector = currentIdentity && workspaceOptions.length > 0 ? (
    <label className="workspace-selector">
      <span>Workspace</span>
      <select
        aria-label="Workspace"
        value={currentIdentity.workspace_id ?? ""}
        disabled={isSwitchingWorkspace}
        onChange={(event) => {
          void handleWorkspaceSelection(event.target.value);
        }}
      >
        {currentIdentity.workspace_id === null ? (
          <option value="" disabled>
            Choose a workspace
          </option>
        ) : null}
        {currentIdentity.accounts.map((account) => (
          <optgroup key={account.id} label={account.name}>
            {account.workspaces.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.name} · {workspace.role}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  ) : null;

  if (
    currentIdentity &&
    currentIdentity.selection_state === "selection_required"
  ) {
    return (
      <Shell
        contextPanel={
          <section className="context-section" aria-label="Account context">
            <h2>Choose a workspace to continue</h2>
            <p>Your session is active, but its previous workspace is no longer available.</p>
            {workspaceSelector}
            {workspaceSelectionError ? (
              <p role="alert">{workspaceSelectionError}</p>
            ) : null}
          </section>
        }
        workspaceName="Choose a workspace"
      >
        <section className="thread-panel" aria-labelledby="workspace-selection-title">
          <p className="eyebrow">Workspace access</p>
          <h1 id="workspace-selection-title">Choose a workspace to continue</h1>
          <p>No workspace data or actions are loaded until you make a selection.</p>
        </section>
      </Shell>
    );
  }

  const contextPanel = (
    <>
      {currentIdentity && currentIdentity.current_account && currentIdentity.current_workspace ? (
        <section
          className="context-section"
          aria-label="Account context"
        >
          <h2>Account</h2>
          <dl>
            <div>
              <dt>Account</dt>
              <dd>{currentIdentity.current_account.name}</dd>
            </div>
            <div>
              <dt>Type</dt>
              <dd>
                {currentIdentity.current_account.kind === "personal"
                  ? "Personal"
                  : "Legacy"}
              </dd>
            </div>
            <div>
              <dt>Workspace</dt>
              <dd>{currentIdentity.current_workspace.name}</dd>
            </div>
          </dl>
          {workspaceOptions.length > 1 ? workspaceSelector : null}
          {workspaceSelectionError ? (
            <p role="alert">{workspaceSelectionError}</p>
          ) : null}
        </section>
      ) : null}
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
        processingCloudRunTaskId={processingCloudRunTaskId}
        cancellingCloudRunTaskId={cancellingCloudRunTaskId}
        creatingPullRequestTaskId={creatingPullRequestTaskId}
        localRunErrors={localRunErrors}
        workflowErrors={workflowErrors}
        onStartLocalRun={handleStartLocalRun}
        onStartCloudRun={handleStartCloudRun}
        onProcessCloudRun={handleProcessCloudRun}
        onCancelCloudRun={handleCancelCloudRun}
        onOpenCloudRunArtifact={handleOpenCloudRunArtifact}
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
    <Shell
      contextPanel={contextPanel}
      accountName={currentIdentity?.current_account?.name}
      workspaceName={currentIdentity?.current_workspace?.name}
    >
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
