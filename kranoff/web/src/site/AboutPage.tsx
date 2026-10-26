import { MapView } from '../components/MapView';
import { usePublic, useDocumentMeta, telegramUrl } from '../public';

export function AboutPage() {
  const { data, t, text3, lang } = usePublic();
  useDocumentMeta(
    lang === 'kk' ? 'KRANOFF компаниясы туралы — Павлодардағы сантехникалық қызмет' : 'О компании KRANOFF — сантехническая служба в Павлодаре',
    t('KRANOFF — сантехническая служба в Павлодаре. Круглосуточные аварийные вызовы, гарантия на работы, мастера по всем районам.', 'KRANOFF is a plumbing service in Pavlodar. 24/7 emergency calls, warranty on work, masters across all districts.'),
  );
  if (!data) return null;
  const company = data.company;
  return (
    <>
      <section className="hero" style={{ padding: '58px 0 46px' }}>
        <div className="container">
          <span className="hero-eyebrow">{t('О компании', 'About')}</span>
          <h1 style={{ fontSize: 'clamp(30px, 4.4vw, 48px)', margin: '16px 0 12px' }}>{company.name}</h1>
          <p className="hero-sub" style={{ maxWidth: 780 }}>{text3(company.about_ru, company.about_kz, company.about_en)}</p>
        </div>
      </section>

      <section className="section">
        <div className="container grid-2" style={{ gap: 40, alignItems: 'start' }}>
          <div>
            <span className="eyebrow">{t('Подход', 'Our approach')}</span>
            <h2 className="h2">{t('Не просто «сантехник», а мастер под задачу', 'Not just a plumber — the right master for the job')}</h2>
            <p className="lead">
              {t(
                'Мы подбираем исполнителя по навыкам, инструментам и расположению. Для бытовой замены крана и для сложного ремонта стояка нужны разные специалисты — это учитывается при назначении.',
                'We match the specialist by skills, tools and location. Replacing a tap and repairing a riser require different specialists — we take that into account.',
              )}
            </p>
            <div className="info-list mt-20">
              <div className="info-item"><span className="ico">🎯</span><div><b>{t('Подбор по навыкам', 'Skill-based matching')}</b><span>{t('Учитываем опыт, инструменты и район', 'Experience, tools and district')}</span></div></div>
              <div className="info-item"><span className="ico">⏱️</span><div><b>{t('Контроль выезда', 'Visit control')}</b><span>{t('Администратор видит, где мастер и когда он приедет', 'The dispatcher sees where the master is and when he arrives')}</span></div></div>
              <div className="info-item"><span className="ico">💬</span><div><b>{t('Связь без потерь', 'Continuous communication')}</b><span>{t('Telegram, WhatsApp и телефон — как удобно клиенту', 'Telegram, WhatsApp and phone — whatever suits the client')}</span></div></div>
              <div className="info-item"><span className="ico">📋</span><div><b>{t('Прозрачные условия', 'Transparent terms')}</b><span>{t('Работы и сумма фиксируются в акте до оплаты', 'Scope and amount are recorded in the report before payment')}</span></div></div>
            </div>
          </div>
          <div className="panel" style={{ padding: 24 }}>
            <div className="hero-card-head" style={{ marginBottom: 10 }}>
              <img src="/logo-emblem.svg" alt="" style={{ width: 52, height: 52, objectFit: 'contain' }} />
              <div><h3 style={{ color: 'var(--ink)' }}>{company.slogan}</h3><p className="muted small">{t('Сантехническая служба', 'Plumbing service')}</p></div>
            </div>
            <div className="kv" style={{ marginTop: 10 }}>
              <dt>{t('Город', 'City')}</dt><dd>{company.address}</dd>
              <dt>{t('Режим', 'Hours')}</dt><dd>{t(company.hours, company.hours === 'Круглосуточно' ? '24/7' : company.hours)}</dd>
              <dt>{t('Выезд', 'Visit')}</dt><dd>{t('Бесплатно', 'Free')}</dd>
              <dt>{t('Гарантия', 'Warranty')}</dt><dd>{t('По акту', 'Per report')}</dd>
            </div>
            <a className="btn btn-primary btn-block mt-16" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">{t('Вызвать мастера', 'Call a plumber')}</a>
          </div>
        </div>
      </section>

      <section className="section alt" id="guarantee">
        <div className="container">
          <div className="section-head">
            <span className="eyebrow">{t('Гарантия', 'Warranty')}</span>
            <h2 className="h2">{t('Что происходит после ремонта', 'What happens after the repair')}</h2>
          </div>
          <div className="grid-3">
            <div className="step"><h3>{t('Акт и условия', 'Report and terms')}</h3><p>{text3(company.guarantee_ru, company.guarantee_kz, company.guarantee_en)}</p></div>
            <div className="step"><h3>{t('Гарантийное обращение', 'Warranty claim')}</h3><p>{t('Напишите в Telegram или позвоните. Мы поднимем исходный заказ, посмотрим работы и материалы и организуем проверку.', 'Message us on Telegram or call. We open the original order, review the work and materials, and arrange an inspection.')}</p></div>
            <div className="step"><h3>{t('Повторный выезд', 'Repeat visit')}</h3><p>{t('Если нужен повторный выезд — согласуем время и мастера. Гарантийные случаи учитываются в истории исполнителя.', 'If a repeat visit is needed, we agree the time and master. Warranty cases are recorded in the master’s history.')}</p></div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container coverage">
          <div>
            <span className="eyebrow">{t('География', 'Coverage')}</span>
            <h2 className="h2">{t('Павлодар и пригород', 'Pavlodar and suburbs')}</h2>
            <p className="lead">{text3(company.coverage_ru, company.coverage_kz, company.coverage_en)}</p>
            <div className="info-list mt-20">
              <div className="info-item"><span className="ico">🏙️</span><div><b>{t('Все районы города', 'All city districts')}</b><span>{t('Выезд по любому адресу в пределах Павлодара', 'Any address within Pavlodar')}</span></div></div>
              <div className="info-item"><span className="ico">🚗</span><div><b>{t('Пригород', 'Suburbs')}</b><span>{t('Стоимость выезда за город согласуется отдельно', 'The price for trips outside the city is agreed separately')}</span></div></div>
            </div>
          </div>
          <div className="map-card">
            <MapView
              center={[52.2873, 76.9674]}
              zoom={11}
              circle={{ lat: 52.2873, lng: 76.9674, radius: 14000 }}
              markers={[{ id: 'pavlodar', lat: 52.2873, lng: 76.9674, html: '<div class="master-pin">🔧</div>', popup: 'KRANOFF' }]}
            />
          </div>
        </div>
      </section>
    </>
  );
}
