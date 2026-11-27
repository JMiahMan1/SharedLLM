import { describe, expect, it, vi } from 'vitest';
import {
  isVoicePath,
  onVoiceAssistantRequested,
  openVoiceAssistant,
  takePendingVoiceRequest,
  voiceRequestedHere,
} from './voiceLaunch';

describe('voice launch addresses', () => {
  it('treats the Android trigger address as the assistant, not a page', () => {
    expect(isVoicePath('/?voice=1')).toBe(true);
    expect(isVoicePath('/?voice=1&from=tile')).toBe(true);
    expect(isVoicePath('/assistant')).toBe(true);
    expect(isVoicePath('/assistant/')).toBe(true);
  });

  it('leaves ordinary pages and nonsense alone', () => {
    expect(isVoicePath('/')).toBe(false);
    expect(isVoicePath('/family?chat=7')).toBe(false);
    expect(isVoicePath('/?voice=0')).toBe(false);
    expect(isVoicePath('https://example.com/?voice=1')).toBe(false);
    expect(isVoicePath('')).toBe(false);
  });

  it('sees the request in the address a cold start arrived at', () => {
    expect(voiceRequestedHere('?voice=1', '')).toBe(true);
    expect(voiceRequestedHere('', '#assistant')).toBe(true);
    expect(voiceRequestedHere('', '#/assistant')).toBe(true);
    expect(voiceRequestedHere('', '#/')).toBe(false);
    expect(voiceRequestedHere('?chat=3', '')).toBe(false);
  });
});

describe('voice launch signal', () => {
  it('reaches every screen that asked, and stops when unsubscribed', () => {
    const first = vi.fn();
    const second = vi.fn();
    const offFirst = onVoiceAssistantRequested(first);
    const offSecond = onVoiceAssistantRequested(second);

    openVoiceAssistant();
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);

    offFirst();
    openVoiceAssistant();
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(2);

    offSecond();
    openVoiceAssistant();
    expect(second).toHaveBeenCalledTimes(2);
  });

  it('keeps going when one listener throws', () => {
    const warn = vi.spyOn(console, 'error').mockImplementation(() => {});
    const broken = vi.fn(() => {
      throw new Error('no sheet here');
    });
    const good = vi.fn();
    const offs = [onVoiceAssistantRequested(broken), onVoiceAssistantRequested(good)];

    expect(() => openVoiceAssistant()).not.toThrow();
    expect(good).toHaveBeenCalledTimes(1);

    offs.forEach((off) => off());
    warn.mockRestore();
  });

  it('holds a request made where no sheet is mounted until a page claims it', () => {
    // The header button on /media: the sheet is not on screen yet.
    takePendingVoiceRequest();
    expect(takePendingVoiceRequest()).toBe(false);

    openVoiceAssistant();
    expect(takePendingVoiceRequest()).toBe(true);
    // Claimed once: a later mount must not reopen a closed sheet.
    expect(takePendingVoiceRequest()).toBe(false);
  });
});
