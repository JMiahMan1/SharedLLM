import { PAGE_TONES, toneOf } from '../components/bible/readingPage';

/**
 * A verse as a square image for sharing: the text set large on the reader's
 * page tone, the reference beneath it. Drawn on a canvas so it needs no server
 * and works offline; wrapped to fit, shrinking the type for a long verse.
 */
export async function renderVerseImage(
  text: string,
  reference: string,
  version: string,
  page?: string,
  size = 1080,
): Promise<Blob> {
  const tone = PAGE_TONES[toneOf(page)];
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('This device cannot draw images.');

  ctx.fillStyle = tone.paper;
  ctx.fillRect(0, 0, size, size);
  ctx.fillStyle = tone.number;
  ctx.fillRect(size * 0.08, size * 0.1, size * 0.08, 6);

  const margin = size * 0.08;
  const width = size - margin * 2;
  let fontSize = 64;
  let lines: string[] = [];
  for (; fontSize >= 30; fontSize -= 4) {
    ctx.font = `${fontSize}px Georgia, 'Times New Roman', serif`;
    lines = wrap(ctx, `“${text.trim()}”`, width);
    if (lines.length * fontSize * 1.4 <= size * 0.62) break;
  }
  const lineHeight = fontSize * 1.4;
  let y = size * 0.17 + fontSize;
  ctx.fillStyle = tone.ink;
  ctx.textBaseline = 'alphabetic';
  for (const line of lines) {
    ctx.fillText(line, margin, y);
    y += lineHeight;
  }

  ctx.font = `600 ${Math.round(size * 0.04)}px system-ui, -apple-system, sans-serif`;
  ctx.fillStyle = tone.number;
  ctx.fillText(`${reference}${version ? ` · ${version.toUpperCase()}` : ''}`, margin, size - margin);

  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('The image could not be made.'))), 'image/png'),
  );
}

function wrap(ctx: CanvasRenderingContext2D, text: string, width: number): string[] {
  const out: string[] = [];
  let line = '';
  for (const word of text.split(/\s+/)) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > width && line) {
      out.push(line);
      line = word;
    } else {
      line = next;
    }
  }
  if (line) out.push(line);
  return out;
}
