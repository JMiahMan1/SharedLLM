/**
 * The private chat's backup file in the app: export it, open it with the chat
 * password on this device, put it back into Jarvis, or save it as a page that
 * opens only with the password.
 *
 * Opening and decrypting are lib/privateChatBackupCore.js (shared verbatim
 * with the saved page). This file adds what only the app does:
 *
 * - **Restore** (`restoreBackup`): after the device has opened the file and
 *   checked every chunk, it sends the chat's records back exactly as they were
 *   in it: the registry record (public key material the server already keeps
 *   for any chat), sealed messages, gallery entries, edits, reactions and
 *   Beloved records, and the encrypted blobs. Nothing is decrypted to be sent;
 *   the server refuses anything that is not sealed (services/gateway/private_chat_export.py).
 * - **The encrypted page** (`buildKeepsakePage`): one HTML file holding the
 *   backup as it is (still encrypted) and the decryptor, which asks for the
 *   password. Saved, mailed or copied anywhere, it is never readable without it.
 *
 * There is deliberately no "save as a readable page": the chat's rule is that a
 * copy is never readable without the password (docs/PRIVATE_CHAT_BACKUP.md).
 */
import coreSource from './privateChatBackupCore.js?raw';
import pageSource from './privateChatKeepsakePage.js?raw';
import { BACKUP_SUFFIX, belongsTo, BackupError, type OpenedBackup } from './privateChatBackupCore.js';
import { blobToBase64 } from './base64';

export { BACKUP_SUFFIX, BackupError };

/** What a restore call returns per kind. */
export interface RestoreCounts {
  added: number;
  same: number;
  kept: number;
}

/** The restore routes (services/api.ts), so this file has no network code. */
export interface RestoreClient {
  begin(registry: Record<string, unknown>, merge: boolean): Promise<{ token: string; created: boolean }>;
  have(token: string): Promise<{ blobs: string[] }>;
  putBlob(token: string, id: string, data: Blob): Promise<{ result: string }>;
  records(token: string, batch: RestoreBatch): Promise<{ result: Record<string, RestoreCounts> }>;
  finish(token: string): Promise<{ counts: Record<string, number> }>;
}

export interface RestoreBatch {
  messages?: unknown[];
  gallery?: unknown[];
  marks?: unknown[];
  beloved?: unknown[];
  read?: Record<string, number>;
  floor?: number;
}

/** What a backup will put back: the registry record, the blobs and the sealed records, read from the opened file. */
export interface RestorePlan {
  token: string;
  registry: Record<string, unknown>;
  blobs: Array<[string, Blob]>;
  records: Required<Pick<RestoreBatch, 'messages' | 'gallery' | 'marks' | 'beloved'>>;
  read: Record<string, number>;
  floor: number;
}

/** Records per restore call, and the most bytes of sealed text in one (the server takes 2000 records). */
const BATCH_RECORDS = 400;
const BATCH_BYTES = 2 * 1024 * 1024;

async function json(blob: Blob | undefined): Promise<Record<string, unknown> | null> {
  if (!blob) return null;
  try {
    const value = JSON.parse(await blob.text()) as unknown;
    return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** Read what a restore sends out of an opened backup. Only this chat's files; the Talk migration exports stay behind. */
export async function restorePlan(opened: OpenedBackup): Promise<RestorePlan> {
  const { files, token } = opened;
  const registry = await json(files.get(`chats/${token}.json`));
  if (!registry || registry.token !== token) throw new BackupError('The backup has no record of its chat.');
  const plan: RestorePlan = { token, registry, blobs: [], records: { messages: [], gallery: [], marks: [], beloved: [] }, read: {}, floor: 0 };
  for (const [path, blob] of files) {
    if (!belongsTo(path, token)) throw new BackupError(`The backup holds a file of another chat (${path}).`, 'tampered');
    const parts = path.split('/');
    if (parts[0] === 'blobs' && parts.length === 3 && /^[0-9a-f]{32}$/.test(parts[2])) plan.blobs.push([parts[2], blob]);
    else if (parts[0] === 'messages' && parts[2] === 'm' && parts.length === 4) {
      const record = await json(blob);
      if (record) plan.records.messages.push(record);
    } else if (parts[0] === 'messages' && parts[2] === 'read.json') {
      const read = await json(blob);
      for (const [who, upto] of Object.entries(read ?? {})) if (typeof upto === 'number') plan.read[who] = upto;
    } else if (parts[0] === 'messages' && parts[2] === 'meta.json') {
      const meta = await json(blob);
      plan.floor = typeof meta?.floor === 'number' ? meta.floor : 0;
    } else if (parts[0] === 'gallery' && parts.length === 3) {
      const entry = await json(blob);
      if (entry) plan.records.gallery.push(entry);
    } else if (parts[0] === 'marks' && parts.length === 3) {
      const mark = await json(blob);
      if (mark) plan.records.marks.push(mark);
    } else if (parts[0] === 'beloved' && parts.length === 4) {
      const record = await json(blob);
      if (record) plan.records.beloved.push(record);
    }
  }
  plan.records.messages.sort((a, b) => Number((a as { id?: number }).id) - Number((b as { id?: number }).id));
  return plan;
}

/** The batches a restore sends, each well under the server's limits. */
export function restoreBatches(plan: RestorePlan): RestoreBatch[] {
  const batches: RestoreBatch[] = [];
  let current: RestoreBatch = {};
  let count = 0;
  let size = 0;
  const kinds = ['messages', 'gallery', 'marks', 'beloved'] as const;
  for (const kind of kinds) {
    for (const record of plan.records[kind]) {
      const bytes = JSON.stringify(record).length;
      if (count && (count >= BATCH_RECORDS || size + bytes > BATCH_BYTES)) {
        batches.push(current);
        current = {};
        count = 0;
        size = 0;
      }
      (current[kind] ??= []).push(record);
      count += 1;
      size += bytes;
    }
  }
  // Read markers and the id floor go last, after every message they refer to is in.
  current.read = plan.read;
  current.floor = plan.floor;
  batches.push(current);
  return batches;
}

export interface RestoreProgress {
  step: 'start' | 'pictures' | 'messages' | 'done';
  done: number;
  total: number;
}

/** Put an opened backup back into Jarvis. `merge` once the person agreed to merge into a chat that has data. */
export async function restoreBackup(
  opened: OpenedBackup,
  client: RestoreClient,
  { merge = false, onProgress }: { merge?: boolean; onProgress?: (p: RestoreProgress) => void } = {},
): Promise<{ created: boolean; counts: Record<string, number>; result: Record<string, RestoreCounts> }> {
  const plan = await restorePlan(opened);
  onProgress?.({ step: 'start', done: 0, total: 1 });
  const begun = await client.begin(plan.registry, merge);
  const have = new Set((await client.have(plan.token)).blobs);
  const missing = plan.blobs.filter(([id]) => !have.has(id));
  for (let i = 0; i < missing.length; i += 1) {
    await client.putBlob(plan.token, missing[i][0], missing[i][1]);
    onProgress?.({ step: 'pictures', done: i + 1, total: missing.length });
  }
  const batches = restoreBatches(plan);
  const result: Record<string, RestoreCounts> = {};
  for (let i = 0; i < batches.length; i += 1) {
    const out = await client.records(plan.token, batches[i]);
    for (const [kind, counts] of Object.entries(out.result)) {
      const sum = (result[kind] ??= { added: 0, same: 0, kept: 0 });
      sum.added += counts.added;
      sum.same += counts.same;
      sum.kept += counts.kept;
    }
    onProgress?.({ step: 'messages', done: i + 1, total: batches.length });
  }
  const finished = await client.finish(plan.token);
  onProgress?.({ step: 'done', done: 1, total: 1 });
  return { created: begun.created, counts: finished.counts, result };
}

// --- Files --------------------------------------------------------------------

/** `private-chat-<date>.jarvis-backup`: a date, never a name. */
export function backupFileName(when: Date = new Date(), suffix: string = BACKUP_SUFFIX): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `private-chat-${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}${suffix}`;
}

/** The page's own name for a backup file's name: `.html` in place of `.jarvis-backup`. */
export function pageFileName(name: string): string {
  return name.endsWith(BACKUP_SUFFIX) ? `${name.slice(0, -BACKUP_SUFFIX.length)}.html` : `${name}.html`;
}

const PAGE_DATA_ID = 'jarvis-backup';
const PAGE_DATA = new RegExp(`<script type="application/octet-stream" id="${PAGE_DATA_ID}">([A-Za-z0-9+/=\\s]+)</script>`);

/**
 * A file the person picked, as a backup: a `.jarvis-backup` as it is, or an
 * encrypted page saved earlier (the backup inside it, still encrypted).
 */
export async function backupFromFile(file: Blob): Promise<Blob> {
  const head = new Uint8Array(await file.slice(0, 15).arrayBuffer());
  if (String.fromCharCode(...head).toLowerCase().startsWith('<!doctype html')) {
    const match = PAGE_DATA.exec(await file.text());
    if (!match) throw new BackupError('This page does not hold a private chat backup.');
    const binary = atob(match[1].replace(/\s+/g, ''));
    const out = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
    return new Blob([out], { type: 'application/octet-stream' });
  }
  return file;
}

function inlineScript(source: string): string {
  // The core ends with its export list; inside the page everything is in one script's scope.
  const code = source.replace(/^export \{[\s\S]*?\};?\s*$/m, '');
  if (/<\/script/i.test(code) || /^\s*(export|import)\s/m.test(code)) throw new Error('The keepsake page script cannot be inlined.');
  return code;
}

/** The page's script: the decryptor (the app's own) and the viewer. Exposed for tests. */
export function keepsakePageScript(): string {
  return `${inlineScript(coreSource)}\n${inlineScript(pageSource)}`;
}

const PAGE_STYLE = `
:root{color-scheme:dark;--bg:#020617;--card:#0f172a;--line:#1e293b;--text:#e2e8f0;--muted:#94a3b8;--rose:#e11d48;--rose-soft:#fecdd3}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px calc(32px + env(safe-area-inset-bottom))}
h1{font-size:22px;margin:8px 0 4px}h2{font-size:17px;margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:6px}
p{margin:6px 0}.muted{color:var(--muted);font-size:14px}.lock{max-width:420px;margin:12vh auto 0;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}
label{display:block;font-size:13px;font-weight:600;color:var(--muted);margin:12px 0 4px}
#keepsake-password{width:100%;min-height:44px;font-size:16px;padding:8px 76px 8px 12px;border-radius:12px;border:1px solid #334155;background:#020617;color:var(--text)}
.pwbox{position:relative}.pwbox .eye{position:absolute;top:0;right:0;height:100%;min-width:64px;padding:0 12px;background:transparent;color:var(--text);border-radius:0 12px 12px 0;font-size:14px}
.pwbox .eye:focus-visible{outline:2px solid var(--rose-soft);outline-offset:-2px}
button{min-height:44px;min-width:44px;padding:8px 16px;border-radius:12px;border:0;background:var(--rose);color:#fff;font-weight:600;font-size:15px;cursor:pointer}
button.quiet{background:transparent;border:1px solid #334155;color:var(--text)}
.error{color:#fda4af;font-size:14px}.day{text-align:center;color:var(--muted);font-size:13px;margin:18px 0 6px}
.msg{max-width:85%;margin:6px 0;padding:8px 12px;border-radius:16px;background:var(--card);border:1px solid var(--line);overflow-wrap:anywhere}
.msg.right{margin-left:auto;background:#4c0519;border-color:#881337}.who{font-size:12px;color:var(--muted)}
.msg img,.pic img{display:block;max-width:100%;border-radius:10px;margin-top:6px}.msg audio,.pic audio{display:block;width:100%;max-width:100%;margin-top:6px}.chips{font-size:14px;margin-top:4px}
.gone{font-style:italic;color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px}
.pic{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px;font-size:13px}
.love{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:8px 0}
.bar{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}
`;

/**
 * One self-contained HTML file: the backup (still encrypted, base64) and the
 * decryptor, which asks for the chat password. Its content security policy
 * lets it load nothing and send nothing.
 */
export async function buildKeepsakePage(backup: Blob, title = 'Private chat backup'): Promise<Blob> {
  const data = await blobToBase64(backup);
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src blob:; media-src blob:">
<meta name="robots" content="noindex"><title>${title.replace(/[<>&"]/g, '')}</title><style>${PAGE_STYLE}</style></head>
<body><main id="keepsake"><div class="lock"><h1>Private chat backup</h1>
<p class="muted">Encrypted. Enter the chat password to read it on this device. Nothing is sent anywhere.</p>
<noscript><p class="error">Turn on JavaScript to open it.</p></noscript></div></main>
<script type="application/octet-stream" id="${PAGE_DATA_ID}">${data}</script>
<script>${keepsakePageScript()}</script></body></html>
`;
  return new Blob([html], { type: 'text/html' });
}
