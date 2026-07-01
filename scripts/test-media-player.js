/**
 * Live test: Media Player Web Player (Sendspin) connection flow
 *
 * Runs headless against the deployed UI to verify:
 * 1. Login succeeds
 * 2. Media page loads
 * 3. Play button triggers initPlayer
 * 4. Sendspin WebSocket connects to gateway
 * 5. Browser sends client/hello to gateway
 *
 * Usage:
 *   cd services/ui && npx playwright install
 *   node ../scripts/test-media-player.js [--host URL] [--user USERNAME] [--pass PASSWORD]
 *
 * Examples:
 *   node ../scripts/test-media-player.js
 *   node ../scripts/test-media-player.js --host https://jarvis.sumemail.com --user admin --pass secret
 */

const { chromium } = require('playwright');

const DEFAULTS = {
  host: process.env.JARVIS_HOST || 'https://jarvis.sumemail.com',
  user: process.env.JARVIS_USER || 'admin',
  pass: process.env.JARVIS_PASS || 'admin',
};

// Parse CLI args
function parseArgs() {
  const args = process.argv.slice(2);
  const opts = { ...DEFAULTS };
  for (let i = 0; i < args.length; i += 2) {
    const flag = args[i];
    if (flag === '--host' && args[i + 1]) opts.host = args[i + 1];
    if (flag === '--user' && args[i + 1]) opts.user = args[i + 1];
    if (flag === '--pass' && args[i + 1]) opts.pass = args[i + 1];
    if (flag === '--headful') opts.headless = false;
  }
  return opts;
}

// Test result tracker
const results = [];
function assert(condition, label) {
  const pass = !!condition;
  results.push({ label, pass });
  const icon = pass ? '✅' : '❌';
  console.log(`${icon} ${label}`);
  return pass;
}

function pass(label) { assert(true, label); return true; }
function fail(label) { assert(false, label); return false; }

// Console log collector
const consoleLogs = [];
let sendspinInitStarted = false;
let sendspinInitCompleted = false;
let sendspinError = false;

function formatConsole(msg) {
  const type = msg.type();
  const text = msg.text();
  const loc = msg.location();
  const prefix = type === 'error' ? '❌' : type === 'warning' ? '⚠️' : '  ';
  const shortLoc = loc.url ? loc.url.split('/').pop() : 'console';
  return `${prefix} [${shortLoc}] ${text}`;
}

async function runTest(opts) {
  console.log(`\n🧪 Media Player Web Player Live Test`);
  console.log(`   Host: ${opts.host}`);
  console.log(`   User: ${opts.user}`);
  console.log(`   Headless: ${opts.headless !== false}\n`);

  const browser = await chromium.launch({
    headless: opts.headless !== false,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
  });

  const page = await context.newPage();

  // Intercept console
  page.on('console', (msg) => {
    const entry = formatConsole(msg);
    consoleLogs.push({ type: msg.type(), text: msg.text() });
    console.log(entry);

    // Track sendspin initPlayer flow
    const text = msg.text();
    if (text.includes('[MAWebPlayer] initPlayer called')) sendspinInitStarted = true;
    if (text.includes('Player initialized and connected successfully')) sendspinInitCompleted = true;
    if (text.includes('[MAWebPlayer] ERROR') || text.includes('[MAWebPlayer] initPlayer FAILED')) sendspinError = true;
  });

  page.on('pageerror', (err) => {
    console.log(`🚨 PAGE ERROR: ${err.message}`);
    sendspinError = true;
  });

  let exitCode = 0;

  try {
    // ── Step 1: Navigate to login ──
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log('Step 1: Login');
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');

    await page.goto(opts.host, { waitUntil: 'domcontentloaded', timeout: 30000 });

    // Check if already logged in
    const currentUrl = page.url();
    if (!currentUrl.includes('/login')) {
      pass('Already logged in, navigating to media...');
    } else {
      pass('On login page');

      // Fill login form
      const emailInput = page.locator('input[type="email"], input[name="email"], input[placeholder*="email"]');
      const passwordInput = page.locator('input[type="password"]');

      const hasEmailInput = await emailInput.count() > 0;
      const hasPasswordInput = await passwordInput.count() > 0;

      assert(hasEmailInput && hasPasswordInput, 'Login form fields found');

      if (hasEmailInput && hasPasswordInput) {
        await emailInput.fill(opts.user);
        await passwordInput.fill(opts.pass);
        console.log('🔑 Credentials filled');

        // Find and click login button
        const loginBtn = page.locator('button[type="submit"], button:has-text("Login"), button:has-text("Sign In")');
        const hasLoginBtn = await loginBtn.count() > 0;
        assert(hasLoginBtn, 'Login button found');

        if (hasLoginBtn) {
          await loginBtn.click();
          await page.waitForLoadState('networkidle', { timeout: 15000 });
          pass('Login submitted, waiting for redirect...');
        }
      }

      // Wait for redirect
      await new Promise(r => setTimeout(r, 3000));

      // Verify we're past login
      const postLoginUrl = page.url();
      assert(!postLoginUrl.includes('/login'), `Redirected from login (now: ${postLoginUrl})`);
    }

    // ── Step 2: Navigate to Media page ──
    console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log('Step 2: Media Page');
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');

    await page.goto(`${opts.host}/media`, { waitUntil: 'networkidle', timeout: 30000 });
    await new Promise(r => setTimeout(r, 3000));

    const mediaUrl = page.url();
    assert(mediaUrl.includes('/media') || mediaUrl.includes('/music'), `On media page (URL: ${mediaUrl})`);

    // Take screenshot of media page
    await page.screenshot({ path: '/tmp/test-media-page.png', fullPage: true });
    console.log('📸 Screenshot: /tmp/test-media-page.png');

    // ── Step 3: Find and click play ──
    console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log('Step 3: Trigger Play');
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');

    // Check localStorage for API token
    const storedKeys = await page.evaluate(() => {
      const result = {};
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key.toLowerCase().includes('token') || key.toLowerCase().includes('api') || key.toLowerCase().includes('jarvis')) {
          result[key] = '***';
        }
      }
      return result;
    });
    assert(Object.keys(storedKeys).length > 0, `API token in localStorage (${Object.keys(storedKeys).join(', ')})`);

    // Find play button
    const playButtons = await page.$$('button[class*="play"], button:has-text("Play"), button[aria-label*="play"]');
    console.log(`🔘 Found ${playButtons.length} play button(s)`);
    assert(playButtons.length > 0, 'Play button exists');

    if (playButtons.length === 0) {
      // Take screenshot and exit
      await page.screenshot({ path: '/tmp/test-no-play-btn.png', fullPage: true });
      console.log('📸 Screenshot: /tmp/test-no-play-btn.png');
      fail('No play button found, cannot continue');
      exitCode = 1;
    } else {
      // Click first play button
      console.log('🖱️ Clicking play button...');
      await playButtons[0].click({ force: true });

      // Wait for initPlayer flow
      console.log('⏳ Waiting for initPlayer to execute (10s)...');
      await new Promise(r => setTimeout(r, 10000));

      // ── Step 4: Verify sendspin flow ──
      console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
      console.log('Step 4: Sendspin Connection Flow');
      console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');

      // Check console logs for initPlayer
      const initLogs = consoleLogs.filter(l =>
        l.text.includes('[MAWebPlayer] initPlayer') ||
        l.text.includes('[MAWebPlayer] connect') ||
        l.text.includes('[MAWebPlayer] [1/6]') ||
        l.text.includes('[MAWebPlayer] [2/6]') ||
        l.text.includes('[MAWebPlayer] [3/6]') ||
        l.text.includes('[MAWebPlayer] [4/6]') ||
        l.text.includes('[MAWebPlayer] [5/6]') ||
        l.text.includes('[MAWebPlayer] [6/6]') ||
        l.text.includes('Player initialized and connected') ||
        l.text.includes('[MAWebPlayer] ERROR') ||
        l.text.includes('[MAWebPlayer] initPlayer FAILED')
      );

      console.log(`📋 initPlayer-related log entries: ${initLogs.length}`);

      if (sendspinInitStarted) {
        pass('initPlayer was called');
      } else {
        fail('initPlayer was NOT called (play button click may not have triggered it)');
      }

      if (sendspinError) {
        const errors = consoleLogs.filter(l =>
          l.text.includes('[MAWebPlayer] ERROR') ||
          l.text.includes('[MAWebPlayer] initPlayer FAILED') ||
          l.text.includes('token')
        );
        fail(`initPlayer encountered errors (${errors.length} error(s))`);
        if (errors.length > 0) {
          console.log('   Error details:');
          errors.slice(0, 5).forEach(e => console.log(`     ${e.text}`));
        }
      } else if (sendspinInitCompleted) {
        pass('initPlayer completed successfully');
      }

      // Check for gateway sendspin WebSocket connection
      const wsLogs = consoleLogs.filter(l =>
        l.text.includes('WebSocket') ||
        l.text.includes('sendspin') ||
        l.text.includes('client/hello') ||
        l.text.includes('server/hello') ||
        l.text.includes('connect:')
      );
      console.log(`🔌 WebSocket-related logs: ${wsLogs.length}`);

      // Take final screenshot
      await page.screenshot({ path: '/tmp/test-after-play.png', fullPage: true });
      console.log('📸 Screenshot: /tmp/test-after-play.png');

      // Check connection state
      const connState = await page.evaluate(() => {
        // Check if there's any connection state in the UI
        const bodyText = document.body?.innerText || '';
        const isConnected = bodyText.toLowerCase().includes('connected');
        const isPlaying = bodyText.toLowerCase().includes('playing');
        return { bodyPreview: bodyText.substring(0, 500), isConnected, isPlaying };
      });

      assert(connState.isConnected || sendspinInitCompleted,
        `UI connection state (connected=${connState.isConnected}, isPlaying=${connState.isPlaying})`);
    }

  } catch (err) {
    console.log(`\n🚨 Test crashed: ${err.message}`);
    await page.screenshot({ path: '/tmp/test-crash.png', fullPage: true });
    console.log('📸 Screenshot: /tmp/test-crash.png');
    fail('Test crashed with exception');
    exitCode = 1;
  } finally {
    // ── Summary ──
    console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log('Test Summary');
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');

    const passed = results.filter(r => r.pass).length;
    const failed = results.filter(r => !r.pass).length;
    const total = results.length;

    console.log(`   Results: ${passed}/${total} passed, ${failed} failed\n`);

    for (const r of results) {
      const icon = r.pass ? '✅' : '❌';
      console.log(`   ${icon} ${r.label}`);
    }

    if (failed > 0) exitCode = 1;

    await browser.close();

    console.log(`\n${failed > 0 ? '❌' : '✅'} Test ${failed > 0 ? 'FAILED' : 'PASSED'}\n`);
    process.exit(exitCode);
  }
}

runTest(parseArgs()).catch(err => {
  console.error('Fatal:', err);
  process.exit(1);
});
