import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import type { MediaHomeResponse, MediaItem } from '../features/media/types';

/** A gateway MediaItem with defaults for the fields a test does not care about. */
export function mediaItem(over: Partial<MediaItem> & Pick<MediaItem, 'uri' | 'name'>): MediaItem {
  return { media_type: 'track', artist: '', artist_uri: '', album: '', album_uri: '', image: '', duration: null, version: '', favorite: null, ...over };
}

/** Serve `/api/media/home` with these shelves (the rest empty). */
export function serveHome(shelves: Partial<MediaHomeResponse> = {}): void {
  server.use(http.get('/api/media/home', () => HttpResponse.json({
    recent: [], continue: [], playlists: [], favorites: [], radio: [], errors: { ma: null, abs: null }, ...shelves,
  })));
}

/** Open the "Playing on" picker and pick `name`. */
export async function chooseOutput(name: string | RegExp): Promise<void> {
  await userEvent.click(await screen.findByRole('button', { name: /^Playing on/ }));
  const dialog = await screen.findByRole('dialog', { name: 'Playing on' });
  await userEvent.click(await within(dialog).findByRole('button', { name }));
}

/** Open the "Playing on" picker and return it, without picking. */
export async function openOutputPicker() {
  await userEvent.click(await screen.findByRole('button', { name: /^Playing on/ }));
  return within(await screen.findByRole('dialog', { name: 'Playing on' }));
}
