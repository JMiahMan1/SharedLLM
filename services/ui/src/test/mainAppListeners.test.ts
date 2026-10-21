import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync('src/main.tsx', 'utf8');

describe('app lifecycle listeners', () => {
  it('removes only the listeners it registered', () => {
    // BUG-67: removeAllListeners() here stripped every Capacitor listener in
    // the app (push, deep links, media) whenever this component cleaned up.
    expect(source).not.toContain('removeAllListeners');
    expect(source).toContain('handle.remove()');
    expect(source).toContain('disposed = true');
  });
});
