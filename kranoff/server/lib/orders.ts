import { all, get, run, tx, getSetting, settingNumber, logAction } from '../db.ts';
import { config, nowIso } from '../config.ts';
import {
  ACTIVE_STATUSES,
  addMinutes,
  calcEarnings,
  formatDateTime,
  haversineKm,
  isNight,
  locationAgeMinutes,
  money,
  nightMultiplier,
  orderNumber,
  parseJsonArray,
  prettyPhone,
  statusLabel,
  tariffLabel,
  urgencyLabel,
} from './utils.ts';
import { broadcastAdmin, emitOfferExpired, emitOrderChanged, emitOrderNew, mastersSnapshot, masterDisplayStatus } from './live.ts';
import { deleteAllMediaForOrder, listMedia, type MediaRow } from './media.ts';

export type ClientRow = {
  id: number;
  telegram_id: number | null;
  name: string;
  phone: string | null;
  address: string;
  language: string;
  note: string;
  created_at: string;
  updated_at: string;
};

export type MasterRow = {
  id: number;
  name: string;
  phone: string;
  telegram_id: number | null;
  skills: string;
  tools: string;
  districts: string;
  night_available: number;
  active: number;
  status: string;
  current_lat: number | null;
  current_lng: number | null;
  location_updated_at: string | null;
  shift_started_at: string | null;
  page_token: string;
  note: string;
  created_at: string;
};

export type OrderRow = {
  id: number;
  number: string;
  client_id: number;
  master_id: number | null;
  status: string;
  source: string;
  problem: string;
  description: string;
  address: string;
  lat: number | null;
  lng: number | null;
  entrance: string;
  floor: string;
  intercom: string;
  contact_note: string;
  desired_at: string | null;
  desired_label: string;
  urgency: string;
  is_emergency: number;
  tariff: string;
  works_total: number;
  materials_note: string;
  master_earnings: number;
  company_share: number;
  guarantee_months: number | null;
  admin_id: number | null;
  assigned_by: number | null;
  shift_id: number | null;
  offer_expires_at: string | null;
  confirmed_at: string | null;
  accepted_at: string | null;
  on_way_at: string | null;
  arrived_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  closed_at: string | null;
  cancelled_at: string | null;
  eta_minutes: number | null;
  language: string;
  created_at: string;
  updated_at: string;
};

export type OrderItemRow = {
  id: number;
  order_id: number;
  service_id: number | null;
  name: string;
  price: number;
  qty: number;
  is_extra: number;
};

export type OrderEventRow = {
  id: number;
  order_id: number;
  type: string;
  text: string;
  actor: string;
  created_at: string;
};

export type OfferRow = {
  id: number;
  order_id: number;
  master_id: number;
  status: string;
  expires_at: string;
  created_at: string;
  responded_at: string | null;
};

export type OrderFull = OrderRow & {
  client: ClientRow;
  master: (MasterRow & { skills_list: string[] }) | null;
  items: OrderItemRow[];
  media: MediaRow[];
  events: OrderEventRow[];
  offers: OfferRow[];
};

export type OrderNotifier = {
  clientStatus?: (orderId: number, status: string) => void | Promise<void>;
  masterOffer?: (offerId: number) => void | Promise<void>;
  masterStatus?: (orderId: number, status: string) => void | Promise<void>;
  masterAssigned?: (orderId: number) => void | Promise<void>;
};

let notifier: OrderNotifier = {};

export function setOrderNotifier(next: OrderNotifier): void {
  notifier = { ...notifier, ...next };
}

function safeNotify(fn: (() => void | Promise<void>) | undefined): void {
  if (!fn) return;
  try {
    Promise.resolve(fn()).catch((error) => console.error('[notify]', error));
  } catch (error) {
    console.error('[notify]', error);
  }
}

export function getClient(id: number): ClientRow | undefined {
  return get<ClientRow>('SELECT * FROM clients WHERE id = ?', [id]);
}

export function getMaster(id: number): MasterRow | undefined {
  return get<MasterRow>('SELECT * FROM masters WHERE id = ?', [id]);
}

export function getMasterByTelegram(telegramId: number): MasterRow | undefined {
  return get<MasterRow>('SELECT * FROM masters WHERE telegram_id = ?', [telegramId]);
}

export function getMasterByPhone(phone: string): MasterRow | undefined {
  return get<MasterRow>('SELECT * FROM masters WHERE phone = ?', [phone]);
}

export function getMasterByToken(token: string): MasterRow | undefined {
  return get<MasterRow>('SELECT * FROM masters WHERE page_token = ?', [token]);
}

export function getOrder(id: number): OrderRow | undefined {
  return get<OrderRow>('SELECT * FROM orders WHERE id = ?', [id]);
}

export function getOffer(id: number): OfferRow | undefined {
  return get<OfferRow>('SELECT * FROM offers WHERE id = ?', [id]);
}

export function getOrderByNumber(number: string): OrderRow | undefined {
  return get<OrderRow>('SELECT * FROM orders WHERE number = ?', [number]);
}

export function getOrderFull(id: number): OrderFull | null {
  const order = getOrder(id);
  if (!order) return null;
  const client = getClient(order.client_id);
  if (!client) return null;
  const master = order.master_id ? getMaster(order.master_id) : null;
  const items = all<OrderItemRow>('SELECT * FROM order_items WHERE order_id = ? ORDER BY id', [id]);
  const events = all<OrderEventRow>('SELECT * FROM order_events WHERE order_id = ? ORDER BY id DESC LIMIT 200', [id]);
  const offers = all<OfferRow>('SELECT * FROM offers WHERE order_id = ? ORDER BY id DESC', [id]);
  return {
    ...order,
    client,
    master: master ? { ...master, skills_list: parseJsonArray(master.skills) } : null,
    items,
    media: listMedia(id),
    events,
    offers,
  };
}

export function addEvent(orderId: number, type: string, text = '', actor = 'system'): void {
  run('INSERT INTO order_events (order_id, type, text, actor) VALUES (?, ?, ?, ?)', [orderId, type, text, actor]);
  run('UPDATE orders SET updated_at = ? WHERE id = ?', [nowIso(), orderId]);
}

export function clientOrders(clientId: number, limit = 20): OrderRow[] {
  return all<OrderRow>('SELECT * FROM orders WHERE client_id = ? ORDER BY id DESC LIMIT ?', [clientId, limit]);
}

export function getClientByTelegram(telegramId: number): ClientRow | undefined {
  return get<ClientRow>('SELECT * FROM clients WHERE telegram_id = ?', [telegramId]);
}

export function getClientByPhone(phone: string): ClientRow | undefined {
  return get<ClientRow>('SELECT * FROM clients WHERE phone = ?', [phone]);
}

export function upsertClient(input: {
  telegramId?: number | null;
  name?: string;
  phone?: string | null;
  address?: string;
  language?: string;
  note?: string;
}): ClientRow {
  const telegramId = input.telegramId ?? null;
  let existing: ClientRow | undefined;
  if (telegramId) existing = getClientByTelegram(telegramId);
  if (!existing && input.phone) existing = getClientByPhone(input.phone);
  if (existing) {
    const name = input.name?.trim() || existing.name;
    const phone = input.phone?.trim() || existing.phone;
    const address = input.address?.trim() || existing.address;
    const language = input.language || existing.language;
    const note = input.note ?? existing.note;
    run('UPDATE clients SET telegram_id = COALESCE(?, telegram_id), name = ?, phone = ?, address = ?, language = ?, note = ?, updated_at = ? WHERE id = ?', [
      telegramId,
      name,
      phone,
      address,
      language,
      note,
      nowIso(),
      existing.id,
    ]);
    return get<ClientRow>('SELECT * FROM clients WHERE id = ?', [existing.id])!;
  }
  const id = run(
    'INSERT INTO clients (telegram_id, name, phone, address, language, note) VALUES (?, ?, ?, ?, ?, ?)',
    [telegramId, input.name?.trim() || '', input.phone?.trim() || null, input.address?.trim() || '', input.language || 'ru', input.note || ''],
  ).lastInsertRowid;
  return get<ClientRow>('SELECT * FROM clients WHERE id = ?', [id])!;
}

export function createOrder(input: {
  clientId: number;
  source: string;
  problem: string;
  description?: string;
  address?: string;
  lat?: number | null;
  lng?: number | null;
  entrance?: string;
  floor?: string;
  intercom?: string;
  contactNote?: string;
  desiredAt?: string | null;
  desiredLabel?: string;
  urgency?: string;
  isEmergency?: boolean;
  tariff?: string;
  items: Array<{ serviceId?: number | null; name: string; price: number; qty?: number }>;
  adminId?: number | null;
  shiftId?: number | null;
  language?: string;
  status?: string;
  materialsNote?: string;
}): OrderFull {
  const created = tx(() => {
    const tmp = `TMP-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const tariff = input.tariff || (input.isEmergency ? (isNight() ? 'emergency_night' : 'emergency_day') : isNight() ? 'night' : 'day');
    const status = input.status || (input.source === 'telegram' ? 'new' : 'confirmed');
    const orderId = run(
      `INSERT INTO orders (number, client_id, status, source, problem, description, address, lat, lng, entrance, floor, intercom, contact_note,
        desired_at, desired_label, urgency, is_emergency, tariff, language, admin_id, shift_id, confirmed_at, materials_note)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        tmp,
        input.clientId,
        status,
        input.source,
        input.problem,
        input.description || '',
        input.address || '',
        input.lat ?? null,
        input.lng ?? null,
        input.entrance || '',
        input.floor || '',
        input.intercom || '',
        input.contactNote || '',
        input.desiredAt || null,
        input.desiredLabel || '',
        input.urgency || 'normal',
        input.isEmergency ? 1 : 0,
        tariff,
        input.language || 'ru',
        input.adminId ?? null,
        input.shiftId ?? null,
        status === 'confirmed' ? nowIso() : null,
        input.materialsNote || '',
      ],
    ).lastInsertRowid;
    run('UPDATE orders SET number = ? WHERE id = ?', [orderNumber(orderId), orderId]);
    for (const item of input.items) {
      const price = Math.max(0, Math.round(Number(item.price) || 0));
      const qty = Math.max(1, Math.round(Number(item.qty) || 1));
      run('INSERT INTO order_items (order_id, service_id, name, price, qty, is_extra) VALUES (?, ?, ?, ?, ?, 0)', [
        orderId,
        item.serviceId ?? null,
        item.name,
        price,
        qty,
      ]);
    }
    const total = all<{ s: number | null }>('SELECT SUM(price * qty) AS s FROM order_items WHERE order_id = ?', [orderId])[0]?.s ?? 0;
    run('UPDATE orders SET works_total = ? WHERE id = ?', [total, orderId]);
    addEvent(orderId, 'created', 'Заказ создан', input.source === 'telegram' ? 'client' : 'admin');
    return orderId;
  });
  const full = getOrderFull(created)!;
  emitOrderNew(full.id, full.number, Boolean(full.is_emergency));
  emitOrderChanged(full.id, full.status);
  return full;
}

export function recalcOrderTotal(orderId: number): number {
  const total = all<{ s: number | null }>('SELECT SUM(price * qty) AS s FROM order_items WHERE order_id = ?', [orderId])[0]?.s ?? 0;
  run('UPDATE orders SET works_total = ?, updated_at = ? WHERE id = ?', [total, nowIso(), orderId]);
  return Number(total || 0);
}

export function setOrderFields(
  orderId: number,
  fields: Partial<{
    problem: string;
    description: string;
    address: string;
    lat: number | null;
    lng: number | null;
    entrance: string;
    floor: string;
    intercom: string;
    contact_note: string;
    desired_at: string | null;
    desired_label: string;
    urgency: string;
    is_emergency: number;
    tariff: string;
    works_total: number;
    materials_note: string;
    guarantee_months: number | null;
    eta_minutes: number | null;
    status: string;
  }>,
  actor: string,
): void {
  const allowed = [
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
    'status',
  ];
  const sets: string[] = [];
  const params: unknown[] = [];
  for (const key of allowed) {
    if (key in fields) {
      sets.push(`${key} = ?`);
      params.push((fields as Record<string, unknown>)[key]);
    }
  }
  if (!sets.length) return;
  sets.push('updated_at = ?');
  params.push(nowIso());
  params.push(orderId);
  run(`UPDATE orders SET ${sets.join(', ')} WHERE id = ?`, params);
  addEvent(orderId, 'updated', 'Данные заказа изменены', actor);
  emitOrderChanged(orderId);
}

export function replaceOrderItems(
  orderId: number,
  items: Array<{ serviceId?: number | null; name: string; price: number; qty: number; isExtra?: boolean }>,
  actor: string,
): OrderFull | null {
  tx(() => {
    run('DELETE FROM order_items WHERE order_id = ?', [orderId]);
    for (const item of items) {
      run('INSERT INTO order_items (order_id, service_id, name, price, qty, is_extra) VALUES (?, ?, ?, ?, ?, ?)', [
        orderId,
        item.serviceId ?? null,
        item.name,
        Math.max(0, Math.round(Number(item.price) || 0)),
        Math.max(1, Math.round(Number(item.qty) || 1)),
        item.isExtra ? 1 : 0,
      ]);
    }
    recalcOrderTotal(orderId);
    addEvent(orderId, 'items', 'Состав работ обновлён', actor);
  });
  emitOrderChanged(orderId);
  return getOrderFull(orderId);
}

export function setMasterStatus(masterId: number, status: 'free' | 'busy' | 'offline'): MasterRow | null {
  const master = getMaster(masterId);
  if (!master) return null;
  const activeOrder = get<{ id: number }>(
    `SELECT id FROM orders WHERE master_id = ? AND status IN (${ACTIVE_STATUSES.map(() => '?').join(',')}) LIMIT 1`,
    [masterId, ...ACTIVE_STATUSES],
  );
  let next = status;
  if (next === 'free' && activeOrder) next = 'busy';
  const shiftStarted = next === 'offline' ? null : master.shift_started_at || nowIso();
  run('UPDATE masters SET status = ?, shift_started_at = ? WHERE id = ?', [next, shiftStarted, masterId]);
  broadcastAdmin({ type: 'masters:changed', masterId, status: next, at: nowIso() });
  return getMaster(masterId) || null;
}

export function freeMaster(masterId: number | null): void {
  if (!masterId) return;
  const master = getMaster(masterId);
  if (!master) return;
  const next = master.shift_started_at ? 'free' : 'offline';
  run('UPDATE masters SET status = ? WHERE id = ?', [next, masterId]);
  broadcastAdmin({ type: 'masters:changed', masterId, status: next, at: nowIso() });
}

export function suggestMasters(orderId: number): Array<
  MasterRow & {
    skills_list: string[];
    match_count: number;
    distance_km: number | null;
    location_age_minutes: number | null;
    can_take: boolean;
    active_orders: number;
    active_order_status: string | null;
    display_status: string;
  }
> {
  const order = getOrder(orderId);
  if (!order) return [];
  const items = all<{ service_id: number | null }>('SELECT service_id FROM order_items WHERE order_id = ?', [orderId]);
  const required = new Set<string>();
  for (const item of items) {
    if (!item.service_id) continue;
    const svc = get<{ skills: string }>('SELECT skills FROM services WHERE id = ?', [item.service_id]);
    for (const skill of parseJsonArray(svc?.skills)) required.add(skill);
  }
  if (order.is_emergency) required.add('emergency');
  const masters = all<MasterRow>('SELECT * FROM masters WHERE active = 1');
  return masters
    .map((master) => {
      const skills = parseJsonArray(master.skills);
      const matchCount = [...required].filter((skill) => skills.includes(skill)).length;
      const distance =
        order.lat != null && order.lng != null && master.current_lat != null && master.current_lng != null
          ? haversineKm(order.lat, order.lng, master.current_lat, master.current_lng)
          : null;
      const activeOrders =
        get<{ c: number }>(
          `SELECT COUNT(*) AS c FROM orders WHERE master_id = ? AND status IN (${ACTIVE_STATUSES.map(() => '?').join(',')})`,
          [master.id, ...ACTIVE_STATUSES],
        )?.c ?? 0;
      const activeOrder = get<{ status: string }>(
        `SELECT status FROM orders WHERE master_id = ? AND status NOT IN ('closed','cancelled') ORDER BY id DESC LIMIT 1`,
        [master.id],
      );
      return {
        ...master,
        skills_list: skills,
        match_count: matchCount,
        distance_km: distance == null ? null : Math.round(distance * 10) / 10,
        location_age_minutes: locationAgeMinutes(master.location_updated_at),
        can_take: masterDisplayStatus({ ...master, active_order_status: activeOrder?.status ?? null }) === 'free',
        active_orders: Number(activeOrders),
        active_order_status: activeOrder?.status ?? null,
        display_status: masterDisplayStatus({ ...master, active_order_status: activeOrder?.status ?? null }),
      };
    })
    .sort((a, b) => {
      if (a.can_take !== b.can_take) return a.can_take ? -1 : 1;
      if (b.match_count !== a.match_count) return b.match_count - a.match_count;
      const da = a.distance_km ?? 9999;
      const db = b.distance_km ?? 9999;
      return da - db;
    });
}

export function assignMaster(
  orderId: number,
  masterId: number,
  adminId: number,
  mode: 'offer' | 'manual' = 'offer',
): { ok: boolean; error?: string; offerId?: number } {
  const order = getOrder(orderId);
  const master = getMaster(masterId);
  if (!order) return { ok: false, error: 'Заказ не найден' };
  if (!master || !master.active) return { ok: false, error: 'Мастер не найден или отключён' };
  if (!['new', 'reviewing', 'confirmed', 'searching', 'accepted', 'on_way'].includes(order.status)) {
    return { ok: false, error: `Нельзя назначить в статусе «${statusLabel(order.status)}»` };
  }
  const masterActive = get<{ id: number }>("SELECT id FROM orders WHERE master_id = ? AND status NOT IN ('closed','cancelled') LIMIT 1", [masterId]);
  if (masterActive && masterActive.id !== orderId) return { ok: false, error: 'У мастера уже есть активный заказ' };
  if (master.status === 'busy' && order.master_id !== masterId) return { ok: false, error: 'Мастер занят' };
  if (master.status === 'offline') return { ok: false, error: 'Мастер не на линии' };
  if (mode === 'offer' && !master.telegram_id) {
    return { ok: false, error: 'У мастера не привязан Telegram — назначьте вручную или привяжите бота' };
  }

  const previousMasterId = order.master_id;
  const timeout = settingNumber('offer_timeout_minutes', 5);
  let offerId = 0;
  tx(() => {
    run("UPDATE offers SET status = 'revoked', responded_at = ? WHERE order_id = ? AND status = 'pending'", [nowIso(), orderId]);
    if (previousMasterId && previousMasterId !== masterId) {
      run("UPDATE masters SET status = CASE WHEN shift_started_at IS NOT NULL THEN 'free' ELSE 'offline' END WHERE id = ?", [previousMasterId]);
      addEvent(orderId, 'reassigned', 'Предыдущий мастер снят с заказа', `admin:${adminId}`);
    }
    const expires = addMinutes(new Date(), timeout).toISOString();
    if (mode === 'manual') {
      run(
        `UPDATE orders SET master_id = ?, status = 'accepted', accepted_at = ?, confirmed_at = COALESCE(confirmed_at, ?),
         offer_expires_at = NULL, on_way_at = NULL, arrived_at = NULL, started_at = NULL, completed_at = NULL,
         closed_at = NULL, assigned_by = ?, updated_at = ? WHERE id = ?`,
        [masterId, nowIso(), nowIso(), adminId, nowIso(), orderId],
      );
      addEvent(orderId, 'assigned', `Мастер ${master.name} назначен вручную`, `admin:${adminId}`);
      run("UPDATE offers SET status = 'manual', responded_at = ? WHERE order_id = ? AND status = 'pending'", [nowIso(), orderId]);
    } else {
      offerId = run('INSERT INTO offers (order_id, master_id, status, expires_at) VALUES (?, ?, ?, ?)', [
        orderId,
        masterId,
        'pending',
        expires,
      ]).lastInsertRowid;
      run(
        `UPDATE orders SET master_id = ?, status = 'offered', offer_expires_at = ?, confirmed_at = COALESCE(confirmed_at, ?),
         accepted_at = NULL, on_way_at = NULL, arrived_at = NULL, started_at = NULL, completed_at = NULL, closed_at = NULL,
         assigned_by = ?, updated_at = ? WHERE id = ?`,
        [masterId, expires, nowIso(), adminId, nowIso(), orderId],
      );
      addEvent(orderId, 'offered', `Предложение отправлено мастеру ${master.name}`, `admin:${adminId}`);
    }
    run('UPDATE masters SET status = ? WHERE id = ?', [mode === 'manual' ? 'busy' : master.status, masterId]);
    logAction(adminId, mode === 'manual' ? `Назначен мастер ${master.name}` : `Предложен мастер ${master.name}`, 'order', orderId);
  });

  emitOrderChanged(orderId, mode === 'manual' ? 'accepted' : 'offered');
  broadcastAdmin({ type: 'masters:changed', at: nowIso() });
  if (mode === 'manual') {
    safeNotify(() => notifier.masterAssigned?.(orderId));
    safeNotify(() => notifier.clientStatus?.(orderId, 'accepted'));
  } else {
    safeNotify(() => notifier.masterOffer?.(offerId));
  }
  return { ok: true, offerId };
}

export function acceptOffer(offerId: number, masterId: number): { ok: boolean; error?: string } {
  const offer = get<OfferRow>('SELECT * FROM offers WHERE id = ?', [offerId]);
  if (!offer) return { ok: false, error: 'Предложение не найдено' };
  if (offer.master_id !== masterId) return { ok: false, error: 'Это предложение другому мастеру' };
  if (offer.status !== 'pending') return { ok: false, error: 'Предложение уже неактуально' };
  if (new Date(offer.expires_at).getTime() < Date.now()) return { ok: false, error: 'Время предложения истекло' };
  const order = getOrder(offer.order_id);
  if (!order || order.status !== 'offered' || order.master_id !== masterId) {
    return { ok: false, error: 'Заказ уже переназначен' };
  }
  tx(() => {
    run("UPDATE offers SET status = 'accepted', responded_at = ? WHERE id = ?", [nowIso(), offerId]);
    run("UPDATE offers SET status = 'revoked', responded_at = ? WHERE order_id = ? AND status = 'pending' AND id <> ?", [
      nowIso(),
      order.id,
      offerId,
    ]);
    run("UPDATE orders SET status = 'accepted', accepted_at = ?, offer_expires_at = NULL, updated_at = ? WHERE id = ?", [
      nowIso(),
      nowIso(),
      order.id,
    ]);
    run("UPDATE masters SET status = 'busy' WHERE id = ?", [masterId]);
    addEvent(order.id, 'accepted', 'Мастер принял заказ', `master:${masterId}`);
  });
  emitOrderChanged(order.id, 'accepted');
  broadcastAdmin({ type: 'masters:changed', at: nowIso() });
  safeNotify(() => notifier.clientStatus?.(order.id, 'accepted'));
  return { ok: true };
}

export function declineOffer(offerId: number, masterId: number): { ok: boolean; error?: string } {
  const offer = get<OfferRow>('SELECT * FROM offers WHERE id = ?', [offerId]);
  if (!offer) return { ok: false, error: 'Предложение не найдено' };
  if (offer.master_id !== masterId) return { ok: false, error: 'Это предложение другому мастеру' };
  if (offer.status !== 'pending') return { ok: true };
  const order = getOrder(offer.order_id);
  tx(() => {
    run("UPDATE offers SET status = 'declined', responded_at = ? WHERE id = ?", [nowIso(), offerId]);
    if (order && order.status === 'offered' && order.master_id === masterId) {
      run("UPDATE orders SET status = 'searching', master_id = NULL, offer_expires_at = NULL, updated_at = ? WHERE id = ?", [
        nowIso(),
        order.id,
      ]);
      addEvent(order.id, 'declined', 'Мастер отклонил заказ', `master:${masterId}`);
      freeMaster(masterId);
    }
  });
  if (order) emitOrderChanged(order.id, 'searching');
  broadcastAdmin({ type: 'order:attention', orderId: order?.id, reason: 'declined', at: nowIso() });
  return { ok: true };
}

export function expireOffers(): number {
  const now = nowIso();
  const expired = all<OfferRow>("SELECT * FROM offers WHERE status = 'pending' AND expires_at <= ?", [now]);
  for (const offer of expired) {
    const order = getOrder(offer.order_id);
    tx(() => {
      run("UPDATE offers SET status = 'expired', responded_at = ? WHERE id = ?", [now, offer.id]);
      if (order && order.status === 'offered' && order.master_id === offer.master_id) {
        run("UPDATE orders SET status = 'searching', master_id = NULL, offer_expires_at = NULL, updated_at = ? WHERE id = ?", [
          now,
          order.id,
        ]);
        addEvent(order.id, 'expired', 'Мастер не ответил за 5 минут — требуется звонок', 'system');
        freeMaster(offer.master_id);
        emitOfferExpired(order.id, order.number, offer.master_id);
      }
    });
    if (order) emitOrderChanged(order.id, 'searching');
  }
  return expired.length;
}

export function setOrderStatus(
  orderId: number,
  status: string,
  actor: string,
  text = '',
  adminId: number | null = null,
): { ok: boolean; error?: string } {
  const order = getOrder(orderId);
  if (!order) return { ok: false, error: 'Заказ не найден' };
  const now = nowIso();
  const timestamps: Record<string, string> = {};
  if (status === 'confirmed') timestamps.confirmed_at = order.confirmed_at || now;
  if (status === 'accepted') timestamps.accepted_at = order.accepted_at || now;
  if (status === 'on_way') timestamps.on_way_at = order.on_way_at || now;
  if (status === 'arrived') timestamps.arrived_at = order.arrived_at || now;
  if (status === 'in_progress') timestamps.started_at = order.started_at || now;
  if (status === 'done_by_master') timestamps.completed_at = order.completed_at || now;
  if (status === 'closed') timestamps.closed_at = order.closed_at || now;
  if (status === 'cancelled') timestamps.cancelled_at = order.cancelled_at || now;
  const sets = ['status = ?', 'updated_at = ?'];
  const params: unknown[] = [status, now];
  for (const [key, value] of Object.entries(timestamps)) {
    sets.push(`${key} = ?`);
    params.push(value);
  }
  params.push(orderId);
  tx(() => {
    run(`UPDATE orders SET ${sets.join(', ')} WHERE id = ?`, params);
    addEvent(orderId, 'status', text || `Статус: ${statusLabel(status)}`, actor);
    if (status === 'cancelled' || status === 'closed') {
      run("UPDATE offers SET status = 'revoked', responded_at = ? WHERE order_id = ? AND status = 'pending'", [now, orderId]);
      freeMaster(order.master_id);
    }
    if (status === 'closed') {
      const earnings = calcEarnings(order.works_total);
      run('UPDATE orders SET master_earnings = ?, company_share = ? WHERE id = ?', [earnings.master, earnings.company, orderId]);
    }
    if (adminId) logAction(adminId, `Статус заказа ${order.number}: ${statusLabel(status)}`, 'order', orderId);
  });
  emitOrderChanged(orderId, status);
  broadcastAdmin({ type: 'masters:changed', at: nowIso() });
  if (['confirmed', 'accepted', 'on_way', 'arrived', 'in_progress', 'done_by_master', 'closed', 'cancelled'].includes(status)) {
    safeNotify(() => notifier.clientStatus?.(orderId, status));
    safeNotify(() => notifier.masterStatus?.(orderId, status));
  }
  return { ok: true };
}

export function masterAction(
  orderId: number,
  masterId: number,
  action: 'on_way' | 'arrived' | 'start' | 'done' | 'cancel',
): { ok: boolean; error?: string } {
  const order = getOrder(orderId);
  if (!order) return { ok: false, error: 'Заказ не найден' };
  if (order.master_id !== masterId) return { ok: false, error: 'Это не ваш заказ' };
  if (action === 'on_way') {
    if (!['accepted', 'offered'].includes(order.status)) return { ok: false, error: 'Нельзя выехать в этом статусе' };
    return setOrderStatus(orderId, 'on_way', `master:${masterId}`, 'Мастер выехал');
  }
  if (action === 'arrived') {
    if (!['on_way', 'accepted'].includes(order.status)) return { ok: false, error: 'Сначала отметьте выезд' };
    return setOrderStatus(orderId, 'arrived', `master:${masterId}`, 'Мастер на месте');
  }
  if (action === 'start') {
    if (!['arrived', 'approval'].includes(order.status)) return { ok: false, error: 'Сначала отметьте прибытие' };
    return setOrderStatus(orderId, 'in_progress', `master:${masterId}`, 'Работы начаты');
  }
  if (action === 'done') {
    if (order.status !== 'in_progress') return { ok: false, error: 'Работы ещё не начаты' };
    return setOrderStatus(orderId, 'done_by_master', `master:${masterId}`, 'Мастер завершил работы');
  }
  if (action === 'cancel') {
    if (['closed', 'cancelled', 'in_progress', 'done_by_master', 'admin_check'].includes(order.status)) {
      return { ok: false, error: 'Работы уже начались — свяжитесь с администратором' };
    }
    return masterAbort(orderId, masterId);
  }
  return { ok: false, error: 'Неизвестное действие' };
}

export function masterAbort(orderId: number, masterId: number): { ok: boolean; error?: string } {
  const order = getOrder(orderId);
  if (!order) return { ok: false, error: 'Заказ не найден' };
  if (order.master_id !== masterId) return { ok: false, error: 'Это не ваш заказ' };
  if (['closed', 'cancelled'].includes(order.status)) return { ok: false, error: 'Заказ уже завершён' };
  const now = nowIso();
  tx(() => {
    run(
      "UPDATE orders SET status = 'searching', master_id = NULL, offer_expires_at = NULL, updated_at = ? WHERE id = ?",
      [now, orderId],
    );
    run("UPDATE offers SET status = 'revoked', responded_at = ? WHERE order_id = ? AND status = 'pending'", [now, orderId]);
    addEvent(orderId, 'aborted', 'Мастер не может выехать — требуется переназначение', `master:${masterId}`);
    freeMaster(masterId);
  });
  emitOrderChanged(orderId, 'searching');
  broadcastAdmin({ type: 'order:attention', orderId, reason: 'master_abort', at: now });
  safeNotify(() => notifier.clientStatus?.(orderId, 'searching'));
  return { ok: true };
}

export function closeOrder(
  orderId: number,
  adminId: number,
  worksTotal?: number,
  guaranteeMonths?: number | null,
  materialsNote?: string,
): { ok: boolean; error?: string } {
  const order = getOrder(orderId);
  if (!order) return { ok: false, error: 'Заказ не найден' };
  if (order.status === 'closed') return { ok: false, error: 'Заказ уже закрыт' };
  if (order.status === 'cancelled') return { ok: false, error: 'Заказ отменён' };
  const total = Math.max(0, Math.round(Number(worksTotal ?? order.works_total) || 0));
  const earnings = calcEarnings(total);
  const now = nowIso();
  tx(() => {
    run(
      `UPDATE orders SET status = 'closed', works_total = ?, master_earnings = ?, company_share = ?, guarantee_months = ?,
       materials_note = ?, closed_at = COALESCE(closed_at, ?), completed_at = COALESCE(completed_at, ?), updated_at = ?
       WHERE id = ?`,
      [total, earnings.master, earnings.company, guaranteeMonths ?? order.guarantee_months, materialsNote ?? order.materials_note, now, now, now, orderId],
    );
    addEvent(orderId, 'closed', `Заказ закрыт. Работы: ${money(total)}, мастеру: ${money(earnings.master)}`, `admin:${adminId}`);
    run("UPDATE offers SET status = 'revoked', responded_at = ? WHERE order_id = ? AND status = 'pending'", [now, orderId]);
    freeMaster(order.master_id);
    logAction(adminId, `Заказ ${order.number} закрыт, сумма ${total} ₸`, 'order', orderId);
  });
  emitOrderChanged(orderId, 'closed');
  broadcastAdmin({ type: 'masters:changed', at: nowIso() });
  safeNotify(() => notifier.clientStatus?.(orderId, 'closed'));
  safeNotify(() => notifier.masterStatus?.(orderId, 'closed'));
  return { ok: true };
}

export function cancelOrder(
  orderId: number,
  actor: string,
  adminId: number | null = null,
  byClient = false,
): { ok: boolean; error?: string } {
  const order = getOrder(orderId);
  if (!order) return { ok: false, error: 'Заказ не найден' };
  if (['closed', 'cancelled'].includes(order.status)) return { ok: false, error: 'Заказ уже завершён' };
  if (byClient && ['in_progress', 'done_by_master', 'admin_check'].includes(order.status)) {
    return { ok: false, error: 'Работы уже начались — отмена через администратора' };
  }
  return setOrderStatus(orderId, 'cancelled', actor, byClient ? 'Клиент отменил заказ' : 'Заказ отменён', adminId);
}

export function setEta(orderId: number, minutes: number): void {
  const order = getOrder(orderId);
  if (!order) return;
  run('UPDATE orders SET eta_minutes = ?, updated_at = ? WHERE id = ?', [Math.max(0, Math.round(minutes)), nowIso(), orderId]);
  addEvent(orderId, 'eta', `Ожидаемое время прибытия: ${minutes} мин.`, `master:${order.master_id}`);
  emitOrderChanged(orderId);
  safeNotify(() => notifier.clientStatus?.(orderId, 'eta'));
}

export function masterEarnings(masterId: number): { shift: number; last24h: number; today: number; orders: number } {
  const master = getMaster(masterId);
  if (!master) return { shift: 0, last24h: 0, today: 0, orders: 0 };
  const shiftStart = master.shift_started_at || '1970-01-01T00:00:00.000Z';
  const shiftRow = get<{ s: number | null; c: number }>(
    "SELECT SUM(master_earnings) AS s, COUNT(*) AS c FROM orders WHERE master_id = ? AND status = 'closed' AND closed_at >= ?",
    [masterId, shiftStart],
  );
  const dayAgo = new Date(Date.now() - 24 * 3600_000).toISOString();
  const dayRow = get<{ s: number | null }>("SELECT SUM(master_earnings) AS s FROM orders WHERE master_id = ? AND status = 'closed' AND closed_at >= ?", [
    masterId,
    dayAgo,
  ]);
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayRow = get<{ s: number | null }>("SELECT SUM(master_earnings) AS s FROM orders WHERE master_id = ? AND status = 'closed' AND closed_at >= ?", [
    masterId,
    todayStart.toISOString(),
  ]);
  return {
    shift: Number(shiftRow?.s || 0),
    last24h: Number(dayRow?.s || 0),
    today: Number(todayRow?.s || 0),
    orders: Number(shiftRow?.c || 0),
  };
}

export function masterActiveOrder(masterId: number): OrderRow | undefined {
  return get<OrderRow>(
    `SELECT * FROM orders WHERE master_id = ? AND status IN (${ACTIVE_STATUSES.map(() => '?').join(',')}) ORDER BY id DESC LIMIT 1`,
    [masterId, ...ACTIVE_STATUSES],
  );
}

export function listOrders(filters: {
  status?: string;
  source?: string;
  q?: string;
  from?: string;
  to?: string;
  masterId?: number;
  clientId?: number;
  emergency?: boolean;
  limit?: number;
  offset?: number;
}): { items: Array<OrderRow & { client_name: string; client_phone: string | null; master_name: string | null; items_count: number }>; total: number } {
  const where: string[] = [];
  const params: unknown[] = [];
  if (filters.status && filters.status !== 'all') {
    if (filters.status === 'active') {
      where.push(`o.status IN (${ACTIVE_STATUSES.map(() => '?').join(',')})`);
      params.push(...ACTIVE_STATUSES);
    } else if (filters.status.includes(',')) {
      const list = filters.status.split(',').filter(Boolean);
      where.push(`o.status IN (${list.map(() => '?').join(',')})`);
      params.push(...list);
    } else {
      where.push('o.status = ?');
      params.push(filters.status);
    }
  }
  if (filters.source) {
    where.push('o.source = ?');
    params.push(filters.source);
  }
  if (filters.masterId) {
    where.push('o.master_id = ?');
    params.push(filters.masterId);
  }
  if (filters.clientId) {
    where.push('o.client_id = ?');
    params.push(filters.clientId);
  }
  if (filters.emergency) where.push('o.is_emergency = 1');
  if (filters.from) {
    where.push('o.created_at >= ?');
    params.push(filters.from);
  }
  if (filters.to) {
    where.push('o.created_at <= ?');
    params.push(filters.to);
  }
  if (filters.q) {
    where.push('(o.number LIKE ? OR o.problem LIKE ? OR o.address LIKE ? OR c.name LIKE ? OR c.phone LIKE ? OR c.phone LIKE ?)');
    const like = `%${filters.q}%`;
    params.push(like, like, like, like, like, `%${filters.q.replace(/\D/g, '')}%`);
  }
  const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';
  const total =
    get<{ c: number }>(
      `SELECT COUNT(*) AS c FROM orders o JOIN clients c ON c.id = o.client_id ${whereSql}`,
      params,
    )?.c ?? 0;
  const limit = Math.min(200, Math.max(1, filters.limit ?? 50));
  const offset = Math.max(0, filters.offset ?? 0);
  const items = all<OrderRow & { client_name: string; client_phone: string | null; master_name: string | null; items_count: number }>(
    `SELECT o.*, c.name AS client_name, c.phone AS client_phone, m.name AS master_name,
       (SELECT COUNT(*) FROM order_items i WHERE i.order_id = o.id) AS items_count
     FROM orders o JOIN clients c ON c.id = o.client_id LEFT JOIN masters m ON m.id = o.master_id
     ${whereSql} ORDER BY o.created_at DESC LIMIT ? OFFSET ?`,
    [...params, limit, offset],
  );
  return { items, total: Number(total) };
}

export function dashboard(): Record<string, unknown> {
  const count = (sql: string, params: unknown[] = []) => Number(get<{ c: number }>(sql, params)?.c || 0);
  const activeClause = ACTIVE_STATUSES.map(() => '?').join(',');
  const newCount = count("SELECT COUNT(*) AS c FROM orders WHERE status IN ('new','reviewing')");
  const emergency = count(`SELECT COUNT(*) AS c FROM orders WHERE is_emergency = 1 AND status IN (${activeClause})`, ACTIVE_STATUSES);
  const onShift = count("SELECT COUNT(*) AS c FROM masters WHERE active = 1 AND shift_started_at IS NOT NULL AND status <> 'offline'");
  const onWay = count("SELECT COUNT(*) AS c FROM orders WHERE status = 'on_way'");
  const atWork = count("SELECT COUNT(*) AS c FROM orders WHERE status IN ('arrived','approval','in_progress')");
  const unreadSupport = count(
    "SELECT COUNT(DISTINCT client_id) AS c FROM support_messages WHERE direction = 'client' AND read_at IS NULL",
  );
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const closedToday = count("SELECT COUNT(*) AS c FROM orders WHERE status = 'closed' AND closed_at >= ?", [todayStart.toISOString()]);
  const revenueToday = Number(
    get<{ s: number | null }>("SELECT SUM(works_total) AS s FROM orders WHERE status = 'closed' AND closed_at >= ?", [todayStart.toISOString()])?.s || 0,
  );
  const latest = all<OrderRow & { client_name: string; client_phone: string | null; master_name: string | null }>(
    `SELECT o.*, c.name AS client_name, c.phone AS client_phone, m.name AS master_name
       FROM orders o JOIN clients c ON c.id = o.client_id LEFT JOIN masters m ON m.id = o.master_id
      ORDER BY o.created_at DESC LIMIT 10`,
  );
  const masters = mastersSnapshot();
  const stale = masters.filter((m) => {
    if (!m.location_updated_at) return m.status === 'free' || m.status === 'busy';
    const age = locationAgeMinutes(m.location_updated_at);
    return age != null && age > 2 && (m.status === 'free' || m.status === 'on_way' || m.status === 'busy');
  });
  const offers = all<{ id: number; order_id: number; number: string; master_id: number; expires_at: string; master_name: string }>(
    `SELECT o.id, o.order_id, o.expires_at, o.master_id, ord.number, m.name AS master_name
       FROM offers o JOIN orders ord ON ord.id = o.order_id JOIN masters m ON m.id = o.master_id
      WHERE o.status = 'pending' ORDER BY o.expires_at LIMIT 20`,
  );
  return {
    counts: { new: newCount, emergency, onShift, onWay, atWork, unreadSupport, closedToday, revenueToday },
    latest,
    masters,
    stale,
    offers,
  };
}
