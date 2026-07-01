const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
  });
  const page = await context.newPage();

  // Capture all console messages
  page.on('console', (msg) => {
    const type = msg.type();
    const text = msg.text();
    const loc = msg.location();
    const prefix = type === 'error' ? '❌' : type === 'warning' ? '⚠️' : '  ';
    console.log(`${prefix} [${loc.url || 'console'}] ${text}`);
  });

  // Capture unhandled errors
  page.on('pageerror', (err) => {
    console.log(`🚨 PAGE ERROR: ${err.message}`);
  });

  console.log('🌐 Navigating to jarvis.sumemail.com...');
  await page.goto('https://jarvis.sumemail.com', {
    waitUntil: 'networkidle',
    timeout: 30000,
  });

  console.log('✅ Page loaded');
  console.log('📋 Current URL:', page.url());

  // Wait for page to fully settle
  await new Promise(r => setTimeout(r, 3000));

  // Try to find the play button or a song to click
  // Look for any play buttons or track items
  const playButtons = await page.$$('button[class*="play"], [role="button"], button:has-text("Play")');
  console.log(`🔘 Found ${playButtons.length} potential play buttons`);

  const trackItems = await page.$$('.track-item, .song-item, [class*="track"], [class*="song"], .list-item');
  console.log(`🎵 Found ${trackItems.length} potential track items`);

  // Take a screenshot to see what we're looking at
  await page.screenshot({ path: '/tmp/ui-state-before.png', fullPage: true });
  console.log('📸 Screenshot saved to /tmp/ui-state-before.png');

  // Click the first track item or play button we can find
  if (playButtons.length > 0) {
    console.log('🖱️ Clicking first play button...');
    try {
      await playButtons[0].click({ force: true });
    } catch (e) {
      console.log('❌ Click failed:', e.message);
    }
  } else if (trackItems.length > 0) {
    console.log('🖱️ Clicking first track item...');
    try {
      await trackItems[0].click({ force: true });
    } catch (e) {
      console.log('❌ Click failed:', e.message);
    }
  }

  // Wait for initPlayer to run and log
  await new Promise(r => setTimeout(r, 5000));

  // Take another screenshot
  await page.screenshot({ path: '/tmp/ui-state-after.png', fullPage: true });
  console.log('📸 Screenshot saved to /tmp/ui-state-after.png');

  // Check for any error messages displayed on page
  const errorText = await page.evaluate(() => {
    const body = document.body?.innerText || '';
    // Look for common error patterns
    const errors = body.match(/No API token|failed|error|undefined|Null/ig);
    return errors ? errors.join(', ') : 'none found';
  });
  console.log('⚠️ Error text on page:', errorText);

  // Dump all localStorage keys related to auth
  const storedKeys = await page.evaluate(() => {
    const result = {};
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key.toLowerCase().includes('token') || key.toLowerCase().includes('api') || key.toLowerCase().includes('auth') || key.toLowerCase().includes('jarvis')) {
        result[key] = localStorage.getItem(key)?.substring(0, 20) + '...';
      }
    }
    return result;
  });
  console.log('🔑 Auth-related localStorage keys:', JSON.stringify(storedKeys, null, 2));

  // Check if any WebSocket connections are open
  const wsState = await page.evaluate(() => {
    // Check for any open websockets
    return { readyState: 'not tracked' };
  });

  // Final screenshot
  await page.screenshot({ path: '/tmp/ui-state-final.png', fullPage: true });
  console.log('📸 Final screenshot saved to /tmp/ui-state-final.png');

  await browser.close();
  console.log('🏁 Done');
})();
