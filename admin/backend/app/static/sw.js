const CACHE = 'nomad-admin-shell-v11';
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
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // Vercel serves the admin shell at /index.html, while the standalone
    // backend serves it at /admin. Cache whichever HTML shell this host has.
    let shellResponse = null;
    let shellPath = null;
    for (const path of ['/index.html', '/admin']) {
      try {
        const response = await fetch(path, { cache: 'no-store' });
        if (response.ok && (response.headers.get('content-type') || '').includes('text/html')) {
          shellResponse = response;
          shellPath = path;
          break;
        }
      } catch (_) {
        // Try the next host-specific shell path.
      }
    }
    if (!shellResponse) throw new Error('Admin shell is unavailable');
    const html = await shellResponse.text();
    const shell = new Response(html, { headers: { 'Content-Type': 'text/html' } });
    await cache.put(shellPath, shell.clone());
    await cache.put(SHELL_KEY, shell);
    const assets = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)]
      .map((match) => new URL(match[1], self.location.origin))
      .filter((url) => url.origin === self.location.origin)
      .map((url) => url.pathname + url.search);
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
  // All administrative data is held in IndexedDB and the write queue. Never
  // let the shell cache serve stale authenticated API responses.
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
