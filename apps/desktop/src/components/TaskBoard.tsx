import type { CloudRunArtifactCard, TaskCard } from "../api/client";

type TaskBoardProps = {
  tasks: TaskCard[];
  runningTaskId?: string | null;
  runningTestTaskId?: string | null;
  reviewingTaskId?: string | null;
  approvingPatchTaskId?: string | null;
  requestingHumanApprovalTaskId?: string | null;
  runningCloudTaskId?: string | null;
  processingCloudRunTaskId?: string | null;
  cancellingCloudRunTaskId?: string | null;
  creatingPullRequestTaskId?: string | null;
  localRunErrors?: Record<string, string>;
  workflowErrors?: Record<string, string>;
  onStartLocalRun?: (taskId: string) => void;
  onStartCloudRun?: (taskId: string) => void;
  onProcessCloudRun?: (task: TaskCard) => void;
  onCancelCloudRun?: (task: TaskCard) => void;
  onOpenCloudRunArtifact?: (task: TaskCard, artifact: CloudRunArtifactCard) => void;
  onRunPatchTests?: (task: TaskCard) => void;
  onReviewPatch?: (task: TaskCard) => void;
  onApprovePatch?: (task: TaskCard) => void;
  onRequestHumanApproval?: (task: TaskCard) => void;
  onCreatePullRequest?: (task: TaskCard) => void;
};

export function TaskBoard({
  tasks,
  runningTaskId = null,
  runningTestTaskId = null,
  reviewingTaskId = null,
  approvingPatchTaskId = null,
  requestingHumanApprovalTaskId = null,
  runningCloudTaskId = null,
  processingCloudRunTaskId = null,
  cancellingCloudRunTaskId = null,
  creatingPullRequestTaskId = null,
  localRunErrors = {},
  workflowErrors = {},
  onStartLocalRun,
  onStartCloudRun,
  onProcessCloudRun,
  onCancelCloudRun,
  onOpenCloudRunArtifact,
  onRunPatchTests,
  onReviewPatch,
  onApprovePatch,
  onRequestHumanApproval,
  onCreatePullRequest
}: TaskBoardProps) {
  const isCloudActionPending =
    runningCloudTaskId !== null ||
    processingCloudRunTaskId !== null ||
    cancellingCloudRunTaskId !== null;
  const isRunPending = runningTaskId !== null || isCloudActionPending;

  function artifactMeta(artifact: CloudRunArtifactCard) {
    return `${artifact.kind} | ${artifact.content_type} | ${artifact.size_bytes} bytes`;
  }

  function isTextArtifact(artifact: CloudRunArtifactCard) {
    return (
      artifact.content_type.startsWith("text/") ||
      artifact.content_type === "application/json"
    );
  }

  function artifactGroups(artifacts: CloudRunArtifactCard[]) {
    return artifacts.reduce<Array<{ kind: string; artifacts: CloudRunArtifactCard[] }>>(
      (groups, artifact) => {
        const group = groups.find((item) => item.kind === artifact.kind);
        if (group) {
          group.artifacts.push(artifact);
          return groups;
        }
        return [...groups, { kind: artifact.kind, artifacts: [artifact] }];
      },
      []
    );
  }

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
              {onStartLocalRun && task.status === "CREATED" && !task.cloud_run ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={isRunPending}
                  onClick={() => onStartLocalRun(task.id)}
                >
                  {runningTaskId === task.id ? "Running" : "Run local"}
                </button>
              ) : null}
              {onStartCloudRun && task.status === "CREATED" && !task.cloud_run ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={isRunPending}
                  onClick={() => onStartCloudRun(task.id)}
                >
                  {runningCloudTaskId === task.id ? "Running cloud" : "Run cloud"}
                </button>
              ) : null}
              {onProcessCloudRun && task.cloud_run?.status === "queued" ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={isCloudActionPending}
                  onClick={() => onProcessCloudRun(task)}
                >
                  {processingCloudRunTaskId === task.id ? "Processing" : "Process"}
                </button>
              ) : null}
              {onCancelCloudRun &&
              (task.cloud_run?.status === "queued" || task.cloud_run?.status === "running") ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={isCloudActionPending}
                  onClick={() => onCancelCloudRun(task)}
                >
                  {cancellingCloudRunTaskId === task.id ? "Cancelling" : "Cancel"}
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
              {onCreatePullRequest && task.status === "HUMAN_APPROVAL" && task.patch_approval ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={creatingPullRequestTaskId !== null}
                  onClick={() => onCreatePullRequest(task)}
                >
                  {creatingPullRequestTaskId === task.id ? "Creating PR" : "Create PR"}
                </button>
              ) : null}
            </div>
            {task.patch_artifact ||
            task.test_run ||
            task.patch_review ||
            task.debug_attempt ||
            task.cloud_run ||
            task.pull_request ? (
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
                {task.cloud_run ? (
                  <div>
                    <dt>Cloud run</dt>
                    <dd>
                      <span>{`${task.cloud_run.status} via ${
                        task.cloud_run.sandbox_kind ?? "fake"
                      } on ${task.cloud_run.head_branch}`}</span>
                      {task.cloud_run.failure_reason ? (
                        <span className="task-inline-error">
                          {task.cloud_run.failure_reason}
                        </span>
                      ) : null}
                    </dd>
                  </div>
                ) : null}
                {task.cloud_run_logs?.length ? (
                  <div>
                    <dt>Cloud logs</dt>
                    <dd>
                      <ol className="task-cloud-log-list">
                        {task.cloud_run_logs.map((entry) => (
                          <li key={entry.id}>{`${entry.event}: ${entry.message}`}</li>
                        ))}
                      </ol>
                    </dd>
                  </div>
                ) : null}
                {task.pull_request ? (
                  <div>
                    <dt>Pull request</dt>
                    <dd>
                      <a href={task.pull_request.github_pr_url ?? task.pull_request.url}>
                        {task.pull_request.github_pr_url ?? task.pull_request.url}
                      </a>
                    </dd>
                  </div>
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
            {task.cloud_run_artifact_manifest ? (
              <div className="task-artifacts">
                <div className="task-artifact-summary">
                  <h4>Artifacts</h4>
                  <span>{`${task.cloud_run_artifact_manifest.artifacts.length} objects`}</span>
                  <span>{task.cloud_run_artifact_manifest.retention.policy}</span>
                </div>
                <div className="task-artifact-groups">
                  {artifactGroups(task.cloud_run_artifact_manifest.artifacts).map((group) => (
                    <div className="task-artifact-kind" key={group.kind}>
                      <h5>{group.kind}</h5>
                      <ul className="task-artifact-list">
                        {group.artifacts.map((artifact) => (
                          <li className="task-artifact-item" key={artifact.id}>
                            {onOpenCloudRunArtifact && isTextArtifact(artifact) ? (
                              <button
                                type="button"
                                className="task-artifact-label-button"
                                onClick={() => onOpenCloudRunArtifact(task, artifact)}
                              >
                                {artifact.label}
                              </button>
                            ) : (
                              <span className="task-artifact-label">{artifact.label}</span>
                            )}
                            <span className="task-artifact-meta">
                              {artifactMeta(artifact)}
                            </span>
                            <span className="task-artifact-uri">
                              {artifact.redacted_uri}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {task.cloud_run_artifact_preview ? (
              <div className="task-artifact-preview">
                <h4 id={`task-${task.id}-artifact-preview-title`}>
                  {task.cloud_run_artifact_preview.artifact.label}
                </h4>
                <pre
                  aria-labelledby={`task-${task.id}-artifact-preview-title`}
                  role="region"
                  tabIndex={0}
                >{task.cloud_run_artifact_preview.content}</pre>
              </div>
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
