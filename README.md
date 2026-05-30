# AI Software Company Desktop Console

This repo includes the Phase 0 monorepo foundation, Phase 1 planner approval loop, Phase 2 backend-first model routing and BYOK foundation, and Phase 3 real planner vertical slice for a desktop multi-agent software engineering console.

## Local Commands

```bash
pnpm install
python -m pip install -e "apps/api[test]" -e "apps/worker[test]" -e "services/llm-gateway[test]"
pnpm test
pnpm typecheck
pnpm dev:api
pnpm dev:desktop
```

The desktop runs in deterministic mock mode by default. Set
`VITE_API_BASE_URL=http://127.0.0.1:8000` before `pnpm dev:desktop` to enable
the minimal FastAPI planner approval path; `VITE_DEMO_PROJECT_ID` can pin the
demo project, otherwise the client creates or reuses one.

Phase 2 was backend-only. It added model providers, write-only BYOK credential metadata, role-based model routes, resolved fake fallback routes, and append-only usage ledger entries without making real provider calls. Credential responses remain metadata-only and never include raw or encrypted secrets.

## Phase 3 Local Real Planner Smoke Test

Phase 3 can call an OpenAI-compatible provider for planner drafts when the API has a configured planner route. DeepSeek can be configured as an OpenAI-compatible provider through the existing backend API. Do not paste API keys into chat, docs, or commits.

Example local setup:

```bash
pnpm dev:api
```

In another shell, set `DEEPSEEK_API_KEY` only for the local shell session, create a provider, create a credential, and create an active `planner` route:

```bash
export DEEPSEEK_API_KEY="<YOUR_LOCAL_API_KEY>"

curl -X POST http://127.0.0.1:8000/model-providers \
  -H "Content-Type: application/json" \
  -d '{"name":"Local DeepSeek","provider_type":"deepseek","base_url":"https://api.deepseek.com"}'

curl -X POST http://127.0.0.1:8000/model-credentials \
  -H "Content-Type: application/json" \
  -d "{\"provider_id\":\"<PROVIDER_ID>\",\"display_name\":\"Local key\",\"secret_value\":\"$DEEPSEEK_API_KEY\"}"

curl -X POST http://127.0.0.1:8000/model-routes \
  -H "Content-Type: application/json" \
  -d '{"agent_role":"planner","provider_id":"<PROVIDER_ID>","credential_id":"<CREDENTIAL_ID>","model_name":"deepseek-chat"}'
```

`<PROVIDER_ID>` and `<CREDENTIAL_ID>` come from the JSON responses of the previous requests. Then run the normal desktop planner flow with `VITE_API_BASE_URL=http://127.0.0.1:8000`.

Verify the created planner run used the real model path, because fallback also creates drafts. The planner run JSON should have `planner_kind == "model"` and `fallback_reason == null`. You can also check `/usage-ledger?planner_run_id=<PLANNER_RUN_ID>` for a model token entry. `<PLANNER_RUN_ID>` comes from the planner run JSON response.

Do not commit or share the `DEEPSEEK_API_KEY` value (`<YOUR_LOCAL_API_KEY>` in the example). Credential responses remain metadata-only; the API does not return raw or encrypted secrets.

See `docs/architecture.md` for architecture and phase boundaries.
