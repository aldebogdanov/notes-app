# M5 — Frontend: notification settings panel

Implementation spec for milestone M5 from `SPEC.md`. Scope: the Settings-page UI over the
M4 API. No toasts (M6 — this milestone uses the existing inline error/success pattern of
`Settings.jsx`), no backend changes, no openapi changes.

## 1. API client (`frontend/src/api.js`)

Five thin methods on the existing `api` object (single `request()` wrapper, JWT handled
there):

```js
getNotificationSettings: () => request('/account/notifications/settings'),
updateNotificationSettings: (payload) =>
  request('/account/notifications/settings', { method: 'PUT', body: payload }),
telegramLink: () => request('/account/notifications/telegram/link', { method: 'POST' }),
telegramVerify: () => request('/account/notifications/telegram/verify', { method: 'POST' }),
telegramUnlink: () => request('/account/notifications/telegram/link', { method: 'DELETE' }),
```

## 2. Component (`frontend/src/components/NotificationSettings.jsx`)

New component rendered by `Settings.jsx` as a `settings-card` section between "Change
password" and the danger zone. Loads settings on mount; all strings via `useLang()`.

### States (telegram channel)

1. **Server not configured** (`available: false`): info line "Telegram notifications are
   not configured on this server", everything else hidden.
2. **Unlinked**: "Link Telegram" button → calls `telegramLink`, shows:
   - the deep link as a clickable anchor (opens `t.me/<bot>?start=<code>` in new tab)
     plus the raw code with a copy button (manual fallback);
   - expiry hint ("valid for 10 minutes");
   - "I pressed Start — verify" button → `telegramVerify`; 404 shows the API's
     "press Start first" message inline; success flips to linked state.
3. **Linked**: "✓ Telegram linked" badge, then:
   - **enable toggle** (checkbox) → `updateNotificationSettings({channels: {telegram:
     {enabled}}})`; the 409 path is unreachable from UI (toggle only shown when linked)
     but still surfaces inline if it happens;
   - **Unlink** button → `telegramUnlink` (flips back to unlinked, toggle off).

### Timezone block (channel-independent, shown whenever the panel loads)

- `<select>` of `Intl.supportedValuesOf('timeZone')` (fallback to a static short list if
  the API is missing — jsdom in tests), current value from settings;
- "Use my timezone: <detected>" helper button — `Intl.DateTimeFormat().resolvedOptions()
  .timeZone`, sets the select and saves;
- change → `updateNotificationSettings({timezone})`, inline success/error.

### Status indication

Reuse the page's existing inline `error`/`success` div pattern (M6 replaces with toasts).
Buttons disabled while a request is in flight (no double-submit).

## 3. i18n (`frontend/src/i18n.jsx`)

New `settings.notifications.*` keys, EN **and** RU (DoD #6), roughly:
title, serverUnavailable, linkButton, linkHint (expiry), openTelegram, copyCode, verify,
verifyPending (404 text), linked, unlink, enableLabel, timezoneLabel, useMyTimezone,
saved, error. The existing i18n parity test must keep passing (it walks both dicts).

## 4. Tests (`frontend/src/components/NotificationSettings.test.jsx`)

vitest + @testing-library/react, `vi.mock('../api.js')` (mirrors existing test style):

1. unavailable state renders info line, no buttons;
2. unlinked → click Link → deep link anchor + code rendered (href asserted);
3. verify success → linked badge + toggle appear (api mocked to return linked settings);
4. verify 404 → inline pending message, panel stays in link state;
5. toggle calls `updateNotificationSettings` with the right payload;
6. unlink returns panel to unlinked state, toggle gone;
7. timezone select renders current value; "use my timezone" saves detected zone
   (`Intl.DateTimeFormat` stubbed);
8. in-flight: buttons disabled while promise pending (controlled promise mock).

## 5. DoD mapping

| DoD item | M5 |
|---|---|
| `make up && make seed` | unaffected |
| `make test` | backend untouched; frontend suite grows (vitest) |
| `make lint` | eslint on new files |
| `openapi.json` | N/A — no backend changes |
| migration | N/A |
| i18n EN+RU | **the** gate for this PR — every new string in both dicts |
| `.env.example` | N/A |

## 6. Risks / notes

- `Intl.supportedValuesOf` is missing in older jsdom — component must feature-detect and
  fall back (also keeps tests deterministic).
- Verify is a manual button by design (matches M4's on-demand `getUpdates`); auto-polling
  would hammer the rate limiter.
- `NoteList` reminder-status badges are deliberately out of scope (SPEC.md M5 covers the
  Settings panel only); candidate for the "next steps" section of SUBMISSION.md.
