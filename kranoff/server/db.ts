import { DatabaseSync } from 'node:sqlite';
import bcrypt from 'bcryptjs';
import { randomBytes } from 'node:crypto';
import { config } from './config.ts';
import { CATEGORIES, SERVICES, DEFAULT_SETTINGS, KZ_CATEGORIES, KZ_SERVICES, KZ_STANDARD, KZ_EMERGENCY } from './seed.ts';

export const db = new DatabaseSync(config.dbPath);
db.exec('PRAGMA journal_mode = WAL;');
db.exec('PRAGMA foreign_keys = ON;');
db.exec('PRAGMA busy_timeout = 5000;');

const SCHEMA = `
CREATE TABLE IF NOT EXISTS admins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  login TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT 'Администратор',
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS admin_sessions (
  token TEXT PRIMARY KEY,
  admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON admin_sessions(expires_at);

CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_shifts_admin ON shifts(admin_id, started_at);

CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_id INTEGER UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  phone TEXT,
  address TEXT NOT NULL DEFAULT '',
  language TEXT NOT NULL DEFAULT 'ru',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_phone ON clients(phone) WHERE phone IS NOT NULL AND phone <> '';
CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(name);

CREATE TABLE IF NOT EXISTS masters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT NOT NULL UNIQUE,
  telegram_id INTEGER UNIQUE,
  skills TEXT NOT NULL DEFAULT '[]',
  tools TEXT NOT NULL DEFAULT '',
  districts TEXT NOT NULL DEFAULT '',
  night_available INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'offline',
  current_lat REAL,
  current_lng REAL,
  location_updated_at TEXT,
  shift_started_at TEXT,
  page_token TEXT NOT NULL UNIQUE,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_masters_status ON masters(status, active);

CREATE TABLE IF NOT EXISTS service_categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name_ru TEXT NOT NULL,
  name_en TEXT NOT NULL,
  name_kz TEXT NOT NULL DEFAULT '',
  icon TEXT NOT NULL DEFAULT '',
  sort INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS services (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER NOT NULL REFERENCES service_categories(id) ON DELETE CASCADE,
  name_ru TEXT NOT NULL,
  name_en TEXT NOT NULL,
  name_kz TEXT NOT NULL DEFAULT '',
  description_ru TEXT NOT NULL DEFAULT '',
  description_en TEXT NOT NULL DEFAULT '',
  description_kz TEXT NOT NULL DEFAULT '',
  price INTEGER NOT NULL DEFAULT 0,
  price_night INTEGER,
  is_emergency INTEGER NOT NULL DEFAULT 0,
  skills TEXT NOT NULL DEFAULT '[]',
  active INTEGER NOT NULL DEFAULT 1,
  sort INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_services_cat ON services(category_id, active, sort);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  number TEXT NOT NULL UNIQUE,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  master_id INTEGER REFERENCES masters(id),
  status TEXT NOT NULL DEFAULT 'new',
  source TEXT NOT NULL DEFAULT 'telegram',
  problem TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  lat REAL,
  lng REAL,
  entrance TEXT NOT NULL DEFAULT '',
  floor TEXT NOT NULL DEFAULT '',
  intercom TEXT NOT NULL DEFAULT '',
  contact_note TEXT NOT NULL DEFAULT '',
  desired_at TEXT,
  desired_label TEXT NOT NULL DEFAULT '',
  urgency TEXT NOT NULL DEFAULT 'normal',
  is_emergency INTEGER NOT NULL DEFAULT 0,
  tariff TEXT NOT NULL DEFAULT 'day',
  works_total INTEGER NOT NULL DEFAULT 0,
  materials_note TEXT NOT NULL DEFAULT '',
  master_earnings INTEGER NOT NULL DEFAULT 0,
  company_share INTEGER NOT NULL DEFAULT 0,
  guarantee_months INTEGER,
  admin_id INTEGER REFERENCES admins(id),
  assigned_by INTEGER REFERENCES admins(id),
  shift_id INTEGER REFERENCES shifts(id),
  offer_expires_at TEXT,
  confirmed_at TEXT,
  accepted_at TEXT,
  on_way_at TEXT,
  arrived_at TEXT,
  started_at TEXT,
  completed_at TEXT,
  closed_at TEXT,
  cancelled_at TEXT,
  eta_minutes INTEGER,
  language TEXT NOT NULL DEFAULT 'ru',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at);
CREATE INDEX IF NOT EXISTS idx_orders_client ON orders(client_id, created_at);
CREATE INDEX IF NOT EXISTS idx_orders_master ON orders(master_id, created_at);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_number ON orders(number);

CREATE TABLE IF NOT EXISTS order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  service_id INTEGER REFERENCES services(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  price INTEGER NOT NULL DEFAULT 0,
  qty INTEGER NOT NULL DEFAULT 1,
  is_extra INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);

CREATE TABLE IF NOT EXISTS order_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  actor TEXT NOT NULL DEFAULT 'system',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_events_order ON order_events(order_id, created_at);

CREATE TABLE IF NOT EXISTS offers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  master_id INTEGER NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending',
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  responded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_offers_order ON offers(order_id, status);
CREATE INDEX IF NOT EXISTS idx_offers_pending ON offers(status, expires_at);

CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'before',
  file_name TEXT NOT NULL,
  mime TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_media_order ON media(order_id);

CREATE TABLE IF NOT EXISTS support_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  direction TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  admin_id INTEGER,
  telegram_message_id INTEGER,
  read_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_support_client ON support_messages(client_id, created_at);
CREATE INDEX IF NOT EXISTS idx_support_unread ON support_messages(direction, read_at);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  admin_id INTEGER,
  action TEXT NOT NULL,
  entity TEXT NOT NULL DEFAULT '',
  entity_id INTEGER,
  details TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
`;

db.exec(SCHEMA);

function ensureColumn(table: string, column: string, definition: string): void {
  const columns = db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>;
  if (!columns.some((item) => item.name === column)) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`);
  }
}

ensureColumn('service_categories', 'name_kz', "TEXT NOT NULL DEFAULT ''");
ensureColumn('services', 'name_kz', "TEXT NOT NULL DEFAULT ''");
ensureColumn('services', 'description_kz', "TEXT NOT NULL DEFAULT ''");

function applyKzTranslations(): void {
  for (const [slug, name] of Object.entries(KZ_CATEGORIES)) {
    run("UPDATE service_categories SET name_kz = ? WHERE slug = ? AND (name_kz IS NULL OR name_kz = '')", [name, slug]);
  }
  for (const [nameRu, kz] of Object.entries(KZ_SERVICES)) {
    const desc = kz.desc || KZ_STANDARD;
    run("UPDATE services SET name_kz = ?, description_kz = CASE WHEN description_kz IS NULL OR description_kz = '' THEN ? ELSE description_kz END WHERE name_ru = ?", [
      kz.name,
      desc,
      nameRu,
    ]);
  }
}

export function all<T = Record<string, unknown>>(sql: string, params: unknown[] = []): T[] {
  return db.prepare(sql).all(...(params as never[])) as T[];
}

export function get<T = Record<string, unknown>>(sql: string, params: unknown[] = []): T | undefined {
  return db.prepare(sql).get(...(params as never[])) as T | undefined;
}

export function run(sql: string, params: unknown[] = []): { changes: number; lastInsertRowid: number } {
  const res = db.prepare(sql).run(...(params as never[]));
  return { changes: Number(res.changes), lastInsertRowid: Number(res.lastInsertRowid) };
}

export function tx<T>(fn: () => T): T {
  db.exec('BEGIN');
  try {
    const result = fn();
    db.exec('COMMIT');
    return result;
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}

export function getSetting(key: string, fallback = ''): string {
  const row = get<{ value: string }>('SELECT value FROM settings WHERE key = ?', [key]);
  return row ? row.value : fallback;
}

export function getSettings(): Record<string, string> {
  const rows = all<{ key: string; value: string }>('SELECT key, value FROM settings');
  const out: Record<string, string> = {};
  for (const row of rows) out[row.key] = row.value;
  return out;
}

export function setSetting(key: string, value: string): void {
  run('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value', [key, value]);
}

export function setSettings(values: Record<string, string>): void {
  tx(() => {
    for (const [key, value] of Object.entries(values)) setSetting(key, String(value ?? ''));
  });
}

export function settingNumber(key: string, fallback: number): number {
  const value = Number(getSetting(key, String(fallback)));
  return Number.isFinite(value) ? value : fallback;
}

export function newMasterToken(): string {
  return randomBytes(24).toString('base64url');
}

function seed(): void {
  const adminCount = get<{ c: number }>('SELECT COUNT(*) AS c FROM admins')?.c ?? 0;
  if (adminCount === 0) {
    const hash = bcrypt.hashSync(config.adminPassword, 10);
    run('INSERT INTO admins (login, password_hash, name) VALUES (?, ?, ?)', [config.adminLogin, hash, 'Администратор']);
    console.log(`[seed] created admin "${config.adminLogin}"`);
  }

  for (const [key, value] of Object.entries(DEFAULT_SETTINGS)) {
    run('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING', [key, value]);
  }

  const serviceCount = get<{ c: number }>('SELECT COUNT(*) AS c FROM services')?.c ?? 0;
  if (serviceCount === 0) {
    const catIds = new Map<string, number>();
    for (const cat of CATEGORIES) {
      const id = run(
        'INSERT INTO service_categories (slug, name_ru, name_en, name_kz, icon, sort) VALUES (?, ?, ?, ?, ?, ?)',
        [cat.slug, cat.name_ru, cat.name_en, KZ_CATEGORIES[cat.slug] || cat.name_ru, cat.icon, cat.sort],
      ).lastInsertRowid;
      catIds.set(cat.slug, id);
    }
    for (const s of SERVICES) {
      const catId = catIds.get(s.cat);
      if (!catId) continue;
      const kz = KZ_SERVICES[s.name_ru];
      run(
        `INSERT INTO services (category_id, name_ru, name_en, name_kz, description_ru, description_en, description_kz, price, price_night, is_emergency, skills, sort)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        [
          catId,
          s.name_ru,
          s.name_en,
          kz?.name || s.name_ru,
          s.desc_ru,
          s.desc_en,
          kz?.desc || (s.emergency ? KZ_EMERGENCY : KZ_STANDARD),
          s.price,
          s.price_night ?? null,
          s.emergency ? 1 : 0,
          JSON.stringify(s.skills),
          s.sort,
        ],
      );
    }
    console.log(`[seed] created ${CATEGORIES.length} categories and ${SERVICES.length} services`);
  }
  applyKzTranslations();
}

seed();

export function logActivity(adminId: number | null, action: string, entity = '', entityId: number | null = null, details = ''): void {
  run('INSERT INTO activity_log (admin_id, action, entity, entity_id, details) VALUES (?, ?, ?, ?, ?)', [
    adminId,
    action,
    entity,
    entityId,
    details,
  ]);
}

export function logAction(adminId: number | null, message: string, entity = '', entityId: number | null = null, action = 'event'): void {
  logActivity(adminId, action, entity, entityId, message);
}
