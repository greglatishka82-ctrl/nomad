import { useEffect, useState } from 'react';
import { api, useFetch } from '../api';
import { Field, Spinner, useToast } from '../ui';

export function SettingsPage() {
  const toast = useToast();
  const { data, loading, error, reload } = useFetch<{ values: Record<string, string> }>('/api/admin/settings');
  const [values, setValues] = useState<Record<string, string>>({});
  const [share, setShare] = useState(70);
  const [saving, setSaving] = useState(false);
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');

  useEffect(() => {
    if (!data?.values) return;
    setValues(data.values);
    setShare(Math.round(Number(data.values.master_share || 0.7) * 100));
  }, [data]);

  const set = (key: string, value: string) => setValues((prev) => ({ ...prev, [key]: value }));

  const save = async () => {
    setSaving(true);
    try {
      await api('/api/admin/settings', {
        method: 'PUT',
        body: JSON.stringify({
          values: {
            company_phone: values.company_phone || '',
            company_telegram: values.company_telegram || '',
            company_hours: values.company_hours || '',
            night_start: values.night_start || '22',
            night_end: values.night_end || '8',
            night_multiplier: values.night_multiplier || '2',
            master_share: share / 100,
            offer_timeout_minutes: values.offer_timeout_minutes || '5',
            min_masters_on_shift: values.min_masters_on_shift || '2',
            currency: values.currency || 'KZT',
          },
        }),
      });
      toast('Настройки сохранены', 'ok');
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setSaving(false);
    }
  };

  const changePassword = async () => {
    if (next.length < 6) return toast('Пароль от 6 символов', 'error');
    if (next !== repeat) return toast('Пароли не совпадают', 'error');
    try {
      await api('/api/auth/password', { method: 'POST', body: JSON.stringify({ current, next }) });
      toast('Пароль изменён. Войдите заново.', 'ok');
      window.setTimeout(() => { window.location.href = '/admin'; }, 800);
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    }
  };

  if (loading && !data) return <Spinner />;
  if (error && !data) return <div className="form-error">{error}</div>;

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Служебные настройки</h1>
          <p>Телефон, бот и часы работы. Содержимое сайта управляется отдельно, не через админку.</p>
        </div>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>{saving ? 'Сохраняем…' : 'Сохранить'}</button>
      </div>

      <div className="grid-2" style={{ alignItems: 'start' }}>
        <div className="stack">
          <div className="panel">
            <div className="panel-head"><h2>Связь и сайт</h2></div>
            <div className="panel-body">
              <div className="form-grid">
                <Field label="Телефон службы"><input className="input" value={values.company_phone || ''} onChange={(e) => set('company_phone', e.target.value)} placeholder="+7 777 123-45-67" /></Field>
                <Field label="Telegram-бот"><input className="input" value={values.company_telegram || ''} onChange={(e) => set('company_telegram', e.target.value)} placeholder="kranof_bot" /></Field>
                <Field label="Часы работы"><input className="input" value={values.company_hours || ''} onChange={(e) => set('company_hours', e.target.value)} placeholder="Круглосуточно" /></Field>
              </div>
              <p className="tiny muted mt-12">
                Эти три поля показываются на сайте и в клиентском боте. Название, адрес, тексты «О компании», гарантия, районы и политика конфиденциальности меняются разработчиком — не через админку.
              </p>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head"><h2>Пароль администратора</h2></div>
            <div className="panel-body">
              <div className="stack">
                <Field label="Текущий пароль"><input className="input" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
                <Field label="Новый пароль"><input className="input" type="password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
                <Field label="Повторите новый пароль"><input className="input" type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} /></Field>
                <button className="btn btn-outline" onClick={changePassword}>Сменить пароль</button>
              </div>
            </div>
          </div>
        </div>

        <div className="stack">
          <div className="panel">
            <div className="panel-head"><h2>Тарифы и правила</h2></div>
            <div className="panel-body">
              <div className="form-grid">
                <Field label="Ночь с (час)"><input className="input" type="number" min={0} max={23} value={values.night_start || '22'} onChange={(e) => set('night_start', e.target.value)} /></Field>
                <Field label="Ночь до (час)"><input className="input" type="number" min={0} max={23} value={values.night_end || '8'} onChange={(e) => set('night_end', e.target.value)} /></Field>
                <Field label="Ночной коэффициент"><input className="input" type="number" step="0.1" min={1} max={5} value={values.night_multiplier || '2'} onChange={(e) => set('night_multiplier', e.target.value)} /></Field>
                <Field label="Доля мастера, %"><input className="input" type="number" min={0} max={100} value={share} onChange={(e) => setShare(Number(e.target.value))} /></Field>
                <Field label="Ожидание ответа мастера, мин"><input className="input" type="number" min={1} max={60} value={values.offer_timeout_minutes || '5'} onChange={(e) => set('offer_timeout_minutes', e.target.value)} /></Field>
                <Field label="Минимум мастеров на смене"><input className="input" type="number" min={1} max={20} value={values.min_masters_on_shift || '2'} onChange={(e) => set('min_masters_on_shift', e.target.value)} /></Field>
                <Field label="Валюта"><input className="input" value={values.currency || 'KZT'} onChange={(e) => set('currency', e.target.value)} /></Field>
              </div>
              <p className="tiny muted mt-12">Доля мастера применяется к стоимости работ (без материалов). Ночной коэффициент действует с 22:00 до 08:00 по времени Павлодара.</p>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head"><h2>Служебное</h2></div>
            <div className="panel-body">
              <div className="kv">
                <dt>Боты</dt><dd>@kranof_bot · @kranmaster_bot</dd>
                <dt>API</dt><dd>/api/health</dd>
                <dt>Данные</dt><dd>SQLite + файлы на VPS</dd>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
