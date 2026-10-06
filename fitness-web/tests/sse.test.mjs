import assert from "node:assert/strict";
import test from "node:test";
import { SseParser } from "../src/sse.ts";

test("UTF-8 and SSE frames can split at every byte", () => {
  const input = 'event: answer.delta\ndata: {"text":"健身🏋️"}\n\n';
  const parser = new SseParser();
  const decoder = new TextDecoder();
  const events = [];
  for (const byte of new TextEncoder().encode(input)) events.push(...parser.push(decoder.decode(new Uint8Array([byte]), { stream: true })));
  assert.deepEqual(events, [{event: "answer.delta", data: '{"text":"健身🏋️"}'}]);
});

test("CRLF split and heartbeat comments preserve multiline data", () => {
  const parser = new SseParser();
  assert.deepEqual(parser.push(": heartbeat\r"), []);
  assert.deepEqual(parser.push("\n\r\nevent: completed\r\ndata: one\r"), []);
  assert.deepEqual(parser.push("\ndata: two\r\n\r\n"), [{event:"completed", data:"one\ntwo"}]);
});

test("unterminated frames have a bounded buffer", () => {
  assert.throws(() => new SseParser().push("x".repeat(1_048_577)));
});
