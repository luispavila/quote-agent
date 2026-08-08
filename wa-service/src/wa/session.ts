/**
 * Sessão ÚNICA de WhatsApp (Baileys rc11), com as armadilhas conhecidas tratadas:
 * - markOnlineOnConnect:false (senão o celular do dono para de notificar)
 * - qrTimeout 60s (default mata o socket antes do pairing code expirar)
 * - 515 restartRequired = fim ESPERADO do pareamento → reconectar sempre, sem webhook de queda
 * - logout() não confiável com store custom → race de 5s + limpeza manual do auth dir
 * - backoff exponencial com jitter; contador zera SÓ no "open"; clearTimeout em todo teardown
 * - getMessage com LRU dos enviados (evita "Aguardando esta mensagem" no destinatário)
 * - fetchLatestBaileysVersion cacheada por processo
 */

import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  normalizeMessageContent,
  type WASocket,
  type proto,
} from "baileys";
import pino from "pino";
import QRCode from "qrcode";

import { env } from "../env.js";
import { clearAuth, getAuthState } from "./authState.js";
import { postWebhook } from "../webhook/client.js";
import {
  brPhoneCandidates,
  extractText,
  isGroupJid,
  isIgnorableJid,
  jidToPhone,
  onlyDigits,
} from "./jid.js";

export type SessionStatus = "disconnected" | "connecting" | "qr" | "connected";

const logger = pino({ level: env.LOG_LEVEL });
const baileysLogger = pino({ level: "silent" });

let sock: WASocket | undefined;
let status: SessionStatus = "disconnected";
let qrDataUrl: string | undefined;
let qrExpiresAt = 0;
let reconnectAttempts = 0;
let reconnectTimer: NodeJS.Timeout | undefined;
let shuttingDown = false;
let waVersion: [number, number, number] | undefined;
let pairingPhone: string | undefined;
let pairingCodeRequested = false;
let pairingCode: string | undefined;

// LRU simples das últimas mensagens enviadas (para retry de decrypt do destinatário)
const sentMessages = new Map<string, proto.IMessage>();
function rememberSent(id: string, msg: proto.IMessage) {
  sentMessages.set(id, msg);
  if (sentMessages.size > 200) {
    const oldest = sentMessages.keys().next().value;
    if (oldest) sentMessages.delete(oldest);
  }
}

export function getStatus() {
  return { status, jid: sock?.user?.id, pairingCode };
}

export function getQr(): { qr: string; expiresAt: string } | undefined {
  if (!qrDataUrl || Date.now() > qrExpiresAt) return undefined;
  return { qr: qrDataUrl, expiresAt: new Date(qrExpiresAt).toISOString() };
}

function clearReconnectTimer() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer); // timer órfão abre socket fantasma → 440 connectionReplaced
    reconnectTimer = undefined;
  }
}

function scheduleReconnect() {
  if (shuttingDown) return;
  clearReconnectTimer();
  reconnectAttempts += 1;
  if (reconnectAttempts > 15) {
    logger.error("máximo de tentativas de reconexão atingido");
    status = "disconnected";
    return;
  }
  const base = Math.min(3000 * 2 ** (reconnectAttempts - 1), 60_000);
  const delay = base + Math.floor(Math.random() * 1000);
  logger.warn({ attempt: reconnectAttempts, delay }, "agendando reconexão");
  reconnectTimer = setTimeout(() => void connect(), delay);
}

export async function connect(): Promise<void> {
  if (shuttingDown) return;
  clearReconnectTimer();
  status = "connecting";

  const { state, saveCreds } = await getAuthState();
  waVersion ??= (await fetchLatestBaileysVersion()).version;

  const socket = makeWASocket({
    version: waVersion,
    auth: state,
    logger: baileysLogger,
    markOnlineOnConnect: false,
    qrTimeout: 60_000,
    getMessage: async (key) => (key.id ? sentMessages.get(key.id) : undefined),
  });
  sock = socket;

  socket.ev.on("creds.update", saveCreds);

  socket.ev.on("connection.update", (update) => {
    void (async () => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        status = "qr";
        qrDataUrl = await QRCode.toDataURL(qr);
        qrExpiresAt = Date.now() + 55_000;
        if (pairingPhone && !pairingCodeRequested && !state.creds.registered) {
          pairingCodeRequested = true; // pedir UMA vez — repetir invalida o código na tela
          try {
            pairingCode = await socket.requestPairingCode(pairingPhone);
            logger.info("pairing code gerado");
          } catch (err) {
            logger.error({ err }, "falha ao gerar pairing code");
          }
        }
        void postWebhook({ event: "connection.update", status: "qr" });
      }

      if (connection === "open") {
        status = "connected";
        reconnectAttempts = 0;
        qrDataUrl = undefined;
        pairingPhone = undefined;
        pairingCode = undefined;
        logger.info({ jid: socket.user?.id }, "conectado");
        void postWebhook({
          event: "connection.update",
          status: "connected",
          jid: socket.user?.id,
        });
      }

      if (connection === "close") {
        const code = (lastDisconnect?.error as { output?: { statusCode?: number } } | undefined)
          ?.output?.statusCode;
        if (shuttingDown) return;
        if (code === DisconnectReason.restartRequired) {
          // 515: fim esperado do pareamento — reconectar imediato, sem contar tentativa
          logger.info("restartRequired (515) — reconectando");
          void connect();
          return;
        }
        if (code === DisconnectReason.loggedOut) {
          logger.warn("sessão deslogada — limpando credenciais");
          status = "disconnected";
          await clearAuth();
          void postWebhook({
            event: "connection.update",
            status: "disconnected",
            reason: "logged_out",
          });
          return;
        }
        logger.warn({ code }, "conexão caiu");
        scheduleReconnect();
      }
    })();
  });

  socket.ev.on("messages.upsert", ({ messages, type }) => {
    if (type !== "notify") return;
    for (const msg of messages) {
      const remoteJid = msg.key.remoteJid ?? undefined;
      if (isIgnorableJid(remoteJid)) continue;
      const content = normalizeMessageContent(msg.message ?? undefined);
      const text = extractText(content);
      if (!text) continue; // Marco 1: só texto

      const fromDigits = remoteJid ? onlyDigits(remoteJid.split("@")[0] ?? "") : "";
      // conta LID: telefone real do remetente vem no *Alt
      const senderPn =
        (msg.key as { remoteJidAlt?: string; participantAlt?: string }).remoteJidAlt ??
        (msg.key as { remoteJidAlt?: string; participantAlt?: string }).participantAlt;

      void postWebhook({
        event: "message.received",
        messageId: msg.key.id ?? "",
        fromJid: remoteJid ?? "",
        from: senderPn ? onlyDigits(senderPn.split("@")[0] ?? "") : fromDigits,
        isGroup: isGroupJid(remoteJid),
        fromMe: msg.key.fromMe ?? false,
        pushName: msg.pushName ?? undefined,
        text,
        timestamp: new Date(Number(msg.messageTimestamp ?? Date.now() / 1000) * 1000).toISOString(),
      });
    }
  });
}

export async function requestPairing(phone: string): Promise<void> {
  const { state } = await getAuthState();
  if (state.creds.registered) {
    throw Object.assign(new Error("já pareado — faça logout antes"), { statusCode: 409 });
  }
  // pairing por código exige auth state virgem
  await clearAuth();
  pairingPhone = onlyDigits(phone);
  pairingCodeRequested = false;
  pairingCode = undefined;
  sock?.end(undefined);
  await connect();
}

export async function sendText(phone: string, text: string): Promise<{ messageId: string }> {
  if (!sock || status !== "connected") {
    throw Object.assign(new Error("sessão não conectada"), { statusCode: 409 });
  }
  let jid: string | undefined;
  for (const candidate of brPhoneCandidates(phone)) {
    const results = await sock.onWhatsApp(candidate);
    const hit = results?.find((r) => r.exists);
    if (hit?.jid) {
      jid = hit.jid;
      break;
    }
  }
  jid ??= `${onlyDigits(phone)}@s.whatsapp.net`;
  const sent = await sock.sendMessage(jid, { text });
  if (sent?.key.id && sent.message) rememberSent(sent.key.id, sent.message);
  return { messageId: sent?.key.id ?? "" };
}

export async function logout(): Promise<void> {
  const current = sock;
  if (current) {
    // logout() sem timeout pode pendurar; limpeza do estado é por nossa conta
    await Promise.race([
      current.logout().catch(() => undefined),
      new Promise((resolve) => setTimeout(resolve, 5000)),
    ]);
    current.end(undefined);
  }
  await clearAuth();
  status = "disconnected";
  sock = undefined;
}

export async function shutdown(): Promise<void> {
  shuttingDown = true;
  clearReconnectTimer();
  sock?.end(undefined); // end(), nunca logout() — preserva as credenciais
}

export function statusForHealth(): SessionStatus {
  return status;
}

export function jidForHealth(): string | undefined {
  return sock?.user?.id ?? undefined;
}

const exportedForTests = { rememberSent, sentMessages };
export { exportedForTests as _internal };
