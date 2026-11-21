import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { CapacitorConfig } from '@capacitor/cli';
import { playerUrl } from '../lib/privateLinkPreview';

// Loaded by path, not a static import: capacitor.config.ts is outside the app's
// tsconfig and sets SystemBars options the CLI's types do not list yet.
const configPath = resolve(__dirname, '../../capacitor.config.ts');
const javaDir = resolve(__dirname, '../../android/app/src/main/java/com/jarvisos/app');

/** The host strings listed in PlayerFrameRules.PLAYER_HOSTS. */
function nativePlayerHosts(): string[] {
  const source = readFileSync(resolve(javaDir, 'PlayerFrameRules.java'), 'utf8');
  const block = /PLAYER_HOSTS\s*=[\s\S]*?\)\)\);/.exec(source);
  if (!block) throw new Error('PLAYER_HOSTS not found in PlayerFrameRules.java');
  return [...block[0].matchAll(/"([^"]+)"/g)].map((m) => m[1]).sort();
}

describe('Capacitor navigation', () => {
  it('lists no allowNavigation host, so no outside origin gets the native bridge or the main frame', async () => {
    const config = ((await import(/* @vite-ignore */ configPath)) as { default: CapacitorConfig }).default;
    expect(config.server).toBeDefined();
    expect(config.server && 'allowNavigation' in config.server).toBe(false);
  });

  it('keeps in the WebView exactly the hosts the chat players load from', () => {
    const webHosts = [
      new URL(playerUrl({ provider: 'youtube', id: 'dQw4w9WgXcQ' })).hostname,
      new URL(playerUrl({ provider: 'vimeo', id: '76979871' })).hostname,
    ].sort();
    expect(nativePlayerHosts()).toEqual(webHosts);
  });

  it('installs the player-frame client on the bridge', () => {
    const main = readFileSync(resolve(javaDir, 'MainActivity.java'), 'utf8');
    expect(main).toMatch(/getBridge\(\)\.setWebViewClient\(new PlayerFrameWebViewClient\(getBridge\(\)\)\)/);
    const client = readFileSync(resolve(javaDir, 'PlayerFrameWebViewClient.java'), 'utf8');
    // Only a subframe can be kept; the main frame is always Capacitor's.
    expect(client).toMatch(/PlayerFrameRules\.keepInFrame\(request\.isForMainFrame\(\)/);
  });
});
