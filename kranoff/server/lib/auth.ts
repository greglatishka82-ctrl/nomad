import bcrypt from 'bcryptjs';
import { randomBytes } from 'node:crypto';
import type { FastifyReply, FastifyRequest } from 'fastify';
import { all, get, run } from '../db.ts';
import { nowIso } from '../config.ts';

export const SESSION_COOKIE = 'kranoff_session';
const SESSION_DAYS = 30;

export type Admin = {
  id: number;
  login: string;
  name: string;
  is_active: number;
};

export type Shift = {
  id: number;
  admin_id: number;
  started_at: string;
  ended_at: string | null;
};

export function hashPassword(password: string): string {
  return bcrypt.hashSync(password, 10);
}

export function verifyPassword(password: string, hash: string): boolean {
  try {
    return bcrypt.compareSync(password, hash);
  } catch {
    return false;
  }
}

export function createSession(adminId: number): { token: string; expiresAt: string } {
  const token = randomBytes(32).toString('hex');
  const expires = new Date(Date.now() + SESSION_DAYS * 24 * 60 * 60 * 1000);
  run('INSERT INTO admin_sessions (token, admin_id, expires_at) VALUES (?, ?, ?)', [token, adminId, expires.toISOString()]);
  return { token, expiresAt: expires.toISOString() };
}

export function destroySession(token: string | undefined): void {
  if (!token) return;
  run('DELETE FROM admin_sessions WHERE token = ?', [token]);
}

export function adminFromToken(token: string | undefined): Admin | null {
  if (!token) return null;
  const row = get<Admin & { expires_at: string }>(
    `SELECT a.id, a.login, a.name, a.is_active, s.expires_at
       FROM admin_sessions s JOIN admins a ON a.id = s.admin_id
      WHERE s.token = ?`,
    [token],
  );
  if (!row) return null;
  if (new Date(row.expires_at).getTime() < Date.now()) {
    run('DELETE FROM admin_sessions WHERE token = ?', [token]);
    return null;
  }
  return { id: row.id, login: row.login, name: row.name, is_active: row.is_active };
}

export function requireAdmin(req: FastifyRequest, reply: FastifyReply, done: (err?: Error) => void): void {
  const token = (req.cookies as Record<string, string | undefined> | undefined)?.[SESSION_COOKIE];
  const admin = adminFromToken(token);
  if (!admin || !admin.is_active) {
    reply.code(401).send({ error: 'unauthorized' });
    return;
  }
  (req as FastifyRequest & { admin?: Admin }).admin = admin;
  done();
}

export function currentAdmin(req: FastifyRequest): Admin {
  const admin = (req as FastifyRequest & { admin?: Admin }).admin;
  if (!admin) throw new Error('admin is not attached to request');
  return admin;
}

export function activeShift(adminId: number): Shift | null {
  return (
    get<Shift>('SELECT * FROM shifts WHERE admin_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1', [adminId]) || null
  );
}

export function startShift(adminId: number): Shift {
  const existing = activeShift(adminId);
  if (existing) return existing;
  const id = run('INSERT INTO shifts (admin_id, started_at) VALUES (?, ?)', [adminId, nowIso()]).lastInsertRowid;
  return get<Shift>('SELECT * FROM shifts WHERE id = ?', [id])!;
}

export function endShift(adminId: number): Shift | null {
  const shift = activeShift(adminId);
  if (!shift) return null;
  run('UPDATE shifts SET ended_at = ? WHERE id = ?', [nowIso(), shift.id]);
  return { ...shift, ended_at: nowIso() };
}

export function shiftStats(shiftId: number): { orders: number; total: number } {
  const row = get<{ orders: number; total: number | null }>(
    'SELECT COUNT(*) AS orders, SUM(works_total) AS total FROM orders WHERE shift_id = ?',
    [shiftId],
  );
  return { orders: Number(row?.orders || 0), total: Number(row?.total || 0) };
}

export function adminsOnShift(): Array<Admin & { shift_id: number; shift_started_at: string }> {
  return all<Admin & { shift_id: number; shift_started_at: string }>(
    `SELECT a.id, a.login, a.name, a.is_active, s.id AS shift_id, s.started_at AS shift_started_at
       FROM shifts s JOIN admins a ON a.id = s.admin_id
      WHERE s.ended_at IS NULL
      ORDER BY s.started_at`,
  );
}

export function cleanupSessions(): void {
  run('DELETE FROM admin_sessions WHERE expires_at < ?', [nowIso()]);
}
