import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';

import { featureForPath } from '../../lib/activityFeature';
import { storageGetSync } from '../../lib/storage';
import { api } from '../../services/api';

/**
 * Posts one light activity event per visited page, fire and forget. The feed
 * is what parents review and what Jarvis later reads to recommend; a beacon
 * never blocks or fails a page, and it stays quiet until signed in.
 */
export default function ActivityBeacon() {
  const location = useLocation();
  const lastPath = useRef<string>('');

  useEffect(() => {
    const path = location.pathname;
    if (path === lastPath.current) return;
    lastPath.current = path;
    if (!storageGetSync('jarvis_api_key')) return;
    void api.postActivity({ feature: featureForPath(path), item: path, event: 'view' }).catch(() => undefined);
  }, [location.pathname]);

  return null;
}
