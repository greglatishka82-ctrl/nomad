import fs from 'node:fs';
import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { all, get, logAction, run, tx } from '../db.ts';
import { currentAdmin } from '../lib/auth.ts';
import { activeShift } from '../lib/auth.ts';
import {
  assignMaster,
  cancelOrder,
  closeOrder,
  createOrder,
  dashboard,
  getOrderFull,
  listOrders,
  masterAction,
  replaceOrderItems,
  setEta,
  setOrderFields,
  setOrderStatus,
  suggestMasters,
  upsertClient,
  type OrderFull,
} from '../lib/orders.ts';
import { createOrderPdf } from '../lib/pdf.ts';
import { createArchive } from '../lib/archive.ts';
import { deleteMediaForOrders, deleteMediaRows, getMedia, listMedia, saveMediaBuffer } from '../lib/media.ts';
import { geocode } from '../lib/geo.ts';
import { STATUS_LABELS, normalizePhone } from '../lib/utils.ts';
import { broadcastAdmin } from '../lib/live.ts';

const itemSchema = z.object({
  serviceId: z.number().int().nullable().optional(),
  name: z.string().min(1),
  price: z.number().int().min(0),
  qty: z.number().int().min(1).default(1),
  isExtra: z.boolean().optional(),
});

const createSchema = z.object({
  clientId: z.number().int().positive().optional(),
  client: z
    .object({
      name: z.string().min(1),
      phone: z.string().min(5),
      address: z.string().optional(),
    })
    .optional(),
  problem: z.string().min(1),
  description: z.string().optional(),
  address: z.string().optional(),
  lat: z.number().nullable().optional(),
  lng: z.number().nullable().optional(),
  entrance: z.string().optional(),
  floor: z.string().optional(),
  intercom: z.string().optional(),
  contactNote: z.string().optional(),
  desiredAt: z.string().nullable().optional(),
  desiredLabel: z.string().optional(),
  urgency: z.enum(['normal', 'urgent', 'emergency']).default('normal'),
  isEmergency: z.boolean().default(false),
  tariff: z.enum(['day', 'night', 'emergency_day', 'emergency_night']).optional(),
  items: z.array(itemSchema).min(1),
  materialsNote: z.string().optional(),
  guaranteeMonths: z.number().int().min(0).max(120).nullable().optional(),
  source: z.enum(['phone', 'whatsapp', 'admin', 'telegram']).default('phone'),
});

const updateSchema = z.object({
  problem: z.string().optional(),
  description: z.string().optional(),
  address: z.string().optional(),
  lat: z.number().nullable().optional(),
  lng: z.number().nullable().optional(),
  entrance: z.string().optional(),
  floor: z.string().optional(),
  intercom: z.string().optional(),
  contact_note: z.string().optional(),
  desired_at: z.string().nullable().optional(),
  desired_label: z.string().optional(),
  urgency: z.enum(['normal', 'urgent', 'emergency']).optional(),
  is_emergency: z.union([z.number(), z.boolean()]).optional(),
  tariff: z.enum(['day', 'night', 'emergency_day', 'emergency_night']).optional(),
  works_total: z.number().int().min(0).optional(),
  materials_note: z.string().optional(),
  guarantee_months: z.number().int().min(0).max(120).nullable().optional(),
  eta_minutes: z.number().int().min(0).max(1440).nullable().optional(),
  items: z.array(itemSchema).optional(),
});

function num(value: unknown): number {
  return Number(value);
}

function orderId(req: { params: unknown }): number {
  return Number((req.params as { id: string }).id);
}

function sendFull(reply: import('fastify').FastifyReply, full: OrderFull | null) {
  if (!full) return reply.code(404).send({ error: 'Заказ не найден' });
  return reply.send(full);
}

export async function orderRoutes(app: FastifyInstance): Promise<void> {
  app.get('/dashboard', async () => dashboard());

  app.get('/orders', async (req) => {
    const q = req.query as Record<string, string | undefined>;
    const result = listOrders({
      status: q.status,
      source: q.source,
      q: q.q,
      from: q.from,
      to: q.to,
      masterId: q.masterId ? Number(q.masterId) : undefined,
      clientId: q.clientId ? Number(q.clientId) : undefined,
      emergency: q.emergency === '1',
      limit: q.limit ? Number(q.limit) : 50,
      offset: q.offset ? Number(q.offset) : 0,
    });
    return result;
  });

  app.post('/orders', async (req, reply) => {
    const admin = currentAdmin(req);
    const parsed = createSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте поля заказа', details: parsed.error.flatten() });
    const body = parsed.data;
    let clientId = body.clientId;
    if (!clientId) {
      if (!body.client) return reply.code(400).send({ error: 'Укажите клиента или его имя и телефон' });
      const client = upsertClient({
        name: body.client.name,
        phone: normalizePhone(body.client.phone),
        address: body.client.address || body.address || '',
      });
      clientId = client.id;
    }
    const shift = activeShift(admin.id);
    const full = createOrder({
      clientId,
      source: body.source,
      problem: body.problem,
      description: body.description,
      address: body.address,
      lat: body.lat ?? null,
      lng: body.lng ?? null,
      entrance: body.entrance,
      floor: body.floor,
      intercom: body.intercom,
      contactNote: body.contactNote,
      desiredAt: body.desiredAt ?? null,
      desiredLabel: body.desiredLabel,
      urgency: body.urgency,
      isEmergency: body.isEmergency,
      tariff: body.tariff,
      items: body.items,
      adminId: admin.id,
      shiftId: shift?.id ?? null,
      status: 'confirmed',
      materialsNote: body.materialsNote,
    });
    if (body.guaranteeMonths != null) {
      run('UPDATE orders SET guarantee_months = ? WHERE id = ?', [body.guaranteeMonths, full.id]);
    }
    logAction(admin.id, `Создан заказ ${full.number}: ${full.client.name}, ${full.problem}`, 'order', full.id);
    return reply.code(201).send(getOrderFull(full.id));
  });

  app.get('/orders/:id', async (req, reply) => sendFull(reply, getOrderFull(orderId(req))));

  app.put('/orders/:id', async (req, reply) => {
    const id = orderId(req);
    const admin = currentAdmin(req);
    const parsed = updateSchema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте поля', details: parsed.error.flatten() });
    const body = parsed.data;
    const fields: Record<string, unknown> = {};
    for (const key of [
      'problem',
      'description',
      'address',
      'lat',
      'lng',
      'entrance',
      'floor',
      'intercom',
      'contact_note',
      'desired_at',
      'desired_label',
      'urgency',
      'is_emergency',
      'tariff',
      'works_total',
      'materials_note',
      'guarantee_months',
      'eta_minutes',
    ]) {
      if (key in body) fields[key] = (body as Record<string, unknown>)[key];
    }
    if (typeof fields.is_emergency === 'boolean') fields.is_emergency = fields.is_emergency ? 1 : 0;
    if (body.items) replaceOrderItems(id, body.items.map((i) => ({ ...i, qty: i.qty ?? 1 })), `admin:${admin.id}`);
    setOrderFields(id, fields, `admin:${admin.id}`);
    const labels: Record<string, string> = {
      problem: 'проблема',
      description: 'описание',
      address: 'адрес',
      lat: 'координаты',
      lng: 'координаты',
      entrance: 'подъезд',
      floor: 'этаж',
      intercom: 'домофон',
      contact_note: 'контактное лицо',
      desired_at: 'желаемое время',
      desired_label: 'желаемое время',
      urgency: 'срочность',
      is_emergency: 'аварийность',
      tariff: 'тариф',
      works_total: 'сумма работ',
      materials_note: 'материалы',
      guarantee_months: 'гарантия',
      eta_minutes: 'время прибытия',
    };
    const changed = Object.keys(fields).filter((key) => key !== 'items').map((key) => labels[key] || key);
    logAction(admin.id, `Изменена карточка заказа${changed.length ? `: ${[...new Set(changed)].join(', ')}` : ''}`, 'order', id);
    return sendFull(reply, getOrderFull(id));
  });

  app.post('/orders/:id/items', async (req, reply) => {
    const id = orderId(req);
    const admin = currentAdmin(req);
    const schema = z.object({ items: z.array(itemSchema) });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Некорректный состав работ' });
    replaceOrderItems(id, parsed.data.items.map((i) => ({ ...i, qty: i.qty ?? 1 })), `admin:${admin.id}`);
    logAction(admin.id, `Изменён состав работ: ${parsed.data.items.length} позиций`, 'order', id);
    return sendFull(reply, getOrderFull(id));
  });

  app.get('/orders/:id/suggest', async (req) => {
    const id = orderId(req);
    return { masters: suggestMasters(id) };
  });

  app.post('/orders/:id/assign', async (req, reply) => {
    const id = orderId(req);
    const admin = currentAdmin(req);
    const schema = z.object({ masterId: z.number().int().positive(), mode: z.enum(['offer', 'manual']).default('offer') });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Выберите мастера' });
    const result = assignMaster(id, parsed.data.masterId, admin.id, parsed.data.mode);
    if (!result.ok) return reply.code(400).send({ error: result.error });
    return sendFull(reply, getOrderFull(id));
  });

  app.post('/orders/:id/status', async (req, reply) => {
    const id = orderId(req);
    const admin = currentAdmin(req);
    const schema = z.object({ status: z.string().min(2), text: z.string().optional() });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Некорректный статус' });
    if (!(parsed.data.status in STATUS_LABELS)) return reply.code(400).send({ error: 'Неизвестный статус' });
    const result = setOrderStatus(id, parsed.data.status, `admin:${admin.id}`, parsed.data.text || '', admin.id);
    if (!result.ok) return reply.code(400).send({ error: result.error });
    return sendFull(reply, getOrderFull(id));
  });

  app.post('/orders/:id/cancel', async (req, reply) => {
    const id = orderId(req);
    const admin = currentAdmin(req);
    const result = cancelOrder(id, `admin:${admin.id}`, admin.id, false);
    if (!result.ok) return reply.code(400).send({ error: result.error });
    return sendFull(reply, getOrderFull(id));
  });

  app.delete('/orders/:id', async (req, reply) => {
    const id = orderId(req);
    const admin = currentAdmin(req);
    const order = getOrderFull(id);
    if (!order) return reply.code(404).send({ error: 'Заказ не найден' });
    const media = deleteMediaForOrders([id]);
    tx(() => {
      if (order.master_id && !['closed', 'cancelled'].includes(order.status)) {
        const other = get<{ id: number }>(
          "SELECT id FROM orders WHERE master_id = ? AND id <> ? AND status NOT IN ('closed','cancelled') LIMIT 1",
          [order.master_id, id],
        );
        if (!other) {
          run("UPDATE masters SET status = 'free', updated_at = ? WHERE id = ?", [new Date().toISOString(), order.master_id]);
        }
      }
      run('DELETE FROM orders WHERE id = ?', [id]);
    });
    logAction(
      admin.id,
      `Удалён заказ ${order.number}: ${order.client.name}, ${order.problem}${media.deleted ? `, файлов: ${media.deleted}` : ''}`,
      'order',
      id,
    );
    broadcastAdmin({ type: 'order:deleted', orderId: id, number: order.number });
    return reply.send({ ok: true, deleted: media.deleted });
  });

  app.post('/orders/:id/close', async (req, reply) => {
    const id = orderId(req);
    const admin = currentAdmin(req);
    const schema = z.object({
      worksTotal: z.number().int().min(0).optional(),
      guaranteeMonths: z.number().int().min(0).max(120).nullable().optional(),
      materialsNote: z.string().optional(),
    });
    const parsed = schema.safeParse(req.body || {});
    if (!parsed.success) return reply.code(400).send({ error: 'Проверьте сумму' });
    const result = closeOrder(id, admin.id, parsed.data.worksTotal, parsed.data.guaranteeMonths, parsed.data.materialsNote);
    if (!result.ok) return reply.code(400).send({ error: result.error });
    return sendFull(reply, getOrderFull(id));
  });

  app.post('/orders/:id/eta', async (req, reply) => {
    const id = orderId(req);
    const admin = currentAdmin(req);
    const schema = z.object({ minutes: z.number().int().min(0).max(1440) });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Укажите время в минутах' });
    setEta(id, parsed.data.minutes);
    logAction(admin.id, `Сообщено время прибытия: ${parsed.data.minutes} мин`, 'order', id);
    return sendFull(reply, getOrderFull(id));
  });

  app.post('/orders/:id/master-action', async (req, reply) => {
    const id = orderId(req);
    const admin = currentAdmin(req);
    const schema = z.object({ action: z.enum(['on_way', 'arrived', 'start', 'done', 'cancel']) });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Действие не распознано' });
    const order = getOrderFull(id);
    if (!order?.master_id) return reply.code(400).send({ error: 'У заказа нет мастера' });
    const result = masterAction(id, order.master_id, parsed.data.action);
    if (!result.ok) return reply.code(400).send({ error: result.error });
    const labels: Record<string, string> = {
      on_way: 'мастер выехал',
      arrived: 'мастер на месте',
      start: 'работы начаты',
      done: 'мастер выполнил работы',
      cancel: 'мастер снял заказ',
    };
    logAction(admin.id, `Действие по заказу ${order.number}: ${labels[parsed.data.action]}`, 'order', id);
    return sendFull(reply, getOrderFull(id));
  });

  app.get('/orders/:id/pdf', async (req, reply) => {
    const full = getOrderFull(orderId(req));
    if (!full) return reply.code(404).send({ error: 'Заказ не найден' });
    const pdf = await createOrderPdf(full);
    logAction(currentAdmin(req).id, `Скачана PDF-карточка ${full.number}`, 'order', full.id);
    reply.header('Content-Type', 'application/pdf');
    reply.header('Content-Disposition', `inline; filename="${full.number}.pdf"`);
    return reply.send(pdf);
  });

  app.post('/orders/:id/media', async (req, reply) => {
    const id = orderId(req);
    const admin = currentAdmin(req);
    const full = getOrderFull(id);
    if (!full) return reply.code(404).send({ error: 'Заказ не найден' });
    const part = await req.file();
    if (!part) return reply.code(400).send({ error: 'Файл не получен' });
    const kind = String((part.fields as Record<string, { value?: unknown }> | undefined)?.kind?.value || 'before');
    const buffer = await part.toBuffer();
    const media = await saveMediaBuffer(id, buffer, part.mimetype || 'application/octet-stream', kind);
    logAction(admin.id, `Загружен файл (${kind}): ${part.filename || media.file_name}`, 'order', id);
    return reply.code(201).send(media);
  });

  app.get('/media/:id', async (req, reply) => {
    const media = getMedia(Number((req.params as { id: string }).id));
    if (!media) return reply.code(404).send({ error: 'Файл не найден' });
    const { mediaAbsolutePath } = await import('../lib/media.ts');
    const filePath = mediaAbsolutePath(media);
    if (!fs.existsSync(filePath)) return reply.code(404).send({ error: 'Файл отсутствует' });
    reply.header('Content-Type', media.mime || 'application/octet-stream');
    reply.header('Content-Disposition', `inline; filename="${media.id}"`);
    return reply.send(fs.createReadStream(filePath));
  });

  app.delete('/media/:id', async (req, reply) => {
    const media = getMedia(Number((req.params as { id: string }).id));
    if (!media) return reply.code(404).send({ error: 'Файл не найден' });
    const admin = currentAdmin(req);
    deleteMediaRows([media]);
    logAction(admin.id, `Удалён файл заказа ${media.order_id}: ${media.file_name}`, 'order', media.order_id);
    return reply.send({ ok: true });
  });

  app.post('/orders/media/delete', async (req, reply) => {
    const admin = currentAdmin(req);
    const schema = z.object({ orderIds: z.array(z.number().int().positive()).min(1) });
    const parsed = schema.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: 'Выберите заказы' });
    const result = deleteMediaForOrders(parsed.data.orderIds);
    logAction(admin.id, `Удалены медиа у заказов: ${parsed.data.orderIds.join(', ')} (файлов: ${result.deleted})`, 'order', null);
    return reply.send(result);
  });

  app.get('/archive', async (req, reply) => {
    const q = req.query as { ids?: string };
    let ids: number[] = [];
    if (q.ids) {
      ids = q.ids
        .split(',')
        .map((x) => Number(x.trim()))
        .filter((x) => Number.isFinite(x) && x > 0);
    }
    if (!ids.length) {
      const weekAgo = new Date(Date.now() - 7 * 24 * 3600_000).toISOString();
      ids = all<{ id: number }>("SELECT id FROM orders WHERE status = 'closed' AND closed_at >= ? ORDER BY id", [weekAgo]).map((r) => r.id);
    }
    ids = [...new Set(ids)].slice(0, 500);
    const archive = createArchive(ids);
    logAction(currentAdmin(req).id, `Скачан архив: ${ids.length} заказов`, 'order', null);
    const date = new Date().toISOString().slice(0, 10);
    reply.header('Content-Type', 'application/zip');
    reply.header('Content-Disposition', `attachment; filename="kranoff-${date}.zip"`);
    return reply.send(archive);
  });

  app.get('/orders/:id/media-list', async (req) => {
    return { media: listMedia(orderId(req)) };
  });

  app.get('/map', async () => {
    const { mastersSnapshot, ordersSnapshot } = await import('../lib/live.ts');
    return { masters: mastersSnapshot(), orders: ordersSnapshot() };
  });

  app.get('/geo', async (req, reply) => {
    const q = (req.query as { q?: string }).q || '';
    if (q.trim().length < 3) return reply.send({ results: [] });
    const results = await geocode(q);
    return reply.send({ results });
  });
}
