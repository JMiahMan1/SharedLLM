import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../../../test/setup';
import { useMediaStore } from '../store/mediaStore';
import type { WebPlayerHandle } from '../engine/adapters/adapters';

// UX-39: the media actions map to `light` haptics, never the harsh `heavy`.
const { trigger } = vi.hoisted(() => ({ trigger: vi.fn() }));
vi.mock('../../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger }) }));

import { usePlayback } from '../engine/usePlayback';

const web: WebPlayerHandle = {
  playerId: 'me',
  play: vi.fn().mockResolvedValue(undefined),
  enqueue: vi.fn().mockResolvedValue(undefined),
  pause: vi.fn().mockResolvedValue(undefined),
  next: vi.fn().mockResolvedValue(undefined),
  previous: vi.fn().mockResolvedValue(undefined),
  stop: vi.fn().mockResolvedValue(undefined),
  seek: vi.fn().mockResolvedValue(undefined),
  setVolume: vi.fn(),
  setMuted: vi.fn(),
  maCommand: vi.fn().mockResolvedValue({}),
};

beforeEach(() => {
  trigger.mockClear();
  server.use(http.post('/api/media/ma-command', () => HttpResponse.json({ status: 'SUCCESS', result: null })));
});

describe('UX-39 media haptics', () => {
  it('UX-39 uses a light tap for transport and play, and never the heavy style', async () => {
    const { result } = renderHook(() => usePlayback(web));
    await act(() => result.current.play({ kind: 'uri', uri: 'library://track/1', title: 'One' }, 'row-1'));
    await act(() => result.current.transport('next'));
    await act(() => result.current.setShuffle(true));
    await act(() => result.current.setRepeat('all'));

    expect(trigger).toHaveBeenCalledWith('light');
    expect(trigger.mock.calls.every(([style]) => style !== 'heavy')).toBe(true);
    // The store's selected output is untouched by these actions.
    expect(useMediaStore.getState().activeOutputId).toBe('');
  });
});
