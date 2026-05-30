# AI Software Company Desktop Console

This repo includes the Phase 0 monorepo foundation, Phase 1 planner approval loop, and Phase 2 backend-first model routing and BYOK foundation for a desktop multi-agent software engineering console.

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

Phase 2 is backend-only. The API can create model providers, write-only BYOK credential metadata, role-based model routes, resolved fake fallback routes, and append-only usage ledger entries. It does not call real model providers yet, and credential responses never include raw or encrypted secrets.

## Phase 3 Local Real Planner Smoke Test

Phase 3 can call an OpenAI-compatible provider for planner drafts when the API has a configured planner route. DeepSeek can be configured as an OpenAI-compatible provider through the existing backend API. Do not paste API keys into chat, docs, or commits.

Example local setup:

```bash
pnpm dev:api
```

In another shell, create a provider, create a credential with your local API key in the request body, create an active `planner` route, then run the normal desktop planner flow with `VITE_API_BASE_URL=http://127.0.0.1:8000`.

Credential responses remain metadata-only. The API does not return raw or encrypted secrets.

See `docs/architecture.md` for architecture and phase boundaries.
