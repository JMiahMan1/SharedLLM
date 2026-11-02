/* Push handler for report notifications. */
self.addEventListener('push', (event) => {
  let payload = { title: 'Jarvis', body: 'A new report is ready.' };
  try {
    if (event.data) payload = { ...payload, ...event.data.json() };
  } catch (e) {
    if (event.data) payload.body = event.data.text();
  }
  const options = {
    body: payload.body,
    icon: '/icons/icon-192.png',
    badge: '/icons/badge-72.png',
    // A report replaces its own earlier notice; any other notice is its own,
    // so a prayer reminder never silently replaces the one before it.
    tag: payload.data && payload.data.report_id ? payload.data.report_id : `jarvis-${Date.now()}`,
    data: payload.data || {},
  };
  event.waitUntil(self.registration.showNotification(payload.title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  // Where the notice points: a report's `path`, or any notice's `url` (a
  // verse, a chat). Only a path inside the app is followed.
  const data = event.notification.data || {};
  const raw = data.path || data.url;
  const target = typeof raw === 'string' && raw.startsWith('/') && !raw.startsWith('//') ? raw : null;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
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
    })
  );
});
