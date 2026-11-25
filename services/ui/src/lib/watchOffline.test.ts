import { describe, it, expect, vi, beforeEach } from 'vitest';

const ble = vi.hoisted(() => ({ askWatch: vi.fn(), isWatchConnected: vi.fn(() => true), notifyWatch: vi.fn(async () => undefined) }));
vi.mock('./watchBle', () => ble);
const offline = vi.hoisted(() => ({ queue: vi.fn(), requests: vi.fn() }));
vi.mock('../plugins/offlineMode', () => ({ default: offline }));

import { askWatchOrQueue, deliverWatchAnswers, isWatchOfflineAnswer, readPending } from './watchOffline';

const ID = 'a'.repeat(32);

describe('watchOffline', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    ble.isWatchConnected.mockReturnValue(true);
  });

  it("knows the watch's own I-could-not-reach-Jarvis replies", () => {
    expect(isWatchOfflineAnswer('The watch is offline. Swipe down and tap Wi-Fi to connect.')).toBe(true);
    expect(isWatchOfflineAnswer("Couldn't reach Jarvis at 192.0.2.10:11435.")).toBe(true);
    expect(isWatchOfflineAnswer('Jarvis returned HTTP 502.')).toBe(true);
    expect(isWatchOfflineAnswer('')).toBe(true);
    expect(isWatchOfflineAnswer('It is sunny.')).toBe(false);
  });

  it('passes an answered question straight back and queues one the watch could not send', async () => {
    ble.askWatch.mockResolvedValueOnce('It is sunny.');
    expect(await askWatchOrQueue('Weather?')).toEqual({ answered: 'It is sunny.' });
    expect(offline.queue).not.toHaveBeenCalled();

    ble.askWatch.mockResolvedValueOnce('The watch is offline. Swipe down and tap Wi-Fi to connect.');
    offline.queue.mockResolvedValueOnce({ id: ID });
    expect(await askWatchOrQueue('Weather?', () => 5)).toEqual({ queued: ID });
    expect(offline.queue).toHaveBeenCalledWith({ kind: 'ask', body: { text: 'Weather?' } });
    expect(readPending()).toEqual([{ id: ID, question: 'Weather?', queuedAt: 5 }]);
  });

  it('sends the answer to the watch when it arrives, once', async () => {
    localStorage.setItem('jarvis_watch_offline_pending', JSON.stringify([{ id: ID, question: 'Weather?', queuedAt: 1 }]));
    offline.requests.mockResolvedValue({ requests: [{ id: ID, status: 'waiting_for_online_phone' }], online: false, peerNearby: true });
    expect(await deliverWatchAnswers()).toBe(0);
    expect(readPending()).toHaveLength(1);

    offline.requests.mockResolvedValue({ requests: [{ id: ID, status: 'answered', answer: 'Rain after 4.' }], online: true, peerNearby: true });
    expect(await deliverWatchAnswers()).toBe(1);
    expect(ble.notifyWatch).toHaveBeenCalledWith('Jarvis', 'Rain after 4.');
    expect(readPending()).toEqual([]);
    expect(await deliverWatchAnswers()).toBe(0);
    expect(ble.notifyWatch).toHaveBeenCalledTimes(1);
  });

  it('waits for the watch to be connected before delivering', async () => {
    localStorage.setItem('jarvis_watch_offline_pending', JSON.stringify([{ id: ID, question: 'Q', queuedAt: 1 }]));
    ble.isWatchConnected.mockReturnValue(false);
    expect(await deliverWatchAnswers()).toBe(0);
    expect(offline.requests).not.toHaveBeenCalled();
    expect(readPending()).toHaveLength(1);
  });
});
