const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const express = require('express');

// Веб-сервер для показа QR в браузере
const app = express();
let currentQR = null;

app.get('/', async (req, res) => {
    if (!currentQR) {
        res.send('<h2>QR ещё не готов, обновите страницу через 3 секунды...</h2><script>setTimeout(()=>location.reload(),3000)</script>');
        return;
    }
    const qrImage = await QRCode.toDataURL(currentQR);
    res.send(`
        <html><body style="text-align:center;font-family:sans-serif;background:#111;color:#fff">
        <h2>📱 Отсканируй QR в WhatsApp</h2>
        <p>Связанные устройства → Привязать устройство</p>
        <img src="${qrImage}" style="width:300px;height:300px"/>
        <p style="color:#aaa">Страница обновляется автоматически...</p>
        <script>setTimeout(()=>location.reload(),20000)</script>
        </body></html>
    `);
});

app.listen(3000, () => console.log('🌐 QR доступен в браузере: http://localhost:3000'));

const PHONE_NUMBER = '+7 702 718 22 33';
const SESSION_DIR = './auth_session';

const STUB_MESSAGE = `Здравствуйте! 👋

Спасибо за обращение в автошколу NOMAD!

К сожалению, запись через WhatsApp пока недоступна.

📌 Для записи на обучение:
👉 Напишите боту в Telegram: https://t.me/drivepvlbot

📞 Или позвоните нам:
📱 ${PHONE_NUMBER}

Ждём вас на обучении!`;

async function startBot() {
    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
    const { version } = await fetchLatestBaileysVersion();

    const sock = makeWASocket({
        version,
        auth: state,
        printQRInTerminal: false,
        logger: pino({ level: 'silent' }),
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            currentQR = qr;
            console.log('\n📱 Откройте браузер: http://localhost:3000\n');
            qrcode.generate(qr, { small: true });
        }

        if (connection === 'close') {
            const reason = lastDisconnect?.error?.output?.statusCode;
            console.log(`❌ Соединение закрыто. Причина: ${reason}`);

            if (reason !== DisconnectReason.loggedOut) {
                console.log('🔄 Переподключение...');
                startBot();
            } else {
                console.log('🚪 Вышли из WhatsApp. Удалите папку auth_session и запустите заново.');
            }
        }

        if (connection === 'open') {
            currentQR = null;
            console.log('✅ WhatsApp бот подключён!');
        }
    });

    sock.ev.on('messages.upsert', async ({ messages }) => {
        for (const msg of messages) {
            if (msg.key.fromMe) continue;
            if (!msg.message) continue;

            const from = msg.key.remoteJid;
            const text = msg.message.conversation
                || msg.message.extendedTextMessage?.text
                || '';

            console.log(`📩 ${from}: ${text}`);

            // ТОЛЬКО ответ на входящее. Никакой проактивной отправки.
            await sock.sendMessage(from, { text: STUB_MESSAGE });
            console.log(`📤 Отправлен ответ: ${from}`);
        }
    });
}

console.log('🚀 Запуск WhatsApp бота NOMAD...');
startBot().catch(console.error);
