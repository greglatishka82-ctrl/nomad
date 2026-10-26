import path from 'node:path';
import archiver from 'archiver';
import { createOrderPdf } from './pdf.ts';
import { getOrderFull } from './orders.ts';
import { listMedia, mediaAbsolutePath } from './media.ts';
import { formatDateTime, money, statusLabel } from './utils.ts';

export function createArchive(orderIds: number[]): archiver.Archiver {
  const archive = archiver('zip', { zlib: { level: 9 } });
  void (async () => {
    try {
      for (const id of orderIds) {
        const order = getOrderFull(id);
        if (!order) continue;
        const folder = order.number;
        archive.append(await createOrderPdf(order), { name: `${folder}/card.pdf` });

        const counters: Record<string, number> = {};
        for (const media of listMedia(id)) {
          const ext = path.extname(media.file_name) || '.bin';
          const label = media.kind === 'before' ? 'before' : media.kind === 'after' ? 'after' : media.kind === 'contract' ? 'contract' : media.kind === 'video' ? 'video' : 'other';
          counters[label] = (counters[label] || 0) + 1;
          archive.file(mediaAbsolutePath(media), { name: `${folder}/${label}-${counters[label]}${ext}` });
        }

        const info = [
          `Заказ: ${order.number}`,
          `Дата: ${formatDateTime(order.created_at)}`,
          `Статус: ${statusLabel(order.status)}`,
          `Клиент: ${order.client.name}`,
          `Телефон: ${order.client.phone || ''}`,
          `Адрес: ${order.address || ''}`,
          `Проблема: ${order.problem}`,
          `Работы: ${money(order.works_total)}`,
          `Материалы: ${order.materials_note || 'по факту / отдельно'}`,
          `Мастер: ${order.master?.name || 'не назначен'}`,
          `Гарантия: ${order.guarantee_months ? order.guarantee_months + ' мес.' : 'по договору'}`,
        ].join('\n');
        archive.append(info, { name: `${folder}/info.txt` });
      }
      await archive.finalize();
    } catch (error) {
      console.error('[archive]', error);
      archive.abort();
    }
  })();
  return archive;
}
