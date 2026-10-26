import { Link } from 'react-router-dom';
import { MapView } from '../components/MapView';
import { usePublic, useDocumentMeta, telegramUrl, whatsappUrl, telUrl } from '../public';
import { money } from '../api';

export function HomePage() {
  const { data, t, text3, lang } = usePublic();
  useDocumentMeta(
    lang === 'en'
      ? 'KRANOFF — plumber in Pavlodar | 24/7 emergency service'
      : lang === 'kk'
        ? 'KRANOFF — Павлодардағы сантехник | тәулік бойы апаттық қызмет'
        : 'KRANOFF — сантехник в Павлодаре | аварийная служба 24/7',
    t(
      'Сантехническая служба KRANOFF в Павлодаре: аварийные вызовы 24/7, устранение протечек и засоров, замена труб, кранов, унитазов. Выезд и оценка бесплатно.',
      'KRANOFF plumbing service in Pavlodar: 24/7 emergency calls, leak and clog repair, pipe, tap and toilet replacement. Free visit and estimate.',
    ),
  );
  if (!data) return null;
  const company = data.company;
  const cats = data.categories.filter((c) => c.services.length > 0 && c.slug !== 'emergency');
  const minPrice = Math.min(...data.categories.flatMap((c) => c.services).filter((s) => s.price > 0).map((s) => s.price));
  const emergencyCat = data.categories.find((c) => c.slug === 'emergency');

  return (
    <>
      <section className="hero">
        <div className="container hero-inner">
          <div>
            <span className="hero-eyebrow">● {t('Павлодар · работаем круглосуточно', 'Pavlodar · open 24/7')}</span>
            <h1>
              {t('Сантехник приедет', 'A plumber will come')}
              <br />
              <span>{t('сегодня. Без ожидания.', 'today. No waiting.')}</span>
            </h1>
            <p className="hero-sub">
              {t(
                'Протечка, засор, замена крана или установка бойлера — берёмся за любую сантехническую задачу. Выезд и оценка стоимости бесплатно, цену согласуем до начала работ.',
                'A leak, a clog, a tap replacement or a boiler installation — we handle any plumbing job. The visit and estimate are free, and we agree the price before work starts.',
              )}
            </p>
            <div className="hero-actions">
              <a className="btn btn-primary btn-lg" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">
                {t('Вызвать мастера в Telegram', 'Call a plumber on Telegram')}
              </a>
              {company.whatsapp ? (
                <a className="btn btn-lg" href={whatsappUrl(company.whatsapp)} target="_blank" rel="noreferrer" style={{ background: 'rgba(255,255,255,.1)', color: '#fff' }}>
                  WhatsApp
                </a>
              ) : null}
              {company.phone ? (
                <a className="btn btn-lg" href={telUrl(company.phone)} style={{ background: 'rgba(255,255,255,.1)', color: '#fff' }}>
                  {company.phone}
                </a>
              ) : null}
            </div>
            <div className="hero-badges">
              <div className="hero-badge"><span>✓</span><span><b>{t('Выезд бесплатно', 'Free visit')}</b>{t('и оценка стоимости', 'and estimate')}</span></div>
              <div className="hero-badge"><span>✓</span><span><b>{t('24/7', '24/7')}</b>{t('аварийные вызовы', 'emergency calls')}</span></div>
              <div className="hero-badge"><span>✓</span><span><b>{t('Гарантия', 'Warranty')}</b>{t('условия в акте', 'terms in the report')}</span></div>
              <div className="hero-badge"><span>✓</span><span><b>{`${t('от', 'from')} ${money(minPrice)}`}</b>{t('типовые работы', 'standard jobs')}</span></div>
            </div>
          </div>
          <div className="hero-card">
            <div className="hero-card-head">
              <img src="/logo-emblem.svg" alt="" />
              <div>
                <h3>{t('Что нужно сделать?', 'What needs fixing?')}</h3>
                <p>{t('Администратор на связи и подберёт мастера', 'A dispatcher is online and will assign a master')}</p>
              </div>
            </div>
            <ul className="hero-list">
              <li>{t('Опишите проблему и пришлите фото', 'Describe the problem and send a photo')}</li>
              <li>{t('Мы уточним адрес и время', 'We confirm the address and time')}</li>
              <li>{t('Мастер приедет и согласует цену', 'The master arrives and agrees the price')}</li>
              <li>{t('Работаем до результата, выдаём акт', 'We finish the job and issue a report')}</li>
            </ul>
            <a className="btn btn-primary btn-block" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">
              {t('Оставить заявку', 'Request service')}
            </a>
            <p className="hero-note">{t('Заявка занимает около минуты', 'The request takes about a minute')}</p>
          </div>
        </div>
      </section>

      <section className="section" id="services">
        <div className="container">
          <div className="section-head row-between">
            <div>
              <span className="eyebrow">{t('Услуги', 'Services')}</span>
              <h2 className="h2">{t('Решаем сантехнические задачи', 'We solve plumbing problems')}</h2>
              <p className="lead">{t('Ориентировочные цены. Окончательная стоимость — после осмотра.', 'Approximate prices. The final price is confirmed after inspection.')}</p>
            </div>
            <Link className="btn btn-outline" to="/services">{t('Все услуги', 'All services')}</Link>
          </div>
          <div className="service-grid">
            {cats.slice(0, 6).map((cat) => {
              const prices = cat.services.filter((s) => s.price > 0).map((s) => s.price);
              const min = prices.length ? Math.min(...prices) : 0;
              return (
                <Link key={cat.id} to={`/services#${cat.slug}`} className="service-card">
                  <span className="service-icon">{cat.icon || '🔧'}</span>
                  <h3>{lang === 'kk' ? cat.name_kz : lang === 'en' ? cat.name_en : cat.name_ru}</h3>
                  <p>
                    {cat.services.slice(0, 3).map((s) => (lang === 'kk' ? s.name_kz : lang === 'en' ? s.name_en : s.name_ru)).join(' · ')}
                  </p>
                  <div className="service-price">
                    <b>{min ? `${t('от', 'from')} ${money(min)}` : t('по осмотру', 'on inspection')}</b>
                    <span>{cat.services.length} {t('услуг', 'services')}</span>
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      </section>

      <section className="section alt" id="how">
        <div className="container">
          <div className="section-head">
            <span className="eyebrow">{t('Как это работает', 'How it works')}</span>
            <h2 className="h2">{t('От заявки до результата — четыре шага', 'From request to result — four steps')}</h2>
          </div>
          <div className="steps">
            <div className="step">
              <h3>{t('Оставьте заявку', 'Send a request')}</h3>
              <p>{t('В Telegram или WhatsApp: выберите услугу, опишите проблему, пришлите фото.', 'On Telegram or WhatsApp: choose a service, describe the problem, send photos.')}</p>
            </div>
            <div className="step">
              <h3>{t('Администратор уточнит', 'Dispatcher confirms')}</h3>
              <p>{t('Свяжемся с вами, уточним адрес, время и детали. Скажем ориентировочную стоимость.', 'We contact you to confirm the address, time and details, and give an estimate.')}</p>
            </div>
            <div className="step">
              <h3>{t('Мастер приедет', 'The master arrives')}</h3>
              <p>{t('Мастер осмотрит объект и согласует с вами работы и цену до начала ремонта.', 'The master inspects the site and agrees the scope and price before starting.')}</p>
            </div>
            <div className="step">
              <h3>{t('Результат и гарантия', 'Result and warranty')}</h3>
              <p>{t('Проверяете работу, получаете акт с гарантией. Вопросы решаем по телефону и в поддержке.', 'You check the work and receive a report with the warranty. We handle issues by phone and in support.')}</p>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="emergency" id="emergency">
            <div>
              <span className="hero-eyebrow" style={{ background: 'rgba(239,68,68,.12)', borderColor: 'rgba(239,68,68,.35)', color: '#fca5a5' }}>
                {t('Аварийная ситуация', 'Emergency')}
              </span>
              <h2>{t('Протечка или потоп? Не ждите утра.', 'Leak or flooding? Don’t wait until morning.')}</h2>
              <p>
                {t(
                  'Аварийные вызовы принимаем круглосуточно. Днём действует дневной тариф, ночью — ночной. Если нужна помощь коммунальной службы — подскажем и приедем раньше, чтобы снять угрозу.',
                  'Emergency calls are accepted 24/7. The day rate applies during the day, the night rate at night. If the utility service is needed, we advise and can arrive earlier to prevent damage.',
                )}
              </p>
            </div>
            <div className="emergency-actions">
              <a className="btn btn-primary btn-lg" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">
                {t('Аварийный вызов', 'Emergency call')}
              </a>
              {company.phone ? <a className="btn" href={telUrl(company.phone)} style={{ background: 'rgba(255,255,255,.1)', color: '#fff' }}>{company.phone}</a> : null}
            </div>
          </div>
        </div>
      </section>

      <section className="section alt" id="coverage">
        <div className="container coverage">
          <div>
            <span className="eyebrow">{t('Зона обслуживания', 'Service area')}</span>
            <h2 className="h2">{t('Работаем по Павлодару и пригороду', 'We serve Pavlodar and the suburbs')}</h2>
            <p className="lead">
              {t(
                'Выезжаем в любой район города. За городом — по согласованию, стоимость обсуждаем отдельно. Аварийные вызовы принимаем круглосуточно.',
                'We cover every district of the city. Outside the city — by arrangement, the price is agreed separately. Emergency calls are accepted 24/7.',
              )}
            </p>
            <div className="info-list mt-20">
              <div className="info-item">
                <span className="ico">📍</span>
                <div><b>{t('Павлодар', 'Pavlodar')}</b><span>{t('Все районы, включая левый берег', 'All districts, including the left bank')}</span></div>
              </div>
              <div className="info-item">
                <span className="ico">🚗</span>
                <div><b>{t('Пригород', 'Suburbs')}</b><span>{t('По предварительному согласованию', 'By prior arrangement')}</span></div>
              </div>
              <div className="info-item">
                <span className="ico">🌙</span>
                <div><b>{t('Ночной тариф ×2', 'Night rate ×2')}</b><span>{t('С 22:00 до 08:00 для обычных работ', 'From 22:00 to 08:00 for standard jobs')}</span></div>
              </div>
            </div>
          </div>
          <div className="map-card">
            <MapView
              center={[52.2873, 76.9674]}
              zoom={11}
              circle={{ lat: 52.2873, lng: 76.9674, radius: 14000 }}
              markers={[{ id: 'pavlodar', lat: 52.2873, lng: 76.9674, html: '<div class="master-pin">🔧</div>', popup: 'KRANOFF · Павлодар' }]}
              className="map-card"
            />
          </div>
        </div>
      </section>

      <section className="section" id="guarantee">
        <div className="container grid-2" style={{ alignItems: 'start', gap: 40 }}>
          <div>
            <span className="eyebrow">{t('Гарантия и доверие', 'Warranty and trust')}</span>
            <h2 className="h2">{t('Работы фиксируем в акте', 'Work is recorded in the report')}</h2>
            <p className="lead">
              {text3(
                company.guarantee_ru || 'Гарантия на работы прописывается в акте выполненных работ. Срок согласуем до начала работ.',
                company.guarantee_kz || '',
                company.guarantee_en || 'The warranty is recorded in the work report. We agree the period before work starts.',
              )}
            </p>
            <div className="info-list mt-20">
              <div className="info-item"><span className="ico">📄</span><div><b>{t('Акт в двух экземплярах', 'Report in two copies')}</b><span>{t('Что сделано, материалы и сумма — до начала оплаты', 'Scope, materials and amount before payment')}</span></div></div>
              <div className="info-item"><span className="ico">🧾</span><div><b>{t('Материалы отдельно', 'Materials separately')}</b><span>{t('Покупаете сами или согласуем покупку с мастером', 'Buy yourself or agree the purchase with the master')}</span></div></div>
              <div className="info-item"><span className="ico">🛠️</span><div><b>{t('Опыт по видам работ', 'Experience by job type')}</b><span>{t('Подбираем мастера под задачу, а не просто «сантехника»', 'We match the master to the task, not just a generic plumber')}</span></div></div>
            </div>
          </div>
          <div className="panel" style={{ padding: 24 }}>
            <div className="hero-card-head" style={{ marginBottom: 12 }}>
              <img src="/logo-emblem.svg" alt="" style={{ width: 48, height: 48, objectFit: 'contain' }} />
              <div>
                <h3 style={{ color: 'var(--ink)' }}>{company.name}</h3>
                <p className="muted small">{company.slogan}</p>
              </div>
            </div>
            <p className="pre" style={{ marginTop: 8 }}>
              {text3(company.about_ru, company.about_kz, company.about_en)}
            </p>
            <div className="divider" />
            <div className="row-between small"><span className="muted">{t('Режим работы', 'Working hours')}</span><b>{t(company.hours, company.hours === 'Круглосуточно' ? '24/7' : company.hours)}</b></div>
            <div className="row-between small mt-12"><span className="muted">{t('Город', 'City')}</span><b>{company.address}</b></div>
            <a className="btn btn-primary btn-block mt-16" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">
              {t('Написать в Telegram', 'Message us on Telegram')}
            </a>
          </div>
        </div>
      </section>

      <section className="section alt" id="faq">
        <div className="container">
          <div className="section-head">
            <span className="eyebrow">FAQ</span>
            <h2 className="h2">{t('Частые вопросы', 'Frequently asked questions')}</h2>
          </div>
          <div className="faq">
            <details>
              <summary>{t('Сколько стоит вызов мастера?', 'How much does a visit cost?')}</summary>
              <p>{t('Выезд и оценка стоимости бесплатны. Стоимость работ — по прайсу или после осмотра. Материалы оплачиваются отдельно.', 'The visit and estimate are free. Work is priced by the catalogue or after inspection. Materials are paid separately.')}</p>
            </details>
            <details>
              <summary>{t('Работаете ли вы ночью?', 'Do you work at night?')}</summary>
              <p>{t('Да, аварийные вызовы принимаем круглосуточно. Для обычных работ ночью действует двойной тариф; аварийные вызовы тарифицируются отдельно.', 'Yes, emergency calls are accepted 24/7. Standard jobs at night are charged at double rate; emergency calls have a separate rate.')}</p>
            </details>
            <details>
              <summary>{t('Как быстро приедет мастер?', 'How fast will the master arrive?')}</summary>
              <p>{t('Зависит от загрузки и адреса. Администратор подтвердит время после согласования. Аварийные вызовы — в приоритете.', 'It depends on workload and address. The dispatcher confirms the time. Emergency calls have priority.')}</p>
            </details>
            <details>
              <summary>{t('Можно ли купить материалы самому?', 'Can I buy materials myself?')}</summary>
              <p>{t('Да. Можно купить самостоятельно, попросить мастера купить с чеком или согласовать покупку через администратора.', 'Yes. Buy them yourself, ask the master to buy with a receipt, or arrange the purchase through the dispatcher.')}</p>
            </details>
            <details>
              <summary>{t('Даёте ли гарантию?', 'Do you provide a warranty?')}</summary>
              <p>{t('Да. Срок гарантии фиксируется в акте выполненных работ и согласуется до начала ремонта.', 'Yes. The warranty period is recorded in the work report and agreed before work starts.')}</p>
            </details>
            <details>
              <summary>{t('Можно ли отменить заказ?', 'Can I cancel the order?')}</summary>
              <p>{t('До начала работ — да, через Telegram-бот или администратора. После начала работ отмена только через администратора.', 'Before work starts — yes, via the Telegram bot or the dispatcher. After work starts, cancellation is only via the dispatcher.')}</p>
            </details>
          </div>
        </div>
      </section>

      <section className="section" id="contacts">
        <div className="container">
          <div className="contact-grid">
            <div>
              <span className="eyebrow">{t('Связаться', 'Contact us')}</span>
              <h2 className="h2">{t('Оставьте заявку удобным способом', 'Choose a convenient way to reach us')}</h2>
              <p className="lead">{t('Ответим в Telegram или WhatsApp, при необходимости позвоним. Заявку принимаем круглосуточно.', 'We reply on Telegram or WhatsApp and call if needed. Requests are accepted 24/7.')}</p>
              <div className="hero-actions">
                <a className="btn btn-primary" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">Telegram</a>
                {company.whatsapp ? <a className="btn btn-outline" href={whatsappUrl(company.whatsapp)} target="_blank" rel="noreferrer">WhatsApp</a> : null}
                {company.phone ? <a className="btn btn-outline" href={telUrl(company.phone)}>{company.phone}</a> : null}
              </div>
            </div>
            <div className="contact-card">
              <h3>{t('Контакты службы', 'Service contacts')}</h3>
              <p className="muted small">{t('Работаем в Павлодаре и пригороде', 'Serving Pavlodar and the suburbs')}</p>
              <div className="contact-lines">
                {company.phone ? <div className="contact-line"><span>{t('Телефон', 'Phone')}</span><b>{company.phone}</b></div> : null}
                {company.whatsapp ? <div className="contact-line"><span>WhatsApp</span><b>+{company.whatsapp.replace(/\D/g, '')}</b></div> : null}
                <div className="contact-line"><span>Telegram</span><b>@{company.telegram}</b></div>
                <div className="contact-line"><span>{t('Адрес', 'Address')}</span><b>{company.address}</b></div>
                <div className="contact-line"><span>{t('Режим работы', 'Hours')}</span><b>{t(company.hours, company.hours === 'Круглосуточно' ? '24/7' : company.hours)}</b></div>
              </div>
              <a className="btn btn-primary btn-block mt-16" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">
                {t('Открыть Telegram-бот', 'Open the Telegram bot')}
              </a>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
