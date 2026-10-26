import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, formatDateTime, money, prettyPhone, statusColor, statusLabel, useDebounced, useFetch, type Client, type Order } from '../api';
import { Empty, Field, Modal, Spinner, StatusPill, useToast } from '../ui';

export function ClientsPage() {
  const [params] = useSearchParams();
  const [q, setQ] = useState(params.get('q') || '');
  const debounced = useDebounced(q, 300);
  const { data, loading, error, reload } = useFetch<{ clients: Client[] }>(`/api/admin/clients?q=${encodeURIComponent(debounced)}`, [debounced]);
  const [selected, setSelected] = useState<Client | null>(null);

  useEffect(() => {
    const handler = () => reload();
    window.addEventListener('kranoff:ws', handler);
    return () => window.removeEventListener('kranoff:ws', handler);
  }, [reload]);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Клиенты</h1>
          <p>Поиск по номеру телефона, имя и история заказов</p>
        </div>
      </div>
      <div className="toolbar">
        <input className="input search" placeholder="Телефон, имя или адрес…" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="muted small">Найдено: {data?.clients?.length || 0}</span>
      </div>
      <div className="panel">
        {loading && !data ? <Spinner /> : error ? <div className="form-error" style={{ margin: 16 }}>{error}</div> : !data?.clients.length ? <Empty text="Клиенты не найдены" /> : (
          <div className="table-wrap clients-table-wrap">
            <table>
              <thead><tr><th>Клиент</th><th>Телефон</th><th>Адрес</th><th>Заказов</th><th className="right">Сумма</th><th>Последний</th><th /></tr></thead>
              <tbody>
                {data.clients.map((client) => (
                  <tr key={client.id} onClick={() => setSelected(client)}>
                    <td><b>{client.name}</b>{client.telegram_id ? <span className="tag tag-gray" style={{ marginLeft: 6 }}>TG</span> : null}</td>
                    <td>{client.phone_pretty || prettyPhone(client.phone)}</td>
                    <td style={{ maxWidth: 260 }}><div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{client.address || '—'}</div></td>
                    <td>{client.orders_count ?? 0}</td>
                    <td className="right mono">{money(client.total_spent || 0)}</td>
                    <td className="muted nowrap">{formatDateTime(client.last_order_at)}</td>
                    <td className="right"><span className="link small">Открыть</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {data?.clients.length ? (
        <div className="clients-mobile">
          {data.clients.map((client) => (
            <div key={client.id} className="client-mobile-card" onClick={() => setSelected(client)}>
              <div className="client-mobile-top">
                <b>{client.name}{client.telegram_id ? <span className="tag tag-gray" style={{ marginLeft: 6 }}>TG</span> : null}</b>
                <b>{money(client.total_spent || 0)}</b>
              </div>
              <div className="client-mobile-line">{client.phone_pretty || prettyPhone(client.phone)}</div>
              <div className="client-mobile-line">{client.address || 'адрес не указан'}</div>
              <div className="client-mobile-meta">
                <span>Заказов: {client.orders_count ?? 0}</span>
                <span>{client.last_order_at ? formatDateTime(client.last_order_at) : 'заказов не было'}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {selected ? <ClientModal client={selected} onClose={() => setSelected(null)} onSaved={() => { setSelected(null); reload(); }} /> : null}
    </div>
  );
}

function ClientModal({ client, onClose, onSaved }: { client: Client; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const { data, loading } = useFetch<{ client: Client; orders: Order[] }>(`/api/admin/clients/${client.id}`);
  const [name, setName] = useState(client.name);
  const [phone, setPhone] = useState(client.phone || '');
  const [address, setAddress] = useState(client.address);
  const [note, setNote] = useState(client.note);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await api(`/api/admin/clients/${client.id}`, { method: 'PUT', body: JSON.stringify({ name, phone, address, note }) });
      toast('Клиент сохранён', 'ok');
      onSaved();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="Карточка клиента" wide onClose={onClose} footer={<><button className="btn btn-outline" onClick={onClose}>Закрыть</button><button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'Сохранить'}</button></>}>
      <div className="grid-2" style={{ gap: 20 }}>
        <div className="stack">
          <Field label="Имя"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Телефон"><input className="input" value={phone} onChange={(e) => setPhone(e.target.value)} /></Field>
          <Field label="Основной адрес"><input className="input" value={address} onChange={(e) => setAddress(e.target.value)} /></Field>
          <Field label="Заметка"><textarea className="textarea" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          <div className="small muted">Telegram: {client.telegram_id ? `ID ${client.telegram_id}` : 'не привязан'}</div>
          <Link className="btn btn-sm btn-outline" to={`/admin/orders?q=${encodeURIComponent(client.phone || client.name)}`}>Все заказы</Link>
        </div>
        <div>
          <div className="detail-title">История заказов</div>
          {loading ? <Spinner /> : !data?.orders?.length ? <Empty text="Заказов нет" /> : (
            <div className="stack">
              {data.orders.slice(0, 12).map((order) => (
                <Link key={order.id} to={`/admin/orders/${order.id}`} className="panel" style={{ padding: 11, display: 'block' }}>
                  <div className="row-between">
                    <b style={{ fontSize: 12.5 }}>{order.number}</b>
                    <StatusPill status={order.status} label={statusLabel(order.status)} color={statusColor(order.status)} />
                  </div>
                  <div className="tiny muted mt-8">{formatDateTime(order.created_at)} · {order.problem}</div>
                  <div className="row-between mt-8"><span className="tiny muted">{order.master_name || 'без мастера'}</span><b className="small">{money(order.works_total)}</b></div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
