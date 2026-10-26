import { useEffect, useMemo, useState } from 'react';
import { api, money, useFetch, type Master, type OrderFull } from '../api';
import { Empty, Modal, Spinner, useToast } from '../ui';
import { MapView, type MapMarker } from '../components/MapView';
import { MASTER_STATUS_META } from './mapStatus';

export function AssignModal({
  order,
  onClose,
  onAssigned,
  onPointSaved,
}: {
  order: OrderFull;
  onClose: () => void;
  onAssigned: (order: OrderFull) => void;
  onPointSaved?: () => void;
}) {
  const toast = useToast();
  const { data, loading, reload } = useFetch<{ masters: Master[] }>(`/api/admin/orders/${order.id}/suggest`);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [point, setPoint] = useState<{ lat: number; lng: number } | null>(
    order.lat != null && order.lng != null ? { lat: order.lat, lng: order.lng } : null,
  );
  const [pointSaving, setPointSaving] = useState(false);
  const masters = useMemo(() => data?.masters || [], [data]);
  const suggested = useMemo(
    () => masters.find((m) => m.can_take && (m.match_count || 0) > 0) || masters.find((m) => m.can_take) || null,
    [masters],
  );
  const selected = masters.find((m) => m.id === selectedId) || suggested || null;

  useEffect(() => {
    if (selectedId == null && suggested) setSelectedId(suggested.id);
  }, [suggested, selectedId]);

  const orderPoint = point;
  const center: [number, number] = orderPoint ? [orderPoint.lat, orderPoint.lng] : [52.2873, 76.9674];

  const markers = useMemo<MapMarker[]>(() => {
    const list: MapMarker[] = [];
    if (orderPoint) {
      list.push({
        id: 'order',
        lat: orderPoint.lat,
        lng: orderPoint.lng,
        html: '<div class="order-pin">📍</div>',
        popup: `<b>${order.number}</b><br/>${order.address || ''}`,
      });
    }
    for (const master of masters) {
      if (master.current_lat == null || master.current_lng == null) continue;
      const state = MASTER_STATUS_META[master.display_status || master.status] || MASTER_STATUS_META.offline;
      const isSuggested = suggested?.id === master.id;
      list.push({
        id: `m-${master.id}`,
        lat: master.current_lat,
        lng: master.current_lng,
        pulse: isSuggested,
        stale: master.location_age_minutes != null && master.location_age_minutes > 3,
        html: `<div class="master-pin" style="background:${state.color}">${state.emoji}</div>`,
        popup: `<b>${master.name}</b><br/>${state.label}${master.distance_km != null ? ' · ' + master.distance_km + ' км' : ''}`,
        onClick: () => setSelectedId(master.id),
      });
    }
    return list;
  }, [masters, order.address, order.number, orderPoint, suggested]);

  const savePoint = async (lat: number, lng: number) => {
    if (pointSaving) return;
    setPointSaving(true);
    try {
      await api(`/api/admin/orders/${order.id}`, { method: 'PUT', body: JSON.stringify({ lat, lng }) });
      setPoint({ lat, lng });
      toast('Точка заказа сохранена. Считаю ближайшего мастера.', 'ok');
      reload();
      onPointSaved?.();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Не удалось сохранить точку', 'error');
    } finally {
      setPointSaving(false);
    }
  };

  const assign = async (master: Master, mode: 'offer' | 'manual') => {
    setBusy(master.id);
    try {
      const result = await api<OrderFull>(`/api/admin/orders/${order.id}/assign`, {
        method: 'POST',
        body: JSON.stringify({ masterId: master.id, mode }),
      });
      toast(mode === 'offer' ? `Предложение отправлено: ${master.name}` : `Назначен: ${master.name}`, 'ok');
      onAssigned(result);
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Не удалось назначить', 'error');
      reload();
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal title="Назначение мастера" wide onClose={onClose} footer={<button className="btn btn-outline" onClick={onClose}>Закрыть</button>}>
      <div className="assign-order-banner">
        <div>
          <span className="tag tag-red">Сейчас обрабатывается</span>
          <b style={{ marginLeft: 8 }}>{order.number}</b>
          <span className="muted small" style={{ marginLeft: 8 }}>{order.address || 'адрес не указан'}</span>
        </div>
        <span className="small">{order.problem} · {money(order.works_total)}</span>
      </div>

      {!loading && selected ? (
        <div className="assign-card">
          <div className="row-between" style={{ alignItems: 'flex-start', gap: 12 }}>
            <div>
              <div className="row" style={{ gap: 8 }}>
                <b style={{ fontSize: 15 }}>{selected.name}</b>
                {(() => {
                  const meta = MASTER_STATUS_META[selected.display_status || selected.status] || MASTER_STATUS_META.offline;
                  return <span className="pill" style={{ color: meta.color, background: `${meta.color}14` }}><span className="pill-dot" />{meta.label}</span>;
                })()}
                {selected.id === suggested?.id ? <span className="tag tag-green">ближайший подходящий</span> : null}
              </div>
              <div className="tiny muted mt-8">
                {selected.phone} · {selected.distance_km != null ? `${selected.distance_km} км до заказа` : 'без геопозиции'} · сигнал: {selected.location_age_minutes == null ? 'нет' : `${selected.location_age_minutes} мин назад`}
              </div>
              <div className="row wrap" style={{ gap: 4, marginTop: 7 }}>
                {(selected.skills_list || []).map((skill) => <span key={skill} className="tag tag-gray">{skill}</span>)}
              </div>
              <div className="tiny muted mt-8">
                Совпадение навыков: {selected.match_count || 0}. {selected.can_take ? 'Готов взять заказ.' : 'Сейчас занят или не на линии.'}
              </div>
            </div>
            <div className="row" style={{ gap: 6, flex: 'none' }}>
              <button className="btn btn-sm btn-outline" disabled={!selected.can_take || busy === selected.id} onClick={() => assign(selected, 'manual')}>
                {busy === selected.id ? '…' : 'Назначить'}
              </button>
              <button className="btn btn-sm btn-primary" disabled={!selected.can_take || !selected.telegram_id || busy === selected.id} onClick={() => assign(selected, 'offer')}>
                Предложить
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="map-legend-top">
        {Object.entries(MASTER_STATUS_META).filter(([key]) => key !== 'offline').map(([key, meta]) => (
          <span key={key} className="legend-item"><span className="legend-dot" style={{ background: meta.color }} />{meta.label}</span>
        ))}
        <span className="legend-item"><span className="legend-dot" style={{ background: '#b91c1c' }} />Заказ</span>
        <span className="tiny muted">Пульсирует ближайший подходящий по навыкам</span>
      </div>

      {loading ? <Spinner /> : (
        <>
          {!orderPoint ? (
            <div className="form-error" style={{ marginBottom: 10 }}>
              У заказа не указана точка. Кликните по карте — точка сохранится, и система рассчитает ближайшего подходящего мастера.
            </div>
          ) : null}
          <MapView
            className="assign-map"
            markers={markers}
            center={center}
            zoom={orderPoint ? 14 : 12}
            follow={orderPoint ? [orderPoint.lat, orderPoint.lng] : null}
            onMapClick={!orderPoint ? savePoint : undefined}
          />
          {masters.length > 0 ? (
            <div className="assign-list">
              {masters.map((master) => {
                const meta = MASTER_STATUS_META[master.display_status || master.status] || MASTER_STATUS_META.offline;
                return (
                  <button key={master.id} className={`assign-list-item ${selected?.id === master.id ? 'on' : ''}`} onClick={() => setSelectedId(master.id)}>
                    <span className="legend-dot" style={{ background: meta.color }} />
                    <span>{master.name}</span>
                    <span className="tiny muted">{master.distance_km != null ? `${master.distance_km} км` : '—'}</span>
                    <span className="tiny muted">{meta.label}</span>
                  </button>
                );
              })}
            </div>
          ) : null}
        </>
      )}
    </Modal>
  );
}
