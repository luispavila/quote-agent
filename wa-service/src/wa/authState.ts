/**
 * Backend do auth state do Baileys:
 * - DATABASE_URL setada → Postgres (tabela wa_auth) — sobrevive a restarts sem disco pago
 * - senão → arquivos em AUTH_DIR (disco persistente / volume local)
 *
 * Serializa com BufferJSON (as chaves Signal do Baileys carregam Buffers) e
 * converte app-state-sync-key de volta para proto na leitura.
 */

import { access, rm } from "node:fs/promises";
import path from "node:path";

import {
  BufferJSON,
  initAuthCreds,
  proto,
  useMultiFileAuthState,
  type AuthenticationCreds,
  type AuthenticationState,
  type SignalDataTypeMap,
} from "baileys";
import pg from "pg";

import { env } from "../env.js";

export type AuthHandle = {
  state: AuthenticationState;
  saveCreds: () => Promise<void>;
};

let pool: pg.Pool | undefined;
let tableReady = false;

function getPool(): pg.Pool {
  if (!pool) {
    pool = new pg.Pool({
      connectionString: env.DATABASE_URL,
      // URL externa do Render exige TLS; a interna (host dpg-...) não
      ssl: env.DATABASE_URL?.includes(".render.com") ? { rejectUnauthorized: false } : undefined,
      max: 5,
    });
  }
  return pool;
}

async function ensureTable(): Promise<void> {
  if (tableReady) return;
  await getPool().query("CREATE TABLE IF NOT EXISTS wa_auth (id TEXT PRIMARY KEY, data TEXT NOT NULL)");
  tableReady = true;
}

async function pgRead(id: string): Promise<string | undefined> {
  await ensureTable();
  const res = await getPool().query<{ data: string }>("SELECT data FROM wa_auth WHERE id = $1", [id]);
  return res.rows[0]?.data;
}

async function pgWrite(id: string, data: string): Promise<void> {
  await ensureTable();
  await getPool().query(
    "INSERT INTO wa_auth (id, data) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
    [id, data],
  );
}

async function usePostgresAuthState(): Promise<AuthHandle> {
  await ensureTable();
  const credsRaw = await pgRead("creds");
  const creds: AuthenticationCreds = credsRaw
    ? JSON.parse(credsRaw, BufferJSON.reviver)
    : initAuthCreds();

  const state: AuthenticationState = {
    creds,
    keys: {
      get: async <T extends keyof SignalDataTypeMap>(type: T, ids: string[]) => {
        const result: { [id: string]: SignalDataTypeMap[T] } = {};
        if (ids.length === 0) return result;
        await ensureTable();
        const keys = ids.map((id) => `${type}-${id}`);
        const res = await getPool().query<{ id: string; data: string }>(
          "SELECT id, data FROM wa_auth WHERE id = ANY($1)",
          [keys],
        );
        for (const row of res.rows) {
          const id = row.id.slice(String(type).length + 1);
          let value = JSON.parse(row.data, BufferJSON.reviver);
          if (type === "app-state-sync-key" && value) {
            value = proto.Message.AppStateSyncKeyData.fromObject(value);
          }
          result[id] = value as SignalDataTypeMap[T];
        }
        return result;
      },
      set: async (data) => {
        const client = await getPool().connect();
        try {
          await client.query("BEGIN");
          for (const [type, byId] of Object.entries(data)) {
            for (const [id, value] of Object.entries(byId ?? {})) {
              const key = `${type}-${id}`;
              if (value) {
                await client.query(
                  "INSERT INTO wa_auth (id, data) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                  [key, JSON.stringify(value, BufferJSON.replacer)],
                );
              } else {
                await client.query("DELETE FROM wa_auth WHERE id = $1", [key]);
              }
            }
          }
          await client.query("COMMIT");
        } catch (err) {
          await client.query("ROLLBACK");
          throw err;
        } finally {
          client.release();
        }
      },
    },
  };

  return {
    state,
    saveCreds: async () => {
      await pgWrite("creds", JSON.stringify(state.creds, BufferJSON.replacer));
    },
  };
}

export async function getAuthState(): Promise<AuthHandle> {
  if (env.DATABASE_URL) return usePostgresAuthState();
  const { state, saveCreds } = await useMultiFileAuthState(env.AUTH_DIR);
  return { state, saveCreds };
}

export async function hasCreds(): Promise<boolean> {
  if (env.DATABASE_URL) {
    try {
      return (await pgRead("creds")) !== undefined;
    } catch {
      return false;
    }
  }
  try {
    await access(path.join(env.AUTH_DIR, "creds.json"));
    return true;
  } catch {
    return false;
  }
}

export async function clearAuth(): Promise<void> {
  if (env.DATABASE_URL) {
    await ensureTable();
    await getPool().query("DELETE FROM wa_auth");
    return;
  }
  await rm(env.AUTH_DIR, { recursive: true, force: true });
}
