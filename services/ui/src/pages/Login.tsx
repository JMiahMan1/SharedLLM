import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Shield, Lock, User, AlertCircle, Fingerprint, Server, WifiOff, Wifi } from 'lucide-react';
import { Capacitor } from '@capacitor/core';
import { useAuth } from '../context/AuthContext';
import { useBiometricAuth } from '../hooks/useBiometricAuth';
import { useHaptics } from '../hooks/useHaptics';
import { useNavigate } from 'react-router-dom';
import { storageGet, storageSet } from '../lib/storage';
import { checkConnectivity } from '../lib/connectivity';

const Login = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [serverUrl, setServerUrl] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [savedUsername, setSavedUsername] = useState<string | null>(null);
  const [connectivity, setConnectivity] = useState<{ ok: boolean; latency?: number; error?: string } | null>(null);
  const { login } = useAuth();
  const { isNative, isAvailable, checkAvailability, authenticate } = useBiometricAuth();
  const { trigger } = useHaptics();
  const navigate = useNavigate();
  const showServerField = Capacitor.isNativePlatform();

  useEffect(() => {
    const loadSaved = async () => {
      const saved = await storageGet('jarvis_last_username');
      if (saved) {
        setSavedUsername(saved);
        setUsername(saved);
      }
      if (Capacitor.isNativePlatform()) {
        const savedServer = await storageGet('jarvis_server_url');
        const url = savedServer || 'https://jarvis.sumemail.com';
        setServerUrl(url);
        const result = await checkConnectivity(url);
        setConnectivity(result);
        await checkAvailability();
      }
    };
    loadSaved();
  }, [checkAvailability]);

  const handleTestConnection = async () => {
    if (!serverUrl) return;
    setConnectivity({ ok: false, error: 'Testing...' });
    const result = await checkConnectivity(serverUrl);
    setConnectivity(result);
    trigger('light');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);
    try {
      if (showServerField && serverUrl) {
        await storageSet('jarvis_server_url', serverUrl.replace(/\/+$/, ''));
      }
      await login({ username, password });
      await storageSet('jarvis_last_username', username);
      navigate('/');
    } catch (err: unknown) {
      if (err instanceof Error) {
        setError(err.message || 'Authentication failed');
      } else {
        setError('Authentication failed');
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleBiometricLogin = async () => {
    trigger('medium');
    setError('');
    setIsLoading(true);

    const result = await authenticate('Sign in to Jarvis OS');

    if (result.success) {
      const savedPassword = await storageGet('jarvis_saved_password');
      if (savedPassword && savedUsername) {
        try {
          await login({ username: savedUsername, password: savedPassword });
          navigate('/');
        } catch {
          setError('Biometric login failed. Please enter your password.');
        }
      } else {
        setError('No saved credentials found. Please sign in manually.');
      }
    } else if (result.error) {
      setError(result.error);
    }

    setIsLoading(false);
  };

  const showBiometricOption = isNative && isAvailable && savedUsername;

  return (
    <div className="min-h-screen flex items-center justify-center p-6 bg-slate-950">
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-purple-600/20 rounded-full blur-[128px]" />
        <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-blue-600/20 rounded-full blur-[128px]" />
      </div>

      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-panel w-full max-w-md p-8 sm:p-10 relative z-10"
      >
        <div className="flex flex-col items-center mb-8">
          <div className="p-4 rounded-2xl bg-purple-600/20 text-purple-400 mb-4 border border-purple-500/20">
            <Shield size={32} />
          </div>
          <h1 className="text-3xl font-bold text-white tracking-tight">Jarvis OS</h1>
          <p className="text-slate-400 mt-2">Family AI Home Operating System</p>
        </div>

        {showBiometricOption && (
          <div className="flex flex-col items-center mb-6">
            <button
              onClick={handleBiometricLogin}
              disabled={isLoading}
              className="w-16 h-16 rounded-full bg-purple-500/20 border border-purple-500/30 flex items-center justify-center hover:bg-purple-500/30 transition-colors disabled:opacity-50"
            >
              <Fingerprint size={36} className="text-purple-400" />
            </button>
            <p className="text-sm text-slate-400 mt-2">
              Sign in as <span className="text-white font-medium">{savedUsername}</span>
            </p>
            <p className="text-xs text-slate-500 mt-1">Tap to authenticate</p>
            <button
              onClick={() => { setSavedUsername(null); setUsername(''); }}
              className="text-xs text-purple-400 hover:text-purple-300 mt-3"
            >
              Use different account
            </button>
          </div>
        )}

        {(!showBiometricOption || savedUsername === null) && (
          <form onSubmit={handleSubmit} className="space-y-6">
            {showServerField && (
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">Server URL</label>
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <Server className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={18} />
                    <input 
                      type="url"
                      value={serverUrl}
                      onChange={(e) => setServerUrl(e.target.value)}
                      className="glass-input w-full pl-10"
                      placeholder="https://jarvis.sumemail.com"
                      required
                    />
                  </div>
                  <button
                    type="button"
                    onClick={handleTestConnection}
                    className="glass-button px-3 min-w-[48px]"
                    title="Test connection"
                  >
                    {connectivity?.ok ? (
                      <Wifi size={18} className="text-green-400" />
                    ) : connectivity?.error === 'Testing...' ? (
                      <div className="w-4 h-4 border-2 border-slate-400 border-t-transparent rounded-full animate-spin" />
                    ) : (
                      <WifiOff size={18} className="text-red-400" />
                    )}
                  </button>
                </div>
                {connectivity && connectivity.error && connectivity.error !== 'Testing...' && (
                  <p className="text-xs text-red-400 mt-1">{connectivity.error}</p>
                )}
                {connectivity?.ok && (
                  <p className="text-xs text-green-400 mt-1">Connected ({connectivity.latency}ms)</p>
                )}
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Username</label>
              <div className="relative">
                <User className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={18} />
                <input 
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="glass-input w-full pl-10"
                  placeholder="Enter username"
                  required
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Password</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={18} />
                <input 
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="glass-input w-full pl-10"
                  placeholder="Enter password"
                  required
                />
              </div>
            </div>

            {error && (
              <motion.div 
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm flex items-center gap-2"
              >
                <AlertCircle size={16} /> {error}
              </motion.div>
            )}

            <button 
              type="submit"
              disabled={isLoading}
              className="glass-button w-full py-3 bg-purple-600 hover:bg-purple-500 text-white font-semibold shadow-lg shadow-purple-900/20 disabled:opacity-50"
            >
              {isLoading ? 'Authenticating...' : 'Sign In'}
            </button>
          </form>
        )}

        <div className="mt-8 text-center">
          <p className="text-xs text-slate-500 uppercase tracking-widest font-semibold">
            Identity Service v1.0
          </p>
        </div>
      </motion.div>
    </div>
  );
};

export default Login;
