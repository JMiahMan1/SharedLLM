import { Capacitor } from '@capacitor/core';
import BottomNav from './BottomNav';
import Header from './Header';
import ServerConfigBanner from './ServerConfigBanner';

interface MobileShellProps {
  children: React.ReactNode;
}

const MobileShell = ({ children }: MobileShellProps) => {
  const isNative = Capacitor.isNativePlatform();
  const isMobileWidth = typeof window !== 'undefined' && window.innerWidth < 768;
  const showBottomNav = isNative || isMobileWidth;

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-slate-950 text-slate-100">
      <ServerConfigBanner />
      <Header />
      <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 scroll-smooth" style={{ paddingBottom: showBottomNav ? 'calc(5rem + env(safe-area-inset-bottom, 0px))' : '1rem' }}>
        {children}
      </main>
      {showBottomNav && <BottomNav />}
    </div>
  );
};

export default MobileShell;
