import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { Capacitor } from '@capacitor/core';
import { App as CapacitorApp } from '@capacitor/app';
import './index.css';
import App from './App.tsx';
import ErrorBoundary from './components/ErrorBoundary.tsx';
import { startBackgroundTask, finishBackgroundTask } from './lib/backgroundTask';

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Failed to find the root element in index.html');
}

function BackgroundTaskManager() {
  useEffect(() => {
    if (!Capacitor.isNativePlatform()) return;

    let bgTaskId: string | null = null;

    const setupListeners = async () => {
      CapacitorApp.addListener('appStateChange', async ({ isActive }) => {
        if (!isActive) {
          bgTaskId = await startBackgroundTask();
        } else {
          if (bgTaskId) {
            await finishBackgroundTask();
            bgTaskId = null;
          }
        }
      });

      CapacitorApp.addListener('backButton', ({ canGoBack }) => {
        if (!canGoBack) {
          window.history.back();
        }
      });
    };

    setupListeners();

    return () => {
      CapacitorApp.removeAllListeners();
    };
  }, []);

  return null;
}

createRoot(rootElement).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
      <BackgroundTaskManager />
    </ErrorBoundary>
  </StrictMode>,
);

const splash = document.getElementById('splash');
if (splash) {
  requestAnimationFrame(() => {
    splash.classList.add('hidden');
    setTimeout(() => splash.remove(), 300);
  });
}
