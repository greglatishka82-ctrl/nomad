import fs from 'node:fs';
import path from 'node:path';
import { Bot, InlineKeyboard, Keyboard, type Context } from 'grammy';
import { config } from '../config.ts';
import { getSettings } from '../db.ts';
import { getCatalog, emergencyService, computeServicePrice, getCompany, priceLabel, tariffFor, type PublicService } from '../lib/catalog.ts';
import {
  cancelOrder,
  clientOrders,
  createOrder,
  getClientByTelegram,
  getOrderFull,
  upsertClient,
  type OrderFull,
} from '../lib/orders.ts';
import { addClientMessage, setSupportSender } from '../lib/support.ts';
import { setOrderNotifier } from '../lib/orders.ts';
import { normalizePhone, prettyPhone, statusLabel, formatDateTime, money } from '../lib/utils.ts';
import { downloadTelegramFile } from '../lib/media.ts';

type Lang = 'ru' | 'kk' | 'en';
type DraftStep =
  | 'idle'
  | 'name'
  | 'phone'
  | 'description'
  | 'media'
  | 'time'
  | 'time_custom'
  | 'urgency'
  | 'confirm'
  | 'support';

type Draft = {
  chatId: number;
  lang: Lang;
  step: DraftStep;
  service: PublicService | null;
  isEmergency: boolean;
  name: string;
  phone: string;
  description: string;
  media: Array<{ path: string; mime: string }>;
  desiredAt: string | null;
  desiredLabel: string;
  urgency: 'normal' | 'urgent' | 'emergency';
  clientId: number | null;
};

const drafts = new Map<number, Draft>();
let clientBot: Bot | null = null;

export const T = {
  ru: {
    menuTitle: 'KRANOFF — сантехническая служба в Павлодаре.\nВыберите действие:',
    btnEmergency: '🚨 Аварийный вызов',
    btnCall: '🛠 Вызвать мастера',
    btnServices: '📋 Услуги и цены',
    btnOrders: '📦 Мои заказы',
    btnSupport: '💬 Поддержка',
    btnContacts: '📞 Контакты',
    chooseCat: 'Выберите направление:',
    chooseService: 'Выберите работу:',
    orderBtn: 'Оставить заявку',
    back: '← Назад',
    priceNote: 'Цена ориентировочная. Окончательная — после осмотра. Выезд и оценка бесплатно.',
    nightNote: 'Ночью (22:00–08:00) действует двойной тариф.',
    askName: 'Как к вам обращаться? Напишите имя.',
    askPhone: 'Отправьте номер телефона кнопкой ниже или напишите вручную.',
    sharePhone: '📱 Отправить номер',
    badPhone: 'Не понял номер. Отправьте контакт кнопкой или напишите в формате +7 777 123-45-67.',
    askDescription: 'Опишите проблему своими словами. Можно пропустить.',
    skip: 'Пропустить',
    askMedia: 'Пришлите до 3 фото и 1 видео неисправности. Когда закончите — нажмите «Готово».',
    mediaDone: 'Готово',
    mediaLimit: 'Больше 3 фото не нужно. Нажмите «Готово».',
    videoLimit: 'Достаточно одного видео. Нажмите «Готово».',
    videoTooBig: 'Видео слишком большое (макс. 20 МБ). Пришлите короче или пропустите.',
    askTime: 'Когда нужен мастер?',
    now: 'Сейчас',
    today: 'Сегодня',
    tomorrow: 'Завтра',
    custom: 'Указать время',
    askCustomTime: 'Напишите желаемое время (например, «завтра после 18:00»).',
    askUrgency: 'Насколько срочно?',
    normal: 'Обычная',
    urgent: 'Срочная',
    emergency: 'Аварийная',
    confirmTitle: 'Проверьте заявку:',
    send: '✅ Отправить',
    cancel: 'Отмена',
    sent: 'Заявка принята. Номер: {number}.\nАдминистратор свяжется с вами, чтобы уточнить адрес и детали.',
    cancelled: 'Заявка отменена. Если понадобится мастер — напишите снова.',
    supportStart: 'Напишите сообщение администратору. Ответ придёт сюда же в Telegram.',
    supportSent: 'Передал администратору. Ответ придёт в этот чат.',
    noOrders: 'У вас пока нет заказов.',
    ordersTitle: 'Ваши заказы:',
    orderNotFound: 'Заказ не найден.',
    contacts: 'Контакты',
    contactsEmpty: 'Телефон и WhatsApp уточняются. Напишите в поддержку — ответим здесь.',
    emergencyStarted: 'Аварийный вызов. Администратор свяжется с вами сразу.',
    invalidName: 'Напишите имя текстом.',
    chooseLang: 'Выберите язык / Choose language:',
    status: 'Статус',
    master: 'Мастер',
    price: 'Стоимость',
    address: 'Адрес',
    created: 'Создан',
    cancelOrder: 'Отменить заказ',
    cancelUnavailable: 'Работы уже начались — отмена через администратора.',
    cancelConfirm: 'Заказ отменён.',
    support: 'Поддержка',
    backToMenu: 'В меню',
    help: 'Я помогу вызвать мастера, показать цены и статус заказа. Выберите пункт меню.',
  },
  en: {
    menuTitle: 'KRANOFF — plumbing service in Pavlodar.\nChoose an action:',
    btnEmergency: '🚨 Emergency call',
    btnCall: '🛠 Call a plumber',
    btnServices: '📋 Services & prices',
    btnOrders: '📦 My orders',
    btnSupport: '💬 Support',
    btnContacts: '📞 Contacts',
    chooseCat: 'Choose a category:',
    chooseService: 'Choose a service:',
    orderBtn: 'Request service',
    back: '← Back',
    priceNote: 'The price is approximate. The final price is confirmed after inspection. Visit and estimate are free.',
    nightNote: 'At night (22:00–08:00) the rate is doubled.',
    askName: 'What is your name?',
    askPhone: 'Send your phone number with the button below or type it.',
    sharePhone: '📱 Share phone',
    badPhone: 'I did not recognise the number. Share a contact or type +7 777 123-45-67.',
    askDescription: 'Describe the problem in your own words. You can skip this.',
    skip: 'Skip',
    askMedia: 'Send up to 3 photos and 1 video of the problem. When done, tap “Done”.',
    mediaDone: 'Done',
    mediaLimit: '3 photos are enough. Tap “Done”.',
    videoLimit: 'One video is enough. Tap “Done”.',
    videoTooBig: 'The video is too large (max 20 MB). Send a shorter one or skip.',
    askTime: 'When do you need a plumber?',
    now: 'Now',
    today: 'Today',
    tomorrow: 'Tomorrow',
    custom: 'Choose time',
    askCustomTime: 'Type the desired time (e.g. “tomorrow after 18:00”).',
    askUrgency: 'How urgent is it?',
    normal: 'Normal',
    urgent: 'Urgent',
    emergency: 'Emergency',
    confirmTitle: 'Check your request:',
    send: '✅ Send',
    cancel: 'Cancel',
    sent: 'Request accepted. Number: {number}.\nThe dispatcher will contact you to confirm the address and details.',
    cancelled: 'Request cancelled. Message us again if you need a plumber.',
    supportStart: 'Write a message to the dispatcher. The reply will arrive here in Telegram.',
    supportSent: 'Sent to the dispatcher. The reply will arrive in this chat.',
    noOrders: 'You have no orders yet.',
    ordersTitle: 'Your orders:',
    orderNotFound: 'Order not found.',
    contacts: 'Contacts',
    contactsEmpty: 'Phone and WhatsApp are being set up. Write to support — we will reply here.',
    emergencyStarted: 'Emergency call. The dispatcher will contact you immediately.',
    invalidName: 'Please type your name.',
    chooseLang: 'Выберите язык / Choose language:',
    status: 'Status',
    master: 'Master',
    price: 'Price',
    address: 'Address',
    created: 'Created',
    cancelOrder: 'Cancel order',
    cancelUnavailable: 'Work has already started — cancellation via the dispatcher.',
    cancelConfirm: 'Order cancelled.',
    support: 'Support',
    backToMenu: 'Menu',
    help: 'I can call a plumber, show prices and order status. Choose a menu item.',
  },
  kk: {
    menuTitle: 'KRANOFF — Павлодардағы сантехникалық қызмет.\nӘрекетті таңдаңыз:',
    btnEmergency: '🚨 Апаттық шақыру',
    btnCall: '🛠 Шебер шақыру',
    btnServices: '📋 Қызметтер мен бағалар',
    btnOrders: '📦 Менің тапсырыстарым',
    btnSupport: '💬 Қолдау',
    btnContacts: '📞 Байланыс',
    chooseCat: 'Бағытты таңдаңыз:',
    chooseService: 'Жұмысты таңдаңыз:',
    orderBtn: 'Өтінім қалдыру',
    back: '← Артқа',
    priceNote: 'Баға шамамен. Нақтысы — тексеруден кейін. Шығу және бағалау тегін.',
    nightNote: 'Түнде (22:00–08:00) екі есе тариф.',
    askName: 'Сізді қалай атаймыз? Атыңызды жазыңыз.',
    askPhone: 'Телефон нөміріңізді төмендегі батырмамен жіберіңіз немесе қолмен жазыңыз.',
    sharePhone: '📱 Нөмірді жіберу',
    badPhone: 'Нөмірді түсінбедім. Байланыс батырмасын басыңыз немесе +7 777 123-45-67 форматында жазыңыз.',
    askDescription: 'Мәселені өз сөзіңізбен сипаттаңыз. Өткізіп жіберуге болады.',
    skip: 'Өткізіп жіберу',
    askMedia: 'Ақаулықтың 3 фото және 1 видеосын жіберіңіз. Бітіргенде «Дайын» басыңыз.',
    mediaDone: 'Дайын',
    mediaLimit: '3 фото жеткілікті. «Дайын» басыңыз.',
    videoLimit: 'Бір видео жеткілікті. «Дайын» басыңыз.',
    videoTooBig: 'Видео тым үлкен (макс. 20 МБ). Қысқасын жіберіңіз немесе өткізіп жіберіңіз.',
    askTime: 'Шебер қашан керек?',
    now: 'Қазір',
    today: 'Бүгін',
    tomorrow: 'Ертең',
    custom: 'Уақытты көрсету',
    askCustomTime: 'Қалаған уақытты жазыңыз (мысалы, «ертең 18:00-ден кейін»).',
    askUrgency: 'Қаншалықты шұғыл?',
    normal: 'Қалыпты',
    urgent: 'Шұғыл',
    emergency: 'Апаттық',
    confirmTitle: 'Өтінімді тексеріңіз:',
    send: '✅ Жіберу',
    cancel: 'Бас тарту',
    sent: 'Өтінім қабылданды. Нөмірі: {number}.\nӘкімші мекенжай мен егжей-тегжейін нақтылау үшін хабарласады.',
    cancelled: 'Өтінімнен бас тартылды. Шебер керек болса — қайта жазыңыз.',
    supportStart: 'Әкімшіге хабарлама жазыңыз. Жауап осы Telegram чатына келеді.',
    supportSent: 'Әкімшіге жіберілді. Жауап осы чатқа келеді.',
    noOrders: 'Сізде әзірге тапсырыс жоқ.',
    ordersTitle: 'Сіздің тапсырыстарыңыз:',
    orderNotFound: 'Тапсырыс табылмады.',
    contacts: 'Байланыс',
    contactsEmpty: 'Телефон мен WhatsApp нақтылануда. Қолдауға жазыңыз — осында жауап береміз.',
    emergencyStarted: 'Апаттық шақыру. Әкімші дереу хабарласады.',
    invalidName: 'Атыңызды мәтінмен жазыңыз.',
    chooseLang: 'Тілді таңдаңыз / Выберите язык / Choose language:',
    status: 'Мәртебе',
    master: 'Шебер',
    price: 'Құны',
    address: 'Мекенжай',
    created: 'Құрылған',
    cancelOrder: 'Тапсырысты болдырмау',
    cancelUnavailable: 'Жұмыс басталды — әкімші арқылы болдырмау керек.',
    cancelConfirm: 'Тапсырыс болдырмады.',
    support: 'Қолдау',
    backToMenu: 'Мәзірге',
    help: 'Шебер шақыруға, бағалар мен тапсырыс мәртебесін көрсетуге көмектесемін. Мәзірден таңдаңыз.',
  },
};

function tr(lang: Lang): typeof T.ru {
  return T[lang] || T.ru;
}

function langOf(ctx: Context): Lang {
  const id = ctx.from?.id;
  if (!id) return 'ru';
  const draft = drafts.get(id);
  if (draft) return draft.lang;
  const client = getClientByTelegram(id);
  return client?.language === 'en' ? 'en' : client?.language === 'kk' ? 'kk' : 'ru';
}

function mainMenu(lang: Lang): Keyboard {
  const t = tr(lang);
  return new Keyboard()
    .text(t.btnEmergency)
    .text(t.btnCall)
    .row()
    .text(t.btnServices)
    .text(t.btnOrders)
    .row()
    .text(t.btnSupport)
    .text(t.btnContacts)
    .resized()
    .persistent();
}

function menuText(lang: Lang): string {
  return tr(lang).menuTitle;
}

function langKeyboard(): InlineKeyboard {
  return new InlineKeyboard()
    .text('🇷🇺 Русский', 'lang:ru')
    .text('🇰🇿 Қазақша', 'lang:kk')
    .row()
    .text('🇬🇧 English', 'lang:en');
}

function categoriesKeyboard(lang: Lang): InlineKeyboard {
  const kb = new InlineKeyboard();
  const cats = getCatalog().filter((c) => c.services.length > 0 && c.slug !== 'emergency');
  cats.forEach((cat, index) => {
    const name = lang === 'kk' ? cat.name_kz : lang === 'en' ? cat.name_en : cat.name_ru;
    kb.text(`${cat.icon || '•'} ${name}`, `cat:${cat.id}`);
    if (index % 2 === 1) kb.row();
  });
  kb.row().text(tr(lang).back, 'menu');
  return kb;
}

function servicesKeyboard(lang: Lang, catId: number): InlineKeyboard {
  const kb = new InlineKeyboard();
  const cat = getCatalog().find((c) => c.id === catId);
  if (cat) {
    for (const svc of cat.services) {
      const name = lang === 'kk' ? svc.name_kz : lang === 'en' ? svc.name_en : svc.name_ru;
      kb.text(`${name} · ${priceLabel(svc, lang)}`, `svc:${svc.id}`).row();
    }
  }
  kb.text(tr(lang).back, 'cats');
  return kb;
}

function serviceText(lang: Lang, service: PublicService): string {
  const t = tr(lang);
  const name = lang === 'kk' ? service.name_kz : lang === 'en' ? service.name_en : service.name_ru;
  const desc = lang === 'kk' ? service.description_kz : lang === 'en' ? service.description_en : service.description_ru;
  return `${name}\n\n${desc}\n\n${priceLabel(service, lang)}\n${t.priceNote}\n${t.nightNote}`;
}

async function safeEdit(ctx: Context, text: string, keyboard?: InlineKeyboard): Promise<void> {
  try {
    await ctx.editMessageText(text, keyboard ? { reply_markup: keyboard } : undefined);
  } catch {
    await ctx.reply(text, keyboard ? { reply_markup: keyboard } : undefined);
  }
}

async function ensureClient(ctx: Context): Promise<{ id: number; lang: Lang }> {
  const from = ctx.from!;
  let client = getClientByTelegram(from.id);
  if (!client) {
    const name = [from.first_name, from.last_name].filter(Boolean).join(' ').trim() || 'Клиент';
    client = upsertClient({ telegramId: from.id, name, language: 'ru' });
  }
  return { id: client.id, lang: client.language === 'en' ? 'en' : client.language === 'kk' ? 'kk' : 'ru' };
}

function newDraft(ctx: Context, lang: Lang, service: PublicService | null, isEmergency: boolean): Draft {
  const from = ctx.from!;
  const client = getClientByTelegram(from.id);
  const draft: Draft = {
    chatId: from.id,
    lang,
    step: 'idle',
    service,
    isEmergency,
    name: client?.name || [from.first_name, from.last_name].filter(Boolean).join(' ').trim() || '',
    phone: client?.phone || '',
    description: '',
    media: [],
    desiredAt: null,
    desiredLabel: '',
    urgency: isEmergency ? 'emergency' : 'normal',
    clientId: client?.id || null,
  };
  drafts.set(from.id, draft);
  return draft;
}

async function askName(ctx: Context, draft: Draft): Promise<void> {
  draft.step = 'name';
  await ctx.reply(tr(draft.lang).askName);
}

async function askPhone(ctx: Context, draft: Draft): Promise<void> {
  draft.step = 'phone';
  const kb = new Keyboard().requestContact(tr(draft.lang).sharePhone).resized().oneTime();
  await ctx.reply(tr(draft.lang).askPhone, { reply_markup: kb });
}

async function askDescription(ctx: Context, draft: Draft): Promise<void> {
  draft.step = 'description';
  const kb = new InlineKeyboard().text(tr(draft.lang).skip, 'skip_desc');
  await ctx.reply(tr(draft.lang).askDescription, { reply_markup: kb });
}

async function askMedia(ctx: Context, draft: Draft): Promise<void> {
  draft.step = 'media';
  const kb = new InlineKeyboard().text(tr(draft.lang).mediaDone, 'media_done');
  await ctx.reply(tr(draft.lang).askMedia, { reply_markup: kb });
}

async function askTime(ctx: Context, draft: Draft): Promise<void> {
  draft.step = 'time';
  const t = tr(draft.lang);
  const kb = new InlineKeyboard()
    .text(t.now, 'time:now')
    .text(t.today, 'time:today')
    .row()
    .text(t.tomorrow, 'time:tomorrow')
    .text(t.custom, 'time:custom');
  await ctx.reply(t.askTime, { reply_markup: kb });
}

async function askUrgency(ctx: Context, draft: Draft): Promise<void> {
  if (draft.isEmergency) {
    await showConfirm(ctx, draft);
    return;
  }
  draft.step = 'urgency';
  const t = tr(draft.lang);
  const kb = new InlineKeyboard().text(t.normal, 'urgency:normal').text(t.urgent, 'urgency:urgent').row().text(t.emergency, 'urgency:emergency');
  await ctx.reply(t.askUrgency, { reply_markup: kb });
}

function summaryText(draft: Draft): string {
  const t = tr(draft.lang);
  const serviceName = draft.service ? (draft.lang === 'kk' ? draft.service.name_kz : draft.lang === 'en' ? draft.service.name_en : draft.service.name_ru) : '—';
  const tariff = tariffFor(draft.isEmergency, new Date());
  const price = draft.service ? computeServicePrice(draft.service, tariff) : 0;
  const time = draft.desiredLabel || (draft.lang === 'kk' ? 'мүмкіндігінше тезірек' : draft.lang === 'en' ? 'as soon as possible' : 'как можно скорее');
  const urgency = draft.urgency === 'emergency' ? t.emergency : draft.urgency === 'urgent' ? t.urgent : t.normal;
  return `${t.confirmTitle}\n\n${serviceName}\n${draft.description ? draft.description + '\n' : ''}${t.price}: ${money(price)}\n${t.address}: ${draft.lang === 'kk' ? 'әкімші нақтылайды' : draft.lang === 'en' ? 'to be confirmed by dispatcher' : 'уточнит администратор'}\n${time}\n${urgency}`;
}

async function showConfirm(ctx: Context, draft: Draft): Promise<void> {
  draft.step = 'confirm';
  const t = tr(draft.lang);
  const kb = new InlineKeyboard().text(t.send, 'submit').row().text(t.cancel, 'cancel_draft');
  await ctx.reply(summaryText(draft), { reply_markup: kb });
}

async function proceedFromDescription(ctx: Context, draft: Draft): Promise<void> {
  await askMedia(ctx, draft);
}

async function startOrderFlow(ctx: Context, service: PublicService, isEmergency: boolean): Promise<void> {
  const lang = langOf(ctx);
  const draft = newDraft(ctx, lang, service, isEmergency);
  await ctx.reply(isEmergency ? tr(lang).emergencyStarted : serviceText(lang, service));
  if (!draft.name) return askName(ctx, draft);
  if (!draft.phone) return askPhone(ctx, draft);
  return askDescription(ctx, draft);
}

function tempFile(chatId: number, ext: string, buffer: Buffer): string {
  const dir = path.join(config.uploadDir, 'tmp', String(chatId));
  fs.mkdirSync(dir, { recursive: true });
  const name = `${Date.now()}-${Math.random().toString(36).slice(2)}.${ext}`;
  const full = path.join(dir, name);
  fs.writeFileSync(full, buffer);
  return full;
}

async function handlePhoto(ctx: Context, draft: Draft): Promise<void> {
  const photos = ctx.message?.photo || [];
  if (!photos.length) return;
  if (draft.media.filter((m) => m.mime.startsWith('image/')).length >= 3) {
    await ctx.reply(tr(draft.lang).mediaLimit);
    return;
  }
  const photo = photos[photos.length - 1];
  try {
    const file = await ctx.api.getFile(photo.file_id);
    if (!file.file_path) throw new Error('no file path');
    const buffer = await downloadTelegramFile(config.clientBotToken, file.file_path);
    const filePath = tempFile(ctx.from!.id, 'jpg', buffer);
    draft.media.push({ path: filePath, mime: 'image/jpeg' });
    await ctx.reply(draft.lang === 'kk' ? 'Фото қосылды.' : draft.lang === 'en' ? 'Photo added.' : 'Фото добавлено.');
  } catch (error) {
    console.error('[client-bot] photo', error);
    await ctx.reply(draft.lang === 'kk' ? 'Фотоны жүктеу мүмкін болмады, қайта көріңіз.' : draft.lang === 'en' ? 'Could not download the photo, try again.' : 'Не удалось загрузить фото, попробуйте ещё раз.');
  }
}

async function handleVideo(ctx: Context, draft: Draft): Promise<void> {
  const video = ctx.message?.video;
  if (!video) return;
  if (draft.media.some((m) => m.mime.startsWith('video/'))) {
    await ctx.reply(tr(draft.lang).videoLimit);
    return;
  }
  if ((video.file_size || 0) > 20 * 1024 * 1024) {
    await ctx.reply(tr(draft.lang).videoTooBig);
    return;
  }
  try {
    const file = await ctx.api.getFile(video.file_id);
    if (!file.file_path) throw new Error('no file path');
    const buffer = await downloadTelegramFile(config.clientBotToken, file.file_path);
    const ext = (video.mime_type || 'video/mp4').includes('quicktime') ? 'mov' : 'mp4';
    draft.media.push({ path: tempFile(ctx.from!.id, ext, buffer), mime: video.mime_type || 'video/mp4' });
    await ctx.reply(draft.lang === 'kk' ? 'Видео қосылды.' : draft.lang === 'en' ? 'Video added.' : 'Видео добавлено.');
  } catch (error) {
    console.error('[client-bot] video', error);
    await ctx.reply(draft.lang === 'kk' ? 'Видеоны жүктеу мүмкін болмады, қайта көріңіз.' : draft.lang === 'en' ? 'Could not download the video, try again.' : 'Не удалось загрузить видео, попробуйте ещё раз.');
  }
}

async function submitDraft(ctx: Context, draft: Draft): Promise<void> {
  if (!draft.service || !draft.name || !draft.phone) {
    await ctx.reply(draft.lang === 'kk' ? 'Деректер жеткіліксіз. /start арқылы қайта бастаңыз.' : draft.lang === 'en' ? 'Not enough data. Start again with /start.' : 'Не хватает данных. Начните заново командой /start.');
    return;
  }
  const client = upsertClient({ telegramId: ctx.from!.id, name: draft.name, phone: draft.phone, language: draft.lang });
  const tariff = tariffFor(draft.isEmergency, new Date());
  const price = computeServicePrice(draft.service, tariff);
  const order = createOrder({
    clientId: client.id,
    source: 'telegram',
    problem: draft.lang === 'kk' ? draft.service.name_kz : draft.lang === 'en' ? draft.service.name_en : draft.service.name_ru,
    description: draft.description,
    desiredAt: draft.desiredAt,
    desiredLabel: draft.desiredLabel,
    urgency: draft.urgency,
    isEmergency: draft.isEmergency,
    tariff,
    items: [{ serviceId: draft.service.id, name: draft.lang === 'kk' ? draft.service.name_kz : draft.lang === 'en' ? draft.service.name_en : draft.service.name_ru, price, qty: 1 }],
    language: draft.lang,
  });
  for (const media of draft.media) {
    try {
      const buffer = fs.readFileSync(media.path);
      const { saveMediaBuffer } = await import('../lib/media.ts');
      await saveMediaBuffer(order.id, buffer, media.mime, 'before');
      fs.rmSync(media.path, { force: true });
    } catch (error) {
      console.error('[client-bot] media save', error);
    }
  }
  drafts.delete(ctx.from!.id);
  await ctx.reply(tr(draft.lang).sent.replace('{number}', order.number), { reply_markup: mainMenu(draft.lang) });
}

function orderStatusText(lang: Lang, status: string, order: OrderFull): string {
  const t = tr(lang);
  const number = order.number;
  const master = order.master ? `${order.master.name}${order.master.phone ? ', ' + prettyPhone(order.master.phone) : ''}` : '';
  const eta = order.eta_minutes
    ? `\n${lang === 'kk' ? 'Келу уақыты шамамен' : lang === 'en' ? 'Arrival in about' : 'Прибытие примерно через'} ${order.eta_minutes} ${lang === 'kk' ? 'мин' : lang === 'en' ? 'min' : 'мин'}.`
    : '';
  const map: Record<string, [string, string, string]> = {
    new: ['Заявка принята. Администратор рассматривает её.', 'Өтінім қабылданды. Әкімші қарап жатыр.', 'Request received. The dispatcher is reviewing it.'],
    reviewing: ['Заявка на рассмотрении. Администратор свяжется с вами.', 'Өтінім қаралуда. Әкімші сізбен хабарласады.', 'Request is being reviewed. The dispatcher will contact you.'],
    confirmed: ['Заказ подтверждён. Подбираем мастера.', 'Тапсырыс расталды. Шебер таңдап жатырмыз.', 'Order confirmed. We are assigning a master.'],
    searching: ['Подбираем мастера. Администратор свяжется с вами.', 'Шебер таңдап жатырмыз. Әкімші сізбен хабарласады.', 'We are assigning a master. The dispatcher will contact you.'],
    offered: ['Предложение отправлено мастеру, ждём подтверждения.', 'Ұсыныс шеберге жіберілді, растауды күтеміз.', 'The offer has been sent to the master, waiting for confirmation.'],
    accepted: [`Мастер назначен: ${master}.`, `Шебер тағайындалды: ${master}.`, `Master assigned: ${master}.`],
    on_way: [`Мастер выехал: ${master}.`, `Шебер жолға шықты: ${master}.`, `The master is on the way: ${master}.`],
    arrived: [`Мастер на месте: ${master}.`, `Шебер орнында: ${master}.`, `The master has arrived: ${master}.`],
    in_progress: ['Работы начаты.', 'Жұмыс басталды.', 'Work has started.'],
    done_by_master: ['Мастер завершил работы. Администратор свяжется с вами для проверки.', 'Шебер жұмысты аяқтады. Әкімші тексеру үшін сізбен хабарласады.', 'The master has finished. The dispatcher will contact you to confirm.'],
    admin_check: ['Заказ проверяется администратором.', 'Тапсырысты әкімші тексеріп жатыр.', 'The order is being checked by the dispatcher.'],
    closed: [
      `Заказ закрыт. Сумма: ${money(order.works_total)}.${order.guarantee_months ? ' Гарантия: ' + order.guarantee_months + ' мес.' : ''}`,
      `Тапсырыс жабылды. Сома: ${money(order.works_total)}.${order.guarantee_months ? ' Кепілдік: ' + order.guarantee_months + ' ай.' : ''}`,
      `Order closed. Amount: ${money(order.works_total)}.${order.guarantee_months ? ' Warranty: ' + order.guarantee_months + ' months.' : ''}`,
    ],
    cancelled: ['Заказ отменён.', 'Тапсырыс болдырылмады.', 'Order cancelled.'],
  };
  const line = map[status]?.[lang === 'kk' ? 1 : lang === 'en' ? 2 : 0] || status;
  return `${number}\n${t.status}: ${statusLabel(status, lang)}\n${line}${eta}`;
}

function cancelKeyboard(lang: Lang, orderId: number): InlineKeyboard | undefined {
  const order = getOrderFull(orderId);
  if (!order) return undefined;
  if (['in_progress', 'done_by_master', 'admin_check', 'closed', 'cancelled'].includes(order.status)) return undefined;
  return new InlineKeyboard().text(tr(lang).cancelOrder, `cancel_order:${orderId}`);
}

export async function sendClientStatus(orderId: number, status: string): Promise<void> {
  if (!clientBot) return;
  const order = getOrderFull(orderId);
  if (!order || !order.client.telegram_id) return;
  const lang: Lang = order.language === 'en' ? 'en' : order.language === 'kk' ? 'kk' : 'ru';
  const text = status === 'eta'
    ? `${order.number}\n${lang === 'kk' ? 'Шебер шамамен' : lang === 'en' ? 'The master will arrive in about' : 'Мастер будет примерно через'} ${order.eta_minutes || 0} ${lang === 'kk' ? 'мин.' : lang === 'en' ? 'min.' : 'мин.'}`
    : orderStatusText(lang, status, order);
  const kb = status === 'eta' ? undefined : cancelKeyboard(lang, orderId);
  try {
    await clientBot.api.sendMessage(order.client.telegram_id, text, kb ? { reply_markup: kb } : undefined);
  } catch (error) {
    console.error('[client-bot] status send', error);
  }
}

export async function sendSupportMessage(telegramId: number, text: string): Promise<void> {
  if (!clientBot) throw new Error('Клиентский бот не запущен');
  await clientBot.api.sendMessage(telegramId, text);
}

function orderDetailText(lang: Lang, order: OrderFull): string {
  const t = tr(lang);
  return `${order.number}\n${t.status}: ${statusLabel(order.status, lang)}\n${order.problem}${order.master ? `\n${t.master}: ${order.master.name}` : ''}\n${t.price}: ${money(order.works_total)}\n${t.created}: ${formatDateTime(order.created_at)}${order.address ? `\n${t.address}: ${order.address}` : ''}`;
}

async function showMyOrders(ctx: Context, lang: Lang): Promise<void> {
  const client = getClientByTelegram(ctx.from!.id);
  if (!client) {
    await ctx.reply(tr(lang).noOrders);
    return;
  }
  const orders = clientOrders(client.id, 10);
  if (!orders.length) {
    await ctx.reply(tr(lang).noOrders);
    return;
  }
  const kb = new InlineKeyboard();
  for (const order of orders) {
    kb.text(`${order.number} · ${statusLabel(order.status, lang)}`, `my:${order.id}`).row();
  }
  await ctx.reply(tr(lang).ordersTitle, { reply_markup: kb });
}

async function showContacts(ctx: Context, lang: Lang): Promise<void> {
  const company = getCompany();
  const hours = company.hours === 'Круглосуточно'
    ? (lang === 'kk' ? 'Тәулік бойы' : lang === 'en' ? '24/7' : 'Круглосуточно')
    : company.hours;
  const lines = [tr(lang).contacts, company.name, company.address, hours];
  if (company.phone) lines.push(prettyPhone(company.phone));
  if (company.whatsapp) lines.push(`WhatsApp: +${company.whatsapp.replace(/\D/g, '')}`);
  if (!company.phone && !company.whatsapp) lines.push(tr(lang).contactsEmpty);
  const kb = new InlineKeyboard();
  if (company.whatsapp) kb.url('WhatsApp', `https://wa.me/${company.whatsapp.replace(/\D/g, '')}`);
  kb.row().text(tr(lang).support, 'support_open');
  await ctx.reply(lines.join('\n'), { reply_markup: kb });
}

function setup(bot: Bot): void {
  bot.catch((err) => console.error('[client-bot]', err.error));

  bot.command('start', async (ctx) => {
    const client = getClientByTelegram(ctx.from!.id);
    const lang: Lang = client?.language === 'en' ? 'en' : client?.language === 'kk' ? 'kk' : 'ru';
    drafts.delete(ctx.from!.id);
    if (!client) {
      await ctx.reply(tr(lang).chooseLang, { reply_markup: langKeyboard() });
    } else {
      await ctx.reply(menuText(lang), { reply_markup: mainMenu(lang) });
    }
  });

  bot.command('menu', async (ctx) => {
    const lang = langOf(ctx);
    drafts.delete(ctx.from!.id);
    await ctx.reply(menuText(lang), { reply_markup: mainMenu(lang) });
  });

  bot.command('lang', async (ctx) => {
    await ctx.reply(tr(langOf(ctx)).chooseLang, { reply_markup: langKeyboard() });
  });

  bot.command('help', async (ctx) => {
    const lang = langOf(ctx);
    await ctx.reply(tr(lang).help, { reply_markup: mainMenu(lang) });
  });

  bot.command('cancel', async (ctx) => {
    drafts.delete(ctx.from!.id);
    const lang = langOf(ctx);
    await ctx.reply(tr(lang).cancelled, { reply_markup: mainMenu(lang) });
  });

  bot.on('callback_query:data', async (ctx) => {
    const data = ctx.callbackQuery.data;
    const lang = langOf(ctx);
    const chatId = ctx.from!.id;
    let draft = drafts.get(chatId);
    try {
      if (data.startsWith('lang:')) {
        const next: Lang = data.endsWith('en') ? 'en' : data.endsWith('kk') ? 'kk' : 'ru';
        const from = ctx.from!;
        const existing = getClientByTelegram(from.id);
        if (existing) {
          upsertClient({ telegramId: from.id, name: existing.name, phone: existing.phone, language: next });
        } else {
          upsertClient({ telegramId: from.id, name: [from.first_name, from.last_name].filter(Boolean).join(' ').trim(), language: next });
        }
        if (draft) draft.lang = next;
        await ctx.answerCallbackQuery();
        await safeEdit(ctx, menuText(next), undefined);
        await ctx.reply(menuText(next), { reply_markup: mainMenu(next) });
        return;
      }

      if (data === 'menu') {
        drafts.delete(chatId);
        await ctx.answerCallbackQuery();
        await ctx.reply(menuText(lang), { reply_markup: mainMenu(lang) });
        return;
      }

      if (data === 'cats') {
        await ctx.answerCallbackQuery();
        await safeEdit(ctx, tr(lang).chooseCat, categoriesKeyboard(lang));
        return;
      }

      if (data.startsWith('cat:')) {
        const catId = Number(data.split(':')[1]);
        const cat = getCatalog().find((c) => c.id === catId);
        await ctx.answerCallbackQuery();
        if (cat) await safeEdit(ctx, `${lang === 'kk' ? cat.name_kz : lang === 'en' ? cat.name_en : cat.name_ru}\n\n${tr(lang).chooseService}`, servicesKeyboard(lang, catId));
        return;
      }

      if (data.startsWith('svc:')) {
        const svcId = Number(data.split(':')[1]);
        const svc = getCatalog().flatMap((c) => c.services).find((s) => s.id === svcId);
        await ctx.answerCallbackQuery();
        if (svc) {
          const kb = new InlineKeyboard().text(tr(lang).orderBtn, `order:${svc.id}`).row().text(tr(lang).back, 'cats');
          await safeEdit(ctx, serviceText(lang, svc), kb);
        }
        return;
      }

      if (data.startsWith('order:')) {
        const svcId = Number(data.split(':')[1]);
        const svc = getCatalog().flatMap((c) => c.services).find((s) => s.id === svcId);
        await ctx.answerCallbackQuery();
        if (svc) await startOrderFlow(ctx, svc, Boolean(svc.is_emergency));
        return;
      }

      if (data === 'support_open') {
        await ctx.answerCallbackQuery();
        const client = getClientByTelegram(chatId);
        const id = client?.id || upsertClient({ telegramId: chatId, name: [ctx.from!.first_name, ctx.from!.last_name].filter(Boolean).join(' ').trim(), language: lang }).id;
        const d = newDraft(ctx, lang, null, false);
        d.clientId = id;
        d.step = 'support';
        await ctx.reply(tr(lang).supportStart);
        return;
      }

      if (!draft) {
        await ctx.answerCallbackQuery();
        return;
      }

      if (data === 'skip_desc') {
        draft.description = '';
        await ctx.answerCallbackQuery();
        await askMedia(ctx, draft);
        return;
      }

      if (data === 'media_done') {
        await ctx.answerCallbackQuery();
        if (!draft.isEmergency) await askTime(ctx, draft);
        else await showConfirm(ctx, draft);
        return;
      }

      if (data.startsWith('time:')) {
        const value = data.split(':')[1];
        draft.desiredAt = null;
        if (value === 'now') {
          draft.desiredAt = new Date().toISOString();
          draft.desiredLabel = draft.lang === 'kk' ? 'Мүмкіндігінше тезірек' : draft.lang === 'en' ? 'As soon as possible' : 'Как можно скорее';
          draft.step = 'urgency';
          await ctx.answerCallbackQuery();
          await askUrgency(ctx, draft);
        } else if (value === 'today') {
          draft.desiredLabel = draft.lang === 'kk' ? 'Бүгін' : draft.lang === 'en' ? 'Today' : 'Сегодня';
          draft.step = 'urgency';
          await ctx.answerCallbackQuery();
          await askUrgency(ctx, draft);
        } else if (value === 'tomorrow') {
          draft.desiredLabel = draft.lang === 'kk' ? 'Ертең' : draft.lang === 'en' ? 'Tomorrow' : 'Завтра';
          draft.step = 'urgency';
          await ctx.answerCallbackQuery();
          await askUrgency(ctx, draft);
        } else {
          draft.step = 'time_custom';
          await ctx.answerCallbackQuery();
          await ctx.reply(tr(draft.lang).askCustomTime);
        }
        return;
      }

      if (data.startsWith('urgency:')) {
        const value = data.split(':')[1] as 'normal' | 'urgent' | 'emergency';
        draft.urgency = value;
        if (value === 'emergency') draft.isEmergency = true;
        await ctx.answerCallbackQuery();
        await showConfirm(ctx, draft);
        return;
      }

      if (data === 'submit') {
        await ctx.answerCallbackQuery();
        await submitDraft(ctx, draft);
        return;
      }

      if (data === 'cancel_draft') {
        drafts.delete(chatId);
        await ctx.answerCallbackQuery();
        await safeEdit(ctx, tr(lang).cancelled, undefined);
        return;
      }

      if (data.startsWith('cancel_order:')) {
        const orderId = Number(data.split(':')[1]);
        const order = getOrderFull(orderId);
        if (!order || order.client.telegram_id !== chatId) {
          await ctx.answerCallbackQuery({ text: tr(lang).orderNotFound, show_alert: true });
          return;
        }
        const result = cancelOrder(orderId, 'client', null, true);
        if (!result.ok) {
          await ctx.answerCallbackQuery({ text: result.error || tr(lang).cancelUnavailable, show_alert: true });
          return;
        }
        await ctx.answerCallbackQuery();
        await safeEdit(ctx, orderStatusText(lang, 'cancelled', order), undefined);
        return;
      }

      if (data.startsWith('my:')) {
        const orderId = Number(data.split(':')[1]);
        const order = getOrderFull(orderId);
        if (!order || order.client.telegram_id !== chatId) {
          await ctx.answerCallbackQuery({ text: tr(lang).orderNotFound, show_alert: true });
          return;
        }
        const kb = cancelKeyboard(lang, orderId);
        await ctx.answerCallbackQuery();
        await safeEdit(ctx, orderDetailText(lang, order), kb);
        return;
      }

      await ctx.answerCallbackQuery();
    } catch (error) {
      console.error('[client-bot] callback', error);
      try {
        await ctx.answerCallbackQuery({ text: 'Ошибка. Попробуйте ещё раз.' });
      } catch {
        // ignore
      }
    }
  });

  bot.on('message:contact', async (ctx) => {
    const draft = drafts.get(ctx.from.id);
    if (!draft || draft.step !== 'phone') return;
    const phone = normalizePhone(ctx.message.contact.phone_number);
    if (phone.replace(/\D/g, '').length < 10) {
      await ctx.reply(tr(draft.lang).badPhone);
      return;
    }
    draft.phone = phone;
    if (draft.description) await askMedia(ctx, draft);
    else await askDescription(ctx, draft);
  });

  bot.on('message:photo', async (ctx) => {
    const draft = drafts.get(ctx.from.id);
    if (!draft || draft.step !== 'media') return;
    await handlePhoto(ctx, draft);
  });

  bot.on('message:video', async (ctx) => {
    const draft = drafts.get(ctx.from.id);
    if (!draft || draft.step !== 'media') return;
    await handleVideo(ctx, draft);
  });

  bot.on('message:text', async (ctx) => {
    const text = ctx.message.text.trim();
    if (text.startsWith('/')) return;
    const lang = langOf(ctx);
    const t = tr(lang);
    if (text === t.btnEmergency) {
      const svc = emergencyService();
      if (svc) await startOrderFlow(ctx, svc, true);
      else await ctx.reply(t.contactsEmpty);
      return;
    }
    if (text === t.btnCall) {
      await ctx.reply(t.chooseCat, { reply_markup: categoriesKeyboard(lang) });
      return;
    }
    if (text === t.btnServices) {
      await ctx.reply(t.chooseCat, { reply_markup: categoriesKeyboard(lang) });
      return;
    }
    if (text === t.btnOrders) {
      await showMyOrders(ctx, lang);
      return;
    }
    if (text === t.btnSupport) {
      const client = getClientByTelegram(ctx.from.id);
      const id = client?.id || upsertClient({ telegramId: ctx.from.id, name: [ctx.from.first_name, ctx.from.last_name].filter(Boolean).join(' ').trim(), language: lang }).id;
      const d = newDraft(ctx, lang, null, false);
      d.clientId = id;
      d.step = 'support';
      await ctx.reply(t.supportStart);
      return;
    }
    if (text === t.btnContacts) {
      await showContacts(ctx, lang);
      return;
    }

    const draft = drafts.get(ctx.from.id);
    if (!draft) {
      await ctx.reply(t.menuTitle, { reply_markup: mainMenu(lang) });
      return;
    }
    if (draft.step === 'name') {
      if (text.length < 2) {
        await ctx.reply(t.invalidName);
        return;
      }
      draft.name = text.slice(0, 80);
      if (!draft.phone) await askPhone(ctx, draft);
      else if (draft.description) await askMedia(ctx, draft);
      else await askDescription(ctx, draft);
      return;
    }
    if (draft.step === 'phone') {
      const phone = normalizePhone(text);
      if (phone.replace(/\D/g, '').length < 10) {
        await ctx.reply(t.badPhone);
        return;
      }
      draft.phone = phone;
      await askDescription(ctx, draft);
      return;
    }
    if (draft.step === 'description') {
      draft.description = text.slice(0, 900);
      await askMedia(ctx, draft);
      return;
    }
    if (draft.step === 'time_custom') {
      draft.desiredLabel = text.slice(0, 120);
      draft.desiredAt = null;
      draft.step = 'urgency';
      await askUrgency(ctx, draft);
      return;
    }
    if (draft.step === 'support') {
      const client = getClientByTelegram(ctx.from.id);
      const id = client?.id || draft.clientId;
      if (id) addClientMessage(id, text.slice(0, 3000), ctx.message.message_id);
      await ctx.reply(t.supportSent);
      return;
    }
    if (draft.step === 'media') {
      await ctx.reply(t.askMedia, { reply_markup: new InlineKeyboard().text(t.mediaDone, 'media_done') });
      return;
    }
    await ctx.reply(t.menuTitle, { reply_markup: mainMenu(lang) });
  });
}

export function startClientBot(): Bot | null {
  if (!config.clientBotToken) {
    console.warn('[client-bot] token is empty, bot disabled');
    return null;
  }
  clientBot = new Bot(config.clientBotToken);
  setup(clientBot);
  setSupportSender(async (telegramId, text) => sendSupportMessage(telegramId, text));
  setOrderNotifier({ clientStatus: (orderId, status) => sendClientStatus(orderId, status) });
  void clientBot.start({
    drop_pending_updates: false,
    onStart: () => console.log('[client-bot] polling started'),
  }).catch((error) => console.error('[client-bot] start failed', error));
  void clientBot.api
    .setMyCommands([
      { command: 'start', description: 'Главное меню / Main menu' },
      { command: 'menu', description: 'Меню / Menu' },
      { command: 'lang', description: 'Язык / Language' },
      { command: 'help', description: 'Помощь / Help' },
    ])
    .catch(() => undefined);
  return clientBot;
}
