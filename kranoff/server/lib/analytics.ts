import { all, get } from '../db.ts';

function n(value: unknown): number {
  return Number(value || 0);
}

export function analytics(fromIso: string, toIso: string) {
  const range = [fromIso, toIso];
  const summary = get<Record<string, unknown>>(
    `SELECT
       COUNT(*) AS orders,
       SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed,
       SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
       SUM(CASE WHEN status IN ('new','reviewing','confirmed','searching','offered','accepted','on_way','arrived','approval','in_progress') THEN 1 ELSE 0 END) AS active,
       SUM(CASE WHEN status = 'closed' THEN works_total ELSE 0 END) AS revenue,
       SUM(CASE WHEN status = 'closed' THEN master_earnings ELSE 0 END) AS master_earnings,
       SUM(CASE WHEN status = 'closed' THEN company_share ELSE 0 END) AS company_share,
       SUM(CASE WHEN is_emergency = 1 THEN 1 ELSE 0 END) AS emergency,
       SUM(CASE WHEN source = 'telegram' THEN 1 ELSE 0 END) AS telegram,
       SUM(CASE WHEN source = 'phone' THEN 1 ELSE 0 END) AS phone,
       SUM(CASE WHEN source = 'whatsapp' THEN 1 ELSE 0 END) AS whatsapp,
       SUM(CASE WHEN tariff IN ('night','emergency_night') THEN 1 ELSE 0 END) AS night_orders,
       AVG(CASE WHEN accepted_at IS NOT NULL THEN (julianday(accepted_at) - julianday(created_at)) * 1440 END) AS avg_assign_min,
       AVG(CASE WHEN on_way_at IS NOT NULL AND arrived_at IS NOT NULL THEN (julianday(arrived_at) - julianday(on_way_at)) * 1440 END) AS avg_travel_min,
       AVG(CASE WHEN closed_at IS NOT NULL THEN (julianday(closed_at) - julianday(created_at)) * 1440 END) AS avg_life_min
     FROM orders WHERE created_at >= ? AND created_at <= ?`,
    range,
  ) || {};
  const orders = n(summary.orders);
  const closed = n(summary.closed);
  const cancelled = n(summary.cancelled);
  const revenue = n(summary.revenue);

  const newClients = n(
    get<{ c: number }>('SELECT COUNT(*) AS c FROM clients WHERE created_at >= ? AND created_at <= ?', range)?.c,
  );
  const repeatClients = n(
    get<{ c: number }>(
      `SELECT COUNT(*) AS c FROM (SELECT client_id, COUNT(*) AS cnt FROM orders
         WHERE created_at >= ? AND created_at <= ? GROUP BY client_id HAVING cnt > 1)`,
      range,
    )?.c,
  );

  const byDay = all<{ day: string; orders: number; closed: number; cancelled: number; revenue: number }>(
    `SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS orders,
       SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed,
       SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
       SUM(CASE WHEN status = 'closed' THEN works_total ELSE 0 END) AS revenue
     FROM orders WHERE created_at >= ? AND created_at <= ? GROUP BY day ORDER BY day`,
    range,
  ).map((r) => ({ ...r, orders: n(r.orders), closed: n(r.closed), cancelled: n(r.cancelled), revenue: n(r.revenue) }));

  const byStatus = all<{ status: string; c: number }>(
    'SELECT status, COUNT(*) AS c FROM orders WHERE created_at >= ? AND created_at <= ? GROUP BY status ORDER BY c DESC',
    range,
  ).map((r) => ({ status: r.status, count: n(r.c) }));

  const bySource = all<{ source: string; c: number; revenue: number }>(
    `SELECT source, COUNT(*) AS c, SUM(CASE WHEN status='closed' THEN works_total ELSE 0 END) AS revenue
     FROM orders WHERE created_at >= ? AND created_at <= ? GROUP BY source ORDER BY c DESC`,
    range,
  ).map((r) => ({ source: r.source, count: n(r.c), revenue: n(r.revenue) }));

  const byService = all<{ name: string; c: number; revenue: number }>(
    `SELECT i.name, COUNT(*) AS c, SUM(i.price * i.qty) AS revenue
     FROM order_items i JOIN orders o ON o.id = i.order_id
     WHERE o.created_at >= ? AND o.created_at <= ? GROUP BY i.name ORDER BY c DESC LIMIT 20`,
    range,
  ).map((r) => ({ name: r.name, count: n(r.c), revenue: n(r.revenue) }));

  const byMaster = all<{
    master_id: number;
    name: string;
    orders: number;
    closed: number;
    revenue: number;
    earnings: number;
    cancelled: number;
    avg_close_min: number | null;
  }>(
    `SELECT m.id AS master_id, m.name,
       COUNT(o.id) AS orders,
       SUM(CASE WHEN o.status = 'closed' THEN 1 ELSE 0 END) AS closed,
       SUM(CASE WHEN o.status = 'closed' THEN o.works_total ELSE 0 END) AS revenue,
       SUM(CASE WHEN o.status = 'closed' THEN o.master_earnings ELSE 0 END) AS earnings,
       SUM(CASE WHEN o.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
       AVG(CASE WHEN o.closed_at IS NOT NULL THEN (julianday(o.closed_at) - julianday(o.created_at)) * 1440 END) AS avg_close_min
     FROM masters m LEFT JOIN orders o ON o.master_id = m.id AND o.created_at >= ? AND o.created_at <= ?
     GROUP BY m.id ORDER BY closed DESC, orders DESC`,
    range,
  ).map((r) => ({
    ...r,
    orders: n(r.orders),
    closed: n(r.closed),
    revenue: n(r.revenue),
    earnings: n(r.earnings),
    cancelled: n(r.cancelled),
    avg_close_min: r.avg_close_min == null ? null : Math.round(n(r.avg_close_min)),
  }));

  const byHour = all<{ hour: string; c: number }>(
    `SELECT strftime('%H', datetime(created_at, '+5 hours')) AS hour, COUNT(*) AS c
     FROM orders WHERE created_at >= ? AND created_at <= ? GROUP BY hour ORDER BY hour`,
    range,
  ).map((r) => ({ hour: r.hour, count: n(r.c) }));

  const byTariff = all<{ tariff: string; c: number; revenue: number }>(
    `SELECT tariff, COUNT(*) AS c, SUM(CASE WHEN status='closed' THEN works_total ELSE 0 END) AS revenue
     FROM orders WHERE created_at >= ? AND created_at <= ? GROUP BY tariff`,
    range,
  ).map((r) => ({ tariff: r.tariff, count: n(r.c), revenue: n(r.revenue) }));

  return {
    range: { from: fromIso, to: toIso },
    summary: {
      orders,
      closed,
      cancelled,
      active: n(summary.active),
      revenue,
      master_earnings: n(summary.master_earnings),
      company_share: n(summary.company_share),
      avg_check: closed ? Math.round(revenue / closed) : 0,
      conversion: orders ? Math.round((closed / orders) * 1000) / 10 : 0,
      emergency: n(summary.emergency),
      night_orders: n(summary.night_orders),
      new_clients: newClients,
      repeat_clients: repeatClients,
      avg_assign_min: summary.avg_assign_min == null ? null : Math.round(n(summary.avg_assign_min)),
      avg_travel_min: summary.avg_travel_min == null ? null : Math.round(n(summary.avg_travel_min)),
      avg_life_min: summary.avg_life_min == null ? null : Math.round(n(summary.avg_life_min)),
      telegram: n(summary.telegram),
      phone: n(summary.phone),
      whatsapp: n(summary.whatsapp),
    },
    byDay,
    byStatus,
    bySource,
    byService,
    byMaster,
    byHour,
    byTariff,
  };
}

export function defaultRange(days = 30): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to.getTime() - days * 24 * 3600_000);
  return { from: from.toISOString(), to: to.toISOString() };
}
