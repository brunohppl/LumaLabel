// Deliberately does almost nothing.
//
// A service worker is required for the app to be installable, but caching
// is where a PWA goes wrong: a driver looking at yesterday's runsheet with
// no sign that it is stale is worse than a driver waiting for a fresh one.
// So this one passes every request straight through to the network and
// caches nothing at all.
//
// Offline support is a separate piece of work — it needs decisions about
// which pages are safe to serve stale, how long to trust them, and how to
// show when data was last updated.

self.addEventListener('install', () => {
  self.skipWaiting();            // a new version takes over promptly
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// No fetch handler on purpose. Adding one that merely forwards the request
// would slow every request down for no benefit.

// ── Push notifications ──────────────────────────────────────────────
// A push arrives here even when no page is open — that is the point of a
// worker. The payload is JSON the server sent; if it is missing or
// malformed, still show something rather than nothing, because a silent
// push is worse than a vague one.
self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { body: event.data ? event.data.text() : '' };
  }

  const title = data.title || 'LUMA';
  const options = {
    body: data.body || '',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    data: { url: data.url || '/today' },
    // Without a tag, several notifications stack up; with one, a newer
    // message replaces the last rather than burying the phone.
    tag: data.tag || 'luma',
    renotify: true,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Tapping the notification should land on the relevant page — and reuse
// an open window rather than piling up new ones.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/today';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((windows) => {
        for (const client of windows) {
          if ('focus' in client) {
            client.navigate(target);
            return client.focus();
          }
        }
        return self.clients.openWindow(target);
      })
  );
});
