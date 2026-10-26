import type { FastifyInstance } from 'fastify';
import { analytics, defaultRange } from '../lib/analytics.ts';

export async function analyticsRoutes(app: FastifyInstance): Promise<void> {
  app.get('/analytics', async (req) => {
    const q = req.query as { from?: string; to?: string; days?: string };
    const fallback = defaultRange(q.days ? Number(q.days) : 30);
    const from = q.from || fallback.from;
    const to = q.to || fallback.to;
    return analytics(from, to);
  });
}
