/* On-set mode service worker (scope /shoots/).
   Network first for on-set pages, falling back to the copy saved on the last visit;
   cache first for static files. Sync requests are never cached: the page queues
   changes in browser storage and sends them when the connection returns. */
const CACHE = 'onset-v1';

self.addEventListener('install', (event) => { self.skipWaiting(); });
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE).then((cache) => cache.match(request).then((hit) => hit || fetch(request).then((response) => {
        if (response.ok) cache.put(request, response.clone());
        return response;
      })))
    );
    return;
  }
  if (url.pathname.startsWith('/shoots/') && url.pathname.endsWith('/on-set/')) {
    event.respondWith(
      fetch(request).then((response) => {
        if (response.ok && !response.redirected) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      }).catch(() => caches.match(request).then((hit) => hit || new Response('You are offline and this shoot was not opened on this phone yet.', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } })))
    );
  }
});
