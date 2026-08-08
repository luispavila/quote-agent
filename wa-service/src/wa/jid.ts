// Utilitários PUROS de JID/telefone (testáveis sem socket) — padrão herdado do funniie-baileys.

export function onlyDigits(v: string): string {
  return v.replace(/\D/g, "");
}

/**
 * Candidatos de telefone BR: o WhatsApp às vezes registra celulares sem o 9º dígito.
 * 55 + DDD + 8 dígitos → tenta também com o 9; 55 + DDD + 9 dígitos → tenta também sem.
 */
export function brPhoneCandidates(digits: string): string[] {
  const d = onlyDigits(digits);
  const withCountry = d.startsWith("55") ? d : `55${d}`;
  const candidates = new Set<string>([withCountry]);
  const local = withCountry.slice(2);
  if (local.length === 10 && ["6", "7", "8", "9"].includes(local[2] ?? "")) {
    candidates.add(`55${local.slice(0, 2)}9${local.slice(2)}`);
  }
  if (local.length === 11 && local[2] === "9") {
    candidates.add(`55${local.slice(0, 2)}${local.slice(3)}`);
  }
  return [...candidates];
}

export function isGroupJid(jid: string | undefined | null): boolean {
  return !!jid && jid.endsWith("@g.us");
}

/** status@broadcast, newsletters etc. — sem isso, story vira "mensagem recebida". */
export function isIgnorableJid(jid: string | undefined | null): boolean {
  if (!jid) return true;
  return (
    jid === "status@broadcast" || jid.endsWith("@broadcast") || jid.endsWith("@newsletter")
  );
}

export function jidToPhone(jid: string): string | undefined {
  if (!jid.endsWith("@s.whatsapp.net")) return undefined;
  return jid.split("@")[0]?.split(":")[0];
}

/** LID (id opaco pós-privacidade): longo (>=15 dígitos) e não começa com 55. */
export function looksLikeLid(digits: string): boolean {
  return digits.length >= 15 && !digits.startsWith("55");
}

type MessageContent = {
  conversation?: string | null;
  extendedTextMessage?: { text?: string | null } | null;
  imageMessage?: { caption?: string | null } | null;
  videoMessage?: { caption?: string | null } | null;
  documentMessage?: { caption?: string | null } | null;
} | null | undefined;

export function extractText(message: MessageContent): string | undefined {
  if (!message) return undefined;
  const text =
    message.conversation ??
    message.extendedTextMessage?.text ??
    message.imageMessage?.caption ??
    message.videoMessage?.caption ??
    message.documentMessage?.caption;
  return text ?? undefined;
}
