import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, formatDateTime, prettyPhone, useFetch, type Client } from '../api';
import { Empty, Spinner, useToast } from '../ui';

type Dialog = { id: number; name: string; phone: string | null; telegram_id: number | null; address: string; last_text: string; last_at: string; unread: number };
type Message = { id: number; client_id: number; direction: string; text: string; admin_id: number | null; created_at: string };

export function SupportPage() {
  const toast = useToast();
  const dialogs = useFetch<{ dialogs: Dialog[] }>('/api/admin/support');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const messages = useFetch<{ client: Client; messages: Message[] }>(selectedId ? `/api/admin/support/${selectedId}` : null, [selectedId]);

  useEffect(() => {
    if (selectedId == null && dialogs.data?.dialogs?.length) setSelectedId(dialogs.data.dialogs[0].id);
  }, [dialogs.data, selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    api(`/api/admin/support/${selectedId}/read`, { method: 'POST' }).then(() => dialogs.reload()).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      dialogs.reload();
      if (detail?.clientId === selectedId) messages.reload();
    };
    window.addEventListener('kranoff:ws', handler);
    return () => window.removeEventListener('kranoff:ws', handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const send = async () => {
    if (!selectedId || !text.trim()) return;
    setSending(true);
    try {
      await api(`/api/admin/support/${selectedId}/reply`, { method: 'POST', body: JSON.stringify({ text: text.trim() }) });
      setText('');
      messages.reload();
      dialogs.reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Не удалось отправить', 'error');
    } finally {
      setSending(false);
    }
  };

  const selected = messages.data?.client;
  const list = dialogs.data?.dialogs || [];

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Поддержка</h1>
          <p>Сообщения из клиентского Telegram. WhatsApp — в рабочем WhatsApp администратора.</p>
        </div>
      </div>

      {dialogs.loading && !dialogs.data ? <Spinner /> : !list.length ? <Empty text="Сообщений в поддержке пока нет" /> : (
        <div className="support-layout">
          <div className="dialog-list">
            {list.map((dialog) => (
              <div key={dialog.id} className={`dialog ${selectedId === dialog.id ? 'on' : ''}`} onClick={() => setSelectedId(dialog.id)}>
                <div className="dialog-top">
                  <b>{dialog.name || 'Без имени'}</b>
                  {dialog.unread ? <span className="nav-badge" style={{ marginLeft: 0 }}>{dialog.unread}</span> : <span className="tiny muted">{formatDateTime(dialog.last_at)}</span>}
                </div>
                <div className="dialog-preview">{dialog.last_text}</div>
              </div>
            ))}
          </div>

          <div className="chat">
            {selectedId == null ? <Empty text="Выберите диалог" /> : (
              <>
                <div className="chat-head">
                  <div>
                    <b>{selected?.name || 'Клиент'}</b>
                    <div className="tiny muted">{prettyPhone(selected?.phone)} {selected?.telegram_id ? '· Telegram' : ''}</div>
                  </div>
                  <div className="row" style={{ gap: 6 }}>
                    {selected?.id ? <Link className="btn btn-sm btn-outline" to={`/admin/clients?q=${encodeURIComponent(selected.phone || selected.name)}`}>Клиент</Link> : null}
                    {selected?.id ? <Link className="btn btn-sm btn-outline" to={`/admin/orders?q=${encodeURIComponent(selected.phone || selected.name)}`}>Заказы</Link> : null}
                  </div>
                </div>
                <div className="chat-messages">
                  {messages.loading && !messages.data ? <Spinner /> : (messages.data?.messages || []).map((message) => (
                    <div key={message.id} className={`msg ${message.direction === 'admin' ? 'msg-admin' : 'msg-client'}`}>
                      {message.text}
                      <div className="msg-time">{formatDateTime(message.created_at)}</div>
                    </div>
                  ))}
                </div>
                <div className="chat-input">
                  <input
                    className="input"
                    placeholder="Ответить клиенту в Telegram…"
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(); } }}
                  />
                  <button className="btn btn-primary" onClick={send} disabled={sending || !text.trim()}>{sending ? '…' : 'Отправить'}</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
