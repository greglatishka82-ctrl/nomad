export const MASTER_STATUS_META: Record<string, { label: string; color: string; emoji: string }> = {
  free: { label: 'Свободен', color: '#15803d', emoji: '🔧' },
  on_way: { label: 'В пути', color: '#0284c7', emoji: '🚗' },
  busy: { label: 'На объекте', color: '#b45309', emoji: '🧰' },
  paused: { label: 'Занят вручную', color: '#64748b', emoji: '⏸' },
  offline: { label: 'Не на линии', color: '#94a3b8', emoji: '⚫' },
};
