import { all, run } from '../db.ts';
import { nowIso } from '../config.ts';
import { parseJsonArray } from './utils.ts';

export interface LiveSocket {
  readyState: number;
  send(data: string): void;
  ping(): void;
  terminate(): void;
  on(event: string, cb: (...args: unknown[]) => void): void;
}

const adminSockets = new Set<LiveSocket>();
const masterSockets = new Map<number, Set<LiveSocket>>();

export function attachAdmin(socket: LiveSocket): void {
  adminSockets.add(socket);
  socket.send(JSON.stringify({ type: 'hello', role: 'admin', at: nowIso() }));
  socket.send(JSON.stringify({ type: 'snapshot', masters: mastersSnapshot(), orders: ordersSnapshot() }));
  socket.on('close', () => adminSockets.delete(socket));
  socket.on('error', () => adminSockets.delete(socket));
}

export function attachMaster(masterId: number, socket: LiveSocket): void {
  let set = masterSockets.get(masterId);
  if (!set) {
    set = new Set();
    masterSockets.set(masterId, set);
  }
  set.add(socket);
  socket.send(JSON.stringify({ type: 'hello', role: 'master', masterId, at: nowIso() }));
  socket.on('close', () => {
    set?.delete(socket);
    if (set && set.size === 0) masterSockets.delete(masterId);
  });
  socket.on('error', () => {
    set?.delete(socket);
    if (set && set.size === 0) masterSockets.delete(masterId);
  });
}

export function broadcastAdmin(event: Record<string, unknown>): void {
  const payload = JSON.stringify(event);
  for (const socket of adminSockets) {
    if (socket.readyState === 1) {
      try {
        socket.send(payload);
      } catch {
        adminSockets.delete(socket);
      }
    }
  }
}

export function broadcastMaster(masterId: number, event: Record<string, unknown>): void {
  const set = masterSockets.get(masterId);
  if (!set) return;
  const payload = JSON.stringify(event);
  for (const socket of set) {
    if (socket.readyState === 1) {
      try {
        socket.send(payload);
      } catch {
        set.delete(socket);
      }
    }
  }
}

export function emitOrderChanged(orderId: number, status?: string): void {
  broadcastAdmin({ type: 'order:changed', orderId, status, at: nowIso() });
}

export function emitOrderNew(orderId: number, number: string, emergency: boolean): void {
  broadcastAdmin({ type: 'order:new', orderId, number, emergency, at: nowIso() });
}

export function emitSupport(clientId: number, messageId: number): void {
  broadcastAdmin({ type: 'support:new', clientId, messageId, at: nowIso() });
}

export function emitOfferExpired(orderId: number, number: string, masterId: number): void {
  broadcastAdmin({ type: 'offer:expired', orderId, number, masterId, at: nowIso() });
}

type MasterRow = {
  id: number;
  name: string;
  status: string;
  current_lat: number | null;
  current_lng: number | null;
  location_updated_at: string | null;
  telegram_id: number | null;
  active: number;
  skills: string;
  shift_started_at: string | null;
  phone: string;
  active_order_status: string | null;
  active_orders: number;
};

const BUSY_ORDER_STATUSES = ['accepted', 'arrived', 'approval', 'in_progress', 'done_by_master', 'admin_check'];

export function masterDisplayStatus(master: Pick<MasterRow, 'active' | 'status' | 'shift_started_at' | 'active_order_status'>): string {
  if (!master.active || master.status === 'offline' || !master.shift_started_at) return 'offline';
  if (master.active_order_status === 'on_way') return 'on_way';
  if (master.active_order_status && BUSY_ORDER_STATUSES.includes(master.active_order_status)) return 'busy';
  if (master.status === 'busy') return 'paused';
  return 'free';
}

export function mastersSnapshot(): Array<MasterRow & { skills_list: string[]; display_status: string }> {
  const rows = all<MasterRow>(
    `SELECT m.id, m.name, m.status, m.current_lat, m.current_lng, m.location_updated_at, m.telegram_id, m.active, m.skills,
       m.shift_started_at, m.phone,
       (SELECT o.status FROM orders o WHERE o.master_id = m.id AND o.status NOT IN ('closed','cancelled') ORDER BY o.id DESC LIMIT 1) AS active_order_status,
       (SELECT COUNT(*) FROM orders o WHERE o.master_id = m.id AND o.status NOT IN ('closed','cancelled')) AS active_orders
       FROM masters m WHERE m.active = 1 ORDER BY m.name`,
  );
  return rows.map((m) => ({ ...m, skills_list: parseJsonArray(m.skills), display_status: masterDisplayStatus(m) }));
}

export function ordersSnapshot(): Array<Record<string, unknown>> {
  return all(
    `SELECT id, number, status, address, lat, lng, is_emergency, created_at, master_id, works_total, urgency
       FROM orders WHERE status NOT IN ('closed', 'cancelled') ORDER BY created_at DESC`,
  );
}

export function updateMasterLocation(masterId: number, lat: number, lng: number, accuracy?: number): void {
  const at = nowIso();
  run('UPDATE masters SET current_lat = ?, current_lng = ?, location_updated_at = ? WHERE id = ?', [lat, lng, at, masterId]);
  broadcastAdmin({ type: 'master:location', masterId, lat, lng, accuracy: accuracy ?? null, at });
}

export function heartbeat(): void {
  for (const socket of adminSockets) {
    if (socket.readyState === 1) {
      try {
        socket.ping();
      } catch {
        adminSockets.delete(socket);
      }
    }
  }
  for (const set of masterSockets.values()) {
    for (const socket of set) {
      if (socket.readyState === 1) {
        try {
          socket.ping();
        } catch {
          set.delete(socket);
        }
      }
    }
  }
}
