import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { api, formatTime, useFetch } from '../api';
import { useToast } from '../ui';
import type { Me } from './AdminApp';

const NAV = [
  { to: '/admin', label: 'Главная', icon: '◉', end: true },
  { to: '/admin/orders', label: 'Заказы', icon: '🧾' },
  { to: '/admin/map', label: 'Карта', icon: '🗺' },
  { to: '/admin/clients', label: 'Клиенты', icon: '👤' },
  { to: '/admin/masters', label: 'Мастера', icon: '🔧' },
  { to: '/admin/services', label: 'Услуги и цены', icon: '💲' },
  { to: '/admin/support', label: 'Поддержка', icon: '💬' },
  { to: '/admin/analytics', label: 'Аналитика', icon: '📈' },
  { to: '/admin/admins', label: 'Смены и админы', icon: '⏱' },
  { to: '/admin/logs', label: 'Журнал действий', icon: '📜' },
  { to: '/admin/settings', label: 'Настройки', icon: '⚙' },
];

const TITLES: Record<string, string> = {
  '/admin': 'Главная',
  '/admin/orders': 'Заказы',
  '/admin/map': 'Карта',
  '/admin/clients': 'Клиенты',
  '/admin/masters': 'Мастера',
  '/admin/services': 'Услуги и цены',
  '/admin/support': 'Поддержка',
  '/admin/analytics': 'Аналитика',
  '/admin/admins': 'Смены и админы',
  '/admin/logs': 'Журнал действий',
  '/admin/settings': 'Настройки',
};

function beep(): void {
  try {
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sine';
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.06, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.22);
    osc.start();
    osc.stop(ctx.currentTime + 0.24);
    window.setTimeout(() => ctx.close().catch(() => undefined), 500);
  } catch {
    // ignore
  }
}

export function AdminLayout({
  me,
  onRefresh,
  onLogout,
  children,
}: {
  me: Me;
  onRefresh: () => void;
  onLogout: () => void;
  children: ReactNode;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const toast = useToast();
  const [soundOn, setSoundOn] = useState(() => localStorage.getItem('kranoff_sound') !== '0');
  const [clock, setClock] = useState(new Date());
  const [starting, setStarting] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const soundRef = useRef(soundOn);
  soundRef.current = soundOn;

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  const support = useFetch<{ dialogs: Array<{ unread: number }> }>('/api/admin/support');
  const unread = (support.data?.dialogs || []).reduce((sum, d) => sum + Number(d.unread || 0), 0);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnect: number | undefined;
    let closed = false;

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      socket = new WebSocket(`${proto}://${window.location.host}/ws/admin`);
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          window.dispatchEvent(new CustomEvent('kranoff:ws', { detail: data }));
          if (data.type === 'order:new') {
            toast(`Новая заявка ${data.number}`, 'info');
            if (soundRef.current) beep();
          } else if (data.type === 'support:new') {
            toast('Новое сообщение в поддержке', 'info');
            if (soundRef.current) beep();
            support.reload();
          } else if (data.type === 'offer:expired') {
            toast(`Мастер не ответил по заказу ${data.number} — нужен звонок`, 'error');
            if (soundRef.current) beep();
          } else if (data.type === 'support:changed') {
            support.reload();
          }
        } catch {
          // ignore
        }
      };
      socket.onclose = () => {
        if (closed) return;
        reconnect = window.setTimeout(connect, 3000);
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    const ping = window.setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping' }));
    }, 25_000);
    return () => {
      closed = true;
      window.clearInterval(ping);
      if (reconnect) window.clearTimeout(reconnect);
      socket?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleSound = () => {
    const next = !soundOn;
    setSoundOn(next);
    localStorage.setItem('kranoff_sound', next ? '1' : '0');
  };

  const startShift = async () => {
    setStarting(true);
    try {
      await api('/api/auth/shift/start', { method: 'POST' });
      toast('Смена начата', 'ok');
      onRefresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    } finally {
      setStarting(false);
    }
  };

  const endShift = async () => {
    if (!window.confirm('Завершить смену? Заказы останутся в системе.')) return;
    try {
      await api('/api/auth/shift/end', { method: 'POST' });
      toast('Смена завершена', 'ok');
      onRefresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Ошибка', 'error');
    }
  };

  const logout = async () => {
    try {
      await api('/api/auth/logout', { method: 'POST' });
    } finally {
      onLogout();
      navigate('/admin');
    }
  };

  const title = location.pathname.startsWith('/admin/orders/')
    ? 'Карточка заказа'
    : TITLES[location.pathname] || 'Админ-панель';

  return (
    <div className="admin">
      <aside className={`sidebar ${navOpen ? 'open' : ''}`}>
        <div className="sidebar-brand">
          <img src="/logo-emblem.svg" alt="" />
          <div>
            <b>KRANOFF</b>
            <span>Диспетчерская</span>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setNavOpen(false)}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            >
              <span className="nav-ico">{item.icon}</span>
              <span>{item.label}</span>
              {item.to === '/admin/support' && unread > 0 ? <span className="nav-badge">{unread}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="sidebar-user">
            <b>{me.admin.name}</b>
            {me.shift ? `На смене с ${formatTime(me.shift.started_at)}` : 'Смена не начата'}
          </div>
          <button className="sidebar-sound" onClick={toggleSound}>{soundOn ? '🔔 Звук включён' : '🔕 Звук выключен'}</button>
        </div>
      </aside>
      <button className={`sidebar-backdrop ${navOpen ? 'show' : ''}`} onClick={() => setNavOpen(false)} aria-label="Закрыть меню" />

      <div className="main">
        <div className="topbar">
          <button className="icon-btn admin-mobile-toggle" onClick={() => setNavOpen((value) => !value)} aria-label="Меню админки">☰</button>
          <div>
            <div className="topbar-title">{title}</div>
            <div className="topbar-sub">{clock.toLocaleDateString('ru-RU', { weekday: 'short', day: '2-digit', month: 'long' })} · {clock.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</div>
          </div>
          <div className="topbar-actions">
            <Link className="icon-btn topbar-site" to="/" target="_blank" title="Открыть сайт">↗</Link>
            <button className="icon-btn topbar-sound" onClick={toggleSound} title={soundOn ? 'Звук включён' : 'Звук выключен'}>{soundOn ? '🔔' : '🔕'}</button>
            {me.shift ? (
              <button className="btn btn-sm btn-outline topbar-shift" onClick={endShift}>Завершить смену</button>
            ) : (
              <button className="btn btn-sm btn-primary topbar-shift" onClick={startShift} disabled={starting}>{starting ? '…' : 'Начать смену'}</button>
            )}
            <button className="icon-btn topbar-logout" onClick={logout} title="Выйти">⎋</button>
          </div>
        </div>

        <div className="content">
          {!me.shift ? (
            <div className="shift-banner">
              <span>Смена не начата. Начните смену, чтобы новые заказы привязывались к вам и отображались в отчётах.</span>
              <button className="btn btn-sm btn-primary" onClick={startShift} disabled={starting}>Начать смену</button>
            </div>
          ) : null}
          {children}
        </div>
      </div>
    </div>
  );
}
