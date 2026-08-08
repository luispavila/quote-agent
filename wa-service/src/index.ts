import { timingSafeEqual } from "node:crypto";

import Fastify from "fastify";
import { z } from "zod";

import { env } from "./env.js";
import { hasCreds } from "./wa/authState.js";
import {
  connect,
  getQr,
  getStatus,
  jidForHealth,
  logout,
  requestPairing,
  sendText,
  shutdown,
  statusForHealth,
} from "./wa/session.js";

const app = Fastify({ logger: { level: env.LOG_LEVEL } });

function tokenOk(header: unknown): boolean {
  if (typeof header !== "string") return false;
  const a = Buffer.from(header);
  const b = Buffer.from(env.WA_SHARED_TOKEN);
  return a.length === b.length && timingSafeEqual(a, b);
}

app.addHook("preHandler", async (req, reply) => {
  if (req.url === "/health") return;
  const token = req.headers["x-wa-token"] ?? (req.query as { token?: string }).token;
  if (!tokenOk(token)) {
    return reply.code(401).send({ ok: false, error: "unauthorized" });
  }
});

async function waitForQr(): Promise<{ qr: string; expiresAt: string } | undefined> {
  const { status } = getStatus();
  if (status === "connected") return undefined;
  if (status === "disconnected") void connect();
  for (let i = 0; i < 40; i++) {
    const qr = getQr();
    if (qr) return qr;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return undefined;
}

app.get("/health", async () => ({ ok: true, status: statusForHealth() }));

app.get("/status", async () => {
  const { status, jid, pairingCode } = getStatus();
  return { ok: true, status, jid: jid ?? null, pairingCode: pairingCode ?? null };
});

app.post("/pairing/qr", async (_req, reply) => {
  if (getStatus().status === "connected")
    return reply.code(409).send({ ok: false, error: "already_connected" });
  const qr = await waitForQr();
  if (!qr) return reply.code(504).send({ ok: false, error: "qr_timeout" });
  return { ok: true, ...qr };
});

// abre direto no browser: GET /pairing/qr.png?token=<WA_SHARED_TOKEN>
app.get("/pairing/qr.png", async (_req, reply) => {
  if (getStatus().status === "connected")
    return reply.code(409).send({ ok: false, error: "already_connected" });
  const qr = await waitForQr();
  if (!qr) return reply.code(504).send({ ok: false, error: "qr_timeout" });
  const png = Buffer.from(qr.qr.split(",")[1] ?? "", "base64");
  return reply.header("content-type", "image/png").header("cache-control", "no-store").send(png);
});

app.post("/pairing/code", async (req, reply) => {
  const body = z.object({ phone: z.string().min(10) }).safeParse(req.body);
  if (!body.success) return reply.code(400).send({ ok: false, error: "invalid_body" });
  try {
    await requestPairing(body.data.phone);
  } catch (err) {
    const status = (err as { statusCode?: number }).statusCode ?? 500;
    return reply.code(status).send({ ok: false, error: (err as Error).message });
  }
  for (let i = 0; i < 60; i++) {
    const { pairingCode } = getStatus();
    if (pairingCode) return { ok: true, code: pairingCode };
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return reply.code(504).send({ ok: false, error: "pairing_code_timeout" });
});

app.post("/messages/text", async (req, reply) => {
  const body = z
    .object({ phone: z.string().min(10), text: z.string().min(1).max(4096) })
    .safeParse(req.body);
  if (!body.success) return reply.code(400).send({ ok: false, error: "invalid_body" });
  try {
    const result = await sendText(body.data.phone, body.data.text);
    return { ok: true, ...result };
  } catch (err) {
    const status = (err as { statusCode?: number }).statusCode ?? 500;
    return reply.code(status).send({ ok: false, error: (err as Error).message });
  }
});

app.post("/session/logout", async () => {
  await logout();
  return { ok: true };
});

async function main() {
  await app.listen({ port: env.PORT, host: "0.0.0.0" });
  // reconecta sozinho se já houver credenciais salvas (Postgres ou disco)
  if (await hasCreds()) {
    app.log.info("credenciais encontradas — reconectando sessão");
    void connect();
  } else {
    const backend = env.DATABASE_URL ? "postgres" : env.AUTH_DIR;
    app.log.info(`sem credenciais (${backend}) — aguarde pareamento (jid=${jidForHealth() ?? "n/a"})`);
  }
}

async function stop() {
  await shutdown();
  await app.close();
  process.exit(0);
}
process.on("SIGTERM", () => void stop());
process.on("SIGINT", () => void stop());

void main();
