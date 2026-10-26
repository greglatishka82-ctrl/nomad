import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, formatDateTime, money, prettyPhone, useFetch, type Master } from '../api';
import { Empty, Field, Modal, Spinner, useToast } from '../ui';

const STATUS: Record<string, { label: string; color: string }> = {
  free: { label: 'Свободен', color: '#15803d' },
  busy: { label: 'Занят', color: '#b45309' },
  offline: { label: 'Не на линии', color: '#64748b' },
};

export function MastersPage() {
  const { data, loading, error, reload } = useFetch<{ masters: Master[]; skills: Array<{ key: string; name_ru: string }> }>('/api/admin/masters');
  const [editing, setEditing] = useState<Master | 'new' | null>(null);
  const [params, setParams] = useSearchParams();
  const toast = useToast();

  useEffect(() => {
    const id = Number(params.get('master'));
    if (!id || !data?.masters.length) return;
    const found = data.masters.find((master) => master.id === id);
    if (found) setEditing(found);
  }, [params, data]);

  const closeEditor = () => {
    setEditing(null);
    if (params.has('master')) {
      const next = new URLSearchParams(params);
      next.delete('master');
      setParams(next, { replace: true });
    }
  };

  useEffect(() => {
    const handler = () => reload();
    window.addEventListener('kranoff:ws', handler);
    return () => window.removeEventListener('kranoff:ws', handler);
  }, [reload]);

  const setStatus = async (master: Master, status: 'free' | 'busy' | 'offline') => {
    try {
      await api(`/api/admin/masters/${master.id}/status`, { method: 'POST', body: JSON.stringify({ status }) });
      toast('Статус обновлён', 'ok');
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    }
  };

  const copyLink = async (master: Master) => {
    const url = master.page_url || `${window.location.origin}/m/${master.page_token}`;
    try {
      await navigator.clipboard.writeText(url);
      toast('Ссылка на страницу мастера скопирована', 'ok');
    } catch {
      window.prompt('Ссылка на страницу мастера:', url);
    }
  };

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Мастера</h1>
          <p>Навыки, инструменты, доступность и заработок. Мастер привязывает Telegram по номеру телефона.</p>
        </div>
        <button className="btn btn-primary btn-sm" onClick={() => setEditing('new')}>+ Добавить мастера</button>
      </div>

      <div className="panel">
        {loading && !data ? <Spinner /> : error ? <div className="form-error" style={{ margin: 16 }}>{error}</div> : !data?.masters.length ? <Empty text="Мастеров пока нет. Добавьте первого." /> : (
          <div className="table-wrap masters-table-wrap">
            <table>
              <thead><tr><th>Мастер</th><th>Статус</th><th>Навыки</th><th>Telegram</th><th>Заказы</th><th className="right">Заработал</th><th /></tr></thead>
              <tbody>
                {data.masters.map((master) => {
                  const status = STATUS[master.status] || STATUS.offline;
                  return (
                    <tr key={master.id} style={{ cursor: 'default' }}>
                      <td>
                        <b>{master.name}</b>
                        <div className="tiny muted">{prettyPhone(master.phone)}{master.active ? '' : ' · отключён'}</div>
                      </td>
                      <td>
                        <span className="pill" style={{ color: status.color, background: `${status.color}14` }}><span className="pill-dot" />{status.label}</span>
                        <div className="row wrap" style={{ gap: 4, marginTop: 6 }}>
                          <button className="btn btn-sm btn-ghost" onClick={() => setStatus(master, 'free')}>свободен</button>
                          <button className="btn btn-sm btn-ghost" onClick={() => setStatus(master, 'busy')}>занят</button>
                          <button className="btn btn-sm btn-ghost" onClick={() => setStatus(master, 'offline')}>offline</button>
                        </div>
                      </td>
                      <td style={{ maxWidth: 280 }}>
                        <div className="row wrap" style={{ gap: 4 }}>
                          {(master.skills_list || []).slice(0, 6).map((skill) => <span key={skill} className="tag tag-gray">{skill}</span>)}
                          {(master.skills_list || []).length > 6 ? <span className="tag tag-gray">+{(master.skills_list || []).length - 6}</span> : null}
                        </div>
                      </td>
                      <td>{master.telegram_id ? <span className="tag tag-green">привязан</span> : <span className="tag tag-amber">нет</span>}</td>
                      <td>
                        <div className="small">активных: {master.active_orders ?? 0}</div>
                        <div className="tiny muted">закрытых: {master.closed_orders ?? 0}</div>
                      </td>
                      <td className="right">
                        <div className="mono">{money(master.earnings?.shift || 0)}</div>
                        <div className="tiny muted">за смену</div>
                      </td>
                      <td className="right" style={{ minWidth: 200 }}>
                        <div className="row" style={{ justifyContent: 'flex-end', gap: 4 }}>
                          <button className="btn btn-sm btn-outline" onClick={() => copyLink(master)}>Ссылка</button>
                          <button className="btn btn-sm btn-outline" onClick={() => setEditing(master)}>Изменить</button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {data?.masters.length ? (
        <div className="masters-mobile">
          {data.masters.map((master) => {
            const status = STATUS[master.status] || STATUS.offline;
            return (
              <div key={master.id} className="master-mobile-card">
                <div className="master-mobile-top">
                  <div>
                    <b>{master.name}</b>
                    <div className="tiny muted">{prettyPhone(master.phone)}{master.active ? '' : ' · отключён'}</div>
                  </div>
                  <span className="pill" style={{ color: status.color, background: `${status.color}14` }}><span className="pill-dot" />{status.label}</span>
                </div>
                <div className="row wrap" style={{ gap: 4, marginTop: 8 }}>
                  {(master.skills_list || []).slice(0, 6).map((skill) => <span key={skill} className="tag tag-gray">{skill}</span>)}
                  {(master.skills_list || []).length > 6 ? <span className="tag tag-gray">+{(master.skills_list || []).length - 6}</span> : null}
                </div>
                <div className="master-mobile-stats">
                  <span>Активных: <b>{master.active_orders ?? 0}</b></span>
                  <span>Закрытых: <b>{master.closed_orders ?? 0}</b></span>
                  <span>За смену: <b>{money(master.earnings?.shift || 0)}</b></span>
                </div>
                <div className="row wrap" style={{ gap: 4, marginTop: 8 }}>
                  <span className="tiny muted" style={{ marginRight: 'auto' }}>{master.telegram_id ? 'Telegram привязан' : 'Telegram не привязан'}</span>
                  <button className="btn btn-sm btn-ghost" onClick={() => setStatus(master, 'free')}>свободен</button>
                  <button className="btn btn-sm btn-ghost" onClick={() => setStatus(master, 'busy')}>занят</button>
                  <button className="btn btn-sm btn-ghost" onClick={() => setStatus(master, 'offline')}>offline</button>
                </div>
                <div className="row" style={{ gap: 6, marginTop: 8 }}>
                  <button className="btn btn-sm btn-outline" onClick={() => copyLink(master)}>Ссылка</button>
                  <button className="btn btn-sm btn-outline" onClick={() => setEditing(master)}>Изменить</button>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      {editing ? (
        <MasterModal
          master={editing === 'new' ? null : editing}
          skills={data?.skills || []}
          onClose={closeEditor}
          onSaved={() => { closeEditor(); reload(); }}
        />
      ) : null}
    </div>
  );
}

function MasterModal({
  master,
  skills,
  onClose,
  onSaved,
}: {
  master: Master | null;
  skills: Array<{ key: string; name_ru: string }>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(master?.name || '');
  const [phone, setPhone] = useState(master?.phone || '');
  const [selectedSkills, setSelectedSkills] = useState<string[]>(master?.skills_list || []);
  const [tools, setTools] = useState(master?.tools || '');
  const [districts, setDistricts] = useState(master?.districts || '');
  const [night, setNight] = useState(Boolean(master?.night_available));
  const [active, setActive] = useState(master ? Boolean(master.active) : true);
  const [note, setNote] = useState(master?.note || '');
  const [saving, setSaving] = useState(false);
  const [stats, setStats] = useState<{ orders: Array<Record<string, unknown>>; stats?: Record<string, unknown> } | null>(null);

  useEffect(() => {
    if (!master) return;
    api<{ orders: Array<Record<string, unknown>>; stats: Record<string, unknown> }>(`/api/admin/masters/${master.id}`).then(setStats).catch(() => undefined);
  }, [master]);

  const toggleSkill = (key: string) => {
    setSelectedSkills((prev) => (prev.includes(key) ? prev.filter((s) => s !== key) : [...prev, key]));
  };

  const save = async () => {
    if (!name.trim() || !phone.trim()) return toast('Укажите имя и телефон', 'error');
    setSaving(true);
    try {
      const body = {
        name: name.trim(),
        phone: phone.trim(),
        skills: selectedSkills,
        tools,
        districts,
        night_available: night,
        active,
        note,
      };
      if (master) await api(`/api/admin/masters/${master.id}`, { method: 'PUT', body: JSON.stringify(body) });
      else await api('/api/admin/masters', { method: 'POST', body: JSON.stringify(body) });
      toast('Мастер сохранён', 'ok');
      onSaved();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={master ? `Мастер: ${master.name}` : 'Новый мастер'}
      wide
      onClose={onClose}
      footer={<><button className="btn btn-outline" onClick={onClose}>Отмена</button><button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'Сохранить'}</button></>}
    >
      <div className="grid-2" style={{ gap: 22 }}>
        <div className="stack">
          <Field label="Имя"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Рабочий телефон"><input className="input" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+7 777 123-45-67" /></Field>
          <Field label="Навыки">
            <div className="checks">
              {skills.map((skill) => (
                <label key={skill.key} className={`check-pill ${selectedSkills.includes(skill.key) ? 'on' : ''}`}>
                  <input type="checkbox" checked={selectedSkills.includes(skill.key)} onChange={() => toggleSkill(skill.key)} />
                  {skill.name_ru}
                </label>
              ))}
            </div>
          </Field>
          <Field label="Инструменты и оборудование"><textarea className="textarea" value={tools} onChange={(e) => setTools(e.target.value)} placeholder="Сварочный аппарат, трос, гидродинамика…" /></Field>
          <Field label="Районы обслуживания"><input className="input" value={districts} onChange={(e) => setDistricts(e.target.value)} /></Field>
          <div className="row wrap" style={{ gap: 16 }}>
            <label className="checkbox"><input type="checkbox" checked={night} onChange={(e) => setNight(e.target.checked)} /> Ночные выезды</label>
            <label className="checkbox"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Активен</label>
          </div>
          <Field label="Заметка"><textarea className="textarea" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        </div>
        <div className="stack">
          {master ? (
            <>
              <div className="panel" style={{ padding: 14 }}>
                <div className="detail-title">Доступ</div>
                <div className="small">Telegram: {master.telegram_id ? `ID ${master.telegram_id}` : 'не привязан'}</div>
                <div className="small mt-8">Страница: <a className="link" href={master.page_url || `${window.location.origin}/m/${master.page_token}`} target="_blank" rel="noreferrer">открыть</a></div>
                <div className="row mt-12" style={{ gap: 6 }}>
                  <button className="btn btn-sm btn-outline" onClick={() => navigator.clipboard.writeText(master.page_url || `${window.location.origin}/m/${master.page_token}`)}>Копировать ссылку</button>
                  {master.telegram_id ? <button className="btn btn-sm btn-outline" onClick={async () => { await api(`/api/admin/masters/${master.id}/unlink-telegram`, { method: 'POST' }); toast('Telegram отвязан', 'ok'); onSaved(); }}>Отвязать TG</button> : null}
                </div>
              </div>
              <div className="panel" style={{ padding: 14 }}>
                <div className="detail-title">Статистика</div>
                <div className="kv">
                  <dt>Всего заказов</dt><dd>{String(stats?.stats?.total ?? '—')}</dd>
                  <dt>Закрыто</dt><dd>{String(stats?.stats?.closed ?? '—')}</dd>
                  <dt>Отменено</dt><dd>{String(stats?.stats?.cancelled ?? '—')}</dd>
                  <dt>Выручка</dt><dd>{money(Number(stats?.stats?.revenue || 0))}</dd>
                  <dt>Заработок</dt><dd>{money(Number(stats?.stats?.earnings || 0))}</dd>
                </div>
              </div>
              <div className="panel" style={{ padding: 14, maxHeight: 260, overflowY: 'auto' }}>
                <div className="detail-title">Последние заказы</div>
                {(stats?.orders || []).slice(0, 8).map((order) => (
                  <div key={String(order.id)} className="row-between" style={{ padding: '6px 0', borderBottom: '1px solid var(--line-2)', fontSize: 12 }}>
                    <span>{String(order.number)}</span>
                    <span className="muted">{formatDateTime(String(order.created_at))}</span>
                    <b>{money(Number(order.works_total || 0))}</b>
                  </div>
                ))}
              </div>
            </>
          ) : <p className="muted small">После сохранения здесь появятся статистика и ссылка на страницу мастера. Мастер привязывает Telegram командой /start в @kranmaster_bot и отправкой своего номера.</p>}
        </div>
      </div>
    </Modal>
  );
}
