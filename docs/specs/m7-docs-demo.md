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

**Written in Russian** — it is the letter to the reviewer and the task is in Russian
(the CLAUDE.md English-only rule covers code/comments/commits, not this document).

The six asked questions, answered honestly:

1. **Fork link** — this repo, `main` (all milestone PRs merged here).
2. **Tools** — Claude Code (Fable 5) driven through a spec-PR → implementation-PR
   workflow per milestone; Qdrant as dev-side memory between sessions (no product
   dependency).
3. **Why these tools** — the real reasons:
   - this spec-driven flow (spec PR → impl PR) was already battle-tested by the author
     with Claude on earlier work;
   - the only currently active paid AI subscription;
   - the Qdrant-memory tooling was already proven in this setup;
   - Fable 5 had just been released — deliberate test drive of the new model;
   - most of the author's AI-tooling experience is with Claude.
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
   - no CI config in the repo despite DoD-style gates;
   - product gap: reminders can only fire at local midnight because `note_date` has no
     time component — a per-note (or per-user default) reminder *time* would be the
     natural next product improvement.
5. **Next steps** — more notification adapters (Slack, Discord, email — the adapter
   registry and `note_notifications.channel` are already shaped for it), per-note
   reminder time (see observation above), toasts (ToastProvider mirroring the
   LangProvider idiom), note-list reminder-status badges, modal confirms,
   `FOR UPDATE SKIP LOCKED` + multi-worker scheduler, webhook-based Telegram updates,
   bot-message i18n, pin ruff + add CI.
6. **Known caveats** — at-least-once delivery (the crash window between send and
   finalize is not fixable by a transaction: the send is an external side effect and
   Telegram's `sendMessage` offers no idempotency key; at-most-once would trade a rare
   duplicate for a silently lost reminder — duplicate chosen deliberately),
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
