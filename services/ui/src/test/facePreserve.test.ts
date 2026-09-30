import { describe, it, expect } from 'vitest';
import {
  buildFacePreservePrompt,
  hasIdentityInstruction,
  FACE_PRESERVE_SUGGESTIONS,
} from '../lib/facePreserve';

describe('buildFacePreservePrompt', () => {
  it('preserves the callers scene wording verbatim', () => {
    const prompt = buildFacePreservePrompt('on a boat at sunset');
    expect(prompt).toContain('Now: on a boat at sunset');
  });

  it('trims surrounding whitespace so the scene is not mangled', () => {
    expect(buildFacePreservePrompt('   in a snowy forest   ')).toContain(
      'Now: in a snowy forest',
    );
  });

  it('falls back to a usable scene rather than an empty instruction', () => {
    // The model needs *something*; an empty tail produces an arbitrary result.
    const prompt = buildFacePreservePrompt('   ');
    expect(prompt).toContain('Now:');
    expect(prompt).not.toContain('Now:  ');
  });

  it('locks identity in the auto wording', () => {
    const prompt = buildFacePreservePrompt('a garden');
    expect(prompt).toMatch(/facial structure/i);
    expect(prompt).toMatch(/bone shape/i);
    expect(prompt).toMatch(/skin tone/i);
  });

  it('uses singular wording for one face', () => {
    const prompt = buildFacePreservePrompt('a cafe', 'one');
    expect(prompt).toMatch(/the person/i);
    expect(prompt).not.toMatch(/bone shape/i);
  });

  it('warns against blending faces for a group photo', () => {
    const prompt = buildFacePreservePrompt('a picnic', 'many');
    expect(prompt).toMatch(/do not blend their faces/i);
    expect(prompt).toMatch(/consistent with one another/i);
  });

  it('tells the model to change the scene, not just reproduce the photo', () => {
    expect(buildFacePreservePrompt('x')).toMatch(/not as the picture to reproduce/i);
  });

  it('forbids beautifying, which is the drift people actually notice', () => {
    expect(buildFacePreservePrompt('x')).toMatch(/do not beautify/i);
  });

  it('every suggestion produces a non-empty prompt', () => {
    for (const s of FACE_PRESERVE_SUGGESTIONS) {
      const p = buildFacePreservePrompt(s);
      expect(p.length).toBeGreaterThan(120);
      expect(p).toContain(`Now: ${s}`);
    }
  });
});

describe('hasIdentityInstruction', () => {
  it('detects a user-supplied identity instruction', () => {
    expect(hasIdentityInstruction('keep her face exactly the same')).toBe(true);
    expect(hasIdentityInstruction('preserve the same identity')).toBe(true);
  });

  it('does not fire on an ordinary scene prompt', () => {
    expect(hasIdentityInstruction('make it look like a 1970s photo')).toBe(false);
    expect(hasIdentityInstruction('remove the sign in the background')).toBe(false);
  });

  it('needs both halves: a face word AND a preservation verb', () => {
    // "keep" alone is not an identity instruction...
    expect(hasIdentityInstruction('keep the background tidy')).toBe(false);
    // ...and neither is a bare face mention.
    expect(hasIdentityInstruction('the face is in shadow')).toBe(false);
  });

  it('handles empty input', () => {
    expect(hasIdentityInstruction('')).toBe(false);
    expect(hasIdentityInstruction(null)).toBe(false);
    expect(hasIdentityInstruction(undefined)).toBe(false);
  });
});
