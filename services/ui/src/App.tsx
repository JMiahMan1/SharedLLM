import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Capacitor } from '@capacitor/core';
import { AuthProvider, useAuth } from './context/AuthContext';
import Sidebar from './components/layout/Sidebar';
import Header from './components/layout/Header';
import MobileShell from './components/layout/MobileShell';
import Dashboard from './pages/Dashboard';
import Admin from './pages/Admin';
import Identity from './pages/Identity';
import Communication from './pages/Communication';
import JarvisLab from './pages/JarvisLab';
import KnowledgeHub from './pages/KnowledgeHub';
import Workspaces from './pages/Workspaces';
import Docs from './pages/Docs';
import Login from './pages/Login';

import { Toaster } from 'react-hot-toast';

const queryClient = new QueryClient();

const ProtectedRoute = ({ children, requireAdmin = false, isMobile = false }: { children: React.ReactNode, requireAdmin?: boolean, isMobile?: boolean }) => {
  const { token, user, isLoading } = useAuth();
  
  if (isLoading) return (
    <div className="h-screen w-screen flex items-center justify-center bg-slate-950">
      <div className="animate-pulse text-indigo-500 font-bold text-xl">Initializing Jarvis OS...</div>
    </div>
  );
  
  if (!token) return <Navigate to="/login" replace />;
  
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
  const isNative = Capacitor.isNativePlatform();

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
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
            <Route path="/" element={<ProtectedRoute isMobile={isNative}><Dashboard /></ProtectedRoute>} />
            <Route path="/admin" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><Admin /></ProtectedRoute>} />
            <Route path="/identity" element={<ProtectedRoute isMobile={isNative}><Identity /></ProtectedRoute>} />
            <Route path="/communication" element={<ProtectedRoute isMobile={isNative}><Communication /></ProtectedRoute>} />
            <Route path="/lab" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><JarvisLab /></ProtectedRoute>} />
            <Route path="/knowledge" element={<ProtectedRoute isMobile={isNative}><KnowledgeHub /></ProtectedRoute>} />
            <Route path="/workspaces" element={<ProtectedRoute isMobile={isNative}><Workspaces /></ProtectedRoute>} />
            <Route path="/docs" element={<ProtectedRoute isMobile={isNative}><Docs /></ProtectedRoute>} />
          </Routes>
        </Router>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
