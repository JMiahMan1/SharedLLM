/**
 * Household rule: every password field lets the person see what they typed
 * before sending it. A masked input belongs inside components/ui/PasswordField
 * (the eye button); one with a toggle of its own says so on its line with
 * `// password-toggle: <reason>`. The two generated offline pages cannot use a
 * React component, so they are checked for their own show/hide button.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';
import { buildEncryptedPage } from '../lib/accountExport/page';
import { keepsakePageScript } from '../lib/privateChatBackupFile';

const SRC = join(__dirname, '..');
const COMPONENT = join('components', 'ui', 'PasswordField.tsx');
const GENERATED = [join('lib', 'privateChatKeepsakePage.js'), join('lib', 'accountExport', 'page.ts')];
const ALLOW = /\/\/\s*password-toggle:\s*\S/;

/** A line that makes an input masked: type="password", type={... 'password' ...}, type: 'password', x.type = 'password'. */
const MASKED = [
  /\btype\s*=(?!=)\s*["']password["']/,
  /\btype\s*=(?!=)\s*\{[^}\n]*["']password["']/,
  /\btype\s*:\s*["']password["']/,
  /\.type\s*=(?!=)[^;\n]*["']password["']/,
  /setAttribute\(\s*["']type["']\s*,\s*["']password["']/,
];

function maskedLine(line: string): boolean {
  return MASKED.some((re) => re.test(line));
}

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (name === '__tests__' || name === '__mocks__' || name === 'node_modules') continue;
      if (path === join(SRC, 'test')) continue;
      sources(path, out);
    } else if (/\.(tsx?|jsx?)$/.test(name) && !/\.(test|spec)\.[tj]sx?$/.test(name) && !name.endsWith('.d.ts')) {
      out.push(path);
    }
  }
  return out;
}

describe('password fields', () => {
  it('recognises every way of masking an input, and not comparisons or type unions', () => {
    expect(maskedLine('<input type="password" />')).toBe(true);
    expect(maskedLine("<input type={'password'} />")).toBe(true);
    expect(maskedLine("type={show ? 'text' : 'password'}")).toBe(true);
    expect(maskedLine("el('input', { type: 'password', id: 'x' })")).toBe(true);
    expect(maskedLine("input.type = shown ? 'text' : 'password';")).toBe(true);
    expect(maskedLine("node.setAttribute('type', 'password')")).toBe(true);
    expect(maskedLine("if (f.type === 'password') return;")).toBe(false);
    expect(maskedLine("type: 'text' | 'password' | 'select';")).toBe(false);
    expect(maskedLine("type Step = 'pick' | 'password';")).toBe(false);
  });

  it('has no masked input outside PasswordField without a password-toggle note', () => {
    const offenders: string[] = [];
    for (const file of sources(SRC)) {
      const rel = relative(SRC, file);
      if (rel === COMPONENT || GENERATED.includes(rel)) continue;
      const lines = readFileSync(file, 'utf8').split('\n');
      lines.forEach((line, i) => {
        if (!maskedLine(line)) return;
        if (ALLOW.test(line) || (i > 0 && ALLOW.test(lines[i - 1]))) return;
        offenders.push(`src${sep}${rel}:${i + 1}: ${line.trim()}`);
      });
    }
    expect(offenders, `Use components/ui/PasswordField (or add "// password-toggle: <reason>" if the field has its own eye):\n${offenders.join('\n')}`).toEqual([]);
  });

  it('names every PasswordField: an aria-label, or an id that a label points at', () => {
    const unnamed: string[] = [];
    for (const file of sources(SRC)) {
      const rel = relative(SRC, file);
      if (rel === COMPONENT) continue;
      const text = readFileSync(file, 'utf8');
      for (const m of text.matchAll(/<PasswordField\b[\s\S]*?\/>/g)) {
        const tag = m[0];
        if (/\baria-label(ledby)?=/.test(tag)) continue;
        const id = tag.match(/\bid=(\{[^}]*\}|"[^"]*")/);
        if (id && text.includes(`htmlFor=${id[1]}`)) continue;
        unnamed.push(`src${sep}${rel}:${text.slice(0, m.index).split('\n').length}`);
      }
    }
    expect(unnamed, `PasswordField without aria-label or a matching htmlFor/id:\n${unnamed.join('\n')}`).toEqual([]);
  });

  it('the shared PasswordField still masks by default and has the eye', () => {
    const source = readFileSync(join(SRC, COMPONENT), 'utf8');
    expect(source).toMatch(/type=\{visible \? 'text' : 'password'\}/);
    expect(source).toContain("'Show password'");
    expect(source).toContain('aria-pressed');
  });

  it('the account export page has an offline show/hide button', () => {
    const html = buildEncryptedPage(new Uint8Array([1, 2, 3]), 'Export');
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const input = doc.getElementById('pw') as HTMLInputElement;
    const eye = doc.getElementById('pw-toggle') as HTMLButtonElement;
    expect(input.getAttribute('type')).toBe('password');
    expect(eye.tagName).toBe('BUTTON');
    expect(eye.getAttribute('type')).toBe('button');
    expect(eye.getAttribute('aria-label')).toBe('Show password');
    expect(eye.getAttribute('aria-pressed')).toBe('false');
    expect(eye.getAttribute('aria-controls')).toBe('pw');
    expect(html).not.toMatch(/<(script|link|img)[^>]+(src|href)=["']?https?:/i);

    // Run the page's own script against its own markup: the eye shows and hides what was typed.
    document.body.innerHTML = doc.body.innerHTML;
    const scripts = [...doc.querySelectorAll('script')].filter((s) => !s.getAttribute('type'));
    new Function(scripts[scripts.length - 1].textContent ?? '')();
    const live = document.getElementById('pw') as HTMLInputElement;
    const button = document.getElementById('pw-toggle') as HTMLButtonElement;
    live.value = 'typed here';
    button.click();
    expect(live.type).toBe('text');
    expect(live.value).toBe('typed here');
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(button.getAttribute('aria-label')).toBe('Hide password');
    button.click();
    expect(live.type).toBe('password');
    expect(button.getAttribute('aria-pressed')).toBe('false');
    document.body.innerHTML = '';
  });

  it('the private chat keepsake page has an offline show/hide button', () => {
    document.body.innerHTML = '<main id="keepsake"></main><script type="application/octet-stream" id="jarvis-backup"></script>';
    new Function(keepsakePageScript())();
    const input = document.getElementById('keepsake-password') as HTMLInputElement;
    const eye = document.getElementById('keepsake-password-toggle') as HTMLButtonElement;
    expect(input.type).toBe('password');
    expect(eye.getAttribute('type')).toBe('button');
    expect(eye.getAttribute('aria-label')).toBe('Show password');
    expect(eye.getAttribute('aria-pressed')).toBe('false');
    input.value = 'typed here';
    eye.click();
    expect(input.type).toBe('text');
    expect(input.value).toBe('typed here');
    expect(eye.getAttribute('aria-pressed')).toBe('true');
    expect(eye.getAttribute('aria-label')).toBe('Hide password');
    eye.click();
    expect(input.type).toBe('password');
    window.dispatchEvent(new Event('pagehide'));
    document.body.innerHTML = '';
  });
});
