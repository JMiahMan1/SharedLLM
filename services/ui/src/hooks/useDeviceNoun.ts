import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { handheldNoun, thisDeviceNoun, type HandheldNoun } from '../lib/deviceKind';
import { statusQuery } from '../lib/memberPage';

let formFactor: Promise<string | null> | null = null;

/** Android's answer, asked once per session (null in a browser or an older APK). */
function askFormFactor(): Promise<string | null> {
  formFactor ??= import('../plugins/deviceInfo')
    .then(({ default: DeviceInfo }) => DeviceInfo.getFormFactor())
    .then((r) => r.formFactor ?? null)
    .catch(() => null);
  return formFactor;
}

/**
 * "phone" or "tablet" for the device the app runs on (Android's own answer),
 * "device" in a browser or before it is known.
 */
export function useThisDeviceNoun(): 'phone' | 'tablet' | 'device' {
  const [answer, setAnswer] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void askFormFactor().then((f) => live && setAnswer(f));
    return () => {
      live = false;
    };
  }, []);
  return thisDeviceNoun(answer);
}

/** The word for a family member's phones and tablets, from their status (shared with the page's other reads). */
export function useMemberDeviceNoun(username: string): HandheldNoun {
  const { data } = useQuery({ ...statusQuery(username), enabled: Boolean(username) });
  return handheldNoun(data?.device_kinds);
}
