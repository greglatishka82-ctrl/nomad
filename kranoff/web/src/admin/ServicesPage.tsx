import { useEffect, useState } from 'react';
import { api, money, useFetch, type Category, type Service } from '../api';
import { Field, Modal, Spinner, useToast } from '../ui';

type Response = { categories: Category[]; services: Service[]; skills: Array<{ key: string; name_ru: string }> };

export function ServicesPage() {
  const { data, loading, error, reload } = useFetch<Response>('/api/admin/services');
  const [cat, setCat] = useState<number | 'all'>('all');
  const [editing, setEditing] = useState<Service | 'new' | null>(null);
  const [categoryOpen, setCategoryOpen] = useState(false);
  const toast = useToast();

  useEffect(() => {
    const handler = () => reload();
    window.addEventListener('kranoff:ws', handler);
    return () => window.removeEventListener('kranoff:ws', handler);
  }, [reload]);

  const cats = data?.categories || [];
  const services = (data?.services || []).filter((s) => cat === 'all' || s.category_id === cat);

  const toggle = async (service: Service) => {
    try {
      await api(`/api/admin/services/${service.id}`, { method: 'PUT', body: JSON.stringify({ active: !service.active }) });
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    }
  };

  const updatePrice = async (service: Service, field: 'price' | 'price_night', value: number | null) => {
    try {
      await api(`/api/admin/services/${service.id}`, { method: 'PUT', body: JSON.stringify({ [field]: value }) });
      toast('Цена обновлена', 'ok');
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    }
  };

  const remove = async (service: Service) => {
    if (!window.confirm(`Удалить услугу «${service.name_ru}»? Она исчезнет из сайта и бота.`)) return;
    try {
      await api(`/api/admin/services/${service.id}`, { method: 'DELETE' });
      toast('Услуга удалена', 'ok');
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    }
  };

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Услуги и цены</h1>
          <p>Единый прайс для сайта, Telegram-бота и ручного создания заказов</p>
        </div>
        <div className="row">
          <button className="btn btn-outline btn-sm" onClick={() => setCategoryOpen(true)}>+ Категория</button>
          <button className="btn btn-primary btn-sm" onClick={() => setEditing('new')}>+ Добавить услугу</button>
        </div>
      </div>

      <div className="tabs">
        <button className={`tab ${cat === 'all' ? 'on' : ''}`} onClick={() => setCat('all')}>Все</button>
        {cats.map((c) => (
          <button key={c.id} className={`tab ${cat === c.id ? 'on' : ''}`} onClick={() => setCat(c.id)}>{c.icon} {c.name_ru}</button>
        ))}
      </div>

      <div className="panel">
        {loading && !data ? <Spinner /> : error ? <div className="form-error" style={{ margin: 16 }}>{error}</div> : (
          <div className="table-wrap services-table-wrap">
            <table>
              <thead><tr><th>Услуга</th><th>Категория</th><th className="right">День</th><th className="right">Ночь</th><th>Аварийная</th><th>Статус</th><th /></tr></thead>
              <tbody>
                {services.map((service) => (
                  <tr key={service.id} style={{ cursor: 'default' }}>
                    <td>
                      <b>{service.name_ru}</b>
                      <div className="tiny muted">{service.name_en}</div>
                    </td>
                    <td className="muted">{cats.find((c) => c.id === service.category_id)?.name_ru}</td>
                    <td className="right"><PriceInput value={service.price} onSave={(value) => updatePrice(service, 'price', value)} /></td>
                    <td className="right"><PriceInput value={service.price_night ?? null} nullable onSave={(value) => updatePrice(service, 'price_night', value)} /></td>
                    <td>{service.is_emergency ? <span className="tag tag-red">да</span> : <span className="muted">—</span>}</td>
                    <td>{service.active ? <span className="tag tag-green">включена</span> : <span className="tag tag-gray">выключена</span>}</td>
                    <td className="right">
                      <div className="row" style={{ justifyContent: 'flex-end', gap: 4 }}>
                        <button className="btn btn-sm btn-outline" onClick={() => toggle(service)}>{service.active ? 'Выключить' : 'Включить'}</button>
                        <button className="btn btn-sm btn-outline" onClick={() => setEditing(service)}>Изменить</button>
                        <button className="btn btn-sm btn-danger" onClick={() => remove(service)}>Удалить</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {services.length ? (
        <div className="services-mobile">
          {services.map((service) => (
            <div key={service.id} className="service-mobile-card">
              <div className="service-mobile-top">
                <div>
                  <b>{service.name_ru}</b>
                  <div className="tiny muted">{service.name_en}</div>
                </div>
                <span className={`tag ${service.active ? 'tag-green' : 'tag-gray'}`}>{service.active ? 'включена' : 'выключена'}</span>
              </div>
              <div className="row wrap" style={{ gap: 4, marginTop: 6 }}>
                <span className="tag tag-gray">{cats.find((c) => c.id === service.category_id)?.name_ru || 'без категории'}</span>
                {service.is_emergency ? <span className="tag tag-red">аварийная</span> : null}
              </div>
              <div className="service-mobile-prices">
                <label>День <PriceInput value={service.price} onSave={(value) => updatePrice(service, 'price', value)} /></label>
                <label>Ночь <PriceInput value={service.price_night ?? null} nullable onSave={(value) => updatePrice(service, 'price_night', value)} /></label>
              </div>
              <div className="row wrap" style={{ gap: 5, marginTop: 8 }}>
                <button className="btn btn-sm btn-outline" onClick={() => toggle(service)}>{service.active ? 'Выключить' : 'Включить'}</button>
                <button className="btn btn-sm btn-outline" onClick={() => setEditing(service)}>Изменить</button>
                <button className="btn btn-sm btn-danger" onClick={() => remove(service)}>Удалить</button>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {editing ? (
        <ServiceModal
          service={editing === 'new' ? null : editing}
          categories={cats}
          skills={data?.skills || []}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }}
        />
      ) : null}

      {categoryOpen ? (
        <CategoryModal onClose={() => setCategoryOpen(false)} onSaved={() => { setCategoryOpen(false); reload(); }} />
      ) : null}
    </div>
  );
}

function PriceInput({
  value,
  nullable,
  onSave,
}: {
  value: number | null;
  nullable?: boolean;
  onSave: (value: number | null) => void;
}) {
  const [current, setCurrent] = useState<string>(value == null ? '' : String(value));
  useEffect(() => {
    setCurrent(value == null ? '' : String(value));
  }, [value]);
  return (
    <input
      className="input"
      style={{ width: 100, padding: '5px 8px', textAlign: 'right' }}
      type="number"
      min={0}
      value={current}
      placeholder={nullable ? '×2' : '0'}
      onChange={(e) => setCurrent(e.target.value)}
      onBlur={() => {
        const next = current.trim() === '' ? (nullable ? null : 0) : Math.max(0, Math.round(Number(current)));
        if (next !== value) onSave(next);
      }}
    />
  );
}

function CategoryModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [slug, setSlug] = useState('');
  const [nameRu, setNameRu] = useState('');
  const [nameEn, setNameEn] = useState('');
  const [nameKz, setNameKz] = useState('');
  const [icon, setIcon] = useState('🔧');
  const [sort, setSort] = useState(0);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await api('/api/admin/categories', {
        method: 'POST',
        body: JSON.stringify({ slug: slug.trim(), name_ru: nameRu.trim(), name_en: nameEn.trim(), name_kz: nameKz.trim(), icon, sort }),
      });
      toast('Категория добавлена', 'ok');
      onSaved();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="Новая категория" narrow onClose={onClose} footer={<><button className="btn btn-outline" onClick={onClose}>Отмена</button><button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'Добавить'}</button></>}>
      <div className="stack">
        <Field label="Название (RU)"><input className="input" value={nameRu} onChange={(e) => setNameRu(e.target.value)} placeholder="Например, Ремонт ванной" /></Field>
        <Field label="Название (EN)"><input className="input" value={nameEn} onChange={(e) => setNameEn(e.target.value)} placeholder="Bathroom repair" /></Field>
        <Field label="Название (KZ)"><input className="input" value={nameKz} onChange={(e) => setNameKz(e.target.value)} placeholder="Ванна бөлмесін жөндеу" /></Field>
        <Field label="Slug (латиницей, без пробелов)"><input className="input" value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="bathroom" /></Field>
        <div className="form-grid">
          <Field label="Иконка"><input className="input" value={icon} onChange={(e) => setIcon(e.target.value)} placeholder="🔧" /></Field>
          <Field label="Сортировка"><input className="input" type="number" value={sort} onChange={(e) => setSort(Number(e.target.value))} /></Field>
        </div>
        <p className="tiny muted">Slug — латиница, цифры и дефис, без пробелов. После создания добавьте в категорию услуги.</p>
      </div>
    </Modal>
  );
}

function ServiceModal({
  service,
  categories,
  skills,
  onClose,
  onSaved,
}: {
  service: Service | null;
  categories: Category[];
  skills: Array<{ key: string; name_ru: string }>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [categoryId, setCategoryId] = useState(service?.category_id || categories[0]?.id || 0);
  const [nameRu, setNameRu] = useState(service?.name_ru || '');
  const [nameEn, setNameEn] = useState(service?.name_en || '');
  const [nameKz, setNameKz] = useState(service?.name_kz || '');
  const [descRu, setDescRu] = useState(service?.description_ru || '');
  const [descEn, setDescEn] = useState(service?.description_en || '');
  const [descKz, setDescKz] = useState(service?.description_kz || '');
  const [price, setPrice] = useState(service?.price || 0);
  const [priceNight, setPriceNight] = useState<number | ''>(service?.price_night ?? '');
  const [emergency, setEmergency] = useState(Boolean(service?.is_emergency));
  const [active, setActive] = useState(service ? Boolean(service.active) : true);
  const [selectedSkills, setSelectedSkills] = useState<string[]>(service?.skills || []);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!nameRu.trim() || !nameEn.trim()) return toast('Заполните название на русском и английском', 'error');
    setSaving(true);
    try {
      const body = {
        category_id: categoryId,
        name_ru: nameRu.trim(),
        name_en: nameEn.trim(),
        name_kz: nameKz.trim(),
        description_ru: descRu,
        description_en: descEn,
        description_kz: descKz,
        price,
        price_night: priceNight === '' ? null : Number(priceNight),
        is_emergency: emergency,
        skills: selectedSkills,
        active,
      };
      if (service) await api(`/api/admin/services/${service.id}`, { method: 'PUT', body: JSON.stringify(body) });
      else await api('/api/admin/services', { method: 'POST', body: JSON.stringify(body) });
      toast('Услуга сохранена', 'ok');
      onSaved();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={service ? `Услуга: ${service.name_ru}` : 'Новая услуга'} wide onClose={onClose} footer={<><button className="btn btn-outline" onClick={onClose}>Отмена</button><button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : 'Сохранить'}</button></>}>
      <div className="form-grid">
        <Field label="Категория">
          <select className="select" value={categoryId} onChange={(e) => setCategoryId(Number(e.target.value))}>
            {categories.map((c) => <option key={c.id} value={c.id}>{c.name_ru}</option>)}
          </select>
        </Field>
        <Field label="Цена днём, ₸"><input className="input" type="number" min={0} value={price} onChange={(e) => setPrice(Number(e.target.value))} /></Field>
        <Field label="Название (RU)"><input className="input" value={nameRu} onChange={(e) => setNameRu(e.target.value)} /></Field>
        <Field label="Название (EN)"><input className="input" value={nameEn} onChange={(e) => setNameEn(e.target.value)} /></Field>
        <Field label="Название (KZ)"><input className="input" value={nameKz} onChange={(e) => setNameKz(e.target.value)} placeholder="Қазақша атауы" /></Field>
        <Field label="Описание (RU)" full><textarea className="textarea" value={descRu} onChange={(e) => setDescRu(e.target.value)} /></Field>
        <Field label="Описание (EN)" full><textarea className="textarea" value={descEn} onChange={(e) => setDescEn(e.target.value)} /></Field>
        <Field label="Описание (KZ)" full><textarea className="textarea" value={descKz} onChange={(e) => setDescKz(e.target.value)} /></Field>
        <Field label="Цена ночью, ₸ (пусто = ×2)"><input className="input" type="number" min={0} value={priceNight} onChange={(e) => setPriceNight(e.target.value === '' ? '' : Number(e.target.value))} /></Field>
        <div className="row wrap" style={{ gap: 16, alignSelf: 'end', paddingBottom: 9 }}>
          <label className="checkbox"><input type="checkbox" checked={emergency} onChange={(e) => setEmergency(e.target.checked)} /> Аварийная</label>
          <label className="checkbox"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Активна</label>
        </div>
        <Field label="Навыки" full>
          <div className="checks">
            {skills.map((skill) => (
              <label key={skill.key} className={`check-pill ${selectedSkills.includes(skill.key) ? 'on' : ''}`}>
                <input type="checkbox" checked={selectedSkills.includes(skill.key)} onChange={() => setSelectedSkills((prev) => prev.includes(skill.key) ? prev.filter((s) => s !== skill.key) : [...prev, skill.key])} />
                {skill.name_ru}
              </label>
            ))}
          </div>
        </Field>
      </div>
    </Modal>
  );
}
