// Collects item details for MapleCharts from MapleStory Classic World's game data (maplestory.io API):
// NPC sell price, requirements, upgrade slots, stat bonuses, scroll success rate, potion recovery, description.
// Only for items the site shows (the ids in data/icons.json). Output: data/items.json, keyed by item id.
// Usage: node tools/item_info.mjs [--all]   (--all refreshes every item; otherwise only new ones are fetched)
import fs from "node:fs";

const API = "https://maplestory.io/api/";
const SOURCES = ["MCW/1", "MCW/CBT2"];
const UA = { "User-Agent": "MapleCharts item data (github.com/DMOG189/meso-market-data)" };
const ALL = process.argv.includes("--all");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function get(url, tries = 4) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, { headers: UA });
      if (r.status === 404) return null;
      if (r.ok) return await r.json();
    } catch (e) { /* retry */ }
    await sleep(700 * (i + 1));
  }
  return null;
}
async function pool(items, n, fn) {
  let next = 0;
  await Promise.all(Array.from({ length: n }, async () => { while (next < items.length) { const i = next++; await fn(items[i], i); } }));
}

const STAT_KEYS = { incSTR: "STR", incDEX: "DEX", incINT: "INT", incLUK: "LUK", incMHP: "HP", incMMP: "MP", incPAD: "ATT", incMAD: "MATT",
  incPDD: "DEF", incMDD: "MDEF", incACC: "ACC", incEVA: "AVOID", incSpeed: "SPEED", incJump: "JUMP", incCraft: "HANDS" };
const JOBS = { 1: "Warrior", 2: "Magician", 4: "Bowman", 8: "Thief", 16: "Pirate" };

function compact(j) {
  const m = j.metaInfo || {};
  const out = { n: (j.description || {}).name || "" };
  if (m.price != null) out.s = m.price;
  if (m.cash) out.cash = 1;
  if (m.reqLevel || m.reqLevelEquip) out.lv = m.reqLevel || m.reqLevelEquip;
  const rq = ["reqSTR", "reqDEX", "reqINT", "reqLUK"].map((k) => m[k] || 0);
  if (rq.some(Boolean)) out.rq = rq;
  if (m.reqJob) out.job = Object.keys(JOBS).filter((b) => m.reqJob & b).map((b) => JOBS[b]);
  if (m.tuc) out.slots = m.tuc;
  const st = {};
  for (const [k, label] of Object.entries(STAT_KEYS)) if (m[k]) st[label] = m[k];
  if (Object.keys(st).length) out.st = st;
  if (m.success != null) out.sc = m.success;
  const cs = m.consumeSpec || j.spec || {};
  for (const k of ["hp", "mp", "hpR", "mpR", "time"]) if (cs[k]) out[k] = cs[k];
  if (m.slotMax) out.max = m.slotMax;
  const d = String((j.description || {}).description || "").replace(/\\n/g, "\n").replace(/#[a-zA-Z]/g, "").trim();
  if (d) out.d = d.slice(0, 400);
  const t = j.typeInfo || {};
  if (t.subCategory) out.cat = [t.overallCategory, t.category, t.subCategory].filter(Boolean);
  return out;
}

const icons = JSON.parse(fs.readFileSync("data/icons.json", "utf8"));
const ids = [...new Set(Object.values(icons))];
const out = fs.existsSync("data/items.json") && !ALL ? JSON.parse(fs.readFileSync("data/items.json", "utf8")) : {};
const want = ids.filter((id) => !out[id]);
let got = 0, missing = 0;
await pool(want, 6, async (id) => {
  for (const s of SOURCES) {
    const j = await get(`${API}${s}/item/${id}`);
    if (j && j.metaInfo) { out[id] = compact(j); got++; return; }
  }
  missing++;
});
fs.writeFileSync("data/items.json", JSON.stringify(out));
console.log(`items: ${Object.keys(out).length}, fetched now: ${got}, not found: ${missing}`);
