# M7 — Docs, demo & submission

Implementation spec for milestone M7 from `SPEC.md`. Scope: documentation and the
deliverables the task asks to submit — no application code changes (pure docs +
one demo shell script). Final mandatory milestone; M8/M9 optional extras decided after.

## 1. README.md — "Telegram reminders" section

New section after "What's inside" (feature bullet also added to that list):

- what it does: a note with a date triggers a Telegram message at 00:00 of that date in
  the user's timezone;
- setup: create a bot via @BotFather → `TELEGRAM_BOT_TOKEN=... make up` (or `.env`);
  explicit note: **the bot must not have a webhook configured** (linking uses
  `getUpdates`);
- linking walkthrough: Settings → Notifications → Link Telegram → press Start → Verify →
  enable toggle (+ timezone picker note);
- behavior summary table: pending/sent/skipped/failed semantics in two lines, link to
  `SPEC.md` for details;
- `SCHEDULER_ENABLED` mention in the env table/commands area.

## 2. Demo script (`scripts/demo-reminder.sh`, repo root `scripts/` — new dir, bash)

The "приложи короткое видео или последовательность команд" deliverable — we ship the
command sequence, runnable end-to-end:

1. preconditions check: stack up, `TELEGRAM_BOT_TOKEN` set in the backend container,
   `jq` present;
2. `make seed`, login as demo via curl → token;
3. `POST /telegram/link` → prints deep link, waits for operator to press Start
   (interactive `read`), `POST /telegram/verify`;
4. `PUT settings` enable telegram;
5. create a note dated today via API → scheduler wakes (CRUD pokes it) → poll
   `GET /api/notes/{id}` until `notification_status.telegram == "sent"` (timeout 90s);
6. prints PASS + tells the operator to check the Telegram message.

Idempotent against reruns (each run creates a fresh note). Script is dev tooling, not
product code: no tests, but `shellcheck`-clean and referenced from README.

## 3. SUBMISSION.md (repo root)

The six asked questions, answered honestly:

1. **Fork link** — this repo, `main` (all milestone PRs merged here).
2. **Tools** — Claude Code (Fable 5) driven through a spec-PR → implementation-PR
   workflow per milestone; Qdrant as dev-side memory between sessions (no product
   dependency).
3. **Why these tools** — 1–2 sentences: reviewable AI output via small human-gated PRs;
   specs force the design conversation before code.
4. **Scaffold observations** — the accumulated list:
   - `ruff>=0.5` unpinned → 0.15.x reformats old code, `make lint` red on clean checkout
     (fixed in M1, pin recommended);
   - tests run on SQLite while prod is Postgres → real bug class invisible (caught live:
     `SELECT DISTINCT` over `json` has no PG equality operator);
   - migrations never exercised by pytest (`create_all` fixtures) — downgrade verified
     manually only;
   - no user feedback layer: silent successes, inline error divs duplicated per page,
     raw `window.confirm` (M6 dropped in favor of this observation);
   - `notes.tags` JSON forces Python-side tag filtering (dialect-agnostic but O(n));
   - no CI config in the repo despite DoD-style gates.
5. **Next steps** — toasts (ToastProvider mirroring LangProvider idiom), note-list
   reminder-status badges, modal confirms, `FOR UPDATE SKIP LOCKED` + multi-worker
   scheduler, webhook-based Telegram updates, bot-message i18n, pin ruff + add CI.
6. **Known caveats** — at-least-once delivery (crash window between send and finalize),
   born-past-due minute-edge (note created 00:05 on its own date → skipped), bot text
   English-only, `getUpdates` linking breaks if the bot ever gets a webhook,
   single-process scheduler assumption.

## 4. SPEC.md / housekeeping (in this PR)

- M4/M5 marked ✅, M6 marked ❌ dropped with rationale (done in this spec PR);
- M7 milestone text updated to match this spec (demo script instead of video).

## 5. DoD mapping

| DoD item | M7 |
|---|---|
| `make up && make seed` | untouched |
| `make test` / coverage | no code changes — suite stays green |
| `make lint` | docs only; script shellcheck'd locally |
| `openapi.json` | N/A |
| migration | N/A |
| i18n | N/A (README/SUBMISSION are docs, not UI strings) |
| `.env.example` | N/A |

## 6. Risks / notes

- Demo script talks to real Telegram — it is excluded from pytest/vitest by location
  (`scripts/`, repo root) and never imported by the app.
- README stays the quickstart-first document; the reminders section is additive, no
  existing content rewritten (brownfield discipline to the end).
