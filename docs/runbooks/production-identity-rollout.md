# Production identity migration and rollout

This runbook deploys the production identity slice without reclassifying
ambiguous Legacy Account Storage, interrupting Workspace API Tokens, or
coupling Worker Callback Tokens to human sessions.

The rollout is controlled by deployment configuration. Do not put secrets in
the stage, allowlist, migration-mode, or release-gate variables.

## Observe the effective state

`GET /auth/rollout-status` returns the effective rollout stage, whether new
OIDC login and Cookie authentication are enabled, the self-registration
audience, the additive-schema state, and passed or missing release gates. The
response contains no provider credentials, tokens, allowlisted email
addresses, or customer identifiers.

`GET /health` returns `503` with component
`identity_schema_migration` while a dry run reports pending schema work. This
prevents a dry-run instance from receiving normal traffic.

## Rehearse and apply the additive schema

Run the rehearsal against a restored, representative production snapshot with
new login disabled:

```text
AI_SCDC_IDENTITY_ROLLOUT_STAGE=disabled
AI_SCDC_IDENTITY_SCHEMA_MIGRATION_MODE=dry_run
```

Start one instance and capture `/auth/rollout-status`. Repeating the request or
restarting the dry-run instance must return the same ordered
`pending_actions`; it must not create or modify business or identity records.

Apply the migration with one deployment instance:

```text
AI_SCDC_IDENTITY_ROLLOUT_STAGE=disabled
AI_SCDC_IDENTITY_SCHEMA_MIGRATION_MODE=apply
```

Wait for `/health` to return `200` and for `/auth/rollout-status` to report
`schema_ready: true` with no pending actions before scaling out. Retry the
apply-mode startup once as the idempotency rehearsal.

The migration adds identity tables and columns, Account classification and
ownership, session state, and audit correlation fields. Existing
Organization-shaped rows receive the explicit `legacy` classification and no
owner. The migration does not rename or delete a table, infer ownership from
email or membership, create an External Identity, or create a User, Account,
Workspace, membership, Device Session, or audit event.
Legacy Workspace and Secret Access audit rows that predate correlation fields
receive a deterministic, single-event `legacy_*` correlation value based only
on their existing row ID. This makes each row exportable without claiming that
unrelated legacy events belonged to one request.

After each rehearsal, verify an existing Workspace API Token can call `/me`
and a representative protected resource endpoint with its current role. Also
verify a representative restricted Worker Callback Token flow and existing
Workspace and Secret Access audit exports. Open an existing encrypted model
credential through an authorized execution path; the production rehearsal
must use restored production KMS ciphertext and the configured production
SecretVault rather than the development vault.

## Release gates

Public self-registration fails closed unless every name below appears in
`AI_SCDC_IDENTITY_RELEASE_GATES_PASSED` as a comma-separated value:

| Gate | Required evidence |
| --- | --- |
| `fake_provider_automation` | Deterministic fake-provider identity suites pass, including success, status changes, malformed responses, outage, replay, and recovery. |
| `real_ciam_smoke` | The Authing test-tenant release suite and the manual email-code acceptance flow in the Authing runbook pass. |
| `browser_cookie_csrf_e2e` | A same-origin browser run proves redirects, `__Host-` Cookie attributes, persistence, exact-Origin and CSRF rejection, Device Session actions, Recent Authentication, and Sign Out. |
| `migration_rollback_rehearsal` | Dry run, apply, retry, rollback, and recovery are rehearsed against the release snapshot. |
| `secret_leak_scan` | Responses, logs, telemetry, audits, traces, CI output, and artifacts are scanned using configured canary values, with no secret found. |
| `api_token_rbac_regression` | Workspace API Token live role, membership, User status, token revocation, and logout-independence tests pass. |
| `workspace_isolation_regression` | Cross-Workspace reads and writes remain rejected and no write is retried in another Workspace. |
| `secret_vault_kms_regression` | SecretVault/KMS readiness, ciphertext-only storage, audited open, and fail-closed outage tests pass. |
| `identity_audit_regression` | Identity Audit security events, immutability, correlation, and retention tests pass. |
| `workspace_audit_regression` | Workspace Audit allow/deny and correlation tests pass. |
| `secret_access_audit_regression` | Secret Access Audit create/open/replace/delete and correlation tests pass. |
| `worker_callback_regression` | Restricted Worker Callback Token route, expiry, replay, and human-credential separation tests pass. |
| `dependency_graph_complete` | Every blocker in the confirmed GitHub dependency graph is closed and its merge commit is present in the release baseline. |

The variable is an operator attestation to already captured evidence; setting
it does not execute or waive a gate. Archive the commit SHA, test output,
Authing tenant identifier, browser evidence, snapshot identifier, and rollback
result with the release record.

## Progress through reversible stages

Deploy each stage independently and confirm `/auth/rollout-status` before
proceeding:

1. `ciam_test_tenant` enables self-service only in the isolated Authing test
   tenant deployment. It is allowed in test/staging and is rejected by the
   production process.
2. `production_internal` admits only verified emails listed in
   `AI_SCDC_IDENTITY_INTERNAL_EMAIL_ALLOWLIST`. Matching is
   case-insensitive. The status endpoint never returns the allowlist.
3. `existing_beta` admits an already linked External Identity or an existing
   Legacy Account customer. An unlinked matching legacy email still returns
   `account_link_required`; a new customer returns
   `identity_rollout_denied`.
4. `public` enables public self-registration only after every release gate is
   present. New Accounts are Personal Accounts explicitly owned by the new
   User.

Moving back to an earlier non-rollback stage stops admissions outside that
stage without deleting data or rewriting current Account ownership. The
`disabled` stage stops new login while allowing otherwise-valid existing User
Sessions and Workspace API Tokens to continue.

## Security rollback

Set:

```text
AI_SCDC_IDENTITY_SECURITY_ROLLBACK=true
```

Security rollback overrides the selected rollout stage. It disables new OIDC
login and Cookie authentication, revokes every still-active User Session once,
and records an Identity Audit event for each revoked Device Session. Repeating
startup is idempotent and does not duplicate those revocation events.

Treat this as an isolated cutover, not a mixed rolling stage:

1. Stop routing new browser traffic to every non-rollback instance.
2. Drain in-flight login callbacks and remove all non-rollback instances.
3. Start the rollback deployment.
4. Call `/health` twice after the old instances are gone and require `200`
   both times. In rollback mode each health check repeats the conditional
   revocation sweep, so the final check catches a Device Session issued during
   the drain window without duplicating its audit event.
5. Archive the final health result and the corresponding rollback audit
   evidence before declaring the rollback complete.

Do not overlap rollback and non-rollback instances after the drain point. A
health check cannot prove completion while an old instance is still allowed
to issue sessions.

Authing configuration may be removed or unavailable during this emergency
mode. Workspace API Tokens remain accepted when their current local User,
Account, Workspace, membership, role, and token state is valid. Worker
Callback Tokens retain their independent route-constrained lifecycle.
Callbacks for Login Transactions issued before rollback are terminally
rejected and audited before any provider call. A prepared provider-logout
continuation is consumed locally, clears its sealed hint, records the provider
logout failure, and redirects only to the configured local origin.

Rollback never deletes Users, External Identities, Accounts, Workspaces,
memberships, credentials, resources, or audits and never runs a destructive
down migration. Restore access only after replacing every rollback instance
with the selected non-rollback stage. The final sweep includes sessions issued
during the cutover window, so users must complete a fresh Authing login;
revoked session credentials are never revived.
