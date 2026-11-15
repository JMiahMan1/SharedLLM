import React, { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../services/api';
import type { UserProfile } from '../services/api';
import toast from 'react-hot-toast';
import axios from 'axios';
import { storageGet, storageSet, storageRemove, storageInit, mirrorNativeCredentials } from '../lib/storage';
import { registerThisDevice } from '../lib/deviceRegistration';
import { sharePushKey } from '../lib/push';
import { mirrorIdentity } from '../lib/locationTracking';
import { lockAll } from '../lib/privateChatSession';

interface AuthContextType {
  user: UserProfile | null;
  token: string | null;
  role: 'admin' | 'user' | null;
  /** Whether the signed-in user's roles (or admin) allow this, e.g. "devices.assign". */
  can: (permission: string) => boolean;
  login: (credentials: { username: string; password: string }) => Promise<void>;
  logout: () => Promise<void>;
  isLoading: boolean;
  initError: string | null;
  refreshProfile: () => Promise<void>;
}

/** Reading the phone's own storage at start; never the server. */
const STORAGE_TIMEOUT_MS = 3000;
/** The server is retried this often at start while it cannot be reached (capped). */
const RETRY_MS = [1000, 2000, 4000, 8000, 15000, 30000];

function retryDelay(attempt: number): number {
  return RETRY_MS[Math.min(attempt, RETRY_MS.length - 1)];
}

/** The server said no to the stored key: the only reason to sign someone out at start. */
function isSignInRefused(error: unknown): boolean {
  const status = axios.isAxiosError(error) ? error.response?.status : undefined;
  return status === 401 || status === 403;
}

function parseStoredUser(raw: string | null): UserProfile | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as UserProfile;
    return parsed && typeof parsed === 'object' && parsed.username ? parsed : null;
  } catch {
    return null; // an old build stored the bare username: confirm with the server instead
  }
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<UserProfile | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [initError, setInitError] = useState<string | null>(null);

  // Follow the ['me'] cache. The avatar picker and the Identity page write a
  // changed profile there; this copy was set only at sign-in, so everything
  // reading useAuth().user (the Settings page among them) kept the old
  // picture until the app was restarted. Only the same person's profile is
  // taken, so a late answer can never swap who is signed in.
  useEffect(() => queryClient.getQueryCache().subscribe((event) => {
    if (event.type !== 'updated' || event.query.queryKey.length !== 1 || event.query.queryKey[0] !== 'me') return;
    const next = event.query.state.data as UserProfile | undefined;
    if (!next?.username) return;
    setUser((prev) => (prev && prev.username === next.username && prev !== next ? next : prev));
  }), [queryClient]);

  useEffect(() => {
    // Only reading the phone's own storage is timed: a server that is slow to
    // answer (the network waking up after an app update) must never send a
    // signed-in person to the login screen.
    const storageTimeout = setTimeout(() => {
      setInitError('Initialization timed out. Please restart the app.');
      setIsLoading(false);
    }, STORAGE_TIMEOUT_MS);
    let cancelled = false;

    const signedIn = async (storedToken: string, profile: UserProfile) => {
      setToken(storedToken);
      setUser(profile);
      setInitError(null);
      queryClient.setQueryData(['me'], profile);
      await storageSet('jarvis_user', JSON.stringify(profile));
      // Register here too, not only on an explicit login: a user who installed
      // an update and kept using the app was otherwise never registered.
      // Fire-and-forget: it must never block the session.
      void registerThisDevice();
      // The background location service reads the key, server and username
      // from TokenBridge, which only an explicit login used to fill.
      void mirrorNativeCredentials();
      if (profile.username) void mirrorIdentity(profile.username);
    };

    const initAuth = async () => {
      let storedToken: string | null;
      try {
        await storageInit();
        storedToken = await storageGet('jarvis_api_key');
      } catch (e) {
        clearTimeout(storageTimeout);
        setInitError(`Auth init failed: ${e instanceof Error ? e.message : String(e)}`);
        setIsLoading(false);
        return;
      }
      clearTimeout(storageTimeout);
      if (!storedToken) {
        setIsLoading(false);
        return;
      }
      // Start at once as the person last signed in here: the app opens on its
      // page instead of a login form (whose password field also made Android's
      // password manager ask for the phone's credentials after every update).
      const cached = parseStoredUser(await storageGet('jarvis_user').catch(() => null));
      if (cached) {
        setToken(storedToken);
        setUser(cached);
        queryClient.setQueryData(['me'], cached);
        setIsLoading(false);
      }
      // Confirm with the server, retrying quietly while it cannot be reached.
      // Only the server refusing the key (401/403) signs out.
      for (let attempt = 0; !cancelled; attempt += 1) {
        try {
          const profile = await api.getMe();
          if (!cancelled) await signedIn(storedToken, profile);
          break;
        } catch (error) {
          if (isSignInRefused(error)) {
            setInitError('Session expired. Please log in again.');
            setToken(null);
            setUser(null);
            storageRemove('jarvis_api_key');
            storageRemove('jarvis_user');
            break;
          }
          await new Promise((r) => setTimeout(r, retryDelay(attempt)));
        }
      }
      setIsLoading(false);
    };
    void initAuth();
    return () => {
      cancelled = true;
      clearTimeout(storageTimeout);
    };
  }, [queryClient]);

  const logout = useCallback(async () => {
    // Every unlocked private chat locks with the person leaving.
    lockAll();
    setToken(null);
    setUser(null);
    setInitError(null);
    queryClient.removeQueries({ queryKey: ['me'] });
    // The native mirror is what the background services read, so the clear has
    // to finish before the page goes away. Redirecting first left the previous
    // person's key in place, and their location and step services kept running
    // with it.
    await Promise.all([
      storageRemove('jarvis_api_key'),
      storageRemove('jarvis_user'),
      // These belong to the person, not the phone: on a shared phone the next
      // person used to inherit "the guard was on" and someone else's sensor
      // switches. The native services' own switches are cleared with the key.
      storageRemove('jarvis_guard_seen_on'),
      storageRemove('jarvis_location_tracking_enabled'),
      storageRemove('jarvis_steps_backfilled_through'),
      // The browser's notice buttons acted with this key (public/push-sw.js); the next person must not.
      sharePushKey(null).catch((e: unknown) => { console.warn('[push] could not clear the push worker key', e); return false; }),
    ]);
    window.location.href = '/login';
  }, [queryClient]);

  const tokenRef = useRef(token);
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  const refreshProfile = useCallback(async () => {
    const currentToken = tokenRef.current;
    if (!currentToken) return;
    try {
      const profile = await api.getMe();
      setUser(profile);
      queryClient.setQueryData(['me'], profile);
      await storageSet('jarvis_user', JSON.stringify(profile));
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 401) {
        void logout();
      }
    }
  }, [logout, queryClient]);

  useEffect(() => {
    const loadProfile = async () => {
      if (token) {
        await refreshProfile();
      }
    };
    loadProfile();
  }, [token, refreshProfile]);

  // A role change on the server used to reach this app only on the next full
  // load, so a demoted member kept being offered actions the server refused.
  // Re-reading the profile when the app comes back into view (and every few
  // minutes while it stays open) keeps the menu honest.
  useEffect(() => {
    if (!token) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refreshProfile();
    };
    document.addEventListener('visibilitychange', onVisible);
    const timer = window.setInterval(() => void refreshProfile(), 5 * 60_000);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.clearInterval(timer);
    };
  }, [token, refreshProfile]);

  const login = useCallback(async (credentials: { username: string; password: string }) => {
    try {
      const data = await api.login(credentials.username, credentials.password);
      const authToken = data.api_key;
      setToken(authToken);
      setInitError(null);
      await storageSet('jarvis_api_key', authToken);
      
      const profile = await api.getMe();
      console.log('Login profile response:', JSON.stringify(profile));
      setUser(profile);
      queryClient.setQueryData(['me'], profile);
      await storageSet('jarvis_user', JSON.stringify(profile));
      
      const displayName = profile.full_name || profile.username || credentials.username;
      toast.success(`Welcome back, ${displayName}!`);

      // Tell the server what this device is, so the panel can answer "which
      // build is this?" without guessing. Fire-and-forget: registration must
      // never delay or fail the login the user just completed.
      void registerThisDevice();

      // Mirror the identity natively. The background location service can
      // outlive the WebView, and without this it could authenticate but not
      // attribute the position to anyone. Same fire-and-forget rule. Uses the
      // profile's username, which is the canonical one the server resolved.
      const canonical = profile.username || credentials.username;
      if (canonical) void mirrorIdentity(canonical);
    } catch (error: unknown) {
      // The login page renders the server's own message inline, so a toast here
      // reported the same failure twice.
      console.error('Login error:', error);
      throw error;
    }
  }, [queryClient]);

  const role = useMemo(
    () => (user ? user.role || (user.is_admin ? 'admin' : 'user') : null),
    [user],
  );
  const can = useCallback(
    (permission: string) => Boolean(user?.is_admin || user?.permissions?.includes(permission)),
    [user],
  );

  const contextValue = useMemo(() => ({
    user,
    token,
    role: role as 'admin' | 'user' | null,
    can,
    login,
    logout,
    isLoading,
    initError,
    refreshProfile
  }), [user, token, role, can, login, logout, isLoading, initError, refreshProfile]);

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
