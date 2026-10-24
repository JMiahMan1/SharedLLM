/**
 * Note attachments, stored as Nextcloud Notes stores them.
 *
 * A picture, drawing, voice note or file is a file in a hidden folder beside
 * the note, named after the note's Nextcloud file id, and the markdown links
 * to it relatively: `![photo.jpg](.attachments.4242/photo.jpg)` for an image,
 * `[voice.webm](.attachments.4242/voice.webm)` for anything else. Nextcloud
 * Notes and Jarvis show the same files; the note stays plain markdown.
 */

/** The largest attachment the server takes (execution's ATTACHMENT_MAX_BYTES). */
export const ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;

/** A relative attachment link, `.attachments.<id>/<file>`. */
const LINK_TARGET = String.raw`\.attachments\.\d{1,20}\/[^)\s>]+`;
const ATTACHMENT_ANY = new RegExp(String.raw`(!?)\[([^\]\n]*)\]\(<?(${LINK_TARGET})>?\)`, 'g');
/** A line that is one attachment and nothing else (the visual editor draws these). */
export const ATTACHMENT_LINE = new RegExp(String.raw`^!?\[[^\]\n]*\]\(<?${LINK_TARGET}>?\)$`);

export type AttachmentKind = 'image' | 'audio' | 'file';

export interface NoteAttachment {
  /** The link as written in the note, e.g. `.attachments.4242/photo.jpg`. */
  link: string;
  /** The file name, for people. */
  name: string;
  kind: AttachmentKind;
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|svg|heic|heif|avif)$/i;
const AUDIO_EXT = /\.(webm|ogg|oga|opus|mp3|m4a|aac|wav|flac|3gp|amr)$/i;

/** True for a link Jarvis treats as one of the note's attachments. */
export function isAttachmentLink(href: string | undefined | null): boolean {
  return Boolean(href && new RegExp(`^${LINK_TARGET}$`).test(href));
}

export function attachmentName(link: string): string {
  const raw = link.split('/').pop() ?? link;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/** What to show for a file: its type when known, else its extension. */
export function attachmentKind(nameOrLink: string, mime?: string): AttachmentKind {
  if (mime?.startsWith('image/') || IMAGE_EXT.test(nameOrLink)) return 'image';
  if (mime?.startsWith('audio/') || AUDIO_EXT.test(nameOrLink)) return 'audio';
  return 'file';
}

/** Every attachment a note links to, once each, in order. */
export function listAttachments(markdown: string): NoteAttachment[] {
  const seen = new Set<string>();
  const out: NoteAttachment[] = [];
  for (const match of markdown.matchAll(ATTACHMENT_ANY)) {
    const link = match[3];
    if (seen.has(link)) continue;
    seen.add(link);
    const name = attachmentName(link);
    out.push({ link, name, kind: match[1] === '!' ? 'image' : attachmentKind(name) });
  }
  return out;
}

/** The markdown line for a new attachment (an image is embedded, anything else linked). */
export function attachmentMarkdown(link: string, name: string, mime?: string): string {
  const label = name.replace(/[[\]\n]/g, '');
  return attachmentKind(name, mime) === 'image' ? `![${label}](${link})` : `[${label}](${link})`;
}

/** The note with one more attachment line at the end. */
export function appendAttachment(markdown: string, line: string): string {
  const trimmed = markdown.replace(/\s+$/, '');
  return trimmed ? `${trimmed}\n${line}` : line;
}

/**
 * The note without its links to one attachment: a line that is only that
 * link goes, a link inside other text leaves the text around it.
 */
export function removeAttachment(markdown: string, link: string): string {
  const lines = markdown.split('\n');
  const kept: string[] = [];
  for (const line of lines) {
    const without = line.replace(ATTACHMENT_ANY, (whole, _bang, _label, target: string) => (target === link ? '' : whole));
    if (without !== line && !without.trim()) continue;
    kept.push(without);
  }
  return kept.join('\n');
}

/** A file's bytes as base64, for the upload's JSON body. */
export async function blobToBase64(blob: Blob): Promise<string> {
  const buffer = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < buffer.length; i += chunk) {
    binary += String.fromCharCode(...buffer.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/** Base64 back to a Blob of the given type, to show it through an object URL. */
export function base64ToBlob(data: string, mime: string): Blob {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}
