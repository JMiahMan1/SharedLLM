import React, { createContext, useContext, useState, useEffect } from 'react';
import { api } from '../services/api';

interface AuthContextType {
  user: any | null;
  apiKey: string | null;
  login: (credentials: { username: string; password: string }) => Promise<void>;
  logout: () => void;
  isLoading: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [user, setUser] = useState<any | null>(null);
  const [apiKey, setApiKey] = useState<string | null>(localStorage.getItem('jarvis_api_key'));
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const storedUser = localStorage.getItem('jarvis_user');
    if (storedUser && apiKey) {
      setUser(JSON.parse(storedUser));
    }
    setIsLoading(false);
  }, [apiKey]);

  const login = async (credentials: { username: string; password: string }) => {
    const data = await api.login(credentials.username, credentials.password);
    setApiKey(data.api_key);
    setUser({ username: data.username, is_admin: data.is_admin });
    localStorage.setItem('jarvis_api_key', data.api_key);
    localStorage.setItem('jarvis_user', JSON.stringify({ username: data.username, is_admin: data.is_admin }));
  };

  const logout = () => {
    setApiKey(null);
    setUser(null);
    localStorage.removeItem('jarvis_api_key');
    localStorage.removeItem('jarvis_user');
  };

  return (
    <AuthContext.Provider value={{ user, apiKey, login, logout, isLoading }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
