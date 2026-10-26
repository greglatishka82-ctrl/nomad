import { useCallback, useEffect, useState } from 'react';

export type Company = {
  name: string;
  slogan: string;
  phone: string;
  whatsapp: string;
  email: string;
  address: string;
  hours: string;
  telegram: string;
  about_ru: string;
  about_en: string;
  about_kz: string;
  guarantee_ru: string;
  guarantee_en: string;
  guarantee_kz: string;
  coverage_ru: string;
  coverage_en: string;
  coverage_kz: string;
  privacy_ru: string;
  privacy_en: string;
  privacy_kz: string;
  service_area_note: string;
};

export type Service = {
  id: number;
  category_id: number;
  name_ru: string;
  name_en: string;
  name_kz: string;
  description_ru: string;
  description_en: string;
  description_kz: string;
  price: number;
  price_night: number | null;
  is_emergency: number;
  skills: string[];
  sort: number;
  active?: number;
};

export type Category = {
  id: number;
  slug: string;
  name_ru: string;
  name_en: string;
  name_kz: string;
  icon: string;
  sort: number;
  services: Service[];
};

export type Bootstrap = {
  company: Company;
  settings: Record<string, string>;
  categories: Category[];
  night: { isNight: boolean; start: number; end: number; multiplier: number };
  bot: string;
  baseUrl: string;
};

export type AdminUser = { id: number; login: string; name: string; is_active: number };
export type Shift = { id: number; admin_id: number; started_at: string; ended_at: string | null };

export type Master = {
  id: number;
  name: string;
  phone: string;
  telegram_id: number | null;
  skills: string;
  skills_list?: string[];
  tools: string;
  districts: string;
  night_available: number;
  active: number;
  status: 'free' | 'busy' | 'offline' | string;
  current_lat: number | null;
  current_lng: number | null;
  location_updated_at: string | null;
  shift_started_at: string | null;
  page_token: string;
  page_url?: string;
  note: string;
  created_at: string;
  closed_orders?: number;
  active_orders?: number;
  earnings?: { shift: number; last24h: number; today: number; orders: number };
  match_count?: number;
  distance_km?: number | null;
  location_age_minutes?: number | null;
  can_take?: boolean;
  active_order_status?: string | null;
  display_status?: string;
};

export type Client = {
  id: number;
  telegram_id: number | null;
  name: string;
  phone: string | null;
  phone_pretty?: string;
  address: string;
  language: string;
  note: string;
  created_at: string;
  updated_at: string;
  orders_count?: number;
  total_spent?: number | null;
  last_order_at?: string | null;
  unread?: number;
};

export type Order = {
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
  client_name?: string;
  client_phone?: string | null;
  master_name?: string | null;
  items_count?: number;
};

export type Media = { id: number; order_id: number; kind: string; file_name: string; mime: string; size: number; created_at: string };
export type OrderItem = { id: number; order_id: number; service_id: number | null; name: string; price: number; qty: number; is_extra: number };
export type OrderEvent = { id: number; order_id: number; type: string; text: string; actor: string; created_at: string };
export type Offer = { id: number; order_id: number; master_id: number; status: string; expires_at: string; created_at: string; responded_at: string | null };

export type OrderFull = Order & {
  client: Client;
  master: Master | null;
  items: OrderItem[];
  media: Media[];
  events: OrderEvent[];
  offers: Offer[];
};

export type DashboardData = {
  counts: {
    new: number;
    emergency: number;
    onShift: number;
    onWay: number;
    atWork: number;
    unreadSupport: number;
    closedToday: number;
    revenueToday: number;
  };
  latest: Order[];
  masters: Master[];
  stale: Master[];
  offers: Array<{ id: number; order_id: number; number: string; master_id: number; expires_at: string; master_name: string }>;
};

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  const hasBody = init?.body !== undefined && init?.body !== null;
  if (hasBody && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const res = await fetch(path, { credentials: 'same-origin', ...init, headers });
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const message = (data as { error?: string } | null)?.error || `Ошибка ${res.status}`;
    throw new Error(message);
  }
  return data as T;
}

export function useFetch<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api<T>(path);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, ...deps]);

  return { data, setData, loading, error, reload };
}

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
  admin_check: 'Проверка админом',
  closed: 'Закрыта',
  cancelled: 'Отменена',
  rescheduled: 'Перенесена',
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

export const SOURCE_LABELS: Record<string, string> = { telegram: 'Telegram', phone: 'Телефон', whatsapp: 'WhatsApp', admin: 'Вручную' };
export const URGENCY_LABELS: Record<string, string> = { normal: 'Обычная', urgent: 'Срочная', emergency: 'Аварийная' };
export const TARIFF_LABELS: Record<string, string> = {
  day: 'Дневной',
  night: 'Ночной ×2',
  emergency_day: 'Аварийный дневной',
  emergency_night: 'Аварийный ночной',
};

export const ACTIVE_STATUSES = ['new', 'reviewing', 'confirmed', 'searching', 'offered', 'accepted', 'on_way', 'arrived', 'approval', 'in_progress'];

export function money(value: number | null | undefined): string {
  return new Intl.NumberFormat('ru-RU').format(Number(value || 0)) + ' ₸';
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

export function prettyPhone(phone: string | null | undefined): string {
  const digits = String(phone || '').replace(/\D/g, '');
  if (digits.length === 11 && digits.startsWith('7')) {
    return `+7 (${digits.slice(1, 4)}) ${digits.slice(4, 7)}-${digits.slice(7, 9)}-${digits.slice(9, 11)}`;
  }
  return phone || '—';
}

export function relativeMinutes(value: string | null | undefined): string {
  if (!value) return 'нет данных';
  const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60_000));
  if (minutes < 1) return 'только что';
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  return `${hours} ч назад`;
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status;
}

export function statusColor(status: string): string {
  return STATUS_COLORS[status] || '#64748b';
}

export function useDebounced<T>(value: T, delay = 350): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function escapeHtml(value: string | null | undefined): string {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
