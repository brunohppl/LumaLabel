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
