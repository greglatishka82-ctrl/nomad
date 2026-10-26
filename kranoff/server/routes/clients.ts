import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { all, get, logAction, run } from '../db.ts';
import { currentAdmin } from '../lib/auth.ts';
import { normalizePhone, prettyPhone } from '../lib/utils.ts';

const clientSchema = z.object({
  name: z.string().min(1).max(160),
  phone: z.string().min(5).max(40),
  address: z.string().optional(),
  note: z.string().optional(),
  language: z.enum(['ru', 'kk', 'en']).optional(),
});

export async function clientRoutes(app: FastifyInstance): Promise<void> {
  app.get('/clients', async (req) => {
    const q = String((req.query as { q?: string }).q || '').trim();
    const limit = Math.min(200, Math.max(1, Number((req.query as { limit?: string }).limit || 100)));
    const params: unknown[] = [];
    let where = '';
    if (q) {
      const digits = q.replace(/\D/g, '');
      const normalized = digits ? normalizePhone(digits) : '';
      where = 'WHERE c.name LIKE ? OR c.phone LIKE ? OR c.phone LIKE ? OR c.address LIKE ?';
      params.push(`%${q}%`, `%${q}%`, normalized ? `%${normalized}%` : `%${q}%`, `%${q}%`);
    }
    const rows = all<Record<string, unknown>>(
      `SELECT c.*,
         (SELECT COUNT(*) FROM orders o WHERE o.client_id = c.id) AS orders_count,
         (SELECT SUM(o.works_total) FROM orders o WHERE o.client_id = c.id AND o.status = 'closed') AS total_spent,
         (SELECT MAX(o.created_at) FROM orders o WHERE o.client_id = c.id) AS last_order_at,
         (SELECT COUNT(*) FROM support_messages s WHERE s.client_id = c.id AND s.direction = 'client' AND s.read_at IS NULL) AS unread
       FROM clients c ${where} ORDER BY c.updated_at DESC LIMIT ?`,
      [...params, limit],
    );
    return { clients: rows.map((c) => ({ ...c, phone_pretty: prettyPhone(String(c.phone || '')) })) };
  });

  app.get('/clients/:id', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const client = get<Record<string, unknown>>('SELECT * FROM clients WHERE id = ?', [id]);
    if (!client) return reply.code(404).send({ error: 'Клиент не найден' });
    const orders = all<Record<string, unknown>>(
      `SELECT o.*, m.name AS master_name FROM orders o LEFT JOIN masters m ON m.id = o.master_id
        WHERE o.client_id = ? ORDER BY o.id DESC`,
      [id],
    );
    return reply.send({ client: { ...client, phone_pretty: prettyPhone(String(client.phone || '')) }, orders });
  });

  app.post('/clients', async (req, reply) => {
    const admin = currentAdmin(req);
    const parsed = clientSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте данные клиента' });
    const phone = normalizePhone(parsed.data.phone);
    const existing = get<{ id: number }>('SELECT id FROM clients WHERE phone = ?', [phone]);
    if (existing) return reply.code(400).send({ error: 'Клиент с таким телефоном уже существует' });
    const id = run('INSERT INTO clients (name, phone, address, note, language) VALUES (?, ?, ?, ?, ?)', [
      parsed.data.name.trim(),
      phone,
      parsed.data.address || '',
      parsed.data.note || '',
      parsed.data.language || 'ru',
    ]).lastInsertRowid;
    logAction(admin.id, `Добавлен клиент ${parsed.data.name.trim()} (${phone})`, 'client', id);
    return reply.code(201).send(get('SELECT * FROM clients WHERE id = ?', [id]));
  });

  app.put('/clients/:id', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const admin = currentAdmin(req);
    const parsed = clientSchema.partial().safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте данные клиента' });
    const data = parsed.data;
    const sets: string[] = [];
    const params: unknown[] = [];
    if (data.name != null) {
      sets.push('name = ?');
      params.push(data.name.trim());
    }
    if (data.phone != null) {
      const phone = normalizePhone(data.phone);
      const existing = get<{ id: number }>('SELECT id FROM clients WHERE phone = ? AND id <> ?', [phone, id]);
      if (existing) return reply.code(400).send({ error: 'Телефон занят другим клиентом' });
      sets.push('phone = ?');
      params.push(phone);
    }
    if (data.address != null) {
      sets.push('address = ?');
      params.push(data.address);
    }
    if (data.note != null) {
      sets.push('note = ?');
      params.push(data.note);
    }
    if (data.language != null) {
      sets.push('language = ?');
      params.push(data.language);
    }
    if (sets.length) {
      sets.push("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')");
      params.push(id);
      run(`UPDATE clients SET ${sets.join(', ')} WHERE id = ?`, params);
      logAction(admin.id, `Изменён клиент #${id}: ${sets.map((s) => s.split(' = ')[0]).filter((k) => k !== 'updated_at').join(', ')}`, 'client', id);
    }
    return reply.send(get('SELECT * FROM clients WHERE id = ?', [id]));
  });
}
