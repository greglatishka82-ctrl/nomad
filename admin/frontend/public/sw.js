const CACHE = 'nomad-admin-shell-v12';
const SHELL_KEY = '/__nomad-admin-shell';

async function fetchAndCache(request) {
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}

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

  const network = fetchAndCache(request);
  event.waitUntil(network.then(() => undefined).catch(() => undefined));
  event.respondWith((async () => {
    const cached = await caches.match(request);
    if (cached) {
      return cached;
    }
    try {
      return await network;
    } catch (_) {
      if (request.mode === 'navigate') {
        return (await caches.match(SHELL_KEY)) || Response.error();
      }
      return Response.error();
    }
  })());
});
