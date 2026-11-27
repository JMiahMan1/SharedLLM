import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, type HomeMocks } from '../fixtures/homeMocks';
import { expectLayoutSound, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Devices > Setup > Voice hold music, in every project (desktop, Pixel 7, a small phone, a tablet both ways
 * round, an iPhone). Hermetic: fixtures/homeMocks.ts answers the hub's setting; the clips are made up bytes.
 */

async function open(page: Page): Promise<{ mocks: HomeMocks; errors: string[] }> {
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/devices#devices-voice-hold-music');
  await expect(page.getByTestId('page-header')).toBeVisible();
  return { mocks, errors };
}

function sent(mocks: HomeMocks, path: string) {
  return mocks.posts.filter((p) => p.path === path).map((p) => p.body).at(-1) ?? `${path} not sent`;
}

const WAV = { name: 'our-theme.wav', mimeType: 'audio/wav', buffer: Buffer.from('RIFF\0\0\0\0WAVEfmt ') };

test.describe('Devices: voice hold music', () => {
  test('choose the tune, upload a clip, remove it, and change the lines', async ({ page }, info) => {
    const { mocks, errors } = await open(page);
    const panel = page.getByTestId('voice-hold-music');
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.getByRole('radio', { name: /Game-show thinking/ })).toBeChecked();
    await expect(panel.getByRole('radio', { name: /Upload your own/ })).toBeDisabled();
    await expectLayoutSound(page, info, 'devices-voice-hold-music');
    await expectTapTargets(panel);

    await panel.getByText('Built-in tune').click();
    await expect(panel.getByRole('radio', { name: /Built-in tune/ })).toBeChecked();
    await expect.poll(() => sent(mocks, '/api/homehub/voice/hold-music')).toEqual({ source: 'builtin' });

    // A file the hub would refuse by its name never leaves the page.
    await panel.getByTestId('hold-music-file').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hi') });
    await expect(panel.getByRole('alert')).toHaveText('Choose a wav, mp3, ogg or m4a file');
    expect(mocks.posts.some((p) => p.path.endsWith('/clip'))).toBe(false);
    // One it refuses for its length says why.
    await panel.getByTestId('hold-music-file').setInputFiles({ ...WAV, name: 'too-long.wav' });
    await expect(panel.getByRole('alert')).toContainText('longer than 60 s');

    await panel.getByTestId('hold-music-file').setInputFiles(WAV);
    await expect(panel.getByRole('radio', { name: /Upload your own/ })).toBeChecked();
    await expect(panel.getByTestId('hold-music-clip')).toContainText('our-theme.wav · 24.5 s');
    await expect(panel.getByRole('alert')).toHaveCount(0);
    await expectTapTargets(panel);
    await expectNoHorizontalOverflow(page);
    await panel.scrollIntoViewIfNeeded();
    await snap(page, info, 'devices-voice-hold-music-uploaded');

    await panel.getByRole('button', { name: 'Remove the uploaded clip' }).click();
    await expect(panel.getByRole('radio', { name: /Game-show thinking/ })).toBeChecked();
    await expect(panel.getByRole('button', { name: 'Upload a clip' })).toBeVisible();

    const box = panel.getByLabel('Hold lines');
    await box.fill('Cue the thinking music.\n\n  Let me mull that over.  ');
    await panel.getByRole('button', { name: 'Save lines' }).click();
    await expect.poll(() => sent(mocks, '/api/homehub/voice/hold-music'))
      .toEqual({ lines: ['Cue the thinking music.', 'Let me mull that over.'] });
    await expect(box).toHaveValue('Cue the thinking music.\nLet me mull that over.');
    await panel.getByRole('button', { name: 'Use the default lines' }).click();
    await expect.poll(() => sent(mocks, '/api/homehub/voice/hold-music')).toEqual({ lines: [] });
    await expect(box).toHaveValue('');
    await expectNoHorizontalOverflow(page);

    expect(errors, `page errors: ${errors.join(' | ')}`).toEqual([]);
    expect(mocks.unmocked, `unmocked: ${mocks.unmocked.join(', ')}`).toEqual([]);
  });
});
