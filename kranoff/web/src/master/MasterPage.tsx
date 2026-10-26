import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { formatTime, money, prettyPhone, statusLabel } from '../api';
import { MapView } from '../components/MapView';

type MasterData = {
  master: {
    id: number; name: string; phone: string; status: string; shift_started_at: string | null;
    location_updated_at: string | null; current_lat: number | null; current_lng: number | null;
  };
  order: {
    id: number; number: string; status: string; problem: string; description: string; address: string;
    lat: number | null; lng: number | null; entrance: string; floor: string; intercom: string;
    desired_label: string; works_total: number; eta_minutes: number | null; tariff: string;
    client: { name: string; phone: string | null };
    master_earnings: number;
    media?: Array<{ id: number; mime: string; kind: string }>;
  } | null;
  earnings: { shift: number; last24h: number; today: number; orders: number };
  company: { name: string; phone: string; whatsapp: string; address: string };
};

type OwnPosition = { lat: number; lng: number; accuracy: number; at: number };

export function MasterPage() {
  const { token } = useParams();
  const [data, setData] = useState<MasterData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [own, setOwn] = useState<OwnPosition | null>(null);
  const [geoError, setGeoError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(true);
  const [route, setRoute] = useState<{ coords: Array<[number, number]>; distance: number | null; duration: number | null; straight: boolean } | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const lastSent = useRef(0);
  const lastRoute = useRef(0);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/master/${token}`);
      if (!res.ok) throw new Error('Страница не найдена');
      setData(await res.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка загрузки');
    }
  }, [token]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 20_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    let closed = false;
    let retry: number | undefined;
    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${proto}://${window.location.host}/ws/master?token=${encodeURIComponent(token || '')}`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = window.setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      closed = true;
      if (retry) window.clearTimeout(retry);
      wsRef.current?.close();
    };
  }, [token]);

  const sendLocation = useCallback(async (lat: number, lng: number, accuracy: number) => {
    const now = Date.now();
    if (now - lastSent.current < 5000) return;
    lastSent.current = now;
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'location', lat, lng, accuracy }));
    } else {
      try {
        await fetch(`/api/master/${token}/location`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lat, lng, accuracy }),
        });
      } catch {
        // offline — next attempt will retry
      }
    }
  }, [token]);

  useEffect(() => {
    if (!navigator.geolocation) {
      setGeoError('Браузер не поддерживает геолокацию');
      return;
    }
    const watch = navigator.geolocation.watchPosition(
      (position) => {
        const next = { lat: position.coords.latitude, lng: position.coords.longitude, accuracy: position.coords.accuracy, at: Date.now() };
        setOwn(next);
        setGeoError(null);
        void sendLocation(next.lat, next.lng, next.accuracy);
      },
      (err) => {
        setGeoError(
          err.code === err.PERMISSION_DENIED
            ? 'Разрешите доступ к геолокации в настройках браузера и обновите страницу.'
            : 'Не удалось определить местоположение. Проверьте GPS и интернет.',
        );
      },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 },
    );
    return () => navigator.geolocation.clearWatch(watch);
  }, [sendLocation]);

  useEffect(() => {
    const destLat = data?.order?.lat;
    const destLng = data?.order?.lng;
    if (!own || destLat == null || destLng == null) {
      setRoute(null);
      return;
    }
    if (Date.now() - lastRoute.current < 20_000) return;
    lastRoute.current = Date.now();
    const params = new URLSearchParams({
      fromLat: String(own.lat),
      fromLng: String(own.lng),
      toLat: String(destLat),
      toLng: String(destLng),
    });
    fetch(`/api/master/${token}/route?${params.toString()}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((value) => {
        if (value?.coords?.length) setRoute(value);
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [own?.lat, own?.lng, data?.order?.lat, data?.order?.lng, token]);

  const action = async (name: 'on_way' | 'arrived' | 'start' | 'done' | 'cancel') => {
    if (name === 'cancel' && !window.confirm('Снять заказ с себя? Администратор назначит другого мастера.')) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch(`/api/master/${token}/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: name }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || 'Ошибка');
      await load();
      setMessage('Готово. Клиент получил уведомление.');
      if (navigator.vibrate) navigator.vibrate(80);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Ошибка');
    } finally {
      setBusy(false);
    }
  };

  const setEta = async (minutes: number) => {
    setBusy(true);
    try {
      const res = await fetch(`/api/master/${token}/eta`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ minutes }),
      });
      if (!res.ok) throw new Error('Ошибка');
      setMessage(`Клиенту сообщено: ${minutes} мин.`);
      await load();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Ошибка');
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div className="master-page" style={{ padding: 24, display: 'grid', placeItems: 'center' }}>
        <div style={{ textAlign: 'center' }}>
          <h1 style={{ fontSize: 22 }}>Страница недоступна</h1>
          <p style={{ color: '#9fc3e0', marginTop: 8 }}>{error}</p>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="master-page" style={{ display: 'grid', placeItems: 'center' }}>
        <div className="loading" style={{ color: '#fff' }}><span className="spinner" /> Загрузка…</div>
      </div>
    );
  }

  const order = data.order;
  const dest = order?.lat != null && order?.lng != null ? { lat: order.lat, lng: order.lng } : null;
  const center: [number, number] = own ? [own.lat, own.lng] : dest ? [dest.lat, dest.lng] : [52.2873, 76.9674];
  const markers = [];
  if (own) markers.push({ id: 'own', lat: own.lat, lng: own.lng, html: '<div class="master-pin" style="background:#0284c7">🚐</div>', popup: 'Вы здесь' });
  if (dest) markers.push({ id: 'dest', lat: dest.lat, lng: dest.lng, html: '<div class="order-pin">🏠</div>', popup: order?.address || 'Адрес заказа' });

  const showOnWay = order?.status === 'accepted';
  const showArrived = order?.status === 'on_way';
  const showStart = order?.status === 'arrived' || order?.status === 'approval';
  const showDone = order?.status === 'in_progress';
  const distanceText = route?.distance != null ? (route.distance < 1000 ? `${Math.round(route.distance)} м` : `${(route.distance / 1000).toFixed(1)} км`) : null;
  const durationText = route?.duration != null ? `${Math.max(1, Math.round(route.duration / 60))} мин` : null;

  return (
    <div className="master-page">
      <div className="mp-head">
        <img src="/logo-emblem.svg" alt="" />
        <div>
          <b>{data.master.name}</b>
          <span>{data.master.status === 'free' ? 'На линии' : data.master.status === 'busy' ? 'Занят' : 'Не на линии'}{data.master.shift_started_at ? ` · смена с ${formatTime(data.master.shift_started_at)}` : ''}</span>
        </div>
        <div className="mp-status">
          <span className={`conn ${connected ? '' : 'bad'}`} />
          {connected ? 'связь есть' : 'нет связи'}
        </div>
      </div>

      <MapView
        className="mp-map"
        markers={markers}
        center={center}
        zoom={16}
        follow={own ? [own.lat, own.lng] : null}
        line={route?.coords || (own && dest ? [[own.lat, own.lng], [dest.lat, dest.lng]] : null)}
        lineDashed={!route || route.straight}
        fitLine
      />

      <div className={`mp-sheet ${sheetOpen ? 'open' : 'collapsed'}`}>
        <div className="mp-sheet-head">
          <div>
            <h2>{order ? order.number : 'Нет активного заказа'}</h2>
            <div className="mp-order-meta">
              {order ? `${statusLabel(order.status)} · ${order.tariff === 'night' || order.tariff === 'emergency_night' ? 'ночной тариф' : 'дневной тариф'}` : 'Ожидание предложения'}
              {distanceText ? ` · до адреса ${distanceText}${durationText ? `, ~${durationText}` : ''}` : ''}
            </div>
          </div>
          <button className="mp-toggle" onClick={() => setSheetOpen((value) => !value)}>
            {sheetOpen ? 'Свернуть' : 'Развернуть'} {sheetOpen ? '⌄' : '⌃'}
          </button>
        </div>
        <div className={`mp-sheet-body ${sheetOpen ? '' : 'closed'}`}>
        {order ? (
          <>
            <div className="row-between">
              <div>
                <div className="mp-order-meta">{order.problem}</div>
              </div>
              <div className="right">
                <b style={{ fontSize: 16 }}>{money(order.works_total)}</b>
                <div className="tiny muted">стоимость работ</div>
              </div>
            </div>

            <div className="mp-address">{order.address || 'Адрес уточняется'}</div>
            <div className="small muted">
              {[order.entrance && `подъезд ${order.entrance}`, order.floor && `этаж ${order.floor}`, order.intercom && `домофон ${order.intercom}`].filter(Boolean).join(' · ') || 'доступ уточните у клиента'}
            </div>
            {distanceText ? (
              <div className="mp-route-info">
                До адреса: <b>{distanceText}</b>{durationText ? ` · в пути ~${durationText}` : ''}
                {route?.straight ? ' · по прямой' : ''}
              </div>
            ) : null}

            <div className="row-between mt-12">
              <div>
                <div style={{ fontWeight: 700 }}>{order.client.name}</div>
                <div className="small muted">{prettyPhone(order.client.phone)}</div>
              </div>
              <div className="row" style={{ gap: 6 }}>
                {order.client.phone ? <a className="btn btn-sm btn-outline" href={`tel:${order.client.phone.replace(/[^\d+]/g, '')}`}>Позвонить</a> : null}
              </div>
            </div>

            <div className="panel" style={{ padding: 12, marginTop: 12, background: '#f8fbfd' }}>
              <div className="detail-title">Проблема</div>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{order.problem}</div>
              {order.description ? <p className="small muted mt-8">{order.description}</p> : null}
              {order.media?.some((m) => m.mime.startsWith('image/')) ? (
                <div className="media-grid mt-12">
                  {order.media.filter((m) => m.mime.startsWith('image/')).slice(0, 3).map((media) => (
                    <a key={media.id} className="media-thumb" href={`/api/master/${token}/media/${media.id}`} target="_blank" rel="noreferrer">
                      <img src={`/api/master/${token}/media/${media.id}`} alt="" loading="lazy" />
                    </a>
                  ))}
                </div>
              ) : null}
            </div>

            {order.eta_minutes != null ? <p className="mp-hint">Клиенту сообщено прибытие через {order.eta_minutes} мин.</p> : null}

            <div className="mp-actions">
              {showOnWay ? <button className="btn btn-primary btn-block" disabled={busy} onClick={() => action('on_way')}>🚗 Выехал</button> : null}
              {showArrived ? <button className="btn btn-primary btn-block" disabled={busy} onClick={() => action('arrived')}>📍 На месте</button> : null}
              {showStart ? <button className="btn btn-primary btn-block" disabled={busy} onClick={() => action('start')}>🔧 Начал работу</button> : null}
              {showDone ? <button className="btn btn-primary btn-block" disabled={busy} onClick={() => action('done')}>✅ Выполнено</button> : null}
              {['accepted', 'on_way', 'arrived', 'approval'].includes(order.status) ? (
                <button className="btn btn-danger" disabled={busy} onClick={() => action('cancel')}>Не могу выехать</button>
              ) : null}
              <button className="btn btn-outline" disabled={busy} onClick={() => setEta(15)}>15 мин</button>
              <button className="btn btn-outline" disabled={busy} onClick={() => setEta(30)}>30 мин</button>
            </div>

            {message ? <p className="mp-hint" style={{ color: message.includes('Ошибка') ? 'var(--red)' : 'var(--green)' }}>{message}</p> : null}
            {geoError ? <p className="mp-hint" style={{ color: 'var(--red)' }}>{geoError}</p> : <p className="mp-hint">Геопозиция передаётся каждые 5 секунд, пока страница открыта. Не закрывайте её в дороге.</p>}
            {own ? <p className="mp-hint">Точность GPS: ±{Math.round(own.accuracy)} м · обновлено {formatTime(new Date(own.at).toISOString())}</p> : null}
          </>
        ) : (
          <>
            <p className="mp-hint">Отметьтесь «На линии» в боте @kranmaster_bot, чтобы получать предложения. Геопозиция передаётся, пока страница открыта.</p>
            <div className="mp-earn">
              <div><span>За смену</span><b>{money(data.earnings.shift)}</b></div>
              <div><span>Заказов</span><b>{data.earnings.orders}</b></div>
            </div>
            {geoError ? <p className="mp-hint" style={{ color: 'var(--red)' }}>{geoError}</p> : null}
          </>
        )}
        </div>
      </div>
    </div>
  );
}
