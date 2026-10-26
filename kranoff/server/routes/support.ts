import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { get, logAction } from '../db.ts';
import { currentAdmin } from '../lib/auth.ts';
import { addAdminReply, markSupportRead, supportDialogs, supportMessages } from '../lib/support.ts';

export async function supportRoutes(app: FastifyInstance): Promise<void> {
  app.get('/support', async () => ({ dialogs: supportDialogs() }));

  app.get('/support/:clientId', async (req, reply) => {
    const clientId = Number((req.params as { clientId: string }).clientId);
    const client = get<Record<string, unknown>>('SELECT * FROM clients WHERE id = ?', [clientId]);
    if (!client) return reply.code(404).send({ error: 'Клиент не найден' });
    return reply.send({ client, messages: supportMessages(clientId) });
  });

  app.post('/support/:clientId/read', async (req) => {
    const clientId = Number((req.params as { clientId: string }).clientId);
    const admin = currentAdmin(req);
    const client = get<{ name: string }>('SELECT name FROM clients WHERE id = ?', [clientId]);
    markSupportRead(clientId);
    logAction(admin.id, `Открыт диалог поддержки: ${client?.name || `#${clientId}`}`, 'client', clientId);
    return { ok: true };
  });

  app.post('/support/:clientId/reply', async (req, reply) => {
    const clientId = Number((req.params as { clientId: string }).clientId);
    const admin = currentAdmin(req);
    const parsed = z.object({ text: z.string().min(1).max(4000) }).safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Введите текст' });
    try {
      const message = await addAdminReply(clientId, parsed.data.text.trim(), admin.id);
      const client = get<{ name: string }>('SELECT name FROM clients WHERE id = ?', [clientId]);
      logAction(admin.id, `Ответ клиенту ${client?.name || `#${clientId}`}: ${parsed.data.text.trim().slice(0, 160)}`, 'client', clientId);
      return reply.send({ message });
    } catch (error) {
      return reply.code(502).send({ error: error instanceof Error ? error.message : 'Не удалось отправить сообщение' });
    }
  });
}
