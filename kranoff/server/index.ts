import fs from 'node:fs';
import path from 'node:path';
import Fastify from 'fastify';
import cookie from '@fastify/cookie';
import multipart from '@fastify/multipart';
import fastifyStatic from '@fastify/static';
import websocket from '@fastify/websocket';
import { config, nowIso } from './config.ts';
import { db } from './db.ts';
import { requireAdmin, cleanupSessions } from './lib/auth.ts';
import { expireOffers } from './lib/orders.ts';
import { heartbeat } from './lib/live.ts';
import { publicRoutes } from './routes/public.ts';
import { authRoutes } from './routes/auth.ts';
import { orderRoutes } from './routes/orders.ts';
import { masterRoutes } from './routes/masters.ts';
import { clientRoutes } from './routes/clients.ts';
import { serviceRoutes } from './routes/services.ts';
import { supportRoutes } from './routes/support.ts';
import { analyticsRoutes } from './routes/analytics.ts';
import { adminRoutes } from './routes/admins.ts';
import { settingsRoutes } from './routes/settings.ts';
import { logsRoutes } from './routes/logs.ts';
import { masterPageRoutes } from './routes/masterPage.ts';
import { wsRoutes } from './routes/ws.ts';
import { startBots } from './bots/index.ts';

const app = Fastify({
  logger: { level: process.env.LOG_LEVEL || 'info' },
  trustProxy: true,
  bodyLimit: 20 * 1024 * 1024,
});

await app.register(cookie, { secret: config.sessionSecret });
await app.register(multipart, { limits: { fileSize: 50 * 1024 * 1024, files: 1 } });
await app.register(websocket);

app.addHook('onRequest', async (req) => {
  if (req.url.startsWith('/api/') || req.url.startsWith('/ws/')) {
    req.headers['x-kranoff-at'] = nowIso();
  }
});

app.setErrorHandler((error, req, reply) => {
  req.log.error({ err: error, url: req.url }, 'request failed');
  if (reply.sent) return;
  const status = error.statusCode && error.statusCode >= 400 ? error.statusCode : 500;
  reply.code(status).send({ error: status >= 500 ? 'Внутренняя ошибка сервера' : error.message });
});

await app.register(publicRoutes, { prefix: '/api/public' });
await app.register(authRoutes, { prefix: '/api/auth' });

await app.register(
  async (scope) => {
    scope.addHook('preHandler', requireAdmin);
    await scope.register(orderRoutes);
    await scope.register(masterRoutes);
    await scope.register(clientRoutes);
    await scope.register(serviceRoutes);
    await scope.register(supportRoutes);
    await scope.register(analyticsRoutes);
    await scope.register(adminRoutes);
    await scope.register(settingsRoutes);
    await scope.register(logsRoutes);
  },
  { prefix: '/api/admin' },
);

await app.register(masterPageRoutes, { prefix: '/api/master' });
await app.register(wsRoutes, { prefix: '/ws' });

const webDir = path.resolve('dist/web');
if (fs.existsSync(webDir)) {
  await app.register(fastifyStatic, {
    root: webDir,
    prefix: '/',
    index: ['index.html'],
    wildcard: true,
  });
}

app.setNotFoundHandler((req, reply) => {
  if (req.url.startsWith('/api/') || req.url.startsWith('/ws/')) {
    return reply.code(404).send({ error: 'Не найдено' });
  }
  if (fs.existsSync(path.join(webDir, 'index.html'))) {
    return reply.type('text/html').send(fs.readFileSync(path.join(webDir, 'index.html')));
  }
  return reply.code(404).send({ error: 'Сборка сайта не найдена. Выполните npm run build.' });
});

app.get('/api/health', async () => ({ ok: true, time: nowIso(), baseUrl: config.publicBaseUrl }));

await app.listen({ port: config.port, host: config.host });
app.log.info(`KRANOFF server listening on http://${config.host}:${config.port}`);
app.log.info(`public base url: ${config.publicBaseUrl}`);

startBots();

const offerTimer = setInterval(() => {
  try {
    const count = expireOffers();
    if (count > 0) app.log.warn(`expired ${count} offers`);
  } catch (error) {
    app.log.error({ err: error }, 'offer expiry failed');
  }
}, 15_000);

const sessionTimer = setInterval(() => {
  try {
    cleanupSessions();
  } catch (error) {
    app.log.error({ err: error }, 'session cleanup failed');
  }
}, 3600_000);

const heartbeatTimer = setInterval(() => heartbeat(), 30_000);

async function shutdown(signal: string): Promise<void> {
  app.log.info(`received ${signal}, shutting down`);
  clearInterval(offerTimer);
  clearInterval(sessionTimer);
  clearInterval(heartbeatTimer);
  try {
    await app.close();
  } catch (error) {
    app.log.error({ err: error }, 'close failed');
  }
  try {
    db.close();
  } catch {
    // ignore
  }
  process.exit(0);
}

process.on('SIGINT', () => void shutdown('SIGINT'));
process.on('SIGTERM', () => void shutdown('SIGTERM'));
process.on('unhandledRejection', (error) => {
  app.log.error({ err: error }, 'unhandled rejection');
});
