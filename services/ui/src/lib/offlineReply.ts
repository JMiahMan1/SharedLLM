/**
 * The quick assistant's answer when the server cannot be reached (Android):
 * from this phone's saved information when it can (saying how old it is),
 * otherwise the question is queued for a family phone that is online, and it
 * says so plainly. Never dropped.
 */
import OfflineMode from '../plugins/offlineMode';
import { localAnswer } from './offlineAnswers';

const COMMAND = /^(turn|switch|set|start|stop|lock|unlock|open|close|dim)\b/i;

export async function offlineReply(text: string): Promise<string> {
  try {
    const { items } = await OfflineMode.cache();
    const local = localAnswer(text, items);
    if (local) return local;
  } catch {
    // No saved information: queue it.
  }
  try {
    await OfflineMode.queue({ kind: COMMAND.test(text) ? 'command' : 'ask', body: { text } });
    const { peerNearby } = await OfflineMode.requests();
    return peerNearby
      ? "Jarvis can't be reached from here. Your question is with the family phones nearby and goes to Jarvis as soon as one is online; the answer comes back here (Wander > Offline mode)."
      : "Jarvis can't be reached and no family phone is nearby yet. Your question is kept on this phone and goes as soon as one is (Wander > Offline mode).";
  } catch {
    return "Jarvis can't be reached from here, and the question could not be kept. Turn on offline mode in Wander.";
  }
}
