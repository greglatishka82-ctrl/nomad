import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  api,
  formatDateTime,
  money,
  SOURCE_LABELS,
  statusColor,
  statusLabel,
  useDebounced,
  useFetch,
  type Order,
  type OrderFull,
} from '../api';
import { Empty, Spinner, StatusPill, useToast } from '../ui';
import { OrderCreateModal } from './OrderCreateModal';

const TABS = [
  { key: 'all', label: 'Все' },
  { key: 'new,reviewing', label: 'Новые' },
  { key: 'active', label: 'Активные' },
  { key: 'searching,offered', label: 'Подбор' },
  { key: 'on_way', label: 'В пути' },
  { key: 'arrived,in_progress', label: 'На объекте' },
  { key: 'done_by_master,admin_check', label: 'Проверка' },
  { key: 'closed', label: 'Закрытые' },
  { key: 'cancelled', label: 'Отменённые' },
];

export function OrdersPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState(params.get('status') || 'all');
  const [q, setQ] = useState('');
  const debouncedQ = useDebounced(q, 300);
  const [source, setSource] = useState('');
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [showCreate, setShowCreate] = useState(params.get('create') === '1');

  const url = `/api/admin/orders?status=${encodeURIComponent(status)}&q=${encodeURIComponent(debouncedQ)}&source=${encodeURIComponent(source)}&limit=${limit}&offset=${offset}`;
  const { data, loading, error, reload } = useFetch<{ items: Order[]; total: number }>(url, [status, debouncedQ, source, offset]);

  useEffect(() => {
    const handler = () => reload();
    window.addEventListener('kranoff:ws', handler);
    return () => window.removeEventListener('kranoff:ws', handler);
  }, [reload]);

  useEffect(() => {
    setOffset(0);
  }, [status, debouncedQ, source]);

  const total = data?.total || 0;
  const pages = Math.max(1, Math.ceil(total / limit));
  const page = Math.floor(offset / limit) + 1;
  const items = useMemo(() => data?.items || [], [data]);

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === items.length) setSelected(new Set());
    else setSelected(new Set(items.map((o) => o.id)));
  };

  const download = (ids?: number[]) => {
    const url = ids && ids.length ? `/api/admin/archive?ids=${ids.join(',')}` : '/api/admin/archive';
    const link = document.createElement('a');
    link.href = url;
    link.click();
  };

  const deleteMedia = async () => {
    if (!selected.size) return;
    if (!window.confirm(`Удалить все фото и видео у выбранных заказов (${selected.size})? Карточки заказов останутся.`)) return;
    try {
      const result = await api<{ deleted: number }>('/api/admin/orders/media/delete', {
        method: 'POST',
        body: JSON.stringify({ orderIds: [...selected] }),
      });
      toast(`Удалено файлов: ${result.deleted}`, 'ok');
      setSelected(new Set());
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    }
  };

  const removeOrder = async (order: Order) => {
    if (!window.confirm(`Удалить заказ ${order.number} (${order.client_name})? Действие необратимо: заказ, медиа и история будут удалены.`)) return;
    try {
      await api(`/api/admin/orders/${order.id}`, { method: 'DELETE' });
      toast('Заказ удалён', 'ok');
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(order.id);
        return next;
      });
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Не удалось удалить заказ', 'error');
    }
  };

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Заказы</h1>
          <p>Все обращения: Telegram, телефон, WhatsApp и ручное создание</p>
        </div>
        <div className="row">
          <button className="btn btn-outline btn-sm" onClick={() => download()}>Недельный архив</button>
          <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>+ Новый заказ</button>
        </div>
      </div>

      <div className="tabs">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            className={`tab ${status === tab.key ? 'on' : ''}`}
            onClick={() => {
              setStatus(tab.key);
              setParams(tab.key === 'all' ? {} : { status: tab.key });
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="toolbar">
        <input className="input search" placeholder="Номер, клиент, телефон, адрес, проблема…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">Все источники</option>
          <option value="telegram">Telegram</option>
          <option value="phone">Телефон</option>
          <option value="whatsapp">WhatsApp</option>
          <option value="admin">Вручную</option>
        </select>
        <span className="muted small">Найдено: {total}</span>
        {selected.size ? (
          <div className="row" style={{ marginLeft: 'auto', gap: 6 }}>
            <span className="tag">Выбрано: {selected.size}</span>
            <button className="btn btn-sm btn-outline" onClick={() => download([...selected])}>Скачать архив</button>
            <button className="btn btn-sm btn-danger" onClick={deleteMedia}>Удалить медиа</button>
          </div>
        ) : null}
      </div>

      <div className="panel">
        {loading && !data ? (
          <Spinner />
        ) : error ? (
          <div className="form-error" style={{ margin: 16 }}>{error}</div>
        ) : items.length === 0 ? (
          <Empty text="Заказов не найдено" />
        ) : (
          <div className="table-wrap orders-table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 34 }}><input type="checkbox" checked={selected.size > 0 && selected.size === items.length} onChange={toggleAll} /></th>
                  <th>Заказ</th>
                  <th>Создан</th>
                  <th>Клиент</th>
                  <th>Проблема</th>
                  <th>Статус</th>
                  <th>Мастер</th>
                  <th className="right">Сумма</th>
                  <th>Источник</th>
                  <th style={{ width: 46 }}></th>
                </tr>
              </thead>
              <tbody>
                {items.map((order) => (
                  <tr key={order.id} onClick={() => navigate(`/admin/orders/${order.id}`)}>
                    <td onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={selected.has(order.id)} onChange={() => toggle(order.id)} />
                    </td>
                    <td>
                      <b>{order.number}</b>
                      {order.is_emergency ? <span className="tag tag-red" style={{ marginLeft: 6 }}>авария</span> : null}
                    </td>
                    <td className="muted nowrap">{formatDateTime(order.created_at)}</td>
                    <td>
                      <div style={{ fontWeight: 600 }}>{order.client_name}</div>
                      <div className="tiny muted">{order.client_phone || '—'}</div>
                    </td>
                    <td style={{ maxWidth: 280 }}>
                      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{order.problem}</div>
                      <div className="tiny muted">{order.address || 'адрес уточняется'}</div>
                    </td>
                    <td><StatusPill status={order.status} label={statusLabel(order.status)} color={statusColor(order.status)} /></td>
                    <td>{order.master_name || <span className="muted">—</span>}</td>
                    <td className="right mono nowrap">{money(order.works_total)}</td>
                    <td><span className="tag tag-gray">{SOURCE_LABELS[order.source] || order.source}</span></td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button className="icon-btn" title="Удалить заказ" onClick={() => removeOrder(order)}>🗑</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {items.length ? (
          <div className="orders-mobile">
            {items.map((order) => (
              <div key={order.id} className="order-mobile-card" onClick={() => navigate(`/admin/orders/${order.id}`)}>
                <div className="order-mobile-top">
                  <input
                    type="checkbox"
                    checked={selected.has(order.id)}
                    onClick={(event) => event.stopPropagation()}
                    onChange={() => toggle(order.id)}
                  />
                  <b>{order.number}</b>
                  {order.is_emergency ? <span className="tag tag-red">авария</span> : null}
                  <StatusPill status={order.status} label={statusLabel(order.status)} color={statusColor(order.status)} />
                </div>
                <div className="order-mobile-client">{order.client_name} · {order.client_phone || '—'}</div>
                <div className="order-mobile-problem">{order.problem}</div>
                <div className="order-mobile-meta">
                  <span>{formatDateTime(order.created_at)}</span>
                  <b>{money(order.works_total)}</b>
                </div>
                <div className="order-mobile-foot">
                  <span>{order.master_name || 'без мастера'} · {SOURCE_LABELS[order.source] || order.source}</span>
                  <button
                    className="icon-btn"
                    title="Удалить заказ"
                    onClick={(event) => { event.stopPropagation(); void removeOrder(order); }}
                  >🗑</button>
                </div>
              </div>
            ))}
          </div>
        ) : null}
        <div className="row-between" style={{ padding: '10px 14px', borderTop: '1px solid var(--line)' }}>
          <span className="muted small">Страница {page} из {pages}</span>
          <div className="row" style={{ gap: 6 }}>
            <button className="btn btn-sm btn-outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>← Назад</button>
            <button className="btn btn-sm btn-outline" disabled={page >= pages} onClick={() => setOffset(offset + limit)}>Вперёд →</button>
          </div>
        </div>
      </div>

      {showCreate ? (
        <OrderCreateModal
          onClose={() => {
            setShowCreate(false);
            const next = new URLSearchParams(params);
            next.delete('create');
            setParams(next);
          }}
          onCreated={(order: OrderFull) => {
            setShowCreate(false);
            navigate(`/admin/orders/${order.id}`);
          }}
        />
      ) : null}
    </div>
  );
}
