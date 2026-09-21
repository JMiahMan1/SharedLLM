import { registerPlugin, type PluginListenerHandle } from "@capacitor/core";

export interface StepCounterAvailability {
  available: boolean;
  permissionRequired?: boolean;
  permissionGranted?: boolean;
}

export interface StepReading {
  available: boolean;
  steps?: number;
  cumulativeSinceBoot?: number;
}

export interface StepCounterPluginInterface {
  isAvailable(): Promise<StepCounterAvailability>;
  requestPermission(): Promise<{ granted: boolean }>;
  getTodaySteps(): Promise<StepReading>;
  startPolling(): Promise<void>;
  stopPolling(): Promise<void>;
  addListener(
    eventName: "stepUpdate",
    listenerFunc: (data: StepReading) => void
  ): Promise<PluginListenerHandle>;
}

// Fallback stub for web browsers (no hardware pedometer — report unavailable,
// never fabricate a reading).
const webFallback: StepCounterPluginInterface = {
  isAvailable: async () => ({ available: false, permissionGranted: false }),
  requestPermission: async () => ({ granted: false }),
  getTodaySteps: async () => ({ available: false }),
  startPolling: async () => undefined,
  stopPolling: async () => undefined,
  addListener: async () => ({ remove: async () => undefined }),
};

const StepCounter = registerPlugin<StepCounterPluginInterface>("StepCounter", {
  web: webFallback,
});

export default StepCounter;
