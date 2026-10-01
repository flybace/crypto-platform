# Development Instructions

## Project and Scope

This repository is the independent Crypto Multi-Market Quant Platform.
Read `docs/AI_HANDOFF.md`, `docs/PROJECT_PLAN.md`, and
`docs/PARITY_MATRIX.md` before choosing a development task.
The newest recorded status is in `docs/STATUS_2026_09_28.md`; earlier
deployment and test records are historical evidence.

- `frontend/`: Vue 3, TypeScript, Vite, Pinia user interface.
- `backend/`: FastAPI composition, authenticated APIs, task control plane.
- `src/`: domain models, use cases, ports, exchange adapters, workers.
- `contracts/`: versioned data and capability contracts.
- `tests/`: unit, contract, replay, fake exchange, integration, acceptance.
- `proxy-stack/`: independent optional Mihomo route manager and Compose stack.

## Boundaries

- Keep execution mode `DISABLED`. Private exchange APIs, real accounts,
  orders, withdrawals, Testnet and GACE write actions require a separate
  explicitly authorized task after the reliability gates are complete.
- Prefer deterministic fixtures and fake exchanges for development.
- Never commit credentials, subscription URLs, private keys, `.env` files,
  logs, browser captures, runtime state, databases or collected market data.
- `.env.example` values are disposable development examples.
- Do not deploy, restart services, purge queues, remove volumes, replay
  production dead letters or modify another project as part of a code task.
- The proxy stack has its own lifecycle. Subscription refresh supplies
  nodes; rules and groups stay under the route manager's control.
- Preserve user changes and existing data. Do not reset or clean the checkout.

## Engineering

- Follow the existing domain/application/ports/adapters separation.
- Keep schemas compatible; version intentional contract changes.
- Fail closed on uncertain task dispatch, archive integrity or recovery.
- Keep API results bounded and redact network credentials.
- Keep every maintainable text file at or below 3000 lines. Consider
  splitting at 2400 lines; retain plan/mindmap section correspondence.
- Use task branches such as `codex/<task>` and propose focused pull requests.
- Report code, automated verification, local acceptance and deployed
  acceptance separately. Historical evidence is not current acceptance.

## Verification

Use Python 3.12 (the Docker baseline) and Node.js 22 for reproducible setup.

```sh
python -m pip install -e '.[runtime,test]'
python -m pytest -q
python -m compileall -q backend src tests
pwsh -NoProfile -File scripts/check-file-line-limit.ps1
pwsh -NoProfile -File scripts/check-plan-mindmap.ps1
cd frontend
npm ci
npm run build
```

For proxy stack changes, in a separate environment or after installing its
requirements, run from `proxy-stack/route-manager`:

```sh
python -m pip install -r requirements.txt pytest
python -m pytest -q -o pythonpath=. tests
```

A UI change also requires an actual browser check. A backend build or
HTTP 200 alone does not prove a complete workflow.
