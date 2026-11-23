import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { api } from '../../../services/api';
import { resetSeenRecords, seenRecord, useSeenVisit } from './seen';

const belovedSeen = vi.fn(async (token: string, what: string) => {
  void token;
  void what;
  return { shared: true, notified: false };
});

vi.mock('../../../services/api', () => ({
  api: { belovedSeen: (...args: Parameters<typeof belovedSeen>) => belovedSeen(...args) },
}));

beforeEach(() => {
  belovedSeen.mockClear();
  belovedSeen.mockResolvedValue({ shared: true, notified: false });
  resetSeenRecords();
});

afterEach(() => {
  resetSeenRecords();
});

describe('reading signals', () => {
  it('signals a page visit once per open, again after the visit ends', async () => {
    const { rerender } = renderHook(({ active }) => useSeenVisit('room one', 'page', active), {
      initialProps: { active: true },
    });
    await waitFor(() => expect(belovedSeen).toHaveBeenCalledTimes(1));
    expect(belovedSeen).toHaveBeenCalledWith('room one', 'page');

    rerender({ active: true });
    await Promise.resolve();
    expect(belovedSeen).toHaveBeenCalledTimes(1);

    rerender({ active: false });
    rerender({ active: true });
    await waitFor(() => expect(belovedSeen).toHaveBeenCalledTimes(2));
  });

  it('signals a gallery opening only while the gallery is open', async () => {
    const { rerender } = renderHook(({ active }) => useSeenVisit('room one', 'gallery', active), {
      initialProps: { active: false },
    });
    await Promise.resolve();
    expect(belovedSeen).not.toHaveBeenCalled();
    rerender({ active: true });
    await waitFor(() => expect(belovedSeen).toHaveBeenCalledWith('room one', 'gallery'));
    expect(belovedSeen).toHaveBeenCalledTimes(1);
  });

  it('signals one expanded record once per session, not on every expand', async () => {
    expect(seenRecord('room one', 'note', 'rec-1')).toBe(true);
    expect(seenRecord('room one', 'note', 'rec-1')).toBe(false);
    expect(seenRecord('room one', 'prayer', 'rec-1')).toBe(true);
    expect(seenRecord('room two', 'note', 'rec-1')).toBe(true);
    await waitFor(() => expect(belovedSeen).toHaveBeenCalledTimes(3));
    expect(belovedSeen.mock.calls.map((c) => c[1])).toEqual(['note', 'prayer', 'note']);
  });

  it('swallows a failed post without throwing or blocking the caller', async () => {
    belovedSeen.mockRejectedValueOnce(new Error('offline'));
    expect(() => seenRecord('room one', 'idea', 'rec-2')).not.toThrow();
    await waitFor(() => expect(belovedSeen).toHaveBeenCalledTimes(1));
  });

  it('sends only the kind, never a record id or any content', async () => {
    seenRecord('room one', 'note', 'secret-record-id');
    await waitFor(() => expect(belovedSeen).toHaveBeenCalled());
    expect(belovedSeen).toHaveBeenCalledWith('room one', 'note');
    expect(JSON.stringify(belovedSeen.mock.calls)).not.toContain('secret-record-id');
    expect(JSON.stringify(belovedSeen.mock.calls)).not.toContain('sealed');
    expect(api).toBeDefined();
  });
});
