// Downloads item icons for MapleCharts (once per item) from the maplestory.io game-data API and saves them in
// icons/<id>.png, plus data/icons.json mapping each item name (normalized) to its icon id.
// Item ids come from MapleClassic Wiki data (Classic World names), with a name match against the game item list
// for anything the wiki doesn't cover. Item icons are Nexon's game art, shown on this fan site with credit.
// Usage: node tools/icon_extract.mjs market.json
import fs from "node:fs";

const MARKET = process.argv[2] || "market.json";
// Classic World's own data first (its items have their own ids), then older global versions.
const SOURCES = ["MCW/1", "MCW/CBT2", "GMS/83", "GMS/95", "GMS/255"];
const API = "https://maplestory.io/api/";
const UA = { "User-Agent": "MapleCharts icons (github.com/DMOG189/meso-market-data)" };

function norm(name) {
  return String(name || "").toLowerCase().replace(/&/g, " and ").replace(/['’`]/g, "").split(/[^a-z0-9]+/).filter(Boolean)
    .map((w) => (w.length > 3 && w.endsWith("s") && !w.endsWith("ss") ? w.slice(0, -1) : w)).join(" ");
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function get(url, kind = "json", tries = 4) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, { headers: UA });
      if (r.status === 404) return null;
      if (r.ok) return kind === "json" ? await r.json() : Buffer.from(await r.arrayBuffer());
    } catch (e) { /* retry */ }
    await sleep(700 * (i + 1));
  }
  return null;
}
async function pool(items, n, fn) {
  let next = 0;
  await Promise.all(Array.from({ length: n }, async () => { while (next < items.length) { const i = next++; await fn(items[i], i); } }));
}

// Names the app shows: market items, NPC shop items, recipe parts and products.
const market = JSON.parse(fs.readFileSync(MARKET, "utf8"));
const wiki = JSON.parse(fs.readFileSync("data/wiki.json", "utf8"));
const npcNames = fs.existsSync("data/npc-item-names.json") ? JSON.parse(fs.readFileSync("data/npc-item-names.json", "utf8")) : [];
const names = new Set([...market.items.map((i) => i.n), ...npcNames]);
// Recipes: [skill, level, group, product, qty, rate, note, [[ingredient, qty], ...]]
for (const r of wiki.recipes || []) {
  if (typeof r[3] === "string") names.add(r[3]);
  for (const p of r[7] || []) if (typeof p[0] === "string") names.add(p[0]);
}
// Monster drops: [name, level, hp, exp, map, region, quest, [[item, rate, kills, quest], ...]]
for (const m of wiki.monsters || []) for (const d of m[7] || []) if (typeof d[0] === "string" && d[0] !== "Meso") names.add(d[0]);

// 1. Ids from the wiki (Classic World names).
const ids = new Map();
const wikiId = new Map();
for (const [name, v] of Object.entries(wiki.items || {})) if (v && v[0]) wikiId.set(norm(name), v[0]);
for (const n of names) { const id = wikiId.get(norm(n)); if (id) ids.set(norm(n), id); }

// 2. Anything left: match by name in the game item lists.
const missing = [...names].filter((n) => !ids.has(norm(n)));
if (missing.length) {
  const byName = new Map();
  for (const v of SOURCES) {
    const list = (await get(API + v + "/item")) || [];
    for (const it of list) { const k = norm(it.name); if (k && !byName.has(k)) byName.set(k, it.id); }
  }
  for (const n of missing) { const id = byName.get(norm(n)); if (id) ids.set(norm(n), id); }
}

// 3. Download icons we don't have yet.
fs.mkdirSync("icons", { recursive: true });
const want = [...new Set(ids.values())].filter((id) => !fs.existsSync(`icons/${id}.png`));
let got = 0, failed = 0;
await pool(want, 6, async (id) => {
  for (const v of SOURCES) {
    const png = await get(`${API}${v}/item/${id}/icon`, "bin");
    if (png && png.length > 60 && png[0] === 0x89 && png[1] === 0x50) { fs.writeFileSync(`icons/${id}.png`, png); got++; return; }
  }
  failed++;
});

const out = {};
for (const [k, id] of [...ids.entries()].sort()) if (fs.existsSync(`icons/${id}.png`)) out[k] = id;
fs.writeFileSync("data/icons.json", JSON.stringify(out));
console.log(`names: ${names.size}, with id: ${ids.size}, downloaded now: ${got}, failed: ${failed}, with icon: ${Object.keys(out).length}`);
