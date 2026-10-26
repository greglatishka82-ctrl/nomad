import fs from 'node:fs';
import PDFDocument from 'pdfkit';
import type { OrderFull } from './orders.ts';
import { formatDateTime, money, sourceLabel, statusLabel, tariffLabel, urgencyLabel, prettyPhone, masterShare } from './utils.ts';
import { getCompany } from './catalog.ts';

function findFont(bold = false): string | undefined {
  const candidates = [
    process.env.PDF_FONT_DIR ? `${process.env.PDF_FONT_DIR}/${bold ? 'DejaVuSans-Bold.ttf' : 'DejaVuSans.ttf'}` : '',
    bold ? '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf' : '/usr/share/fonts/dejavu/DejaVuSans.ttf',
    bold ? '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf' : '/usr/share/fonts/TTF/DejaVuSans.ttf',
    bold ? 'C:/Windows/Fonts/arialbd.ttf' : 'C:/Windows/Fonts/arial.ttf',
    bold ? 'C:/Windows/Fonts/segoeuib.ttf' : 'C:/Windows/Fonts/segoeui.ttf',
  ];
  return candidates.find((p) => p && fs.existsSync(p));
}

const NAVY = '#0b1b2b';
const BLUE = '#0b6bcb';
const GRAY = '#64748b';
const LIGHT = '#eef2f7';

export async function createOrderPdf(order: OrderFull): Promise<Buffer> {
  const company = getCompany();
  const fontRegular = findFont(false);
  const fontBold = findFont(true);

  return new Promise((resolve, reject) => {
    const doc = new PDFDocument({ size: 'A4', margin: 40, bufferPages: true, info: { Title: `Заказ ${order.number}` } });
    if (fontRegular) doc.registerFont('regular', fontRegular);
    if (fontBold) doc.registerFont('bold', fontBold);
    const regular = fontRegular ? 'regular' : 'Helvetica';
    const bold = fontBold ? 'bold' : 'Helvetica-Bold';

    const chunks: Buffer[] = [];
    doc.on('data', (chunk: Buffer) => chunks.push(chunk));
    doc.on('end', () => resolve(Buffer.concat(chunks)));
    doc.on('error', reject);

    const pageWidth = doc.page.width - 80;
    const left = 40;

    doc.rect(0, 0, doc.page.width, 92).fill(NAVY);
    doc.fillColor('#ffffff').font(bold).fontSize(24).text(company.name || 'KRANOFF', left, 24);
    doc.font(regular).fontSize(10).fillColor('#9fc3e8').text(company.slogan || '', left, 54);
    doc.fillColor('#cbd5e1').fontSize(9).text(`${company.phone || ''}  ${company.address || ''}`, left, 70);
    doc.fillColor('#ffffff').font(bold).fontSize(11).text('Карточка заказа', pageWidth - 140, 28, { width: 180, align: 'right' });
    doc.font(regular).fontSize(9).fillColor('#cbd5e1').text(formatDateTime(order.created_at), pageWidth - 140, 46, { width: 180, align: 'right' });

    let y = 112;
    doc.fillColor(NAVY).font(bold).fontSize(20).text(order.number, left, y);
    doc.fillColor(BLUE).font(regular).fontSize(10).text(statusLabel(order.status), left + 150, y + 6);
    y += 32;

    const badge = (text: string, x: number, color = BLUE) => {
      const w = doc.widthOfString(text) + 16;
      doc.roundedRect(x, y, w, 20, 10).fill(color);
      doc.fillColor('#ffffff').font(regular).fontSize(9).text(text, x + 8, y + 6);
      return x + w + 6;
    };
    let bx = badge(`Источник: ${sourceLabel(order.source)}`, left);
    bx = badge(`Тариф: ${tariffLabel(order.tariff)}`, bx, order.tariff.includes('night') ? '#7c3aed' : '#0e7490');
    bx = badge(`Срочность: ${urgencyLabel(order.urgency)}`, bx, order.is_emergency ? '#b91c1c' : '#475569');
    y += 32;

    const section = (title: string) => {
      doc.fillColor(NAVY).font(bold).fontSize(11).text(title.toUpperCase(), left, y);
      y += 16;
      doc.moveTo(left, y).lineTo(left + pageWidth, y).strokeColor('#dbe3ec').lineWidth(1).stroke();
      y += 8;
    };

    const kv = (label: string, value: string, x = left, w = pageWidth / 2 - 10) => {
      doc.fillColor(GRAY).font(regular).fontSize(8.5).text(label, x, y, { width: w });
      doc.fillColor(NAVY).font(regular).fontSize(10).text(value || '—', x, y + 11, { width: w });
    };

    section('Клиент и адрес');
    kv('Клиент', order.client.name || '—');
    kv('Телефон', prettyPhone(order.client.phone) || '—', left + pageWidth / 2);
    y += 34;
    kv('Адрес', order.address || '—', left, pageWidth);
    y += 34;
    kv('Подъезд / этаж / домофон', [order.entrance, order.floor, order.intercom].filter(Boolean).join(' / ') || '—');
    kv('Комментарий', order.contact_note || '—', left + pageWidth / 2);
    y += 34;
    kv('Желаемое время', order.desired_label || formatDateTime(order.desired_at));
    kv('Мастер', order.master ? `${order.master.name} · ${prettyPhone(order.master.phone)}` : 'не назначен', left + pageWidth / 2);
    y += 38;

    section('Проблема');
    kv('Услуга', order.problem || '—', left, pageWidth);
    y += 34;
    if (order.description) {
      doc.fillColor(GRAY).font(regular).fontSize(8.5).text('Описание', left, y);
      doc.fillColor(NAVY).font(regular).fontSize(10).text(order.description, left, y + 11, { width: pageWidth });
      y += 14 + doc.heightOfString(order.description, { width: pageWidth });
    }
    y += 14;

    section('Согласованные работы');
    const colX = [left, left + 28, left + pageWidth - 170, left + pageWidth - 90];
    doc.fillColor(GRAY).font(regular).fontSize(8).text('№', colX[0], y);
    doc.text('Работа', colX[1], y);
    doc.text('Кол-во', colX[2], y, { width: 50, align: 'right' });
    doc.text('Сумма', colX[3], y, { width: 90, align: 'right' });
    y += 14;
    doc.moveTo(left, y).lineTo(left + pageWidth, y).strokeColor('#dbe3ec').stroke();
    y += 8;
    order.items.forEach((item, index) => {
      if (y > 720) {
        doc.addPage();
        y = 60;
      }
      doc.fillColor(NAVY).font(regular).fontSize(9.5).text(String(index + 1), colX[0], y);
      doc.text(item.name + (item.is_extra ? ' (доп.)' : ''), colX[1], y, { width: pageWidth - 210 });
      doc.text(String(item.qty), colX[2], y, { width: 50, align: 'right' });
      doc.text(money(item.price * item.qty), colX[3], y, { width: 90, align: 'right' });
      y += Math.max(16, doc.heightOfString(item.name, { width: pageWidth - 210 }) + 4);
    });
    if (!order.items.length) {
      doc.fillColor(GRAY).font(regular).fontSize(9).text('Позиции не добавлены', colX[1], y);
      y += 18;
    }
    y += 4;
    doc.moveTo(left, y).lineTo(left + pageWidth, y).strokeColor('#dbe3ec').stroke();
    y += 10;

    const totalBoxW = 250;
    const totalBoxX = left + pageWidth - totalBoxW;
    doc.roundedRect(totalBoxX, y, totalBoxW, 74, 8).fill(LIGHT);
    doc.fillColor(GRAY).font(regular).fontSize(8.5).text('Стоимость работ', totalBoxX + 14, y + 12);
    doc.fillColor(NAVY).font(bold).fontSize(12).text(money(order.works_total), totalBoxX + 14, y + 22, { width: totalBoxW - 28, align: 'right' });
    doc.fillColor(GRAY).font(regular).fontSize(8.5).text(`Мастеру (${Math.round(masterShare() * 100)}%)`, totalBoxX + 14, y + 42);
    doc.fillColor(NAVY).font(regular).fontSize(10).text(money(order.master_earnings), totalBoxX + 14, y + 52, { width: totalBoxW - 28, align: 'right' });
    doc.fillColor(GRAY).font(regular).fontSize(8.5).text('Материалы', left, y + 12, { width: totalBoxW - 20 });
    doc.fillColor(NAVY).font(regular).fontSize(10).text(order.materials_note || 'по факту / отдельно', left, y + 24, { width: 300 });
    if (order.guarantee_months) {
      doc.fillColor(GRAY).font(regular).fontSize(8.5).text('Гарантия', left, y + 46);
      doc.fillColor(NAVY).font(regular).fontSize(10).text(`${order.guarantee_months} мес.`, left, y + 56);
    }
    y += 90;

    if (order.events.length) {
      section('История');
      const events = order.events.slice(0, 12).reverse();
      for (const event of events) {
        doc.fillColor(GRAY).font(regular).fontSize(8).text(formatDateTime(event.created_at), left, y, { width: 90 });
        doc.fillColor(NAVY).font(regular).fontSize(8.5).text(event.text || event.type, left + 95, y, { width: pageWidth - 95 });
        y += 12;
        if (y > 760) break;
      }
    }

    const footerY = doc.page.height - 66;
    doc.moveTo(left, footerY - 8).lineTo(left + pageWidth, footerY - 8).strokeColor('#dbe3ec').stroke();
    doc.fillColor(GRAY).font(regular).fontSize(8).text(
      [company.name || 'KRANOFF', company.phone, company.address].filter(Boolean).join(' · '),
      left,
      footerY,
      { width: pageWidth / 2, lineBreak: false },
    );
    doc.text(`Сформировано ${formatDateTime(new Date().toISOString())}`, left + pageWidth / 2, footerY, {
      width: pageWidth / 2,
      align: 'right',
      lineBreak: false,
    });

    doc.end();
  });
}
