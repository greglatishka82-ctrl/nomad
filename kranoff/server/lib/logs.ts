import { STATUS_LABELS } from './utils.ts';

export type ActivityRow = {
  id: number;
  admin_id: number | null;
  admin_name: string | null;
  action: string;
  entity: string;
  entity_id: number | null;
  details: string;
  created_at: string;
};

const SETTING_LABELS: Record<string, string> = {
  company_phone: 'телефон',
  company_telegram: 'Telegram-бот',
  company_hours: 'часы работы',
  night_start: 'начало ночного тарифа',
  night_end: 'конец ночного тарифа',
  night_multiplier: 'ночной коэффициент',
  master_share: 'доля мастера',
  offer_timeout_minutes: 'таймаут предложения',
  min_masters_on_shift: 'минимум мастеров на смене',
  currency: 'валюта',
};

function trim(text: string, length = 160): string {
  const value = (text || '').replace(/\s+/g, ' ').trim();
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}

export function describeLog(row: ActivityRow): string {
  const details = row.details || '';
  if (row.action === 'event') return details || 'Действие';
  switch (row.action) {
    case 'auth.login':
      return 'Вход в панель';
    case 'auth.logout':
      return 'Выход из панели';
    case 'auth.login_failed':
      return `Неудачная попытка входа${details ? `: ${details}` : ''}`;
    case 'auth.password':
      return 'Смена пароля администратора';
    case 'shift.start':
      return 'Начало смены';
    case 'shift.end':
      return 'Завершение смены';
    case 'order.create':
      return `Создан заказ${details ? `: ${details}` : ''}`;
    case 'order.update':
      return `Изменена карточка заказа${details ? `: ${details}` : ''}`;
    case 'order.items':
      return 'Изменён состав работ заказа';
    case 'order.assign':
      return `Назначение мастера${details ? `: ${details}` : ''}`;
    case 'order.status':
      return `Изменён статус заказа: ${STATUS_LABELS[details] || details || 'без статуса'}`;
    case 'order.close':
      return `Заказ закрыт${details ? `, сумма: ${details}` : ''}`;
    case 'order.cancel':
      return 'Заказ отменён';
    case 'order.delete':
      return `Удалён заказ${details ? `: ${details}` : ''}`;
    case 'order.eta':
      return `Сообщено время прибытия${details ? `: ${details}` : ''}`;
    case 'order.master_action':
      return `Действие по заказу${details ? `: ${details}` : ''}`;
    case 'media.upload':
      return `Загружен файл${details ? `: ${details}` : ''}`;
    case 'media.delete':
      return `Удалён файл${details ? `: ${details}` : ''}`;
    case 'media.bulk_delete':
      return `Удалены медиа${details ? `: ${details}` : ''}`;
    case 'order.pdf':
      return `Скачана PDF-карточка${details ? `: ${details}` : ''}`;
    case 'order.archive':
      return `Скачан архив${details ? `: ${details}` : ''}`;
    case 'client.create':
      return `Добавлен клиент${details ? `: ${details}` : ''}`;
    case 'client.update':
      return `Изменён клиент${details ? `: ${details}` : ''}`;
    case 'master.create':
      return `Добавлен мастер${details ? `: ${details}` : ''}`;
    case 'master.update':
      return `Изменён мастер${details ? `: ${details}` : ''}`;
    case 'master.deactivate':
      return `Отключён мастер${details ? `: ${details}` : ''}`;
    case 'master.status':
      return `Изменён статус мастера${details ? `: ${details}` : ''}`;
    case 'master.regenerate_token':
      return `Обновлена ссылка страницы мастера${details ? `: ${details}` : ''}`;
    case 'master.unlink_telegram':
      return `Отвязан Telegram мастера${details ? `: ${details}` : ''}`;
    case 'service.create':
      return `Добавлена услуга${details ? `: ${details}` : ''}`;
    case 'service.update':
      return `Изменена услуга${details ? `: ${details}` : ''}`;
    case 'service.delete':
      return `Удалена услуга${details ? `: ${details}` : ''}`;
    case 'category.create':
      return `Добавлена категория${details ? `: ${details}` : ''}`;
    case 'category.update':
      return `Изменена категория${details ? `: ${details}` : ''}`;
    case 'category.delete':
      return `Удалена категория${details ? `: ${details}` : ''}`;
    case 'settings.update': {
      const labels = details.split(',').filter(Boolean).map((key) => SETTING_LABELS[key] || key);
      return `Изменены настройки${labels.length ? `: ${labels.join(', ')}` : ''}`;
    }
    case 'admin.create':
      return `Добавлен администратор${details ? `: ${details}` : ''}`;
    case 'admin.update':
      return `Изменён администратор${details ? `: ${details}` : ''}`;
    case 'support.read':
      return `Открыт диалог поддержки${details ? `: ${details}` : ''}`;
    case 'support.reply':
      return `Ответ клиенту${details ? `: ${trim(details)}` : ''}`;
    default:
      return `${row.action}${details ? `: ${details}` : ''}`;
  }
}
