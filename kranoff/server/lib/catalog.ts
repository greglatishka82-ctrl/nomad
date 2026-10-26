import { all, get, getSettings, settingNumber } from '../db.ts';
import { config } from '../config.ts';
import { isNight, money, nightMultiplier, parseJsonArray } from './utils.ts';

export type PublicService = {
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
};

export type PublicCategory = {
  id: number;
  slug: string;
  name_ru: string;
  name_en: string;
  name_kz: string;
  icon: string;
  sort: number;
  services: PublicService[];
};

export function getService(id: number): PublicService | undefined {
  const row = get<Omit<PublicService, 'skills'> & { skills: string }>('SELECT * FROM services WHERE id = ? AND active = 1', [id]);
  if (!row) return undefined;
  return { ...row, skills: parseJsonArray(row.skills) };
}

export function getCatalog(): PublicCategory[] {
  const categories = all<{ id: number; slug: string; name_ru: string; name_en: string; name_kz: string; icon: string; sort: number }>(
    'SELECT * FROM service_categories ORDER BY sort, id',
  );
  const services = all<Omit<PublicService, 'skills'> & { skills: string }>('SELECT * FROM services WHERE active = 1 ORDER BY sort, id');
  return categories.map((cat) => ({
    ...cat,
    services: services
      .filter((s) => s.category_id === cat.id)
      .map((s) => ({ ...s, skills: parseJsonArray(s.skills) })),
  }));
}

export function emergencyService(): PublicService | undefined {
  const row = get<Omit<PublicService, 'skills'> & { skills: string }>(
    'SELECT * FROM services WHERE active = 1 AND is_emergency = 1 ORDER BY sort, id LIMIT 1',
  );
  if (!row) return undefined;
  return { ...row, skills: parseJsonArray(row.skills) };
}

export function tariffFor(isEmergency: boolean, date: Date = new Date()): 'day' | 'night' | 'emergency_day' | 'emergency_night' {
  if (isEmergency) return isNight(date) ? 'emergency_night' : 'emergency_day';
  return isNight(date) ? 'night' : 'day';
}

export function computeServicePrice(service: Pick<PublicService, 'price' | 'price_night' | 'is_emergency'>, tariff: string): number {
  const mult = nightMultiplier();
  if (tariff === 'emergency_night') return Number(service.price_night ?? Math.round(service.price * mult));
  if (tariff === 'emergency_day') return Number(service.price);
  if (tariff === 'night') return Math.round(service.price * mult);
  return Number(service.price);
}

export function priceLabel(service: Pick<PublicService, 'price' | 'price_night' | 'is_emergency'>, lang: 'ru' | 'kk' | 'en' = 'ru'): string {
  const prefix = lang === 'kk' ? 'бастап' : lang === 'en' ? 'from' : 'от';
  if (service.price === 0) return lang === 'kk' ? 'тегін' : lang === 'en' ? 'free' : 'бесплатно';
  return `${prefix} ${money(service.price)}`;
}

export function getCompany(): Record<string, string> {
  const s = getSettings();
  return {
    name: s.company_name || 'KRANOFF',
    slogan: s.company_slogan || 'Решил — Действуй',
    phone: s.company_phone || config.companyPhone || '',
    whatsapp: s.company_whatsapp || s.company_phone || config.companyWhatsapp || config.companyPhone || '',
    email: s.company_email || config.companyEmail || '',
    address: s.company_address || config.companyAddress || 'Павлодар',
    hours: s.company_hours || 'Круглосуточно',
    telegram: s.company_telegram || config.clientBotUsername,
    about_ru: s.company_about_ru || '',
    about_en: s.company_about_en || '',
    about_kz: s.company_about_kz || '',
    guarantee_ru: s.company_guarantee_ru || '',
    guarantee_en: s.company_guarantee_en || '',
    guarantee_kz: s.company_guarantee_kz || '',
    coverage_ru: s.company_coverage_ru || '',
    coverage_en: s.company_coverage_en || '',
    coverage_kz: s.company_coverage_kz || '',
    privacy_ru: s.company_privacy_ru || '',
    privacy_en: s.company_privacy_en || '',
    privacy_kz: s.company_privacy_kz || '',
    service_area_note: s.service_area_note || '',
  };
}

export function getPublicSettings(): Record<string, string> {
  const s = getSettings();
  return {
    company_name: s.company_name || '',
    company_slogan: s.company_slogan || '',
    company_phone: s.company_phone || '',
    company_whatsapp: s.company_whatsapp || '',
    company_email: s.company_email || '',
    company_address: s.company_address || '',
    company_hours: s.company_hours || '',
    company_telegram: s.company_telegram || config.clientBotUsername,
    night_start: s.night_start || '22',
    night_end: s.night_end || '8',
    night_multiplier: s.night_multiplier || '2',
    currency: s.currency || 'KZT',
    service_area_note: s.service_area_note || '',
  };
}

export function nightInfo(): { isNight: boolean; start: number; end: number; multiplier: number } {
  return {
    isNight: isNight(),
    start: settingNumber('night_start', 22),
    end: settingNumber('night_end', 8),
    multiplier: nightMultiplier(),
  };
}
