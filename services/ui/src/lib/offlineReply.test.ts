import { describe, it, expect, vi, beforeEach } from 'vitest';

const plugin = vi.hoisted(() => ({ cache: vi.fn(), queue: vi.fn(), requests: vi.fn() }));
vi.mock('../plugins/offlineMode', () => ({ default: plugin }));

import { offlineReply } from './offlineReply';

describe('offlineReply', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    plugin.cache.mockResolvedValue({ items: [{ key: 'locations', version: Date.now(), data: [{ username: 'jordan', zone: 'Home' }] }] });
    plugin.queue.mockResolvedValue({ id: 'x' });
  });

  it('answers from saved information when it can', async () => {
    expect(await offlineReply('Where is jordan?')).toMatch(/^jordan was at Home \(as of/);
    expect(plugin.queue).not.toHaveBeenCalled();
  });

  it('queues the rest and says where it is', async () => {
    plugin.requests.mockResolvedValue({ requests: [], online: false, peerNearby: false });
    expect(await offlineReply('Turn on the porch light')).toMatch(/no family phone is nearby yet/);
    expect(plugin.queue).toHaveBeenCalledWith({ kind: 'command', body: { text: 'Turn on the porch light' } });
    plugin.requests.mockResolvedValue({ requests: [], online: false, peerNearby: true });
    expect(await offlineReply('What is for dinner?')).toMatch(/with the family phones nearby/);
  });

  it('never drops a question silently', async () => {
    plugin.queue.mockRejectedValue(new Error('off'));
    expect(await offlineReply('Hello?')).toMatch(/could not be kept/);
  });
});
