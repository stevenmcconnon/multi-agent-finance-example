// Regenerates docs/diagrams/multi-agent-rag-turn-annotated.png from the running app, so the README's first
// image always shows the real interface. Usage (stack up, corpus ingested, Playwright available):
//   node docs/diagrams/make_hero.mjs [http://localhost:3000] [output.png]
// It signs in as alice, asks the first example question, then draws numbered callouts beside the phase cards.
import { chromium } from "playwright";

const BASE = process.argv[2] || "http://localhost:3000";
const OUT = process.argv[3] || new URL("./multi-agent-rag-turn-annotated.png", import.meta.url).pathname;
const QUESTION = "What risks does Apple describe about its supply chain?";
// [card eyebrow text, callout text, colour, side]
const CALLOUTS = [
  ["USER QUESTION", "A person asks in plain English", "#2563eb", "left"],
  ["AUTHORIZE", "Guard applies policy to the verified identity", "#0f766e", "right"],
  ["PLAN", "Planner picks from the tools this role may use", "#7c3aed", "left"],
  ["RETRIEVE + ACT", "Researcher returns only evidence this person may read", "#0f766e", "right"],
  ["DRAFT + VERIFY", "Writer drafts from the evidence, Judge checks every claim", "#7c3aed", "left"],
  ["RESULT", "A cited answer, or an honest no-answer", "#15803d", "right"],
];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1760, height: 1000 }, deviceScaleFactor: 1 });
await page.goto(BASE + "/");
await page.getByRole("button", { name: /alice/i }).first().click();
await page.waitForURL(/chat/);
await page.getByRole("button", { name: QUESTION, exact: true }).click();
await page.waitForFunction(() => { const i = document.querySelector("form input"); return i && !i.disabled; }, null, { timeout: 180000 });
await page.waitForTimeout(800);

const height = await page.evaluate((callouts) => {
  window.scrollTo(0, 0);
  const BANNER = 96;
  document.body.style.position = "relative";
  document.body.style.paddingTop = BANNER + "px";
  const banner = document.createElement("div");
  banner.textContent = "One multi-agent turn, with every control visible";
  banner.style.cssText = `position:absolute;top:0;left:0;right:0;height:${BANNER}px;display:flex;align-items:center;justify-content:center;background:#0f172a;color:#fff;font:700 40px system-ui,sans-serif;letter-spacing:-0.01em;z-index:50`;
  document.body.appendChild(banner);
  const find = (label) => [...document.querySelectorAll("p")].find((p) => p.textContent.trim() === label)?.closest("section, div.rounded-xl, article") ?? null;
  let bottom = 0;
  callouts.forEach(([label, text, colour, side], i) => {
    const card = find(label);
    if (!card) return;
    const r = card.getBoundingClientRect();
    const top = r.top + window.scrollY, mid = top + Math.min(r.height, 220) / 2;
    card.style.outline = `3px solid ${colour}`;
    card.style.outlineOffset = "3px";
    const W = 300, GAP = 34;
    const x = side === "left" ? r.left - GAP - W : r.right + GAP;
    const box = document.createElement("div");
    box.innerHTML = `<span style="flex:none;width:40px;height:40px;border-radius:9999px;background:${colour};color:#fff;display:flex;align-items:center;justify-content:center;font:700 22px system-ui">${i + 1}</span><span>${text}</span>`;
    box.style.cssText = `position:absolute;left:${x}px;top:${mid - 44}px;width:${W}px;box-sizing:border-box;padding:14px 16px;display:flex;gap:12px;align-items:center;background:#fff;border:3px solid ${colour};border-radius:16px;font:600 19px/1.25 system-ui,sans-serif;color:#0f172a;box-shadow:0 6px 18px rgba(15,23,42,.12);z-index:40`;
    document.body.appendChild(box);
    const line = document.createElement("div");
    line.style.cssText = `position:absolute;top:${mid}px;height:3px;background:${colour};z-index:39;left:${side === "left" ? r.left - GAP : r.right + 3}px;width:${GAP - 3}px`;
    document.body.appendChild(line);
    if (label === "RESULT") bottom = top + r.height;
  });
  return Math.ceil(bottom + 28);
}, CALLOUTS);

await page.setViewportSize({ width: 1760, height }); // a clip taller than the viewport is cut off otherwise
await page.evaluate(() => window.scrollTo(0, 0));
await page.screenshot({ path: OUT, clip: { x: 0, y: 0, width: 1760, height } });
await browser.close();
console.log(`wrote ${OUT} (1760x${height})`);
