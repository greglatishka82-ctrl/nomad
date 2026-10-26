import { Bot, InlineKeyboard, Keyboard, InputFile, type Context } from 'grammy';
import { config } from '../config.ts';
import { getMaster, getMasterByPhone, getMasterByTelegram, setMasterStatus, type MasterRow, type OrderFull } from '../lib/orders.ts';
import {
  acceptOffer,
  declineOffer,
  getOrder,
  getOrderFull,
  masterAbort,
  masterAction,
  masterActiveOrder,
  masterEarnings,
  setEta,
  setOrderNotifier,
} from '../lib/orders.ts';
import { getOffer } from '../lib/orders.ts';
import { saveMediaBuffer, downloadTelegramFile, listMedia, mediaAbsolutePath } from '../lib/media.ts';
import { formatDateTime, money, normalizePhone, prettyPhone, statusLabel, tariffLabel, urgencyLabel } from '../lib/utils.ts';

type Pending = { orderId: number };
const awaitingEta = new Map<number, Pending>();
const awaitingAfter = new Map<number, Pending>();
let masterBot: Bot | null = null;

const MENU = {
  free: '🟢 На линии',
  busy: '🟠 Занят',
  off: '⚫ Завершить смену',
  order: '📦 Мой заказ',
  money: '💰 Заработок',
  history: '🕘 История',
};

function menu(): Keyboard {
  return new Keyboard()
    .text(MENU.free)
    .text(MENU.busy)
    .row()
    .text(MENU.order)
    .text(MENU.money)
    .row()
    .text(MENU.history)
    .text(MENU.off)
    .resized()
    .persistent();
}

function pageUrl(master: MasterRow): string {
  return `${config.publicBaseUrl}/m/${master.page_token}`;
}

function pageKeyboard(master: MasterRow): InlineKeyboard {
  const kb = new InlineKeyboard();
  if (config.publicBaseUrl.startsWith('https://')) kb.url('🗺 Открыть рабочую страницу', pageUrl(master));
  return kb;
}

function orderActions(order: OrderFull, master: MasterRow): InlineKeyboard {
  const kb = new InlineKeyboard();
  if (order.status === 'accepted') kb.text('🚗 Выехал', `order:on_way:${order.id}`).row();
  if (order.status === 'on_way') kb.text('📍 На месте', `order:arrived:${order.id}`).row();
  if (order.status === 'arrived' || order.status === 'approval') kb.text('🔧 Начал работу', `order:start:${order.id}`).row();
  if (order.status === 'in_progress') kb.text('✅ Выполнено', `order:done:${order.id}`).row();
  if (['accepted', 'on_way', 'arrived', 'approval'].includes(order.status)) kb.text('⚠️ Не могу выехать', `order:abort:${order.id}`).row();
  if (config.publicBaseUrl.startsWith('https://')) kb.url('🗺 Рабочая страница', pageUrl(master)).row();
  kb.text('🔄 Обновить', `order:refresh:${order.id}`);
  return kb;
}

function orderCard(order: OrderFull, master: MasterRow): string {
  const lines = [
    `Заказ ${order.number}`,
    `Статус: ${statusLabel(order.status)}`,
    '',
    `Проблема: ${order.problem}`,
    order.description ? `Описание: ${order.description}` : '',
    `Адрес: ${order.address || 'уточняется'}`,
    [order.entrance ? `Подъезд: ${order.entrance}` : '', order.floor ? `Этаж: ${order.floor}` : '', order.intercom ? `Домофон: ${order.intercom}` : '']
      .filter(Boolean)
      .join(' · '),
    `Клиент: ${order.client.name}`,
    order.client.phone ? `Телефон: ${prettyPhone(order.client.phone)}` : '',
    `Тариф: ${tariffLabel(order.tariff)}`,
    `Срочность: ${urgencyLabel(order.urgency)}`,
    `Стоимость работ: ${money(order.works_total)}`,
    order.eta_minutes != null ? `В пути: ${order.eta_minutes} мин.` : '',
    order.desired_label || order.desired_at ? `Желаемое время: ${order.desired_label || formatDateTime(order.desired_at)}` : '',
  ].filter((line) => line !== '');
  if (order.status === 'done_by_master' || order.status === 'admin_check') lines.push('', 'Ожидает проверки администратора.');
  if (order.status === 'closed') lines.push('', `Закрыт. Ваш заработок: ${money(order.master_earnings)}.`);
  return lines.join('\n');
}

function masterOf(ctx: Context): MasterRow | undefined {
  if (!ctx.from) return undefined;
  return getMasterByTelegram(ctx.from.id);
}

async function showOrder(ctx: Context, master: MasterRow): Promise<void> {
  const active = masterActiveOrder(master.id);
  if (!active) {
    await ctx.reply('Нет активного заказа. Отметьтесь «На линии», чтобы получать предложения.', { reply_markup: menu() });
    return;
  }
  const full = getOrderFull(active.id);
  if (!full) {
    await ctx.reply('Заказ не найден.');
    return;
  }
  await ctx.reply(orderCard(full, master), { reply_markup: orderActions(full, master) });
}

async function showEarnings(ctx: Context, master: MasterRow): Promise<void> {
  const totals = masterEarnings(master.id);
  await ctx.reply(
    [
      'Заработок',
      `За смену: ${money(totals.shift)} · заказов: ${totals.orders}`,
      `За последние 24 часа: ${money(totals.last24h)}`,
      `Сегодня: ${money(totals.today)}`,
    ].join('\n'),
    { reply_markup: menu() },
  );
}

async function showHistory(ctx: Context, master: MasterRow): Promise<void> {
  const { all } = await import('../db.ts');
  const rows = all<{ number: string; status: string; closed_at: string | null; created_at: string; works_total: number; master_earnings: number }>(
    'SELECT number, status, closed_at, created_at, works_total, master_earnings FROM orders WHERE master_id = ? ORDER BY id DESC LIMIT 10',
    [master.id],
  );
  if (!rows.length) {
    await ctx.reply('История пуста.', { reply_markup: menu() });
    return;
  }
  const lines = rows.map(
    (r) => `${r.number} · ${formatDateTime(r.closed_at || r.created_at)} · ${statusLabel(r.status)} · ${money(r.works_total)} (вам ${money(r.master_earnings)})`,
  );
  await ctx.reply(['История заказов:', ...lines].join('\n'), { reply_markup: menu() });
}

async function askEta(ctx: Context, orderId: number): Promise<void> {
  const kb = new InlineKeyboard()
    .text('15 мин', `eta:${orderId}:15`)
    .text('30 мин', `eta:${orderId}:30`)
    .row()
    .text('45 мин', `eta:${orderId}:45`)
    .text('60 мин', `eta:${orderId}:60`)
    .row()
    .text('90 мин', `eta:${orderId}:90`)
    .text('Другое', `eta_custom:${orderId}`);
  await ctx.reply('Через сколько минут будете на месте?', { reply_markup: kb });
}

async function sendOrderMedia(chatId: number, orderId: number): Promise<void> {
  if (!masterBot) return;
  const photos = listMedia(orderId).filter((media) => media.mime.startsWith('image/')).slice(0, 3);
  for (const photo of photos) {
    try {
      await masterBot.api.sendPhoto(chatId, new InputFile(mediaAbsolutePath(photo)));
    } catch (error) {
      console.error('[master-bot] photo', error);
    }
  }
}

export async function sendMasterOffer(offerId: number): Promise<void> {
  if (!masterBot) return;
  const offer = getOffer(offerId);
  if (!offer) return;
  const master = getMaster(offer.master_id);
  const order = getOrderFull(offer.order_id);
  if (!master?.telegram_id || !order) return;
  const kb = new InlineKeyboard().text('✅ Принять', `offer:accept:${offer.id}`).text('❌ Отказаться', `offer:decline:${offer.id}`);
  const text = [
    '🔔 Новый заказ',
    orderCard(order, master),
    '',
    'Примите заказ в течение 5 минут или откажитесь. Если не ответите — администратор позвонит.',
  ].join('\n');
  try {
    await sendOrderMedia(master.telegram_id, order.id);
    await masterBot.api.sendMessage(master.telegram_id, text, { reply_markup: kb });
  } catch (error) {
    console.error('[master-bot] offer', error);
  }
}

export async function sendMasterAssigned(orderId: number): Promise<void> {
  if (!masterBot) return;
  const order = getOrderFull(orderId);
  if (!order?.master?.telegram_id) return;
  try {
    await sendOrderMedia(order.master.telegram_id, order.id);
    await masterBot.api.sendMessage(order.master.telegram_id, `Заказ ${order.number} назначен на вас.\n\n${orderCard(order, order.master)}`, {
      reply_markup: orderActions(order, order.master),
    });
  } catch (error) {
    console.error('[master-bot] assigned', error);
  }
}

export async function sendMasterStatus(orderId: number, status: string): Promise<void> {
  if (!masterBot) return;
  const order = getOrderFull(orderId);
  if (!order?.master?.telegram_id) return;
  if (!['cancelled', 'closed', 'searching'].includes(status)) return;
  const text =
    status === 'cancelled'
      ? `Заказ ${order.number} отменён.`
      : status === 'searching'
        ? `Заказ ${order.number} снят с вас и передан администратору.`
        : `Заказ ${order.number} закрыт. Стоимость работ: ${money(order.works_total)}. Ваш заработок: ${money(order.master_earnings)}.`;
  try {
    await masterBot.api.sendMessage(order.master.telegram_id, text, { reply_markup: menu() });
  } catch (error) {
    console.error('[master-bot] status', error);
  }
}

function setup(bot: Bot): void {
  bot.catch((err) => console.error('[master-bot]', err.error));

  bot.command('start', async (ctx) => {
    const master = masterOf(ctx);
    if (master) {
      if (!master.active) {
        await ctx.reply('Доступ к боту отключён администратором.');
        return;
      }
      await ctx.reply(`Здравствуйте, ${master.name}.\nОтметьтесь «На линии», чтобы получать заказы.`, { reply_markup: menu() });
      return;
    }
    const kb = new Keyboard().requestContact('📱 Отправить номер').resized().oneTime();
    await ctx.reply('Здравствуйте. Отправьте свой номер телефона, чтобы привязать бота к карточке мастера.', { reply_markup: kb });
  });

  bot.on('message:contact', async (ctx) => {
    const phone = normalizePhone(ctx.message.contact.phone_number);
    const master = getMasterByPhone(phone);
    if (!master) {
      await ctx.reply('Мастер с таким номером не найден. Обратитесь к администратору.');
      return;
    }
    if (!master.active) {
      await ctx.reply('Доступ отключён администратором.');
      return;
    }
    const { run } = await import('../db.ts');
    try {
      run('UPDATE masters SET telegram_id = ? WHERE id = ?', [ctx.from.id, master.id]);
    } catch {
      await ctx.reply('Этот Telegram уже привязан к другому мастеру.');
      return;
    }
    const fresh = getMaster(master.id)!;
    await ctx.reply(`Готово, ${fresh.name}. Теперь вы будете получать заказы здесь.`, { reply_markup: menu() });
  });

  bot.on('callback_query:data', async (ctx) => {
    const data = ctx.callbackQuery.data;
    const master = masterOf(ctx);
    if (!master) {
      await ctx.answerCallbackQuery({ text: 'Сначала привяжите номер через /start', show_alert: true });
      return;
    }
    try {
      if (data.startsWith('offer:accept:')) {
        const offerId = Number(data.split(':')[2]);
        const result = acceptOffer(offerId, master.id);
        if (!result.ok) {
          await ctx.answerCallbackQuery({ text: result.error || 'Не удалось принять', show_alert: true });
          return;
        }
        await ctx.answerCallbackQuery({ text: 'Заказ принят' });
        await safeEdit(ctx, '✅ Заказ принят. Укажите время прибытия.');
        await askEta(ctx, getOffer(offerId)?.order_id || 0);
        return;
      }
      if (data.startsWith('offer:decline:')) {
        const offerId = Number(data.split(':')[2]);
        const result = declineOffer(offerId, master.id);
        await ctx.answerCallbackQuery({ text: result.ok ? 'Отказ отправлен' : result.error || 'Ошибка' });
        await safeEdit(ctx, '❌ Вы отказались от заказа. Администратор назначит другого мастера.');
        return;
      }
      if (data.startsWith('eta:')) {
        const [, idRaw, minutesRaw] = data.split(':');
        const orderId = Number(idRaw);
        const minutes = Number(minutesRaw);
        const order = getOrder(orderId);
        if (!order || order.master_id !== master.id) {
          await ctx.answerCallbackQuery({ text: 'Заказ не найден', show_alert: true });
          return;
        }
        setEta(orderId, minutes);
        await ctx.answerCallbackQuery({ text: 'Время отправлено' });
        await safeEdit(ctx, `Время прибытия: ${minutes} мин. Клиент получил уведомление.`);
        await showOrder(ctx, master);
        return;
      }
      if (data.startsWith('eta_custom:')) {
        const orderId = Number(data.split(':')[1]);
        awaitingEta.set(master.id, { orderId });
        await ctx.answerCallbackQuery();
        await ctx.reply('Напишите, через сколько минут будете на месте (числом).');
        return;
      }
      if (data.startsWith('order:')) {
        const parts = data.split(':');
        const action = parts[1];
        const orderId = Number(parts[2]);
        const order = getOrder(orderId);
        if (!order || order.master_id !== master.id) {
          await ctx.answerCallbackQuery({ text: 'Заказ не найден', show_alert: true });
          return;
        }
        if (action === 'refresh') {
          await ctx.answerCallbackQuery();
          await showOrder(ctx, master);
          return;
        }
        if (action === 'on_way') {
          const result = masterAction(orderId, master.id, 'on_way');
          await ctx.answerCallbackQuery({ text: result.ok ? 'Клиент уведомлён' : result.error || 'Ошибка' });
          if (result.ok) {
            await safeEdit(ctx, '🚗 Выехали. Клиент получил уведомление.\n\n' + orderCard(getOrderFull(orderId)!, master), {
              reply_markup: orderActions(getOrderFull(orderId)!, master),
            });
          }
          return;
        }
        if (action === 'arrived') {
          const result = masterAction(orderId, master.id, 'arrived');
          await ctx.answerCallbackQuery({ text: result.ok ? 'Прибытие отмечено' : result.error || 'Ошибка' });
          if (result.ok) {
            const full = getOrderFull(orderId)!;
            await safeEdit(ctx, '📍 На месте. Клиент уведомлён.\n\n' + orderCard(full, master), { reply_markup: orderActions(full, master) });
          }
          return;
        }
        if (action === 'start') {
          const result = masterAction(orderId, master.id, 'start');
          await ctx.answerCallbackQuery({ text: result.ok ? 'Работы начаты' : result.error || 'Ошибка' });
          if (result.ok) {
            const full = getOrderFull(orderId)!;
            await safeEdit(ctx, '🔧 Работы начаты.\n\n' + orderCard(full, master), { reply_markup: orderActions(full, master) });
          }
          return;
        }
        if (action === 'done') {
          const result = masterAction(orderId, master.id, 'done');
          await ctx.answerCallbackQuery({ text: result.ok ? 'Работа завершена' : result.error || 'Ошибка' });
          if (result.ok) {
            awaitingAfter.set(master.id, { orderId });
            await ctx.reply('Работа отмечена выполненной. Пришлите до 2 фото «после» (или нажмите «Готово»).', {
              reply_markup: new InlineKeyboard().text('Готово', 'after_done'),
            });
          }
          return;
        }
        if (action === 'abort') {
          const result = masterAbort(orderId, master.id);
          await ctx.answerCallbackQuery({ text: result.ok ? 'Администратор уведомлён' : result.error || 'Ошибка' });
          if (result.ok) await safeEdit(ctx, '⚠️ Заказ снят с вас. Администратор назначит другого мастера.');
          return;
        }
      }
      if (data === 'after_done') {
        awaitingAfter.delete(master.id);
        await ctx.answerCallbackQuery({ text: 'Фото сохранены' });
        await safeEdit(ctx, 'Готово. Администратор проверит заказ и свяжется с клиентом.');
        return;
      }
      await ctx.answerCallbackQuery();
    } catch (error) {
      console.error('[master-bot] callback', error);
      try {
        await ctx.answerCallbackQuery({ text: 'Ошибка. Попробуйте ещё раз.' });
      } catch {
        // ignore
      }
    }
  });

  bot.on('message:photo', async (ctx) => {
    const master = masterOf(ctx);
    if (!master) return;
    const pending = awaitingAfter.get(master.id);
    if (!pending) return;
    const photos = ctx.message.photo;
    const photo = photos[photos.length - 1];
    try {
      const file = await ctx.api.getFile(photo.file_id);
      if (!file.file_path) throw new Error('no path');
      const buffer = await downloadTelegramFile(config.masterBotToken, file.file_path);
      const existing = listMedia(pending.orderId).filter((m) => m.kind === 'after');
      if (existing.length >= 2) {
        await ctx.reply('Достаточно 2 фото. Нажмите «Готово».');
        return;
      }
      await saveMediaBuffer(pending.orderId, buffer, 'image/jpeg', 'after');
      const count = listMedia(pending.orderId).filter((m) => m.kind === 'after').length;
      if (count >= 2) {
        awaitingAfter.delete(master.id);
        await ctx.reply('Фото «после» сохранены. Администратор проверит заказ.', { reply_markup: menu() });
      } else {
        await ctx.reply(`Фото сохранено (${count}/2). Можно прислать ещё одно или нажать «Готово».`, {
          reply_markup: new InlineKeyboard().text('Готово', 'after_done'),
        });
      }
    } catch (error) {
      console.error('[master-bot] photo', error);
      await ctx.reply('Не удалось сохранить фото, попробуйте ещё раз.');
    }
  });

  bot.on('message:text', async (ctx) => {
    const text = ctx.message.text.trim();
    if (text.startsWith('/')) return;
    const master = masterOf(ctx);
    if (!master) {
      await ctx.reply('Отправьте номер через /start.');
      return;
    }
    if (awaitingEta.has(master.id)) {
      const minutes = Number(text.replace(/\D/g, ''));
      const pending = awaitingEta.get(master.id)!;
      if (!Number.isFinite(minutes) || minutes <= 0 || minutes > 1440) {
        await ctx.reply('Напишите число минут, например 25.');
        return;
      }
      awaitingEta.delete(master.id);
      setEta(pending.orderId, minutes);
      await ctx.reply(`Время прибытия: ${minutes} мин. Клиент получил уведомление.`, { reply_markup: menu() });
      await showOrder(ctx, master);
      return;
    }
    if (text === MENU.free) {
      const updated = setMasterStatus(master.id, 'free');
      const active = masterActiveOrder(master.id);
      await ctx.reply(
        active ? 'У вас есть активный заказ — статус «Занят».' : 'Вы на линии. Предложения будут приходить сюда.',
        { reply_markup: menu() },
      );
      void updated;
      return;
    }
    if (text === MENU.busy) {
      setMasterStatus(master.id, 'busy');
      await ctx.reply('Статус «Занят». Новые заказы не приходят.', { reply_markup: menu() });
      return;
    }
    if (text === MENU.off) {
      const active = masterActiveOrder(master.id);
      if (active) {
        await ctx.reply('Нельзя завершить смену с активным заказом. Сначала закройте заказ.');
        return;
      }
      setMasterStatus(master.id, 'offline');
      await ctx.reply('Смена завершена. До связи!', { reply_markup: menu() });
      return;
    }
    if (text === MENU.order) {
      await showOrder(ctx, master);
      return;
    }
    if (text === MENU.money) {
      await showEarnings(ctx, master);
      return;
    }
    if (text === MENU.history) {
      await showHistory(ctx, master);
      return;
    }
    if (text === 'Готово') {
      awaitingAfter.delete(master.id);
      await ctx.reply('Готово.', { reply_markup: menu() });
      return;
    }
    await ctx.reply('Выберите действие в меню.', { reply_markup: menu() });
  });
}

async function safeEdit(ctx: Context, text: string, extra?: { reply_markup?: InlineKeyboard }): Promise<void> {
  try {
    await ctx.editMessageText(text, extra);
  } catch {
    await ctx.reply(text, extra);
  }
}

export function startMasterBot(): Bot | null {
  if (!config.masterBotToken) {
    console.warn('[master-bot] token is empty, bot disabled');
    return null;
  }
  masterBot = new Bot(config.masterBotToken);
  setup(masterBot);
  setOrderNotifier({
    masterOffer: (offerId) => sendMasterOffer(offerId),
    masterStatus: (orderId, status) => sendMasterStatus(orderId, status),
    masterAssigned: (orderId) => sendMasterAssigned(orderId),
  });
  void masterBot.start({
    drop_pending_updates: false,
    onStart: () => console.log('[master-bot] polling started'),
  }).catch((error) => console.error('[master-bot] start failed', error));
  return masterBot;
}
