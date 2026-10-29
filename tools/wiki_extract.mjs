// Collects game data from MapleClassic Wiki (https://mapleclassic.wiki, CC BY-NC-SA 4.0) for MapleCharts:
// every item's NPC sell price, potion recovery, scroll success rates, crafting recipes and monster drops.
// Run: node tools/wiki_extract.mjs data/wiki.json
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

const OUT = process.argv[2] || 'data/wiki.json';
const API = 'https://mapleclassic.wiki/api.php';
const UA = 'MapleCharts data collector (+https://github.com/DMOG189/meso-market-data)';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(params) {
  const url = new URL(API);
  url.search = new URLSearchParams({ format: 'json', formatversion: '2', ...params }).toString();
  for (let attempt = 1; ; attempt++) {
    try {
      const res = await fetch(url, { headers: { 'User-Agent': UA } });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (e) {
      if (attempt >= 4) throw e;
      await sleep(2000 * attempt);
    }
  }
}

async function members(cat) {
  let out = [], cont = {};
  for (;;) {
    const j = await api({ action: 'query', list: 'categorymembers', cmtitle: 'Category:' + cat, cmlimit: '500', cmnamespace: '0', ...cont });
    out = out.concat(j.query.categorymembers.map((m) => m.title));
    if (!j.continue) break;
    cont = j.continue;
    await sleep(300);
  }
  return out;
}

async function contents(titles) {
  const res = {};
  for (let i = 0; i < titles.length; i += 50) {
    const j = await api({ action: 'query', prop: 'revisions', rvprop: 'content', rvslots: 'main', titles: titles.slice(i, i + 50).join('|') });
    for (const p of j.query.pages) if (!p.missing && p.revisions) res[p.title] = p.revisions[0].slots.main.content;
    await sleep(300);
  }
  return res;
}

/** Every {{name...}} template in s, with nested templates kept whole. */
function templates(s, name) {
  const out = [];
  let i = 0;
  while ((i = s.indexOf('{{' + name, i)) !== -1) {
    let depth = 0, j = i;
    for (; j < s.length - 1; j++) {
      if (s[j] === '{' && s[j + 1] === '{') { depth++; j++; }
      else if (s[j] === '}' && s[j + 1] === '}') { depth--; j++; if (depth === 0) break; }
    }
    out.push(s.slice(i, j + 1));
    i = j + 1;
  }
  return out;
}

/** Named and positional fields of one template. */
function fields(t) {
  const body = t.slice(2, -2);
  const parts = [];
  let depth = 0, cur = '';
  for (let k = 0; k < body.length; k++) {
    const c = body[k], n = body[k + 1];
    if ((c === '{' && n === '{') || (c === '[' && n === '[')) { depth++; cur += c + n; k++; continue; }
    if ((c === '}' && n === '}') || (c === ']' && n === ']')) { depth--; cur += c + n; k++; continue; }
    if (c === '|' && depth === 0) { parts.push(cur); cur = ''; continue; }
    cur += c;
  }
  parts.push(cur);
  const f = { _name: parts[0].trim() };
  parts.slice(1).forEach((p, idx) => {
    const e = p.indexOf('=');
    if (e > -1) f[p.slice(0, e).trim()] = p.slice(e + 1).trim();
    else f['_' + idx] = p.trim();
  });
  return f;
}

const num = (v) => { const n = parseFloat(String(v || '').replace(/,/g, '')); return isFinite(n) ? n : 0; };

function parseIngredients(s) {
  return String(s || '').split(/,(?![^{]*\}\})/).map((p) => {
    const m = p.match(/\{\{i\|([^}|]+)[^}]*\}\}\s*(?:&times;|×|x)?\s*(\d+)?/);
    return m ? [m[1].trim(), +(m[2] || 1)] : null;
  }).filter(Boolean);
}

async function main() {
  const out = {
    collected: new Date().toISOString().slice(0, 10),
    source: 'https://mapleclassic.wiki',
    license: 'CC BY-NC-SA 4.0',
    items: {}, recovery: {}, scrolls: {}, recipes: [], monsters: []
  };

  // Items: category, NPC sell price, potion recovery, scroll rate and stats
  const itemTitles = await members('Items');
  const itemPages = await contents(itemTitles);
  for (const [title, s] of Object.entries(itemPages)) {
    const ib = templates(s, 'Infobox item')[0];
    if (!ib) continue;
    const f = fields(ib);
    const name = f.name_en || title;
    out.items[name] = [num(f.id), f.category || '', f.subcategory || '', f.sell_price === undefined || f.sell_price === '' ? null : num(f.sell_price)];
    const rec = [num(f.recovery_hp), num(f.recovery_mp), num(f.recovery_hp_pct), num(f.recovery_mp_pct)];
    if (rec.some((v) => v > 0)) out.recovery[name] = rec;
    const sr = (f.description_en || '').match(/Success rate:\s*(\d+)%/);
    if (sr || /scroll/i.test(f.subcategory || '')) {
      const stats = {};
      Object.keys(f).filter((k) => k.startsWith('stat_')).forEach((k) => { stats[k.slice(5)] = num(f[k]); });
      out.scrolls[name] = [sr ? +sr[1] : null, stats];
    }
  }

  // Crafting recipes
  const profs = (await members('Crafting')).filter((p) => p !== 'Crafting');
  const profPages = await contents(profs);
  for (const s of Object.values(profPages)) {
    for (const t of templates(s, 'Recipe row')) {
      const f = fields(t);
      out.recipes.push([f.profession || '', num(f.level), f.category || '', f.output_item || '', num(f.output_count) || 1, num(f.meso_cost), f.catalyst || '', parseIngredients(f.ingredients)]);
    }
  }

  // Monsters and what they drop
  const monTitles = await members('Monsters');
  const monPages = await contents(monTitles);
  for (const [title, s] of Object.entries(monPages)) {
    const ib = templates(s, 'Infobox monster')[0];
    if (!ib) continue;
    const f = fields(ib);
    const drops = templates(s, 'Drop|').map((d) => {
      const g = fields(d);
      return [g.item_id || '', num(g.drop_rate), num(g.sample_kills), g.quest_flag === '1' ? 1 : 0];
    }).filter((d) => d[0]);
    out.monsters.push([f.name_en || title, num(f.level), num(f.hp), num(f.exp), f.primary_location || '', f.region || '', f.boss === 'yes' ? 1 : 0, drops]);
  }

  const counts = {
    items: Object.keys(out.items).length, recovery: Object.keys(out.recovery).length, scrolls: Object.keys(out.scrolls).length,
    recipes: out.recipes.length, monsters: out.monsters.length, drops: out.monsters.reduce((a, m) => a + m[7].length, 0)
  };
  console.log(JSON.stringify(counts));
  if (counts.items < 500 || counts.recipes < 50) throw new Error('Too little data came back from the wiki; keeping the previous file.');
  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, JSON.stringify(out));
}

main().catch((e) => { console.error(e); process.exit(1); });
