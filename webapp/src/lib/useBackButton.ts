/**
 * Telegram BackButton integration (§141): detail pages register a back action
 * so users are not stuck closing the whole Mini App from the top-right.
 * Falls back to window.history.back() when the bridge is unavailable.
 */
import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

export function useBackButton(to?: string) {
  const navigate = useNavigate();
  useEffect(() => {
    const back = window.Telegram?.WebApp?.BackButton;
    if (!back) {
      return;
    }
    const handler = () => {
      if (to) {
        navigate(to);
      } else if (window.history.length > 1) {
        window.history.back();
      } else {
        navigate('/');
      }
    };
    back.show();
    back.onClick(handler);
    return () => {
      back.offClick(handler);
      back.hide();
    };
  }, [to, navigate]);
}
