import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import sharp from 'sharp';
import { config } from '../config.ts';
import { all, get, run } from '../db.ts';

export type MediaRow = {
  id: number;
  order_id: number;
  kind: string;
  file_name: string;
  mime: string;
  size: number;
  created_at: string;
};

export function orderDir(orderId: number): string {
  const dir = path.join(config.uploadDir, `order-${orderId}`);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

export function safeFileName(name: string): string {
  return String(name || 'file').replace(/[^\w.\-]+/g, '_').slice(0, 120);
}

function extForMime(mime: string, fallback = 'jpg'): string {
  if (mime.includes('png')) return 'png';
  if (mime.includes('webp')) return 'webp';
  if (mime.includes('gif')) return 'gif';
  if (mime.includes('mp4')) return 'mp4';
  if (mime.includes('quicktime')) return 'mov';
  if (mime.includes('webm')) return 'webm';
  if (mime.includes('jpeg') || mime.includes('jpg')) return 'jpg';
  return fallback;
}

export async function processImage(buffer: Buffer): Promise<Buffer> {
  return sharp(buffer)
    .rotate()
    .resize({ width: 1600, height: 1600, fit: 'inside', withoutEnlargement: true })
    .jpeg({ quality: 82, mozjpeg: true })
    .toBuffer();
}

export async function saveMediaBuffer(
  orderId: number,
  buffer: Buffer,
  mime: string,
  kind: string,
): Promise<MediaRow> {
  const isImage = /^image\//.test(mime);
  let data = buffer;
  let outMime = mime;
  let ext = extForMime(mime);
  if (isImage) {
    try {
      data = await processImage(buffer);
      outMime = 'image/jpeg';
      ext = 'jpg';
    } catch {
      // keep original if sharp cannot decode it
    }
  }
  const name = `${randomUUID()}.${ext}`;
  const dir = orderDir(orderId);
  const rel = `order-${orderId}/${name}`;
  fs.writeFileSync(path.join(dir, name), data);
  const id = run(
    'INSERT INTO media (order_id, kind, file_name, mime, size) VALUES (?, ?, ?, ?, ?)',
    [orderId, kind, rel, outMime, data.length],
  ).lastInsertRowid;
  return get<MediaRow>('SELECT * FROM media WHERE id = ?', [id])!;
}

export function saveMediaFile(
  orderId: number,
  sourcePath: string,
  originalName: string,
  mime: string,
  kind: string,
): MediaRow {
  const buffer = fs.readFileSync(sourcePath);
  const ext = path.extname(originalName || '') || '.' + extForMime(mime);
  const name = `${randomUUID()}${ext.toLowerCase()}`;
  const dir = orderDir(orderId);
  const rel = `order-${orderId}/${name}`;
  fs.writeFileSync(path.join(dir, name), buffer);
  const id = run(
    'INSERT INTO media (order_id, kind, file_name, mime, size) VALUES (?, ?, ?, ?, ?)',
    [orderId, kind, rel, mime, buffer.length],
  ).lastInsertRowid;
  return get<MediaRow>('SELECT * FROM media WHERE id = ?', [id])!;
}

export async function downloadTelegramFile(botToken: string, filePath: string): Promise<Buffer> {
  const url = `https://api.telegram.org/file/bot${botToken}/${filePath}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`telegram file download failed: ${res.status}`);
  const array = await res.arrayBuffer();
  return Buffer.from(array);
}

export function listMedia(orderId: number): MediaRow[] {
  return all<MediaRow>('SELECT * FROM media WHERE order_id = ? ORDER BY id', [orderId]);
}

export function getMedia(id: number): MediaRow | undefined {
  return get<MediaRow>('SELECT * FROM media WHERE id = ?', [id]);
}

export function mediaAbsolutePath(row: MediaRow): string {
  const resolved = path.resolve(config.uploadDir, row.file_name);
  if (!resolved.startsWith(path.resolve(config.uploadDir) + path.sep)) {
    throw new Error('invalid media path');
  }
  return resolved;
}

export function deleteMediaRows(rows: MediaRow[]): void {
  for (const row of rows) {
    try {
      fs.rmSync(mediaAbsolutePath(row), { force: true });
    } catch {
      // ignore file errors
    }
    run('DELETE FROM media WHERE id = ?', [row.id]);
  }
}

export function deleteMediaForOrders(orderIds: number[]): { deleted: number } {
  let deleted = 0;
  for (const orderId of orderIds) {
    const rows = listMedia(orderId);
    deleteMediaRows(rows);
    deleted += rows.length;
    try {
      const dir = path.join(config.uploadDir, `order-${orderId}`);
      if (fs.existsSync(dir) && fs.readdirSync(dir).length === 0) fs.rmdirSync(dir);
    } catch {
      // ignore
    }
  }
  return { deleted };
}

export function deleteAllMediaForOrder(orderId: number): void {
  deleteMediaRows(listMedia(orderId));
  try {
    fs.rmSync(path.join(config.uploadDir, `order-${orderId}`), { recursive: true, force: true });
  } catch {
    // ignore
  }
}

export function mediaCounts(): Record<string, number> {
  const rows = all<{ kind: string; c: number }>('SELECT kind, COUNT(*) AS c FROM media GROUP BY kind');
  const out: Record<string, number> = {};
  for (const row of rows) out[row.kind] = Number(row.c);
  return out;
}
