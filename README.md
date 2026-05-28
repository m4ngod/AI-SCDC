# AI Software Company Desktop Console

Phase 0 is a contract-first monorepo foundation for a desktop multi-agent software engineering console.

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
the minimal FastAPI task-creation path; `VITE_DEMO_PROJECT_ID` can pin the demo
project, otherwise the client creates or reuses one.

See `docs/architecture.md` for architecture and phase boundaries.
