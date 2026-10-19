import { Capacitor } from '@capacitor/core';
import { storageGetSync } from './storage';

/** One brush segment on the shared 640x416 drawing board. */
export interface DrawingSegment {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  color: string;
  width: number;
  user?: string;
  name?: string;
}

/** Where the app reaches the gateway, native shell included. */
export function drawingBaseUrl(): string {
  if (Capacitor.isNativePlatform()) {
    return storageGetSync('jarvis_server_url') || 'http://localhost';
  }
  return window.location.origin;
}

/** The WebSocket URL for one live drawing board. */
export function drawingSocketUrl(baseUrl: string, token: string, room: string): string {
  const url = new URL('/api/family/drawing/ws', baseUrl);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('token', token);
  url.searchParams.set('room', room);
  return url.toString();
}

/** The signed-in person, as the local session stored them. */
export function drawingIdentity(): { username: string; name: string } {
  try {
    const raw = storageGetSync('jarvis_user');
    const user = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    const username = typeof user.username === 'string' ? user.username : '';
    const name =
      (typeof user.full_name === 'string' && user.full_name) ||
      (typeof user.display_name === 'string' && user.display_name) ||
      username ||
      'Family member';
    return { username, name };
  } catch {
    return { username: '', name: 'Family member' };
  }
}

export function isDrawingSegment(value: unknown): value is DrawingSegment {
  if (!value || typeof value !== 'object') return false;
  const segment = value as Record<string, unknown>;
  return (
    typeof segment.id === 'string' &&
    typeof segment.x1 === 'number' &&
    typeof segment.y1 === 'number' &&
    typeof segment.x2 === 'number' &&
    typeof segment.y2 === 'number' &&
    typeof segment.color === 'string' &&
    typeof segment.width === 'number'
  );
}
