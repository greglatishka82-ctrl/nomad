import { useEffect, useMemo, useState } from 'react';
import { api, money, useDebounced, useFetch, type Client, type OrderFull, type Service } from '../api';
import { Field, Modal, useToast } from '../ui';
import { MapView } from '../components/MapView';

type ServicesResponse = { categories: Array<{ id: number; name_ru: string }>; services: Service[] };
type GeoResult = { display_name: string; lat: number; lon: number };

function pavlodarHour(): number {
  return Number(new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Almaty', hour: '2-digit', hour12: false }).format(new Date()));
}

function priceFor(service: Service | undefined, tariff: string): number {
  if (!service) return 0;
  if (tariff === 'emergency_night') return service.price_night ?? Math.round(service.price * 2);
  if (tariff === 'emergency_day') return service.price;
  if (tariff === 'night') return Math.round(service.price * 2);
  return service.price;
}

type Item = { key: string; serviceId: number | null; name: string; price: number; qty: number };

export function OrderCreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (order: OrderFull) => void;
}) {
  const toast = useToast();
  const services = useFetch<ServicesResponse>('/api/admin/services');
  const hour = pavlodarHour();
  const night = hour >= 22 || hour < 8;
  const [clientQuery, setClientQuery] = useState('');
  const debouncedQuery = useDebounced(clientQuery, 300);
  const clients = useFetch<{ clients: Client[] }>(debouncedQuery.trim().length >= 2 ? `/api/admin/clients?q=${encodeURIComponent(debouncedQuery.trim())}` : null, [debouncedQuery]);
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [problem, setProblem] = useState('');
  const [description, setDescription] = useState('');
  const [address, setAddress] = useState('');
  const [lat, setLat] = useState<number | null>(null);
  const [lng, setLng] = useState<number | null>(null);
  const [entrance, setEntrance] = useState('');
  const [floor, setFloor] = useState('');
  const [intercom, setIntercom] = useState('');
  const [contactNote, setContactNote] = useState('');
  const [desiredLabel, setDesiredLabel] = useState('');
  const [urgency, setUrgency] = useState<'normal' | 'urgent' | 'emergency'>('normal');
  const [isEmergency, setIsEmergency] = useState(false);
  const [tariff, setTariff] = useState(night ? 'night' : 'day');
  const [source, setSource] = useState<'phone' | 'whatsapp' | 'admin'>('phone');
  const [materialsNote, setMaterialsNote] = useState('');
  const [items, setItems] = useState<Item[]>([]);
  const [newServiceId, setNewServiceId] = useState('');
  const [geoQuery, setGeoQuery] = useState('');
  const [geoResults, setGeoResults] = useState<GeoResult[]>([]);
  const [geoLoading, setGeoLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const allServices = useMemo(() => services.data?.services || [], [services.data]);
  const grouped = useMemo(
    () => (services.data?.categories || []).map((category) => ({ ...category, services: allServices.filter((service) => service.category_id === category.id) })),
    [services.data, allServices],
  );
  const total = items.reduce((sum, item) => sum + item.price * item.qty, 0);

  useEffect(() => {
    if (isEmergency) {
      setUrgency('emergency');
      setTariff((prev) => (prev.startsWith('emergency') ? prev : night ? 'emergency_night' : 'emergency_day'));
    } else {
      setTariff((prev) => (prev.startsWith('emergency') ? (night ? 'night' : 'day') : prev));
      setUrgency((prev) => (prev === 'emergency' ? 'normal' : prev));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEmergency]);

  useEffect(() => {
    setItems((prev) => prev.map((item) => {
      const service = allServices.find((s) => s.id === item.serviceId);
      return service ? { ...item, price: priceFor(service, tariff) } : item;
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tariff]);

  const addService = () => {
    const id = Number(newServiceId);
    const service = allServices.find((s) => s.id === id);
    if (!service) return;
    const itemName = service.name_ru;
    setItems((prev) => [...prev, { key: `${id}-${Date.now()}`, serviceId: id, name: itemName, price: priceFor(service, tariff), qty: 1 }]);
    if (!problem) setProblem(itemName);
    if (!isEmergency && service.is_emergency) setIsEmergency(true);
    setNewServiceId('');
  };

  const geocode = async () => {
    if (geoQuery.trim().length < 3) return;
    setGeoLoading(true);
    try {
      const result = await api<{ results: GeoResult[] }>(`/api/admin/geo?q=${encodeURIComponent(geoQuery.trim())}`);
      setGeoResults(result.results || []);
      if (!result.results?.length) toast('Адрес не найден. Укажите точку вручную в карточке заказа.', 'error');
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка поиска адреса', 'error');
    } finally {
      setGeoLoading(false);
    }
  };

  const submit = async () => {
    if (!problem.trim()) return toast('Укажите проблему', 'error');
    if (!selectedClient && (!name.trim() || !phone.trim())) return toast('Укажите имя и телефон клиента', 'error');
    if (!items.length) return toast('Добавьте хотя бы одну услугу', 'error');
    setSaving(true);
    try {
      const order = await api<OrderFull>('/api/admin/orders', {
        method: 'POST',
        body: JSON.stringify({
          clientId: selectedClient?.id,
          client: selectedClient ? undefined : { name: name.trim(), phone: phone.trim(), address },
          problem: problem.trim(),
          description,
          address,
          lat,
          lng,
          entrance,
          floor,
          intercom,
          contactNote,
          desiredLabel,
          urgency,
          isEmergency,
          tariff,
          source,
          materialsNote,
          items: items.map((item) => ({ serviceId: item.serviceId, name: item.name, price: item.price, qty: item.qty })),
        }),
      });
      toast(`Заказ ${order.number} создан`, 'ok');
      onCreated(order);
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Не удалось создать заказ', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="Новый заказ"
      wide
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-outline" onClick={onClose}>Отмена</button>
          <button className="btn btn-primary" onClick={submit} disabled={saving}>{saving ? 'Сохраняем…' : `Создать заказ · ${money(total)}`}</button>
        </>
      }
    >
      <div className="grid-2" style={{ gap: 22 }}>
        <div className="stack">
          <div className="detail-title" style={{ margin: 0 }}>Клиент</div>
          {selectedClient ? (
            <div className="panel" style={{ padding: 12 }}>
              <div className="row-between">
                <div>
                  <b>{selectedClient.name}</b>
                  <div className="small muted">{selectedClient.phone_pretty || selectedClient.phone || 'без телефона'}</div>
                  {selectedClient.address ? <div className="tiny muted">{selectedClient.address}</div> : null}
                </div>
                <button className="btn btn-sm btn-outline" onClick={() => setSelectedClient(null)}>Сменить</button>
              </div>
            </div>
          ) : (
            <>
              <Field label="Найти клиента по телефону или имени">
                <input className="input" value={clientQuery} onChange={(e) => setClientQuery(e.target.value)} placeholder="Начните вводить…" />
              </Field>
              {clients.data?.clients?.length ? (
                <div className="panel" style={{ maxHeight: 160, overflowY: 'auto' }}>
                  {clients.data.clients.slice(0, 8).map((client) => (
                    <div key={client.id} className="dialog" onClick={() => { setSelectedClient(client); setClientQuery(''); }}>
                      <div className="dialog-top"><b>{client.name}</b><span className="muted small">{client.phone_pretty || client.phone}</span></div>
                      <div className="dialog-preview">{client.address || 'адрес не указан'}</div>
                    </div>
                  ))}
                </div>
              ) : null}
              <div className="form-grid">
                <Field label="Имя"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Имя клиента" /></Field>
                <Field label="Телефон"><input className="input" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+7 777 123-45-67" /></Field>
              </div>
              <p className="tiny muted">Если клиент уже есть в базе — найдите его, чтобы не дублировать карточку.</p>
            </>
          )}

          <div className="detail-title" style={{ margin: '6px 0 0' }}>Адрес и доступ</div>
          <Field label="Адрес">
            <input className="input" value={address} onChange={(e) => { setAddress(e.target.value); setGeoQuery(e.target.value); }} placeholder="Улица, дом, квартира" />
          </Field>
          <div className="row" style={{ gap: 8 }}>
            <input className="input" value={geoQuery} onChange={(e) => setGeoQuery(e.target.value)} placeholder="Поиск адреса на карте" />
            <button className="btn btn-outline btn-sm" onClick={geocode} disabled={geoLoading}>{geoLoading ? '…' : 'Найти'}</button>
          </div>
          {lat != null && lng != null ? <div className="tiny" style={{ color: 'var(--green)' }}>Координаты: {lat.toFixed(5)}, {lng.toFixed(5)}</div> : null}
          {geoResults.length ? (
            <div className="panel" style={{ maxHeight: 150, overflowY: 'auto' }}>
              {geoResults.map((result, index) => (
                <div key={index} className="dialog" onClick={() => { setAddress(result.display_name); setLat(result.lat); setLng(result.lon); setGeoResults([]); }}>
                  <div className="dialog-preview" style={{ whiteSpace: 'normal' }}>{result.display_name}</div>
                </div>
              ))}
            </div>
          ) : null}
          <div>
            <MapView
              center={lat != null && lng != null ? [lat, lng] : [52.2873, 76.9674]}
              zoom={lat != null ? 15 : 12}
              height={220}
              follow={lat != null && lng != null ? [lat, lng] : null}
              markers={lat != null && lng != null ? [{ id: 'pin', lat, lng, html: '<div class="order-pin">📍</div>', popup: address || 'Точка вызова' }] : []}
              onMapClick={(nextLat, nextLng) => { setLat(nextLat); setLng(nextLng); }}
            />
            <p className="tiny muted mt-8">Кликните по карте, чтобы поставить точку вызова. Если адрес не находится — просто укажите точку вручную.</p>
          </div>
          <div className="form-grid">
            <Field label="Подъезд"><input className="input" value={entrance} onChange={(e) => setEntrance(e.target.value)} /></Field>
            <Field label="Этаж"><input className="input" value={floor} onChange={(e) => setFloor(e.target.value)} /></Field>
            <Field label="Домофон"><input className="input" value={intercom} onChange={(e) => setIntercom(e.target.value)} /></Field>
            <Field label="Желаемое время"><input className="input" value={desiredLabel} onChange={(e) => setDesiredLabel(e.target.value)} placeholder="Сейчас / сегодня после 18:00" /></Field>
          </div>
          <Field label="Комментарий по доступу"><input className="input" value={contactNote} onChange={(e) => setContactNote(e.target.value)} placeholder="Кто встречает, код, особенности" /></Field>
        </div>

        <div className="stack">
          <div className="detail-title" style={{ margin: 0 }}>Заявка</div>
          <Field label="Проблема / услуга">
            <input className="input" value={problem} onChange={(e) => setProblem(e.target.value)} placeholder="Что случилось" />
          </Field>
          <Field label="Описание">
            <textarea className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Детали от клиента" />
          </Field>

          <div className="form-grid">
            <Field label="Источник">
              <select className="select" value={source} onChange={(e) => setSource(e.target.value as 'phone' | 'whatsapp' | 'admin')}>
                <option value="phone">Телефон</option>
                <option value="whatsapp">WhatsApp</option>
                <option value="admin">Вручную</option>
              </select>
            </Field>
            <Field label="Тариф">
              <select className="select" value={tariff} onChange={(e) => setTariff(e.target.value)}>
                <option value="day">Дневной</option>
                <option value="night">Ночной ×2</option>
                <option value="emergency_day">Аварийный дневной</option>
                <option value="emergency_night">Аварийный ночной</option>
              </select>
            </Field>
            <Field label="Срочность">
              <select className="select" value={urgency} onChange={(e) => setUrgency(e.target.value as 'normal' | 'urgent' | 'emergency')}>
                <option value="normal">Обычная</option>
                <option value="urgent">Срочная</option>
                <option value="emergency">Аварийная</option>
              </select>
            </Field>
            <label className="checkbox" style={{ alignSelf: 'end', paddingBottom: 9 }}>
              <input type="checkbox" checked={isEmergency} onChange={(e) => setIsEmergency(e.target.checked)} />
              Аварийный вызов
            </label>
          </div>

          <div className="detail-title" style={{ margin: '6px 0 0' }}>Работы</div>
          <div className="row" style={{ gap: 8 }}>
            <select className="select" value={newServiceId} onChange={(e) => setNewServiceId(e.target.value)}>
              <option value="">Выберите услугу…</option>
              {(grouped || []).map((cat) => (
                <optgroup key={cat.id} label={cat.name_ru}>
                  {cat.services.map((service) => (
                    <option key={service.id} value={service.id}>{service.name_ru} · от {money(service.price)}</option>
                  ))}
                </optgroup>
              ))}
            </select>
            <button className="btn btn-outline btn-sm" onClick={addService} disabled={!newServiceId}>Добавить</button>
          </div>
          {items.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Работа</th><th>Цена</th><th>Кол-во</th><th>Сумма</th><th /></tr></thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.key}>
                      <td>{item.name}</td>
                      <td><input className="input" style={{ width: 92, padding: '5px 8px' }} type="number" min={0} value={item.price} onChange={(e) => setItems((prev) => prev.map((i) => i.key === item.key ? { ...i, price: Number(e.target.value) } : i))} /></td>
                      <td><input className="input" style={{ width: 58, padding: '5px 8px' }} type="number" min={1} value={item.qty} onChange={(e) => setItems((prev) => prev.map((i) => i.key === item.key ? { ...i, qty: Number(e.target.value) } : i))} /></td>
                      <td className="mono nowrap">{money(item.price * item.qty)}</td>
                      <td><button className="icon-btn" onClick={() => setItems((prev) => prev.filter((i) => i.key !== item.key))}>✕</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="muted small">Добавьте работы из прайса. Итог можно изменить в карточке заказа.</p>}
          <div className="row-between" style={{ paddingTop: 8, borderTop: '1px solid var(--line)' }}>
            <span className="muted small">Итого по работам</span>
            <b style={{ fontSize: 18 }}>{money(total)}</b>
          </div>
          <Field label="Материалы (комментарий)">
            <input className="input" value={materialsNote} onChange={(e) => setMaterialsNote(e.target.value)} placeholder="Кто покупает, что нужно" />
          </Field>
        </div>
      </div>
    </Modal>
  );
}
