/**
 * When to upload a fix, given the geofence decision.
 *
 * The bug this exists to prevent: the app used to skip the location upload
 * entirely while someone was stationary inside the geofence. That is the one
 * state a family tracker exists to report -- "she is still home" -- and it was
 * the one state never sent. Steps kept flowing (that branch still synced them),
 * so the symptom was exactly "her steps update but her position doesn't".
 *
 * The original intent was sound: don't record a breadcrumb trail for someone
 * sitting still. The fix keeps that intent and changes the consequence, from
 * "never" to "occasionally".
 */

/** ~1/8 mile, matching LocationContext's GEOFENCE_RADIUS_M. */
export const GEOFENCE_RADIUS_M = 200;

/** How often to refresh a stationary position. */
export const STATIONARY_HEARTBEAT_MS = 5 * 60 * 1000;

/** Below this, a derived speed is noise rather than movement. */
export const MIN_SPEED_MPS = 0.5;

export function isInsideGeofence(
  prev: { lat: number; lng: number } | null,
  lat: number,
  lng: number,
  distanceM: (aLat: number, aLng: number, bLat: number, bLng: number) => number,
  radiusM: number = GEOFENCE_RADIUS_M,
): boolean {
  if (!prev) return false;
  return distanceM(prev.lat, prev.lng, lat, lng) < radiusM;
}

/**
 * Whether this fix should be uploaded.
 *
 * `lastUploadAt` is when this device last uploaded *any* location, so a person
 * who starts walking is uploaded immediately again rather than waiting out a
 * heartbeat window.
 */
export function shouldUploadFix(opts: {
  insideGeofence: boolean;
  moving: boolean;
  lastUploadAt: number | null;
  now: number;
  heartbeatMs?: number;
}): boolean {
  const { insideGeofence, moving, lastUploadAt, now } = opts;
  const heartbeatMs = opts.heartbeatMs ?? STATIONARY_HEARTBEAT_MS;

  // Moving: always upload, so a route is recorded and the trip detector sees it.
  if (moving) return true;
  // Not inside the geofence: a real move worth recording.
  if (!insideGeofence) return true;
  // Stationary and inside: heartbeat only, so "still here" stays a fact.
  if (lastUploadAt === null) return true;
  return now - lastUploadAt >= heartbeatMs;
}

/** A fix this accurate (m) is GPS-grade; worse than COARSE_FIX_M is a network/cell guess. */
export const GOOD_FIX_M = 50;
export const COARSE_FIX_M = 100;
/** Coarse fixes are skipped while a good fix is at most this old. */
export const GOOD_FIX_FRESH_MS = 2 * 60 * 1000;
/** ~134 mph: anything faster between two fixes is a position jump, not a drive. */
export const MAX_PLAUSIBLE_MPS = 60;

/**
 * Whether a fix is worth sending.
 *
 * Android hands over a backlog of fixes at once after the app was in the
 * background, GPS and network fixes interleaved; in production a network
 * cluster 800 m off the road alternated with the GPS track, and the route was
 * drawn through both. A fix older than the last one sent is "stale"; a coarse
 * one while accurate fixes are arriving is "coarse".
 */
export function classifyFix(opts: {
  fixTs: number;
  accuracy: number | null;
  lastUploadedFixTs: number;
  lastGoodFixTs: number;
}): 'ok' | 'stale' | 'coarse' {
  if (opts.fixTs < opts.lastUploadedFixTs) return 'stale';
  const coarse = opts.accuracy !== null && opts.accuracy > COARSE_FIX_M;
  if (coarse && opts.fixTs - opts.lastGoodFixTs < GOOD_FIX_FRESH_MS) return 'coarse';
  return 'ok';
}

/**
 * Speed (m/s) between two fixes, or 0 when it cannot be trusted: both must be
 * GPS-grade and a second or more apart. Two fixes from different sources
 * milliseconds apart made 200+ m/s "speeds" that started fake trips.
 */
export function derivedSpeedMps(
  prev: { lat: number; lng: number; t: number; acc: number | null } | null,
  cur: { lat: number; lng: number; t: number; acc: number | null },
  distanceM: (aLat: number, aLng: number, bLat: number, bLng: number) => number,
): number {
  if (!prev || prev.acc === null || cur.acc === null) return 0;
  if (prev.acc > GOOD_FIX_M || cur.acc > GOOD_FIX_M) return 0;
  const dtSec = (cur.t - prev.t) / 1000;
  if (dtSec < 1 || dtSec >= 60) return 0;
  const speed = distanceM(prev.lat, prev.lng, cur.lat, cur.lng) / dtSec;
  return speed <= MAX_PLAUSIBLE_MPS ? speed : 0;
}
