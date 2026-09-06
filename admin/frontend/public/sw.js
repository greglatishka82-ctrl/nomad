const CACHE = 'nomad-admin-shell-v8';
const SHELL_KEY = '/__nomad-admin-shell';

self.addEventListener('install', (event) => {
  // Vite fingerprints JS/CSS file names. Read the current HTML on install so
  // the first online visit caches the exact fingerprinted shell too.
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    const indexResponse = await fetch('/index.html', { cache: 'no-store' });
    const html = await indexResponse.text();
    const shell = new Response(html, { headers: { 'Content-Type': 'text/html' } });
    await cache.put('/index.html', shell.clone());
    await cache.put('/', shell.clone());
    await cache.put(SHELL_KEY, shell);
    const assets = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)]
      .map((match) => new URL(match[1], self.location.origin))
      .filter((url) => url.origin === self.location.origin)
      .map((url) => url.pathname);
    await cache.addAll([...new Set(assets)]);
  })());
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter((name) => name.startsWith('nomad-admin-shell-') && name !== CACHE)
      .map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  // API payloads and mutations are handled by the app's IndexedDB queue. Do
  // not cache authenticated responses in Cache Storage.
  if (url.pathname.startsWith('/api/')) return;
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  event.respondWith(
    fetch(request).then((response) => {
      // Clone synchronously.  If cloning happens in the later cache promise,
      // the browser may already have consumed the original response body.
      const responseForCache = response.ok ? response.clone() : null;
      if (responseForCache) {
        caches.open(CACHE).then((cache) => cache.put(request, responseForCache));
      }
      return response;
    }).catch(async () => {
      const cached = await caches.match(request);
      return cached || caches.match(SHELL_KEY);
    })
  );
});
