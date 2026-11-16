import { Capacitor, registerPlugin } from '@capacitor/core';
import type { Reminder } from '../lib/beloved/reminders';

/**
 * Beloved's special-day reminders, scheduled by the Android app itself
 * (BelovedRemindersPlugin.java: AlarmManager, re-armed after a reboot). The
 * server never sees them. `sync` replaces every reminder this app had set with
 * the given ones; `clear` cancels them all.
 *
 * Newer APKs only: an older APK has no such plugin, its calls reject, and
 * Beloved says reminders need an app update. The web build has no scheduler.
 */
export interface BelovedRemindersPluginInterface {
  sync(options: { reminders: Reminder[] }): Promise<{ scheduled: number }>;
  clear(): Promise<{ scheduled: number }>;
}

const registered = registerPlugin<BelovedRemindersPluginInterface>('JarvisBelovedReminders');

declare global {
  interface Window {
    /** Hermetic browser tests only (the dev server): stands in for the Android plugin. */
    __jarvisBelovedRemindersMock?: BelovedRemindersPluginInterface;
  }
}

export function belovedRemindersPlugin(): BelovedRemindersPluginInterface | null {
  if (import.meta.env.DEV && typeof window !== 'undefined' && window.__jarvisBelovedRemindersMock) {
    return window.__jarvisBelovedRemindersMock;
  }
  return Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android' ? registered : null;
}

/** 'on' when the phone scheduled them, 'unsupported' on an APK without the plugin, 'web' in a browser. */
export type ReminderState = 'on' | 'unsupported' | 'web';

export async function syncReminders(reminders: Reminder[], plugin = belovedRemindersPlugin()): Promise<ReminderState> {
  if (!plugin) return 'web';
  try {
    await plugin.sync({ reminders });
    return 'on';
  } catch {
    return 'unsupported';
  }
}
