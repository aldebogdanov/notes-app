import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import NotificationSettings from './NotificationSettings.jsx';
import { LangProvider } from '../i18n.jsx';
import { api } from '../api.js';

vi.mock('../api.js', () => ({
  api: {
    getNotificationSettings: vi.fn(),
    updateNotificationSettings: vi.fn(),
    telegramLink: vi.fn(),
    telegramVerify: vi.fn(),
    telegramUnlink: vi.fn(),
  },
}));

const UNAVAILABLE = {
  timezone: 'UTC',
  channels: { telegram: { available: false, enabled: false, linked: false } },
};
const UNLINKED = {
  timezone: 'UTC',
  channels: { telegram: { available: true, enabled: false, linked: false } },
};
const LINKED = {
  timezone: 'UTC',
  channels: { telegram: { available: true, enabled: false, linked: true } },
};
const LINK = { code: 'abc123', deep_link: 'https://t.me/test_bot?start=abc123', expires_in: 600 };

function renderPanel() {
  return render(
    <LangProvider>
      <NotificationSettings />
    </LangProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('NotificationSettings', () => {
  it('shows server-unavailable info and no link button', async () => {
    api.getNotificationSettings.mockResolvedValue(UNAVAILABLE);
    renderPanel();

    expect(
      await screen.findByText('Telegram notifications are not configured on this server.')
    ).toBeInTheDocument();
    expect(screen.queryByText('Link Telegram')).not.toBeInTheDocument();
  });

  it('link flow renders deep link and code', async () => {
    api.getNotificationSettings.mockResolvedValue(UNLINKED);
    api.telegramLink.mockResolvedValue(LINK);
    renderPanel();

    await userEvent.click(await screen.findByText('Link Telegram'));

    const anchor = await screen.findByText('Open Telegram bot');
    expect(anchor).toHaveAttribute('href', LINK.deep_link);
    expect(screen.getByText('abc123')).toBeInTheDocument();
    expect(screen.getByText('I pressed Start — verify')).toBeInTheDocument();
  });

  it('verify success flips panel to linked state', async () => {
    api.getNotificationSettings.mockResolvedValue(UNLINKED);
    api.telegramLink.mockResolvedValue(LINK);
    api.telegramVerify.mockResolvedValue(LINKED);
    renderPanel();

    await userEvent.click(await screen.findByText('Link Telegram'));
    await userEvent.click(await screen.findByText('I pressed Start — verify'));

    expect(await screen.findByText('Telegram linked ✓')).toBeInTheDocument();
    expect(screen.getByText('Unlink Telegram')).toBeInTheDocument();
    expect(screen.queryByText('Open Telegram bot')).not.toBeInTheDocument();
  });

  it('verify 404 shows inline error, stays in link state', async () => {
    api.getNotificationSettings.mockResolvedValue(UNLINKED);
    api.telegramLink.mockResolvedValue(LINK);
    api.telegramVerify.mockRejectedValue(new Error('Press Start in Telegram, then retry'));
    renderPanel();

    await userEvent.click(await screen.findByText('Link Telegram'));
    await userEvent.click(await screen.findByText('I pressed Start — verify'));

    expect(
      await screen.findByText('Press Start in Telegram, then retry')
    ).toBeInTheDocument();
    expect(screen.getByText('I pressed Start — verify')).toBeInTheDocument();
  });

  it('toggle sends the right payload', async () => {
    api.getNotificationSettings.mockResolvedValue(LINKED);
    api.updateNotificationSettings.mockResolvedValue({
      ...LINKED,
      channels: { telegram: { ...LINKED.channels.telegram, enabled: true } },
    });
    renderPanel();

    await userEvent.click(await screen.findByRole('checkbox'));

    expect(api.updateNotificationSettings).toHaveBeenCalledWith({
      channels: { telegram: { enabled: true } },
    });
    expect(await screen.findByRole('checkbox')).toBeChecked();
  });

  it('unlink returns panel to unlinked state', async () => {
    api.getNotificationSettings.mockResolvedValue(LINKED);
    api.telegramUnlink.mockResolvedValue(UNLINKED);
    renderPanel();

    await userEvent.click(await screen.findByText('Unlink Telegram'));

    expect(await screen.findByText('Link Telegram')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('timezone select saves and detect button uses browser zone', async () => {
    api.getNotificationSettings.mockResolvedValue({ ...UNLINKED, timezone: 'Europe/Berlin' });
    api.updateNotificationSettings.mockResolvedValue({ ...UNLINKED, timezone: 'Asia/Tokyo' });
    renderPanel();

    await userEvent.selectOptions(await screen.findByRole('combobox'), 'Asia/Tokyo');
    expect(api.updateNotificationSettings).toHaveBeenCalledWith({ timezone: 'Asia/Tokyo' });

    // jsdom resolves the browser timezone as UTC
    await userEvent.click(screen.getByText('Use my timezone: UTC'));
    expect(api.updateNotificationSettings).toHaveBeenCalledWith({ timezone: 'UTC' });
  });

  it('buttons disabled while a request is in flight', async () => {
    api.getNotificationSettings.mockResolvedValue(UNLINKED);
    let resolveLink;
    api.telegramLink.mockReturnValue(
      new Promise((resolve) => {
        resolveLink = resolve;
      })
    );
    renderPanel();

    const button = await screen.findByText('Link Telegram');
    await userEvent.click(button);
    expect(button).toBeDisabled();

    resolveLink(LINK);
    expect(await screen.findByText('Open Telegram bot')).toBeInTheDocument();
  });
});
