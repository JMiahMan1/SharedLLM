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
