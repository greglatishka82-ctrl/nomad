import type { FastifyInstance } from 'fastify';
import { all, get } from '../db.ts';
import { requireAdmin } from '../lib/auth.ts';
import { describeLog, type ActivityRow } from '../lib/logs.ts';

const PAGE_SIZE = 50;

export async function logsRoutes(app: FastifyInstance): Promise<void> {
  app.get('/logs', { preHandler: requireAdmin }, async (req) => {
    const query = req.query as { page?: string };
    const total = Number(get<{ c: number }>('SELECT COUNT(*) AS c FROM activity_log')?.c || 0);
    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    let page = Math.max(1, Math.min(pages, Number(query.page) || 1));
    const offset = (page - 1) * PAGE_SIZE;
    const rows = all<ActivityRow>(
      `SELECT l.id, l.admin_id, l.action, l.entity, l.entity_id, l.details, l.created_at,
              a.name AS admin_name
         FROM activity_log l
         LEFT JOIN admins a ON a.id = l.admin_id
        ORDER BY l.id DESC
        LIMIT ? OFFSET ?`,
      [PAGE_SIZE, offset],
    );
    return {
      items: rows.map((row) => ({
        ...row,
        admin_name: row.admin_name || 'Неизвестный',
        description: describeLog(row),
      })),
      page,
      pages,
      pageSize: PAGE_SIZE,
      total,
    };
  });
}
