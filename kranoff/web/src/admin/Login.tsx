import { useState } from 'react';
import { api } from '../api';

export function Login({ onSuccess }: { onSuccess: () => void }) {
  const [login, setLogin] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ login, password }) });
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ошибка входа');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <img src="/logo-emblem.svg" alt="KRANOFF" />
          <div>
            <b>KRANOFF</b>
            <span>Решил — Действуй</span>
          </div>
        </div>
        <h1>Вход в панель</h1>
        <p>Доступ для администраторов службы</p>
        {error && <div className="form-error">{error}</div>}
        <div className="field">
          <span className="label">Логин</span>
          <input className="input" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" />
        </div>
        <div className="field">
          <span className="label">Пароль</span>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus />
        </div>
        <button className="btn btn-primary btn-block mt-16" disabled={loading}>
          {loading ? 'Входим…' : 'Войти'}
        </button>
        <p className="login-note">KRANOFF · Павлодар</p>
      </form>
    </div>
  );
}
