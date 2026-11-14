import { useEffect } from 'react';
import BottomNav from './BottomNav';
import Header from './Header';
import ServerConfigBanner from './ServerConfigBanner';
import DeployBanner from './DeployBanner';

let shellsMounted = 0;

interface MobileShellProps {
  children: React.ReactNode;
}

const MobileShell = ({ children }: MobileShellProps) => {
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


  return (
    // Pinned to the WebView's own box rather than sized by 100vh/100dvh, which
    // go stale while Android's bars and keyboard change the window around it.
    <div className="fixed inset-0 flex flex-col overflow-hidden app-shell transition-all duration-300" data-testid="mobile-shell">
      <div className="safe-area-top app-bar backdrop-blur-md z-40 border-b border-white/5 transition-all duration-300">
        <ServerConfigBanner />
        <Header />
        <DeployBanner />
      </div>
      <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 pb-clear-bottom-bar scroll-smooth transition-all duration-300">
        {children}
      </main>
      {/* The app only mounts this shell for a phone or tablet (App.tsx: native,
          a narrow window, or a phone or tablet browser), and the header here
          has no menu, so the bottom bar is the only way between pages. It used
          to hide at 768px and wider, which left a tablet browser with no
          navigation at all. */}
      <div className="z-40 transition-all duration-300">
        <BottomNav />
      </div>
    </div>
  );
};

export default MobileShell;
