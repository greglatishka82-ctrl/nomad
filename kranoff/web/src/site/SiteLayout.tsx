import { useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { usePublic, telegramUrl, whatsappUrl, telUrl } from '../public';
import { Spinner } from '../ui';

export function SiteLayout() {
  const { data, loading, error, reload, lang, setLang, t } = usePublic();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setMenuOpen(false);
    window.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior });
  }, [location.pathname]);

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#06131e' }}>
        <Spinner label="KRANOFF" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#f4f8fc', padding: 20 }}>
        <div className="panel" style={{ padding: 28, maxWidth: 460, textAlign: 'center' }}>
          <h2 className="h2" style={{ fontSize: 22 }}>Не удалось загрузить сайт</h2>
          <p className="lead" style={{ margin: '10px 0 18px' }}>{error || 'Сервер недоступен'}</p>
          <button className="btn btn-primary" onClick={reload}>Повторить</button>
        </div>
      </div>
    );
  }

  const company = data.company;
  const nav = [
    { to: '/services', label: t('Услуги и цены', 'Services & prices') },
    { to: '/about', label: t('О компании', 'About') },
    { to: '/contacts', label: t('Контакты', 'Contacts') },
  ];

  return (
    <div className="site">
      <header className="site-header">
        <div className="container site-header-inner">
          <Link to="/" className="brand" aria-label="KRANOFF">
            <span className="brand-badge"><img src="/logo-emblem.svg" alt="KRANOFF" /></span>
            <span>
              <span className="brand-name">KRANOFF</span>
              <span className="brand-sub">{t('Решил — Действуй', 'Decide — Act')}</span>
            </span>
          </Link>
          <nav className={`site-nav ${menuOpen ? 'open' : ''}`}>
            {nav.map((item) => (
              <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? 'active' : '')}>
                {item.label}
              </NavLink>
            ))}
            <a href="/#how">{t('Как это работает', 'How it works')}</a>
            <a href="/#faq">{t('Вопросы', 'FAQ')}</a>
          </nav>
          <div className="header-actions">
            <div className="lang-switch" aria-label="Language">
              <button className={lang === 'ru' ? 'on' : ''} onClick={() => setLang('ru')}>RU</button>
              <button className={lang === 'kk' ? 'on' : ''} onClick={() => setLang('kk')}>KZ</button>
              <button className={lang === 'en' ? 'on' : ''} onClick={() => setLang('en')}>EN</button>
            </div>
            {company.phone && (
              <a className="header-phone" href={telUrl(company.phone)}>{company.phone}</a>
            )}
            <a className="btn btn-primary btn-sm" href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">
              {t('Вызвать мастера', 'Call a plumber')}
            </a>
            <button className="icon-btn mobile-toggle" onClick={() => setMenuOpen((v) => !v)} aria-label="Меню" style={{ background: 'transparent', borderColor: 'rgba(255,255,255,.2)', color: '#fff' }}>
              ☰
            </button>
          </div>
        </div>
      </header>

      <Outlet />

      <footer className="site-footer">
        <div className="container">
          <div className="footer-grid">
            <div className="footer-brand">
              <Link to="/" className="brand">
                <span className="brand-badge"><img src="/logo-emblem.svg" alt="KRANOFF" /></span>
                <span>
                  <span className="brand-name">KRANOFF</span>
                  <span className="brand-sub">{t('Решил — Действуй', 'Decide — Act')}</span>
                </span>
              </Link>
              <p className="footer-text">
                {t(
                  'Сантехническая служба в Павлодаре. Аварийные вызовы круглосуточно, ремонт и установка сантехники. Выезд и оценка стоимости бесплатно.',
                  'Plumbing service in Pavlodar. Emergency calls 24/7, plumbing repair and installation. Visit and estimate are free.',
                )}
              </p>
            </div>
            <div className="footer-col">
              <h4>{t('Услуги', 'Services')}</h4>
              <Link to="/services">{t('Все услуги и цены', 'All services & prices')}</Link>
              <Link to="/services#emergency">{t('Аварийный вызов', 'Emergency call')}</Link>
              <Link to="/services#clogs">{t('Устранение засоров', 'Clog removal')}</Link>
              <Link to="/services#pipes">{t('Замена труб', 'Pipe replacement')}</Link>
            </div>
            <div className="footer-col">
              <h4>{t('Компания', 'Company')}</h4>
              <Link to="/about">{t('О компании', 'About')}</Link>
              <Link to="/about#guarantee">{t('Гарантия', 'Warranty')}</Link>
              <Link to="/contacts">{t('Контакты', 'Contacts')}</Link>
              <Link to="/privacy">{t('Политика конфиденциальности', 'Privacy policy')}</Link>
            </div>
            <div className="footer-col">
              <h4>{t('Связаться', 'Contact us')}</h4>
              {company.phone && <a href={telUrl(company.phone)}>{company.phone}</a>}
              {company.whatsapp && <a href={whatsappUrl(company.whatsapp)} target="_blank" rel="noreferrer">WhatsApp</a>}
              <a href={telegramUrl(company.telegram)} target="_blank" rel="noreferrer">Telegram</a>
              <span>{company.address}</span>
              <span>{t(company.hours, company.hours === 'Круглосуточно' ? '24/7' : company.hours)}</span>
            </div>
          </div>
          <div className="footer-bottom">
            <span>© {new Date().getFullYear()} KRANOFF. {t('Все права защищены.', 'All rights reserved.')}</span>
            <span>{t('Сантехническая служба, Павлодар', 'Plumbing service, Pavlodar')}</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
