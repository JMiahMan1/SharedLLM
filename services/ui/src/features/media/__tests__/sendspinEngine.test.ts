import { afterEach, describe, expect, it, vi } from 'vitest';
import { SendspinPlayer } from '@sendspin/sendspin-js';
import { missingSendspinInternals, resumeSendspinAudio, sendspinSocket } from '../engine/sendspinInternals';
import { SendspinAudioOutput } from '../engine/sendspinEngine';
import { FakeWebSocket } from '../../../test/fakes/FakeWebSocket';

describe('sendspin internals', () => {
  it('still finds the private fields on the real library (fails after an incompatible upgrade)', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const ws = new FakeWebSocket('ws://gw/api/sendspin') as unknown as WebSocket;
    const player = new SendspinPlayer({
      audioElement: document.createElement('audio'),
      playerId: 'shape-test',
      clientName: 'Shape test',
      webSocket: ws,
    } as ConstructorParameters<typeof SendspinPlayer>[0]);
    expect(missingSendspinInternals(player)).toEqual([]);
    // The socket is adopted on connect(); before that the field exists but is empty.
    expect(sendspinSocket(player)).toBeNull();
    expect(sendspinSocket({ core: { wsManager: { ws } } })).toBe(ws);
    vi.unstubAllGlobals();
  });

  it('reports what is missing and copes with odd shapes', () => {
    expect(missingSendspinInternals({})).toEqual(['core', 'scheduler']);
    expect(missingSendspinInternals({ core: {}, scheduler: {} })).toEqual(['core.wsManager', 'scheduler.resumeAudioContext']);
    expect(missingSendspinInternals({ core: { wsManager: {} }, scheduler: { resumeAudioContext: () => {} } })).toEqual(['core.wsManager.ws']);
    expect(sendspinSocket(null)).toBeNull();
  });

  it('resumes the audio context through whichever hook exists', () => {
    const resume = vi.fn().mockResolvedValue(undefined);
    resumeSendspinAudio({ scheduler: { resumeAudioContext: resume } });
    expect(resume).toHaveBeenCalled();
    const ctxResume = vi.fn().mockResolvedValue(undefined);
    resumeSendspinAudio({ scheduler: { audioContext: { state: 'suspended', resume: ctxResume } } });
    expect(ctxResume).toHaveBeenCalled();
    resumeSendspinAudio({});
  });
});

describe('SendspinAudioOutput', () => {
  afterEach(() => {
    document.querySelectorAll('audio').forEach((a) => a.remove());
  });

  it('BUG-49 keeps exactly one <audio> element over connect/disconnect cycles', () => {
    const out = new SendspinAudioOutput();
    for (let i = 0; i < 3; i += 1) {
      out.create(1, () => {});
      expect(document.querySelectorAll('audio')).toHaveLength(1);
      if (i < 2) out.create(1, () => {}); // a retry before the old one closed
      expect(document.querySelectorAll('audio')).toHaveLength(1);
      out.dispose();
      expect(document.querySelectorAll('audio')).toHaveLength(0);
    }
    out.dispose();
  });

  it('applies volume, mute and speed to the element', () => {
    const out = new SendspinAudioOutput();
    out.setVolume(50); // nothing to set yet
    const audio = out.create(1.5, () => {});
    expect(audio.playbackRate).toBe(1.5);
    out.setVolume(140);
    expect(audio.volume).toBe(1);
    out.setVolume(25);
    expect(audio.volume).toBe(0.25);
    out.setMuted(true);
    expect(audio.muted).toBe(true);
    out.setPlaybackRate(2);
    expect(audio.playbackRate).toBe(2);
    out.dispose();
  });

  it('unlocks the element and the audio context from a gesture', () => {
    const out = new SendspinAudioOutput();
    const audio = out.create(1, () => {});
    const play = vi.spyOn(audio, 'play').mockResolvedValue(undefined);
    const resume = vi.fn().mockResolvedValue(undefined);
    out.unlock({ scheduler: { resumeAudioContext: resume } });
    expect(play).toHaveBeenCalled();
    expect(resume).toHaveBeenCalled();
    out.dispose();
    out.unlock(null);
  });
});
