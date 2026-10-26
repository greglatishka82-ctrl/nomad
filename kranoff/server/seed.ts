export type SeedCategory = {
  slug: string;
  name_ru: string;
  name_en: string;
  icon: string;
  sort: number;
};

export type SeedService = {
  cat: string;
  name_ru: string;
  name_en: string;
  desc_ru: string;
  desc_en: string;
  price: number;
  price_night?: number;
  emergency?: boolean;
  skills: string[];
  sort: number;
};

export const SKILLS = [
  { key: 'leak', name_ru: 'Протечки' },
  { key: 'clog', name_ru: 'Засоры' },
  { key: 'mixer', name_ru: 'Краны и смесители' },
  { key: 'toilet', name_ru: 'Унитазы' },
  { key: 'pipes', name_ru: 'Трубы' },
  { key: 'sewer', name_ru: 'Канализация' },
  { key: 'heating', name_ru: 'Отопление' },
  { key: 'boiler', name_ru: 'Бойлеры' },
  { key: 'install', name_ru: 'Установка оборудования' },
  { key: 'emergency', name_ru: 'Аварийные вызовы' },
  { key: 'welding', name_ru: 'Сварка' },
  { key: 'electric', name_ru: 'Электрика' },
  { key: 'diagnostics', name_ru: 'Диагностика' },
];

export const CATEGORIES: SeedCategory[] = [
  { slug: 'emergency', name_ru: 'Аварийные вызовы', name_en: 'Emergency calls', icon: '🚨', sort: 1 },
  { slug: 'leaks', name_ru: 'Протечки', name_en: 'Leaks', icon: '💧', sort: 2 },
  { slug: 'clogs', name_ru: 'Засоры', name_en: 'Clogs', icon: '🌀', sort: 3 },
  { slug: 'mixers', name_ru: 'Краны и смесители', name_en: 'Taps & mixers', icon: '🚰', sort: 4 },
  { slug: 'toilets', name_ru: 'Унитазы', name_en: 'Toilets', icon: '🚽', sort: 5 },
  { slug: 'pipes', name_ru: 'Трубы', name_en: 'Pipes', icon: '🔧', sort: 6 },
  { slug: 'sewer', name_ru: 'Канализация', name_en: 'Sewer', icon: '🕳️', sort: 7 },
  { slug: 'heating', name_ru: 'Отопление', name_en: 'Heating', icon: '🔥', sort: 8 },
  { slug: 'install', name_ru: 'Установка оборудования', name_en: 'Installation', icon: '🛠️', sort: 9 },
  { slug: 'other', name_ru: 'Прочее', name_en: 'Other', icon: '📞', sort: 10 },
];

const D = {
  standard_ru: 'Выезд, диагностика и выполнение работы. Материалы оплачиваются отдельно.',
  standard_en: 'Visit, diagnostics and the work itself. Materials are paid separately.',
  emergency_ru: 'Срочный вызов: администратор связывается сразу и принимает решение по выезду.',
  emergency_en: 'Urgent call: the dispatcher calls you back immediately and arranges the visit.',
};

export const SERVICES: SeedService[] = [
  // emergency
  { cat: 'emergency', name_ru: 'Аварийный вызов днём', name_en: 'Emergency call (day)', desc_ru: D.emergency_ru, desc_en: D.emergency_en, price: 15000, price_night: 30000, emergency: true, skills: ['emergency', 'leak', 'clog'], sort: 1 },
  { cat: 'emergency', name_ru: 'Аварийный вызов ночью', name_en: 'Emergency call (night)', desc_ru: D.emergency_ru, desc_en: D.emergency_en, price: 30000, price_night: 30000, emergency: true, skills: ['emergency', 'leak', 'clog'], sort: 2 },
  { cat: 'emergency', name_ru: 'Устранение протечки', name_en: 'Leak repair', desc_ru: 'Локализация и устранение протечки, проверка соединений.', desc_en: 'Locate and fix the leak, check connections.', price: 10000, price_night: 20000, emergency: true, skills: ['emergency', 'leak'], sort: 3 },
  { cat: 'emergency', name_ru: 'Аварийная прочистка канализации', name_en: 'Emergency sewer cleaning', desc_ru: 'Срочная прочистка засора тросом или гидродинамикой.', desc_en: 'Urgent clog removal with cable or hydro-jetting.', price: 20000, price_night: 40000, emergency: true, skills: ['emergency', 'clog', 'sewer'], sort: 4 },

  // leaks
  { cat: 'leaks', name_ru: 'Устранение течи крана', name_en: 'Tap leak repair', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 7000, skills: ['leak', 'mixer'], sort: 1 },
  { cat: 'leaks', name_ru: 'Устранение течи соединения', name_en: 'Joint leak repair', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 8000, skills: ['leak', 'pipes'], sort: 2 },
  { cat: 'leaks', name_ru: 'Замена гибкой подводки', name_en: 'Flexible hose replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 5000, skills: ['leak', 'install'], sort: 3 },
  { cat: 'leaks', name_ru: 'Устранение течи бачка', name_en: 'Cistern leak repair', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 6000, skills: ['leak', 'toilet'], sort: 4 },
  { cat: 'leaks', name_ru: 'Поиск скрытой протечки', name_en: 'Hidden leak detection', desc_ru: 'Диагностика без вскрытия, поиск причины сырости и протечки.', desc_en: 'Non-destructive diagnostics to find the leak source.', price: 12000, skills: ['leak', 'diagnostics'], sort: 5 },

  // clogs
  { cat: 'clogs', name_ru: 'Прочистка раковины', name_en: 'Sink clog removal', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 7000, skills: ['clog'], sort: 1 },
  { cat: 'clogs', name_ru: 'Прочистка ванны', name_en: 'Bath clog removal', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 7000, skills: ['clog'], sort: 2 },
  { cat: 'clogs', name_ru: 'Прочистка унитаза', name_en: 'Toilet clog removal', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 8000, skills: ['clog', 'toilet'], sort: 3 },
  { cat: 'clogs', name_ru: 'Прочистка канализации тросом', name_en: 'Sewer cable cleaning', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 15000, skills: ['clog', 'sewer'], sort: 4 },
  { cat: 'clogs', name_ru: 'Прочистка стояка', name_en: 'Riser cleaning', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 20000, skills: ['clog', 'sewer'], sort: 5 },
  { cat: 'clogs', name_ru: 'Гидродинамическая прочистка', name_en: 'Hydro-jetting', desc_ru: 'Прочистка труб под давлением, удаление жировых и твёрдых отложений.', desc_en: 'High-pressure pipe cleaning, removes grease and build-up.', price: 25000, skills: ['clog', 'sewer'], sort: 6 },

  // mixers
  { cat: 'mixers', name_ru: 'Замена смесителя', name_en: 'Mixer replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 9000, skills: ['mixer', 'install'], sort: 1 },
  { cat: 'mixers', name_ru: 'Ремонт смесителя', name_en: 'Mixer repair', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 6000, skills: ['mixer'], sort: 2 },
  { cat: 'mixers', name_ru: 'Замена картриджа', name_en: 'Cartridge replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 6000, skills: ['mixer'], sort: 3 },
  { cat: 'mixers', name_ru: 'Замена крана или вентиля', name_en: 'Valve replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 7000, skills: ['mixer', 'pipes'], sort: 4 },
  { cat: 'mixers', name_ru: 'Установка шарового крана', name_en: 'Ball valve installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 8000, skills: ['mixer', 'pipes'], sort: 5 },
  { cat: 'mixers', name_ru: 'Установка фильтра грубой очистки', name_en: 'Sediment filter installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 9000, skills: ['mixer', 'install'], sort: 6 },

  // toilets
  { cat: 'toilets', name_ru: 'Установка унитаза', name_en: 'Toilet installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 15000, skills: ['toilet', 'install'], sort: 1 },
  { cat: 'toilets', name_ru: 'Замена унитаза', name_en: 'Toilet replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 18000, skills: ['toilet', 'install'], sort: 2 },
  { cat: 'toilets', name_ru: 'Ремонт бачка', name_en: 'Cistern repair', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 7000, skills: ['toilet'], sort: 3 },
  { cat: 'toilets', name_ru: 'Замена арматуры бачка', name_en: 'Cistern fittings replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 6000, skills: ['toilet'], sort: 4 },
  { cat: 'toilets', name_ru: 'Установка инсталляции', name_en: 'Wall-hung toilet installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 25000, skills: ['toilet', 'install'], sort: 5 },
  { cat: 'toilets', name_ru: 'Замена сиденья унитаза', name_en: 'Toilet seat replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 4000, skills: ['toilet'], sort: 6 },

  // pipes
  { cat: 'pipes', name_ru: 'Замена трубы (1 точка)', name_en: 'Pipe replacement (1 point)', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 10000, skills: ['pipes'], sort: 1 },
  { cat: 'pipes', name_ru: 'Замена стояка', name_en: 'Riser replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 30000, skills: ['pipes', 'welding'], sort: 2 },
  { cat: 'pipes', name_ru: 'Разводка труб (1 точка)', name_en: 'Pipe routing (1 point)', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 12000, skills: ['pipes'], sort: 3 },
  { cat: 'pipes', name_ru: 'Пайка полипропилена (1 точка)', name_en: 'PPR welding (1 point)', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 5000, skills: ['pipes', 'welding'], sort: 4 },
  { cat: 'pipes', name_ru: 'Замена радиатора', name_en: 'Radiator replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 20000, skills: ['heating', 'pipes'], sort: 5 },
  { cat: 'pipes', name_ru: 'Врезка в стояк', name_en: 'Riser tapping', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 12000, skills: ['pipes', 'welding'], sort: 6 },

  // sewer
  { cat: 'sewer', name_ru: 'Замена канализационной трубы', name_en: 'Sewer pipe replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 12000, skills: ['sewer', 'pipes'], sort: 1 },
  { cat: 'sewer', name_ru: 'Установка сифона', name_en: 'Siphon installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 5000, skills: ['sewer', 'install'], sort: 2 },
  { cat: 'sewer', name_ru: 'Подключение стиральной машины', name_en: 'Washing machine connection', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 8000, skills: ['install', 'sewer'], sort: 3 },
  { cat: 'sewer', name_ru: 'Подключение посудомоечной машины', name_en: 'Dishwasher connection', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 8000, skills: ['install', 'sewer'], sort: 4 },
  { cat: 'sewer', name_ru: 'Установка трапа', name_en: 'Floor drain installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 15000, skills: ['sewer', 'install'], sort: 5 },

  // heating
  { cat: 'heating', name_ru: 'Установка радиатора', name_en: 'Radiator installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 20000, skills: ['heating', 'pipes'], sort: 1 },
  { cat: 'heating', name_ru: 'Замена полотенцесушителя', name_en: 'Towel rail replacement', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 15000, skills: ['heating', 'pipes'], sort: 2 },
  { cat: 'heating', name_ru: 'Ремонт отопления', name_en: 'Heating repair', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 12000, skills: ['heating'], sort: 3 },
  { cat: 'heating', name_ru: 'Опрессовка системы', name_en: 'Pressure testing', desc_ru: 'Проверка системы отопления под давлением.', desc_en: 'Heating system pressure test.', price: 10000, skills: ['heating', 'diagnostics'], sort: 4 },
  { cat: 'heating', name_ru: 'Установка терморегулятора', name_en: 'Thermostat installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 6000, skills: ['heating', 'install'], sort: 5 },

  // install
  { cat: 'install', name_ru: 'Установка бойлера', name_en: 'Water heater installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 15000, skills: ['boiler', 'install', 'electric'], sort: 1 },
  { cat: 'install', name_ru: 'Установка раковины', name_en: 'Sink installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 12000, skills: ['install'], sort: 2 },
  { cat: 'install', name_ru: 'Установка ванны', name_en: 'Bath installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 18000, skills: ['install'], sort: 3 },
  { cat: 'install', name_ru: 'Установка душевой кабины', name_en: 'Shower cabin installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 25000, skills: ['install'], sort: 4 },
  { cat: 'install', name_ru: 'Установка счётчика воды', name_en: 'Water meter installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 10000, skills: ['install', 'pipes'], sort: 5 },
  { cat: 'install', name_ru: 'Установка фильтра', name_en: 'Water filter installation', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 8000, skills: ['install'], sort: 6 },

  // other
  { cat: 'other', name_ru: 'Диагностика', name_en: 'Diagnostics', desc_ru: 'Осмотр, определение причины и оценка объёма работ.', desc_en: 'Inspection, root cause and scope assessment.', price: 0, skills: ['diagnostics'], sort: 1 },
  { cat: 'other', name_ru: 'Мелкий ремонт', name_en: 'Minor repair', desc_ru: D.standard_ru, desc_en: D.standard_en, price: 5000, skills: ['install'], sort: 2 },
  { cat: 'other', name_ru: 'Консультация', name_en: 'Consultation', desc_ru: 'Ответ на вопрос по сантехнике, без выезда.', desc_en: 'Plumbing advice without a visit.', price: 0, skills: ['diagnostics'], sort: 3 },
];

export const KZ_CATEGORIES: Record<string, string> = {
  emergency: 'Апаттық шақырулар',
  leaks: 'Ағулар',
  clogs: 'Бітелулер',
  mixers: 'Крандар мен араластырғыштар',
  toilets: 'Дәретханалар',
  pipes: 'Құбырлар',
  sewer: 'Кәріз',
  heating: 'Жылыту',
  install: 'Жабдықты орнату',
  other: 'Басқа',
};

export const KZ_STANDARD = 'Шығу, диагностика және жұмысты орындау. Материалдар бөлек төленеді.';
export const KZ_EMERGENCY = 'Шұғыл шақыру: әкімші бірден хабарласып, шығу туралы шешім қабылдайды.';

export const KZ_SERVICES: Record<string, { name: string; desc?: string }> = {
  'Аварийный вызов днём': { name: 'Күндізгі апаттық шақыру', desc: KZ_EMERGENCY },
  'Аварийный вызов ночью': { name: 'Түнгі апаттық шақыру', desc: KZ_EMERGENCY },
  'Устранение протечки': { name: 'Ағуды жою', desc: 'Ағуды тоқтату және қосылыстарды тексеру.' },
  'Аварийная прочистка канализации': { name: 'Кәрізді апаттық тазалау', desc: 'Кәріз бітелуін трос немесе гидродинамика әдісімен шұғыл тазалау.' },
  'Устранение течи крана': { name: 'Кран ағуын жою' },
  'Устранение течи соединения': { name: 'Қосылыс ағуын жою' },
  'Замена гибкой подводки': { name: 'Икемді қосқышты ауыстыру' },
  'Устранение течи бачка': { name: 'Бачок ағуын жою' },
  'Поиск скрытой протечки': { name: 'Жасырын ағуды іздеу', desc: 'Қабырғаны бұзбай диагностика жүргізу, ылғал мен ағудың себебін табу.' },
  'Прочистка раковины': { name: 'Шұңғылшаны тазалау' },
  'Прочистка ванны': { name: 'Ваннаны тазалау' },
  'Прочистка унитаза': { name: 'Дәретхананы тазалау' },
  'Прочистка канализации тросом': { name: 'Кәрізді троспен тазалау' },
  'Прочистка стояка': { name: 'Тіректі тазалау' },
  'Гидродинамическая прочистка': { name: 'Гидродинамикалық тазалау', desc: 'Құбырларды жоғары қысыммен тазалау, май мен қатты қабаттарды кетіру.' },
  'Замена смесителя': { name: 'Араластырғышты ауыстыру' },
  'Ремонт смесителя': { name: 'Араластырғышты жөндеу' },
  'Замена картриджа': { name: 'Картриджді ауыстыру' },
  'Замена крана или вентиля': { name: 'Кранды немесе вентильді ауыстыру' },
  'Установка шарового крана': { name: 'Шарлы кранды орнату' },
  'Установка фильтра грубой очистки': { name: 'Дөрекі тазалау сүзгісін орнату' },
  'Установка унитаза': { name: 'Дәретхананы орнату' },
  'Замена унитаза': { name: 'Дәретхананы ауыстыру' },
  'Ремонт бачка': { name: 'Бачокты жөндеу' },
  'Замена арматуры бачка': { name: 'Бачок арматурасын ауыстыру' },
  'Установка инсталляции': { name: 'Инсталляцияны орнату' },
  'Замена сиденья унитаза': { name: 'Дәретхана отырғышын ауыстыру' },
  'Замена трубы (1 точка)': { name: 'Құбырды ауыстыру (1 нүкте)' },
  'Замена стояка': { name: 'Тіректі ауыстыру' },
  'Разводка труб (1 точка)': { name: 'Құбырларды тарту (1 нүкте)' },
  'Пайка полипропилена (1 точка)': { name: 'Полипропиленді дәнекерлеу (1 нүкте)' },
  'Замена радиатора': { name: 'Радиаторды ауыстыру' },
  'Врезка в стояк': { name: 'Тірекке қосылу' },
  'Замена канализационной трубы': { name: 'Кәріз құбырын ауыстыру' },
  'Установка сифона': { name: 'Сифонды орнату' },
  'Подключение стиральной машины': { name: 'Кір жуғыш машинаны қосу' },
  'Подключение посудомоечной машины': { name: 'Ыдыс жуғыш машинаны қосу' },
  'Установка трапа': { name: 'Трапты орнату' },
  'Установка радиатора': { name: 'Радиаторды орнату' },
  'Замена полотенцесушителя': { name: 'Сүлгі кептіргішті ауыстыру' },
  'Ремонт отопления': { name: 'Жылытуды жөндеу' },
  'Опрессовка системы': { name: 'Жүйені қысыммен тексеру', desc: 'Жылыту жүйесін қысыммен тексеру.' },
  'Установка терморегулятора': { name: 'Термореттегішті орнату' },
  'Установка бойлера': { name: 'Бойлерді орнату' },
  'Установка раковины': { name: 'Шұңғылшаны орнату' },
  'Установка ванны': { name: 'Ваннаны орнату' },
  'Установка душевой кабины': { name: 'Душ кабинасын орнату' },
  'Установка счётчика воды': { name: 'Су есептегішін орнату' },
  'Установка фильтра': { name: 'Сүзгіні орнату' },
  'Диагностика': { name: 'Диагностика', desc: 'Қарау, себебін анықтау және жұмыс көлемін бағалау.' },
  'Мелкий ремонт': { name: 'Ұсақ жөндеу' },
  'Консультация': { name: 'Кеңес', desc: 'Сантехника бойынша сұраққа жауап, шығусыз.' },
};

export const DEFAULT_SETTINGS: Record<string, string> = {
  company_name: 'KRANOFF',
  company_slogan: 'Решил — Действуй',
  company_phone: '',
  company_whatsapp: '',
  company_email: '',
  company_address: 'Павлодар',
  company_hours: 'Круглосуточно',
  company_telegram: 'kranof_bot',
  company_about_ru:
    'KRANOFF — сантехническая служба в Павлодаре. Принимаем заявки круглосуточно, выезжаем на дом и в офис, устраняем протечки, засоры, меняем трубы, краны, унитазы, подключаем оборудование. Выезд и оценка стоимости — бесплатно. На выполненные работы даём гарантию, условия фиксируем в акте.',
  company_about_en:
    'KRANOFF is a plumbing service in Pavlodar. We take requests 24/7, visit homes and offices, fix leaks and clogs, replace pipes, taps and toilets, and install equipment. The visit and estimate are free. We provide a warranty on completed work, with terms recorded in the work report.',
  company_about_kz:
    'KRANOFF — Павлодардағы сантехникалық қызмет. Өтінімдерді тәулік бойы қабылдаймыз, үй мен кеңсеге шығамыз, ағуды, бітелуді жоямыз, құбырларды, крандарды, дәретханаларды ауыстырамыз, жабдықты орнатамыз. Шығу және бағалау тегін. Орындалған жұмыстарға кепілдік береміз, шарттары актіде көрсетіледі.',
  company_guarantee_ru:
    'Гарантия на работы прописывается в акте выполненных работ. Срок гарантии мастер и клиент согласуют до начала работ. Гарантийные обращения принимаются по телефону, в WhatsApp или Telegram — мы поднимаем исходный заказ и разбираемся по факту.',
  company_guarantee_en:
    'The warranty on work is recorded in the work report. The warranty period is agreed between the master and the client before work starts. Warranty claims are accepted by phone, WhatsApp or Telegram — we open the original order and investigate.',
  company_guarantee_kz:
    'Жұмыстарға кепілдік орындалған жұмыстар актісінде жазылады. Кепілдік мерзімін шебер мен клиент жұмыс басталғанға дейін келіседі. Кепілдік өтініштері телефон, WhatsApp немесе Telegram арқылы қабылданады — бастапқы тапсырысты ашып, тексеріп, шешеміз.',
  company_coverage_ru:
    'Работаем по Павлодару. Выезд за город и в пригород — по согласованию, стоимость обсуждается отдельно. Аварийные вызовы принимаются круглосуточно: днём действует дневной тариф, ночью — ночной.',
  company_coverage_en:
    'We serve Pavlodar. Trips outside the city are arranged individually, the price is agreed separately. Emergency calls are accepted 24/7: the day rate applies during the day, the night rate at night.',
  company_coverage_kz:
    'Павлодар бойынша жұмыс істейміз. Қала сыртына шығу — келісім бойынша, құны бөлек талқыланады. Апаттық шақырулар тәулік бойы қабылданады: күндіз күндізгі тариф, түнде түнгі тариф.',
  company_privacy_ru:
    'Мы храним только имя, номер телефона и адрес, чтобы знать клиента и не спрашивать всё заново при повторном обращении. Данные не передаются третьим лицам и используются только для выполнения заказа и связи с клиентом. По запросу данные можно удалить.',
  company_privacy_en:
    'We store only the name, phone number and address so that we recognise the client and do not ask everything again on a repeat visit. The data is not shared with third parties and is used only to fulfil the order and contact the client. Data can be deleted on request.',
  company_privacy_kz:
    'Біз клиентті тану және қайта жүгінгенде қайта сұрамау үшін тек аты, телефон нөмірі және мекенжайын сақтаймыз. Деректер үшінші тұлғаларға берілмейді және тек тапсырысты орындау мен клиентпен байланысу үшін пайдаланылады. Сұраныс бойынша деректерді жоюға болады.',
  night_start: '22',
  night_end: '8',
  night_multiplier: '2',
  master_share: '0.7',
  offer_timeout_minutes: '5',
  min_masters_on_shift: '2',
  currency: 'KZT',
  service_area_note: 'Павлодар и пригород',
};
