/**
 * Auth provider: boots the Telegram Mini App session once, exposes the
 * verified server-side user + roles, and re-bootstraps on 401 (§108).
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
  | 'loading' | 'authenticating' | 'authenticated' | 'outside_telegram'
  | 'miniapp_disabled' | 'invalid_init_data' | 'expired_init_data'
  | 'auth_failed' | 'server_unavailable';

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

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<SessionUser | null>(null);

  const bootstrap = useCallback(async () => {
    const initData = getLaunchInitData();
    if (!initData) {
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
      if (error instanceof ApiError && error.code === 'miniapp_disabled') setStatus('miniapp_disabled');
      else if (error instanceof ApiError && error.code === 'init_data_expired') setStatus('expired_init_data');
      else if (error instanceof ApiError && error.code.startsWith('invalid_init_data')) setStatus('invalid_init_data');
      else if (error instanceof ApiError && error.status < 500) setStatus('auth_failed');
      else setStatus('server_unavailable');
    }
  }, [queryClient]);

  useEffect(() => {
    // Boot when inside Telegram (initData present) or under dev/test where a
    // mock bridge provides one; otherwise show the "open in Telegram" hint.
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
    setStatus('auth_failed');
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
