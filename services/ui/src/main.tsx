/* eslint-disable react-refresh/only-export-components */
import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { Capacitor } from '@capacitor/core';
import type { PluginListenerHandle } from '@capacitor/core';
import { App as CapacitorApp } from '@capacitor/app';
import './index.css';
import './themes/theme-remap.css';
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
    let disposed = false;
    const handles: PluginListenerHandle[] = [];

    const setupListeners = async () => {
      const stateHandle = await CapacitorApp.addListener('appStateChange', async ({ isActive }) => {
        if (!isActive) {
          bgTaskId = await startBackgroundTask();
        } else {
          if (bgTaskId) {
            await finishBackgroundTask();
            bgTaskId = null;
          }
        }
      });
      if (disposed) {
        void stateHandle.remove();
        return;
      }
      handles.push(stateHandle);

      const backHandle = await CapacitorApp.addListener('backButton', ({ canGoBack }) => {
        if (!canGoBack) {
          window.history.back();
        }
      });
      if (disposed) {
        void backHandle.remove();
        return;
      }
      handles.push(backHandle);
    };

    void setupListeners();

    return () => {
      disposed = true;
      for (const handle of handles) void handle.remove();
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

/* eslint-enable react-refresh/only-export-components */
const splash = document.getElementById('splash');
if (splash) {
  requestAnimationFrame(() => {
    splash.classList.add('hidden');
    setTimeout(() => splash.remove(), 300);
  });
}
