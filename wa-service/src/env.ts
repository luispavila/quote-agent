import "dotenv/config"; // NÃO `import "dotenv"` — bug silencioso herdado da referência

import { z } from "zod";

const schema = z.object({
  PORT: z.coerce.number().default(3001),
  WA_SHARED_TOKEN: z.string().min(16, "WA_SHARED_TOKEN precisa de >=16 chars"),
  WEBHOOK_URL: z.string().url().optional(), // ex.: https://quote-agent.onrender.com/webhooks/wa
  AUTH_DIR: z.string().default("/data/auth"),
  LOG_LEVEL: z.enum(["debug", "info", "warn", "error"]).default("info"),
});

export const env = Object.freeze(schema.parse(process.env));
