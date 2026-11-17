import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { openBackup } from './privateChatBackupCore.js';
import {
  backupFileName,
  backupFromFile,
  buildKeepsakePage,
  keepsakePageScript,
  pageFileName,
  restoreBackup,
  restoreBatches,
  restorePlan,
  type RestoreBatch,
  type RestoreClient,
} from './privateChatBackupFile';

/** Backups the Python encrypter made of fake chats (see privateChatBackupCore.test.ts). */
const FIXTURE = JSON.parse(
  readFileSync(resolve(__dirname, '../../../../tests/unit/fixtures/private_chat_backup_files.json'), 'utf8'),
) as Record<'v1' | 'v2', { password: string; file: string; expect: { token: string; members: string[]; texts: string[]; blobs: string[] } }>;
const V2 = FIXTURE.v2;
const backup = () => new Blob([new Uint8Array(Buffer.from(V2.file, 'base64'))]);

afterEach(() => {
  document.body.replaceChildren();
});

describe('names', () => {
  it('carry a date and nothing else', () => {
    expect(backupFileName(new Date(2026, 10, 16))).toBe('private-chat-2026-11-16.jarvis-backup');
    expect(pageFileName('private-chat-2026-11-16.jarvis-backup')).toBe('private-chat-2026-11-16.html');
  });
});

describe('restoring', () => {
  it('sends back only what was in the file, sealed, and never a word of the chat', async () => {
    const opened = await openBackup(backup(), V2.password);
    const sent: { begin?: Record<string, unknown>; blobs: Record<string, Uint8Array>; batches: RestoreBatch[]; finished?: boolean } = { blobs: {}, batches: [] };
    const client: RestoreClient = {
      begin: async (registry) => {
        sent.begin = registry;
        return { token: String(registry.token), created: true };
      },
      have: async () => ({ blobs: [V2.expect.blobs[0]] }),
      putBlob: async (_token, id, data) => {
        sent.blobs[id] = new Uint8Array(await data.arrayBuffer());
        return { result: 'added' };
      },
      records: async (_token, batch) => {
        sent.batches.push(batch);
        return { result: { messages: { added: batch.messages?.length ?? 0, same: 0, kept: 0 } } };
      },
      finish: async () => {
        sent.finished = true;
        return { counts: { messages: 6 } };
      },
    };
    const out = await restoreBackup(opened, client);
    expect(out.created).toBe(true);
    expect(out.result.messages.added).toBe(6);
    expect(sent.finished).toBe(true);
    expect(sent.begin?.token).toBe(V2.expect.token);
    // The blob already on the server is not sent again; the rest go as they were in the file (ciphertext).
    expect(Object.keys(sent.blobs).sort()).toEqual(V2.expect.blobs.slice(1));
    for (const [id, data] of Object.entries(sent.blobs)) {
      expect([...data]).toEqual([...new Uint8Array(await opened.files.get(`blobs/${opened.token}/${id}`)!.arrayBuffer())]);
    }
    const wire = JSON.stringify([sent.begin, sent.batches]);
    for (const text of V2.expect.texts) expect(wire).not.toContain(text);
    expect(wire).not.toContain('You make every day better');
    // Read markers and the floor come last, after the messages.
    expect(sent.batches.at(-1)?.read).toEqual({ [V2.expect.members[0]]: 6, [V2.expect.members[1]]: 5 });
  });

  it('splits a large chat into batches the server accepts', async () => {
    const plan = await restorePlan(await openBackup(backup(), V2.password));
    const many = { ...plan, records: { ...plan.records, messages: Array.from({ length: 1000 }, (_, i) => ({ ...plan.records.messages[0] as object, id: i + 1 })) } };
    const batches = restoreBatches(many);
    expect(batches.length).toBeGreaterThan(2);
    for (const batch of batches) expect((batch.messages?.length ?? 0) + (batch.gallery?.length ?? 0) + (batch.beloved?.length ?? 0) + (batch.marks?.length ?? 0)).toBeLessThanOrEqual(400);
    expect(batches.flatMap((b) => b.messages ?? [])).toHaveLength(1000);
  });
});

describe('the encrypted page', () => {
  it('holds the backup still encrypted, loads nothing, and gives the same file back', async () => {
    const page = await buildKeepsakePage(backup());
    const html = await page.text();
    expect(html.startsWith('<!doctype html>')).toBe(true);
    expect(html).toContain("default-src 'none'");
    for (const text of [...V2.expect.texts, ...V2.expect.members, V2.password]) expect(html).not.toContain(text);
    expect(html).not.toMatch(/<script[^>]+src=/);
    expect(html).not.toMatch(/https?:\/\//);
    const again = await backupFromFile(page);
    expect(Buffer.from(await again.arrayBuffer()).equals(Buffer.from(V2.file, 'base64'))).toBe(true);
    expect(keepsakePageScript()).not.toMatch(/^\s*(export|import)\s/m);
  });

  async function runPage(): Promise<void> {
    const html = await (await buildKeepsakePage(backup())).text();
    const data = /<script type="application\/octet-stream" id="jarvis-backup">([^<]+)<\/script>/.exec(html)![1];
    document.body.innerHTML = `<main id="keepsake"></main><script type="application/octet-stream" id="jarvis-backup">${data}</script>`;
    new Function(keepsakePageScript())();
  }

  async function submit(password: string): Promise<void> {
    (document.getElementById('keepsake-password') as HTMLInputElement).value = password;
    document.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  }

  it("opens with the password, in the page's own decryptor, and shows the chat as text", async () => {
    await runPage();
    expect(document.body.textContent).not.toContain(V2.expect.texts[0]);
    await submit(V2.password);
    await expect.poll(() => document.getElementById('keepsake')!.textContent ?? '', { timeout: 15_000 }).toContain(V2.expect.texts[0]);
    const text = document.getElementById('keepsake')!.textContent!;
    expect(text).toContain('Dinner at seven, just us?');
    expect(text).toContain('edited');
    expect(text).toContain('Deleted');
    expect(text).toContain('You make every day better');
    expect(text).toContain('Our first date');
    expect(document.querySelectorAll('#keepsake img').length).toBeGreaterThan(0);
    // The password field was cleared before anything else happened.
    expect(document.getElementById('keepsake-password')).toBeNull();
  });

  it('refuses a wrong password and shows nothing', async () => {
    await runPage();
    await submit('not the password');
    await expect.poll(() => document.getElementById('keepsake-error')?.textContent ?? '', { timeout: 15_000 }).toContain('does not open');
    expect(document.body.textContent).not.toContain(V2.expect.texts[0]);
  });
});
