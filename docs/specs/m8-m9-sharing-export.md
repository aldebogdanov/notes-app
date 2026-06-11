# M8+M9 — Public sharing & export (combined)

Implementation spec for the optional milestones M8 and M9 from `SPEC.md`, combined into
one spec PR + one implementation PR (both features are small and touch the same
surfaces: notes router, NoteEditor, i18n).

## 1. Goal

- **Sharing (M8)**: owner can publish a read-only link to a single note and revoke it.
- **Export (M9)**: owner can download one note as Markdown or all notes as a zip.

## 2. Migration `0004_share_token.py`

```python
op.add_column("notes", sa.Column("share_token", sa.String(64), nullable=True))
op.create_index("ix_notes_share_token", "notes", ["share_token"], unique=True)
```

`NULL` = not shared. `downgrade()` drops index + column. No backfill needed.
Token: `secrets.token_urlsafe(16)` (~22 chars, 128 bits — unguessable; no extra rate
limiting on the public endpoint).

## 3. Backend endpoints

### Sharing

| method/path | auth | behavior |
|---|---|---|
| `POST /api/notes/{id}/share` | owner | sets `share_token` if absent (idempotent — repeated POST returns the same token); response `NoteOut` |
| `DELETE /api/notes/{id}/share` | owner | clears the token (revoke); idempotent; `NoteOut` |
| `GET /api/public/notes/{token}` | **none** | `PublicNoteOut` or 404 (unknown token **or archived note** — archived = put away) |

- New router `app/routers/public.py` (`/public` prefix, no auth dependencies) — keeps
  the no-auth surface visibly separate from the authed routers.
- Share/revoke live in `app/routers/notes.py` next to archive/pin (same idiom).
- `NoteOut` gains `share_token: str | None` — owner-only schema, safe to expose there;
  the frontend builds the public URL as `{origin}/share/{token}`.
- `PublicNoteOut`: `title, content, tags, note_date, updated_at` — **nothing else**: no
  ids, no owner info, no notification fields. The shared view is live (always current
  content), revocation is immediate.

### Export

| method/path | auth | behavior |
|---|---|---|
| `GET /api/notes/{id}/export` | owner | single Markdown file: `# {title}\n\n{content}`; `Content-Disposition: attachment; filename="{slug}.md"`; `text/markdown` |
| `GET /api/notes/export` | owner | zip of **all** notes (active + archived), flat, `{id}-{slug}.md` entries; in-memory `zipfile` → `Response`, `application/zip`, filename `notes-export-{YYYY-MM-DD}.zip` |
| `POST /api/notes/bulk-export` | owner | body `{"ids": [int]}` (`BulkDeleteIn` shape/limits) → zip of the selected owned notes; foreign ids silently skipped (bulk-delete idiom); nothing exportable → 404 |

- Bulk export mirrors `bulk-delete`: POST because the id list rides the body; reuses
  the same zip builder as export-all.
- `_slug(title)`: lowercase, alnum + dash, max 40 chars, fallback `"note"`.
- Route order matters: `/notes/export` must be declared **before** `/notes/{note_id}`
  (FastAPI matches in declaration order; same reason `/notes/calendar` already sits
  above it).

## 4. Frontend

- **api.js**: `shareNote(id)`, `revokeShare(id)`, `getPublicNote(token)` via the
  existing `request()`; plus a `download(path)` helper (fetch with auth header →
  blob → temporary `<a download>` click) used by `exportNote(id)` / `exportAll()` —
  the JWT lives in localStorage, so a plain `<a href>` cannot authenticate.
- **NoteEditor**: two buttons in the existing action row — Share/Unshare toggle
  (shows the copyable public URL when shared, `navigator.clipboard`) and
  "Export .md". Driven by `note.share_token` from `NoteOut`.
- **Public page**: new route in `App.jsx` **outside** `RequireAuth`:
  `<Route path="/share/:token" element={<SharedNote />} />`. `pages/SharedNote.jsx`:
  fetches `getPublicNote`, renders title + `react-markdown` body + a "read-only shared
  note" hint; 404 → friendly "link is invalid or revoked" message. No app chrome
  dependencies that require auth.
- **Settings**: "Export all notes (.zip)" button in a small new card (data ownership
  lives naturally next to account management).
- **Notes select mode**: "Export ({count})" button next to the existing
  "Delete ({count})" — `bulkExport(ids)` through the download helper.
- i18n: `share.*` and `export.*` keys, EN + RU.

## 5. Tests

Backend (`test_sharing.py`, `test_export.py`):
1. share → token appears in `NoteOut`, stable across repeated POST; revoke → null;
   re-share → **new** token (old link dead);
2. public GET: shared note → 200 with exactly the `PublicNoteOut` fields (leak scan:
   response JSON has no `id`/`user_id`/`notification_status`); unknown token → 404;
   revoked → 404; archived-but-shared → 404; edits visible live;
3. ownership: non-owner share/revoke → 404 (same `_own_note_or_404` idiom);
4. export single: content + content-disposition + slug; foreign note → 404;
5. export all: zip opens, one entry per note incl. archived, entries non-empty;
6. bulk export: zip contains exactly the selected owned notes; foreign ids silently
   skipped; all-foreign / empty result → 404;
7. openapi snapshot regenerated (drift test guards).

Frontend (`SharedNote.test.jsx`, NoteEditor test extension):
7. SharedNote renders fetched note; error state on 404;
8. share button calls api and shows the public URL; export button triggers download
   helper (mocked).

## 6. DoD mapping

| DoD item | M8+M9 |
|---|---|
| `make up && make seed` | migration 0004 runs on boot; seed untouched |
| `make test` ≥ 80% | §5 |
| `make lint` | ruff + eslint |
| `openapi.json` | **regenerated** — four new endpoints + NoteOut change |
| migration downgrade | 0004 verified up/down/up in PR description |
| i18n EN+RU | `share.*` / `export.*` keys in both dicts |
| `.env.example` | no new env vars — N/A |

## 7. Risks / notes

- Public endpoint returns live content: revocation must be checked on every request
  (no caching headers; explicitly send `Cache-Control: no-store`).
- Re-share after revoke intentionally rotates the token — a leaked old link must not
  resurrect.
- Zip is built in memory — fine at personal-notes scale; streaming zips are out of
  scope (SUBMISSION note).
- `SharedNote` page is served by the SPA — deep link works because Vite (and any static
  host with SPA fallback) rewrites unknown paths to `index.html`.
