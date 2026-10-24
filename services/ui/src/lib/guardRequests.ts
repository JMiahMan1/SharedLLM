// Plain words for "ask a parent first" requests (new apps, permissions, sites).
import { appName } from './phoneUsageFormat';
import type { GuardRequest, PermissionGroup } from '../types/api';

export const PERMISSION_WORDS: Record<PermissionGroup, string> = {
  location: 'location',
  camera: 'the camera',
  microphone: 'the microphone',
  contacts: 'contacts',
  sms: 'text messages',
  phone: 'phone calls',
  photos: 'photos and videos',
  calendar: 'the calendar',
  nearby: 'nearby devices',
};

/** What a request asks, in plain words. */
export function requestLine(r: GuardRequest): string {
  if (r.kind === 'app') return `Installed ${r.label || appName(r.target)} — waiting for your OK`;
  if (r.kind === 'permission') {
    const [pkg, group] = r.target.split('|');
    return `${r.label || appName(pkg)} wants ${PERMISSION_WORDS[group as PermissionGroup] || group}`;
  }
  return `Asks to open ${r.target}`;
}

/** "https://www.Example.com/page" -> "example.com". */
export const siteOf = (text: string) =>
  text.trim().toLowerCase().replace(/^[a-z]+:\/\//, '').replace(/[/?#].*$/, '').replace(/^www\./, '').replace(/\.$/, '');
