import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { api, apiClient } from '../services/api';
import type { UserProfile } from '../services/api';
import toast from 'react-hot-toast';
import axios from 'axios';

interface AuthContextType {
  user: UserProfile | null;
  token: string | null;
  role: 'admin' | 'user' | null;
  login: (credentials: { username: string; password: string }) => Promise<void>;
  logout: () => void;
  isLoading: boolean;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [token, setToken] = useState<string | null>(localStorage.getItem('jarvis_api_key'));
  const [isLoading, setIsLoading] = useState(true);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    localStorage.removeItem('jarvis_api_key');
    localStorage.removeItem('jarvis_user');
    window.location.href = '/login';
  }, []);

  const refreshProfile = useCallback(async () => {
    if (!token) return;
    try {
      const profile = await api.getMe();
      setUser(profile);
      localStorage.setItem('jarvis_user', JSON.stringify(profile));
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 401) {
        logout();
      }
    }
  }, [token, logout]);

  useEffect(() => {
    const initAuth = async () => {
      if (token) {
        await refreshProfile();
      }
      setIsLoading(false);
    };
    initAuth();
  }, [token, refreshProfile]);

  // Centralized 401 handling is now in services/api.ts

  const login = async (credentials: { username: string; password: string }) => {
    try {
      const data = await api.login(credentials.username, credentials.password);
      const authToken = data.api_key;
      setToken(authToken);
      localStorage.setItem('jarvis_api_key', authToken);
      
      const profile = await api.getMe();
      setUser(profile);
      localStorage.setItem('jarvis_user', JSON.stringify(profile));
      
      toast.success(`Welcome back, ${profile.full_name || profile.username}!`);
    } catch (error: unknown) {
      if (axios.isAxiosError(error)) {
        toast.error(error.response?.data?.detail || 'Login failed');
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
