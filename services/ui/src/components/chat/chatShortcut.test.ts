import { describe, expect, it, vi } from 'vitest';
import { CHAT_SHORTCUT_GONE, belovedShortcutId, belovedShortcutRequest, chatShortcutId, chatShortcutPath, chatShortcutRequest, chatShortcutTarget } from './chatShortcut';
import { buildFamilyLookup } from './familyDirectory';
import { chatShortcutPlugin, chatShortcutSupported, type ChatShortcutPluginInterface } from '../../plugins/chatShortcut';

const people = buildFamilyLookup([
  { username: 'sam', display_name: 'Sam Example', nextcloud_user: 'sam', avatar: 'upload:7' },
  { username: 'robin', display_name: 'Robin Sample', nextcloud_user: 'robin', avatar: 'preset:balaams-donkey' },
]);

describe('chat shortcut payload', () => {
  it('has a stable id and the page a notification tap opens', () => {
    expect(chatShortcutId('abc123')).toBe('chat:abc123');
    expect(chatShortcutPath('abc123')).toBe('/family?chat=abc123');
    expect(chatShortcutPath('a b/c')).toBe('/family?chat=a%20b%2Fc');
    expect(CHAT_SHORTCUT_GONE).toBe('This chat is no longer available');
  });

  it("a one-to-one chat shows the other person's name and picture", () => {
    const target = chatShortcutTarget({
      conversation: { token: 'room-sam', display_name: 'Sam Example', name: 'sam', type: 1 },
      people,
      mayUsePrivate: false,
    });
    expect(target).toMatchObject({ token: 'room-sam', label: 'Sam Example', username: 'sam', avatar: 'upload:7', monogram: 'SE' });
    expect(chatShortcutRequest(target!, 'iVBORw0K')).toEqual({
      id: 'chat:room-sam', label: 'Sam Example', path: '/family?chat=room-sam', color: target!.color, monogram: 'SE', icon: 'iVBORw0K',
    });
    expect(chatShortcutRequest(target!, null)).not.toHaveProperty('icon');
  });

  it('a group gets its name and a monogram, no picture', () => {
    const target = chatShortcutTarget({ conversation: { token: 'room-g', display_name: 'Camping Trip', type: 2 }, people, mayUsePrivate: true });
    expect(target).toMatchObject({ label: 'Camping Trip', username: null, avatar: null, monogram: 'CT' });
    expect(target!.color).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it('a private chat goes to its own page (the lock screen) and carries only the partner', () => {
    const conversation = { token: 'roomprivate1', display_name: 'Private chat', type: 2, private: true };
    const privateInfo = { token: 'roomprivate1', partner: 'robin', partner_name: 'Robin Sample' };
    const target = chatShortcutTarget({ conversation, people, privateInfo, mayUsePrivate: true });
    expect(target).toMatchObject({ label: 'Robin Sample', username: 'robin', avatar: 'preset:balaams-donkey' });
    const request = chatShortcutRequest(target!);
    // A private shortcut asks for the chat itself, so a tap opens it even when
    // Beloved or the gallery is already on top of the same conversation.
    expect(request.path).toBe('/family?chat=roomprivate1&private=1');
    expect(Object.keys(request).sort()).toEqual(['color', 'id', 'label', 'monogram', 'path']);
  });

  it("Beloved's shortcut is the private chat's picture, named Beloved, opening Beloved", () => {
    const conversation = { token: 'roomprivate1', display_name: 'Private chat', type: 2, private: true };
    const privateInfo = { token: 'roomprivate1', partner: 'robin', partner_name: 'Robin Sample' };
    const target = chatShortcutTarget({ conversation, people, privateInfo, mayUsePrivate: true })!;
    const request = belovedShortcutRequest(target, 'cG5n');
    expect(request).toMatchObject({ id: 'chat:roomprivate1:beloved', label: 'Beloved', path: '/family?chat=roomprivate1&beloved=1', icon: 'cG5n' });
    expect(request.color).toBe(chatShortcutRequest(target).color);
    expect(belovedShortcutId('roomprivate1')).toBe('chat:roomprivate1:beloved');
  });

  it('a private chat is not offered to someone without the private chat, or before its record loads', () => {
    const conversation = { token: 'roomprivate1', display_name: 'Private chat', type: 2, private: true };
    const privateInfo = { token: 'roomprivate1', partner: 'robin', partner_name: 'Robin Sample' };
    expect(chatShortcutTarget({ conversation, people, privateInfo, mayUsePrivate: false })).toBeNull();
    expect(chatShortcutTarget({ conversation, people, mayUsePrivate: true })).toBeNull();
    expect(chatShortcutTarget({ conversation, people, privateInfo: { ...privateInfo, token: 'other' }, mayUsePrivate: true })).toBeNull();
    expect(chatShortcutTarget({ conversation: undefined, people, mayUsePrivate: true })).toBeNull();
  });
});

describe('chat shortcut plugin wrapper', () => {
  it('is absent on the web', async () => {
    expect(chatShortcutPlugin()).toBeNull();
    expect(await chatShortcutSupported()).toBe(false);
  });

  it('asks the launcher, and an APK without the plugin is not offered it', async () => {
    const yes = { supported: vi.fn(async () => ({ supported: true })) } as unknown as ChatShortcutPluginInterface;
    const missing = { supported: vi.fn(async () => { throw new Error('not implemented'); }) } as unknown as ChatShortcutPluginInterface;
    expect(await chatShortcutSupported(yes)).toBe(true);
    expect(await chatShortcutSupported(missing)).toBe(false);
  });
});
