import type { FastifyInstance } from 'fastify';
import { config } from '../config.ts';
import { getCatalog, getCompany, getPublicSettings, nightInfo } from '../lib/catalog.ts';

export async function publicRoutes(app: FastifyInstance): Promise<void> {
  app.get('/health', async () => ({ ok: true, time: new Date().toISOString() }));

  app.get('/bootstrap', async () => ({
    company: getCompany(),
    settings: getPublicSettings(),
    categories: getCatalog(),
    night: nightInfo(),
    bot: config.clientBotUsername,
    baseUrl: config.publicBaseUrl,
  }));
}
