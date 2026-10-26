import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { getSettings, setSettings, logActivity } from '../db.ts';
import { currentAdmin } from '../lib/auth.ts';

const ALLOWED = new Set([
  'company_phone',
  'company_telegram',
  'company_hours',
  'night_start',
  'night_end',
  'night_multiplier',
  'master_share',
  'offer_timeout_minutes',
  'min_masters_on_shift',
  'currency',
]);

export async function settingsRoutes(app: FastifyInstance): Promise<void> {
  app.get('/settings', async () => ({ values: getSettings() }));

  app.put('/settings', async (req, reply) => {
    const admin = currentAdmin(req);
    const schema = z.object({ values: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])) });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Некорректные настройки' });
    const clean: Record<string, string> = {};
    for (const [key, raw] of Object.entries(parsed.data.values)) {
      if (!ALLOWED.has(key)) continue;
      clean[key] = String(raw).slice(0, 6000);
    }
    const intRange = (key: string, min: number, max: number) => {
      if (clean[key] == null || clean[key] === '') return;
      const value = Number(clean[key]);
      if (!Number.isFinite(value) || value < min || value > max) throw new Error(`${key}: допустимо ${min}–${max}`);
      clean[key] = String(Math.round(value));
    };
    try {
      intRange('night_start', 0, 23);
      intRange('night_end', 0, 23);
      intRange('offer_timeout_minutes', 1, 60);
      intRange('min_masters_on_shift', 1, 20);
      if (clean.night_multiplier != null && clean.night_multiplier !== '') {
        const value = Number(clean.night_multiplier);
        if (!Number.isFinite(value) || value < 1 || value > 5) throw new Error('Ночной коэффициент: 1–5');
        clean.night_multiplier = String(value);
      }
      if (clean.master_share != null && clean.master_share !== '') {
        let value = Number(clean.master_share);
        if (!Number.isFinite(value) || value < 0 || value > 100) throw new Error('Доля мастера: 0–100%');
        if (value > 1) value = value / 100;
        clean.master_share = String(value);
      }
    } catch (error) {
      return reply.code(400).send({ error: error instanceof Error ? error.message : 'Проверьте настройки' });
    }
    setSettings(clean);
    logActivity(admin.id, 'settings.update', 'settings', null, Object.keys(clean).join(','));
    return reply.send({ values: getSettings() });
  });
}
