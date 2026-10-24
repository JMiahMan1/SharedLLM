import { useState, useEffect } from 'react';
import { Capacitor } from '@capacitor/core';
import BottomNav from './BottomNav';
import Header from './Header';
import ServerConfigBanner from './ServerConfigBanner';

let shellsMounted = 0;

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

  // The document itself must never scroll under the phone shell: a drag would
  // slide the whole app under the status bar and the navigation buttons.
  // Only <main> scrolls; the lock lifts when the shell goes (the login page).
  useEffect(() => {
    // Counted: when a route swaps one shell for another, the old one's
    // cleanup can run after the new one mounted.
    shellsMounted += 1;
    document.documentElement.classList.add('mobile-shell-lock');
    return () => {
      shellsMounted -= 1;
      if (shellsMounted === 0) document.documentElement.classList.remove('mobile-shell-lock');
    };
  }, []);

  const showBottomNav = isNative || isMobileWidth;

  return (
    // Pinned to the WebView's own box rather than sized by 100vh/100dvh, which
    // go stale while Android's bars and keyboard change the window around it.
    <div className="fixed inset-0 flex flex-col overflow-hidden app-shell transition-all duration-300" data-testid="mobile-shell">
      <div className="safe-area-top app-bar backdrop-blur-md z-40 border-b border-white/5 transition-all duration-300">
        <ServerConfigBanner />
        <Header />
      </div>
      <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 pb-clear-bottom-bar scroll-smooth transition-all duration-300">
        {children}
      </main>
      {showBottomNav && (
        <div className="z-40 transition-all duration-300">
          <BottomNav />
        </div>
      )}
    </div>
  );
};

export default MobileShell;
