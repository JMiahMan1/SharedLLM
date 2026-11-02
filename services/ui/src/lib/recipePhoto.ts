import { getServerOrigin } from './serverUrl';
import { currentMediaToken, ensureMediaToken } from './mediaToken';
import { blobToBase64 } from './base64';
import { attachmentName } from './noteAttachments';

/**
 * A recipe photo for an `<img>`: a cookbook recipe's own (by its note path) or
 * a search result's web address, both through the gateway (``file`` picks
 * another picture the recipe links, such as the card it was read from), signed with the
 * media token like every other picture (§7.4). Null until a token is held;
 * callers re-render with `useMediaToken()`.
 */
export function recipePhotoUrl(source: { path?: string | null; url?: string | null; file?: string | null }): string | null {
  const query = source.path
    ? `path=${encodeURIComponent(source.path)}${source.file ? `&file=${encodeURIComponent(source.file)}` : ''}`
    : source.url
      ? `url=${encodeURIComponent(source.url)}`
      : '';
  if (!query) return null;
  const mt = currentMediaToken();
  if (!mt) {
    void ensureMediaToken();
    return null;
  }
  const base = getServerOrigin().replace(/\/+$/, '');
  return `${base}/api/media/recipe-image?${query}&user=${encodeURIComponent(mt.user)}&mt=${encodeURIComponent(mt.token)}`;
}

/**
 * A phone photo made small enough to send: at most `edge` px on its long side,
 * as a JPEG (base64, no data: prefix). A picture this browser cannot draw
 * (HEIC on some phones) is sent as it is.
 */
export async function shrinkPhoto(file: Blob, edge = 1600): Promise<{ data: string; filename: string }> {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, edge / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('no canvas');
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close?.();
    const url = canvas.toDataURL('image/jpeg', 0.85);
    return { data: url.slice(url.indexOf(',') + 1), filename: 'photo.jpg' };
  } catch {
    const name = file instanceof File && file.name ? file.name : 'photo.jpg';
    return { data: await blobToBase64(file), filename: name };
  }
}

export interface RecipePicture {
  link: string;
  label: string;
}

/**
 * Every picture a recipe shows, cover first: its `Image:` photo, then each
 * picture linked on a line of its own (`![Photo by mom](.attachments.42/x.jpg)`).
 * A recipe read from a photo calls that first picture its original.
 */
export function recipePictures(cover: string | null | undefined, lines: string[], photoImport = false): RecipePicture[] {
  const out: RecipePicture[] = [];
  const seen = new Set<string>();
  const add = (link: string, label: string) => {
    if (!link || seen.has(link)) return;
    seen.add(link);
    out.push({ link, label });
  };
  const linked = lines
    .map((line) => /^!?\[([^\]\n]*)\]\(<?([^)>\s]+)>?\)/.exec(line))
    .filter((m): m is RegExpExecArray => Boolean(m))
    .map((m, index) => ({
      link: m[2],
      label: /^photo by /i.test(m[1]) ? m[1] : photoImport && index === 0 ? 'Original recipe' : attachmentName(m[2]),
    }));
  if (cover) add(cover, linked.find((p) => p.link === cover)?.label ?? 'Photo');
  for (const picture of linked) add(picture.link, picture.label);
  return out;
}
