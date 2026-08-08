import assert from "node:assert/strict";
import { test } from "node:test";

import {
  brPhoneCandidates,
  extractText,
  isGroupJid,
  isIgnorableJid,
  jidToPhone,
  looksLikeLid,
} from "../src/wa/jid.js";

test("brPhoneCandidates insere 9º dígito em celular BR de 8 dígitos", () => {
  assert.deepEqual(brPhoneCandidates("5511987654321"), ["5511987654321", "551187654321"]);
  assert.deepEqual(brPhoneCandidates("551187654321"), ["551187654321", "5511987654321"]);
  assert.deepEqual(brPhoneCandidates("11987654321"), ["5511987654321", "551187654321"]);
});

test("isGroupJid / isIgnorableJid", () => {
  assert.equal(isGroupJid("123-456@g.us"), true);
  assert.equal(isGroupJid("5511987654321@s.whatsapp.net"), false);
  assert.equal(isIgnorableJid("status@broadcast"), true);
  assert.equal(isIgnorableJid("abc@newsletter"), true);
  assert.equal(isIgnorableJid("5511987654321@s.whatsapp.net"), false);
});

test("jidToPhone e looksLikeLid", () => {
  assert.equal(jidToPhone("5511987654321@s.whatsapp.net"), "5511987654321");
  assert.equal(jidToPhone("123@g.us"), undefined);
  assert.equal(looksLikeLid("123456789012345"), true);
  assert.equal(looksLikeLid("5511987654321"), false);
});

test("extractText cobre conversation, extended e captions", () => {
  assert.equal(extractText({ conversation: "oi" }), "oi");
  assert.equal(extractText({ extendedTextMessage: { text: "olá" } }), "olá");
  assert.equal(extractText({ imageMessage: { caption: "foto" } }), "foto");
  assert.equal(extractText({}), undefined);
});
