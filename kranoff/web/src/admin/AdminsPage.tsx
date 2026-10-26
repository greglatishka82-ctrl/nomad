import { useEffect, useState } from 'react';
import { api, formatDateTime, formatTime, money, useFetch, type AdminUser } from '../api';
import { Field, Modal, Spinner, useToast } from '../ui';
import type { Me } from './AdminApp';

type AdminRow = AdminUser & { shift_id: number | null; shift_started_at: string | null; orders_created: number; created_at: string };
type ShiftRow = { id: number; admin_id: number; admin_name: string; started_at: string; ended_at: string | null; minutes: number; orders: number; revenue: number };

export function AdminsPage() {
  const toast = useToast();
  const admins = useFetch<{ admins: AdminRow[]; onShift: Array<{ id: number; name: string }> }>('/api/admin/admins');
  const shifts = useFetch<{ shifts: ShiftRow[] }>('/api/admin/shifts');
  const me = useFetch<Me>('/api/auth/me');
  const [addOpen, setAddOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const handler = () => { admins.reload(); shifts.reload(); };
    window.addEventListener('kranoff:ws', handler);
    return () => window.removeEventListener('kranoff:ws', handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const shiftAction = async (action: 'start' | 'end') => {
    setBusy(true);
    try {
      await api(`/api/auth/shift/${action}`, { method: 'POST' });
      toast(action === 'start' ? 'Смена начата' : 'Смена завершена', 'ok');
      window.dispatchEvent(new CustomEvent('kranoff:auth'));
      me.reload();
      admins.reload();
      shifts.reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (row: AdminRow) => {
    try {
      await api(`/api/admin/admins/${row.id}`, { method: 'PUT', body: JSON.stringify({ is_active: !row.is_active }) });
      admins.reload();
      toast('Доступ обновлён', 'ok');
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    }
  };

  const resetPassword = async (row: AdminRow) => {
    const password = window.prompt(`Новый пароль для ${row.login} (минимум 6 символов):`);
    if (!password) return;
    try {
      await api(`/api/admin/admins/${row.id}`, { method: 'PUT', body: JSON.stringify({ password }) });
      toast('Пароль изменён', 'ok');
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    }
  };

  if (admins.loading && !admins.data) return <Spinner />;

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Смены и администраторы</h1>
          <p>Все администраторы равны по доступам. Смена фиксирует часы и заказы.</p>
        </div>
        <button className="btn btn-primary btn-sm" onClick={() => setAddOpen(true)}>+ Добавить администратора</button>
      </div>

      <div className="cards cards-3">
        <div className="stat accent">
          <div className="stat-label">Моя смена</div>
          <div className="stat-value" style={{ fontSize: 20 }}>{me.data?.shift ? `с ${formatTime(me.data.shift.started_at)}` : 'не начата'}</div>
          <div className="stat-foot">
            {me.data?.shift ? (
              <button className="btn btn-sm" style={{ background: 'rgba(255,255,255,.2)', color: '#fff' }} onClick={() => shiftAction('end')} disabled={busy}>Завершить</button>
            ) : (
              <button className="btn btn-sm" style={{ background: '#fff', color: 'var(--blue)' }} onClick={() => shiftAction('start')} disabled={busy}>Начать смену</button>
            )}
          </div>
        </div>
        <div className="stat"><div className="stat-label">На смене сейчас</div><div className="stat-value">{admins.data?.onShift?.length || 0}</div><div className="stat-foot">{admins.data?.onShift?.map((a) => a.name).join(', ') || '—'}</div></div>
        <div className="stat"><div className="stat-label">Всего администраторов</div><div className="stat-value">{admins.data?.admins?.length || 0}</div><div className="stat-foot">активных: {admins.data?.admins.filter((a) => a.is_active).length || 0}</div></div>
      </div>

      <div className="panel">
        <div className="panel-head"><h2>Администраторы</h2></div>
        <div className="table-wrap">
          <table className="admins-table">
            <thead><tr><th>Имя</th><th>Логин</th><th>Смена</th><th>Создано заказов</th><th>Доступ</th><th /></tr></thead>
            <tbody>
              {(admins.data?.admins || []).map((row) => (
                <tr key={row.id} style={{ cursor: 'default' }}>
                  <td><b>{row.name}</b>{me.data?.admin.id === row.id ? <span className="tag tag-gray" style={{ marginLeft: 6 }}>это вы</span> : null}</td>
                  <td>{row.login}</td>
                  <td>{row.shift_started_at ? `с ${formatTime(row.shift_started_at)}` : <span className="muted">—</span>}</td>
                  <td>{row.orders_created}</td>
                  <td>{row.is_active ? <span className="tag tag-green">активен</span> : <span className="tag tag-red">отключён</span>}</td>
                  <td className="right">
                    <div className="row" style={{ justifyContent: 'flex-end', gap: 4 }}>
                      <button className="btn btn-sm btn-outline" onClick={() => resetPassword(row)}>Пароль</button>
                      <button className="btn btn-sm btn-outline" onClick={() => toggleActive(row)}>{row.is_active ? 'Отключить' : 'Включить'}</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head"><h2>История смен</h2></div>
        <div className="table-wrap">
          <table className="shifts-table">
            <thead><tr><th>Админ</th><th>Начало</th><th>Конец</th><th>Часы</th><th>Заказы</th><th className="right">Сумма</th></tr></thead>
            <tbody>
              {shifts.loading && !shifts.data ? <tr><td colSpan={6}><Spinner /></td></tr> : (shifts.data?.shifts || []).map((shift) => (
                <tr key={shift.id} style={{ cursor: 'default' }}>
                  <td><b>{shift.admin_name}</b></td>
                  <td>{formatDateTime(shift.started_at)}</td>
                  <td>{shift.ended_at ? formatDateTime(shift.ended_at) : <span className="tag tag-green">идёт</span>}</td>
                  <td>{(shift.minutes / 60).toFixed(1)} ч</td>
                  <td>{shift.orders}</td>
                  <td className="right mono">{money(shift.revenue)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {addOpen ? <AddAdminModal onClose={() => setAddOpen(false)} onSaved={() => { setAddOpen(false); admins.reload(); }} /> : null}
    </div>
  );
}

function AddAdminModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await api('/api/admin/admins', { method: 'POST', body: JSON.stringify({ login, password, name }) });
      toast('Администратор добавлен', 'ok');
      onSaved();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="Новый администратор" narrow onClose={onClose} footer={<><button className="btn btn-outline" onClick={onClose}>Отмена</button><button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'Добавить'}</button></>}>
      <div className="stack">
        <Field label="Имя"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Например, Айгуль" /></Field>
        <Field label="Логин"><input className="input" value={login} onChange={(e) => setLogin(e.target.value)} placeholder="aigul" /></Field>
        <Field label="Пароль"><input className="input" type="text" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="минимум 6 символов" /></Field>
        <p className="tiny muted">Все администраторы имеют одинаковый доступ: заказы, клиенты, мастера, цены, настройки.</p>
      </div>
    </Modal>
  );
}
