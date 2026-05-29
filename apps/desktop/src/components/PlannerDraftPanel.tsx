import type { PlannerRunDraft } from "../api/client";

type PlannerDraftPanelProps = {
  plannerRun: PlannerRunDraft | null;
  decisionStatus: string | null;
  decisionError: string | null;
  isDeciding: boolean;
  onApprove: () => Promise<void> | void;
  onReject: () => Promise<void> | void;
};

export function PlannerDraftPanel({
  plannerRun,
  decisionStatus,
  decisionError,
  isDeciding,
  onApprove,
  onReject
}: PlannerDraftPanelProps) {
  if (!plannerRun) {
    return null;
  }

  return (
    <section className="planner-draft-panel" aria-labelledby="planner-draft-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Planner draft</p>
          <h2 id="planner-draft-title">Planner draft</h2>
        </div>
        <span className="run-state">{decisionStatus ?? plannerRun.status}</span>
      </div>
      <p className="planner-goal">{plannerRun.goal}</p>
      <div className="planner-draft-list">
        {plannerRun.drafts.map((draft) => (
          <article className="planner-draft-card" key={draft.id}>
            <div className="section-heading">
              <h3>{draft.title}</h3>
              <span className="status-pill">{draft.role_required}</span>
            </div>
            <p>{draft.objective}</p>
            <dl>
              <div>
                <dt>Risk</dt>
                <dd>{draft.risk_level}</dd>
              </div>
              <div>
                <dt>Allowed paths</dt>
                <dd>{draft.allowed_paths.join(", ")}</dd>
              </div>
              <div>
                <dt>Acceptance</dt>
                <dd>{draft.acceptance_criteria.join("; ")}</dd>
              </div>
              <div>
                <dt>Tests</dt>
                <dd>{draft.required_tests.join("; ") || "None specified"}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
      {decisionError ? (
        <p className="goal-input-error" role="alert">
          {decisionError}
        </p>
      ) : null}
      <div className="planner-actions">
        <button type="button" onClick={onApprove} disabled={isDeciding || decisionStatus !== null}>
          Approve drafts
        </button>
        <button type="button" onClick={onReject} disabled={isDeciding || decisionStatus !== null}>
          Reject drafts
        </button>
      </div>
    </section>
  );
}
