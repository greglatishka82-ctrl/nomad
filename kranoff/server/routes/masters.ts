import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { all, get, logAction, newMasterToken, run } from '../db.ts';
import { config } from '../config.ts';
import { currentAdmin } from '../lib/auth.ts';
import { freeMaster, getMaster, masterEarnings, setMasterStatus } from '../lib/orders.ts';
import { broadcastAdmin } from '../lib/live.ts';
import { formatDateTime, normalizePhone, parseJsonArray, prettyPhone } from '../lib/utils.ts';
import { SKILLS } from '../seed.ts';

const masterSchema = z.object({
  name: z.string().min(1).max(120),
  phone: z.string().min(5).max(40),
  telegram_id: z.number().int().positive().nullable().optional(),
  skills: z.array(z.string()).default([]),
  tools: z.string().optional(),
  districts: z.string().optional(),
  night_available: z.boolean().optional(),
  active: z.boolean().optional(),
  note: z.string().optional(),
});

function pageUrl(token: string): string {
  return `${config.publicBaseUrl}/m/${token}`;
}

function decorate(master: Record<string, unknown>) {
  const skills = parseJsonArray(String(master.skills || '[]'));
  return {
    ...master,
    skills_list: skills,
    page_url: pageUrl(String(master.page_token)),
    earnings: masterEarnings(Number(master.id)),
  };
}

export async function masterRoutes(app: FastifyInstance): Promise<void> {
  app.get('/masters', async () => {
    const masters = all<Record<string, unknown>>('SELECT * FROM masters ORDER BY active DESC, name');
    const orders = all<{ master_id: number; c: number }>(
      "SELECT master_id, COUNT(*) AS c FROM orders WHERE master_id IS NOT NULL AND status = 'closed' GROUP BY master_id",
    );
    const byId = new Map(orders.map((o) => [o.master_id, Number(o.c)]));
    const activeOrders = all<{ master_id: number; c: number }>(
      "SELECT master_id, COUNT(*) AS c FROM orders WHERE master_id IS NOT NULL AND status NOT IN ('closed','cancelled') GROUP BY master_id",
    );
    const activeById = new Map(activeOrders.map((o) => [o.master_id, Number(o.c)]));
    return {
      masters: masters.map((m) => ({
        ...decorate(m),
        closed_orders: byId.get(Number(m.id)) || 0,
        active_orders: activeById.get(Number(m.id)) || 0,
      })),
      skills: SKILLS,
    };
  });

  app.get('/masters/:id', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const master = get<Record<string, unknown>>('SELECT * FROM masters WHERE id = ?', [id]);
    if (!master) return reply.code(404).send({ error: 'Мастер не найден' });
    const stats = get<Record<string, unknown>>(
      `SELECT COUNT(*) AS total,
         SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed,
         SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
         SUM(CASE WHEN status = 'closed' THEN works_total ELSE 0 END) AS revenue,
         SUM(CASE WHEN status = 'closed' THEN master_earnings ELSE 0 END) AS earnings,
         AVG(CASE WHEN status = 'closed' THEN works_total END) AS avg_check
       FROM orders WHERE master_id = ?`,
      [id],
    );
    const orders = all<Record<string, unknown>>(
      `SELECT o.id, o.number, o.status, o.created_at, o.works_total, o.master_earnings,
         c.name AS client_name, c.phone AS client_phone, o.address, o.problem
       FROM orders o JOIN clients c ON c.id = o.client_id
      WHERE o.master_id = ? ORDER BY o.id DESC LIMIT 100`,
      [id],
    );
    return { master: decorate(master), stats, orders, skills: SKILLS };
  });

  app.post('/masters', async (req, reply) => {
    const admin = currentAdmin(req);
    const parsed = masterSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте данные мастера', details: parsed.error.flatten() });
    const data = parsed.data;
    const phone = normalizePhone(data.phone);
    if (get('SELECT id FROM masters WHERE phone = ?', [phone])) return reply.code(400).send({ error: 'Мастер с таким телефоном уже есть' });
    const token = newMasterToken();
    const id = run(
      `INSERT INTO masters (name, phone, telegram_id, skills, tools, districts, night_available, active, page_token, note)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        data.name.trim(),
        phone,
        data.telegram_id ?? null,
        JSON.stringify(data.skills || []),
        data.tools || '',
        data.districts || '',
        data.night_available ? 1 : 0,
        data.active === false ? 0 : 1,
        token,
        data.note || '',
      ],
    ).lastInsertRowid;
    logAction(admin.id, `Добавлен мастер ${data.name.trim()}`, 'master', id);
    broadcastAdmin({ type: 'masters:changed', at: new Date().toISOString() });
    return reply.code(201).send(decorate(get<Record<string, unknown>>('SELECT * FROM masters WHERE id = ?', [id])!));
  });

  app.put('/masters/:id', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const admin = currentAdmin(req);
    const parsed = masterSchema.partial().safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте данные мастера' });
    const data = parsed.data;
    const sets: string[] = [];
    const params: unknown[] = [];
    if (data.name != null) {
      sets.push('name = ?');
      params.push(data.name.trim());
    }
    if (data.phone != null) {
      const phone = normalizePhone(data.phone);
      const exists = get<{ id: number }>('SELECT id FROM masters WHERE phone = ? AND id <> ?', [phone, id]);
      if (exists) return reply.code(400).send({ error: 'Телефон занят другим мастером' });
      sets.push('phone = ?');
      params.push(phone);
    }
    if (data.telegram_id !== undefined) {
      sets.push('telegram_id = ?');
      params.push(data.telegram_id ?? null);
    }
    if (data.skills != null) {
      sets.push('skills = ?');
      params.push(JSON.stringify(data.skills));
    }
    if (data.tools != null) {
      sets.push('tools = ?');
      params.push(data.tools);
    }
    if (data.districts != null) {
      sets.push('districts = ?');
      params.push(data.districts);
    }
    if (data.night_available != null) {
      sets.push('night_available = ?');
      params.push(data.night_available ? 1 : 0);
    }
    if (data.active != null) {
      sets.push('active = ?');
      params.push(data.active ? 1 : 0);
      if (!data.active) {
        sets.push("status = 'offline'", 'shift_started_at = NULL');
      }
    }
    if (data.note != null) {
      sets.push('note = ?');
      params.push(data.note);
    }
    if (!sets.length) return reply.send(decorate(get<Record<string, unknown>>('SELECT * FROM masters WHERE id = ?', [id])!));
    params.push(id);
    run(`UPDATE masters SET ${sets.join(', ')} WHERE id = ?`, params);
    logAction(admin.id, `Изменён мастер #${id}${sets.length ? `: ${sets.map((s) => s.split(' = ')[0]).join(', ')}` : ''}`, 'master', id);
    broadcastAdmin({ type: 'masters:changed', at: new Date().toISOString() });
    return reply.send(decorate(get<Record<string, unknown>>('SELECT * FROM masters WHERE id = ?', [id])!));
  });

  app.delete('/masters/:id', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const admin = currentAdmin(req);
    const master = get<{ name: string }>('SELECT name FROM masters WHERE id = ?', [id]);
    run("UPDATE masters SET active = 0, status = 'offline', shift_started_at = NULL WHERE id = ?", [id]);
    freeMaster(id);
    logAction(admin.id, `Отключён мастер${master ? ` ${master.name}` : ` #${id}`}`, 'master', id);
    broadcastAdmin({ type: 'masters:changed', at: new Date().toISOString() });
    return reply.send({ ok: true });
  });

  app.post('/masters/:id/status', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const admin = currentAdmin(req);
    const schema = z.object({ status: z.enum(['free', 'busy', 'offline']) });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Некорректный статус' });
    const master = setMasterStatus(id, parsed.data.status);
    if (!master) return reply.code(404).send({ error: 'Мастер не найден' });
    const labels: Record<string, string> = { free: 'свободен', busy: 'занят', offline: 'не на линии' };
    logAction(admin.id, `Статус мастера ${master.name}: ${labels[parsed.data.status]}`, 'master', id);
    return reply.send(decorate(master as unknown as Record<string, unknown>));
  });

  app.post('/masters/:id/regenerate-token', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const admin = currentAdmin(req);
    const token = newMasterToken();
    run('UPDATE masters SET page_token = ? WHERE id = ?', [token, id]);
    logAction(admin.id, `Обновлена ссылка страницы мастера #${id}`, 'master', id);
    return reply.send({ page_url: pageUrl(token) });
  });

  app.post('/masters/:id/unlink-telegram', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const admin = currentAdmin(req);
    run('UPDATE masters SET telegram_id = NULL WHERE id = ?', [id]);
    logAction(admin.id, `Отвязан Telegram мастера #${id}`, 'master', id);
    broadcastAdmin({ type: 'masters:changed', at: new Date().toISOString() });
    return reply.send({ ok: true });
  });

  app.get('/masters/:id/orders', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const orders = all<Record<string, unknown>>(
      `SELECT o.*, c.name AS client_name, c.phone AS client_phone
       FROM orders o JOIN clients c ON c.id = o.client_id
      WHERE o.master_id = ? ORDER BY o.id DESC LIMIT 200`,
      [id],
    );
    return reply.send({ orders, master: getMaster(id) ? decorate(getMaster(id) as unknown as Record<string, unknown>) : null });
  });

  app.get('/masters/:id/earnings', async (req) => {
    const id = Number((req.params as { id: string }).id);
    const master = getMaster(id);
    const orders = all<Record<string, unknown>>(
      `SELECT o.id, o.number, o.closed_at, o.works_total, o.master_earnings, c.name AS client_name
       FROM orders o JOIN clients c ON c.id = o.client_id
      WHERE o.master_id = ? AND o.status = 'closed' ORDER BY o.closed_at DESC LIMIT 50`,
      [id],
    );
    return {
      master: master ? { id: master.id, name: master.name, phone: prettyPhone(master.phone) } : null,
      totals: masterEarnings(id),
      orders: orders.map((o) => ({ ...o, closed_at_label: formatDateTime(String(o.closed_at || '')) })),
    };
  });
}
