import PhoneUsage from '../plugins/phoneUsage';
import MediaSession from '../plugins/mediaSession';
import Comms from '../plugins/comms';
import PhotoScan from '../plugins/photoScan';
import { hasActivityAccess } from './locationTracking';

/**
 * ``promise``, or ``fallback`` when it fails or has not answered in ``ms``.
 * A native check that never answers used to stall the phone's reports for
 * good, with nothing on the server to say why.
 */
export function within<T>(promise: Promise<T>, ms: number, fallback: T): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<T>((resolve) => {
    timer = setTimeout(() => resolve(fallback), ms);
  });
  return Promise.race([promise.catch(() => fallback), late]).finally(() => clearTimeout(timer));
}

/** How long one native permission check may take before it counts as unanswered. */
export const CHECK_MS = 8000;

/** What the parents' reports need from this phone, besides the guard itself. */
export interface ReportingAccess {
  usage: boolean;
  notifications: boolean;
  calls: boolean;
  sms: boolean;
  /** Photos and videos for the safety scanner; null on an app build without it. */
  photos: boolean | null;
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
  const [usage, media, comms, photos, activity] = await Promise.all([
    within(PhoneUsage.hasAccess(), CHECK_MS, { granted: false }),
    within(MediaSession.hasAccess(), CHECK_MS, { granted: false }),
    within(Comms.hasAccess(), CHECK_MS, { calls: false, sms: false }),
    within<{ images: boolean; videos: boolean } | null>(PhotoScan.hasAccess(), CHECK_MS, null),
    within<boolean | null>(hasActivityAccess(), CHECK_MS, null),
  ]);
  return {
    usage: Boolean(usage?.granted),
    notifications: Boolean(media?.granted),
    calls: Boolean(comms?.calls),
    sms: Boolean(comms?.sms),
    photos: photos === null ? null : Boolean(photos.images || photos.videos),
    activity,
  };
}
