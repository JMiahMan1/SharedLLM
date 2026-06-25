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
import JarvisLab from './pages/JarvisLab';
import KnowledgeHub from './pages/KnowledgeHub';
import Workspaces from './pages/Workspaces';
import Docs from './pages/Docs';
import Login from './pages/Login';
import Media from './pages/Media';
import Remote from './pages/Remote';
import Settings from './pages/Settings';

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
  const isNative = Capacitor.isNativePlatform();

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
            <Route path="/" element={<ProtectedRoute isMobile={isNative}><Dashboard /></ProtectedRoute>} />
            <Route path="/admin" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />
            <Route path="/admin/ops" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />
            <Route path="/admin/users" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />
            <Route path="/admin/groups" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />
            <Route path="/admin/monitor" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />
            <Route path="/admin/intercom" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />
            <Route path="/admin/integrations" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />
            <Route path="/admin/database" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />
            <Route path="/admin/sounds" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />
            <Route path="/admin/services" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><Admin /></AdminElevation></ProtectedRoute>} />
            <Route path="/identity" element={<ProtectedRoute isMobile={isNative}><Identity /></ProtectedRoute>} />
            <Route path="/communication" element={<ProtectedRoute isMobile={isNative}><Communication /></ProtectedRoute>} />
            <Route path="/chat" element={<ProtectedRoute isMobile={isNative}><Communication /></ProtectedRoute>} />
            <Route path="/intercom" element={<ProtectedRoute isMobile={isNative}><Communication /></ProtectedRoute>} />
            <Route path="/media" element={<ProtectedRoute isMobile={isNative}><Media /></ProtectedRoute>} />
            <Route path="/remote" element={<ProtectedRoute isMobile={isNative}><Remote /></ProtectedRoute>} />
            <Route path="/settings" element={<ProtectedRoute isMobile={isNative}><Settings /></ProtectedRoute>} />
            <Route path="/settings/integrations" element={<ProtectedRoute isMobile={isNative}><Settings /></ProtectedRoute>} />
            <Route path="/lab" element={<ProtectedRoute requireAdmin={true} isMobile={isNative}><AdminElevation><JarvisLab /></AdminElevation></ProtectedRoute>} />
            <Route path="/knowledge" element={<ProtectedRoute isMobile={isNative}><KnowledgeHub /></ProtectedRoute>} />
            <Route path="/workspaces" element={<ProtectedRoute isMobile={isNative}><Workspaces /></ProtectedRoute>} />
            <Route path="/docs" element={<ProtectedRoute isMobile={isNative}><Docs /></ProtectedRoute>} />
          </Routes>
        </Router>
      </LocationProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

export default App;
