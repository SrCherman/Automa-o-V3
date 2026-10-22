/**
 * WhatsApp Bridge - historical only.
 * Captures only the chats already loaded in WhatsApp history.
 * No real-time message processing.
 */

const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
    makeCacheableSignalKeyStore,
} = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const express = require('express');
const pino = require('pino');
const path = require('path');
const fs = require('fs');
const QRCode = require('qrcode');

const FASTAPI_CONTACTS_WEBHOOK = 'http://127.0.0.1:8000/webhook/contatos';
const AUTH_DIR = path.join(__dirname, 'auth_info');
const CONTACTS_FILE = path.join(__dirname, 'contacts.json');
const STATS_FILE = path.join(__dirname, 'stats.json');
const LOG_FILE = path.join(__dirname, 'bridge.log');
const PORT = 3001;
const WA_HISTORICAL_ONLY = (process.env.WA_HISTORICAL_ONLY || '1') === '1';

const logStream = fs.createWriteStream(LOG_FILE, { flags: 'a' });
for (const method of ['log', 'error', 'warn']) {
    const original = console[method].bind(console);
    console[method] = (...args) => {
        const line = args.map(a => typeof a === 'string' ? a : JSON.stringify(a)).join(' ');
        logStream.write(`[${new Date().toISOString()}] ${line}\n`);
        original(...args);
    };
}

const logger = pino({ level: 'silent' });

let sock = null;
let latestQR = null;
let pairingCode = null;
let qrTimestamp = 0;
let connectionState = 'disconnected';
const contatos = new Map();
const lidToPhone = new Map();
let stats = {
    contatosSalvos: 0,
    lotesSincronizados: 0,
    lidIgnorados: 0,
    lidMapeados: 0,
    syncLidSemTelefone: 0,
    ultimoSync: null,
};

function saveStats() {
    fs.writeFileSync(STATS_FILE, JSON.stringify(stats, null, 2), 'utf8');
}

function loadStats() {
    try {
        const raw = fs.readFileSync(STATS_FILE, 'utf8');
        stats = { ...stats, ...JSON.parse(raw) };
    } catch (_) {}
    stats.contatosSalvos = contatos.size;
    saveStats();
}

function loadContacts() {
    try {
        const raw = fs.readFileSync(CONTACTS_FILE, 'utf8');
        for (const contato of JSON.parse(raw)) {
            if (contato && contato.telefone) contatos.set(contato.telefone, contato);
        }
    } catch (_) {}
}

function saveContacts() {
    fs.writeFileSync(CONTACTS_FILE, JSON.stringify([...contatos.values()], null, 2), 'utf8');
}

function normalizePhone(value) {
    if (!value) return null;
    let digits = String(value).replace(/\D/g, '');
    if (!digits) return null;
    if (digits.startsWith('55') && digits.length > 11) digits = digits.slice(2);
    if (digits.length > 11) digits = digits.slice(-11);
    return digits.length >= 10 ? digits : null;
}

function phoneFromJid(jid) {
    if (!jid) return null;
    if (String(jid).includes('@lid')) return null;
    return normalizePhone(String(jid).split('@')[0]);
}

function phoneFromMessage(msg) {
    return (
        normalizePhone(msg?.senderPn) ||
        normalizePhone(msg?.participantPn) ||
        normalizePhone(msg?.key?.senderPn) ||
        normalizePhone(msg?.key?.participantPn)
    );
}

function registerContact(name, jid, extraPhone = null) {
    const telefone = extraPhone || phoneFromJid(jid);
    if (!telefone) return null;

    const contato = {
        nome: String(name || 'Desconhecido').trim() || 'Desconhecido',
        telefone,
        remoteJid: jid,
        atualizadoEm: new Date().toISOString(),
    };

    contatos.set(telefone, contato);
    stats.contatosSalvos = contatos.size;
    saveStats();
    saveContacts();
    return contato;
}

async function sendAllContacts() {
    const lista = [...contatos.values()];
    if (!lista.length) return { enviados: 0 };

    const res = await fetch(FASTAPI_CONTACTS_WEBHOOK, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ contatos: lista }),
    });
    const data = await res.json().catch(() => ({}));

    stats.lotesSincronizados += 1;
    stats.ultimoSync = new Date().toISOString();
    saveStats();

    return { enviados: lista.length, status_http: res.status, ...data };
}

async function connectToWhatsApp(usePairingCode = false, phoneNumber = null) {
    latestQR = null;
    pairingCode = null;

    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    const { version, isLatest } = await fetchLatestBaileysVersion();
    console.log(`[BAILEYS] WA version: ${version.join('.')} (latest: ${isLatest})`);

    sock = makeWASocket({
        version,
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, logger),
        },
        logger,
        browser: ['Chrome', 'Edge', '20.0.04'],
        syncFullHistory: true,
        generateHighQualityLinkPreview: false,
        printQRInTerminal: false,
    });

    sock.ev.on('creds.update', saveCreds);

    if (usePairingCode && phoneNumber && !state.creds.registered) {
        setTimeout(async () => {
            try {
                const code = await sock.requestPairingCode(phoneNumber);
                pairingCode = code;
                console.log(`[PAIRING] Codigo: ${code}`);
            } catch (err) {
                console.error('[PAIRING] Erro ao solicitar codigo:', err.message);
            }
        }, 3000);
    }

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            latestQR = qr;
            qrTimestamp = Date.now();
            console.log(`[QR] Novo QR Code gerado. Acesse http://localhost:${PORT}/qr`);
        }

        if (connection === 'close') {
            connectionState = 'disconnected';
            const boom = new Boom(lastDisconnect?.error);
            const statusCode = boom?.output?.statusCode;
            const reason = boom?.output?.payload?.message || 'unknown';
            console.log(`[CONN] Fechada. Status: ${statusCode} | Motivo: ${reason}`);

            if (statusCode === DisconnectReason.loggedOut || statusCode === 401) {
                console.log('[CONN] Sessao invalidada. Limpando auth e reconectando...');
                fs.rmSync(AUTH_DIR, { recursive: true, force: true });
                setTimeout(() => connectToWhatsApp(usePairingCode, phoneNumber), 3000);
            } else {
                setTimeout(() => connectToWhatsApp(usePairingCode, phoneNumber), 3000);
            }
        } else if (connection === 'open') {
            connectionState = 'connected';
            latestQR = null;
            pairingCode = null;
            console.log('\n✅ WhatsApp conectado.\n');
        }
    });

    sock.ev.on('messaging-history.set', async ({ chats, messages }) => {
        if (!WA_HISTORICAL_ONLY) return;

        chats = chats || [];
        messages = messages || [];
        console.log(`\n[SYNC] Historico carregado. Analisando ${chats.length} conversas...`);

        const messageLidStats = messages.reduce((acc, msg) => {
            const jid = msg?.key?.remoteJid || '';
            if (jid.includes('@lid')) {
                acc.total += 1;
                if (phoneFromMessage(msg)) acc.comTelefone += 1;
                else acc.semTelefone += 1;
                const lid = jid;
                const pn = phoneFromMessage(msg);
                if (pn) lidToPhone.set(lid, pn);
            }
            return acc;
        }, { total: 0, comTelefone: 0, semTelefone: 0 });

        if (messageLidStats.total) {
            console.log(`[SYNC] LID em mensagens: total=${messageLidStats.total}, comTelefone=${messageLidStats.comTelefone}, semTelefone=${messageLidStats.semTelefone}`);
        }

        let count = 0;
        for (const chat of chats) {
            const jid = chat?.id || '';
            if (!jid) continue;
            if (!jid.includes('@s.whatsapp.net') && !jid.includes('@lid')) continue;

            const msgRelacionada = messages.find(m => m?.key?.remoteJid === jid);
            const jidReal = jid.includes('@lid')
                ? (phoneFromMessage(msgRelacionada) || lidToPhone.get(jid) || null)
                : jid;

            if (!jidReal) {
                stats.syncLidSemTelefone += 1;
                saveStats();
                console.log(`[SYNC] Chat LID sem telefone real: ${jid}`);
                continue;
            }

            const nome = chat.name || chat.notify || chat.verifiedName || msgRelacionada?.pushName || 'Desconhecido';
            registerContact(nome, jidReal, jidReal);
            count += 1;
        }

        console.log(`[SYNC] Processamento finalizado: ${count} contatos historicos enviados para a planilha.`);
    });
}

const app = express();
app.use(express.json());

app.get('/qr', (req, res) => {
    res.send(`<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WhatsApp Bridge</title>
  <style>
    body { font-family: Segoe UI, Arial, sans-serif; background: #f6f7fb; margin: 0; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
    .card { background: #fff; border-radius: 16px; padding: 24px; width: min(520px, calc(100vw - 32px)); box-shadow: 0 12px 32px rgba(0,0,0,.12); }
    h1 { margin: 0 0 12px; }
    button { padding: 10px 14px; border: 0; border-radius: 10px; background: #2563eb; color: #fff; font-weight: 700; cursor: pointer; }
    .status { margin-top: 12px; color: #475569; }
    img { max-width: 280px; display: block; margin-top: 16px; }
    input { width: 100%; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 10px; margin-top: 12px; }
    code { background: #f1f5f9; padding: 2px 6px; border-radius: 6px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>WhatsApp Bridge</h1>
    <div>Modo: <code>${WA_HISTORICAL_ONLY ? 'historico_apenas' : 'tempo_real'}</code></div>
    <div style="margin-top:10px;">
      <button onclick="loadQr()">Atualizar QR</button>
    </div>
    <div id="out" class="status">Aguardando...</div>
    <script>
      async function loadQr() {
        const res = await fetch('/api/qr');
        const data = await res.json();
        const out = document.getElementById('out');
        if (data.status === 'connected') {
          out.innerHTML = '<b>Conectado</b>';
          return;
        }
        if (data.qr_image) {
          out.innerHTML = '<img src="' + data.qr_image + '" alt="QR" />';
          return;
        }
        out.textContent = data.status || 'generating';
      }
      loadQr();
    </script>
  </div>
</body>
</html>`);
});

app.get('/api/qr', async (req, res) => {
    if (connectionState === 'connected') return res.json({ status: 'connected' });
    if (latestQR && (Date.now() - qrTimestamp < 25000)) {
        const qrImage = await QRCode.toDataURL(latestQR, { width: 280, margin: 2 });
        return res.json({ status: 'waiting', qr_image: qrImage });
    }
    return res.json({ status: 'generating' });
});

app.get('/api/pair', async (req, res) => {
    const phone = req.query.phone;
    if (!phone) return res.json({ status: 'error', message: 'Numero nao informado' });
    if (connectionState === 'connected') return res.json({ status: 'connected' });

    if (sock) {
        try { sock.end(); } catch (_) {}
    }
    fs.rmSync(AUTH_DIR, { recursive: true, force: true });
    await connectToWhatsApp(true, phone);

    for (let i = 0; i < 15; i++) {
        await new Promise(r => setTimeout(r, 1000));
        if (pairingCode) return res.json({ status: 'success', code: pairingCode });
        if (connectionState === 'connected') return res.json({ status: 'connected' });
    }

    return res.json({ status: 'error', message: 'Timeout ao gerar codigo.' });
});

app.get('/status', (req, res) => {
    res.json({
        status: connectionState,
        modo: WA_HISTORICAL_ONLY ? 'historico_apenas' : 'tempo_real',
        contatos_local: contatos.size,
        stats,
    });
});

app.post('/api/sync-all', async (req, res) => {
    try {
        const resultado = await sendAllContacts();
        res.json({ status: 'ok', total_local: contatos.size, ...resultado });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.listen(PORT, () => {
    console.log(`WhatsApp Bridge listening on http://localhost:${PORT}`);
    loadContacts();
    loadStats();
    connectToWhatsApp();
});
