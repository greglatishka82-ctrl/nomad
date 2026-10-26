import { config, nowIso } from '../config.ts';
import { settingNumber } from '../db.ts';

export { nowIso };

export const STATUS_LABELS: Record<string, string> = {
  new: 'Новая',
  reviewing: 'На рассмотрении',
  confirmed: 'Подтверждена',
  searching: 'Подбор мастера',
  offered: 'Предложена мастеру',
  accepted: 'Мастер назначен',
  on_way: 'Мастер в пути',
  arrived: 'Мастер на месте',
  approval: 'Согласование',
  in_progress: 'В работе',
  done_by_master: 'Выполнена мастером',
  admin_check: 'Проверка администратором',
  closed: 'Закрыта',
  cancelled: 'Отменена',
  rescheduled: 'Перенесена',
};

export const STATUS_LABELS_EN: Record<string, string> = {
  new: 'New',
  reviewing: 'Under review',
  confirmed: 'Confirmed',
  searching: 'Assigning master',
  offered: 'Offer sent',
  accepted: 'Master assigned',
  on_way: 'Master on the way',
  arrived: 'Master on site',
  approval: 'Approval',
  in_progress: 'In progress',
  done_by_master: 'Completed by master',
  admin_check: 'Dispatcher check',
  closed: 'Closed',
  cancelled: 'Cancelled',
  rescheduled: 'Rescheduled',
};

export const STATUS_LABELS_KZ: Record<string, string> = {
  new: 'Жаңа',
  reviewing: 'Қарауда',
  confirmed: 'Расталды',
  searching: 'Шебер іздеу',
  offered: 'Шеберге ұсынылды',
  accepted: 'Шебер тағайындалды',
  on_way: 'Жолда',
  arrived: 'Орнында',
  approval: 'Келісу',
  in_progress: 'Жұмыста',
  done_by_master: 'Шебер аяқтады',
  admin_check: 'Әкімші тексереді',
  closed: 'Жабылды',
  cancelled: 'Бас тартылды',
  rescheduled: 'Ауыстырылды',
};

export const STATUS_COLORS: Record<string, string> = {
  new: '#0b6bcb',
  reviewing: '#0b6bcb',
  confirmed: '#0e7490',
  searching: '#b45309',
  offered: '#b45309',
  accepted: '#7c3aed',
  on_way: '#0284c7',
  arrived: '#0d9488',
  approval: '#b45309',
  in_progress: '#0d9488',
  done_by_master: '#15803d',
  admin_check: '#15803d',
  closed: '#475569',
  cancelled: '#b91c1c',
  rescheduled: '#6b7280',
};

export const SOURCE_LABELS: Record<string, string> = {
  telegram: 'Telegram',
  phone: 'Телефон',
  whatsapp: 'WhatsApp',
  admin: 'Вручную',
};

export const URGENCY_LABELS: Record<string, string> = {
  normal: 'Обычная',
  urgent: 'Срочная',
  emergency: 'Аварийная',
};

export const TARIFF_LABELS: Record<string, string> = {
  day: 'Дневной',
  night: 'Ночной ×2',
  emergency_day: 'Аварийный дневной',
  emergency_night: 'Аварийный ночной',
};

export const ACTIVE_STATUSES = ['new', 'reviewing', 'confirmed', 'searching', 'offered', 'accepted', 'on_way', 'arrived', 'approval', 'in_progress'];

export function statusLabel(status: string, lang: 'ru' | 'kk' | 'en' = 'ru'): string {
  const map = lang === 'en' ? STATUS_LABELS_EN : lang === 'kk' ? STATUS_LABELS_KZ : STATUS_LABELS;
  return map[status] || status;
}

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] || source;
}

export function urgencyLabel(urgency: string): string {
  return URGENCY_LABELS[urgency] || urgency;
}

export function tariffLabel(tariff: string): string {
  return TARIFF_LABELS[tariff] || tariff;
}

export function normalizePhone(input: string | null | undefined): string {
  let digits = String(input || '').replace(/\D/g, '');
  if (!digits) return '';
  if (digits.length === 11 && digits.startsWith('8')) digits = '7' + digits.slice(1);
  if (digits.length === 10) digits = '7' + digits;
  return digits ? '+' + digits : '';
}

export function phoneDigits(input: string | null | undefined): string {
  return String(input || '').replace(/\D/g, '');
}

export function prettyPhone(input: string | null | undefined): string {
  const d = phoneDigits(input);
  if (d.length === 11 && d.startsWith('7')) {
    return `+7 (${d.slice(1, 4)}) ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9, 11)}`;
  }
  return input || '';
}

export function money(value: number | null | undefined): string {
  const n = Number(value || 0);
  return new Intl.NumberFormat('ru-RU').format(n) + ' ₸';
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('ru-RU', {
    timeZone: config.timezone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString('ru-RU', { timeZone: config.timezone, day: '2-digit', month: '2-digit', year: 'numeric' });
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString('ru-RU', { timeZone: config.timezone, hour: '2-digit', minute: '2-digit' });
}

export function isoDate(value: Date = new Date()): string {
  return value.toISOString();
}

export function addMinutes(date: Date, minutes: number): Date {
  return new Date(date.getTime() + minutes * 60_000);
}

export function minutesBetween(from: string | null | undefined, to: string | null | undefined): number | null {
  if (!from || !to) return null;
  const a = new Date(from).getTime();
  const b = new Date(to).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.max(0, Math.round((b - a) / 60_000));
}

export function localHour(date: Date = new Date()): number {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: config.timezone,
    hour: '2-digit',
    hour12: false,
  }).formatToParts(date);
  return Number(parts.find((p) => p.type === 'hour')?.value || 0);
}

export function isNight(date: Date = new Date()): boolean {
  const start = settingNumber('night_start', 22);
  const end = settingNumber('night_end', 8);
  const hour = localHour(date);
  if (start > end) return hour >= start || hour < end;
  return hour >= start && hour < end;
}

export function nightMultiplier(): number {
  return settingNumber('night_multiplier', 2);
}

export function masterShare(): number {
  const value = settingNumber('master_share', 0.7);
  return value > 1 ? value / 100 : value;
}

export function calcEarnings(worksTotal: number): { master: number; company: number } {
  const share = masterShare();
  const master = Math.round(Number(worksTotal || 0) * share);
  return { master, company: Math.max(0, Number(worksTotal || 0) - master) };
}

export function parseJsonArray(value: string | null | undefined): string[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

export function safeJson<T>(value: string | null | undefined, fallback: T): T {
  if (!value) return fallback;
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function locationAgeMinutes(updatedAt: string | null | undefined): number | null {
  if (!updatedAt) return null;
  const t = new Date(updatedAt).getTime();
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.round((Date.now() - t) / 60_000));
}

export function orderNumber(id: number): string {
  return `KR-${String(id).padStart(5, '0')}`;
}

export function escapeHtml(value: string): string {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function pick<T extends Record<string, unknown>>(obj: T, keys: string[]): Partial<T> {
  const out: Record<string, unknown> = {};
  for (const key of keys) if (key in obj) out[key] = obj[key];
  return out as Partial<T>;
}

export function numeric(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

export function boolInt(value: unknown): number {
  return value === true || value === 1 || value === '1' || value === 'true' ? 1 : 0;
}
