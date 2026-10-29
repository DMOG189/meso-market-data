// Collects map layout data for the MapleCharts NPC maps from the maplestory.io game-data API, using
// MapleStory Classic World's own data: town platforms (footholds), ladders, doors, NPC positions and
// world-map town spots. Only coordinates and names are kept; no game images are downloaded.
// Usage: node tools/map_extract.mjs data/npc-shops.json data/maps.json [source] [fallback source]
//   source defaults to MCW/CBT2 (complete door data); the fallback (MCW/1) fills in NPCs added later.
import fs from "node:fs";

const [shopsPath = "data/npc-shops.json", outPath = "data/maps.json", SRC = "MCW/CBT2", SRC2 = "MCW/1"] = process.argv.slice(2);
const API = "https://maplestory.io/api/";
const TOWNS = { "Maple Island": ["Amherst", "Southperry"],
  "Victoria Island": ["Lith Harbor", "Henesys", "Ellinia", "Perion", "Kerning City", "Sleepywood", "Florina Beach"] };
const NOWHERE = 999999999;

const shops = JSON.parse(fs.readFileSync(shopsPath, "utf8"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let calls = 0;

async function get(path, src = SRC, tries = 4) {
  for (let i = 0; i < tries; i++) {
    try {
      calls++;
      const r = await fetch(API + src + path, { headers: { "User-Agent": "MapleCharts map data (github.com/DMOG189/meso-market-data)" } });
      if (r.status === 404) return null;
      if (r.ok) return await r.json();
    } catch (e) { /* retry */ }
    await sleep(800 * (i + 1));
  }
  console.warn("gave up on", src + path);
  return null;
}
// Try the main source, then the fallback.
async function get2(path) { return (await get(path, SRC)) || (SRC2 ? await get(path, SRC2) : null); }

const cache = new Map();
function once(key, fn) { if (!cache.has(key)) cache.set(key, fn()); return cache.get(key); }
const mapName = (id) => once("mn" + id, () => get2(`/map/${id}/name`).then((j) => (j ? j.name : null)));
const npcName = (id) => once("nn" + id, () => get2(`/npc/${id}/name`).then((j) => (j ? j.name : null)));
const rawMap = (id) => once("m" + id, () => get2(`/map/${id}`));

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

async function layout(id) {
  const m = await rawMap(id);
  if (!m) return null;
  const fh = Object.values(m.footholds || {}).filter(Boolean).map((f) => [f.x1, f.y1, f.x2, f.y2]);
  const mm = m.miniMap || {};
  let b;
  if (mm.width && mm.height) b = { l: -mm.centerX, t: -mm.centerY, r: mm.width - mm.centerX, b: mm.height - mm.centerY };
  if (!b || !(b.r > b.l)) {
    const xs = fh.flatMap((f) => [f[0], f[2]]), ys = fh.flatMap((f) => [f[1], f[3]]);
    b = { l: Math.min(...xs) - 100, r: Math.max(...xs) + 100, t: Math.min(...ys) - 400, b: Math.max(...ys) + 100 };
  }
  const seen = new Set();
  const doors = [];
  for (const p of (m.portals || []).filter((p) => p && p.toMap && p.toMap !== NOWHERE && p.toMap !== id)) {
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

const out = { source: "maplestory.io (MapleStory Classic World data)", license: "Layout coordinates only; no game artwork.",
  version: "Classic World " + SRC.split("/")[1], collected: new Date().toISOString().slice(0, 10), world: {}, towns: {}, maps: {}, shops: [] };

// 1. Town map ids by name.
const mapList = [...((await get("/map", SRC)) || []), ...((SRC2 && (await get("/map", SRC2))) || [])];
const idsByName = new Map();
for (const m of mapList) { const k = norm(m.name); if (!idsByName.has(k)) idsByName.set(k, []); if (!idsByName.get(k).includes(m.id)) idsByName.get(k).push(m.id); }
for (const [island, towns] of Object.entries(TOWNS)) {
  for (const t of towns) {
    const cands = idsByName.get(norm(t)) || [];
    let pick = null;
    for (const id of cands) { const m = await rawMap(id); if (m && m.isTown) { pick = id; break; } }
    if (!pick && cands.length) pick = cands[0];
    if (pick) out.towns[t] = { id: pick, island }; else console.warn("town not found:", t);
  }
}

// 2. World map spots (positions only). Each world map is matched to an island by the towns on it.
const worldList = ((await get("/map/worldmap", SRC)) || []).filter((n) => !/^Search/.test(n));
for (const wm of worldList) {
  const w = await get(`/map/worldmap/${wm}`, SRC);
  const spots = (w?.maps || []).map((s) => ({ x: s.spot.x, y: s.spot.y, type: s.type, title: s.description || s.title || "", maps: s.mapNumbers || [] }));
  for (const [island, towns] of Object.entries(TOWNS)) {
    const townIds = towns.map((t) => out.towns[t] && out.towns[t].id).filter(Boolean);
    const hits = spots.filter((s) => s.maps.some((id) => townIds.includes(id)));
    if (hits.length < 2 || out.world[island]) continue;
    for (const s of spots) {
      const t = towns.find((n) => out.towns[n] && s.maps.includes(out.towns[n].id));
      if (t) s.town = t;
      s.maps = s.maps.slice(0, 1);
    }
    out.world[island] = { spots, from: wm };
  }
}

// 3. Locate every shop: find the NPC (or the shop's map by name), then walk the doors back to the town.
function addMap(lay) { if (lay && !out.maps[lay.id]) out.maps[lay.id] = lay; }
const npcList = [...((await get("/npc", SRC)) || []), ...((SRC2 && (await get("/npc", SRC2))) || [])];
const npcIdsByName = new Map();
for (const n of npcList) { const k = norm(n.name); if (!npcIdsByName.has(k)) npcIdsByName.set(k, []); if (!npcIdsByName.get(k).includes(n.id)) npcIdsByName.get(k).push(n.id); }

async function walkToTown(from, townId, limit = 90) {
  const prev = new Map([[from, null]]);
  let frontier = [from], fetched = 0;
  for (let depth = 0; depth < 7 && frontier.length; depth++) {
    const next = [];
    for (const id of frontier) {
      if (id === townId) {
        const path = [];
        for (let x = id; x != null; x = prev.get(x)) path.push(x);
        return path;
      }
      if (fetched++ > limit) return null;
      const lay = await layout(id);
      if (!lay) continue;
      for (const d of lay.doors) if (!prev.has(d.to)) { prev.set(d.to, id); next.push(d.to); }
    }
    frontier = next;
  }
  return null;
}

for (const t of Object.keys(out.towns)) {
  const townId = out.towns[t].id;
  const townLay = await layout(townId);
  addMap(townLay);
  for (const s of shops.filter((x) => x.town === t)) {
    const rec = { npc: s.npc, town: t, map: s.map, townMap: townId };
    const cands = [];
    for (const nid of npcIdsByName.get(norm(s.npc)) || []) {
      const info = await get2(`/npc/${nid}`);
      for (const f of info?.foundAt || []) if (f && f.id) cands.push({ id: f.id, npcId: nid, nameMatch: norm(f.name) === norm(s.map) });
    }
    for (const mid of idsByName.get(norm(s.map)) || []) cands.push({ id: mid, npcId: null, nameMatch: true });
    const score = (c) => (c.nameMatch && c.npcId ? 3 : c.nameMatch ? 2 : 1);
    cands.sort((a, b) => score(b) - score(a));
    let best = null;
    const tried = new Set();
    for (const c of cands) {
      if (tried.has(c.id) || tried.size >= 8) continue;
      tried.add(c.id);
      const path = c.id === townId ? [townId] : await walkToTown(c.id, townId);
      if (path) { best = { ...c, path }; break; }
    }
    if (!best) {
      // Fallback: walk outward from the town (up to 4 doors away) looking for the map or the NPC by name.
      const prev = new Map([[townId, null]]);
      let frontier = [townId], budget = 120;
      search: for (let depth = 0; depth <= 4 && frontier.length; depth++) {
        const next = [];
        for (const id of frontier) {
          if (budget-- <= 0) break search;
          const lay = await layout(id);
          if (!lay) continue;
          const npc = lay.npcs.find((n) => norm(n.name) === norm(s.npc));
          if (npc || norm(lay.name) === norm(s.map)) {
            const path = [];
            for (let x = id; x != null; x = prev.get(x)) path.unshift(x);
            best = { id, npcId: npc ? npc.id : null, path };
            break search;
          }
          for (const d of lay.doors) if (!prev.has(d.to)) { prev.set(d.to, id); next.push(d.to); }
        }
        frontier = next;
      }
    }
    if (best) {
      const lay = await layout(best.id);
      addMap(lay);
      rec.mapId = best.id;
      rec.mapName = lay?.name || s.map;
      const npc = lay?.npcs.find((n) => (best.npcId ? n.id === best.npcId : norm(n.name) === norm(s.npc))) ||
        lay?.npcs.find((n) => norm(n.name) === norm(s.npc));
      if (npc) rec.at = { x: npc.x, y: npc.y };
      if (best.path.length > 1) {
        // Prefer the town door that leads furthest along the way.
        for (let k = best.path.length - 1; k >= 2; k--) {
          if (townLay.doors.some((d) => d.to === best.path[k])) { best.path = [townId, ...best.path.slice(k)]; break; }
        }
        const hop = best.path[1];
        let door = townLay.doors.find((d) => d.to === hop);
        if (!door) {
          const raw = await rawMap(hop);
          const back = (raw?.portals || []).find((p) => p && p.toMap === townId && p.toName);
          const townRaw = await rawMap(townId);
          const tp = back && (townRaw?.portals || []).find((p) => p && p.portalName === back.toName);
          if (tp) door = { x: tp.x, y: tp.y };
        }
        if (door) rec.entry = { x: door.x, y: door.y };
      }
      rec.path = best.path;
      rec.pathNames = await Promise.all(best.path.map((id) => mapName(id)));
    } else {
      console.warn("not found:", s.npc, "/", s.map, "in", t);
    }
    out.shops.push(rec);
  }
}

fs.writeFileSync(outPath, JSON.stringify(out));
const found = out.shops.filter((s) => s.mapId).length;
const placed = out.shops.filter((s) => s.entry || (s.at && s.mapId === s.townMap)).length;
console.log(`maps: ${Object.keys(out.maps).length}, shops located: ${found}/${out.shops.length}, placed on a town map: ${placed}, api calls: ${calls}, bytes: ${fs.statSync(outPath).size}`);
