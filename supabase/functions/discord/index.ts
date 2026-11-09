// MapleCharts Discord bot (slash commands only; it never reads messages and never runs as a user account).
//   /price <item>                       player price, NPC shop price, open listings and a link
//   /sell <item> <price> [qty] [hours] [note]   post a sell listing on the MapleCharts trade board (expires in 6–24 h)
//   /buy  <item> <price> [qty] [hours] [note]   post a buy listing
//   /sold [listing]                     close one of your listings (or all of them)
//   /mylistings                         your open listings
//   /alert <item> <price> [direction]   DM me when a listing or the market goes under/over a price (price 0 removes)
//   /forget                             delete everything MapleCharts stored for your Discord account
// Runs as a Supabase Edge Function (Deno). Function secrets:
//   DISCORD_PUBLIC_KEY  from the Discord application's General Information page
//   BOT_RPC_SECRET      a long random value; the same value is stored in Supabase Vault as 'discord_bot_rpc'
//                       (see supabase/discord_trade.sql). Without it, /price still works and trade commands say "not set up".
//   TRADE_GUILDS        optional: comma-separated server ids where trade commands work (empty = any server that invited the bot)
// SUPABASE_URL and SUPABASE_ANON_KEY are provided by Supabase automatically. Only the public anon key is used.
// Turn off "Verify JWT" for this function, because Discord signs its requests with its own key instead of a Supabase token.

const PUBLIC_KEY = Deno.env.get("DISCORD_PUBLIC_KEY") ?? "";
const SITE = (Deno.env.get("SITE_URL") ?? "https://dmog189.github.io/meso-market-data/").replace(/\/?$/, "/");
const FEE = 0.03;
const SB_URL = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/$/, "");
const SB_KEY = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const RPC_SECRET = Deno.env.get("BOT_RPC_SECRET") ?? "";
const TRADE_GUILDS = (Deno.env.get("TRADE_GUILDS") ?? "").split(",").map((x) => x.trim()).filter(Boolean);

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
  const fields: { name: string; value: string; inline: boolean }[] = [];
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

// ---------------------------------------------------------------- trade board
type Opt = { name: string; value?: unknown; focused?: boolean; options?: Opt[] };
const opt = (i: { data?: { options?: Opt[] } }, name: string) => i.data?.options?.find((o) => o.name === name)?.value;

/** "1.5m", "250k", "1,200,000", "3b" → mesos. */
function parsePrice(text: string): number | null {
  const m = String(text).trim().toLowerCase().replace(/,/g, "").match(/^(\d+(?:\.\d+)?)\s*([kmb]?)$/);
  if (!m) return null;
  const n = parseFloat(m[1]) * ({ "": 1, k: 1e3, m: 1e6, b: 1e9 } as Record<string, number>)[m[2]];
  return n >= 0 && n < 1e11 ? Math.round(n) : null;
}

async function rpc(fn: string, args: Record<string, unknown>) {
  const r = await fetch(`${SB_URL}/rest/v1/rpc/${fn}`, {
    method: "POST",
    headers: { apikey: SB_KEY, Authorization: `Bearer ${SB_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ p_secret: RPC_SECRET, ...args }),
  });
  const text = await r.text();
  if (!r.ok) {
    let msg = text;
    try { msg = JSON.parse(text).message ?? text; } catch { /* plain text */ }
    throw new Error(msg);
  }
  return text ? JSON.parse(text) : null;
}

/** Open listings for an item (public data, same as the website shows). */
async function openListings(slug: string) {
  if (!SB_URL || !SB_KEY) return [];
  const q = `${SB_URL}/rest/v1/listings?select=side,price,qty,discord_name,source&item_slug=eq.${encodeURIComponent(slug)}` +
    `&status=eq.open&expires_at=gt.${encodeURIComponent(new Date().toISOString())}&order=price.asc&limit=50`;
  try {
    const r = await fetch(q, { headers: { apikey: SB_KEY, Authorization: `Bearer ${SB_KEY}` } });
    return r.ok ? (await r.json()) as { side: string; price: number; qty: number }[] : [];
  } catch {
    return [];
  }
}

const say = (content: string, publicMsg = false) => reply({ type: 4, data: { content, flags: publicMsg ? undefined : 64, allowed_mentions: { parse: [] } } });
const userOf = (i: any) => i.member?.user ?? i.user ?? {};
const nameOf = (i: any) => String(i.member?.nick || userOf(i).global_name || userOf(i).username || "Player").replace(/[@#`*_~|<>]/g, "").slice(0, 40);
const inTime = (iso: string) => {
  const h = Math.max(0, Math.round((Date.parse(iso) - Date.now()) / 3600000));
  return h >= 1 ? `in ${h} h` : "within the hour";
};

async function trade(i: any, d: Awaited<ReturnType<typeof data>>) {
  const cmd = i.data.name as string;
  const uid = String(userOf(i).id ?? "");
  if (!RPC_SECRET || !SB_URL || !SB_KEY) return say("Trading from Discord isn't switched on yet. Use the website for now: " + SITE);
  if (!uid) return say("Couldn't tell who you are. Try again.");
  if ((cmd === "sell" || cmd === "buy") && !i.guild_id) return say("Use /" + cmd + " in a server channel, so other players see it.");
  if (TRADE_GUILDS.length && i.guild_id && !TRADE_GUILDS.includes(String(i.guild_id)) && cmd !== "forget" && cmd !== "sold" && cmd !== "mylistings") {
    return say("Trading commands work in the MapleCharts Discord server.");
  }
  try {
    if (cmd === "sell" || cmd === "buy") {
      const query = String(opt(i, "item") ?? "");
      const it = find(d.items, query)[0];
      if (!it) return say(`No item matches “${query}”. Pick one from the list.`);
      const price = parsePrice(String(opt(i, "price") ?? ""));
      if (!price) return say("Price looks wrong. Examples: 250k, 1.5m, 1200000.");
      // Far from the market is still allowed (prices move), but the post says so.
      const far = it.p && (price < it.p / 4 || price > it.p * 4) ? " ⚠️ far from the market price" : "";
      const qty = Number(opt(i, "qty") ?? 1);
      const hours = Number(opt(i, "hours") ?? 24);
      const note = String(opt(i, "note") ?? "");
      const url = i.guild_id && i.channel_id ? `https://discord.com/channels/${i.guild_id}/${i.channel_id}` : null;
      const res = await rpc("discord_post_listing", {
        p_discord_id: uid, p_name: nameOf(i), p_side: cmd, p_slug: it.s, p_item: it.n, p_price: price,
        p_qty: qty, p_hours: hours, p_note: note, p_url: url,
      });
      const market = it.p != null ? ` · market median ${num(it.p)}${far}` : "";
      return reply({ type: 4, data: {
        allowed_mentions: { parse: [] },
        embeds: [{
          title: `${cmd === "sell" ? "Selling" : "Buying"}: ${it.n}`,
          url: `${SITE}item/${it.s}/`, color: cmd === "sell" ? 0x4caf50 : 0xff8c1a,
          description: `**${num(price)}** mesos${qty > 1 ? ` × ${num(qty)}` : ""} · by ${nameOf(i)}${market}` +
            (note ? `\n> ${note.replace(/\n/g, " ").slice(0, 140)}` : "") +
            `\nExpires ${inTime(res.expires_at)} · close it with /sold`,
          footer: { text: "MapleCharts trade board · asking price, not a sale price" },
        }],
      } });
    }
    if (cmd === "mylistings") {
      const rows = (await rpc("discord_my_listings", { p_discord_id: uid })) as any[];
      if (!rows.length) return say("You have no open listings. Post one with /sell or /buy.");
      return say(rows.map((r) => `#${r.id} ${r.side === "sell" ? "Selling" : "Buying"} **${r.item}** for ${num(r.price)}${r.qty > 1 ? ` × ${r.qty}` : ""} · ends ${inTime(r.expires_at)}`).join("\n"));
    }
    if (cmd === "sold") {
      const v = opt(i, "listing");
      const id = v == null || v === "all" ? null : Number(v);
      const n = await rpc("discord_close_listing", { p_discord_id: uid, p_id: Number.isFinite(id) ? id : null });
      return say(n ? `Closed ${n} listing${n > 1 ? "s" : ""}. Good trade!` : "Nothing to close. /mylistings shows what's open.");
    }
    if (cmd === "alert") {
      const query = String(opt(i, "item") ?? "");
      const it = find(d.items, query)[0];
      if (!it) return say(`No item matches “${query}”. Pick one from the list.`);
      const raw = String(opt(i, "price") ?? "");
      const price = raw.trim() === "0" ? 0 : parsePrice(raw);
      if (price == null) return say("Price looks wrong. Examples: 250k, 1.5m. Use 0 to remove the alert.");
      const dir = String(opt(i, "direction") ?? "below") === "above" ? "above" : "below";
      const res = await rpc("discord_set_alert", { p_discord_id: uid, p_slug: it.s, p_item: it.n, p_direction: dir, p_price: price });
      const list = (res.alerts as any[]).map((a) => `• ${a.item} ${a.direction} ${num(a.price)}`).join("\n");
      return say((price ? `Alert set: I'll DM you when **${it.n}** is ${dir} ${num(price)} mesos (a new listing, or the daily market price).`
        : `Removed your alerts for **${it.n}**.`) + `\nYour alerts (${res.count}/${res.limit}):\n${list || "none"}\nMake sure you allow DMs from this server.`);
    }
    if (cmd === "forget") {
      const res = await rpc("discord_forget", { p_discord_id: uid });
      return say(`Done. Deleted ${res.listings} listings, ${res.alerts} alerts and ${res.messages} queued messages for your Discord account.`);
    }
  } catch (e) {
    const m = String((e as Error).message || e);
    if (/Not allowed/.test(m)) return say("Trading from Discord isn't switched on yet.");
    return say(m.length < 200 ? m : "Something went wrong. Try again later.");
  }
  return say("Unknown command.");
}

async function soldChoices(i: any) {
  const uid = String(userOf(i).id ?? "");
  if (!uid || !RPC_SECRET) return [];
  try {
    const rows = (await rpc("discord_my_listings", { p_discord_id: uid })) as any[];
    return [{ name: "All my listings", value: "all" }].concat(rows.slice(0, 24).map((r) => ({
      name: `#${r.id} ${r.side} ${r.item} ${num(r.price)}`.slice(0, 100), value: String(r.id),
    })));
  } catch {
    return [];
  }
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
    if (i.data?.name === "sold") return reply({ type: 8, data: { choices: await soldChoices(i) } });
    const choices = find(d.items, query).slice(0, 25).map((it) => ({ name: it.n.slice(0, 100), value: it.n.slice(0, 100) }));
    return reply({ type: 8, data: { choices } });
  }
  if (i.type === 2 && ["sell", "buy", "sold", "mylistings", "alert", "forget"].includes(i.data?.name)) return trade(i, d);
  if (i.type === 2 && i.data?.name === "price") {
    const hits = find(d.items, query);
    if (!hits.length) return reply({ type: 4, data: { content: `No item matches “${query}”. Try part of the name.`, flags: 64 } });
    const it = hits[0];
    const more = hits.slice(1, 4).map((h) => h.n);
    const e = embed(it, d.statics[norm(it.n)], d.updated);
    const open = await openListings(it.s);
    const sells = open.filter((l) => l.side === "sell"), buys = open.filter((l) => l.side === "buy");
    if (open.length) {
      e.fields.push({ name: "Trade board", value: (sells.length ? `${sells.length} selling, from ${num(sells[0].price)}` : "nobody selling") +
        (buys.length ? `\n${buys.length} buying, up to ${num(Math.max(...buys.map((b) => b.price)))}` : ""), inline: false });
    }
    return reply({ type: 4, data: {
      embeds: [e],
      content: more.length ? `Also matching: ${more.join(", ")}` : undefined,
    } });
  }
  return reply({ type: 4, data: { content: "Unknown command.", flags: 64 } });
});
