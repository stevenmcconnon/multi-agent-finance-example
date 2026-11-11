import assert from "node:assert/strict";
import { test } from "node:test";
import { sseFramer } from "./sse.ts";

test("frames events split across chunks, ignores comments, handles CRLF and trailing block", () => {
  const got: { type: string; data: string }[] = [];
  const f = sseFramer((b) => got.push(b));
  f.push('event: status\ndata: {"node":"plan"}\n\n: keep-alive\n\nevent: tok');
  f.push('en\r\ndata: {"text":"a"}\r\n\r\ndata: {"x":1}\ndata: {"y":2}\n\nevent: answer\ndata: {"ok":true}');
  f.end();
  assert.deepEqual(got, [
    { type: "status", data: '{"node":"plan"}' },
    { type: "token", data: '{"text":"a"}' },
    { type: "message", data: '{"x":1}\n{"y":2}' },
    { type: "answer", data: '{"ok":true}' },
  ]);
});
