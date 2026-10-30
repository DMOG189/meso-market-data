"""Build the MapleCharts website into _site/ for GitHub Pages.

  python3 tools/build_site.py market.json [_site]

Uses web/ (the app, page template, icons; built from the MapleCharts project), data/wiki.json (game data from
MapleClassic Wiki, CC BY-NC-SA 4.0) and the latest market.json from maplestory-classic.com.

Makes: index.html, item/<slug>/index.html for every traded item (and the same in Hebrew under he/), og/<slug>.png share
images, sitemap.xml, robots.txt, 404.html, market.json, item-info.json (game data per item), hunt/ (hunting maps) and
the app files. SITE_URL sets the public address used in links.
"""
import datetime, html, json, os, re, shutil, sys, urllib.parse

MARKET = sys.argv[1] if len(sys.argv) > 1 else "market.json"
OUT = sys.argv[2] if len(sys.argv) > 2 else "_site"
WEB = "web"
CONF = json.load(open("site.config.json")) if os.path.exists("site.config.json") else {}
SITE_URL = (os.environ.get("SITE_URL") or CONF.get("siteUrl") or "https://dmog189.github.io/meso-market-data/").rstrip("/") + "/"
SITE_DOMAIN = (os.environ.get("SITE_DOMAIN") or CONF.get("siteDomain") or "").strip()
FONT = os.environ.get("OG_FONT", "")
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or CONF.get("supabaseUrl") or "").strip()
SUPABASE_KEY = (os.environ.get("SUPABASE_ANON_KEY") or CONF.get("supabaseAnonKey") or "").strip()  # public "anon" key, meant for web pages
# Ads stay off until an AdSense publisher ID (ca-pub-…) and ad unit IDs are set in site.config.json.
ADS_CLIENT = (CONF.get("adsenseClient") or "").strip()
ADS_SLOTS = {k: str(v) for k, v in (CONF.get("adSlots") or {}).items() if v}
# Donation links for the "Support us" window: a card payment page (may contain {amount} and {currency}),
# a PayPal.me name, and any other links [{"name": "...", "url": "..."}]. Empty = "donations open soon".
_d = CONF.get("donate") or {}
DONATE = {k: v for k, v in {"card": (_d.get("card") or "").strip(), "paypal": (_d.get("paypal") or "").strip(),
                            "links": [l for l in (_d.get("links") or []) if l.get("url")]}.items() if v}
DISCORD_APP = str(CONF.get("discordAppId") or "").strip()
DISCORD_INVITE = (CONF.get("discordInvite") or "").strip()
FEE = 0.03

ROOT_PATH = re.sub(r"^https?://[^/]+", "", SITE_URL) or "/"
esc = lambda s: html.escape(str(s if s is not None else ""), quote=True)


def norm(name):
    s = str(name or "").lower().replace("&", " and ")
    s = re.sub(r"['’`]", "", s)
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w]
    words = [w[:-1] if (len(w) > 3 and w.endswith("s") and not w.endswith("ss")) else w for w in words]
    return " ".join(words)


def num(n):
    if n is None:
        return "—"
    if abs(n) >= 10_000_000:
        return ("%.2f" % (n / 1_000_000)).rstrip("0").rstrip(".") + "M"
    return "{:,.0f}".format(n)


market = json.load(open(MARKET, encoding="utf-8"))
wiki = json.load(open("data/wiki.json", encoding="utf-8")) if os.path.exists("data/wiki.json") else {"monsters": []}
static = json.load(open(os.path.join(WEB, "items-static.json"), encoding="utf-8"))
template = open(os.path.join(WEB, "template.html"), encoding="utf-8").read()
_vm = re.search(r"app\.min\.js\?v=([0-9a-f]+)", template)
APP_V = _vm.group(1) if _vm else ""
SPLIT_I18N = os.path.exists(os.path.join(WEB, "i18n-en.js"))
MAPS_V = ""
if os.path.exists(os.path.join(WEB, "maps-data.js")):
    import hashlib
    MAPS_V = hashlib.sha256(open(os.path.join(WEB, "maps-data.js"), "rb").read()).hexdigest()[:10]
import hashlib


def file_hash(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:10] if os.path.exists(path) else ""


# Game data from MapleStory Classic World (tools/item_info.mjs, tools/hunt_extract.mjs)
game_items = json.load(open("data/items.json", encoding="utf-8")) if os.path.exists("data/items.json") else {}
game = {}
for _id, _v in sorted(game_items.items(), key=lambda kv: int(kv[0])):
    if _v.get("n") and norm(_v["n"]) not in game:
        game[norm(_v["n"])] = dict(_v, id=int(_id))
icon_ids = json.load(open("data/icons.json", encoding="utf-8")) if os.path.exists("data/icons.json") else {}
hunt = json.load(open("data/hunt.json", encoding="utf-8")) if os.path.exists("data/hunt.json") else {"mobs": {}, "maps": {}}
hunt_by_norm = {norm(k): (k, v) for k, v in hunt.get("mobs", {}).items()}
HUNT_V = file_hash("data/hunt.json")
INFO_V = file_hash("data/items.json")
updated = market.get("updatedAt", "")
updated_day = updated[:10]
low_data = market.get("lowDataBelow") or 5

drops = {}
for m in wiki.get("monsters", []):
    for d in m[7]:
        drops.setdefault(norm(d[0]), []).append((m, d))

items = [it for it in market["items"] if it.get("s")]
by_cat = {}
for it in items:
    by_cat.setdefault(it.get("k") or "Etc", []).append(it)
for lst in by_cat.values():
    lst.sort(key=lambda x: -(x.get("sold") or 0))


def config(base, slug=None, lang=None):
    # The app first loads the price list without history (smaller), then the daily history right after.
    cfg = {"marketUrl": base + "market-lite.json", "histUrl": base + "market-hist.json",
           "pollMs": 300000, "base": base, "itemPaths": True, "siteUrl": SITE_URL, "sw": True}
    if SPLIT_I18N:
        cfg.update(i18nBase=base, v=APP_V)
    if os.path.isdir("icons"):
        cfg["iconBase"] = base + "icons/"
    if os.path.exists(os.path.join(WEB, "maps-data.js")):
        cfg["mapsUrl"] = base + "maps-data.js?v=" + MAPS_V
    if SUPABASE_URL and SUPABASE_KEY:
        cfg.update(supabaseUrl=SUPABASE_URL, supabaseKey=SUPABASE_KEY)
    if ADS_CLIENT:
        cfg["ads"] = {"client": ADS_CLIENT, "slots": ADS_SLOTS}
    if DONATE:
        cfg["donate"] = DONATE
    if game_items:
        cfg["itemInfoUrl"] = base + "item-info.json?v=" + INFO_V
    if hunt.get("mobs"):
        cfg.update(huntBase=base + "hunt/", huntV=HUNT_V)
    if DISCORD_APP:
        cfg["discordAppId"] = DISCORD_APP
    if DISCORD_INVITE:
        cfg["discordInvite"] = DISCORD_INVITE
    if lang:
        cfg.update(lang=lang, langPath=lang + "/")
    if slug:
        cfg["item"] = slug
    return "<script>window.MESO_CONFIG = %s;</script>" % json.dumps(cfg)


def head(title, desc, url, image, extra=""):
    return ("<title>%s</title>\n"
            '  <meta name="description" content="%s">\n'
            '  <link rel="canonical" href="%s">\n'
            '  <meta property="og:type" content="website">\n'
            '  <meta property="og:site_name" content="MapleCharts">\n'
            '  <meta property="og:title" content="%s">\n'
            '  <meta property="og:description" content="%s">\n'
            '  <meta property="og:url" content="%s">\n'
            '  <meta property="og:image" content="%s">\n'
            '  <meta property="og:image:width" content="1200">\n'
            '  <meta property="og:image:height" content="630">\n'
            '  <meta name="twitter:card" content="summary_large_image">%s') % (
        esc(title), esc(desc), esc(url), esc(title), esc(desc), esc(url), esc(image), extra)


def page(base, head_html, cfg, seo="", brand_tag="h1", lang=None):
    out = template.replace("<!--HEAD-->", head_html).replace("<!--CONFIG-->", cfg)
    if lang == "he":
        out = out.replace('<html lang="en" dir="ltr">', '<html lang="he" dir="rtl" data-lang="he">', 1)
    out = out.replace("<!--BRAND-->", '<%s class="app-title">MapleCharts</%s>' % (brand_tag, brand_tag))
    out = out.replace("<!--SEO-->", seo)
    return out.replace("{{BASE}}", base)


def item_facts(it):
    st = static.get(norm(it["n"]), {})
    buy, sell = st.get("buy"), st.get("sell")
    p = it.get("p")
    return st, buy, sell, p


def item_text(it):
    st, buy, sell, p = item_facts(it)
    name = it["n"]
    unit = " per set" if it.get("u") == "set" else ""
    parts = []
    if p is not None:
        parts.append("Players pay about %s mesos%s for %s (typical %s–%s, %s sold)." % (num(p), unit, name, num(it.get("p25")), num(it.get("p75")), num(it.get("sold"))))
    if buy:
        parts.append("NPC shops sell it for %s mesos (%s%s)." % (num(buy[0]), buy[1] or "NPC", ", " + buy[2] if buy[2] else ""))
    if sell is not None:
        parts.append("NPCs buy it for %s mesos." % num(sell))
    if buy and p is not None:
        profit = p * (1 - FEE) - buy[0]
        if profit > 0 and profit / buy[0] >= 0.05 and (it.get("sold") or 0) >= low_data:
            parts.append("Buying from the NPC and selling to players makes about %s mesos each after the %d%% fee." % (num(profit), FEE * 100))
        elif p < buy[0]:
            parts.append("It is cheaper from players than from the NPC.")
    elif sell is not None and p is not None:
        parts.append("Selling to players pays more." if p * (1 - FEE) > sell else "Selling to the NPC pays more.")
    return " ".join(parts)


def item_text_he(it):
    st, buy, sell, p = item_facts(it)
    name = it["n"]
    unit = " לסט" if it.get("u") == "set" else ""
    parts = []
    if p is not None:
        parts.append("שחקנים משלמים בערך %s מזו%s על %s (בדרך כלל %s–%s, %s נמכרו)." % (num(p), unit, name, num(it.get("p25")), num(it.get("p75")), num(it.get("sold"))))
    if buy:
        parts.append("חנויות NPC מוכרות אותו ב-%s מזו (%s%s)." % (num(buy[0]), buy[1] or "NPC", ", " + buy[2] if buy[2] else ""))
    if sell is not None:
        parts.append("NPC קונים אותו ב-%s מזו." % num(sell))
    if buy and p is not None:
        profit = p * (1 - FEE) - buy[0]
        if profit > 0 and profit / buy[0] >= 0.05 and (it.get("sold") or 0) >= low_data:
            parts.append("קנייה מה-NPC ומכירה לשחקנים מרוויחה בערך %s מזו ליחידה אחרי עמלה של %d%%." % (num(profit), FEE * 100))
        elif p < buy[0]:
            parts.append("זול יותר לקנות משחקנים מאשר מה-NPC.")
    elif sell is not None and p is not None:
        parts.append("מכירה לשחקנים משתלמת יותר." if p * (1 - FEE) > sell else "מכירה ל-NPC משתלמת יותר.")
    return " ".join(parts)


L = {
    "en": {"npcShop": "NPC shop price", "npcAt": "%s mesos at %s", "anNpc": "an NPC", "npcBuys": "NPC buys for", "mesos": "%s mesos",
           "fm": "Player price (median)", "range": "Typical range", "sold": "Sold", "sellers": "Sellers", "updated": "Updated",
           "h1": "%s price", "where": "Where to get %s", "drop": "%s (Lv. %d%s): %.2g%% drop rate", "huntAt": "best hunting map: %s",
           "more": "More %s prices", "details": "Item details", "lv": "Required level", "rq": "Required stats", "jobs": "Jobs",
           "slots": "Upgrade slots", "sc": "Success rate", "restores": "Restores", "lasts": "Lasts", "min": "%d min", "stack": "Stacks up to",
           "source": "Item details from MapleStory Classic World's game data (maplestory.io).", "items": "Items"},
    "he": {"npcShop": "מחיר בחנות NPC", "npcAt": "%s מזו אצל %s", "anNpc": "NPC", "npcBuys": "NPC קונה ב-", "mesos": "%s מזו",
           "fm": "מחיר שחקנים (חציון)", "range": "טווח רגיל", "sold": "נמכרו", "sellers": "מוכרים", "updated": "עודכן",
           "h1": "מחיר %s", "where": "איפה משיגים %s", "drop": "%s (רמה %d%s): סיכוי נפילה %.2g%%", "huntAt": "מפת הציד הכי טובה: %s",
           "more": "עוד מחירים: %s", "details": "פרטי הפריט", "lv": "רמה נדרשת", "rq": "סטטים נדרשים", "jobs": "מקצועות",
           "slots": "סלוטים לשדרוג", "sc": "סיכויי הצלחה", "restores": "משחזר", "lasts": "משך", "min": "%d דק׳", "stack": "נערם עד",
           "source": "פרטי הפריט מתוך נתוני המשחק של MapleStory Classic World (maplestory.io).", "items": "פריטים"},
}
JOBS_HE = {"Warrior": "לוחם", "Magician": "קוסם", "Bowman": "קשת", "Thief": "גנב", "Pirate": "פיראט"}
STAT_ORDER = ["STR", "DEX", "INT", "LUK", "HP", "MP", "ATT", "MATT", "DEF", "MDEF", "ACC", "AVOID", "SPEED", "JUMP", "HANDS"]


def game_block(it, lang):
    g = game.get(norm(it["n"]))
    if not g:
        return ""
    T = L[lang]
    rows = []
    if g.get("lv"):
        rows.append((T["lv"], str(g["lv"])))
    if g.get("rq") and any(g["rq"]):
        rows.append((T["rq"], ", ".join("%s %d" % (k, v) for k, v in zip(("STR", "DEX", "INT", "LUK"), g["rq"]) if v)))
    if g.get("job"):
        rows.append((T["jobs"], ", ".join(JOBS_HE.get(j, j) if lang == "he" else j for j in g["job"])))
    if g.get("slots"):
        rows.append((T["slots"], str(g["slots"])))
    if g.get("sc") is not None:
        rows.append((T["sc"], "%d%%" % g["sc"]))
    rest = [x for x in (("+%s HP" % num(g["hp"])) if g.get("hp") else "", ("+%d%% HP" % g["hpR"]) if g.get("hpR") else "",
                        ("+%s MP" % num(g["mp"])) if g.get("mp") else "", ("+%d%% MP" % g["mpR"]) if g.get("mpR") else "") if x]
    if rest:
        rows.append((T["restores"], ", ".join(rest)))
    if g.get("time"):
        rows.append((T["lasts"], T["min"] % max(1, round(g["time"] / 60000))))
    if (g.get("max") or 0) > 1:
        rows.append((T["stack"], num(g["max"])))
    stats = " ".join("%+d %s" % (g["st"][k], k) for k in STAT_ORDER if (g.get("st") or {}).get(k))
    if stats:
        rows.insert(0, ("Stats" if lang == "en" else "סטטים", stats))
    if not rows and not g.get("d"):
        return ""
    facts = "".join("<div><dt>%s</dt><dd dir=\"ltr\">%s</dd></div>" % (esc(k), esc(v)) for k, v in rows)
    desc = '<p class="ii-desc" dir="ltr" lang="en">%s</p>' % esc(g["d"]).replace("\n", "<br>") if g.get("d") else ""
    return '<h2>%s</h2>%s%s<p class="ii-src">%s</p>' % (esc(T["details"]), '<dl class="seo-facts">%s</dl>' % facts if facts else "", desc, esc(T["source"]))


def seo_block(it, lang="en"):
    T = L[lang]
    up = "../../../" if lang == "he" else "../../"
    home = up + ("he/" if lang == "he" else "")
    st, buy, sell, p = item_facts(it)
    name = it["n"]
    rows = []
    if buy:
        rows.append((T["npcShop"], T["npcAt"] % (num(buy[0]), buy[1] or T["anNpc"])))
    if sell is not None:
        rows.append((T["npcBuys"], T["mesos"] % num(sell)))
    if p is not None:
        rows.append((T["fm"], T["mesos"] % num(p)))
        rows.append((T["range"], "%s–%s" % (num(it.get("p25")), num(it.get("p75")))))
        rows.append((T["sold"], num(it.get("sold"))))
        if it.get("sellers"):
            rows.append((T["sellers"], num(it.get("sellers"))))
    rows.append((T["updated"], updated_day))
    facts = "".join("<div><dt>%s</dt><dd>%s</dd></div>" % (esc(k), esc(v)) for k, v in rows)
    drop_html = ""
    ds = sorted(drops.get(norm(name), []), key=lambda x: -x[1][1])[:6]
    if ds:
        lis = []
        for m, d in ds:
            line = esc(T["drop"] % (m[0], m[1], ", " + m[4] if m[4] else "", d[1]))
            hm = hunt_by_norm.get(norm(m[0]))
            if hm and hm[1]:
                best = hunt.get("maps", {}).get(str(hm[1][0][0]), {}).get("n")
                if best:
                    line += ' · <a href="%s?hunt=%s">%s</a>' % (home, esc(urllib.parse.quote(hm[0])), esc(T["huntAt"] % best))
            lis.append("<li>%s</li>" % line)
        drop_html = "<h2>%s</h2><ul>%s</ul>" % (esc(T["where"] % name), "".join(lis))
    related = [r for r in by_cat.get(it.get("k") or "Etc", []) if r is not it][:8]
    rel_html = ""
    if related:
        rel_html = "<h2>%s</h2><ul class=\"seo-links\">%s</ul>" % (esc(T["more"] % (it.get("k") or "item")), "".join(
            '<li><a href="../%s/">%s</a></li>' % (esc(r["s"]), esc(r["n"])) for r in related))
    iid = icon_ids.get(norm(name))
    ico = '<img class="seo-ico" src="%sicons/%s.png" alt="" width="32" height="32" loading="lazy"> ' % (up, iid) if iid and os.path.isdir("icons") else ""
    text = item_text_he(it) if lang == "he" else item_text(it)
    return ('<section id="seo-item" class="seo-item"><article class="panel seo-card">'
            '<nav class="crumbs"><a href="%s">MapleCharts</a> › %s</nav>'
            '<h1>%s%s</h1><p>%s</p><dl class="seo-facts">%s</dl>%s%s%s</article></section>') % (
        home, esc(it.get("k") or T["items"]), ico, esc(T["h1"] % name), esc(text), facts, game_block(it, lang), drop_html, rel_html)


# ---------------------------------------------------------------- write the site
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
for f in os.listdir(WEB):
    if f == "template.html":
        continue
    src = os.path.join(WEB, f)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(OUT, f))
shutil.copy(MARKET, os.path.join(OUT, "market.json"))
# Same prices in two parts: the list (no history) and the history per item slug.
_m = json.load(open(MARKET, encoding="utf-8"))
_lite = dict(_m, items=[{k: v for k, v in it.items() if k != "h"} for it in _m["items"]])
json.dump(_lite, open(os.path.join(OUT, "market-lite.json"), "w"), separators=(",", ":"))
json.dump({"updatedAt": _m.get("updatedAt"), "h": {it["s"]: it["h"] for it in _m["items"] if it.get("s") and it.get("h")}},
          open(os.path.join(OUT, "market-hist.json"), "w"), separators=(",", ":"))
if os.path.isdir("icons"):
    shutil.copytree("icons", os.path.join(OUT, "icons"), dirs_exist_ok=True)
# Item details for the app's item sheet (keyed by item name) and the hunting maps.
if game_items:
    _info = {}
    for _id, _v in sorted(game_items.items(), key=lambda kv: int(kv[0])):
        if _v.get("n") and _v["n"] not in _info:
            _info[_v["n"]] = dict({k: x for k, x in _v.items() if k != "n"}, id=int(_id))
    json.dump(_info, open(os.path.join(OUT, "item-info.json"), "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)
if hunt.get("mobs"):
    os.makedirs(os.path.join(OUT, "hunt", "m"), exist_ok=True)
    shutil.copy("data/hunt.json", os.path.join(OUT, "hunt", "index.json"))
    for f in os.listdir("data/huntmaps"):
        if f.endswith(".json"):
            shutil.copy(os.path.join("data/huntmaps", f), os.path.join(OUT, "hunt", "m", f))
if ADS_CLIENT:
    open(os.path.join(OUT, "ads.txt"), "w").write("google.com, %s, DIRECT, f08c47fec0942fa0\n" % ADS_CLIENT.replace("ca-", ""))

home_title = "MapleCharts: MapleStory Classic World prices, NPC vs Free Market"
home_desc = ("Every item in MapleStory Classic World: NPC shop price vs player Free Market price, price charts, "
             "the market index, NPC flips, crafting profits and calculators. Updated automatically.")
home_title_he = "MapleCharts: מחירי NPC מול Free Market ב-MapleStory Classic World"
home_desc_he = ("כל פריט ב-MapleStory Classic World: מחיר בחנות NPC מול המחיר בין שחקנים, גרפים, מדד השוק, "
                "רווחים מ-NPC, מפות NPC ומפות ציד. מתעדכן אוטומטית.")


def alternates(path):
    """hreflang links between the English page and its Hebrew copy (path is relative to the site root)."""
    return ('\n  <link rel="alternate" hreflang="en" href="%s%s">\n  <link rel="alternate" hreflang="he" href="%she/%s">'
            '\n  <link rel="alternate" hreflang="x-default" href="%s%s">') % (SITE_URL, path, SITE_URL, path, SITE_URL, path)


open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(
    page("", head(home_title, home_desc, SITE_URL, SITE_URL + "og/home.png", alternates("")), config("")))
os.makedirs(os.path.join(OUT, "he"), exist_ok=True)
open(os.path.join(OUT, "he", "index.html"), "w", encoding="utf-8").write(
    page("../", head(home_title_he, home_desc_he, SITE_URL + "he/", SITE_URL + "og/home.png", alternates("")), config("../", lang="he"), lang="he"))
open(os.path.join(OUT, "404.html"), "w", encoding="utf-8").write(
    page(ROOT_PATH, head(home_title, home_desc, SITE_URL, SITE_URL + "og/home.png"), config(ROOT_PATH)))

for it in items:
    path = "item/" + it["s"] + "/"
    image = SITE_URL + "og/" + it["s"] + ".png"
    d = os.path.join(OUT, "item", it["s"])
    os.makedirs(d, exist_ok=True)
    title = "%s price · MapleStory Classic World | MapleCharts" % it["n"]
    desc = item_text(it)[:300] or "Prices for %s in MapleStory Classic World." % it["n"]
    open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(
        page("../../", head(title, desc, SITE_URL + path, image, alternates(path)), config("../../", it["s"]), seo_block(it), "p"))
    d = os.path.join(OUT, "he", "item", it["s"])
    os.makedirs(d, exist_ok=True)
    title = "מחיר %s ב-MapleStory Classic World | MapleCharts" % it["n"]
    desc = item_text_he(it)[:300] or "מחירים של %s ב-MapleStory Classic World." % it["n"]
    open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(
        page("../../../", head(title, desc, SITE_URL + "he/" + path, image, alternates(path)), config("../../../", it["s"], "he"),
             seo_block(it, "he"), "p", "he"))

urls = [(SITE_URL, updated_day, "1.0"), (SITE_URL + "he/", updated_day, "0.9")] + \
    [(SITE_URL + "item/" + it["s"] + "/", updated_day, "0.7") for it in items] + \
    [(SITE_URL + "he/item/" + it["s"] + "/", updated_day, "0.6") for it in items]
open(os.path.join(OUT, "sitemap.xml"), "w").write(
    '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
    "".join("  <url><loc>%s</loc><lastmod>%s</lastmod><priority>%s</priority></url>\n" % (esc(u), m, pr) for u, m, pr in urls) +
    "</urlset>\n")
open(os.path.join(OUT, "robots.txt"), "w").write("User-agent: *\nAllow: /\nSitemap: %ssitemap.xml\n" % SITE_URL)
open(os.path.join(OUT, ".nojekyll"), "w").write("")
if SITE_DOMAIN:
    open(os.path.join(OUT, "CNAME"), "w").write(SITE_DOMAIN + "\n")

# ---------------------------------------------------------------- share images
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

if Image:
    def font(size, weight=600):
        if FONT and os.path.exists(FONT):
            f = ImageFont.truetype(FONT, size)
            try:
                vals = []
                for ax in f.get_variation_axes():
                    nm = ax.get("name", b"")
                    nm = (nm.decode() if isinstance(nm, bytes) else str(nm)).lower()
                    vals.append(weight if "weight" in nm else (100 if "width" in nm else ax.get("default", ax["minimum"])))
                f.set_variation_by_axes(vals)
            except Exception as e:
                print("font weight not set:", e)
            return f
        for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
            if os.path.exists(cand):
                return ImageFont.truetype(cand, int(size * 0.9))
        return ImageFont.load_default()

    W, H = 1200, 630
    base = Image.new("RGB", (W, H))
    g = ImageDraw.Draw(base)
    for y in range(H):
        if y < 390:
            t = y / 390
            c = tuple(int(a + (b - a) * t) for a, b in zip((99, 190, 255), (182, 227, 255)))
        else:
            t = (y - 390) / (H - 390)
            c = tuple(int(a + (b - a) * t) for a, b in zip((158, 219, 122), (108, 196, 72)))
        g.line([(0, y), (W, y)], fill=c)
    for x, y, r in ((980, 90, 70), (1040, 70, 50), (600, 105, 55), (650, 88, 40)):
        g.ellipse([x - r, y - r * 0.45, x + r, y + r * 0.45], fill=(255, 255, 255))
    F_BRAND, F_NAME, F_LABEL, F_BIG, F_NOTE = font(46, 700), font(62, 600), font(30, 600), font(78, 700), font(28, 500)

    def card(title, left, right, note, path):
        im = base.copy()
        d = ImageDraw.Draw(im)
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                d.text((90 + dx, 60 + dy), "MapleCharts", font=F_BRAND, fill=(196, 90, 0))
        d.text((90, 60), "MapleCharts", font=F_BRAND, fill=(255, 255, 255))
        d.rounded_rectangle([50, 150, W - 50, 550], radius=36, fill=(255, 253, 245), outline=(232, 211, 166), width=5)
        size = 62
        f = F_NAME
        while d.textlength(title, font=f) > W - 190 and size > 30:
            size -= 2
            f = font(size, 600)
        d.text((90, 178), title, font=f, fill=(59, 42, 30))
        for x, (label, value, col, bg) in ((90, left), (610, right)):
            d.rounded_rectangle([x, 270, x + 500, 440], radius=26, fill=bg)
            d.text((x + 30, 288), label, font=F_LABEL, fill=col)
            d.text((x + 30, 335), value, font=F_BIG, fill=col)
        d.text((90, 478), note, font=F_NOTE, fill=(110, 86, 68))
        host = re.sub(r"^https?://", "", SITE_URL).split("/")[0]
        d.text((W - 70 - d.textlength(host, font=F_LABEL), 575), host, font=F_LABEL, fill=(23, 61, 14))
        im.quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)

    os.makedirs(os.path.join(OUT, "og"), exist_ok=True)
    GREEN = ((43, 122, 28), (226, 245, 216))
    ORANGE = ((166, 74, 0), (255, 233, 210))
    card("NPC shops vs Free Market", ("NPC shop", "Prices", GREEN[0], GREEN[1]), ("Players", "Charts", ORANGE[0], ORANGE[1]),
         "MapleStory Classic World · updated automatically", os.path.join(OUT, "og", "home.png"))
    for it in items:
        st, buy, sell, p = item_facts(it)
        left_label, left_val = ("NPC shop", num(buy[0])) if buy else (("NPC buys", num(sell)) if sell is not None else ("NPC shop", "—"))
        note = "MapleStory Classic World · mesos · %s" % updated_day
        card(it["n"], (left_label, left_val, GREEN[0], GREEN[1]), ("Players", num(p), ORANGE[0], ORANGE[1]), note,
             os.path.join(OUT, "og", it["s"] + ".png"))

count = sum(len(fs) for _, _, fs in os.walk(OUT))
print("built", OUT, "items:", len(items), "files:", count, "site:", SITE_URL)
