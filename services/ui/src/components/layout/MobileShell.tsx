import { Capacitor } from '@capacitor/core';
import BottomNav from './BottomNav';
import Header from './Header';

interface MobileShellProps {
  children: React.ReactNode;
}

const MobileShell = ({ children }: MobileShellProps) => {
  const isNative = Capacitor.isNativePlatform();
  const isMobileWidth = typeof window !== 'undefined' && window.innerWidth < 768;
  const showBottomNav = isNative || isMobileWidth;

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-slate-950 text-slate-100">
      <Header />
      <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 pb-20 scroll-smooth">
        {children}
      </main>
      {showBottomNav && <BottomNav />}
    </div>
  );
};

export default MobileShell;
