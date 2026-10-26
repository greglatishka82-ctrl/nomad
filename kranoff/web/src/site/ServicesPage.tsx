import { useMemo, useState } from 'react';
import { usePublic, useDocumentMeta, telegramUrl } from '../public';
import { money } from '../api';

export function ServicesPage() {
  const { data, t, lang } = usePublic();
  const [query, setQuery] = useState('');
  useDocumentMeta(
    lang === 'kk' ? 'Қызметтер мен бағалар — KRANOFF, Павлодар' : 'Услуги и цены — KRANOFF, Павлодар',
    t('Сантехнические услуги и ориентировочные цены в Павлодаре: протечки, засоры, трубы, краны, унитазы, отопление, установка оборудования.', 'Plumbing services and approximate prices in Pavlodar: leaks, clogs, pipes, taps, toilets, heating, installation.'),
  );
  if (!data) return null;
  const company = data.company;
  const q = query.trim().toLowerCase();
  const categories = useMemo(
    () =>
      data.categories
        .map((cat) => ({
          ...cat,
          services: cat.services.filter((s) => {
            if (!q) return true;
            const hay = [s.name_ru, s.name_en, s.description_ru, s.description_en, cat.name_ru, cat.name_en].join(' ').toLowerCase();
            return hay.includes(q);
          }),
        }))
        .filter((cat) => cat.services.length > 0),
    [data.categories, q],
  );

  return (
    <>
      <section className="hero" style={{ padding: '58px 0 46px' }}>
        <div className="container">
          <span className="hero-eyebrow">{t('Услуги и цены', 'Services & prices')}</span>
          <h1 style={{ fontSize: 'clamp(30px, 4.4vw, 48px)', margin: '16px 0 12px' }}>{t('Что мы делаем', 'What we do')}</h1>
          <p className="hero-sub" style={{ maxWidth: 760 }}>
            {t(
              'Типовые работы с ориентировочными ценами. Окончательная стоимость — после осмотра мастером. Выезд и оценка бесплатны, материалы оплачиваются отдельно.',
              'Standard jobs with approximate prices. The final price is confirmed after the master’s inspection. The visit and estimate are free; materials are paid separately.',
            )}
          </p>
          <div className="hero-actions">
            <a className="btn btn-primary" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">{t('Вызвать мастера', 'Call a plumber')}</a>
            <a className="btn" href="#emergency" style={{ background: 'rgba(255,255,255,.1)', color: '#fff' }}>{t('Аварийный вызов', 'Emergency call')}</a>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="toolbar" style={{ marginBottom: 22 }}>
            <input className="input search" placeholder={t('Поиск по услугам…', 'Search services…')} value={query} onChange={(e) => setQuery(e.target.value)} style={{ maxWidth: 420 }} />
            <div className="row wrap" style={{ gap: 6 }}>
              {data.categories.map((cat) => (
                <a key={cat.id} href={`#${cat.slug}`} className="tab">{cat.icon} {lang === 'kk' ? cat.name_kz : lang === 'en' ? cat.name_en : cat.name_ru}</a>
              ))}
            </div>
          </div>

          {categories.length === 0 ? (
            <div className="empty">{t('Ничего не найдено. Попробуйте другой запрос.', 'Nothing found. Try another search.')}</div>
          ) : null}

          {categories.map((cat) => (
            <div key={cat.id} id={cat.slug} style={{ marginBottom: 42, scrollMarginTop: 96 }}>
              <div className="row-between mb-16" style={{ alignItems: 'flex-end' }}>
                <div>
                  <span className="eyebrow">{cat.icon} {cat.slug === 'emergency' ? t('Срочно', 'Urgent') : t('Категория', 'Category')}</span>
                  <h2 className="h2" style={{ fontSize: 28, margin: '6px 0 0' }}>{lang === 'kk' ? cat.name_kz : lang === 'en' ? cat.name_en : cat.name_ru}</h2>
                </div>
                <span className="muted small">{cat.services.length} {t('услуг', 'services')}</span>
              </div>
              <div className="service-grid">
                {cat.services.map((s) => (
                  <div key={s.id} className="service-card" style={s.is_emergency ? { borderColor: '#fecaca', background: '#fffbfb' } : undefined}>
                    <div className="row-between">
                      <span className="service-icon">{cat.icon || '🔧'}</span>
                      {s.is_emergency ? <span className="tag tag-red">{t('Аварийно', 'Emergency')}</span> : null}
                      {!s.is_emergency && s.price_night ? <span className="tag tag-gray">{t('ночь ×2', 'night ×2')}</span> : null}
                    </div>
                    <h3>{lang === 'kk' ? s.name_kz : lang === 'en' ? s.name_en : s.name_ru}</h3>
                    <p>{lang === 'kk' ? s.description_kz : lang === 'en' ? s.description_en : s.description_ru}</p>
                    <div className="service-price">
                      <b>{s.price === 0 ? t('бесплатно', 'free') : `${t('от', 'from')} ${money(s.price)}`}</b>
                      <span>{s.price_night ? `${t('ночью', 'night')} ${money(s.price_night)}` : t('окончательно после осмотра', 'final after inspection')}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          <div className="emergency" id="emergency" style={{ marginTop: 10 }}>
            <div>
              <h2>{t('Не нашли свою проблему?', 'Didn’t find your problem?')}</h2>
              <p>{t('Опишите ситуацию в Telegram — администратор подскажет, что делать, и подберёт мастера. Аварийные вызовы принимаем круглосуточно.', 'Describe the situation on Telegram — the dispatcher will advise and assign a master. Emergency calls are accepted 24/7.')}</p>
            </div>
            <div className="emergency-actions">
              <a className="btn btn-primary" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">{t('Написать в Telegram', 'Message on Telegram')}</a>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
