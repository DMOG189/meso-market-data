// MapleCharts Discord bot: /price <item> answers with the player price, NPC shop price and a link.
// Runs as a Supabase Edge Function (Deno). Set the function secret DISCORD_PUBLIC_KEY (from the Discord
// application's General Information page) and turn off "Verify JWT" for this function, because Discord signs
// its requests with its own key instead of a Supabase token.

const PUBLIC_KEY = Deno.env.get("DISCORD_PUBLIC_KEY") ?? "";
const SITE = (Deno.env.get("SITE_URL") ?? "https://dmog189.github.io/meso-market-data/").replace(/\/?$/, "/");
const FEE = 0.03;

type Item = { n: string; s: string; k?: string; p?: number; p25?: number; p75?: number; sold?: number; u?: string };
type Static = Record<string, { buy?: [number, string, string, number | null]; sell?: number }>;

let cache: { at: number; items: Item[]; statics: Static; updated: string } | null = null;

async function data() {
  if (cache && Date.now() - cache.at < 10 * 60_000) return cache;
  const [market, statics] = await Promise.all([
    fetch(SITE + "market.json").then((r) => r.json()),
    fetch(SITE + "items-static.json").then((r) => r.json()).catch(() => ({})),
  ]);
  cache = { at: Date.now(), items: (market.items as Item[]).filter((i) => i.s), statics, updated: String(market.updatedAt ?? "").slice(0, 10) };
  return cache;
}

function norm(name: string) {
  return name.toLowerCase().replace(/&/g, " and ").replace(/['’`]/g, "").split(/[^a-z0-9]+/).filter(Boolean)
    .map((w) => (w.length > 3 && w.endsWith("s") && !w.endsWith("ss") ? w.slice(0, -1) : w)).join(" ");
}
const num = (n?: number | null) => (n == null ? "—" : Math.round(n).toLocaleString("en-US"));

function find(items: Item[], q: string): Item[] {
  const k = norm(q);
  if (!k) return [];
  const exact = items.filter((i) => norm(i.n) === k);
  if (exact.length) return exact;
  const starts = items.filter((i) => norm(i.n).startsWith(k));
  const has = items.filter((i) => norm(i.n).includes(k) && !starts.includes(i));
  return [...starts, ...has].sort((a, b) => (b.sold ?? 0) - (a.sold ?? 0));
}

function hex(h: string) {
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.substr(i * 2, 2), 16);
  return out;
}
async function verified(req: Request, body: string) {
  const sig = req.headers.get("X-Signature-Ed25519"), ts = req.headers.get("X-Signature-Timestamp");
  if (!sig || !ts || !PUBLIC_KEY) return false;
  try {
    const key = await crypto.subtle.importKey("raw", hex(PUBLIC_KEY), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify("Ed25519", key, hex(sig), new TextEncoder().encode(ts + body));
  } catch {
    return false;
  }
}
const reply = (obj: unknown) => new Response(JSON.stringify(obj), { headers: { "Content-Type": "application/json" } });

function embed(it: Item, st: Static[string] | undefined, updated: string) {
  const fields = [];
  if (it.p != null) fields.push({ name: "Players (median)", value: `${num(it.p)} mesos${it.u === "set" ? " per set" : ""}\nTypical ${num(it.p25)}–${num(it.p75)}`, inline: true });
  if (st?.buy) fields.push({ name: "NPC shop", value: `${num(st.buy[0])} mesos\n${st.buy[1] ?? ""}${st.buy[2] ? ", " + st.buy[2] : ""}`, inline: true });
  if (st?.sell != null) fields.push({ name: "NPC buys for", value: `${num(st.sell)} mesos`, inline: true });
  if (it.sold) fields.push({ name: "Sold", value: num(it.sold), inline: true });
  let tip = "";
  if (st?.buy && it.p != null) {
    const profit = it.p * (1 - FEE) - st.buy[0];
    if (profit > 0 && profit / st.buy[0] >= 0.05) tip = `Buy from the NPC and sell to players: about +${num(profit)} each after the ${FEE * 100}% fee.`;
    else if (it.p < st.buy[0]) tip = "Cheaper from players than from the NPC.";
  } else if (st?.sell != null && it.p != null) {
    tip = it.p * (1 - FEE) > st.sell ? "Selling to players pays more than the NPC." : "Selling to the NPC pays more.";
  }
  return {
    title: it.n, url: `${SITE}item/${it.s}/`, color: 0xff8c1a, description: tip || undefined, fields,
    thumbnail: { url: `${SITE}og/${it.s}.png` },
    footer: { text: `MapleCharts · MapleStory Classic World · prices from ${updated}` },
  };
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("MapleCharts Discord bot is running.");
  const body = await req.text();
  if (!(await verified(req, body))) return new Response("Bad signature", { status: 401 });
  const i = JSON.parse(body);
  if (i.type === 1) return reply({ type: 1 });
  const d = await data();
  const query = String(i.data?.options?.find((o: { name: string }) => o.name === "item")?.value ?? "");
  if (i.type === 4) {
    const choices = find(d.items, query).slice(0, 25).map((it) => ({ name: it.n.slice(0, 100), value: it.n.slice(0, 100) }));
    return reply({ type: 8, data: { choices } });
  }
  if (i.type === 2 && i.data?.name === "price") {
    const hits = find(d.items, query);
    if (!hits.length) return reply({ type: 4, data: { content: `No item matches “${query}”. Try part of the name.`, flags: 64 } });
    const it = hits[0];
    const more = hits.slice(1, 4).map((h) => h.n);
    return reply({ type: 4, data: {
      embeds: [embed(it, d.statics[norm(it.n)], d.updated)],
      content: more.length ? `Also matching: ${more.join(", ")}` : undefined,
    } });
  }
  return reply({ type: 4, data: { content: "Unknown command.", flags: 64 } });
});
