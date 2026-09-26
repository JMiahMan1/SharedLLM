import { registerPlugin, type PluginListenerHandle } from "@capacitor/core";

export interface StepCounterAvailability {
  available: boolean;
  permissionRequired?: boolean;
  permissionGranted?: boolean;
}

export interface StepPermissionResult {
  granted: boolean;
  /** Android "Don't ask again" / previously denied forever — open system settings. */
  permanentlyDenied?: boolean;
}

export interface StepReading {
  available: boolean;
  steps?: number;
  cumulativeSinceBoot?: number;
}

/** One row of the on-device ledger (the source of truth for past days). */
export interface StepLedgerDay {
  day: string;
  steps: number;
  source: string;
  updatedAt?: number;
}

export interface StepLedgerHistory {
  days: StepLedgerDay[];
  source: string;
}

export interface StepCounterPluginInterface {
  isAvailable(): Promise<StepCounterAvailability>;
  requestPermission(): Promise<StepPermissionResult>;
  getTodaySteps(): Promise<StepReading>;
  /** Durable history: survives reboots, app kills and days the app never ran. */
  getDayHistory(options?: { days?: number }): Promise<StepLedgerHistory>;
  /** Days recorded after `since` (exclusive), oldest first — used for backfill. */
  getDaysSince(options: { since?: string; max?: number }): Promise<StepLedgerHistory>;
  startPolling(): Promise<void>;
  stopPolling(): Promise<void>;
  /** Open this app's system settings page (for permanently denied permissions). */
  openSettings(): Promise<void>;
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
  getDayHistory: async () => ({ days: [], source: "phone" }),
  getDaysSince: async () => ({ days: [], source: "phone" }),
  startPolling: async () => undefined,
  stopPolling: async () => undefined,
  openSettings: async () => undefined,
  addListener: async () => ({ remove: async () => undefined }),
};

const StepCounter = registerPlugin<StepCounterPluginInterface>("StepCounter", {
  web: webFallback,
});

export default StepCounter;
