"""Build the MapleCharts website into _site/ for GitHub Pages.

  python3 tools/build_site.py market.json [_site]

Uses web/ (the app, page template, icons; built from the MapleCharts project), data/wiki.json (game data from
MapleClassic Wiki, CC BY-NC-SA 4.0) and the latest market.json from maplestory-classic.com.

Makes: index.html, item/<slug>/index.html for every traded item, og/<slug>.png share images, sitemap.xml,
robots.txt, 404.html, market.json and the app files. SITE_URL sets the public address used in links.
"""
import datetime, html, json, os, re, shutil, sys

MARKET = sys.argv[1] if len(sys.argv) > 1 else "market.json"
OUT = sys.argv[2] if len(sys.argv) > 2 else "_site"
WEB = "web"
SITE_URL = os.environ.get("SITE_URL", "https://dmog189.github.io/meso-market-data/").rstrip("/") + "/"
SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "").strip()
FONT = os.environ.get("OG_FONT", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()  # the public "anon" key, safe to put in web pages
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


def config(base, slug=None):
    cfg = {"marketUrl": base + "market.json", "pollMs": 300000, "base": base, "itemPaths": True, "siteUrl": SITE_URL, "sw": True}
    if SUPABASE_URL and SUPABASE_KEY:
        cfg.update(supabaseUrl=SUPABASE_URL, supabaseKey=SUPABASE_KEY)
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


def page(base, head_html, cfg, seo="", brand_tag="h1"):
    out = template.replace("<!--HEAD-->", head_html).replace("<!--CONFIG-->", cfg)
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


def seo_block(it):
    st, buy, sell, p = item_facts(it)
    name = it["n"]
    rows = []
    if buy:
        rows.append(("NPC shop price", "%s mesos at %s" % (num(buy[0]), buy[1] or "an NPC")))
    if sell is not None:
        rows.append(("NPC buys for", "%s mesos" % num(sell)))
    if p is not None:
        rows.append(("Player price (median)", "%s mesos" % num(p)))
        rows.append(("Typical range", "%s–%s" % (num(it.get("p25")), num(it.get("p75")))))
        rows.append(("Sold", num(it.get("sold"))))
        if it.get("sellers"):
            rows.append(("Sellers", num(it.get("sellers"))))
    rows.append(("Updated", updated_day))
    facts = "".join("<div><dt>%s</dt><dd>%s</dd></div>" % (esc(k), esc(v)) for k, v in rows)
    drop_html = ""
    ds = sorted(drops.get(norm(name), []), key=lambda x: -x[1][1])[:6]
    if ds:
        drop_html = "<h2>Where to get %s</h2><ul>%s</ul>" % (esc(name), "".join(
            "<li>%s (Lv. %d%s): %.2g%% drop rate</li>" % (esc(m[0]), m[1], ", " + esc(m[4]) if m[4] else "", d[1]) for m, d in ds))
    related = [r for r in by_cat.get(it.get("k") or "Etc", []) if r is not it][:8]
    rel_html = ""
    if related:
        rel_html = "<h2>More %s prices</h2><ul class=\"seo-links\">%s</ul>" % (esc(it.get("k") or "item"), "".join(
            '<li><a href="../%s/">%s</a></li>' % (esc(r["s"]), esc(r["n"])) for r in related))
    return ('<section id="seo-item" class="seo-item"><article class="panel seo-card">'
            '<nav class="crumbs"><a href="../../">MapleCharts</a> › %s</nav>'
            '<h1>%s price</h1><p>%s</p><dl class="seo-facts">%s</dl>%s%s</article></section>') % (
        esc(it.get("k") or "Items"), esc(name), esc(item_text(it)), facts, drop_html, rel_html)


# ---------------------------------------------------------------- write the site
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
for f in os.listdir(WEB):
    if f in ("template.html", "items-static.json"):
        continue
    src = os.path.join(WEB, f)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(OUT, f))
shutil.copy(MARKET, os.path.join(OUT, "market.json"))

home_title = "MapleCharts: MapleStory Classic World prices, NPC vs Free Market"
home_desc = ("Every item in MapleStory Classic World: NPC shop price vs player Free Market price, price charts, "
             "the market index, NPC flips, crafting profits and calculators. Updated automatically.")
open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(
    page("", head(home_title, home_desc, SITE_URL, SITE_URL + "og/home.png"), config("")))
open(os.path.join(OUT, "404.html"), "w", encoding="utf-8").write(
    page(ROOT_PATH, head(home_title, home_desc, SITE_URL, SITE_URL + "og/home.png"), config(ROOT_PATH)))

for it in items:
    d = os.path.join(OUT, "item", it["s"])
    os.makedirs(d, exist_ok=True)
    title = "%s price · MapleStory Classic World | MapleCharts" % it["n"]
    desc = item_text(it)[:300] or "Prices for %s in MapleStory Classic World." % it["n"]
    url = SITE_URL + "item/" + it["s"] + "/"
    open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(
        page("../../", head(title, desc, url, SITE_URL + "og/" + it["s"] + ".png"), config("../../", it["s"]), seo_block(it), "p"))

urls = [(SITE_URL, updated_day, "1.0")] + [(SITE_URL + "item/" + it["s"] + "/", updated_day, "0.7") for it in items]
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
                f.set_variation_by_axes([100, weight])
            except Exception:
                pass
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
