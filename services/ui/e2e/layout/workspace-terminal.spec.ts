import { test, expect as baseExpect, type WebSocketRoute } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { expectNoHorizontalOverflow, snap } from '../fixtures/layoutChecks';

/**
 * A workspace's terminal on every form factor: it opens a socket to the
 * gateway with the user's token, shows the shell's prompt, sends keystrokes
 * and its size, and prints full-screen output where the program put it.
 * Hermetic: the gateway's terminal socket is answered by the test.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const WORKSPACE = {
  id: 'demo-ws', display_name: 'Demo', local_path: 'demo-ws', scope: 'user', sync_mode: 'local_git_authoritative',
  capabilities: ['read', 'write'], resolved_path: '/workspaces/demo-ws', available: true,
};

test('the workspace terminal connects, echoes and resizes', async ({ page }, info) => {
  const mocks = await mockHome(page, 'admin');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/api/workspaces', (route) => route.fulfill({ json: { status: 'SUCCESS', workspaces: [WORKSPACE] } }));
  await page.route('**/api/workspaces/sessions', (route) => route.fulfill({ json: { sessions: {} } }));
  // The IDE's chat tab reads these; nothing in them matters to the terminal.
  await page.route('**/api/settings', (route) => route.fulfill({ json: {} }));
  await page.route('**/api/ai/capabilities', (route) => route.fulfill({ json: {} }));
  await page.route('**/api/workspaces/files/list', (route) => route.fulfill({ json: { status: 'SUCCESS', entries: [] } }));

  const typed: string[] = [];
  const resizes: Array<{ width: number; height: number }> = [];
  let socketUrl = '';
  let socket: WebSocketRoute | null = null;
  await page.routeWebSocket(/\/api\/workspaces\/demo-ws\/terminal/, (ws) => {
    socket = ws;
    socketUrl = ws.url();
    ws.send(JSON.stringify({ type: 'stdout', data: 'raven@wsbox:/workspaces/demo-ws$ ' }));
    ws.onMessage((message) => {
      const text = String(message);
      if (text.startsWith('{')) {
        const ctrl = JSON.parse(text) as { type?: string; width: number; height: number };
        if (ctrl.type === 'resize') resizes.push({ width: ctrl.width, height: ctrl.height });
        return;
      }
      typed.push(text);
      if (text === '\r') {
        // What a pty sends back: the command's output, then a fresh prompt.
        ws.send(JSON.stringify({ type: 'stdout', data: '\r\nhello from the sandbox\r\nraven@wsbox:/workspaces/demo-ws$ ' }));
      } else {
        ws.send(JSON.stringify({ type: 'stdout', data: text }));
      }
    });
  });

  await page.goto('/workspaces?select=demo-ws');
  await page.getByRole('button', { name: 'Terminal', exact: true }).first().click();
  const screen = page.locator('.xterm-rows');
  await expect(screen).toContainText('raven@wsbox:/workspaces/demo-ws$');
  expect(new URL(socketUrl).searchParams.get('token')).toBe('test-key');
  expect(new URL(socketUrl).host).toBe(new URL(page.url()).host);
  await expect.poll(() => resizes.length).toBeGreaterThan(0);
  expect(resizes.at(-1)!.width).toBeGreaterThan(10);
  expect(resizes.at(-1)!.height).toBeGreaterThan(2);

  await page.locator('.xterm').first().click();
  await page.keyboard.type('echo hi');
  await page.keyboard.press('Enter');
  await expect(screen).toContainText('hello from the sandbox');
  expect(typed.join('')).toBe('echo hi\r');

  // A bare line feed from a full-screen program moves down, not back to column 0.
  socket!.send(JSON.stringify({ type: 'stdout', data: '\r\nAB\nCD' }));
  await expect(screen).toContainText('CD');
  const rows = await screen.locator(':scope > div').allTextContents();
  const cdRow = rows.find((r) => r.includes('CD')) ?? '';
  expect(cdRow.indexOf('CD'), 'CD stays under the end of AB').toBe(2);

  await expectNoHorizontalOverflow(page);
  await snap(page, info, 'workspace-terminal');
  expect(errors).toEqual([]);
  expect(mocks.unmocked).toEqual([]);
});
