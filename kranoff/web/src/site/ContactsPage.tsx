import { MapView } from '../components/MapView';
import { usePublic, useDocumentMeta, telegramUrl, whatsappUrl, telUrl } from '../public';

export function ContactsPage() {
  const { data, t, lang } = usePublic();
  useDocumentMeta(
    lang === 'kk' ? 'KRANOFF байланыстары — Павлодар' : 'Контакты KRANOFF — Павлодар',
    t('Контакты сантехнической службы KRANOFF в Павлодаре: Telegram, WhatsApp, телефон. Круглосуточный приём заявок.', 'KRANOFF plumbing service contacts in Pavlodar: Telegram, WhatsApp, phone. Requests accepted 24/7.'),
  );
  if (!data) return null;
  const company = data.company;
  const lines = [
    company.phone ? { label: t('Телефон', 'Phone'), value: company.phone, href: telUrl(company.phone) } : null,
    company.whatsapp ? { label: 'WhatsApp', value: `+${company.whatsapp.replace(/\D/g, '')}`, href: whatsappUrl(company.whatsapp) } : null,
    { label: 'Telegram', value: `@${company.telegram}`, href: telegramUrl(company.telegram) },
    { label: t('Адрес', 'Address'), value: company.address, href: undefined },
    { label: t('Режим работы', 'Hours'), value: t(company.hours, company.hours === 'Круглосуточно' ? '24/7' : company.hours), href: undefined },
  ].filter(Boolean) as Array<{ label: string; value: string; href?: string }>;

  return (
    <>
      <section className="hero" style={{ padding: '58px 0 46px' }}>
        <div className="container">
          <span className="hero-eyebrow">{t('Контакты', 'Contacts')}</span>
          <h1 style={{ fontSize: 'clamp(30px, 4.4vw, 48px)', margin: '16px 0 12px' }}>{t('Свяжитесь с нами', 'Get in touch')}</h1>
          <p className="hero-sub" style={{ maxWidth: 720 }}>
            {t('Принимаем заявки круглосуточно. Напишите в Telegram или WhatsApp — администратор ответит и уточнит детали.', 'Requests are accepted 24/7. Message us on Telegram or WhatsApp — the dispatcher will reply and confirm the details.')}
          </p>
          <div className="hero-actions">
            <a className="btn btn-primary" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">Telegram</a>
            {company.whatsapp ? <a className="btn" href={whatsappUrl(company.whatsapp)} target="_blank" rel="noreferrer" style={{ background: 'rgba(255,255,255,.1)', color: '#fff' }}>WhatsApp</a> : null}
            {company.phone ? <a className="btn" href={telUrl(company.phone)} style={{ background: 'rgba(255,255,255,.1)', color: '#fff' }}>{company.phone}</a> : null}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container contact-grid">
          <div className="contact-card">
            <h3>{t('Контакты службы', 'Service contacts')}</h3>
            <p className="muted small">{company.name} · {company.slogan}</p>
            <div className="contact-lines">
              {lines.map((line) => (
                <div className="contact-line" key={line.label}>
                  <span>{line.label}</span>
                  {line.href ? <a href={line.href} target={line.href.startsWith('http') ? '_blank' : undefined} rel="noreferrer"><b>{line.value}</b></a> : <b>{line.value}</b>}
                </div>
              ))}
            </div>
            <a className="btn btn-primary btn-block mt-16" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">
              {t('Оставить заявку в Telegram', 'Request service on Telegram')}
            </a>
            <p className="muted small mt-12">
              {t('Если не пользуетесь Telegram — напишите в WhatsApp или позвоните, администратор оформит заказ сам.', 'If you don’t use Telegram — message WhatsApp or call, the dispatcher will create the order.')}
            </p>
          </div>
          <div className="map-card">
            <MapView
              center={[52.2873, 76.9674]}
              zoom={12}
              circle={{ lat: 52.2873, lng: 76.9674, radius: 14000 }}
              markers={[{ id: 'pavlodar', lat: 52.2873, lng: 76.9674, html: '<div class="master-pin">🔧</div>', popup: 'KRANOFF · Павлодар' }]}
            />
          </div>
        </div>
      </section>
    </>
  );
}
