import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { formatDateTime, relativeMinutes, statusLabel, useFetch, type Master, type Order, escapeHtml } from '../api';
import { MapView, type MapMarker } from '../components/MapView';
import { MASTER_STATUS_META as STATUS_META } from './mapStatus';
import { Spinner } from '../ui';

export function MapPage() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useFetch<{ masters: Master[]; orders: Order[] }>('/api/admin/map');
  const [showMasters, setShowMasters] = useState(true);
  const [showOrders, setShowOrders] = useState(true);

  useEffect(() => {
    let timer: number | undefined;
    const handler = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => reload(), 400);
    };
    window.addEventListener('kranoff:ws', handler);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('kranoff:ws', handler);
    };
  }, [reload]);

  const markers = useMemo<MapMarker[]>(() => {
    const list: MapMarker[] = [];
    if (showMasters) {
      for (const master of data?.masters || []) {
        if (master.current_lat == null || master.current_lng == null) continue;
        const state = STATUS_META[master.display_status || master.status] || STATUS_META.offline;
        if (state === STATUS_META.offline) continue;
        const stale = master.location_updated_at ? Date.now() - new Date(master.location_updated_at).getTime() > 3 * 60_000 : true;
        list.push({
          id: `m-${master.id}`,
          lat: master.current_lat,
          lng: master.current_lng,
          stale,
          html: `<div class="master-pin" style="background:${state.color}">${state.emoji}</div>`,
          popup: `<b>${escapeHtml(master.name)}</b><br/>${state.label}<br/>${master.location_updated_at ? 'Сигнал: ' + relativeMinutes(master.location_updated_at) : 'нет геопозиции'}<br/>${escapeHtml(master.phone)}`,
        });
      }
    }
    if (showOrders) {
      for (const order of data?.orders || []) {
        if (order.lat == null || order.lng == null) continue;
        list.push({
          id: `o-${order.id}`,
          lat: order.lat,
          lng: order.lng,
          html: `<div class="order-pin" ${order.is_emergency ? 'style="background:#b91c1c"' : ''}>📍</div>`,
          popup: `<b>${escapeHtml(order.number)}</b><br/>${statusLabel(order.status)}<br/>${escapeHtml(order.address || '')}<br/><a href="/admin/orders/${order.id}">Открыть заказ</a>`,
        });
      }
    }
    return list;
  }, [data, showMasters, showOrders]);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Карта</h1>
          <p>Мастера и заказы в реальном времени. Обновление геопозиции — каждые 5 секунд.</p>
        </div>
        <div className="row">
          <label className="checkbox"><input type="checkbox" checked={showMasters} onChange={(e) => setShowMasters(e.target.checked)} /> Мастера</label>
          <label className="checkbox"><input type="checkbox" checked={showOrders} onChange={(e) => setShowOrders(e.target.checked)} /> Заказы</label>
          <button className="btn btn-sm btn-outline" onClick={() => reload()}>Обновить</button>
        </div>
      </div>

      {loading && !data ? <Spinner /> : error ? <div className="form-error">{error}</div> : (
        <div>
          <div className="map-legend-top">
            {Object.entries(STATUS_META).filter(([key]) => key !== 'offline').map(([key, meta]) => (
              <span key={key} className="legend-item"><span className="legend-dot" style={{ background: meta.color }} />{meta.label}</span>
            ))}
            <span className="legend-item"><span className="legend-dot" style={{ background: '#b91c1c' }} />Заказ</span>
            <span className="tiny muted">Мастера без смены на карте не отображаются</span>
          </div>
          <MapView className="map-page" markers={markers} zoom={12} />
        </div>
      )}

      <div className="cards" style={{ marginTop: 16 }}>
        <div className="stat"><div className="stat-label">Мастеров на карте</div><div className="stat-value">{(data?.masters || []).filter((m) => m.current_lat != null).length}</div></div>
        <div className="stat"><div className="stat-label">Активных заказов с адресом</div><div className="stat-value">{(data?.orders || []).filter((o) => o.lat != null).length}</div></div>
        <div className="stat"><div className="stat-label">Свободны</div><div className="stat-value">{(data?.masters || []).filter((m) => m.display_status === 'free').length}</div></div>
        <div className="stat"><div className="stat-label">Заняты</div><div className="stat-value">{(data?.masters || []).filter((m) => ['busy', 'on_way', 'paused'].includes(m.display_status || '')).length}</div></div>
      </div>

      <div className="panel mt-16">
        <div className="panel-head"><h2>Мастера</h2><Link className="link small" to="/admin/masters">Управление →</Link></div>
        <div className="table-wrap">
          <table className="map-masters">
            <thead><tr><th>Мастер</th><th>Статус</th><th>Геопозиция</th><th>Активные заказы</th><th /></tr></thead>
            <tbody>
              {(data?.masters || []).map((master) => (
                <tr
                  key={master.id}
                  style={{ cursor: 'pointer' }}
                  title="Открыть карточку мастера"
                  onClick={() => navigate(`/admin/masters?master=${master.id}`)}
                >
                  <td><b>{master.name}</b><div className="tiny muted">{master.phone}</div></td>
                  <td>{(() => { const meta = STATUS_META[master.display_status || master.status] || STATUS_META.offline; return <span className="pill" style={{ color: meta.color, background: `${meta.color}14` }}><span className="pill-dot" />{meta.label}</span>; })()}</td>
                  <td className="muted">{master.location_updated_at ? `${relativeMinutes(master.location_updated_at)} · ${formatDateTime(master.location_updated_at)}` : 'нет данных'}</td>
                  <td>{master.active_orders ?? 0}</td>
                  <td className="right"><Link className="link small" to={`/admin/masters?master=${master.id}`} onClick={(event) => event.stopPropagation()}>карточка</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
