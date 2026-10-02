import { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Capacitor } from '@capacitor/core';
import { Geolocation } from '@capacitor/geolocation';
import toast from 'react-hot-toast';
import { storageGet, storageSet } from '../lib/storage';
import { getServerOrigin } from '../lib/serverUrl';
import StepCounter from '../plugins/stepCounter';
import TokenBridge from '../plugins/tokenBridge';
import {
  classifyLocationFailure,
  classifyStepFailure,
  isStepPermissionDenied,
  isUnsupportedAvailability,
  nextRetryDelayMs,
  sensorErrorMessage,
  shouldRetry,
} from '../lib/sensorFailure';
import { hasDayRolledOver, localDayKey } from '../lib/stepDay';

export type SensorId = 'location' | 'steps';
type NoticeOwner = 'service' | 'read';
export type SensorPermissionStatus = 'unknown' | 'granted' | 'denied' | 'unavailable' | 'disabled';

interface LocationState {
  latitude: number | null;
  longitude: number | null;
  accuracy: number | null;
  speed: number | null;
  timestamp: number | null;
  isTracking: boolean;
  error: string | null;
  interval: 'stationary' | 'transit';
}

interface SensorToggleState {
  enabled: boolean;
  permission: SensorPermissionStatus;
  message: string | null;
  /**
   * True while a transient failure is being retried automatically.
   *
   * Deliberately separate from `message`: a successful step read or upload
   * clears the routine message, and must not wipe the notice that tracking is
   * still recovering. The UI renders this as an explicit "reconnecting" state
   * so a silent stall is never the only symptom again.
   */
  recovering: boolean;
}

export interface SensorsState {
  location: SensorToggleState;
  steps: SensorToggleState;
}

interface LocationContextValue extends LocationState {
  sensors: SensorsState;
  enableSensor: (id: SensorId) => Promise<boolean>;
  disableSensor: (id: SensorId) => Promise<void>;
  openSensorSettings: (id: SensorId) => Promise<void>;
  startTracking: () => Promise<void>;
  stopTracking: () => void;
}

/* eslint-disable react-refresh/only-export-components */
export const LocationContext = createContext<LocationContextValue | null>(null);

const SPEED_THRESHOLD_MPH = 15;
const GEOFENCE_RADIUS_M = 200; // ~1/8 mile — don't log routes under this when stationary
const DAILY_STEPS_SYNC_INTERVAL_MS = 30000; // sync steps at least every 30s when stationary
const KEY_LOCATION_ENABLED = 'jarvis_sensor_location_enabled';
const KEY_STEPS_ENABLED = 'jarvis_sensor_steps_enabled';
const KEY_STEPS_BACKFILLED = 'jarvis_steps_backfilled_through';

function logSensor(scope: string, message: string, err?: unknown) {
  if (err !== undefined) {
    console.error(`[sensors:${scope}] ${message}`, err);
  } else {
    console.warn(`[sensors:${scope}] ${message}`);
  }
}


export function LocationProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<LocationState>({
    latitude: null,
    longitude: null,
    accuracy: null,
    speed: null,
    timestamp: null,
    isTracking: false,
    error: null,
    interval: 'stationary',
  });

  const [sensors, setSensors] = useState<SensorsState>({
    location: { enabled: false, permission: 'unknown', message: null, recovering: false },
    steps: { enabled: false, permission: 'unknown', message: null, recovering: false },
  });

  const lastLocationRef = useRef<{ lat: number; lng: number } | null>(null);
  const lastFixRef = useRef<{ lat: number; lng: number; t: number } | null>(null);
  const watchIdRef = useRef<string | number | null>(null);
  const startingRef = useRef(false);
  const intervalRef = useRef<'stationary' | 'transit'>('stationary');
  const dailyStepsRef = useRef<number | null>(null);
  const lastSyncedStepsRef = useRef<number | null>(null);
  const stepsPluginReadyRef = useRef(false);
  const stepUpdateListenerRef = useRef<{ remove: () => Promise<void> } | null>(null);
  const stationarySyncTimerRef = useRef<number | null>(null);
  // Self-heal bookkeeping: a transient step-counter failure must be retried with
  // backoff rather than persisted as a permanent disable.
  const stepRetryAttemptRef = useRef(0);
  const stepRetryAtRef = useRef(0);
  // Local calendar day the current step reading belongs to, so a rollover is
  // detected by comparing dates instead of racing a 60-second midnight window.
  const stepReadingDayRef = useRef<string | null>(null);
  // Synchronous mirrors of the `recovering` flags.
  //
  // React state is not safe to consult from the message-clearing guards below:
  // they run inside async continuations, and reading `sensorsRef.current` there
  // can observe the value from *before* the patch that set it — which silently
  // wiped the "retrying automatically" notice. These refs are written in the
  // same tick as the patch, so a guard always sees current truth.
  const stepRecoveringRef = useRef(false);
  const locationRecoveringRef = useRef(false);
  const locationNoticeOwnerRef = useRef<NoticeOwner | null>(null);
  const sensorsRef = useRef(sensors);
  useEffect(() => {
    sensorsRef.current = sensors;
  }, [sensors]);

  const calculateDistance = useCallback((lat1: number, lng1: number, lat2: number, lng2: number) => {
    const R = 6371e3;
    const φ1 = (lat1 * Math.PI) / 180;
    const φ2 = (lat2 * Math.PI) / 180;
    const Δφ = ((lat2 - lat1) * Math.PI) / 180;
    const Δλ = ((lng2 - lng1) * Math.PI) / 180;
    const a = Math.sin(Δφ / 2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }, []);

  const patchSensor = useCallback((id: SensorId, patch: Partial<SensorToggleState>) => {
    // Compute from the ref, write the ref synchronously, then commit state.
    //
    // This used to assign `sensorsRef.current` *inside* the setSensors updater,
    // which React does not run until render — so the ref was stale for the rest
    // of the tick. Every async gate that read it (startTracking, the sync
    // guards, the message-clearing guards) was therefore reading pre-patch
    // values, which silently clobbered messages set moments earlier.
    const next = {
      ...sensorsRef.current,
      [id]: { ...sensorsRef.current[id], ...patch },
    };
    sensorsRef.current = next;
    setSensors(next);
  }, []);

  /**
   * Which part of the sensor pipeline last set the visible message.
   *
   * The step counter has two independent failure sources — the *service*
   * (native listener/poll) and the *read* (fetching today's count) — and they
   * must not clear each other's notice. Without this, a successful service start
   * wiped "no hardware step counter" set moments earlier by the read path.
   */
  const stepNoticeOwnerRef = useRef<NoticeOwner | null>(null);

  /**
   * Set a sensor's recovery state, keeping the synchronous mirror in step with
   * React state so the message-clearing guards never read a stale value.
   *
   * Clearing only takes effect for the owner that set the notice, so unrelated
   * successes leave it alone.
   */
  const markRecovering = useCallback(
    (id: SensorId, owner: NoticeOwner, recovering: boolean, message: string | null) => {
      const recoveringRef = id === 'steps' ? stepRecoveringRef : locationRecoveringRef;
      const ownerRef = id === 'steps' ? stepNoticeOwnerRef : locationNoticeOwnerRef;
      recoveringRef.current = recovering;
      if (recovering) {
        ownerRef.current = owner;
        patchSensor(id, { recovering: true, message });
      } else if (ownerRef.current === owner || ownerRef.current === null) {
        ownerRef.current = null;
        patchSensor(id, { recovering: false, message: null });
      } else {
        // Another subsystem owns the current notice — leave it in place.
        patchSensor(id, { recovering: true });
      }
    },
    [patchSensor],
  );

  /**
   * Show a non-recovering notice (a definite condition such as "no hardware",
   * or a failed upload) without touching the recovery flag. Ownership still
   * applies, so a later success elsewhere cannot wipe it.
   */
  const setNotice = useCallback((id: SensorId, owner: NoticeOwner, message: string | null) => {
    const ownerRef = id === 'steps' ? stepNoticeOwnerRef : locationNoticeOwnerRef;
    ownerRef.current = message ? owner : null;
    patchSensor(id, { message });
  }, [patchSensor]);

  /**
   * Clear a sensor's message, unless it is currently recovering or owned by a
   * different subsystem — in either case the notice is still the truth.
   */
  const clearMessageUnlessRecovering = useCallback((id: SensorId, owner: NoticeOwner) => {
    const recoveringRef = id === 'steps' ? stepRecoveringRef : locationRecoveringRef;
    const ownerRef = id === 'steps' ? stepNoticeOwnerRef : locationNoticeOwnerRef;
    if (recoveringRef.current) return;
    if (ownerRef.current && ownerRef.current !== owner) return;
    ownerRef.current = null;
    patchSensor(id, { message: null });
  }, [patchSensor]);

  const stopStepService = useCallback(async () => {
    try {
      await StepCounter.stopPolling();
    } catch (err) {
      logSensor('steps', 'stopPolling failed', err);
    }
    if (stepUpdateListenerRef.current) {
      try {
        await stepUpdateListenerRef.current.remove();
      } catch (err) {
        logSensor('steps', 'listener remove failed', err);
      }
      stepUpdateListenerRef.current = null;
    }
    stepsPluginReadyRef.current = false;
    dailyStepsRef.current = null;
    lastSyncedStepsRef.current = null;
  }, []);

  const refreshDailySteps = useCallback(async () => {
    if (!sensorsRef.current.steps.enabled) return;
    try {
      if (!stepsPluginReadyRef.current) {
        const avail = await StepCounter.isAvailable();
        if (!avail.available) {
          patchSensor('steps', { permission: 'unavailable' });
          setNotice('steps', 'read', 'No hardware step counter on this device');
          return;
        }
        if (avail.permissionRequired && !avail.permissionGranted) {
          const req = await StepCounter.requestPermission();
          if (isStepPermissionDenied(req)) {
            // Denial turns the feature OFF (still re-enableable from Settings)
            patchSensor('steps', {
              enabled: false,
              permission: 'denied',
              message: req.permanentlyDenied
                ? 'Step permission blocked — enable it in system Settings'
                : 'Physical activity permission denied — steps turned off',
            });
            await storageSet(KEY_STEPS_ENABLED, 'false');
            await stopStepService();
            toast.error('Step tracking disabled — permission denied');
            logSensor('steps', 'permission denied by user');
            return;
          }
        }
        stepsPluginReadyRef.current = true;
      }
      const reading = await StepCounter.getTodaySteps();
      if (reading.available && typeof reading.steps === 'number' && reading.steps >= 0) {
        dailyStepsRef.current = reading.steps;
        stepReadingDayRef.current = localDayKey();
        patchSensor('steps', { permission: 'granted' });
        clearMessageUnlessRecovering('steps', 'read');
      }
    } catch (err) {
      // A throw here is never a permission denial (see lib/sensorFailure) — the
      // plugin only signals denial through requestPermission()'s return value.
      // The old code scanned the message for "permission" and permanently
      // disabled step tracking, which is how both phones silently went stale.
      markRecovering(
        'steps',
        'read',
        true,
        `${sensorErrorMessage(err, 'Step counter unavailable')} — retrying automatically`,
      );
      logSensor('steps', 'refresh failed (transient, will retry)', err);
    }
  }, [patchSensor, stopStepService]);

  // Sync steps independently of location updates (treadmill/stationary use)
  /**
   * The real username to attribute sensor readings to, or `null` if there isn't
   * one yet.
   *
   * This deliberately has no fallback literal. Three copies of this logic used
   * to end in `|| 'me'`, which wrote readings under the literal key `me` —
   * `geo:steps_meta:me` sat unreadable in production for a week. A reading we
   * cannot attribute is worse than a reading we skip: skipping is visible and
   * recoverable, a bogus key is invisible and permanent.
   */
  const resolveSyncUsername = useCallback(async (): Promise<string | null> => {
    const rawUser = await storageGet('jarvis_user');
    if (rawUser) {
      try {
        const parsed = JSON.parse(rawUser);
        const name = parsed?.username || parsed?.user_id || parsed?.id;
        if (typeof name === 'string' && name.trim()) return name.trim();
      } catch {
        // Stored value was a bare username rather than JSON.
        if (rawUser.trim()) return rawUser.trim();
      }
    }
    const stored = await storageGet('username');
    return typeof stored === 'string' && stored.trim() ? stored.trim() : null;
  }, []);

  /**
   * Reconcile missed days from the on-device ledger.
   *
   * The ledger is the source of truth for days the app never got to sync (app
   * not opened, phone off, no network). Each run sends any day newer than the
   * last one we reconciled, tagged with the source, so a gap is filled from
   * real device history instead of being silently lost.
   */
  const backfillStepHistory = useCallback(async () => {
    try {
      const token = await storageGet('jarvis_api_key');
      const rawServerUrl = await storageGet('jarvis_server_url');
      const serverUrl = rawServerUrl || getServerOrigin();
      if (!token || !serverUrl) return;
      if (!Capacitor.isNativePlatform()) return;

      const since = (await storageGet(KEY_STEPS_BACKFILLED)) || undefined;
      const history = await StepCounter.getDaysSince({ since, max: 60 });
      if (!history?.days?.length) return;

      const user = await resolveSyncUsername();
      if (!user) {
        logSensor('steps', 'backfill skipped: no username in storage (not writing a placeholder)');
        return;
      }

      let newest = since ?? '';
      for (const entry of history.days) {
        // Noon local keeps the reading clearly inside its own day.
        const ts = new Date(`${entry.day}T12:00:00`).getTime() / 1000;
        const resp = await fetch(`${serverUrl}/api/geo/steps`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({
            user_id: user,
            steps: entry.steps,
            timestamp: ts,
            source: entry.source || 'phone',
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          }),
        });
        if (!resp.ok) {
          logSensor('steps', `backfill for ${entry.day} failed HTTP ${resp.status}`);
          return; // retry the whole window next time
        }
        if (entry.day > newest) newest = entry.day;
      }
      if (newest) await storageSet(KEY_STEPS_BACKFILLED, newest);
      logSensor('steps', `backfilled ${history.days.length} ledger day(s) through ${newest || 'now'}`);
    } catch (err) {
      logSensor('steps', 'ledger backfill failed', err);
    }
  }, [ resolveSyncUsername ]);

  const syncDailySteps = useCallback(async () => {
    if (!sensorsRef.current.steps.enabled) return;
    try {
      const token = await storageGet('jarvis_api_key');
      const rawServerUrl = await storageGet('jarvis_server_url');
      const serverUrl = rawServerUrl || getServerOrigin();
      if (!token || !serverUrl) {
        logSensor('steps', 'sync skipped: missing token or server URL');
        return;
      }
      if (dailyStepsRef.current === null || dailyStepsRef.current === lastSyncedStepsRef.current) return;

      const user = await resolveSyncUsername();
      if (!user) {
        logSensor('steps', 'sync skipped: no username in storage (not writing a placeholder)');
        setNotice('steps', 'read', 'Not signed in — steps are not being uploaded yet');
        return;
      }

      const resp = await fetch(`${serverUrl}/api/geo/steps`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
        user_id: user,
        steps: dailyStepsRef.current,
        timestamp: Date.now() / 1000,
        source: 'phone',
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
      });
      if (!resp.ok) {
        const body = await resp.text().catch(() => '');
        logSensor('steps', `POST /api/geo/steps failed HTTP ${resp.status}`, body);
        setNotice('steps', 'read', `Step sync failed (HTTP ${resp.status})`);
        return;
      }
      lastSyncedStepsRef.current = dailyStepsRef.current;
      clearMessageUnlessRecovering('steps', 'read');
    } catch (err) {
      logSensor('steps', 'sync failed', err);
      markRecovering('steps', 'read', true, sensorErrorMessage(err, 'Step sync failed'));
    }
  }, [ patchSensor, resolveSyncUsername ]);

  /**
   * Single 30 s step-sync cadence.
   *
   * There used to be two timers writing into the same ref: one gated on being
   * stationary and one unconditional. Whichever registered first won, so the
   * gate silently did nothing. One unconditional tick keeps server step counts
   * fresh during walks too, and a pedometer read is cheap.
   */
  /**
   * Start the native step listener. **Never switches the sensor off.**
   *
   * The step plugin reports permission only through `requestPermission()`'s
   * return value — it puts no code on a thrown error — so anything thrown here
   * is transient by construction. The previous implementation disabled steps and
   * persisted `KEY_STEPS_ENABLED='false'` on *any* failure, which meant a single
   * bridge hiccup silently ended tracking until the user went digging in
   * Settings. Failures now schedule a backoff retry instead.
   *
   * Idempotent: returns immediately once the listener is attached.
   */
  const startStepService = useCallback(async (): Promise<boolean> => {
    if (stepUpdateListenerRef.current) {
      stepRetryAttemptRef.current = 0;
      stepRetryAtRef.current = 0;
      return true;
    }
    // Respect the backoff window so a persistent fault is not hammered.
    if (Date.now() < stepRetryAtRef.current) return false;
    try {
      await StepCounter.startPolling();
      stepUpdateListenerRef.current = await StepCounter.addListener('stepUpdate', (reading) => {
        if (reading.available && typeof reading.steps === 'number') {
          const changed = dailyStepsRef.current !== reading.steps;
          dailyStepsRef.current = reading.steps;
          // Push new step counts immediately — don't wait for a GPS fix
          if (changed) void syncDailySteps();
        }
      });
      stepRetryAttemptRef.current = 0;
      stepRetryAtRef.current = 0;
      markRecovering('steps', 'service', false, null);
      return true;
    } catch (err) {
      const kind = classifyStepFailure(err);
      if (shouldRetry(kind)) {
        stepRetryAttemptRef.current += 1;
        const wait = nextRetryDelayMs(stepRetryAttemptRef.current - 1);
        stepRetryAtRef.current = Date.now() + wait;
        logSensor('steps', `step service unavailable, retrying in ${wait}ms`, err);
        patchSensor('steps', {
          permission: Capacitor.isNativePlatform() ? 'unknown' : 'unavailable',
        });
        markRecovering(
          'steps',
          'service',
          true,
          `${sensorErrorMessage(err, 'Step counter unavailable')} — retrying automatically`,
        );
      }
      return false;
    }
  }, [patchSensor, syncDailySteps]);

  const ensureStepSyncTimer = useCallback(() => {
    if (stationarySyncTimerRef.current !== null) return;
    stationarySyncTimerRef.current = window.setInterval(() => {
      // This tick is the self-heal loop: it re-attaches the native step
      // listener (idempotent, backoff-aware) and re-reads the counter, so a
      // transient bridge failure recovers on its own instead of leaving the
      // sensor silently off.
      void startStepService()
        .then(() => refreshDailySteps())
        .then(() => syncDailySteps());
    }, DAILY_STEPS_SYNC_INTERVAL_MS);
  }, [refreshDailySteps, syncDailySteps, startStepService]);

  const syncToGateway = useCallback(async (lat: number, lng: number, accuracy: number | null, speed: number | null) => {
    if (!sensorsRef.current.location.enabled) return;
    try {
      const token = await storageGet('jarvis_api_key');
      const rawServerUrl = await storageGet('jarvis_server_url');
      const serverUrl = rawServerUrl || getServerOrigin();
      if (!token || !serverUrl) {
        logSensor('location', 'breadcrumb skipped: missing token or server URL');
        return;
      }

      const user = await resolveSyncUsername();
      if (!user) {
        logSensor('location', 'breadcrumb skipped: no username in storage (not writing a placeholder)');
        return;
      }

      let battery: number | undefined;
      try {
        if (typeof navigator !== 'undefined' && 'getBattery' in navigator) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const b = await (navigator as any).getBattery();
          if (b && typeof b.level === 'number') {
            battery = Math.round(b.level * 100);
          }
        }
      } catch (err) {
        logSensor('location', 'battery status unavailable', err);
      }

      const payload: Record<string, unknown> = {
        latitude: lat,
        longitude: lng,
        accuracy: accuracy ?? 0,
        speed: speed ?? 0,
        battery,
        timestamp: Date.now() / 1000,
        user_id: user,
      };
      // Attach hardware pedometer reading when present (real sensor data only)
      if (sensorsRef.current.steps.enabled && dailyStepsRef.current !== null) {
        payload.daily_steps = dailyStepsRef.current;
      }

      const resp = await fetch(`${serverUrl}/api/users/${encodeURIComponent(user)}/location`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify(payload),
      });

      if (!resp.ok && resp.status === 404) {
        const fallback = await fetch(`${serverUrl}/api/users/location`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify(payload),
        });
        if (!fallback.ok) {
          logSensor('location', `fallback location POST failed HTTP ${fallback.status}`);
          patchSensor('location', { message: `Location sync failed (HTTP ${fallback.status})` });
          return;
        }
      } else if (!resp.ok) {
        const body = await resp.text().catch(() => '');
        logSensor('location', `location POST failed HTTP ${resp.status}`, body);
        patchSensor('location', { message: `Location sync failed (HTTP ${resp.status})` });
        return;
      }
      patchSensor('location', { permission: 'granted', message: null });
    } catch (err) {
      logSensor('location', 'sync failed', err);
      markRecovering('location', 'service', true, sensorErrorMessage(err, 'Location unavailable'));
    }
  }, [ patchSensor, resolveSyncUsername ]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleLocationUpdate = useCallback(async (position: any) => {
    if (!sensorsRef.current.location.enabled) return;
    const { latitude, longitude, accuracy, speed } = position.coords;
    const fixTs = typeof position.timestamp === 'number' ? position.timestamp : Date.now();

    // Many Android/iOS fixes omit `speed`. Derive it from consecutive fixes so
    // the server's trip detector (>= 10 mph) actually fires during a drive.
    let speedMps = typeof speed === 'number' && speed > 0 ? speed : 0;
    if (speedMps === 0 && lastFixRef.current) {
      const dtSec = (fixTs - lastFixRef.current.t) / 1000;
      if (dtSec > 0.5 && dtSec < 60) {
        speedMps = calculateDistance(lastFixRef.current.lat, lastFixRef.current.lng, latitude, longitude) / dtSec;
      }
    }
    lastFixRef.current = { lat: latitude, lng: longitude, t: fixTs };
    if (Capacitor.isNativePlatform()) {
      TokenBridge.setLastLocation({ latitude, longitude }).catch((err) =>
        logSensor('location', 'TokenBridge.setLastLocation failed', err)
      );
    }

    const speedMph = speedMps * 2.237;
    const newInterval = speedMph > SPEED_THRESHOLD_MPH ? 'transit' : 'stationary';
    intervalRef.current = newInterval;

    setState((prev) => ({
      ...prev,
      latitude,
      longitude,
      accuracy: accuracy ?? null,
      speed: speedMps,
      timestamp: position.timestamp,
      isTracking: true,
      error: null,
      interval: newInterval,
    }));
    // Successful fix — clear any sticky sensor banner (timeout, old TokenBridge miss, etc.)
    patchSensor('location', { permission: 'granted', message: null });

    if (lastLocationRef.current) {
      const distance = calculateDistance(lastLocationRef.current.lat, lastLocationRef.current.lng, latitude, longitude);
      if (distance < GEOFENCE_RADIUS_M && newInterval === 'stationary') {
        // Stationary under threshold — don't log a breadcrumb, but keep syncing steps
        void syncDailySteps();
        return;
      }
    }

    lastLocationRef.current = { lat: latitude, lng: longitude };
    await syncToGateway(latitude, longitude, accuracy ?? null, speedMps);
  }, [calculateDistance, syncToGateway, syncDailySteps]);

  const startTracking = useCallback(async () => {
    // Guard against re-entry: startTracking identity changes must never stack watches
    if (watchIdRef.current !== null || startingRef.current) return;
    if (!sensorsRef.current.location.enabled) return;
    startingRef.current = true;
    setState((s) => ({ ...s, isTracking: true, error: null }));
    void storageSet('jarvis_location_tracking_enabled', 'true');

    try {
      // Kick off hardware step counting in parallel with location tracking
      if (sensorsRef.current.steps.enabled) {
        void refreshDailySteps();
        await startStepService();
      }

      if (!Capacitor.isNativePlatform()) {
        if (!navigator.geolocation) {
          const msg = 'Geolocation is not supported by this browser';
          setState((s) => ({ ...s, error: msg, isTracking: false }));
          patchSensor('location', { enabled: false, permission: 'unavailable', message: msg });
          await storageSet(KEY_LOCATION_ENABLED, 'false');
          return;
        }

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const success = (pos: any) => {
          handleLocationUpdate(pos);
        };

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const error = (err: any) => {
          logSensor('location', 'watchPosition error', err);
          setState((s) => ({ ...s, error: err.message, isTracking: false }));
          if (err?.code === 1) {
            patchSensor('location', { enabled: false, permission: 'denied', message: err.message });
            void storageSet(KEY_LOCATION_ENABLED, 'false');
            toast.error('Location permission denied — tracking turned off');
          } else {
            patchSensor('location', { message: err.message });
          }
        };

        const options = { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 };

        navigator.geolocation.getCurrentPosition(success, error, options);
        const watchId = navigator.geolocation.watchPosition(success, error, options);
        watchIdRef.current = watchId;
        return;
      }

      try {
        const permission = await Geolocation.checkPermissions();
        if (permission.location === 'denied') {
          const request = await Geolocation.requestPermissions();
          if (request.location === 'denied') {
            const msg = 'Location permission denied';
            setState((s) => ({ ...s, error: msg, isTracking: false }));
            patchSensor('location', { enabled: false, permission: 'denied', message: msg });
            await storageSet(KEY_LOCATION_ENABLED, 'false');
            toast.error('Location tracking disabled — permission denied');
            return;
          }
        }
        patchSensor('location', { permission: 'granted', message: null });

        // Start the continuous watch FIRST so a slow first fix never blocks
        // tracking. A one-shot getCurrentPosition timeout used to abort here
        // and surface "Could not obtain location in time" with no watch running.
        const gpsOptions = { enableHighAccuracy: true, timeout: 30000, maximumAge: 5000 };
        watchIdRef.current = await Geolocation.watchPosition(
          gpsOptions,
          (pos, err) => {
            if (pos) {
              setState((s) => ({ ...s, error: null }));
              handleLocationUpdate(pos);
            }
            if (err) {
              logSensor('location', 'native watch error', err);
              // Transient timeout while acquiring a fix is not a hard failure —
              // only surface permission-class errors as blocking errors.
              if (err.code === 1) {
                setState((s) => ({ ...s, error: err.message, isTracking: false }));
                patchSensor('location', { enabled: false, permission: 'denied', message: err.message });
                void storageSet(KEY_LOCATION_ENABLED, 'false');
                toast.error('Location permission denied — tracking turned off');
              } else if (err.code === 3) {
                // POSITION_UNAVAILABLE / TIMEOUT — keep watching, soft message only
                patchSensor('location', { message: 'Waiting for GPS fix…' });
                logSensor('location', 'waiting for GPS fix', err.message);
              } else {
                setState((s) => ({ ...s, error: err.message }));
                patchSensor('location', { message: err.message });
              }
            }
          }
        );

        // Opportunistic first fix — failures are non-fatal; the watch is live.
        try {
          const position = await Geolocation.getCurrentPosition(gpsOptions);
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          await handleLocationUpdate(position as any);
          setState((s) => ({ ...s, error: null }));
        } catch (firstFixErr) {
          logSensor('location', 'first fix not ready yet (watch continues)', firstFixErr);
        }

        // Sync steps immediately on tracking start, then reconcile any days
        // the ledger knows about that the server has not seen.
        void syncDailySteps();
        void backfillStepHistory();

        // One intentional 30 s cadence for step sync (see ensureStepSyncTimer):
        // the old pair of timers raced each other — whichever registered first
        // won, which accidentally disabled the intended stationary gate.
        ensureStepSyncTimer();
      } catch (err) {
        const msg = sensorErrorMessage(err, 'Failed to start location tracking');
        const kind = classifyLocationFailure(err);
        logSensor('location', `startTracking failed (${kind})`, err);
        if (kind === 'denied') {
          setState((s) => ({ ...s, error: msg, isTracking: false }));
          patchSensor('location', { enabled: false, permission: 'denied', message: msg });
          await storageSet(KEY_LOCATION_ENABLED, 'false');
          toast.error('Location tracking disabled — permission denied');
        } else {
          // Transient: leave the preference ON and let the retry loop recover.
          // The old code matched the substrings "permission"/"denied" in the
          // message, so any error mentioning permissions killed tracking for
          // good. Only a coded PERMISSION_DENIED is allowed to disable.
          setState((s) => ({ ...s, error: msg }));
          markRecovering('location', 'service', true, `${msg} — retrying automatically`);
        }
      }
    } finally {
      startingRef.current = false;
    }
  }, [handleLocationUpdate, refreshDailySteps, syncDailySteps, patchSensor]);

  const stopTracking = useCallback(() => {
    void storageSet('jarvis_location_tracking_enabled', 'false');
    if (watchIdRef.current !== null) {
      if (typeof watchIdRef.current === 'number') {
        navigator.geolocation.clearWatch(watchIdRef.current);
      } else {
        Geolocation.clearWatch({ id: watchIdRef.current });
      }
      watchIdRef.current = null;
    }
    if (stationarySyncTimerRef.current !== null) {
      clearInterval(stationarySyncTimerRef.current);
      stationarySyncTimerRef.current = null;
    }
    setState((s) => ({ ...s, isTracking: false }));
  }, []);

  const enableSensor = useCallback(async (id: SensorId): Promise<boolean> => {
    if (id === 'steps') {
      patchSensor('steps', { enabled: true, message: null });
      await storageSet(KEY_STEPS_ENABLED, 'true');
      try {
        const avail = await StepCounter.isAvailable();
        if (isUnsupportedAvailability(avail)) {
          patchSensor('steps', { enabled: false, permission: 'unavailable' });
          setNotice('steps', 'read', 'No hardware step counter on this device');
          await storageSet(KEY_STEPS_ENABLED, 'false');
          toast.error('Step counter unavailable on this device');
          return false;
        }
        if (avail.permissionRequired && !avail.permissionGranted) {
          const req = await StepCounter.requestPermission();
          if (isStepPermissionDenied(req)) {
            patchSensor('steps', {
              enabled: false,
              permission: 'denied',
              message: req.permanentlyDenied
                ? 'Blocked in system settings — open Settings to allow'
                : 'Permission denied — steps stay off',
            });
            await storageSet(KEY_STEPS_ENABLED, 'false');
            toast.error('Step permission denied');
            if (req.permanentlyDenied) {
              try {
                await StepCounter.openSettings();
              } catch (err) {
                logSensor('steps', 'openSettings failed', err);
              }
            }
            return false;
          }
        }
        patchSensor('steps', { enabled: true, permission: 'granted', message: null, recovering: false });
        stepsPluginReadyRef.current = false;
        void refreshDailySteps().then(() => syncDailySteps());
        void backfillStepHistory();
        await startStepService();
        return true;
      } catch (err) {
        // A failure while *enabling* is usually transient (bridge not ready).
        // Keep the user's intent on and let the retry loop recover, rather than
        // persisting an off-state they have to undo in Settings.
        patchSensor('steps', {
          enabled: true,
          message: `${sensorErrorMessage(err, 'Step counter unavailable')} — retrying automatically`,
        });
        stepRetryAttemptRef.current = 0;
        stepRetryAtRef.current = 0;
        void startStepService();
        logSensor('steps', 'enable attempt failed (will retry)', err);
        return true;
      }
    }

    // Location
    patchSensor('location', { enabled: true, permission: 'unknown', message: null });
    await storageSet(KEY_LOCATION_ENABLED, 'true');
    await storageSet('jarvis_location_tracking_enabled', 'true');
    await startTracking();
    return watchIdRef.current !== null || sensorsRef.current.location.enabled === true;
  }, [patchSensor, refreshDailySteps, syncDailySteps, startTracking]);

  const disableSensor = useCallback(async (id: SensorId): Promise<void> => {
    if (id === 'steps') {
      patchSensor('steps', { enabled: false, message: null });
      stepRecoveringRef.current = false;
      await storageSet(KEY_STEPS_ENABLED, 'false');
      await stopStepService();
      logSensor('steps', 'sensor disabled by user');
      return;
    }
    patchSensor('location', { enabled: false, message: null });
    locationRecoveringRef.current = false;
    await storageSet(KEY_LOCATION_ENABLED, 'false');
    await storageSet('jarvis_location_tracking_enabled', 'false');
    stopTracking();
    lastLocationRef.current = null;
    logSensor('location', 'sensor disabled by user');
  }, [patchSensor, stopStepService, stopTracking]);

  const openSensorSettings = useCallback(async (id: SensorId) => {
    if (id !== 'steps') {
      toast('Open system Settings → Apps → Jarvis OS → Permissions', { icon: 'ℹ️' });
      return;
    }
    try {
      await StepCounter.openSettings();
    } catch (err) {
      logSensor('steps', 'openSettings failed', err);
      toast.error('Could not open system settings');
    }
  }, []);

  // Load persisted sensor prefs and start any that were left on
  useEffect(() => {
    let cancelled = false;
    async function initSensors() {
      try {
        const [locPref, stepsPref, legacyLoc] = await Promise.all([
          storageGet(KEY_LOCATION_ENABLED),
          storageGet(KEY_STEPS_ENABLED),
          storageGet('jarvis_location_tracking_enabled'),
        ]);
        if (cancelled) return;

        // Default location ON for native (first run / legacy installs), OFF if explicitly off
        const locationOn = locPref !== null
          ? locPref === 'true'
          : (legacyLoc !== null ? legacyLoc === 'true' : Capacitor.isNativePlatform());
        // Default steps ON so first run can prompt; denial will flip it off
        const stepsOn = stepsPref !== 'true' ? stepsPref !== 'false' : true;

        setSensors(() => {
          const next: SensorsState = {
            location: {
              enabled: locationOn,
              permission: locationOn ? 'unknown' : 'disabled',
              message: null,
              recovering: false,
            },
            steps: {
              enabled: stepsOn,
              permission: stepsOn ? 'unknown' : 'disabled',
              message: null,
              recovering: false,
            },
          };
          sensorsRef.current = next;
          return next;
        });

        if (locationOn) {
          void startTracking();
        }
        if (stepsOn) {
          void refreshDailySteps().then(() => syncDailySteps());
          void backfillStepHistory();
          await startStepService();
          if (!Capacitor.isNativePlatform()) {
            // Web has no hardware pedometer; report unavailable without
            // disabling the preference so a native build recovers on its own.
            patchSensor('steps', { permission: 'unavailable' });
            setNotice('steps', 'read', 'Step counter requires the native app');
          }
          ensureStepSyncTimer();
        }
      } catch (err) {
        logSensor('init', 'failed to load sensor preferences', err);
      }
    }
    void initSensors();
    return () => {
      cancelled = true;
    };
  }, [startTracking, refreshDailySteps, syncDailySteps, patchSensor]);

  // Midnight rollover: re-read the sensor so the new day's bucket starts even if
// no step event has fired yet.
  //
  // This used to poll for `getHours() === 0 && getMinutes() === 0` on a 30 s
  // interval — a 60-second window. Backgrounded or killed across midnight and
  // the reset never ran. It now tracks which local day the current reading
  // belongs to and reacts to an actual date change, so the reset happens on the
  // next tick regardless of when the app was open.
  useEffect(() => {
    const checkDayChange = () => {
      if (!sensorsRef.current.steps.enabled) return;
      if (!hasDayRolledOver(stepReadingDayRef.current)) return;
      logSensor('steps', 'local day changed, resetting daily bucket');
      void refreshDailySteps().then(() => syncDailySteps());
    };
    // 30 s is frequent enough to feel immediate and rare enough to be free.
    const timer = window.setInterval(checkDayChange, 30000);
    const onVisible = () => {
      if (document.visibilityState === 'visible') checkDayChange();
    };
    document.addEventListener('visibilitychange', onVisible);
    checkDayChange();
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [refreshDailySteps, syncDailySteps]);

  useEffect(() => {
    return () => {
      if (watchIdRef.current !== null) {
        if (typeof watchIdRef.current === 'number') {
          navigator.geolocation.clearWatch(watchIdRef.current);
        } else {
          Geolocation.clearWatch({ id: watchIdRef.current });
        }
      }
      void StepCounter.stopPolling();
      if (stepUpdateListenerRef.current) {
        void stepUpdateListenerRef.current.remove();
        stepUpdateListenerRef.current = null;
      }
      if (stationarySyncTimerRef.current !== null) {
        clearInterval(stationarySyncTimerRef.current);
        stationarySyncTimerRef.current = null;
      }
    };
  }, []);

  // Refresh hardware step count when app returns to foreground — and push it,
  // otherwise the server only sees steps after the next GPS fix
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        if (sensorsRef.current.steps.enabled) {
          void refreshDailySteps().then(() => syncDailySteps());
        }
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refreshDailySteps, syncDailySteps]);

  const contextValue = useMemo(() => ({
    ...state,
    sensors,
    enableSensor,
    disableSensor,
    openSensorSettings,
    startTracking,
    stopTracking,
  }), [state, sensors, enableSensor, disableSensor, openSensorSettings, startTracking, stopTracking]);

  return (
    <LocationContext.Provider value={contextValue}>
      {children}
    </LocationContext.Provider>
  );
}

export function useLocation() {
  const ctx = useContext(LocationContext);
  if (!ctx) throw new Error('useLocation must be used within a LocationProvider');
  return ctx;
}

export function useBackgroundLocation() {
  return useLocation();
}
/* eslint-enable react-refresh/only-export-components */
