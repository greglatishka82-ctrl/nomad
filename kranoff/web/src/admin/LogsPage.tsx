import { useEffect, useState } from 'react';
import { formatDateTime, useFetch } from '../api';
import { Empty, Spinner } from '../ui';

type LogItem = {
  id: number;
  admin_id: number | null;
  admin_name: string;
  action: string;
  entity: string;
  entity_id: number | null;
  details: string;
  created_at: string;
  description: string;
};

type LogsResponse = {
  items: LogItem[];
  page: number;
  pages: number;
  pageSize: number;
  total: number;
};

function pageNumbers(current: number, total: number): Array<number | '…'> {
  if (total <= 9) return Array.from({ length: total }, (_, index) => index + 1);
  const pages = new Set<number>([1, total, current - 2, current - 1, current, current + 1, current + 2]);
  const sorted = [...pages].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b);
  const result: Array<number | '…'> = [];
  for (const page of sorted) {
    const previous = result[result.length - 1];
    if (typeof previous === 'number' && page - previous > 1) result.push('…');
    result.push(page);
  }
  return result;
}

export function LogsPage() {
  const [page, setPage] = useState(1);
  const { data, loading, error, reload } = useFetch<LogsResponse>(`/api/admin/logs?page=${page}`, [page]);

  useEffect(() => {
    if (data && page > data.pages) setPage(data.pages);
  }, [data, page]);

  const pages = data?.pages || 1;
  const current = data?.page || page;
  const items = data?.items || [];

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Журнал действий</h1>
          <p>Все действия администраторов. Журнал только для чтения — очистка и удаление записей запрещены.</p>
        </div>
        <button className="btn btn-sm btn-outline" onClick={reload} disabled={loading}>{loading ? 'Обновляем…' : 'Обновить'}</button>
      </div>

      <div className="log-summary">
        <span>Всего записей: <b>{data?.total ?? 0}</b></span>
        <span>На странице: <b>{data?.pageSize ?? 50}</b></span>
        <span>Страница: <b>{current}</b> из <b>{pages}</b></span>
      </div>

      <div className="panel">
        {loading && !data ? <Spinner /> : error ? (
          <div className="form-error" style={{ margin: 16 }}>{error}</div>
        ) : items.length === 0 ? (
          <Empty text="Записей пока нет" />
        ) : (
          <div className="table-wrap">
            <table className="log-table">
              <thead>
                <tr>
                  <th style={{ width: 64 }}>№</th>
                  <th style={{ width: 170 }}>Время</th>
                  <th style={{ width: 170 }}>Администратор</th>
                  <th>Действие</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td className="mono muted">{item.id}</td>
                    <td className="nowrap muted">{formatDateTime(item.created_at)}</td>
                    <td><b>{item.admin_name}</b></td>
                    <td className="log-description">{item.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="log-pager">
          <div className="log-pager-info">
            Страница <b>{current}</b> из <b>{pages}</b> · всего <b>{data?.total ?? 0}</b> записей
          </div>
          <div className="log-pager-controls">
            <button className="btn btn-sm btn-outline" disabled={current <= 1} onClick={() => setPage(1)}>« Первая</button>
            <button className="btn btn-sm btn-outline" disabled={current <= 1} onClick={() => setPage(current - 1)}>← Назад</button>
            <div className="log-pages">
              {pageNumbers(current, pages).map((number, index) => number === '…' ? (
                <span key={`gap-${index}`} className="log-gap">…</span>
              ) : (
                <button
                  key={number}
                  className={`log-page ${number === current ? 'on' : ''}`}
                  onClick={() => setPage(number)}
                  aria-current={number === current ? 'page' : undefined}
                >
                  {number}
                </button>
              ))}
            </div>
            <button className="btn btn-sm btn-outline" disabled={current >= pages} onClick={() => setPage(current + 1)}>Вперёд →</button>
            <button className="btn btn-sm btn-outline" disabled={current >= pages} onClick={() => setPage(pages)}>Последняя »</button>
          </div>
        </div>
      </div>
    </div>
  );
}
