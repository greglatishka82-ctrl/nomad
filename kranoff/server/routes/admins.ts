import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { all, get, logAction, run } from '../db.ts';
import { currentAdmin, hashPassword, adminsOnShift } from '../lib/auth.ts';
import { nowIso } from '../config.ts';

export async function adminRoutes(app: FastifyInstance): Promise<void> {
  app.get('/admins', async () => {
    const admins = all<Record<string, unknown>>(
      `SELECT a.id, a.login, a.name, a.is_active, a.created_at,
         (SELECT s.id FROM shifts s WHERE s.admin_id = a.id AND s.ended_at IS NULL ORDER BY s.id DESC LIMIT 1) AS shift_id,
         (SELECT s.started_at FROM shifts s WHERE s.admin_id = a.id AND s.ended_at IS NULL ORDER BY s.id DESC LIMIT 1) AS shift_started_at,
         (SELECT COUNT(*) FROM orders o WHERE o.admin_id = a.id) AS orders_created
       FROM admins a ORDER BY a.id`,
    );
    return { admins, onShift: adminsOnShift() };
  });

  app.post('/admins', async (req, reply) => {
    const admin = currentAdmin(req);
    const schema = z.object({
      login: z.string().min(3).max(40).regex(/^[a-zA-Z0-9_.-]+$/),
      password: z.string().min(6).max(200),
      name: z.string().min(1).max(120),
    });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Логин от 3 символов, пароль от 6' });
    if (get('SELECT id FROM admins WHERE login = ?', [parsed.data.login])) return reply.code(400).send({ error: 'Логин занят' });
    const id = run('INSERT INTO admins (login, password_hash, name) VALUES (?, ?, ?)', [
      parsed.data.login,
      hashPassword(parsed.data.password),
      parsed.data.name.trim(),
    ]).lastInsertRowid;
    logAction(admin.id, `Добавлен администратор ${parsed.data.name.trim()} (${parsed.data.login})`, 'admin', id);
    return reply.code(201).send(get('SELECT id, login, name, is_active, created_at FROM admins WHERE id = ?', [id]));
  });

  app.put('/admins/:id', async (req, reply) => {
    const id = Number((req.params as { id: string }).id);
    const me = currentAdmin(req);
    const schema = z.object({
      name: z.string().min(1).max(120).optional(),
      is_active: z.boolean().optional(),
      password: z.string().min(6).max(200).optional(),
    });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте данные' });
    const target = get<{ id: number; is_active: number; login: string; name: string }>('SELECT id, is_active, login, name FROM admins WHERE id = ?', [id]);
    if (!target) return reply.code(404).send({ error: 'Администратор не найден' });
    if (parsed.data.is_active === false) {
      if (id === me.id) return reply.code(400).send({ error: 'Нельзя отключить самого себя' });
      const active = Number(get<{ c: number }>('SELECT COUNT(*) AS c FROM admins WHERE is_active = 1')?.c || 0);
      if (active <= 1) return reply.code(400).send({ error: 'Нельзя отключить последнего администратора' });
    }
    const sets: string[] = [];
    const params: unknown[] = [];
    if (parsed.data.name != null) {
      sets.push('name = ?');
      params.push(parsed.data.name.trim());
    }
    if (parsed.data.is_active != null) {
      sets.push('is_active = ?');
      params.push(parsed.data.is_active ? 1 : 0);
    }
    if (parsed.data.password) {
      sets.push('password_hash = ?');
      params.push(hashPassword(parsed.data.password));
      run('DELETE FROM admin_sessions WHERE admin_id = ? AND admin_id <> ?', [id, me.id]);
    }
    if (sets.length) {
      params.push(id);
      run(`UPDATE admins SET ${sets.join(', ')} WHERE id = ?`, params);
      logAction(me.id, `Изменён администратор ${target?.name || `#${id}`}${parsed.data.password ? ' (сменён пароль)' : ''}`, 'admin', id);
    }
    return reply.send(get('SELECT id, login, name, is_active, created_at FROM admins WHERE id = ?', [id]));
  });

  app.get('/shifts', async () => {
    const shifts = all<Record<string, unknown>>(
      `SELECT s.id, s.admin_id, s.started_at, s.ended_at, a.name AS admin_name,
         CAST((julianday(COALESCE(s.ended_at, ?)) - julianday(s.started_at)) * 1440 AS INTEGER) AS minutes,
         (SELECT COUNT(*) FROM orders o WHERE o.shift_id = s.id) AS orders,
         (SELECT SUM(o.works_total) FROM orders o WHERE o.shift_id = s.id AND o.status = 'closed') AS revenue
       FROM shifts s JOIN admins a ON a.id = s.admin_id
       ORDER BY s.started_at DESC LIMIT 100`,
      [nowIso()],
    );
    return { shifts, onShift: adminsOnShift() };
  });
}
