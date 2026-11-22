/* Web Push: show a notice with its buttons, and answer a button the way the bell does.
 *
 * A notice's buttons (docs/PUSH.md) arrive as `actions`: `view` opens its `url`; `callback` and `reply`
 * post `{callback, params, reply?}` to the gateway's `POST /api/push/action` as this person, the same
 * call the bell's buttons make (NotificationActions.tsx). A service worker cannot read the page's
 * storage, so the page hands it the person's API key (`jarvis-push-key`, lib/push.ts) and it is kept in
 * Cache Storage on this origin. Without a key, or when the gateway refuses it, the person is told so in
 * a notice; a button never fails without a word.
 */
const KEY_CACHE = 'jarvis-push-auth';
const KEY_URL = '/__jarvis_push_key';

async function readKey() {
  try {
    const cache = await caches.open(KEY_CACHE);
    const hit = await cache.match(KEY_URL);
    const key = hit ? await hit.text() : '';
    return key || null;
  } catch (e) {
    return null;
  }
}

async function writeKey(key) {
  const cache = await caches.open(KEY_CACHE);
  if (key) await cache.put(KEY_URL, new Response(key));
  else await cache.delete(KEY_URL);
}

self.addEventListener('message', (event) => {
  const msg = event.data || {};
  if (msg.type !== 'jarvis-push-key') return;
  const key = typeof msg.key === 'string' && msg.key ? msg.key : null;
  const done = writeKey(key);
  if (event.waitUntil) event.waitUntil(done);
});

/** The buttons the browser can show (Chrome shows two), as Notification actions `a0`, `a1`, ... */
function browserActions(actions) {
  const max = (self.Notification && self.Notification.maxActions) || 2;
  return actions.slice(0, max).map((a, i) => {
    const out = { action: `a${i}`, title: String(a.label || 'Open') };
    // A reply asks for text in the notice where the browser can (Chrome); elsewhere a tap opens the app.
    if (a.type === 'reply') Object.assign(out, { type: 'text', placeholder: String(a.label || 'Reply') });
    return out;
  });
}

self.addEventListener('push', (event) => {
  let payload = { title: 'Jarvis', body: 'A new report is ready.' };
  try {
    if (event.data) payload = { ...payload, ...event.data.json() };
  } catch (e) {
    if (event.data) payload.body = event.data.text();
  }
  const data = payload.data || {};
  const actions = Array.isArray(payload.actions) ? payload.actions.filter((a) => a && typeof a === 'object') : [];
  const options = {
    body: payload.body,
    icon: '/icons/icon-192.png',
    badge: '/icons/badge-72.png',
    // A report replaces its own earlier notice, and a notice with a tag (a timer's) its own; any other
    // notice is its own, so a prayer reminder never silently replaces the one before it.
    tag: data.report_id || payload.tag || `jarvis-${Date.now()}`,
    data: { ...data, jarvis_actions: actions },
  };
  if (actions.length) options.actions = browserActions(actions);
  event.waitUntil(self.registration.showNotification(payload.title, options));
});

/** An in-app path, or null (only paths inside the app are followed). */
function appPath(raw) {
  return typeof raw === 'string' && raw.startsWith('/') && !raw.startsWith('//') ? raw : null;
}

function openApp(target, data) {
  return self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
    for (const client of clientList) {
      if ('focus' in client) {
        client.postMessage({ type: 'jarvis-report-opened', data });
        if (target && 'navigate' in client) {
          return client.navigate(target).then((opened) => (opened || client).focus());
        }
        return client.focus();
      }
    }
    if (self.clients.openWindow) {
      return self.clients.openWindow(target || '/settings');
    }
    return undefined;
  });
}

function tell(title, body) {
  return self.registration.showNotification(title, {
    body, icon: '/icons/icon-192.png', badge: '/icons/badge-72.png', tag: `jarvis-action-${Date.now()}`,
  });
}

/** POST /api/push/action as this person; a refusal is shown, never swallowed. */
async function runAction(action, reply) {
  const key = await readKey();
  if (!key) {
    return tell(`${action.label} did not run`, 'Open Jarvis in this browser and sign in, then try the button again.');
  }
  const body = { callback: action.callback, params: action.params || {} };
  if (reply !== undefined) body.reply = reply;
  let resp;
  try {
    resp = await fetch('/api/push/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
      body: JSON.stringify(body),
    });
  } catch (e) {
    return tell(`${action.label} did not run`, 'Jarvis could not be reached.');
  }
  let answer = {};
  try {
    answer = await resp.json();
  } catch (e) {
    answer = {};
  }
  if (resp.status === 401) {
    await writeKey(null);
    return tell(`${action.label} did not run`, 'Sign in to Jarvis in this browser again, then try the button again.');
  }
  if (!resp.ok || answer.status === 'FAILURE' || answer.status === 'ERROR') {
    const why = answer.message || answer.detail || `Jarvis answered ${resp.status}`;
    return tell(`${action.label} did not run`, String(why));
  }
  return undefined;
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const picked = /^a(\d+)$/.exec(event.action || '');
  const action = picked ? (data.jarvis_actions || [])[Number(picked[1])] : null;
  if (action && action.type === 'view') {
    event.waitUntil(openApp(appPath(action.url), data));
    return;
  }
  if (action && action.callback && (action.type === 'callback' || (action.type === 'reply' && event.reply))) {
    event.waitUntil(runAction(action, action.type === 'reply' ? event.reply : undefined));
    return;
  }
  // The notice itself, or a reply where the browser could not ask for text: open it in the app, where the
  // bell lists it with every button.
  // Its link (the page and the item on it, docs/PUSH.md "Deep links"), else the older url.
  event.waitUntil(openApp(appPath((data.link && data.link.href) || data.path || data.url), data));
});
