import Guard from '../plugins/guard';
import { api } from '../services/api';
import { storageGetSync, storageSet } from './storage';

const SEEN_ON = 'jarvis_guard_seen_on';

/**
 * On a child's Android phone: once the screen guard has been on, its being
 * off later (switched off, or killed and not restarted) is reported so the
 * parents hear. Quiet everywhere else, and before setup is ever finished.
 */
export async function checkGuard(username: string): Promise<void> {
  const status = await Guard.status();
  if (status.guardEnabled) {
    if (storageGetSync(SEEN_ON) !== '1') await storageSet(SEEN_ON, '1');
    return;
  }
  if (storageGetSync(SEEN_ON) !== '1') return;
  const screen = await api.getScreenTimeStatus(username);
  if (!screen.protected) return;
  await api.reportGuardTamper('guard_missing');
}
