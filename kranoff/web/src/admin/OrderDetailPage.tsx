import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  api,
  formatDateTime,
  money,
  prettyPhone,
  SOURCE_LABELS,
  statusColor,
  statusLabel,
  TARIFF_LABELS,
  URGENCY_LABELS,
  useFetch,
  escapeHtml,
  type Media,
  type OrderFull,
  type Service,
} from '../api';
import { Empty, Field, Modal, Spinner, StatusPill, useToast } from '../ui';
import { MapView } from '../components/MapView';
import { AssignModal } from './AssignModal';

const MEDIA_KINDS: Record<string, string> = { before: 'До', after: 'После', contract: 'Договор', other: 'Другое' };

export function OrderDetailPage() {
  const { id } = useParams();
  const orderId = Number(id);
  const navigate = useNavigate();
  const toast = useToast();
  const { data: order, loading, error, reload } = useFetch<OrderFull>(`/api/admin/orders/${orderId}`, [orderId]);
  const services = useFetch<{ categories: Array<{ id: number; name_ru: string }>; services: Service[] }>('/api/admin/services');
  const [assignOpen, setAssignOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [closeOpen, setCloseOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploadKind, setUploadKind] = useState('before');
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (!detail || detail.orderId === orderId || detail.type === 'snapshot') reload();
    };
    window.addEventListener('kranoff:ws', handler);
    return () => window.removeEventListener('kranoff:ws', handler);
  }, [orderId, reload]);

  const setStatus = async (status: string) => {
    setBusy(true);
    try {
      await api(`/api/admin/orders/${orderId}/status`, { method: 'POST', body: JSON.stringify({ status }) });
      toast('Статус обновлён', 'ok');
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!window.confirm('Отменить заказ? Клиент и мастер получат уведомление.')) return;
    setBusy(true);
    try {
      await api(`/api/admin/orders/${orderId}/cancel`, { method: 'POST' });
      toast('Заказ отменён', 'ok');
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setBusy(false);
    }
  };

  const removeOrder = async () => {
    if (!window.confirm(`Удалить заказ ${order.number} (${order.client.name})? Действие необратимо: заказ, его медиа и история будут удалены.`)) return;
    setBusy(true);
    try {
      await api(`/api/admin/orders/${orderId}`, { method: 'DELETE' });
      toast('Заказ удалён', 'ok');
      navigate('/admin/orders');
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Не удалось удалить заказ', 'error');
      setBusy(false);
    }
  };

  const removeMedia = async (media: Media) => {
    if (!window.confirm('Удалить файл?')) return;
    try {
      await api(`/api/admin/media/${media.id}`, { method: 'DELETE' });
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    }
  };

  const upload = async () => {
    if (!uploadFile) return;
    const form = new FormData();
    form.append('kind', uploadKind);
    form.append('file', uploadFile);
    setBusy(true);
    try {
      await api(`/api/admin/orders/${orderId}/media`, { method: 'POST', body: form });
      setUploadFile(null);
      toast('Файл добавлен', 'ok');
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка загрузки', 'error');
    } finally {
      setBusy(false);
    }
  };

  const copyWhatsApp = async () => {
    const orderData = order as OrderFull;
    const text = [
      `Здравствуйте, ${orderData.client.name}!`,
      `По заказу ${orderData.number}: ${statusLabel(orderData.status)}.`,
      orderData.master ? `Мастер: ${orderData.master.name}, ${prettyPhone(orderData.master.phone)}.` : '',
      orderData.eta_minutes ? `Прибытие примерно через ${orderData.eta_minutes} мин.` : '',
      `Стоимость работ: ${money(orderData.works_total)}.`,
      orderData.address ? `Адрес: ${orderData.address}.` : '',
    ].filter(Boolean).join(' ');
    try {
      await navigator.clipboard.writeText(text);
      toast('Текст скопирован — вставьте его в WhatsApp', 'ok');
    } catch {
      window.prompt('Скопируйте текст для WhatsApp:', text);
    }
  };

  if (loading && !order) return <Spinner />;
  if (error && !order) return <div className="form-error">{error}</div>;
  if (!order) return null;

  const closed = ['closed', 'cancelled'].includes(order.status);
  const canAssign = !closed && !order.master_id && ['new', 'reviewing', 'confirmed', 'searching'].includes(order.status);
  const canClose = !closed && !['new', 'reviewing', 'confirmed', 'searching', 'offered'].includes(order.status);
  const masterPage = order.master ? `${window.location.origin}/m/${order.master.page_token}` : '';

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="row" style={{ gap: 10 }}>
            <h1>{order.number}</h1>
            <StatusPill status={order.status} label={statusLabel(order.status)} color={statusColor(order.status)} />
            {order.is_emergency ? <span className="tag tag-red">аварийный</span> : null}
          </div>
          <p>{formatDateTime(order.created_at)} · {SOURCE_LABELS[order.source] || order.source} · {TARIFF_LABELS[order.tariff] || order.tariff}</p>
        </div>
        <div className="row wrap">
          <button className="btn btn-sm btn-outline" onClick={copyWhatsApp}>WhatsApp</button>
          <a className="btn btn-sm btn-outline" href={`/api/admin/orders/${order.id}/pdf`} target="_blank" rel="noreferrer">PDF</a>
          {order.status === 'new' || order.status === 'reviewing' ? (
            <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => setStatus('confirmed')}>Подтвердить</button>
          ) : null}
          {canAssign ? <button className="btn btn-sm btn-primary" onClick={() => setAssignOpen(true)}>Назначить мастера</button> : null}
          {canClose ? <button className="btn btn-sm btn-primary" onClick={() => setCloseOpen(true)}>Закрыть заказ</button> : null}
          <button className="btn btn-sm btn-outline" onClick={() => setEditOpen(true)}>Редактировать</button>
          {!closed ? <button className="btn btn-sm btn-danger" disabled={busy} onClick={cancel}>Отменить</button> : null}
          <button className="btn btn-sm btn-danger" disabled={busy} onClick={removeOrder}>Удалить заказ</button>
        </div>
      </div>

      <div className="order-layout">
        <div>
          <div className="panel">
            <div className="order-head">
              <div>
                <div className="detail-title">Клиент</div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{order.client.name}</div>
                <div className="small muted">{prettyPhone(order.client.phone)}</div>
                {order.client.note ? <div className="tiny muted">{order.client.note}</div> : null}
              </div>
              <div className="right">
                <div className="detail-title">Адрес</div>
                <div style={{ fontSize: 13, fontWeight: 600, maxWidth: 320 }}>{order.address || 'не указан'}</div>
                <div className="tiny muted">
                  {[order.entrance && `подъезд ${order.entrance}`, order.floor && `этаж ${order.floor}`, order.intercom && `домофон ${order.intercom}`].filter(Boolean).join(' · ') || '—'}
                </div>
              </div>
            </div>
            <div className="detail-section">
              <div className="detail-title">Проблема</div>
              <div style={{ fontSize: 15, fontWeight: 650 }}>{order.problem}</div>
              {order.description ? <p className="pre mt-8">{order.description}</p> : <p className="muted small mt-8">Описание не указано</p>}
              <div className="row wrap mt-12">
                <span className="tag tag-gray">{URGENCY_LABELS[order.urgency] || order.urgency}</span>
                {order.desired_label ? <span className="tag tag-gray">{order.desired_label}</span> : null}
                {order.contact_note ? <span className="tag tag-gray">{order.contact_note}</span> : null}
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>Фото и видео</h2>
              <span className="muted small">{order.media.length} файлов</span>
            </div>
            <div className="panel-body">
              {order.media.length ? (
                <div className="media-grid">
                  {order.media.map((media) => (
                    <div key={media.id} className="media-thumb">
                      {media.mime.startsWith('video/') ? (
                        <video src={`/api/admin/media/${media.id}`} controls preload="metadata" />
                      ) : (
                        <a href={`/api/admin/media/${media.id}`} target="_blank" rel="noreferrer">
                          <img src={`/api/admin/media/${media.id}`} alt="" />
                        </a>
                      )}
                      <span className="media-kind">{MEDIA_KINDS[media.kind] || media.kind}</span>
                      <button className="icon-btn" style={{ position: 'absolute', right: 4, bottom: 4, width: 26, height: 26 }} onClick={() => removeMedia(media)} title="Удалить">✕</button>
                    </div>
                  ))}
                </div>
              ) : <p className="muted small">Файлов нет.</p>}
              <div className="row wrap mt-16" style={{ gap: 8 }}>
                <select className="select" style={{ width: 130 }} value={uploadKind} onChange={(e) => setUploadKind(e.target.value)}>
                  {Object.entries(MEDIA_KINDS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                </select>
                <input className="input" type="file" style={{ width: 'auto' }} accept="image/*,video/*" onChange={(e) => setUploadFile(e.target.files?.[0] || null)} />
                <button className="btn btn-sm btn-outline" disabled={!uploadFile || busy} onClick={upload}>Загрузить</button>
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>Работы</h2>
              <span className="muted small">Итого: {money(order.works_total)}</span>
            </div>
            <div className="panel-body flush">
              {order.items.length ? (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Работа</th><th className="right">Цена</th><th className="right">Кол-во</th><th className="right">Сумма</th></tr></thead>
                    <tbody>
                      {order.items.map((item) => (
                        <tr key={item.id} style={{ cursor: 'default' }}>
                          <td>{item.name} {item.is_extra ? <span className="tag tag-amber">доп.</span> : null}</td>
                          <td className="right mono">{money(item.price)}</td>
                          <td className="right">{item.qty}</td>
                          <td className="right mono">{money(item.price * item.qty)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <Empty text="Работы не добавлены" />}
            </div>
            <div className="detail-section">
              <div className="row-between"><span className="muted small">Работы</span><b>{money(order.works_total)}</b></div>
              <div className="row-between mt-8"><span className="muted small">Мастеру</span><b>{money(order.master_earnings)}</b></div>
              <div className="row-between mt-8"><span className="muted small">Компании</span><b>{money(order.company_share)}</b></div>
              {order.materials_note ? <p className="small muted mt-12">Материалы: {order.materials_note}</p> : null}
              {order.guarantee_months ? <p className="small muted mt-8">Гарантия: {order.guarantee_months} мес.</p> : null}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head"><h2>История</h2></div>
            <div className="panel-body">
              <div className="timeline">
                {order.events.length === 0 ? <p className="muted small">Событий нет.</p> : order.events.map((event) => (
                  <div key={event.id} className="timeline-item">
                    <span className="timeline-time">{formatDateTime(event.created_at)}</span>
                    <span>{event.text || event.type}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="order-side">
          <div className="panel">
            <div className="panel-head"><h2>Мастер</h2></div>
            <div className="panel-body">
              {order.master ? (
                <>
                  <div style={{ fontWeight: 700 }}>{order.master.name}</div>
                  <div className="small muted">{prettyPhone(order.master.phone)}</div>
                  <div className="row wrap mt-8">
                    <span className="tag tag-gray">{order.master.status === 'free' ? 'свободен' : order.master.status === 'busy' ? 'занят' : 'не на линии'}</span>
                    {order.master.location_updated_at ? <span className="tag tag-gray">{formatDateTime(order.master.location_updated_at)}</span> : null}
                  </div>
                  <div className="stack mt-12">
                    <button className="btn btn-sm btn-outline" onClick={() => setAssignOpen(true)} disabled={closed}>Переназначить</button>
                    <a className="btn btn-sm btn-outline" href={masterPage} target="_blank" rel="noreferrer">Страница мастера</a>
                  </div>
                </>
              ) : (
                <>
                  <p className="muted small">Мастер не назначен.</p>
                  <button className="btn btn-primary btn-block mt-12" onClick={() => setAssignOpen(true)} disabled={closed}>Назначить</button>
                </>
              )}
              {order.eta_minutes != null ? <p className="small mt-12">Прибытие: ~{order.eta_minutes} мин.</p> : null}
              {order.offer_expires_at && order.status === 'offered' ? <p className="small mt-8" style={{ color: 'var(--amber)' }}>Ответ до {formatDateTime(order.offer_expires_at)}</p> : null}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head"><h2>Статус</h2></div>
            <div className="panel-body stack">
              <StatusPill status={order.status} label={statusLabel(order.status)} color={statusColor(order.status)} />
              <div className="small muted">Создан: {formatDateTime(order.created_at)}</div>
              {order.confirmed_at ? <div className="small muted">Подтверждён: {formatDateTime(order.confirmed_at)}</div> : null}
              {order.accepted_at ? <div className="small muted">Принят мастером: {formatDateTime(order.accepted_at)}</div> : null}
              {order.on_way_at ? <div className="small muted">Выехал: {formatDateTime(order.on_way_at)}</div> : null}
              {order.arrived_at ? <div className="small muted">На месте: {formatDateTime(order.arrived_at)}</div> : null}
              {order.closed_at ? <div className="small muted">Закрыт: {formatDateTime(order.closed_at)}</div> : null}
              <div className="divider" />
              <select className="select" value="" onChange={(e) => e.target.value && setStatus(e.target.value)} disabled={busy || closed}>
                <option value="">Сменить статус…</option>
                {Object.entries({ confirmed: 'Подтверждена', searching: 'Подбор', arrived: 'На месте', in_progress: 'В работе', done_by_master: 'Выполнена мастером', admin_check: 'Проверка', closed: 'Закрыта' }).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
          </div>

          {order.lat != null && order.lng != null ? (
            <div className="panel">
              <div className="panel-head"><h2>На карте</h2></div>
              <MapView
                center={[order.lat, order.lng]}
                zoom={15}
                height={230}
                className="map-card"
                markers={[
                  { id: 'order', lat: order.lat, lng: order.lng, html: '<div class="order-pin">📍</div>', popup: escapeHtml(order.number) },
                  ...(order.master?.current_lat != null && order.master?.current_lng != null
                    ? [{ id: 'master', lat: order.master.current_lat, lng: order.master.current_lng, html: '<div class="master-pin">🔧</div>', popup: escapeHtml(order.master.name) }]
                    : []),                ]}
              />
            </div>
          ) : null}

          <div className="panel">
            <div className="panel-head"><h2>Быстрые ссылки</h2></div>
            <div className="panel-body stack">
              <Link className="btn btn-sm btn-outline" to={`/admin/clients?q=${encodeURIComponent(order.client.phone || order.client.name)}`}>Карточка клиента</Link>
              <Link className="btn btn-sm btn-outline" to={`/admin/orders?q=${encodeURIComponent(order.number)}`}>Все заказы клиента</Link>
              <button className="btn btn-sm btn-outline" onClick={() => navigate('/admin/orders')}>К списку заказов</button>
            </div>
          </div>
        </div>
      </div>

      {assignOpen ? <AssignModal order={order} onClose={() => setAssignOpen(false)} onAssigned={() => { setAssignOpen(false); reload(); }} onPointSaved={() => reload()} /> : null}
      {editOpen ? <EditOrderModal order={order} onClose={() => setEditOpen(false)} onSaved={() => { setEditOpen(false); reload(); }} services={services.data} /> : null}
      {closeOpen ? <CloseOrderModal order={order} onClose={() => setCloseOpen(false)} onClosed={() => { setCloseOpen(false); reload(); }} /> : null}
    </div>
  );
}

function EditOrderModal({
  order,
  onClose,
  onSaved,
  services,
}: {
  order: OrderFull;
  onClose: () => void;
  onSaved: () => void;
  services: { categories: Array<{ id: number; name_ru: string }>; services: Service[] } | null;
}) {
  const toast = useToast();
  const [problem, setProblem] = useState(order.problem);
  const [description, setDescription] = useState(order.description);
  const [address, setAddress] = useState(order.address);
  const [lat, setLat] = useState<number | null>(order.lat);
  const [lng, setLng] = useState<number | null>(order.lng);
  const [entrance, setEntrance] = useState(order.entrance);
  const [floor, setFloor] = useState(order.floor);
  const [intercom, setIntercom] = useState(order.intercom);
  const [contactNote, setContactNote] = useState(order.contact_note);
  const [desiredLabel, setDesiredLabel] = useState(order.desired_label);
  const [urgency, setUrgency] = useState(order.urgency);
  const [tariff, setTariff] = useState(order.tariff);
  const [worksTotal, setWorksTotal] = useState(order.works_total);
  const [materialsNote, setMaterialsNote] = useState(order.materials_note);
  const [guarantee, setGuarantee] = useState(order.guarantee_months ?? 0);
  const [items, setItems] = useState(order.items.map((i) => ({ key: String(i.id), serviceId: i.service_id, name: i.name, price: i.price, qty: i.qty })));
  const [newServiceId, setNewServiceId] = useState('');
  const [saving, setSaving] = useState(false);

  const sum = useMemo(() => items.reduce((acc, item) => acc + item.price * item.qty, 0), [items]);

  const add = () => {
    const id = Number(newServiceId);
    const service = services?.services.find((s) => s.id === id);
    if (!service) return;
    setItems((prev) => [...prev, { key: `${id}-${Date.now()}`, serviceId: id, name: service.name_ru, price: service.price, qty: 1 }]);
    setNewServiceId('');
  };

  const save = async () => {
    setSaving(true);
    try {
      await api(`/api/admin/orders/${order.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          problem,
          description,
          address,
          lat,
          lng,
          entrance,
          floor,
          intercom,
          contact_note: contactNote,
          desired_label: desiredLabel,
          urgency,
          tariff,
          works_total: worksTotal,
          materials_note: materialsNote,
          guarantee_months: guarantee || null,
          items: items.map((i) => ({ serviceId: i.serviceId, name: i.name, price: i.price, qty: i.qty })),
        }),
      });
      toast('Заказ сохранён', 'ok');
      onSaved();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={`Редактировать ${order.number}`} wide onClose={onClose} footer={<><button className="btn btn-outline" onClick={onClose}>Отмена</button><button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? 'Сохраняем…' : 'Сохранить'}</button></>}>
      <div className="form-grid">
        <Field label="Проблема" full><input className="input" value={problem} onChange={(e) => setProblem(e.target.value)} /></Field>
        <Field label="Описание" full><textarea className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label="Адрес" full><input className="input" value={address} onChange={(e) => setAddress(e.target.value)} /></Field>
        <div className="field full">
          <span className="label">Точка на карте</span>
          <MapView
            center={lat != null && lng != null ? [lat, lng] : [52.2873, 76.9674]}
            zoom={lat != null ? 15 : 12}
            height={200}
            follow={lat != null && lng != null ? [lat, lng] : null}
            markers={lat != null && lng != null ? [{ id: 'pin', lat, lng, html: '<div class="order-pin">📍</div>', popup: address || 'Точка вызова' }] : []}
            onMapClick={(nextLat, nextLng) => { setLat(nextLat); setLng(nextLng); }}
          />
          <span className="tiny muted">Кликните по карте, чтобы изменить точку вызова.</span>
        </div>
        <Field label="Подъезд"><input className="input" value={entrance} onChange={(e) => setEntrance(e.target.value)} /></Field>
        <Field label="Этаж"><input className="input" value={floor} onChange={(e) => setFloor(e.target.value)} /></Field>
        <Field label="Домофон"><input className="input" value={intercom} onChange={(e) => setIntercom(e.target.value)} /></Field>
        <Field label="Комментарий"><input className="input" value={contactNote} onChange={(e) => setContactNote(e.target.value)} /></Field>
        <Field label="Желаемое время"><input className="input" value={desiredLabel} onChange={(e) => setDesiredLabel(e.target.value)} /></Field>
        <Field label="Срочность">
          <select className="select" value={urgency} onChange={(e) => setUrgency(e.target.value)}>
            <option value="normal">Обычная</option><option value="urgent">Срочная</option><option value="emergency">Аварийная</option>
          </select>
        </Field>
        <Field label="Тариф">
          <select className="select" value={tariff} onChange={(e) => setTariff(e.target.value)}>
            <option value="day">Дневной</option><option value="night">Ночной ×2</option><option value="emergency_day">Аварийный дневной</option><option value="emergency_night">Аварийный ночной</option>
          </select>
        </Field>
        <Field label="Гарантия, мес."><input className="input" type="number" min={0} value={guarantee} onChange={(e) => setGuarantee(Number(e.target.value))} /></Field>
        <Field label="Материалы (комментарий)"><input className="input" value={materialsNote} onChange={(e) => setMaterialsNote(e.target.value)} /></Field>
      </div>

      <div className="divider" />
      <div className="row-between mb-8">
        <b style={{ fontSize: 13 }}>Работы</b>
        <div className="row">
          <select className="select" style={{ width: 260 }} value={newServiceId} onChange={(e) => setNewServiceId(e.target.value)}>
            <option value="">Добавить услугу…</option>
            {(services?.categories || []).map((cat) => (
              <optgroup key={cat.id} label={cat.name_ru}>
                {(services?.services || []).filter((s) => s.category_id === cat.id).map((s) => <option key={s.id} value={s.id}>{s.name_ru}</option>)}
              </optgroup>
            ))}
          </select>
          <button className="btn btn-sm btn-outline" onClick={add} disabled={!newServiceId}>Добавить</button>
        </div>
      </div>
      {items.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Работа</th><th>Цена</th><th>Кол-во</th><th>Сумма</th><th /></tr></thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.key} style={{ cursor: 'default' }}>
                  <td>{item.name}</td>
                  <td><input className="input" style={{ width: 90, padding: '5px 8px' }} type="number" value={item.price} onChange={(e) => setItems((prev) => prev.map((i) => i.key === item.key ? { ...i, price: Number(e.target.value) } : i))} /></td>
                  <td><input className="input" style={{ width: 56, padding: '5px 8px' }} type="number" value={item.qty} onChange={(e) => setItems((prev) => prev.map((i) => i.key === item.key ? { ...i, qty: Number(e.target.value) } : i))} /></td>
                  <td className="mono">{money(item.price * item.qty)}</td>
                  <td><button className="icon-btn" onClick={() => setItems((prev) => prev.filter((i) => i.key !== item.key))}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="muted small">Работы не добавлены.</p>}
      <div className="row-between mt-12">
        <button className="btn btn-sm btn-outline" onClick={() => setWorksTotal(sum)}>Итого по работам: {money(sum)}</button>
        <div className="row"><span className="muted small">Стоимость работ</span><input className="input" style={{ width: 130 }} type="number" value={worksTotal} onChange={(e) => setWorksTotal(Number(e.target.value))} /></div>
      </div>
    </Modal>
  );
}

function CloseOrderModal({ order, onClose, onClosed }: { order: OrderFull; onClose: () => void; onClosed: () => void }) {
  const toast = useToast();
  const settings = useFetch<{ values: Record<string, string> }>('/api/admin/settings');
  const [worksTotal, setWorksTotal] = useState(order.works_total);
  const [guarantee, setGuarantee] = useState(order.guarantee_months ?? 0);
  const [materials, setMaterials] = useState(order.materials_note);
  const [saving, setSaving] = useState(false);
  const share = Number(settings.data?.values.master_share || 0.7);
  const earnings = Math.round(worksTotal * share);
  const save = async () => {
    setSaving(true);
    try {
      await api(`/api/admin/orders/${order.id}/close`, {
        method: 'POST',
        body: JSON.stringify({ worksTotal, guaranteeMonths: guarantee || null, materialsNote: materials }),
      });
      toast('Заказ закрыт', 'ok');
      onClosed();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal title={`Закрыть ${order.number}`} onClose={onClose} footer={<><button className="btn btn-outline" onClick={onClose}>Отмена</button><button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'Закрыть заказ'}</button></>}>
      <div className="stack">
        <Field label="Стоимость работ, ₸"><input className="input" type="number" min={0} value={worksTotal} onChange={(e) => setWorksTotal(Number(e.target.value))} /></Field>
        <Field label="Гарантия, мес."><input className="input" type="number" min={0} value={guarantee} onChange={(e) => setGuarantee(Number(e.target.value))} /></Field>
        <Field label="Материалы (комментарий)"><input className="input" value={materials} onChange={(e) => setMaterials(e.target.value)} /></Field>
        <div className="panel" style={{ padding: 12 }}>
          <div className="row-between"><span className="muted small">Мастеру ({Math.round(share * 100)}%)</span><b>{money(earnings)}</b></div>
          <div className="row-between mt-8"><span className="muted small">Компании</span><b>{money(worksTotal - earnings)}</b></div>
        </div>
        <p className="tiny muted">После закрытия заказ уходит в архив, мастер освобождается, клиент получает уведомление.</p>
      </div>
    </Modal>
  );
}
