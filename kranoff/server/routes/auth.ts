import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { get, run, logAction } from '../db.ts';
import {
  SESSION_COOKIE,
  activeShift,
  adminFromToken,
  adminsOnShift,
  createSession,
  destroySession,
  endShift,
  hashPassword,
  requireAdmin,
  startShift,
  verifyPassword,
  currentAdmin,
  type Admin,
} from '../lib/auth.ts';

const loginSchema = z.object({
  login: z.string().min(1).max(80),
  password: z.string().min(1).max(200),
});

export async function authRoutes(app: FastifyInstance): Promise<void> {
  app.post('/login', async (req, reply) => {
    const parsed = loginSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Введите логин и пароль' });
    const row = get<Admin & { password_hash: string }>('SELECT * FROM admins WHERE login = ?', [parsed.data.login.trim()]);
    if (!row || !verifyPassword(parsed.data.password, row.password_hash)) {
      logAction(null, `Неудачная попытка входа: ${parsed.data.login.trim()}`, 'auth');
      return reply.code(401).send({ error: 'Неверный логин или пароль' });
    }
    if (!row.is_active) return reply.code(403).send({ error: 'Доступ отключён' });
    const session = createSession(row.id);
    reply.setCookie(SESSION_COOKIE, session.token, {
      path: '/',
      httpOnly: true,
      sameSite: 'lax',
      maxAge: 30 * 24 * 3600,
      secure: false,
    });
    const admin = { id: row.id, login: row.login, name: row.name, is_active: row.is_active };
    const shift = activeShift(row.id);
    logAction(row.id, 'Вход в панель', 'admin', row.id);
    return reply.send({ admin, shift, needShift: !shift });
  });

  app.post('/logout', async (req, reply) => {
    const token = (req.cookies as Record<string, string | undefined> | undefined)?.[SESSION_COOKIE];
    const sessionAdmin = token ? adminFromToken(token) : null;
    destroySession(token);
    if (sessionAdmin) logAction(sessionAdmin.id, 'Выход из панели', 'admin', sessionAdmin.id);
    reply.clearCookie(SESSION_COOKIE, { path: '/' });
    return reply.send({ ok: true });
  });

  app.get('/me', { preHandler: requireAdmin }, async (req) => {
    const admin = currentAdmin(req);
    const token = (req.cookies as Record<string, string | undefined>)[SESSION_COOKIE];
    const sessionAdmin = adminFromToken(token);
    return {
      admin: sessionAdmin || admin,
      shift: activeShift(admin.id),
      onShift: adminsOnShift(),
    };
  });

  app.post('/shift/start', { preHandler: requireAdmin }, async (req) => {
    const admin = currentAdmin(req);
    const shift = startShift(admin.id);
    logAction(admin.id, 'Начало смены', 'shift', shift.id);
    return { shift, onShift: adminsOnShift() };
  });

  app.post('/shift/end', { preHandler: requireAdmin }, async (req) => {
    const admin = currentAdmin(req);
    const shift = endShift(admin.id);
    if (shift) logAction(admin.id, 'Завершение смены', 'shift', shift.id);
    return { shift, onShift: adminsOnShift() };
  });

  app.post('/password', { preHandler: requireAdmin }, async (req, reply) => {
    const admin = currentAdmin(req);
    const schema = z.object({ current: z.string().min(1), next: z.string().min(6).max(200) });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Пароль должен быть не короче 6 символов' });
    const row = get<{ password_hash: string }>('SELECT password_hash FROM admins WHERE id = ?', [admin.id]);
    if (!row || !verifyPassword(parsed.data.current, row.password_hash)) {
      return reply.code(400).send({ error: 'Текущий пароль неверен' });
    }
    run('UPDATE admins SET password_hash = ? WHERE id = ?', [hashPassword(parsed.data.next), admin.id]);
    run('DELETE FROM admin_sessions WHERE admin_id = ?', [admin.id]);
    logAction(admin.id, 'Смена пароля администратора', 'admin', admin.id);
    reply.clearCookie(SESSION_COOKIE, { path: '/' });
    return reply.send({ ok: true });
  });
}
