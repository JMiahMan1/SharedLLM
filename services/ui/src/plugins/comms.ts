import { registerPlugin } from '@capacitor/core';

/* Calls and SMS from the device's own call log and message store. Runtime
   permissions (READ_CALL_LOG, READ_SMS) are required; contacts are resolved
   best-effort for display names. */

export interface CommsAccess {
  calls: boolean;
  sms: boolean;
}

export interface CommsCall {
  id: number;
  number: string;
  name: string;
  direction: string;
  at: number;
  duration: number;
}

export interface CommsSms {
  id: number;
  number: string;
  name: string;
  body: string;
  direction: string;
  at: number;
}

export interface CommsNewCalls {
  calls: CommsCall[];
}

export interface CommsNewSms {
  messages: CommsSms[];
}

/** What one native pass sent (`syncNow`, APK with CommsCollector). */
export interface CommsSyncResult {
  ok: boolean;
  calls: number;
  sms: number;
}

export interface CommsPluginInterface {
  hasAccess(): Promise<CommsAccess>;
  requestAccess(): Promise<CommsAccess>;
  /** Send new calls and texts natively (the background worker's own pass). */
  syncNow(): Promise<CommsSyncResult>;
  /** Older APKs only: read new rows for the WebView to post itself. */
  getNewCalls(): Promise<CommsNewCalls>;
  getNewSms(): Promise<CommsNewSms>;
}

const webFallback: CommsPluginInterface = {
  hasAccess: async () => ({ calls: false, sms: false }),
  requestAccess: async () => ({ calls: false, sms: false }),
  syncNow: async () => ({ ok: true, calls: 0, sms: 0 }),
  getNewCalls: async () => ({ calls: [] }),
  getNewSms: async () => ({ messages: [] }),
};

const Comms = registerPlugin<CommsPluginInterface>('Comms', {
  web: () => webFallback,
});

export default Comms;
