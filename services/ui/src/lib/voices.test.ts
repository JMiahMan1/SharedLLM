import { describe, expect, it } from 'vitest';
import { describeVoice, voiceGroups } from './voices';

describe('voice names', () => {
  it('names Kokoro voices by name, accent and voice', () => {
    expect(describeVoice('af_heart')).toEqual({ id: 'af_heart', name: 'Heart, American female', group: 'American' });
    expect(describeVoice('bm_fable').name).toBe('Fable, British male');
  });

  it('names Piper voices from their model name', () => {
    expect(describeVoice('piper:en_US-hfc_male-medium')).toEqual({
      id: 'piper:en_US-hfc_male-medium', name: 'HFC male, American (Piper)', group: 'Piper',
    });
  });

  it('uses the featured name for a blend, and spells one out otherwise', () => {
    const featured = [{ id: 'bm_fable,bm_lewis,bm_george', name: 'Parable Party announcer' }];
    expect(describeVoice('bm_fable,bm_lewis,bm_george', featured).name).toBe('Parable Party announcer');
    expect(describeVoice('af_heart,am_adam').name).toBe('Blend of Heart, Adam');
  });

  it('groups Piper and blends first, then accents, each sorted by name', () => {
    const groups = voiceGroups(['bm_lewis', 'af_heart', 'am_adam', 'piper:en_US-hfc_male-medium'],
      [{ id: 'bm_fable,bm_lewis,bm_george', name: 'Parable Party announcer' }]);
    expect(groups.map((g) => g.group)).toEqual(['Piper', 'Blends', 'American', 'British']);
    expect(groups[2].voices.map((v) => v.name)).toEqual(['Adam, American male', 'Heart, American female']);
  });

  it('survives a malformed list instead of throwing during render', () => {
    // The gateway answered with a FAILURE body (or an older server sent none), so
    // the panel passed undefined: spreading it threw "voices is not iterable" and
    // the error boundary replaced the whole Settings page.
    expect(voiceGroups(undefined as unknown as string[], [])).toEqual([]);
    expect(voiceGroups([], undefined as unknown as { id: string; name: string }[])).toEqual([]);
    // Whatever is readable is still offered.
    const groups = voiceGroups(['af_heart', undefined as unknown as string], []);
    expect(groups.flatMap((g) => g.voices.map((v) => v.id))).toEqual(['af_heart']);
  });
});
