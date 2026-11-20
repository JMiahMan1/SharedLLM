import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { mockHealth } from '../fixtures/healthMocks';
import { mockBible } from '../fixtures/bibleMocks';
import { mockFamilyPages, MESSAGES } from '../fixtures/familyPagesMocks';
import { quietHotReload, screenFindings, settle, shot } from '../fixtures/pageAudit';

const expect = baseExpect.configure({ timeout: 20_000 });

/**
 * Reactions in the family chat, as two people see them. "Parent" is this
 * device; "Robin" is another member whose reactions reach it the way Talk
 * delivers them: a hidden system update (no line to draw) through the long
 * poll, after which the message's counts are read again. Covers adding mine,
 * a second person using the same emoji (a count of two, not one replacing the
 * other), a new emoji arriving from someone else, who reacted, and taking mine
 * back without touching theirs. Hermetic: the server is mocked.
 */

const TOKEN = 'room-trip';
const SEC = Math.floor(Date.now() / 1000);

interface Actor { actor_type: string; actor_id: string; display_name: string }

interface Room {
  /** Who reacted with what, per message id: the server's truth. */
  reactions: Record<number, Record<string, Actor[]>>;
  /** Hidden updates waiting for the long poll, and the newest id the room has. */
  updates: Array<Record<string, unknown>>;
  lastId: number;
  calls: string[];
}

const ME: Actor = { actor_type: 'users', actor_id: 'parent', display_name: 'Parent' };
const ROBIN: Actor = { actor_type: 'users', actor_id: 'robin', display_name: 'Robin' };

function counts(room: Room, id: number): { reactions: Record<string, number>; reactions_self: string[] } {
  const per = room.reactions[id] ?? {};
  const reactions: Record<string, number> = {};
  const self: string[] = [];
  for (const [emoji, people] of Object.entries(per)) {
    if (!people.length) continue;
    reactions[emoji] = people.length;
    if (people.some((p) => p.actor_id === ME.actor_id)) self.push(emoji);
  }
  return { reactions, reactions_self: self };
}

function messages(room: Room) {
  return MESSAGES.map((m) => ({ ...m, token: TOKEN, ...counts(room, m.id) }));
}

/** Someone else reacts (or takes one back): stored, and a hidden update queued for the long poll. */
function otherReacts(room: Room, who: Actor, id: number, emoji: string, adding = true): void {
  const per = (room.reactions[id] ??= {});
  const people = (per[emoji] ??= []);
  if (adding) people.push(who);
  else per[emoji] = people.filter((p) => p.actor_id !== who.actor_id);
  room.lastId += 1;
  room.updates.push({ id: room.lastId, token: TOKEN, actor_type: 'users', actor_id: who.actor_id, actor_display_name: who.display_name,
    timestamp: SEC + room.lastId, message_type: 'system', system_message: adding ? 'reaction' : 'reaction_revoked', message: emoji,
    parent: { id } });
}

async function serve(page: Page, room: Room): Promise<void> {
  // Registered after the shared mocks, so these answer first.
  await page.route(/\/api\/communication\/talk\/(messages(\/wait)?|react|unreact|reactions)(\?|$)/, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    if (p.endsWith('/messages/wait')) {
      const after = Number(url.searchParams.get('last_id')) || 0;
      const fresh = room.updates.filter((u) => Number(u.id) > after);
      if (!fresh.length) await new Promise((resolve) => setTimeout(resolve, 1200));
      // As execution answers: the update itself is left out of `messages`, but `last_id` moves past it.
      return route.fulfill({ json: { status: 'SUCCESS', detail: { messages: [], last_id: room.lastId,
        updated: fresh.map((u) => (u.parent as { id: number }).id) } } }).catch(() => {});
    }
    if (p.endsWith('/messages')) {
      // The updates ride along too, as an older execution sent them: the app must still not draw them.
      return route.fulfill({ json: { status: 'SUCCESS', detail: { messages: [...messages(room), ...room.updates], last_id: room.lastId } } });
    }
    if (p.endsWith('/reactions')) {
      const id = Number(url.searchParams.get('message_id'));
      const per = room.reactions[id] ?? {};
      return route.fulfill({ json: { status: 'SUCCESS', detail: { ...counts(room, id), reactors: per } } });
    }
    const body = req.postDataJSON() as { message_id: number; reaction: string };
    room.calls.push(`${p.endsWith('/unreact') ? 'unreact' : 'react'} ${body.message_id} ${body.reaction}`);
    const per = (room.reactions[body.message_id] ??= {});
    const people = (per[body.reaction] ??= []);
    if (p.endsWith('/unreact')) per[body.reaction] = people.filter((x) => x.actor_id !== ME.actor_id);
    else if (!people.some((x) => x.actor_id === ME.actor_id)) people.push(ME);
    return route.fulfill({ json: { status: 'SUCCESS', detail: { ...counts(room, body.message_id), reactors: per } } });
  });
}

async function open(page: Page, room: Room): Promise<string[]> {
  await quietHotReload(page);
  await mockHome(page, 'admin');
  await mockHealth(page);
  await mockBible(page);
  await mockFamilyPages(page);
  await serve(page, room);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/family');
  await page.getByRole('list', { name: 'Conversations' }).getByText('Camping trip planning').first().click();
  await expect(page.getByTestId('chat-feed')).toContainText('Who is picking up the groceries?');
  return errors;
}

/** The chips under one message, found by its text. */
function chipsOf(page: Page, text: string) {
  return page.getByTestId('chat-feed').locator('div.group', { hasText: text }).getByTestId('reaction-chip');
}

test.describe('chat reactions between two people', () => {
  test.describe.configure({ timeout: 90_000 });

  test('mine and theirs add up, arrive live, show who, and mine comes back off alone', async ({ page }, info) => {
    const room: Room = { reactions: {}, updates: [], lastId: 50, calls: [] };
    const errors = await open(page, room);
    const groceries = 'Who is picking up the groceries?';

    // I react: one chip, lit as mine.
    await page.getByTestId('chat-feed').getByTitle('Tap to react').filter({ hasText: groceries }).click();
    await page.getByTestId('reaction-bar').getByRole('button', { name: 'React 👍' }).click();
    const thumbs = chipsOf(page, groceries).filter({ hasText: '👍' });
    await expect(thumbs).toHaveAttribute('aria-pressed', 'true');
    await expect(thumbs).toHaveAccessibleName('👍 1, including you');
    expect(room.calls).toEqual(['react 5 👍']);

    // Robin uses the same emoji on another phone: it reaches me through the long poll, as a second, not a replacement.
    otherReacts(room, ROBIN, 5, '👍');
    await expect(thumbs).toHaveAccessibleName('👍 2, including you', { timeout: 8000 });
    // And a reaction on a message I never touched shows up for me too.
    otherReacts(room, ROBIN, 6, '❤️');
    const heart = chipsOf(page, 'I will, on my way home').filter({ hasText: '❤️' });
    await expect(heart).toHaveAccessibleName('❤️ 1', { timeout: 8000 });
    await expect(heart).toHaveAttribute('aria-pressed', 'false');
    // Reaction updates are never drawn as lines of their own.
    await expect(page.getByTestId('chat-feed').locator('p', { hasText: /👍|❤️/ })).toHaveCount(0);

    // Holding a chip shows who reacted.
    await thumbs.click({ button: 'right' });
    const people = page.getByTestId('reaction-people');
    await expect(people).toContainText('You, Robin');
    // Opened next to the composer, it is brought on screen rather than left under it.
    await expect(people).toBeInViewport({ ratio: 1 });
    await settle(page);
    const found = await screenFindings(page, page.getByTestId('chat-feed'), 'who reacted');
    await shot(page, info, 'chat-reactions-who');
    await people.getByRole('button', { name: 'Close who reacted' }).click();
    await expect(people).toHaveCount(0);

    // Tapping my chip takes mine back; Robin's stays.
    await thumbs.click();
    await expect(thumbs).toHaveAccessibleName('👍 1');
    await expect(thumbs).toHaveAttribute('aria-pressed', 'false');
    expect(room.calls).toEqual(['react 5 👍', 'unreact 5 👍']);

    // Robin takes theirs back too: the chip goes for everyone.
    otherReacts(room, ROBIN, 5, '👍', false);
    await expect(chipsOf(page, groceries)).toHaveCount(0, { timeout: 8000 });

    found.push(...await screenFindings(page, page.getByTestId('chat-feed'), 'reactions'));
    expect(found, found.join('\n')).toEqual([]);
    expect(errors, errors.join(' | ')).toEqual([]);
  });
});
