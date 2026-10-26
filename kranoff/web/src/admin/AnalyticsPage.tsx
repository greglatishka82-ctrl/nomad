import { useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { money, useFetch } from '../api';
import { Spinner } from '../ui';

type Analytics = {
  summary: {
    orders: number; closed: number; cancelled: number; active: number; revenue: number; master_earnings: number; company_share: number;
    avg_check: number; conversion: number; emergency: number; night_orders: number; new_clients: number; repeat_clients: number;
    avg_assign_min: number | null; avg_travel_min: number | null; avg_life_min: number | null;
  };
  byDay: Array<{ day: string; orders: number; closed: number; cancelled: number; revenue: number }>;
  bySource: Array<{ source: string; count: number; revenue: number }>;
  byService: Array<{ name: string; count: number; revenue: number }>;
  byMaster: Array<{ master_id: number; name: string; orders: number; closed: number; revenue: number; earnings: number; cancelled: number; avg_close_min: number | null }>;
  byHour: Array<{ hour: string; count: number }>;
  byTariff: Array<{ tariff: string; count: number; revenue: number }>;
};

const COLORS = ['#0b6bcb', '#00a5e6', '#0d9488', '#7c3aed', '#b45309', '#b91c1c', '#475569', '#15803d'];
const SOURCE_LABELS: Record<string, string> = { telegram: 'Telegram', phone: 'Телефон', whatsapp: 'WhatsApp', admin: 'Вручную' };

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const [from, setFrom] = useState(() => isoDate(new Date(Date.now() - 30 * 24 * 3600_000)));
  const [to, setTo] = useState(() => isoDate(new Date()));
  const path = `/api/admin/analytics?from=${encodeURIComponent(`${from}T00:00:00.000Z`)}&to=${encodeURIComponent(`${to}T23:59:59.999Z`)}`;
  const { data, loading, error } = useFetch<Analytics>(path, [from, to]);

  const setPreset = (value: number) => {
    setDays(value);
    setFrom(isoDate(new Date(Date.now() - value * 24 * 3600_000)));
    setTo(isoDate(new Date()));
  };

  const serviceData = useMemo(() => (data?.byService || []).slice(0, 8).map((s) => ({ ...s, name: s.name.length > 22 ? s.name.slice(0, 21) + '…' : s.name })), [data]);
  const masterData = useMemo(() => (data?.byMaster || []).filter((m) => m.orders > 0).slice(0, 8), [data]);

  if (loading && !data) return <Spinner />;
  if (error && !data) return <div className="form-error">{error}</div>;
  if (!data) return null;

  const s = data.summary;
  const cards = [
    { label: 'Обращений', value: s.orders, foot: `конверсия в закрытие ${s.conversion}%` },
    { label: 'Закрыто', value: s.closed, foot: `отменено ${s.cancelled}` },
    { label: 'Выручка', value: money(s.revenue), foot: `средний чек ${money(s.avg_check)}` },
    { label: 'Заработок мастеров', value: money(s.master_earnings), foot: `компании ${money(s.company_share)}` },
    { label: 'Аварийных', value: s.emergency, foot: `ночных заказов ${s.night_orders}` },
    { label: 'Новых клиентов', value: s.new_clients, foot: `повторных ${s.repeat_clients}` },
    { label: 'Назначение', value: s.avg_assign_min == null ? '—' : `${s.avg_assign_min} мин`, foot: 'в среднем до принятия мастером' },
    { label: 'В пути', value: s.avg_travel_min == null ? '—' : `${s.avg_travel_min} мин`, foot: `закрытие ${s.avg_life_min == null ? '—' : s.avg_life_min + ' мин'}` },
  ];

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Отчётность и аналитика</h1>
          <p>Обращения, заказы, выручка, загрузка мастеров и источники</p>
        </div>
        <div className="row wrap">
          {[7, 30, 90].map((value) => (
            <button key={value} className={`tab ${days === value ? 'on' : ''}`} onClick={() => setPreset(value)}>{value} дней</button>
          ))}
          <input className="input" type="date" value={from} onChange={(e) => { setFrom(e.target.value); setDays(0); }} style={{ width: 150 }} />
          <input className="input" type="date" value={to} onChange={(e) => { setTo(e.target.value); setDays(0); }} style={{ width: 150 }} />
        </div>
      </div>

      <div className="cards">
        {cards.map((card) => (
          <div key={card.label} className="stat">
            <div className="stat-label">{card.label}</div>
            <div className="stat-value" style={{ fontSize: card.label === 'Выручка' || card.label === 'Заработок мастеров' ? 20 : 25 }}>{card.value}</div>
            <div className="stat-foot">{card.foot}</div>
          </div>
        ))}
      </div>

      <div className="panel">
        <div className="panel-head"><h2>Обращения и закрытые заказы по дням</h2></div>
        <div className="panel-body">
          <div className="chart">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.byDay} margin={{ top: 6, right: 12, bottom: 0, left: -18 }}>
                <CartesianGrid stroke="#eef2f7" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 10 }} tickFormatter={(v) => String(v).slice(5)} />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="orders" name="Обращения" stroke="#0b6bcb" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="closed" name="Закрыто" stroke="#15803d" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="cancelled" name="Отменено" stroke="#b91c1c" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 16 }}>
        <div className="panel">
          <div className="panel-head"><h2>Источники обращений</h2></div>
          <div className="panel-body">
            <div className="chart">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={data.bySource.map((x) => ({ ...x, name: SOURCE_LABELS[x.source] || x.source }))} dataKey="count" nameKey="name" outerRadius={90} label>
                    {data.bySource.map((_, index) => <Cell key={index} fill={COLORS[index % COLORS.length]} />)}
                  </Pie>
                  <Tooltip />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-head"><h2>Популярные услуги</h2></div>
          <div className="panel-body">
            <div className="chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={serviceData} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 40 }}>
                  <CartesianGrid stroke="#eef2f7" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 10 }} allowDecimals={false} />
                  <YAxis type="category" dataKey="name" width={130} tick={{ fontSize: 9 }} />
                  <Tooltip />
                  <Bar dataKey="count" name="Заказы" fill="#0b6bcb" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 16 }}>
        <div className="panel">
          <div className="panel-head"><h2>Загрузка мастеров</h2></div>
          <div className="panel-body">
            <div className="chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={masterData} margin={{ top: 4, right: 12, bottom: 0, left: -18 }}>
                  <CartesianGrid stroke="#eef2f7" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 9 }} interval={0} angle={-15} textAnchor="end" height={50} />
                  <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                  <Tooltip />
                  <Bar dataKey="closed" name="Закрыто" fill="#0d9488" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="cancelled" name="Отменено" fill="#b91c1c" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-head"><h2>Обращения по часам</h2></div>
          <div className="panel-body">
            <div className="chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.byHour} margin={{ top: 4, right: 12, bottom: 0, left: -18 }}>
                  <CartesianGrid stroke="#eef2f7" vertical={false} />
                  <XAxis dataKey="hour" tick={{ fontSize: 9 }} />
                  <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                  <Tooltip />
                  <Bar dataKey="count" name="Обращения" fill="#00a5e6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head"><h2>Мастера за период</h2></div>
        <div className="table-wrap">
          <table className="analytics-masters">
            <thead><tr><th>Мастер</th><th>Заказы</th><th>Закрыто</th><th>Отменено</th><th className="right">Выручка</th><th className="right">Заработок</th><th>Среднее закрытие</th></tr></thead>
            <tbody>
              {data.byMaster.map((master) => (
                <tr key={master.master_id} style={{ cursor: 'default' }}>
                  <td><b>{master.name}</b></td>
                  <td>{master.orders}</td>
                  <td>{master.closed}</td>
                  <td>{master.cancelled}</td>
                  <td className="right mono">{money(master.revenue)}</td>
                  <td className="right mono">{money(master.earnings)}</td>
                  <td className="muted">{master.avg_close_min == null ? '—' : `${master.avg_close_min} мин`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
