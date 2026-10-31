import PhoneUsage from '../plugins/phoneUsage';
import MediaSession from '../plugins/mediaSession';
import Comms from '../plugins/comms';
import { hasActivityAccess } from './locationTracking';

/** What the parents' reports need from this phone, besides the guard itself. */
export interface ReportingAccess {
  usage: boolean;
  notifications: boolean;
  calls: boolean;
  sms: boolean;
  /** Physical activity (motion sensors: knowing when someone is driving); null on an app build without it. */
  activity: boolean | null;
}

/**
 * Whether this phone lets Jarvis read screen time, notifications (media and
 * chat apps), the call log and text messages. Without them those reports stay
 * empty with nothing to say why, so the phone also reports them (see
 * PhoneUsageSync) for the member page.
 */
export async function readReportingAccess(): Promise<ReportingAccess> {
  const [usage, media, comms, activity] = await Promise.all([
    PhoneUsage.hasAccess().catch(() => ({ granted: false })),
    MediaSession.hasAccess().catch(() => ({ granted: false })),
    Comms.hasAccess().catch(() => ({ calls: false, sms: false })),
    hasActivityAccess().catch(() => null),
  ]);
  return {
    usage: Boolean(usage?.granted),
    notifications: Boolean(media?.granted),
    calls: Boolean(comms?.calls),
    sms: Boolean(comms?.sms),
    activity,
  };
}
