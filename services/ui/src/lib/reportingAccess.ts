import PhoneUsage from '../plugins/phoneUsage';
import MediaSession from '../plugins/mediaSession';
import Comms from '../plugins/comms';

/** What the parents' reports need from this phone, besides the guard itself. */
export interface ReportingAccess {
  usage: boolean;
  notifications: boolean;
  calls: boolean;
  sms: boolean;
}

/**
 * Whether this phone lets Jarvis read screen time, notifications (media and
 * chat apps), the call log and text messages. Without them those reports stay
 * empty with nothing to say why, so the phone also reports them (see
 * PhoneUsageSync) for the member page.
 */
export async function readReportingAccess(): Promise<ReportingAccess> {
  const [usage, media, comms] = await Promise.all([
    PhoneUsage.hasAccess().catch(() => ({ granted: false })),
    MediaSession.hasAccess().catch(() => ({ granted: false })),
    Comms.hasAccess().catch(() => ({ calls: false, sms: false })),
  ]);
  return { usage: Boolean(usage?.granted), notifications: Boolean(media?.granted), calls: Boolean(comms?.calls), sms: Boolean(comms?.sms) };
}
