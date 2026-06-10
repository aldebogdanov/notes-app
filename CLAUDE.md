# CLAUDE.md — working agreements for this repo

Brownfield FastAPI + Postgres + React/Vite notes app (Docker Compose). We are implementing
`SPEC.md` (Telegram reminders test issue, see `issue.pdf`). Read `SPEC.md` before any work.

## Remotes — critical

- `origin` = `aldebogdanov/notes-app` (our fork; all pushes and PRs go here, PR base = fork's `main`).
- `upstream` = `ShmakovVA/notes-app` (interviewer's repo, READ-only). **Never push, never open
  PRs against upstream.** Upstream has open PRs (#7, #8) with other people's solutions to the
  same task — do not read or reuse them.

## Workflow

- One milestone from `SPEC.md` = two Pull Requests, in order:
  1. **Spec PR** (`spec/m<N>-<slug>`) — detailed implementation spec in
     `docs/specs/m<N>-<slug>.md`: migration DDL, model/schema changes, test list,
     DoD mapping, risks. Merged before implementation starts.
  2. **Implementation PR** (`feat/m<N>-<slug>`) — code following the merged spec.
     If implementation must deviate, update the spec doc in the same PR and call it
     out in the description.
- PRs must stay compact and human-reviewable. If a milestone grows too big, split it and
  update `SPEC.md` rather than shipping a huge diff.
- Never commit directly to `main`. Don't start milestone N+1 in the same branch as N.
- Mark milestone progress in `SPEC.md` only when its PR is merged.
- PR description: what/why, how to verify locally, and a DoD checklist (below) with each
  item explicitly checked.

## Commands (everything runs in Docker)

- `make up` — start stack (db + backend :8000 + frontend :5173), runs migrations on boot.
- `make seed` — demo user `demo`/`demo1234` with sample notes (idempotent, wipes demo user).
- `make test` / `make test-backend` / `make test-frontend`.
- `make lint` — ruff check + format check, eslint.
- `make migrate` — `alembic upgrade head` inside backend container.
- `make openapi-dump` — regenerate `backend/openapi.json` (required after endpoint changes).
- `make clean` — stop and wipe db volume.

## Definition of Done — gate for every PR

1. `make up && make seed` works on a clean machine.
2. `make test` green; backend coverage ≥ 80% (enforced by `pytest.ini`).
3. `make lint` green.
4. Endpoint added/changed → `backend/openapi.json` regenerated via `make openapi-dump`.
5. New migration has a working `downgrade()`; `make seed` works after it.
6. Every new UI string present in both EN and RU in `frontend/src/i18n.jsx`.
7. New env vars added to `backend/.env.example` (and `docker-compose.yml` if the container
   needs them).

## Conventions

- Code, comments, identifiers, commit messages: English.
- Backend: SQLAlchemy 2.0 typed `Mapped[...]` style (see `app/models.py`), Pydantic schemas
  in `app/schemas.py`, routers in `app/routers/`, settings via `app/config.py`
  (pydantic-settings). Follow existing rate-limit / auth dependency patterns.
- Migrations: sequential `000N_<slug>.py` in `backend/alembic/versions/`.
- Tests: pytest in `backend/tests/` (see `conftest.py` for app/db fixtures); frontend uses
  vitest co-located `*.test.jsx`.
- No real network in tests: mock Telegram HTTP (respx/httpx mock), inject fake adapters and
  a fake clock into the scheduler. Never `sleep()` in tests.
- The app must boot and pass tests without `TELEGRAM_BOT_TOKEN` set (optional env var).
- Frontend: follow existing component style in `frontend/src/components/`, API calls only
  through `frontend/src/api.js`, strings only through `useLang()` i18n.

## Qdrant memory (development only — never a product dependency)

Collection **`dev-notes-app`**. Primary interface: the `qdrant-mem` CLI via Bash
(the `qdrant-local` MCP is currently broken — empty errors; if its `qdrant-store`/
`qdrant-find` tools work again, they are equivalent and interchangeable):

- `qdrant-mem find "query" [-n 5]` — semantic search.
- `qdrant-mem store "short note text"` — store a note.
- `qdrant-mem list` — dump stored notes.
- Lives at `~/.local/bin/qdrant-mem`, same storage format as the MCP. Never commit it
  or any Qdrant tooling into this repo.

- Before starting a milestone: `qdrant-find` for prior decisions/findings about it.
- After finishing a milestone or making a non-obvious decision (design choice, gotcha in
  scaffold, failed approach): `qdrant-store` a short note (what, why, where in code).
- Store facts about the repo and decisions — not code dumps, not secrets/tokens.
- Qdrant is a dev-side memory aid only: no Qdrant code, config, or dependency may appear
  in the application itself.
