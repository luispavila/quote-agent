/** Entrega de webhook com retry + timeout em TODOS os eventos (a referência só fazia em poll-vote). */

import { env } from "../env.js";

export type WebhookEvent =
  | {
      event: "message.received";
      messageId: string;
      from: string;
      fromJid: string;
      isGroup: boolean;
      fromMe: boolean;
      pushName?: string;
      text: string;
      timestamp: string;
    }
  | {
      event: "connection.update";
      status: "qr" | "connected" | "disconnected";
      jid?: string;
      reason?: string;
    };

const BACKOFF_MS = [1000, 3000, 9000];

export async function postWebhook(body: WebhookEvent): Promise<boolean> {
  if (!env.WEBHOOK_URL) return false;
  for (let attempt = 0; attempt < BACKOFF_MS.length; attempt++) {
    try {
      const res = await fetch(env.WEBHOOK_URL, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-wa-token": env.WA_SHARED_TOKEN,
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(10_000),
      });
      if (res.ok) return true;
      // não logamos o body — conteúdo de mensagem não vai para log
      console.warn(`webhook ${body.event} → HTTP ${res.status} (tentativa ${attempt + 1})`);
    } catch (err) {
      console.warn(`webhook ${body.event} falhou (tentativa ${attempt + 1}): ${String(err)}`);
    }
    await new Promise((resolve) => setTimeout(resolve, BACKOFF_MS[attempt]));
  }
  return false;
}
