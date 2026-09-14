/**
 * Auth provider: boots the Telegram Mini App session once, exposes the
 * verified server-side user + roles, and re-bootstraps on 401.
 *
 * Error taxonomy (§27): only the ABSENCE of Telegram launch data is
 * "outside_telegram". A server/auth/signature failure while inside Telegram
 * must show a precise error, never the misleading "open in Telegram" hint.
 */
import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  ApiError,
  bootstrapSession,
  clearSession,
  getLaunchInitData,
} from '../api/client';
import { fetchMe } from '../api/me';

export interface SessionUser {
  telegram_user_id: number;
  username: string;
  roles: string[];
}

export type AuthStatus =
  | 'loading'
  | 'outside_telegram'
  | 'authenticating'
  | 'authenticated'
  | 'miniapp_disabled'
  | 'auth_failed'
  | 'server_unavailable';

interface AuthContextValue {
  status: AuthStatus;
  user: SessionUser | null;
  bootstrap: () => Promise<void>;
  logout: () => void;
  isReviewer: boolean;
  isAdmin: boolean;
}

const AuthContext = createContext<AuthContextValue>({
  status: 'loading',
  user: null,
  bootstrap: async () => undefined,
  logout: () => undefined,
  isReviewer: false,
  isAdmin: false,
});

/** Session-boot errors the server can answer with. */
const INIT_DATA_ERROR_CODES = new Set([
  'missing_init_data',
  'invalid_init_data_signature',
  'invalid_init_data_format',
  'init_data_expired',
  'invalid_token',
]);

function classifyBootstrapError(error: unknown): AuthStatus {
  if (error instanceof ApiError) {
    if (error.code === 'miniapp_disabled') {
      return 'miniapp_disabled';
    }
    if (INIT_DATA_ERROR_CODES.has(error.code)) {
      return 'auth_failed';
    }
    if (error.status >= 500) {
      return 'server_unavailable';
    }
    return 'auth_failed';
  }
  // Network failure / JSON parse / unknown transport error.
  return 'server_unavailable';
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<SessionUser | null>(null);

  const bootstrap = useCallback(async () => {
    const initData = getLaunchInitData();
    if (!initData) {
      // No Telegram launch context at all: this is a plain browser.
      setStatus('outside_telegram');
      return;
    }
    setStatus('authenticating');
    try {
      await bootstrapSession(initData);
      const me = await queryClient.fetchQuery({
        queryKey: ['me'],
        queryFn: fetchMe,
        staleTime: 30_000,
      });
      setUser({
        telegram_user_id: me.telegram_user_id,
        username: (me as { username?: string }).username || '',
        roles: me.roles || [],
      });
      setStatus('authenticated');
    } catch (error) {
      clearSession();
      setUser(null);
      setStatus(classifyBootstrapError(error));
      // Re-bootstrap on the next explicit navigation / retry.
    }
  }, [queryClient]);

  useEffect(() => {
    // Boot when inside Telegram (SDK launch params or bridge initData present);
    // otherwise this is a plain browser and we show outside_telegram. Loading
    // state is kept until we positively know which case this is, so the UI
    // never flashes a wrong error before Home renders.
    if (getLaunchInitData()) {
      void bootstrap();
    } else {
      setStatus('outside_telegram');
    }
  }, [bootstrap]);

  const logout = useCallback(() => {
    clearSession();
    queryClient.clear();
    setUser(null);
    setStatus('outside_telegram');
  }, [queryClient]);

  const value = useMemo<AuthContextValue>(() => {
    if (status === 'authenticated' && user) {
      const roles = user.roles || [];
      return {
        status: 'authenticated',
        user,
        bootstrap,
        logout,
        isReviewer: roles.includes('reviewer') || roles.includes('admin'),
        isAdmin: roles.includes('admin'),
      };
    }
    return { status, user, bootstrap, logout, isReviewer: false, isAdmin: false };
  }, [status, user, bootstrap, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}