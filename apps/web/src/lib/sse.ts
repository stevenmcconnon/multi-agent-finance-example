// Pure SSE framing: feed text chunks, get complete `{type, data}` blocks back. No deps, so node --test can run it.
export type SseBlock = { type: string; data: string };

export function parseSseBlock(block: string): SseBlock | null {
  let type = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return data.length ? { type, data: data.join("\n") } : null; // null = comment / keep-alive
}

export function sseFramer(onBlock: (b: SseBlock) => void) {
  let buf = "";
  return {
    push(text: string) {
      buf = (buf + text).replace(/\r\n/g, "\n");
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const b = parseSseBlock(buf.slice(0, idx));
        buf = buf.slice(idx + 2);
        if (b) onBlock(b);
      }
    },
    end() {
      const b = buf.trim() ? parseSseBlock(buf) : null;
      buf = "";
      if (b) onBlock(b);
    },
  };
}
