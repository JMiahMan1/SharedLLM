import { execFileSync } from 'child_process';
import path from 'path';
import { describe, expect, it } from 'vitest';

const UI = path.resolve(__dirname, '../..');

describe('theme-remap.css', () => {
  // A color class added without regenerating would show the original dark
  // purple look under every other theme, which only a screenshot would catch.
  it('is up to date with the color classes the source uses', () => {
    expect(() =>
      execFileSync(process.execPath, ['scripts/gen-theme-css.mjs', '--check'], { cwd: UI, stdio: 'pipe' }),
    ).not.toThrow();
  });
});
