# Notes

A personal Markdown notes app. Keep notes, tag them, search them, and pin some to a date so you can browse them on a calendar.

Each user has their own private space. Every note is a Markdown document with a live preview while editing.

## What's inside

- Log in / register (single-user-per-account — no sharing).
- CRUD for notes with Markdown preview.
- Tags with filtering.
- Full-text search across title and body.
- Optional date on a note + a calendar view.
- Optional Telegram reminder when a note's date arrives.
- Read-only public link for a note (revocable).
- Export: one note as Markdown, selected or all notes as a zip.

## Run it

Requirements: Docker with Compose.

```bash
make up          # start db + backend + frontend
make seed        # (optional) create a demo user with a few notes
```

Then open <http://localhost:5173>.

Demo credentials (after `make seed`):

- **username:** `demo`
- **password:** `demo1234`

## Common commands

```bash
make help        # list all targets
make logs        # tail logs
make test        # run backend tests
make down        # stop the stack
make clean       # stop and wipe the database volume
```

## Telegram reminders

A note with a date can ping you on Telegram at 00:00 of that date in your timezone
(a note dated today is delivered right away).

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
   The bot must **not** have a webhook configured — account linking relies on
   `getUpdates`.
2. Start the stack with the token (or put it into `backend/.env`):

   ```bash
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token make up
   ```

   Without a token the app runs exactly as before; due reminders resolve to `skipped`.
3. In the app: **Settings → Notifications → Link Telegram** → open the deep link and
   press **Start** → back in Settings press **Verify** → flip the enable toggle and
   pick your timezone.

Per note and channel the reminder status is `pending` → `sent`, or `skipped`
(notifications off / unlinked / no server token / note archived / note created after
its date), or `failed` (Telegram kept erroring after 3 attempts). The note API exposes
it as `notification_status`. Design details: `SPEC.md` and `docs/specs/`.

The scheduler runs inside the backend process (`SCHEDULER_ENABLED=true` by default;
the test suite disables it).

Terminal end-to-end demo (requires `jq` and a token-enabled stack):

```bash
scripts/demo-reminder.sh
```

## Sharing & export

- **Share**: open a note → **Share** — you get `/share/<token>` to send around; the page
  is read-only and always shows the current content. **Unshare** kills the link
  immediately (re-sharing issues a new token).
- **Export**: a single note exports as `.md` from the editor; selected notes export as
  a zip from the list's select mode; everything (including archived) — from
  Settings → Export.

## Layout

- `backend/` — FastAPI + SQLAlchemy + Alembic, talks to Postgres.
- `frontend/` — React + Vite.
- `docker-compose.yml` — db + backend + frontend.
- `scripts/` — dev-side helpers (reminder demo).
