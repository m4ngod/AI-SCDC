# Phase 13C Usage Cost Quota Design

## Purpose

Phase 13C adds the first commercial cost-protection boundary. The goal is not
to bill users yet; it is to prevent unbounded execution-plane spend, record the
cost signals that already exist in the control plane, and expose workspace-level
usage data through stable API contracts for Phase 14 desktop and billing work.

## Scope

This slice covers:

- Extending `UsageType` beyond `model_tokens` with execution-plane cost
  categories: `cloud_run_runtime_seconds`, `worker_submissions`,
  `object_storage_bytes`, `object_storage_reads`, `log_sync_calls`,
  `queue_messages`, and `pr_publish_attempts`.
- Adding workspace-scoped wallet, spend-limit, and budget-reservation records.
- Blocking cloud-run enqueue when available credits cannot cover a reservation.
- Settling or releasing reservations when a cloud run reaches a terminal state,
  including failed runs.
- Adding a per-cloud-run cost summary API and workspace/project/task usage
  aggregation API.

This slice does not add Stripe, invoices, subscriptions, desktop UI beyond API
contracts, real provider price tables, or destructive provider cleanup.

## Architecture

The existing append-only `UsageLedgerEntry` remains the source of truth for
usage facts. New wallet and reservation models act as control-plane guardrails:
wallets hold manually granted credits, spend limits cap maximum reserved spend,
and reservations prevent enqueueing work that cannot be funded.

Cloud-run enqueue will call a small budget service before the `CloudRun` row is
created. The service reserves a conservative fixed estimate for one run. Cloud
run completion, failure, queued cancellation, and runtime submission failure
paths call the same service to settle or release the reservation and append
usage rows for worker submissions, queue messages, runtime seconds, object
storage bytes, and log sync calls when those dimensions are known.

Cost estimates stay deterministic and local for this phase. Provider-specific
pricing is deferred; the service records measurable units and an estimated
amount in cents using simple default constants that tests can assert.

## Data Model

`CreditWallet`:

- `workspace_id`, `organization_id`
- `balance_cents`
- timestamps

`SpendLimit`:

- `workspace_id`
- `monthly_limit_cents`
- `per_run_limit_cents`
- `status`
- timestamps

`BudgetReservation`:

- `workspace_id`, `project_id`, `task_id`, `cloud_run_id`
- `reserved_cents`
- `settled_cents`
- `status`: `reserved`, `released`, `settled`
- timestamps and optional `settled_at`

`CloudRun` adds nullable cost summary fields:

- `budget_reservation_id`
- `estimated_cost_cents`
- `actual_cost_cents`
- `cost_summary_json`

`actual_cost_cents` remains the Phase 13C billable-cost alias. Per-run cost
summary responses also expose `measured_cost_cents` and `billable_cost_cents`
so deterministic reservation caps are explicit until real provider price tables
exist.

`UsageLedgerEntry` gets optional execution dimensions:

- `cloud_run_id`
- `quantity`
- `unit_name`

Existing token fields remain for model-token compatibility.

## API Design

New endpoints:

- `POST /workspace/credits/manual-grants`
  - Development/operator-scoped endpoint for manual credit grants in this phase.
  - Request: `amount_cents`, `reason`.
  - Response: current wallet.
- `PUT /workspace/spend-limit`
  - Sets workspace spend limits.
  - Response: current spend limit.
- `GET /workspace/usage-summary`
  - Optional filters: `project_id`, `task_id`.
  - Returns grouped totals by `usage_type` and total estimated cents.
- `GET /cloud-runs/{cloud_run_id}/cost-summary`
  - Returns reservation, measured amount, billable amount, usage rows, and cost
    summary fields.

Existing `POST /usage-ledger` remains append-only and accepts the new usage
types and dimensions.

## Enqueue Behavior

When a cloud run is requested:

1. Validate the task, repository, profile, and provider selection as today.
2. Reserve a fixed estimate before persisting the cloud run.
3. If no wallet exists or available credits are too low, return `402 Payment
   Required` with `Insufficient credits`.
4. If the per-run spend limit is below the reservation estimate, return `402`
   with `Spend limit exceeded`.
5. On successful enqueue, attach the reservation id and estimate to the
   `CloudRun` row.

This keeps cost protection before queue submission and runtime provider calls.

## Settlement Behavior

Terminal cloud-run paths settle or release exactly once:

- Queued cancellation releases the reservation with zero actual cost.
- Failed enqueue before external submission releases the reservation.
- Runtime submission failure records `worker_submissions` and any queue-message
  usage that occurred, then settles.
- Worker completion records runtime seconds from `claimed_at` to `completed_at`
  when available. Failed runs still record actual measurable cost.
- Artifact refs and log-stream metadata contribute storage bytes and log sync
  usage when known.

Settlement is idempotent. Re-running a terminal callback or cleanup path must
not duplicate usage ledger rows.

## Error Handling

Budget failures use `402 Payment Required` because the request is valid but
cannot be funded. Cross-workspace filters continue to use the existing 404
resource-hiding behavior. Invalid negative values remain Pydantic 422 errors.

No raw secrets, encrypted secrets, callback tokens, token hashes, queue
receipts, or provider credentials are added to usage payloads or cost summaries.

## Testing

Tests must be written before implementation:

- Usage ledger accepts all new usage types and preserves append-only behavior.
- Workspace usage summary aggregates by usage type and filters by project/task.
- Manual credit grant creates or updates the active workspace wallet.
- Cloud-run enqueue returns 402 when credits are insufficient.
- Cloud-run enqueue creates a reservation when credits are sufficient.
- Queued cancellation releases the reservation.
- Failed run settles measurable usage and exposes a cost summary.
- Cross-workspace usage summary and cloud-run cost summary deny access.

## Non-Goals

- Stripe, payment methods, invoices, or subscriptions.
- Real Aliyun price tables.
- Desktop usage page implementation.
- Refactoring `cloud_runner.py` beyond small calls into the new budget service.
- Database-level immutable ledger enforcement beyond preserving no update/delete
  API routes in this slice.
