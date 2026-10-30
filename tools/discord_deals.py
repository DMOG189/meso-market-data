"""Post MapleCharts' daily deals to a Discord channel (a channel webhook).

  DISCORD_DEALS_WEBHOOK=https://discord.com/api/webhooks/... python3 tools/discord_deals.py market.json

Picks, from the latest prices: the best NPC flips (buy from an NPC shop, sell to players after the 3% fee), items
players sell for less than the NPC shop, and the week's biggest risers and fallers. Only items with enough sales.
Without the webhook it prints the message instead (a dry run).
"""
import datetime, json, os, re, sys, urllib.request

MARKET = sys.argv[1] if len(sys.argv) > 1 else "market.json"
CONF = json.load(open("site.config.json")) if os.path.exists("site.config.json") else {}
SITE = (CONF.get("siteUrl") or "https://dmog189.github.io/meso-market-data/").rstrip("/") + "/"
HOOK = os.environ.get("DISCORD_DEALS_WEBHOOK", "").strip()
FEE = 0.03


def norm(name):
    s = str(name or "").lower().replace("&", " and ")
    s = re.sub(r"['’`]", "", s)
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w]
    return " ".join(w[:-1] if (len(w) > 3 and w.endswith("s") and not w.endswith("ss")) else w for w in words)


def num(n):
    if n >= 10_000_000:
        return ("%.2f" % (n / 1_000_000)).rstrip("0").rstrip(".") + "M"
    return "{:,.0f}".format(n)


market = json.load(open(MARKET, encoding="utf-8"))
static = json.load(open("web/items-static.json", encoding="utf-8")) if os.path.exists("web/items-static.json") else {}
low = market.get("lowDataBelow") or 5
items = [it for it in market["items"] if it.get("s") and it.get("p") and (it.get("sold") or 0) >= max(low, 10)]


def link(it):
    return "[%s](%sitem/%s/?ref=discord)" % (it["n"], SITE, it["s"])


flips, cheaper = [], []
for it in items:
    buy = (static.get(norm(it["n"])) or {}).get("buy")
    if not buy:
        continue
    profit = it["p"] * (1 - FEE) - buy[0]
    if profit > 0 and profit / buy[0] >= 0.08:
        flips.append((profit * min(it.get("sold") or 0, 200), profit, buy, it))
    if it["p"] < buy[0] * 0.9:
        cheaper.append(((buy[0] - it["p"]) / buy[0], buy, it))
flips.sort(key=lambda x: -x[0])
cheaper.sort(key=lambda x: -x[0])


def week_change(it):
    h = [d for d in (it.get("h") or []) if d[1]]
    if len(h) < 8:
        return None
    cur, old = h[-1][1], h[-8][1]
    week_sales = sum((d[2] or 0) for d in h[-7:])
    # Equipment prices jump with each item's stats, so the weekly moves only cover stackable things with steady sales.
    if it.get("k") in ("Equips", "Weapons", "Cash Shop") or week_sales < 50 or not old:
        return None
    return (cur / old - 1) * 100, cur


moves = [(c[0], c[1], it) for it in items for c in [week_change(it)] if c]
risers = sorted([m for m in moves if m[0] >= 10], key=lambda m: -m[0])[:5]
fallers = sorted([m for m in moves if m[0] <= -10], key=lambda m: m[0])[:5]

fields = []
if flips:
    fields.append({"name": "🛒 Buy from the NPC, sell to players", "inline": False, "value": "\n".join(
        "%s: NPC %s → players %s (**+%s** each)" % (link(it), num(buy[0]), num(it["p"]), num(profit)) for _, profit, buy, it in flips[:5])})
if cheaper:
    fields.append({"name": "💸 Cheaper from players than the NPC", "inline": False, "value": "\n".join(
        "%s: players %s vs NPC %s (**-%d%%**)" % (link(it), num(it["p"]), num(buy[0]), round(pct * 100)) for pct, buy, it in cheaper[:5])})
if risers:
    fields.append({"name": "📈 Up this week", "inline": True, "value": "\n".join("%s **+%d%%** (%s)" % (link(it), round(p), num(cur)) for p, cur, it in risers)})
if fallers:
    fields.append({"name": "📉 Down this week", "inline": True, "value": "\n".join("%s **%d%%** (%s)" % (link(it), round(p), num(cur)) for p, cur, it in fallers)})
for f in fields:
    f["value"] = f["value"][:1020]

day = (market.get("updatedAt") or datetime.datetime.utcnow().isoformat())[:10]
msg = {
    "username": "MapleCharts",
    "avatar_url": SITE + "icon-192.png",
    "embeds": [{
        "title": "Daily deals · %s" % day,
        "url": SITE + "?ref=discord",
        "description": "MapleStory Classic World: NPC shop prices vs the Free Market. Prices in mesos (player median).",
        "color": 0xFF8C1A,
        "fields": fields or [{"name": "Quiet day", "value": "No standout deals today. Check the site for all prices."}],
        "footer": {"text": "MapleCharts · prices from maplestory-classic.com · use /price in Discord for any item"},
    }],
}

if not HOOK:
    print("No DISCORD_DEALS_WEBHOOK set; this is what would be posted:")
    print(json.dumps(msg, indent=2, ensure_ascii=False))
    sys.exit(0)
req = urllib.request.Request(HOOK, data=json.dumps(msg).encode(), headers={"Content-Type": "application/json", "User-Agent": "MapleCharts deals"})
with urllib.request.urlopen(req, timeout=20) as r:
    print("posted:", r.status)
