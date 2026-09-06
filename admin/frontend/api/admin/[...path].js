export const config = {
  api: { bodyParser: false },
};

const SKIPPED_REQUEST_HEADERS = new Set([
  'connection', 'content-length', 'host', 'transfer-encoding',
]);
const SKIPPED_RESPONSE_HEADERS = new Set([
  'connection', 'content-encoding', 'content-length', 'set-cookie', 'transfer-encoding',
]);
const PROXY_PATH_PARAM = '__nomad_proxy_path';

function getBackendUrl() {
  const rawUrl = process.env.ADMIN_BACKEND_URL?.trim();
  if (!rawUrl) return null;

  const url = new URL(rawUrl);
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('ADMIN_BACKEND_URL must use http or https');
  }
  return url.toString().replace(/\/+$/, '');
}

function getTargetUrl(requestUrl, backendUrl) {
  const target = new URL(requestUrl, backendUrl);
  const rewrittenPath = target.searchParams.get(PROXY_PATH_PARAM);

  if (rewrittenPath !== null) {
    const normalized = new URL(`/api/admin/${rewrittenPath.replace(/^\/+/, '')}`, backendUrl);
    if (!normalized.pathname.startsWith('/api/admin/')) {
      throw new Error('Invalid admin proxy path');
    }
    target.pathname = normalized.pathname;
    target.searchParams.delete(PROXY_PATH_PARAM);
  }

  return target.toString();
}

export default async function adminApiProxy(request, response) {
  let backendUrl;
  try {
    backendUrl = getBackendUrl();
  } catch (error) {
    response.status(500).json({ detail: 'Некорректно задан ADMIN_BACKEND_URL' });
    return;
  }
  if (!backendUrl) {
    response.status(503).json({ detail: 'ADMIN_BACKEND_URL не настроен' });
    return;
  }

  const headers = new Headers();
  for (const [name, value] of Object.entries(request.headers)) {
    if (value !== undefined && !SKIPPED_REQUEST_HEADERS.has(name.toLowerCase())) {
      headers.set(name, Array.isArray(value) ? value.join(', ') : value);
    }
  }

  const method = request.method || 'GET';
  const hasBody = !['GET', 'HEAD'].includes(method);
  let target;
  try {
    target = getTargetUrl(request.url, backendUrl);
  } catch (error) {
    response.status(400).json({ detail: 'Некорректный путь admin API' });
    return;
  }

  try {
    const upstream = await fetch(target, {
      method,
      headers,
      body: hasBody ? request : undefined,
      cache: 'no-store',
      duplex: hasBody ? 'half' : undefined,
      redirect: 'manual',
    });

    for (const [name, value] of upstream.headers) {
      if (!SKIPPED_RESPONSE_HEADERS.has(name.toLowerCase())) {
        response.setHeader(name, value);
      }
    }
    const cookies = upstream.headers.getSetCookie();
    if (cookies.length) response.setHeader('set-cookie', cookies);

    response.status(upstream.status).end(Buffer.from(await upstream.arrayBuffer()));
  } catch (error) {
    response.status(502).json({ detail: 'Не удалось подключиться к admin backend' });
  }
}
