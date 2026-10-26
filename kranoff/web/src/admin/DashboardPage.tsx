import { useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { formatDateTime, money, relativeMinutes, statusColor, statusLabel, useFetch, type DashboardData } from '../api';
import { Empty, Spinner, StatusPill } from '../ui';

export function DashboardPage() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useFetch<DashboardData>('/api/admin/dashboard');

  useEffect(() => {
    const handler = () => reload();
    window.addEventListener('kranoff:ws', handler);
    return () => window.removeEventListener('kranoff:ws', handler);
  }, [reload]);

  if (loading && !data) return <Spinner />;
  if (error && !data) return <div className="form-error">{error}</div>;
  if (!data) return null;

  const c = data.counts;
  const cards = [
    { label: 'Новые заявки', value: c.new, foot: 'ожидают рассмотрения', accent: true },
    { label: 'Аварийные', value: c.emergency, foot: 'активные вызовы', red: c.emergency > 0 },
    { label: 'Мастера на смене', value: c.onShift, foot: 'на линии и заняты' },
    { label: 'В пути', value: c.onWay, foot: 'едут к клиенту' },
    { label: 'На объекте', value: c.atWork, foot: 'работают / согласуют' },
    { label: 'Поддержка', value: c.unreadSupport, foot: 'диалогов без ответа' },
    { label: 'Закрыто сегодня', value: c.closedToday, foot: 'заказов' },
    { label: 'Выручка сегодня', value: money(c.revenueToday), foot: 'по закрытым заказам', money: true },
  ];

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Главная</h1>
          <p>Новые заявки, мастера на смене и работы в процессе</p>
        </div>
        <div className="row">
          <Link className="btn btn-outline btn-sm" to="/admin/orders?status=new">Новые заявки</Link>
          <Link className="btn btn-primary btn-sm" to="/admin/map">Открыть карту</Link>
        </div>
      </div>

      <div className="cards">
        {cards.map((card) => (
          <div key={card.label} className={`stat ${card.accent ? 'accent' : ''} ${card.red ? 'red' : ''}`}>
            <div className="stat-label">{card.label}</div>
            <div className="stat-value" style={card.money ? { fontSize: 21 } : undefined}>{card.value}</div>
            <div className="stat-foot">{card.foot}</div>
          </div>
        ))}
      </div>

      {c.onShift < 2 ? (
        <div className="shift-banner" style={{ background: '#fffbeb', borderColor: '#fde68a', color: '#92400e' }}>
          На смене {c.onShift} мастер(а). По правилу службы минимум 2 мастера на смене — свяжитесь с мастерами и договоритесь о подмене.
        </div>
      ) : null}

      <div className="order-layout">
        <div>
          <div className="panel">
            <div className="panel-head">
              <h2>Последние заказы</h2>
              <Link className="link small" to="/admin/orders">Все заказы →</Link>
            </div>
            <div className="panel-body flush">
              {data.latest.length === 0 ? (
                <Empty text="Заказов пока нет" />
              ) : (
                <div className="table-wrap">
                  <table className="dashboard-orders">
                    <thead>
                      <tr>
                        <th>Заказ</th>
                        <th>Клиент</th>
                        <th>Проблема</th>
                        <th>Статус</th>
                        <th>Мастер</th>
                        <th className="right">Сумма</th>
                        <th>Создан</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.latest.map((order) => (
                        <tr key={order.id} onClick={() => navigate(`/admin/orders/${order.id}`)}>
                          <td><b>{order.number}</b></td>
                          <td>{order.client_name || '—'}<div className="tiny muted">{order.client_phone || ''}</div></td>
                          <td style={{ maxWidth: 230 }}><div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{order.problem}</div></td>
                          <td><StatusPill status={order.status} label={statusLabel(order.status)} color={statusColor(order.status)} /></td>
                          <td>{order.master_name || <span className="muted">—</span>}</td>
                          <td className="right mono">{money(order.works_total)}</td>
                          <td className="muted nowrap">{formatDateTime(order.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>Мастера на смене</h2>
              <Link className="link small" to="/admin/masters">Все мастера →</Link>
            </div>
            <div className="panel-body">
              {data.masters.filter((m) => m.shift_started_at).length === 0 ? (
                <Empty text="На смене нет мастеров" />
              ) : (
                <div className="grid-2">
                  {data.masters.filter((m) => m.shift_started_at).map((m) => (
                    <div key={m.id} className="row-between" style={{ padding: '10px 0', borderBottom: '1px solid var(--line-2)' }}>
                      <div>
                        <b style={{ fontSize: 13 }}>{m.name}</b>
                        <div className="tiny muted">{m.status === 'free' ? 'Свободен' : m.status === 'busy' ? 'Занят' : 'Не в сети'} · {m.location_updated_at ? relativeMinutes(m.location_updated_at) : 'нет геопозиции'}</div>
                      </div>
                      <span className="pill" style={{ color: m.status === 'free' ? '#15803d' : m.status === 'busy' ? '#b45309' : '#64748b', background: 'transparent' }}>
                        <span className="pill-dot" />{m.status === 'free' ? 'свободен' : m.status === 'busy' ? 'занят' : 'offline'}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="order-side">
          <div className="panel">
            <div className="panel-head"><h2>Требуют внимания</h2></div>
            <div className="panel-body">
              {data.stale.length === 0 && data.offers.length === 0 ? (
                <p className="muted small">Всё под контролем.</p>
              ) : (
                <div className="stack">
                  {data.stale.map((m) => (
                    <div key={m.id} className="row-between" style={{ gap: 8 }}>
                      <div>
                        <b style={{ fontSize: 12.5 }}>{m.name}</b>
                        <div className="tiny muted">Геопозиция: {m.location_updated_at ? relativeMinutes(m.location_updated_at) : 'нет данных'}</div>
                      </div>
                      <span className="tag tag-amber">сигнал</span>
                    </div>
                  ))}
                  {data.offers.map((offer) => (
                    <div key={offer.id} className="row-between" style={{ gap: 8 }}>
                      <div>
                        <b style={{ fontSize: 12.5 }}>{offer.number}</b>
                        <div className="tiny muted">{offer.master_name} · до {formatDateTime(offer.expires_at)}</div>
                      </div>
                      <Link className="link small" to={`/admin/orders/${offer.order_id}`}>открыть</Link>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head"><h2>Быстрые действия</h2></div>
            <div className="panel-body stack">
              <Link className="btn btn-primary btn-block" to="/admin/orders?create=1">+ Создать заказ</Link>
              <Link className="btn btn-outline btn-block" to="/admin/orders?status=active">Активные заказы</Link>
              <Link className="btn btn-outline btn-block" to="/admin/support">Поддержка{data.counts.unreadSupport ? ` · ${data.counts.unreadSupport}` : ''}</Link>
              <Link className="btn btn-outline btn-block" to="/admin/analytics">Аналитика</Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
