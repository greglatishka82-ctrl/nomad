import { all, get, run } from '../db.ts';
import { nowIso } from '../config.ts';
import { emitSupport, broadcastAdmin } from './live.ts';

export type SupportMessage = {
  id: number;
  client_id: number;
  direction: string;
  text: string;
  admin_id: number | null;
  telegram_message_id: number | null;
  read_at: string | null;
  created_at: string;
};

type SupportSender = (telegramId: number, text: string) => Promise<void>;
let sender: SupportSender | null = null;

export function setSupportSender(fn: SupportSender): void {
  sender = fn;
}

export function supportDialogs() {
  return all<Record<string, unknown>>(
    `SELECT c.id, c.name, c.phone, c.telegram_id, c.address,
       (SELECT text FROM support_messages s WHERE s.client_id = c.id ORDER BY s.id DESC LIMIT 1) AS last_text,
       (SELECT created_at FROM support_messages s WHERE s.client_id = c.id ORDER BY s.id DESC LIMIT 1) AS last_at,
       (SELECT COUNT(*) FROM support_messages s WHERE s.client_id = c.id AND s.direction = 'client' AND s.read_at IS NULL) AS unread
     FROM clients c
     WHERE EXISTS (SELECT 1 FROM support_messages s WHERE s.client_id = c.id)
     ORDER BY last_at DESC`,
  );
}

export function supportMessages(clientId: number, limit = 200): SupportMessage[] {
  return all<SupportMessage>('SELECT * FROM support_messages WHERE client_id = ? ORDER BY id DESC LIMIT ?', [clientId, limit]).reverse();
}

export function markSupportRead(clientId: number): void {
  run("UPDATE support_messages SET read_at = ? WHERE client_id = ? AND direction = 'client' AND read_at IS NULL", [nowIso(), clientId]);
  broadcastAdmin({ type: 'support:changed', clientId, at: nowIso() });
}

export function unreadSupportCount(): number {
  return Number(
    get<{ c: number }>("SELECT COUNT(DISTINCT client_id) AS c FROM support_messages WHERE direction = 'client' AND read_at IS NULL")?.c || 0,
  );
}

export function addClientMessage(clientId: number, text: string, telegramMessageId?: number): SupportMessage {
  const id = run(
    'INSERT INTO support_messages (client_id, direction, text, telegram_message_id) VALUES (?, ?, ?, ?)',
    [clientId, 'client', text, telegramMessageId ?? null],
  ).lastInsertRowid;
  emitSupport(clientId, id);
  return get<SupportMessage>('SELECT * FROM support_messages WHERE id = ?', [id])!;
}

export async function addAdminReply(clientId: number, text: string, adminId: number): Promise<SupportMessage> {
  const client = get<{ id: number; telegram_id: number | null; name: string }>('SELECT id, telegram_id, name FROM clients WHERE id = ?', [clientId]);
  if (!client) throw new Error('Клиент не найден');
  if (!client.telegram_id) throw new Error('У клиента не привязан Telegram');
  if (!sender) throw new Error('Бот не запущен');
  await sender(client.telegram_id, text);
  const id = run(
    'INSERT INTO support_messages (client_id, direction, text, admin_id) VALUES (?, ?, ?, ?)',
    [clientId, 'admin', text, adminId],
  ).lastInsertRowid;
  markSupportRead(clientId);
  broadcastAdmin({ type: 'support:changed', clientId, at: nowIso() });
  return get<SupportMessage>('SELECT * FROM support_messages WHERE id = ?', [id])!;
}
