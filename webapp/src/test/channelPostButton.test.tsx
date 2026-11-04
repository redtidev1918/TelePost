import { AppRoot } from '@telegram-apps/telegram-ui';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { openTelegramLink } from '@telegram-apps/sdk';
import { ChannelPostButton } from '../components/ChannelPostButton';

vi.mock('@telegram-apps/sdk', () => ({
  openTelegramLink: Object.assign(vi.fn(), { isAvailable: vi.fn() }),
}));

describe('channel navigation', () => {
  it('opens the confirmed channel in Telegram', () => {
    vi.mocked(openTelegramLink.isAvailable).mockReturnValue(true);
    render(<AppRoot platform="ios"><ChannelPostButton url="https://t.me/example_channel/81" testId="channel" /></AppRoot>);
    fireEvent.click(screen.getByTestId('channel'));
    expect(openTelegramLink).toHaveBeenCalledWith('https://t.me/example_channel/81');
  });
  it('uses a browser fallback without the Telegram bridge', () => {
    vi.mocked(openTelegramLink.isAvailable).mockReturnValue(false);
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    render(<AppRoot platform="ios"><ChannelPostButton url="https://t.me/c/123/81" testId="channel" /></AppRoot>);
    fireEvent.click(screen.getByTestId('channel'));
    expect(open).toHaveBeenCalledWith('https://t.me/c/123/81', '_blank', 'noopener');
  });
  it.each([undefined, '', 'https://example.com/source', 'javascript:alert(1)'])(
    'does not turn a missing/invalid channel URL into a source jump (%s)', (url) => {
      render(<AppRoot platform="ios"><ChannelPostButton url={url} testId="channel" /></AppRoot>);
      expect(screen.queryByTestId('channel')).toBeNull();
    },
  );
});
