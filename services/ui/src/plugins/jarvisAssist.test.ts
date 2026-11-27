import { describe, expect, it } from 'vitest';
import { JarvisAssist } from './jarvisAssist';

/**
 * On the web (and in tests) there is no native half. Every call must still
 * settle: a missing assistant is a feature the phone does not have, never an
 * exception thrown at the person pressing the button.
 */
describe('the assistant plugin without a native half', () => {
  it('reports a state instead of throwing', async () => {
    await expect(JarvisAssist.status()).resolves.toEqual({ running: false, state: 'idle' });
  });

  it('accepts a turn, a stop and a wake phrase and settles every time', async () => {
    await expect(JarvisAssist.start({ wake: false })).resolves.toBeUndefined();
    await expect(JarvisAssist.stop()).resolves.toBeUndefined();
    await expect(JarvisAssist.stopSpeaking()).resolves.toBeUndefined();
    await expect(JarvisAssist.setWakePhrase({ phrase: 'hey jarvis' })).resolves.toBeUndefined();
  });

  it('says nothing out loud rather than failing', async () => {
    await expect(JarvisAssist.speak({ text: 'Hello.' })).resolves.toBeUndefined();
  });

  it('says a browser cannot hold the assistant role, and does not fail trying', async () => {
    await expect(JarvisAssist.assistantRole()).resolves.toEqual({ canHold: false, held: false });
    await expect(JarvisAssist.requestAssistantRole()).resolves.toBeUndefined();
  });
});
