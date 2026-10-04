import { describe, it, expect } from 'vitest';
import { formatDeviceLabel, parseMaPlayerList } from './deviceLabels';

/**
 * The exact name Music Assistant still holds for the web player, captured from
 * the production player list. Registered by an older build that passed the raw
 * `jarvis_user` localStorage value -- `JSON.stringify(profile)` -- as the
 * client's display name.
 */
const BLOB_NAME =
  '{"id":1,"username":"default","display_name":"Shared/Default User","is_admin":true,' +
  '"is_system_default":true,"nextcloud_url":"https://cloud.sumemail.com","nextcloud_user":"summers",' +
  '"ha_url":"https://ha.sumemail.com","audiobookshelf_url":"https://abs.sumemail.com/",' +
  '"audiobookshelf_user":"jarvis","audiobookshelf_api_key":null,"skylight_email":"someone@example.com",' +
  '"api_key":null,"role":"admin","voice_id":null}' +
  "'s Web Player (Desktop)";

describe('formatDeviceLabel', () => {
  it('returns an ordinary player name unchanged', () => {
    expect(formatDeviceLabel('Loft TV', 'loft-tv-2')).toBe('Loft TV');
  });

  it('recovers the display name out of a serialized profile blob', () => {
    expect(formatDeviceLabel(BLOB_NAME, 'wsp-9')).toBe("Shared/Default User's Web Player (Desktop)");
  });

  it('never leaks the rest of the profile the blob carried', () => {
    const label = formatDeviceLabel(BLOB_NAME, 'wsp-9');
    expect(label).not.toContain('api_key');
    expect(label).not.toContain('nextcloud');
    expect(label).not.toContain('someone@example.com');
    expect(label).not.toContain('{');
    expect(label.length).toBeLessThan(60);
  });

  it('falls back through username when a blob has no display name', () => {
    expect(formatDeviceLabel('{"username":"jeremiah"}' + "'s Web Player (Mobile)", 'wsp-1')).toBe(
      "jeremiah's Web Player (Mobile)"
    );
  });

  it('ignores an empty blob name and uses the next candidate', () => {
    expect(formatDeviceLabel('{"role":"admin"}', 'Loft TV', 'loft-tv-2')).toBe('Loft TV');
  });

  it('skips a blob that is not parseable instead of rendering it', () => {
    const label = formatDeviceLabel('{"username":"jeremiah", oops', 'wsp-2');
    expect(label).toBe('wsp-2');
    expect(label).not.toContain('{');
  });

  it('handles a whole-blob name with nothing appended to it', () => {
    expect(formatDeviceLabel('{"display_name":"Shared/Default User"}', 'wsp-3')).toBe(
      'Shared/Default User'
    );
  });

  it('is not fooled by an apostrophe inside a JSON value', () => {
    const blob = '{"display_name":"Jeremiah\'s Speaker"}' + "'s Web Player (Mobile)";
    expect(formatDeviceLabel(blob, 'wsp-4')).toBe("Jeremiah's Speaker's Web Player (Mobile)");
  });

  it('reads a name out of a structured value rather than stringifying it', () => {
    expect(formatDeviceLabel({ display_name: 'Loft TV' }, 'loft-tv-2')).toBe('Loft TV');
    expect(formatDeviceLabel({ name: 'Kitchen Speaker' }, 'kitchen')).toBe('Kitchen Speaker');
  });

  it('never renders "[object Object]"', () => {
    expect(formatDeviceLabel({ player_id: 'x' }, 'unknown-id')).toBe('unknown-id');
    expect(formatDeviceLabel(undefined, null, '')).toBe('Unknown Player');
  });

  it('collapses whitespace and clamps an unreasonably long label', () => {
    expect(formatDeviceLabel('  Loft   TV  ', 'loft')).toBe('Loft TV');
    const long = 'x'.repeat(200);
    const label = formatDeviceLabel(long, 'loft');
    expect(label.length).toBeLessThanOrEqual(60);
    expect(label.endsWith('…')).toBe(true);
  });
});
describe('parseMaPlayerList', () => {
  /** The exact player list production Music Assistant returned when this was found. */
  const prodReply = {
    result: [
      { player_id: 'wsp-9mn', name: BLOB_NAME, available: true, state: 'idle', powered: true },
      { player_id: 'loft-samsung-q900', name: 'Loft Samsung Q900', available: false, state: 'off' },
      { player_id: 'sendspin-js', name: 'Sendspin JS Client (u9mn)' },
      { player_id: 'no-name-here', available: true },
      { name: 'missing an id entirely' },
      null,
    ],
  };

  it('unwraps the JSON-RPC result envelope', () => {
    expect(parseMaPlayerList(prodReply)).toHaveLength(4);
  });

  it('labels the blob-named player without dumping the profile', () => {
    const [webPlayer] = parseMaPlayerList(prodReply);
    expect(webPlayer.name).toBe("Shared/Default User's Web Player (Desktop)");
    expect(webPlayer.name).not.toContain('api_key');
  });

  it('falls back to the player id when MA has no usable name', () => {
    const rows = parseMaPlayerList(prodReply);
    expect(rows[3]).toMatchObject({ player_id: 'no-name-here', name: 'no-name-here' });
  });

  it('drops entries that could never be selected', () => {
    const ids = parseMaPlayerList(prodReply).map((p) => p.player_id);
    expect(ids).not.toContain('');
    expect(parseMaPlayerList([{ name: 'orphan' }])).toEqual([]);
  });

  it('defaults availability, state and power the way the picker expects', () => {
    const rows = parseMaPlayerList(prodReply);
    expect(rows[2]).toMatchObject({ available: true, state: 'idle', powered: true });
    expect(rows[1]).toMatchObject({ available: false, state: 'off', powered: true });
  });

  it('accepts a bare array as well as the envelope', () => {
    expect(parseMaPlayerList([{ player_id: 'a', name: 'Loft TV' }])).toEqual([
      { player_id: 'a', name: 'Loft TV', available: true, state: 'idle', powered: true },
    ]);
  });

  it('returns an empty list for anything that is not a list', () => {
    // What a disconnected or erroring ma-jsonrpc socket actually replies with.
    expect(parseMaPlayerList({ result: {} })).toEqual([]);
    expect(parseMaPlayerList({})).toEqual([]);
    expect(parseMaPlayerList(null)).toEqual([]);
    expect(parseMaPlayerList('nope')).toEqual([]);
  });
});
