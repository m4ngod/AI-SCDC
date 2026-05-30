# AI Company Console Phase 3 Real Planner Vertical Slice Design

## Goal

Build the first real model-backed planner path for **AI Software Company Desktop Console** while preserving the Phase 1 human approval boundary and the Phase 2 model routing/BYOK control-plane foundation.

Phase 3 turns the existing backend records into a working vertical slice:

```text
desktop goal
  -> API planner run
  -> resolve planner model route
  -> decrypt development BYOK credential internally
  -> call OpenAI-compatible chat completions through the gateway package
  -> parse TaskSpec drafts
  -> persist drafts for human approval
  -> append usage ledger entry
```

If the model route, credential, provider request, or model output is not usable, the API falls back to the existing `FakePlanner` and records why.

## Confirmed Decisions

- Phase 3 uses a generic OpenAI-compatible adapter, with DeepSeek as the first documented example provider configuration.
- Real planner output only creates `TaskSpec` drafts. Humans must still approve or reject drafts before tasks are created.
- Phase 3 is backend-first and does not add a desktop model settings UI.
- Provider failure, missing credentials, unavailable routes, or invalid model output fall back to `FakePlanner`.
- Phase 3 changes the development `DevSecretVault` so the server can decrypt secrets internally for local real-provider calls.
- Credential values remain write-only through the API. They are never returned in responses, read schemas, or validation error bodies.
- The API process calls the `services/llm-gateway` package directly. Phase 3 does not run a separate gateway HTTP service.
- Automated tests use a mock OpenAI-compatible server or fake transport. They do not require a real DeepSeek key.

## Scope

Phase 3 includes:

- A reversible development vault interface for local BYOK credentials.
- OpenAI-compatible chat completion adapter in `services/llm-gateway`.
- API service integration that resolves the planner route and calls the gateway package in-process.
- Prompt construction for model-backed planner runs.
- Strict parsing and validation of model output into existing `TaskSpecDraft` / `PlannerTaskDraft` shapes.
- Planner run metadata that records whether the run used a model, fake planner, or model-to-fake fallback.
- Usage ledger append on successful real model calls.
- Fallback reason recording when the real planner path cannot be used.
- Tests for successful model planning, fallback behavior, usage logging, secret redaction, and approval-flow compatibility.
- Documentation updates for local DeepSeek/OpenAI-compatible manual testing.

Phase 3 excludes:

- Desktop model settings UI.
- Automatic task creation without approval.
- Standalone LLM gateway HTTP service.
- Production KMS, OS keychain, or cloud secret manager.
- Streaming completions.
- Multi-turn model repair loops.
- Real provider pricing or billing calculations.
- Production organization auth/RBAC.

## Architecture

### Existing Components

`apps/api` continues to own:

- planner run lifecycle,
- planner draft persistence,
- approval/rejection behavior,
- task creation after approval,
- model provider/credential/route records,
- usage ledger records.

`services/llm-gateway` remains the network boundary package for provider adapters and provider response contracts.

### New Components

#### Reversible Development Vault

`DevSecretVault` gains an `open()` operation in addition to `seal()`.

The API contract remains write-only:

- `POST /model-credentials` accepts `secret_value`.
- Credential read models return metadata only.
- OpenAPI read schemas do not expose `secret_value` or `encrypted_secret`.
- validation errors redact `secret_value`.

The storage is explicitly development-only. It may be reversible local encryption or an encoded authenticated envelope suitable for local tests. It is not a production KMS design.

#### OpenAI-Compatible Adapter

The gateway package adds an adapter for providers that expose a `/chat/completions`-compatible API.

Responsibilities:

- Build an HTTP request from provider config, model name, prompt messages, and secret credential.
- Send `Authorization: Bearer <api_key>`.
- Apply a finite timeout.
- Parse response content from `choices[0].message.content`.
- Parse usage tokens from `usage.prompt_tokens` and `usage.completion_tokens` when present.
- Return a secret-free response object.
- Raise typed errors for provider request failure, non-2xx status, malformed response, or missing content.

DeepSeek is represented as provider metadata:

```json
{
  "name": "deepseek-dev",
  "provider_type": "deepseek",
  "base_url": "https://api.deepseek.com"
}
```

The adapter remains generic and does not hard-code a DeepSeek-only client.

#### Model Planner Service

The API adds a model-backed planner service behind the existing planner run creation path.

Responsibilities:

- Resolve the planner route using existing Phase 2 route logic.
- Decide whether the real model path is available.
- Load provider, credential, and route metadata.
- Decrypt the credential internally.
- Construct planner prompt messages.
- Call the gateway adapter.
- Parse and validate model output.
- Persist planner drafts.
- Update planner run model metadata.
- Append usage ledger entry for successful model calls.
- Fall back to `FakePlanner` on configured failure cases.

## Data Model Changes

`PlannerRun` gains model execution metadata:

- `planner_kind: str`
  - existing field, extended values:
    - `"fake"`
    - `"model"`
    - `"model_fallback_fake"`
- `model_route_id: str | None`
- `model_provider_name: str | None`
- `model_name: str | None`
- `fallback_reason: str | None`

These fields let the desktop and tests understand how a planner run was produced without changing the approval API.

### Fallback Reasons

Supported fallback reason values:

- `"no_configured_route"`
- `"provider_unavailable"`
- `"credential_unavailable"`
- `"provider_request_failed"`
- `"invalid_model_output"`

When the configured route is fake by design, the API uses `planner_kind = "fake"` and does not need a fallback reason.

## Planner Data Flow

1. Desktop submits a goal through the existing planner run API.
2. API creates a `PlannerRun` shell or prepares one through the existing repository path.
3. API resolves the active planner route.
4. If the route is fake, missing, or unavailable, API uses `FakePlanner`.
5. If the route is available:
   - provider metadata is loaded,
   - credential metadata is loaded,
   - `DevSecretVault.open()` recovers the secret inside the server,
   - OpenAI-compatible request messages are generated,
   - gateway adapter calls the provider,
   - model text is parsed as JSON,
   - parsed drafts are validated against existing TaskSpec constraints.
6. Valid drafts are persisted as `PlannerTaskDraft` rows.
7. On successful model calls, usage ledger receives a model token entry tied to the planner run and project.
8. The response returns the planner run with drafts, preserving the current desktop flow.
9. Human approval or rejection works exactly as in Phase 1.

## Prompt and Output Contract

The model receives:

- a system message that says it is a planner for AI Software Company Desktop Console,
- the expected JSON-only output format,
- allowed agent roles and risk levels,
- a user message containing the goal and any available project context.

The model must output only a JSON array of draft objects with fields compatible with the existing draft schema:

- `title`
- `role_required`
- `objective`
- `acceptance_criteria`
- `allowed_paths`
- `required_tests`
- `risk_level`

The parser must reject:

- non-JSON text,
- Markdown fences that cannot be safely unwrapped,
- missing required fields,
- extra fields if the existing schema disallows them,
- unsupported `role_required` values,
- unsupported `risk_level` values,
- empty scalar strings,
- empty list items.

Phase 3 does not add a model-output repair loop. Invalid output falls back to fake.

## Error Handling

The planner creation request should still succeed with fake drafts when the real planner path fails for an expected operational reason.

Fallback behavior:

- No active planner route: fake planner, `fallback_reason = "no_configured_route"`.
- Route resolves to fake provider: fake planner, no fallback reason required.
- Provider disabled or otherwise unavailable: fake planner, `fallback_reason = "provider_unavailable"`.
- Missing, deleted, or undecryptable credential: fake planner, `fallback_reason = "credential_unavailable"`.
- Provider timeout, network failure, non-2xx response, or malformed provider response: fake planner, `fallback_reason = "provider_request_failed"`.
- Model content fails JSON parsing or TaskSpec validation: fake planner, `fallback_reason = "invalid_model_output"`.

Usage ledger append failure must not break planner run creation. Phase 3 can log or otherwise contain the failure; it does not need a compensation job.

Unexpected programming errors should still surface as normal server errors during development.

## Security Boundaries

- Raw API keys enter the system only through credential creation.
- Raw API keys are never returned by API responses.
- Raw API keys are never included in credential read schemas.
- Raw API keys are redacted from validation errors.
- Provider `default_headers` continue to reject secret-like header names.
- Provider request errors must not echo the API key.
- The gateway adapter response object must not contain the API key.
- Model output is untrusted input and must pass validation before persistence.
- Reversible `DevSecretVault` is development-only and must be documented as not production encryption.

## API Behavior

No new desktop-facing endpoint is required for the vertical slice.

Existing planner run creation behavior changes internally:

- If a real planner route is configured and usable, the created planner run contains model-generated drafts.
- If the real route is not usable, the created planner run contains fake drafts and fallback metadata.
- Approval and rejection endpoints remain unchanged.

Existing model settings endpoints remain the configuration path:

- `POST /model-providers`
- `POST /model-credentials`
- `POST /model-routes`
- `GET /model-routes/resolve`

The README should document a minimal local DeepSeek-style configuration flow using these endpoints without placing a real key in docs.

## Usage Ledger Behavior

On successful model-backed planner calls, append one usage ledger entry with:

- `project_id`
- `planner_run_id`
- `usage_type = "model_tokens"`
- `provider_name`
- `model_name`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- raw usage JSON with non-secret provider usage details

Fallback planner runs do not create fake model usage entries.

Phase 3 does not calculate real price. `unit_price_cents` and `amount_cents` can remain zero.

## Testing Strategy

### Gateway Tests

Add tests that prove:

- adapter sends an OpenAI-compatible chat completion request,
- adapter uses `Authorization: Bearer <secret>` internally,
- response objects do not contain the secret,
- valid responses return content and token usage,
- non-2xx responses raise provider request errors,
- malformed responses raise malformed response errors,
- timeout/request errors are mapped to provider request errors.

Use a mock HTTP transport or local mock OpenAI-compatible server. Do not use real DeepSeek credentials in automated tests.

### Secret Vault Tests

Add tests that prove:

- `seal()` output does not contain plaintext,
- `open()` recovers the original secret,
- invalid vault payloads cannot be opened as secrets,
- credential API responses still exclude `secret_value` and `encrypted_secret`,
- validation errors still redact submitted `secret_value`.

### Planner Integration Tests

Add API/service tests that prove:

- configured active planner route with active credential and mock provider produces model-backed drafts,
- planner run records `planner_kind = "model"` and route/provider/model metadata,
- successful model planner call appends usage ledger entry,
- missing route falls back to fake with `no_configured_route`,
- missing or deleted credential falls back with `credential_unavailable`,
- provider request failure falls back with `provider_request_failed`,
- invalid model output falls back with `invalid_model_output`,
- fallback runs do not append fake model usage,
- existing approval flow still creates tasks only after human approval.

### Regression Verification

Phase 3 is complete only when these pass:

```bash
pnpm test
pnpm typecheck
pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v
git diff --check
```

## Manual DeepSeek Smoke Test

Manual smoke testing can use the existing API endpoints:

1. Create a DeepSeek provider with base URL `https://api.deepseek.com`.
2. Create a model credential by sending the API key to `POST /model-credentials`.
3. Create an active planner route for the DeepSeek provider, credential, and model name.
4. Run the existing desktop/API planner flow.
5. Confirm the returned planner run has `planner_kind = "model"`.
6. Confirm usage ledger contains a planner-run model token entry.

The key must be entered locally through the API request body or future UI. It must not be pasted into chat, committed, or placed in documentation.

## Success Criteria

Phase 3 is complete when:

- A developer can configure an OpenAI-compatible provider, BYOK credential, and planner route through the API.
- Planner run creation uses the configured real provider when available.
- Model output is validated before drafts are persisted.
- Human approval remains required before task creation.
- Provider, credential, request, or parse failures fall back to fake planner with recorded reason.
- Successful model-backed planner runs append usage ledger entries.
- No API response or validation error exposes raw or encrypted credentials.
- The gateway adapter is covered by deterministic tests without real provider credentials.
- The repository passes the agreed test and typecheck commands.

## Future Deferrals

- Desktop model settings UI.
- Standalone gateway HTTP service.
- Production vault/KMS integration.
- Streaming model responses.
- Model output repair and retry loops.
- Per-organization provider credentials and production RBAC.
- Price table and credit wallet mutation.
- Local Runner implementation.
