import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { all, get, logAction, run } from '../db.ts';
import { currentAdmin } from '../lib/auth.ts';
import { broadcastAdmin } from '../lib/live.ts';
import { parseJsonArray } from '../lib/utils.ts';
import { SKILLS } from '../seed.ts';

const serviceSchema = z.object({
  category_id: z.number().int().positive(),
  name_ru: z.string().min(1).max(200),
  name_en: z.string().min(1).max(200),
  name_kz: z.string().max(200).optional(),
  description_ru: z.string().optional(),
  description_en: z.string().optional(),
  description_kz: z.string().optional(),
  price: z.number().int().min(0),
  price_night: z.number().int().min(0).nullable().optional(),
  is_emergency: z.boolean().optional(),
  skills: z.array(z.string()).optional(),
  active: z.boolean().optional(),
  sort: z.number().int().optional(),
});

const categorySchema = z.object({
  slug: z.string().min(1).max(80).regex(/^[a-z0-9-]+$/),
  name_ru: z.string().min(1).max(120),
  name_en: z.string().min(1).max(120),
  name_kz: z.string().max(120).optional(),
  icon: z.string().max(16).optional(),
  sort: z.number().int().optional(),
});

function decorateService(row: Record<string, unknown>) {
  return { ...row, skills_list: parseJsonArray(String(row.skills || '[]')) };
}

export async function serviceRoutes(app: FastifyInstance): Promise<void> {
  app.get('/services', async () => {
    const categories = all<Record<string, unknown>>('SELECT * FROM service_categories ORDER BY sort, id');
    const services = all<Record<string, unknown>>('SELECT * FROM services ORDER BY category_id, sort, id').map(decorateService);
    return { categories, services, skills: SKILLS };
  });

  app.post('/services', async (req, reply) => {
    const admin = currentAdmin(req);
    const parsed = serviceSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте данные услуги', details: parsed.error.flatten() });
    const d = parsed.data;
    if (!get('SELECT id FROM service_categories WHERE id = ?', [d.category_id])) return reply.code(400).send({ error: 'Категория не найдена' });
    const id = run(
      `INSERT INTO services (category_id, name_ru, name_en, name_kz, description_ru, description_en, description_kz, price, price_night, is_emergency, skills, active, sort)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        d.category_id,
        d.name_ru.trim(),
        d.name_en.trim(),
        d.name_kz?.trim() || d.name_ru.trim(),
        d.description_ru || '',
        d.description_en || '',
        d.description_kz || '',
        d.price,
        d.price_night ?? null,
        d.is_emergency ? 1 : 0,
        JSON.stringify(d.skills || []),
        d.active === false ? 0 : 1,
        d.sort ?? 0,
      ],
    ).lastInsertRowid;
    logAction(admin.id, `Добавлена услуга ${d.name_ru.trim()} (${d.price} ₸)`, 'service', id);
    broadcastAdmin({ type: 'catalog:changed', at: new Date().toISOString() });
    return reply.code(201).send(decorateService(get<Record<string, unknown>>('SELECT * FROM services WHERE id = ?', [id])!));
  });

  app.put('/services/:id', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const admin = currentAdmin(req);
    const before = get<{ name_ru: string }>('SELECT name_ru FROM services WHERE id = ?', [id]);
    const parsed = serviceSchema.partial().safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте данные услуги' });
    const d = parsed.data;
    const sets: string[] = [];
    const params: unknown[] = [];
    const map: Array<[keyof typeof d, string]> = [
      ['category_id', 'category_id'],
      ['name_ru', 'name_ru'],
      ['name_en', 'name_en'],
      ['name_kz', 'name_kz'],
      ['description_ru', 'description_ru'],
      ['description_en', 'description_en'],
      ['description_kz', 'description_kz'],
      ['price', 'price'],
      ['price_night', 'price_night'],
      ['sort', 'sort'],
    ];
    for (const [key, column] of map) {
      if (key in d && d[key] !== undefined) {
        sets.push(`${column} = ?`);
        params.push(d[key]);
      }
    }
    if (d.is_emergency != null) {
      sets.push('is_emergency = ?');
      params.push(d.is_emergency ? 1 : 0);
    }
    if (d.active != null) {
      sets.push('active = ?');
      params.push(d.active ? 1 : 0);
    }
    if (d.skills != null) {
      sets.push('skills = ?');
      params.push(JSON.stringify(d.skills));
    }
    if (!sets.length) return reply.send(decorateService(get<Record<string, unknown>>('SELECT * FROM services WHERE id = ?', [id])!));
    params.push(id);
    run(`UPDATE services SET ${sets.join(', ')} WHERE id = ?`, params);
    const labels: Record<string, string> = {
      category_id: 'категория',
      name_ru: 'название',
      name_en: 'название EN',
      name_kz: 'название KZ',
      description_ru: 'описание',
      description_en: 'описание EN',
      description_kz: 'описание KZ',
      price: 'цена',
      price_night: 'ночная цена',
      is_emergency: 'аварийность',
      active: 'активность',
      skills: 'навыки',
      sort: 'сортировка',
    };
    const changed = sets.map((s) => s.split(' = ')[0]).filter((key) => key !== 'updated_at');
    logAction(admin.id, `Изменена услуга ${before?.name_ru || `#${id}`}: ${changed.map((key) => labels[key] || key).join(', ')}`, 'service', id);
    broadcastAdmin({ type: 'catalog:changed', at: new Date().toISOString() });
    return reply.send(decorateService(get<Record<string, unknown>>('SELECT * FROM services WHERE id = ?', [id])!));
  });

  app.delete('/services/:id', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const admin = currentAdmin(req);
    const service = get<{ name_ru: string }>('SELECT name_ru FROM services WHERE id = ?', [id]);
    run('DELETE FROM services WHERE id = ?', [id]);
    logAction(admin.id, `Удалена услуга${service ? ` ${service.name_ru}` : ` #${id}`}`, 'service', id);
    broadcastAdmin({ type: 'catalog:changed', at: new Date().toISOString() });
    return reply.send({ ok: true });
  });

  app.post('/categories', async (req, reply) => {
    const admin = currentAdmin(req);
    const parsed = categorySchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте категорию' });
    const d = parsed.data;
    if (get('SELECT id FROM service_categories WHERE slug = ?', [d.slug])) return reply.code(400).send({ error: 'Такой slug уже есть' });
    const id = run('INSERT INTO service_categories (slug, name_ru, name_en, name_kz, icon, sort) VALUES (?, ?, ?, ?, ?, ?)', [
      d.slug,
      d.name_ru,
      d.name_en,
      d.name_kz || d.name_ru,
      d.icon || '',
      d.sort ?? 0,
    ]).lastInsertRowid;
    logAction(admin.id, `Добавлена категория ${d.name_ru}`, 'category', id);
    return reply.code(201).send(get('SELECT * FROM service_categories WHERE id = ?', [id]));
  });

  app.put('/categories/:id', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const admin = currentAdmin(req);
    const parsed = categorySchema.partial().safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте категорию' });
    const d = parsed.data;
    const sets: string[] = [];
    const params: unknown[] = [];
    for (const key of ['slug', 'name_ru', 'name_en', 'name_kz', 'icon', 'sort'] as const) {
      if (d[key] != null) {
        sets.push(`${key} = ?`);
        params.push(d[key]);
      }
    }
    if (sets.length) {
      params.push(id);
      run(`UPDATE service_categories SET ${sets.join(', ')} WHERE id = ?`, params);
      logAction(admin.id, `Изменена категория #${id}: ${sets.map((s) => s.split(' = ')[0]).join(', ')}`, 'category', id);
    }
    return reply.send(get('SELECT * FROM service_categories WHERE id = ?', [id]));
  });

  app.delete('/categories/:id', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const admin = currentAdmin(req);
    const category = get<{ name_ru: string }>('SELECT name_ru FROM service_categories WHERE id = ?', [id]);
    const count = Number(get<{ c: number }>('SELECT COUNT(*) AS c FROM services WHERE category_id = ?', [id])?.c || 0);
    if (count > 0) return reply.code(400).send({ error: 'В категории есть услуги — сначала перенесите или отключите их' });
    run('DELETE FROM service_categories WHERE id = ?', [id]);
    logAction(admin.id, `Удалена категория${category ? ` ${category.name_ru}` : ` #${id}`}`, 'category', id);
    return reply.send({ ok: true });
  });
}
