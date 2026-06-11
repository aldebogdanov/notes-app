import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { useLang } from '../i18n.jsx';

const FALLBACK_TIMEZONES = [
  'UTC',
  'Europe/London',
  'Europe/Berlin',
  'Europe/Moscow',
  'Asia/Tokyo',
  'America/New_York',
  'America/Los_Angeles',
];

function timezoneOptions() {
  if (typeof Intl.supportedValuesOf === 'function') {
    try {
      return Intl.supportedValuesOf('timeZone');
    } catch {
      // fall through to the static list
    }
  }
  return FALLBACK_TIMEZONES;
}

function detectedTimezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

export default function NotificationSettings() {
  const { t } = useLang();
  const [settings, setSettings] = useState(null);
  const [link, setLink] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  useEffect(() => {
    api
      .getNotificationSettings()
      .then(setSettings)
      .catch((err) => setError(err.message));
  }, []);

  const run = async (action) => {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      await action();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const startLink = () =>
    run(async () => {
      setLink(await api.telegramLink());
    });

  const verify = () =>
    run(async () => {
      // The linked badge appearing is the feedback; no extra success line.
      setSettings(await api.telegramVerify());
      setLink(null);
    });

  const unlink = () =>
    run(async () => {
      setSettings(await api.telegramUnlink());
      setLink(null);
    });

  const setEnabled = (enabled) =>
    run(async () => {
      setSettings(await api.updateNotificationSettings({ channels: { telegram: { enabled } } }));
      setSuccess(t('settings.notifications.saved'));
    });

  const saveTimezone = (timezone) =>
    run(async () => {
      setSettings(await api.updateNotificationSettings({ timezone }));
      setSuccess(t('settings.notifications.saved'));
    });

  const copyCode = async () => {
    if (navigator.clipboard && link) {
      await navigator.clipboard.writeText(link.code);
      setSuccess(t('settings.notifications.codeCopied'));
    }
  };

  if (!settings) {
    return (
      <section className="settings-card">
        <h2>{t('settings.notifications.title')}</h2>
        {error && <div className="error">{error}</div>}
      </section>
    );
  }

  const telegram = settings.channels?.telegram ?? {
    available: false,
    enabled: false,
    linked: false,
  };
  const detected = detectedTimezone();

  return (
    <section className="settings-card">
      <h2>{t('settings.notifications.title')}</h2>

      <div className="notification-settings">
        {!telegram.available && (
          <p className="settings-hint">{t('settings.notifications.serverUnavailable')}</p>
        )}

        {telegram.available && !telegram.linked && !link && (
          <button type="button" className="btn btn-primary" disabled={busy} onClick={startLink}>
            {t('settings.notifications.linkButton')}
          </button>
        )}
        {telegram.available && !telegram.linked && link && (
          <>
            <p className="settings-hint">{t('settings.notifications.linkHint')}</p>
            <a href={link.deep_link} target="_blank" rel="noreferrer">
              {t('settings.notifications.openTelegram')}
            </a>
            <div className="notification-row">
              <code>{link.code}</code>
              <button type="button" className="btn" disabled={busy} onClick={copyCode}>
                {t('settings.notifications.copyCode')}
              </button>
            </div>
            <button type="button" className="btn btn-primary" disabled={busy} onClick={verify}>
              {t('settings.notifications.verify')}
            </button>
          </>
        )}

        {telegram.available && telegram.linked && (
          <>
            <p className="notification-linked">{t('settings.notifications.linked')}</p>
            <label className="notification-toggle">
              <input
                type="checkbox"
                checked={telegram.enabled}
                disabled={busy}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              {t('settings.notifications.enableLabel')}
            </label>
            <button type="button" className="btn btn-danger" disabled={busy} onClick={unlink}>
              {t('settings.notifications.unlink')}
            </button>
          </>
        )}

        <hr className="notification-divider" />

        <label className="notification-timezone">
          {t('settings.notifications.timezoneLabel')}
          <select
            value={settings.timezone}
            disabled={busy}
            onChange={(e) => saveTimezone(e.target.value)}
          >
            {timezoneOptions().map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </select>
        </label>
        {settings.timezone !== detected && (
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => saveTimezone(detected)}
          >
            {t('settings.notifications.useMyTimezone', { timezone: detected })}
          </button>
        )}

        {error && <div className="error">{error}</div>}
        {success && <div className="success">{success}</div>}
      </div>
    </section>
  );
}
