import { Button } from '@telegram-apps/telegram-ui';
import { openTelegramLink } from '@telegram-apps/sdk';
import { tr } from '../lib/i18n';

/** Channel navigation is independent of the optional original-work link. */
export function ChannelPostButton({ url, testId }: { url?: string; testId: string }) {
  if (!url || !/^https:\/\/t\.me\//i.test(url)) return null;
  return (
    <Button mode="filled" stretched data-testid={testId} onClick={() => {
      if (openTelegramLink.isAvailable()) openTelegramLink(url);
      else window.open(url, '_blank', 'noopener');
    }}>
      {tr("在频道中查看")}
    </Button>
  );
}
