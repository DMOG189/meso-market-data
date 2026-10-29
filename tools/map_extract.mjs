// Collects map layout data for the MapleCharts NPC maps from the maplestory.io game-data API:
// town platforms (footholds), ladders, portals, NPC positions and world-map town spots.
// Only coordinates and names are kept; no game images are downloaded.
// Usage: node tools/map_extract.mjs data/npc-shops.json data/maps.json [version]
import fs from "node:fs";

const [shopsPath = "data/npc-shops.json", outPath = "data/maps.json", VERSION = "62"] = process.argv.slice(2);
const API = `https://maplestory.io/api/GMS/${VERSION}`;
const TOWNS = ["Amherst", "Southperry", "Lith Harbor", "Henesys", "Ellinia", "Perion", "Kerning City", "Sleepywood", "Florina Beach"];
const WORLD = { "Maple Island": "WorldMap00", "Victoria Island": "WorldMap10" };
const NOWHERE = 999999999;

const shops = JSON.parse(fs.readFileSync(shopsPath, "utf8"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let calls = 0;

async function get(path, tries = 4) {
  for (let i = 0; i < tries; i++) {
    try {
      calls++;
      const r = await fetch(API + path, { headers: { "User-Agent": "MapleCharts map data (github.com/DMOG189/meso-market-data)" } });
      if (r.status === 404) return null;
      if (r.ok) return await r.json();
    } catch (e) { /* retry */ }
    await sleep(800 * (i + 1));
  }
  console.warn("gave up on", path);
  return null;
}

const nameCache = new Map();
async function mapName(id) {
  if (!nameCache.has(id)) nameCache.set(id, get(`/map/${id}/name`).then((j) => (j ? j.name : null)));
  return nameCache.get(id);
}
const npcCache = new Map();
async function npcName(id) {
  if (!npcCache.has(id)) npcCache.set(id, get(`/npc/${id}/name`).then((j) => (j ? j.name : null)));
  return npcCache.get(id);
}
const mapCache = new Map();
async function rawMap(id) {
  if (!mapCache.has(id)) mapCache.set(id, get(`/map/${id}`));
  return mapCache.get(id);
}

async function pool(items, n, fn) {
  const out = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: n }, async () => {
    while (next < items.length) { const i = next++; out[i] = await fn(items[i], i); }
  }));
  return out;
}

const norm = (s) => String(s || "").toLowerCase().replace(/defen[cs]e/g, "defense").replace(/\bshop\b/g, "store")
  .replace(/[^a-z0-9]+/g, " ").trim();

// Turn the API's map object into a compact layout record.
async function layout(id) {
  const m = await rawMap(id);
  if (!m) return null;
  const fh = Object.values(m.footholds || {}).filter(Boolean)
    .map((f) => [f.x1, f.y1, f.x2, f.y2]);
  const mm = m.miniMap || {};
  let b;
  if (mm.width && mm.height) b = { l: -mm.centerX, t: -mm.centerY, r: mm.width - mm.centerX, b: mm.height - mm.centerY };
  if (!b || !(b.r > b.l)) {
    const xs = fh.flatMap((f) => [f[0], f[2]]), ys = fh.flatMap((f) => [f[1], f[3]]);
    b = { l: Math.min(...xs) - 100, r: Math.max(...xs) + 100, t: Math.min(...ys) - 400, b: Math.max(...ys) + 100 };
  }
  const portals = (m.portals || []).filter((p) => p && p.toMap && p.toMap !== NOWHERE && p.toMap !== id);
  const seen = new Set();
  const doors = [];
  for (const p of portals) {
    const key = p.toMap + ":" + Math.round(p.x / 40);
    if (seen.has(key)) continue;
    seen.add(key);
    doors.push({ x: p.x, y: p.y, to: p.toMap, toName: await mapName(p.toMap) });
  }
  const npcs = await pool((m.npcs || []).filter((n) => n && !n.hidden), 6, async (n) => ({ x: n.x, y: n.y, id: n.id, name: await npcName(n.id) }));
  return {
    id, name: m.name, street: m.streetName || "", town: !!m.isTown, bounds: b, fh,
    lad: (m.ladderRopes || []).map((l) => [l.x, Math.min(l.y1, l.y2), Math.max(l.y1, l.y2), l.isLadder ? 1 : 0]),
    doors, npcs: npcs.filter((n) => n.name),
  };
}

const out = { source: "maplestory.io", license: "Layout coordinates only; no game artwork.", version: "GMS v" + VERSION,
  collected: new Date().toISOString().slice(0, 10), world: {}, towns: {}, maps: {}, shops: [] };

// 1. World map spots (positions only).
const top = await get("/map/worldmap/WorldMap");
out.world.overview = (top?.maps || []).map((s) => ({ x: s.spot.x, y: s.spot.y, name: s.description || s.title || "", maps: (s.mapNumbers || []).slice(0, 3) }));
const spotMaps = new Set();
for (const [island, wm] of Object.entries(WORLD)) {
  const w = await get(`/map/worldmap/${wm}`);
  const spots = (w?.maps || []).map((s) => ({ x: s.spot.x, y: s.spot.y, type: s.type, title: s.description || s.title || "", maps: s.mapNumbers || [] }));
  spots.forEach((s) => s.maps.forEach((id) => spotMaps.add(id)));
  out.world[island] = { spots };
}

// 2. Find each town's map id by name among the world-map spots.
const ids = [...spotMaps];
const names = await pool(ids, 8, (id) => mapName(id));
const byName = new Map();
ids.forEach((id, i) => { if (names[i] && !byName.has(names[i])) byName.set(names[i], id); });
for (const island of Object.keys(WORLD)) {
  for (const s of out.world[island].spots) {
    const town = TOWNS.find((t) => s.maps.some((id) => nameCache.get(id) && byName.get(t) === id));
    if (town) s.town = town;
    s.maps = s.maps.slice(0, 1);
  }
}
for (const t of TOWNS) {
  const id = byName.get(t);
  if (!id) { console.warn("town not found on world map:", t); continue; }
  const island = Object.keys(WORLD).find((k) => out.world[k].spots.some((s) => s.town === t)) || "";
  out.towns[t] = { id, island };
}

// 3. For every shop, walk from its town through portals (up to 3 steps) to find the shop's map and NPC.
function addMap(lay) { if (lay && !out.maps[lay.id]) out.maps[lay.id] = lay; }
for (const t of Object.keys(out.towns)) {
  const townId = out.towns[t].id;
  const want = shops.filter((s) => s.town === t);
  const townLay = await layout(townId);
  addMap(townLay);
  const parent = new Map([[townId, null]]);
  let frontier = [townId];
  const found = new Map();
  for (let depth = 0; depth <= 3 && frontier.length && found.size < want.length; depth++) {
    const lays = await pool(frontier, 6, (id) => (id === townId ? townLay : layout(id)));
    const nextIds = [];
    for (const lay of lays) {
      if (!lay) continue;
      for (const s of want) {
        if (found.has(s.npc)) continue;
        const npc = lay.npcs.find((n) => norm(n.name) === norm(s.npc));
        const mapMatch = norm(lay.name) === norm(s.map);
        if (npc || mapMatch) found.set(s.npc, { lay, npc: npc || null, mapMatch });
      }
      if (parent.size < 70) for (const d of lay.doors) if (!parent.has(d.to)) { parent.set(d.to, lay.id); nextIds.push(d.to); }
    }
    frontier = nextIds;
  }
  for (const s of want) {
    const f = found.get(s.npc);
    const rec = { npc: s.npc, town: t, map: s.map, townMap: townId };
    if (f) {
      addMap(f.lay);
      rec.mapId = f.lay.id;
      rec.mapName = f.lay.name;
      if (f.npc) rec.at = { x: f.npc.x, y: f.npc.y };
      // Path back to the town and the door on the town map that leads toward the shop.
      const path = [];
      for (let id = f.lay.id; id != null; id = parent.get(id)) path.unshift(id);
      rec.path = path;
      if (path.length > 1) {
        const firstHop = path[1];
        const door = townLay.doors.find((d) => d.to === firstHop);
        if (door) rec.entry = { x: door.x, y: door.y };
        // Keep the in-between maps too, for "go through" directions.
        for (const id of path.slice(1, -1)) addMap(await layout(id));
      }
    } else {
      console.warn("not found:", s.npc, "/", s.map, "in", t);
    }
    out.shops.push(rec);
  }
}

fs.writeFileSync(outPath, JSON.stringify(out));
const found = out.shops.filter((s) => s.mapId).length;
console.log(`maps: ${Object.keys(out.maps).length}, shops located: ${found}/${out.shops.length}, api calls: ${calls}, bytes: ${fs.statSync(outPath).size}`);
