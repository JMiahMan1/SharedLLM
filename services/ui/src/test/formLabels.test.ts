import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const SRC = path.resolve(__dirname, '..');

function walkTsx(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === '__tests__') continue;
      walkTsx(full, out);
    } else if (entry.name.endsWith('.tsx') && !entry.name.includes('.test.')) {
      out.push(full);
    }
  }
  return out;
}

interface JsxTag {
  start: number;
  end: number;
  tag: string;
}

function findJsxTags(text: string, tagNames: string[]): JsxTag[] {
  const results: JsxTag[] = [];
  const pattern = new RegExp(`<(${tagNames.join('|')})\\b`, 'g');
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text)) !== null) {
    let i = m.index + m[0].length;
    let brace = 0;
    let quote: string | null = null;
    while (i < text.length) {
      const ch = text[i];
      if (quote) {
        if (ch === '\\' && i + 1 < text.length) {
          i += 2;
          continue;
        }
        if (ch === quote) quote = null;
      } else if (ch === '"' || ch === "'" || ch === '`') {
        quote = ch;
      } else if (ch === '{') {
        brace += 1;
      } else if (ch === '}') {
        brace = Math.max(0, brace - 1);
      } else if (ch === '>' && brace === 0) {
        results.push({ start: m.index, end: i + 1, tag: text.slice(m.index, i + 1) });
        break;
      }
      i += 1;
    }
  }
  return results;
}

describe('form label and control accessibility', () => {
  const files = walkTsx(SRC);

  it('associates every <label> with a single form control (htmlFor or wrapping)', () => {
    const badLabels: string[] = [];
    const labelBlock = /<label\b[\s\S]*?<\/label>/g;
    for (const file of files) {
      const text = fs.readFileSync(file, 'utf8');
      const rel = path.relative(SRC, file);
      let m: RegExpExecArray | null;
      while ((m = labelBlock.exec(text)) !== null) {
        const block = m[0];
        const openTag = findJsxTags(block, ['label'])[0]?.tag ?? '';
        if (openTag.includes('htmlFor=')) continue;
        const body = block.slice(openTag.length, -'</label>'.length);
        const controls = findJsxTags(body, ['input', 'select', 'textarea']);
        if (controls.length === 1) continue;
        if (body.includes('{children}') && path.basename(file) === 'HealthProfilePanel.tsx') continue;
        const line = text.slice(0, m.index).split('\n').length;
        badLabels.push(`${rel}:${line} (controls=${controls.length})`);
      }
    }
    expect(badLabels).toEqual([]);
  });

  it('gives every visible <input>, <select> and <textarea> an accessible name', () => {
    const unlabelled: string[] = [];
    const containerBlock = /<(label|Field)\b[\s\S]*?<\/(label|Field)>/g;
    for (const file of files) {
      const text = fs.readFileSync(file, 'utf8');
      const rel = path.relative(SRC, file);
      const htmlForIds = new Set(
        Array.from(text.matchAll(/htmlFor=["']([^"']+)["']/g), (m) => m[1]),
      );
      const ranges: Array<[number, number]> = [];
      let m: RegExpExecArray | null;
      while ((m = containerBlock.exec(text)) !== null) {
        ranges.push([m.index, m.index + m[0].length]);
      }
      for (const { start, end, tag } of findJsxTags(text, ['input', 'select', 'textarea'])) {
        if (tag.includes('type="hidden"') || /\bhidden\b/.test(tag)) continue;
        if (tag.includes('className="hidden"') || tag.includes('sr-only')) continue;
        if (ranges.some(([s, e]) => s <= start && end <= e)) continue;
        if (/\b(aria-label|aria-labelledby|title)=/.test(tag)) continue;
        const idMatch = tag.match(/\bid=["']([^"']+)["']/);
        if (idMatch && htmlForIds.has(idMatch[1])) continue;
        if (/\bid=\{/.test(tag) && text.includes('htmlFor={')) continue;
        const line = text.slice(0, start).split('\n').length;
        unlabelled.push(`${rel}:${line}`);
      }
    }
    expect(unlabelled).toEqual([]);
  });
});
