import { useState, useEffect } from 'react';
import { Capacitor } from '@capacitor/core';
import BottomNav from './BottomNav';
import Header from './Header';
import ServerConfigBanner from './ServerConfigBanner';

interface MobileShellProps {
  children: React.ReactNode;
}

const MobileShell = ({ children }: MobileShellProps) => {
  const isNative = Capacitor.isNativePlatform();
  const [isMobileWidth, setIsMobileWidth] = useState(false);

  useEffect(() => {
    const checkMobile = () => setIsMobileWidth(window.innerWidth < 768);
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  const showBottomNav = isNative || isMobileWidth;

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-slate-950 text-slate-100 transition-all duration-300">
      <div className="safe-area-top bg-slate-950/40 backdrop-blur-md z-40 border-b border-white/5 transition-all duration-300">
        <ServerConfigBanner />
        <Header />
      </div>
      <main 
        className="flex-1 overflow-y-auto overflow-x-hidden p-4 scroll-smooth transition-all duration-300" 
        style={{ paddingBottom: showBottomNav ? 'calc(5.5rem + env(safe-area-inset-bottom, 0px))' : '1rem' }}
      >
        {children}
      </main>
      {showBottomNav && (
        <div className="safe-area-bottom z-40 transition-all duration-300">
          <BottomNav />
        </div>
      )}
    </div>
  );
};

export default MobileShell;
