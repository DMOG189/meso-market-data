// Collects hunting maps for MapleCharts from MapleStory Classic World's game data (maplestory.io API):
// for every map, which monsters spawn where, plus the platforms to draw it. Coordinates and names only.
// Output: data/hunt.json (index: monster -> maps, map -> name/area/island spot) and data/huntmaps/<id>.json.
// Usage: node tools/hunt_extract.mjs
import fs from "node:fs";

const SRC = "MCW/1", SRC2 = "MCW/CBT2";
const API = "https://maplestory.io/api/";
const UA = { "User-Agent": "MapleCharts hunting maps (github.com/DMOG189/meso-market-data)" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function get(path, src = SRC, tries = 4) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(API + src + path, { headers: UA });
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
const mobNames = new Map();
async function mobName(id) {
  if (!mobNames.has(id)) mobNames.set(id, (async () => {
    const j = (await get(`/mob/${id}/name`)) || (await get(`/mob/${id}/name`, SRC2));
    return j ? j.name : null;
  })());
  return mobNames.get(id);
}

function mergeFootholds(fh) {
  const segs = fh.map((f) => f.map((v) => Math.round(v))).map(([x1, y1, x2, y2]) => (x1 > x2 ? [x2, y2, x1, y1] : [x1, y1, x2, y2]))
    .sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const out = [];
  for (const s of segs) {
    const last = out[out.length - 1];
    if (last && s[1] === s[3] && last[1] === last[3] && last[1] === s[1] && Math.abs(last[2] - s[0]) <= 2) last[2] = Math.max(last[2], s[2]);
    else out.push(s);
  }
  return out.flat();
}

const list = [...((await get("/map")) || []), ...((await get("/map", SRC2)) || [])];
const seen = new Set();
const maps = list.filter((m) => m && !seen.has(m.id) && seen.add(m.id));
console.log("maps to scan:", maps.length);

// World-map spots, to put each hunting map on the island map.
const spotOf = new Map();
for (const src of [SRC2, SRC]) {
  const names = ((await get("/map/worldmap", src)) || []).filter((n) => !/^Search/.test(n));
  for (const wm of names) {
    const w = await get(`/map/worldmap/${wm}`, src);
    for (const s of w?.maps || []) for (const id of s.mapNumbers || []) if (!spotOf.has(id)) spotOf.set(id, { wm, x: s.spot.x, y: s.spot.y });
  }
}

fs.mkdirSync("data/huntmaps", { recursive: true });
const index = { source: "maplestory.io (MapleStory Classic World data)", collected: new Date().toISOString().slice(0, 10), mobs: {}, maps: {} };
await pool(maps, 6, async (m) => {
  const j = (await get(`/map/${m.id}`)) || (await get(`/map/${m.id}`, SRC2));
  if (!j || !j.mobs || !j.mobs.length) return;
  const spawns = new Map();
  for (const mob of j.mobs) {
    if (!mob || mob.hidden) continue;
    const name = await mobName(mob.id);
    if (!name) continue;
    if (!spawns.has(name)) spawns.set(name, []);
    spawns.get(name).push(Math.round(mob.x), Math.round(mob.y));
  }
  if (!spawns.size) return;
  const fh = Object.values(j.footholds || {}).filter(Boolean).map((f) => [f.x1, f.y1, f.x2, f.y2]);
  const xs = fh.flatMap((f) => [f[0], f[2]]), ys = fh.flatMap((f) => [f[1], f[3]]);
  const mm = j.miniMap || {};
  let l = Math.min(...xs) - 160, r = Math.max(...xs) + 160;
  if (mm.width) { l = Math.max(l, -mm.centerX); r = Math.min(r, mm.width - mm.centerX); }
  const b = [Math.round(l), Math.round(Math.min(...ys) - 380), Math.round(r), Math.round(Math.max(...ys) + 90)];
  const doors = (j.portals || []).filter((p) => p && p.toMap && p.toMap !== 999999999 && p.toMap !== m.id).map((p) => [Math.round(p.x), Math.round(p.y), p.toMap]);
  fs.writeFileSync(`data/huntmaps/${m.id}.json`, JSON.stringify({
    id: m.id, n: j.name || m.name, st: j.streetName || "", b, f: mergeFootholds(fh),
    l: (j.ladderRopes || []).flatMap((x) => [Math.round(x.x), Math.round(Math.min(x.y1, x.y2)), Math.round(Math.max(x.y1, x.y2)), x.isLadder ? 1 : 0]),
    d: doors, m: [...spawns.entries()],
  }));
  const spot = spotOf.get(m.id);
  index.maps[m.id] = { n: j.name || m.name, st: j.streetName || "", ...(spot ? { wm: spot.wm, sx: spot.x, sy: spot.y } : {}) };
  for (const [name, pts] of spawns) {
    if (!index.mobs[name]) index.mobs[name] = [];
    index.mobs[name].push([m.id, pts.length / 2]);
  }
});
for (const k of Object.keys(index.mobs)) index.mobs[k].sort((a, b) => b[1] - a[1]);
fs.writeFileSync("data/hunt.json", JSON.stringify(index));
console.log(`hunting maps: ${Object.keys(index.maps).length}, monsters: ${Object.keys(index.mobs).length}`);
