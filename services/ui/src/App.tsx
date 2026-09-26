import { useState, useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Capacitor } from '@capacitor/core';
import { AuthProvider, useAuth } from './context/AuthContext';
import { LocationProvider } from './context/LocationContext';
import Sidebar from './components/layout/Sidebar';
import Header from './components/layout/Header';
import MobileShell from './components/layout/MobileShell';
import AdminElevation from './components/auth/AdminElevation';
import Dashboard from './pages/Dashboard';
import Admin from './pages/Admin';
import Identity from './pages/Identity';
import Communication from './pages/Communication';
import Notes from './pages/Notes';
import Calendar from './pages/Calendar';
import JarvisLab, { JarvisLabAdmin } from './pages/JarvisLab';
import KnowledgeHub from './pages/KnowledgeHub';
import Workspaces from './pages/Workspaces';
import Docs from './pages/Docs';
import Login from './pages/Login';
import Media from './pages/Media';
import Remote from './pages/Remote';
import Settings from './pages/Settings';
import Wander from './pages/Wander';
import { initAppUpdater } from './lib/appUpdater';
import { useSiteTheme } from './themes/siteTheme';

import { Toaster } from 'react-hot-toast';

const queryClient = new QueryClient();

const ProtectedRoute = ({ children, requireAdmin = false, isMobile = false }: { children: React.ReactNode, requireAdmin?: boolean, isMobile?: boolean }) => {
  const { token, user, isLoading, initError } = useAuth();
  
  if (isLoading) return (
    <div className="h-screen w-screen flex items-center justify-center bg-slate-950">
      <div className="flex flex-col items-center gap-4">
        <div className="animate-pulse text-indigo-500 font-bold text-xl">Initializing Jarvis OS...</div>
        <div className="w-48 h-1 bg-slate-800 rounded-full overflow-hidden">
          <div className="h-full bg-indigo-500 animate-[loading_1.5s_ease-in-out_infinite]" style={{width: '30%'}}></div>
        </div>
      </div>
    </div>
  );
  
  if (initError || !token) return <Navigate to="/login" replace />;
  
  if (requireAdmin && !user?.is_admin) {
    console.warn("RBAC Violation: Admin required for this route.");
    return <Navigate to="/" replace />;
  }

  if (isMobile) {
    return <MobileShell>{children}</MobileShell>;
  }
  
  return (
    <div className="flex h-screen overflow-hidden bg-slate-950 text-slate-100">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden relative">
        <Header />
        <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 md:p-8 scroll-smooth">
          {children}
        </main>
      </div>
    </div>
  );
};

function App() {
  const [isMobile, setIsMobile] = useState(() => {
    if (Capacitor.isNativePlatform()) return true;
    if (typeof window !== 'undefined') {
      return window.innerWidth < 768 || /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
    }
    return false;
  });

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(
        Capacitor.isNativePlatform() ||
        window.innerWidth < 768 ||
        /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
      );
    };
    window.addEventListener('resize', handleResize);
    void initAppUpdater();
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useSiteTheme();

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <LocationProvider>
        <Toaster position="top-right" toastOptions={{
          style: {
            background: 'rgba(15, 23, 42, 0.9)',
            color: '#fff',
            backdropFilter: 'blur(8px)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
          }
        }} />
        <Router>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/" element={<ProtectedRoute isMobile={isMobile}><Dashboard /></ProtectedRoute>} />
            
            {/* Admin-only routes */}
            <Route path="/admin/*" element={<ProtectedRoute requireAdmin={true} isMobile={isMobile}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />

            {/* Service routes */}
            <Route path="/identity" element={<ProtectedRoute isMobile={isMobile}><Identity /></ProtectedRoute>} />
            <Route path="/communication" element={<ProtectedRoute isMobile={isMobile}><Communication /></ProtectedRoute>} />
            <Route path="/notes" element={<ProtectedRoute isMobile={isMobile}><Notes /></ProtectedRoute>} />
            <Route path="/calendar" element={<ProtectedRoute isMobile={isMobile}><Calendar /></ProtectedRoute>} />
            <Route path="/media" element={<ProtectedRoute isMobile={isMobile}><Media /></ProtectedRoute>} />
            <Route path="/wander" element={<ProtectedRoute isMobile={isMobile}><Wander /></ProtectedRoute>} />
            <Route path="/family" element={<ProtectedRoute isMobile={isMobile}><Wander /></ProtectedRoute>} />
            <Route path="/family-circle" element={<ProtectedRoute isMobile={isMobile}><Wander /></ProtectedRoute>} />
            <Route path="/remote" element={<ProtectedRoute isMobile={isMobile}><Remote /></ProtectedRoute>} />
            <Route path="/settings" element={<ProtectedRoute isMobile={isMobile}><Settings /></ProtectedRoute>} />
            <Route path="/knowledge" element={<ProtectedRoute isMobile={isMobile}><KnowledgeHub /></ProtectedRoute>} />
            <Route path="/workspaces" element={<ProtectedRoute isMobile={isMobile}><Workspaces /></ProtectedRoute>} />
            <Route path="/docs" element={<ProtectedRoute isMobile={isMobile}><Docs /></ProtectedRoute>} />

            {/* Jarvis Lab — user view (admin-only access) and admin view */}
            <Route path="/lab" element={<ProtectedRoute requireAdmin={true} isMobile={isMobile}><AdminElevation><JarvisLab /></AdminElevation></ProtectedRoute>} />
            <Route path="/lab/admin" element={<ProtectedRoute requireAdmin={true} isMobile={isMobile}><AdminElevation><JarvisLabAdmin /></AdminElevation></ProtectedRoute>} />
          </Routes>
        </Router>
      </LocationProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
