import 'dotenv/config';
import path from 'node:path';
import fs from 'node:fs';

const dataDir = path.resolve(process.env.DATA_DIR || './data');
fs.mkdirSync(dataDir, { recursive: true });
fs.mkdirSync(path.join(dataDir, 'uploads'), { recursive: true });

export const config = {
  port: Number(process.env.PORT || 3000),
  host: process.env.HOST || '0.0.0.0',
  dataDir,
  uploadDir: path.join(dataDir, 'uploads'),
  dbPath: path.join(dataDir, 'kranoff.db'),
  publicBaseUrl: (process.env.PUBLIC_BASE_URL || 'http://127.0.0.1:3000').replace(/\/+$/, ''),
  sessionSecret: process.env.SESSION_SECRET || 'kranoff-dev-secret',
  adminLogin: process.env.ADMIN_LOGIN || 'admin',
  adminPassword: process.env.ADMIN_PASSWORD || 'admin',
  clientBotToken: process.env.CLIENT_BOT_TOKEN || '',
  masterBotToken: process.env.MASTER_BOT_TOKEN || '',
  clientBotUsername: process.env.CLIENT_BOT_USERNAME || 'kranof_bot',
  disableBots: process.env.DISABLE_BOTS === '1',
  timezone: process.env.TZ || 'Asia/Almaty',
  isProd: process.env.NODE_ENV === 'production',
  companyPhone: process.env.COMPANY_PHONE || '',
  companyWhatsapp: process.env.COMPANY_WHATSAPP || '',
  companyEmail: process.env.COMPANY_EMAIL || '',
  companyAddress: process.env.COMPANY_ADDRESS || 'Павлодар',
};

export function nowIso(): string {
  return new Date().toISOString();
}
