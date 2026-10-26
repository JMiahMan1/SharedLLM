import { api } from '../services/api';
import { parseMaPlayerList, type MaPlayerListEntry } from './deviceLabels';

/**
 * Send a Music Assistant JSON-RPC command for a remote speaker.
 *
 * A remote `ma:<id>` target is not this browser: its commands go through the
 * gateway so selecting or controlling it never starts the Sendspin player.
 * Only local playback (`selectedTarget === ''`) uses the web player.
 */
export async function sendMaRemoteCommand(
  command: string,
  args: Record<string, unknown> = {},
): Promise<unknown> {
  const resp = await api.mediaMaCommand({ command, args });
  if (resp.status !== 'SUCCESS') {
    throw new Error(resp.message || `Music Assistant command failed: ${command}`);
  }
  return resp.result;
}

/**
 * List Music Assistant players without opening the web player, for when the
 * browser has no control socket to ask. A failure returns null (unknown), so
 * the caller keeps the list it already has instead of clearing the picker.
 */
export async function listRemoteMaPlayers(): Promise<MaPlayerListEntry[] | null> {
  try {
    return parseMaPlayerList(await sendMaRemoteCommand('players/all'));
  } catch {
    return null;
  }
}
