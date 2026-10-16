import { describe, it, expect, vi, beforeEach } from 'vitest';

class FakeParam {
  setValueAtTime = vi.fn();
  exponentialRampToValueAtTime = vi.fn();
}
class FakeNode {
  frequency = new FakeParam();
  gain = new FakeParam();
  type = '';
  connect = vi.fn((next: unknown) => next);
  start = vi.fn();
  stop = vi.fn();
}

let created: FakeContext[] = [];
class FakeContext {
  state: AudioContextState = 'running';
  currentTime = 0;
  destination = {};
  oscillators: FakeNode[] = [];
  resume = vi.fn(async () => { this.state = 'running'; });
  constructor() { created.push(this); }
  createOscillator() { const n = new FakeNode(); this.oscillators.push(n); return n; }
  createGain() { return new FakeNode(); }
}

describe('chime', () => {
  beforeEach(() => {
    created = [];
    vi.resetModules();
    Object.defineProperty(window, 'AudioContext', { configurable: true, writable: true, value: FakeContext });
  });

  it('plays two tones from one shared audio context', async () => {
    const { playChime } = await import('./chime');
    playChime();
    playChime();
    expect(created).toHaveLength(1);
    expect(created[0].oscillators).toHaveLength(4);
    for (const osc of created[0].oscillators) expect(osc.start).toHaveBeenCalled();
  });

  it('stays silent while the browser has audio suspended', async () => {
    const { playChime } = await import('./chime');
    FakeContext.prototype.resume = vi.fn(async () => {});
    const ctx = Object.assign(new FakeContext(), { state: 'suspended' as AudioContextState });
    created = [];
    Object.defineProperty(window, 'AudioContext', { configurable: true, writable: true, value: vi.fn(() => ctx) });
    playChime();
    expect(ctx.oscillators).toHaveLength(0);
  });

  it('does nothing where Web Audio does not exist', async () => {
    Object.defineProperty(window, 'AudioContext', { configurable: true, writable: true, value: undefined });
    const { playChime } = await import('./chime');
    expect(() => playChime()).not.toThrow();
  });
});
