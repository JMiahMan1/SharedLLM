import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { api } from '../services/api';
import type { UserProfile } from '../services/api';
import toast from 'react-hot-toast';
import axios from 'axios';
import { storageGet, storageSet, storageRemove, storageInit } from '../lib/storage';

interface AuthContextType {
  user: UserProfile | null;
  token: string | null;
  role: 'admin' | 'user' | null;
  login: (credentials: { username: string; password: string }) => Promise<void>;
  logout: () => void;
  isLoading: boolean;
  initError: string | null;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [initError, setInitError] = useState<string | null>(null);

  useEffect(() => {
    const hardTimeout = setTimeout(() => {
      setInitError('Initialization timed out. Please restart the app.');
      setIsLoading(false);
    }, 3000);

    const initAuth = async () => {
      try {
        await storageInit();
        const storedToken = await storageGet('jarvis_api_key');
        if (storedToken) {
          try {
            const profile = await api.getMe();
            setToken(storedToken);
            setUser(profile);
            await storageSet('jarvis_user', JSON.stringify(profile));
          } catch {
            setInitError('Session expired. Please log in again.');
            setToken(null);
            storageRemove('jarvis_api_key');
            storageRemove('jarvis_user');
          }
        }
      } catch (e) {
        setInitError(`Auth init failed: ${e instanceof Error ? e.message : String(e)}`);
      } finally {
        clearTimeout(hardTimeout);
        setIsLoading(false);
      }
    };
    initAuth();
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    storageRemove('jarvis_api_key');
    storageRemove('jarvis_user');
    window.location.href = '/login';
  }, []);

  const refreshProfile = useCallback(async () => {
    if (!token) return;
    try {
      const profile = await api.getMe();
      setUser(profile);
      await storageSet('jarvis_user', JSON.stringify(profile));
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 401) {
        logout();
      }
    }
  }, [token, logout]);

  useEffect(() => {
    const loadProfile = async () => {
      if (token) {
        await refreshProfile();
      }
    };
    loadProfile();
  }, [token, refreshProfile]);

  const login = async (credentials: { username: string; password: string }) => {
    try {
      const data = await api.login(credentials.username, credentials.password);
      const authToken = data.api_key;
      setToken(authToken);
      await storageSet('jarvis_api_key', authToken);
      
      const profile = await api.getMe();
      console.log('Login profile response:', JSON.stringify(profile));
      setUser(profile);
      await storageSet('jarvis_user', JSON.stringify(profile));
      
      const displayName = profile.full_name || profile.username || credentials.username;
      toast.success(`Welcome back, ${displayName}!`);
    } catch (error: unknown) {
      console.error('Login error:', error);
      if (axios.isAxiosError(error)) {
        toast.error(error.response?.data?.detail || `Login failed: ${error.message}`);
      } else {
        toast.error('Login failed');
      }
      throw error;
    }
  };

  const role = user?.role || (user?.is_admin ? 'admin' : 'user');

  return (
    <AuthContext.Provider value={{ 
      user, 
      token, 
      role: role as 'admin' | 'user' | null, 
      login, 
      logout, 
      isLoading,
      initError,
      refreshProfile
    }}>
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
