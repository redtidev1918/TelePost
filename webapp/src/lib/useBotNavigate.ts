import { useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

/** Bot selection is launch context, and must survive every route change. */
export function useBotNavigate() {
  const navigate = useNavigate();
  const { search } = useLocation();
  const bot = new URLSearchParams(search).get('bot') || 'bot1';
  return useCallback((pathname: string) => {
    navigate({ pathname, search: new URLSearchParams({ bot }).toString() });
  }, [navigate, bot]);
}
