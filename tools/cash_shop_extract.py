"""NX / Cash Shop items for MapleCharts: data/cash-shop.json.

Sources:
  data/cash-shop-manual.json  - the current crate (from the in-game Cash Shop, names checked against game data), sales, skin colors.
  MapleClassic Wiki (CC BY-NC-SA 4.0) "Update:" pages of the Cash Shop updates: earlier Mystery Fashion Crate
  rotations (items, NX price, dates) and the Mystery hair/face styles per gender. Each links Nexon's post.
Item type comes from data/wiki.json (or the manual list). Runs with the Build site workflow; if the wiki is down,
the previous file's wiki part is kept. Usage: python3 tools/cash_shop_extract.py [data/cash-shop.json]
"""
import datetime, json, os, re, sys, time, urllib.parse, urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else "data/cash-shop.json"
API = "https://mapleclassic.wiki/api.php"
UA = {"User-Agent": "MapleCharts cash shop (+https://github.com/DMOG189/meso-market-data)"}
MON = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September",
                                    "October", "November", "December"], 1)}
TYPES = {"Cash Hat": "Hat", "Cash Overall": "Overall", "Cash Top": "Top", "Cash Bottom": "Bottom", "Cash Shoes": "Shoes",
         "Cash Gloves": "Gloves", "Cash Glove": "Gloves", "Cash Cape": "Cape", "Cash Weapon": "Weapon cover",
         "Cash Accessory": "Accessory", "Cash Ring": "Ring", "Cash Face Accessory": "Face accessory",
         "Cash Eye Accessory": "Eye accessory", "Cash Earrings": "Earrings", "Weapon Cover": "Weapon cover"}


def api(**p):
    p.update(format="json", formatversion="2")
    url = API + "?" + urllib.parse.urlencode(p)
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            print("wiki:", e)
            time.sleep(3 * (i + 1))
    return None


def gender_of(iid):
    if not iid or iid >= 2000000:
        return "any"
    g = (iid // 1000) % 10
    return {0: "m", 1: "f"}.get(g, "any")


def utc(day, mon, year, hm="7:59 AM"):
    h, m = map(int, re.match(r"(\d+):(\d+)", hm).groups())
    if "PM" in hm and h != 12:
        h += 12
    if "AM" in hm and h == 12:
        h = 0
    return "%04d-%02d-%02dT%02d:%02d:00Z" % (int(year), MON[mon], int(day), h, m)


def wiki_part(witems):
    j = api(action="query", list="prefixsearch", pssearch="Update:", pslimit="100")
    if not j:
        return None
    titles = [p["title"] for p in j["query"]["prefixsearch"]]
    c = api(action="query", prop="revisions", rvprop="content", rvslots="main", titles="|".join(titles))
    if not c:
        return None
    crates, styles = [], []
    for p in c["query"]["pages"]:
        if not p.get("revisions"):
            continue
        s = p["revisions"][0]["slots"]["main"]["content"]
        url = (re.search(r"source_url\s*=\s*(\S+)", s) or [None, ""])[1]
        if not url.startswith("https://www.nexon.com/"):
            continue
        # Crates: "From 4 November 2026 ... the Cash Shop sells the {{i|Mystery Fashion Crate}} for 3,500 NX"
        for m in re.finditer(r"From (\d+) (\w+)(?: (\d{4}))?(?: at ([\d:]+ [AP]M) UTC)? (?:to|until) (?:(\d+) (\w+) (\d{4})|the maintenance on (\d+) (\w+))[^.]*?"
                             r"\{\{i\|([^}|]*Crate)\}\} for ([\d,]+) NX", s):
            d1, mo1, y1, hm1, d2, mo2, y2, md, mm, name, nx = m.groups()
            y = y1 or y2 or "2026"
            start = utc(d1, mo1, y, hm1 or "8:00 AM")
            end = utc(d2, mo2, y2) if d2 else utc(md, mm, y, "1:00 PM")
            sec = s[m.end():]
            sec = sec[:sec.find("\n==")] if "\n==" in sec else sec
            names = []
            for line in sec.splitlines():
                if line.startswith("*"):
                    for n in re.findall(r"\{\{i\|([^}|]*)", line):
                        if n not in names:
                            names.append(n)
            if not names:
                continue
            items = []
            for n in names:
                w = witems.get(n)
                iid = w[0] if w else None
                items.append({"n": n, "id": iid, "type": TYPES.get((w or [0, 0, ""])[2], (w or [0, 0, ""])[2] or "Cash item")})
            crates.append({"id": "wiki-" + start[:10] + "-" + re.sub(r"\W+", "-", name.lower()), "name": name, "start": start, "end": end,
                           "nx": int(nx.replace(",", "")), "url": url, "source": "MapleClassic Wiki", "items": items})
        # Styles: "From 11 to 25 November 2026 ... Mystery Hair Coupon}} (5,500 NX) and the {{i|Mystery Face Coupon}} (3,500 NX)" + table
        for m in re.finditer(r"From (\d+)(?: (\w+))? to (\d+) (\w+) (\d{4})[^\n]*?Mystery Hair Coupon\}\} \(([\d,]+) NX\)[^\n]*?Mystery Face Coupon\}\} \(([\d,]+) NX\)", s):
            d1, mo1, d2, mo2, y, hx, fx = m.groups()
            tab = s[m.end():].split("|}")[0]
            rows = {}
            for kind, row in re.findall(r"! (Hairstyles|Faces)\n\|(.*)", tab):
                parts = row.split("||")
                if len(parts) == 2:
                    rows[kind] = [re.findall(r"\|([^\]]+)\]\]", x) for x in parts]
            if not rows:
                continue
            base = {"start": utc(d1, mo1 or mo2, y, "8:00 AM"), "end": utc(d2, mo2, y), "url": url}
            if "Hairstyles" in rows:
                styles.append(dict(base, kind="hair", coupon="Mystery Hair Coupon", nx=int(hx.replace(",", "")),
                                   names={"m": rows["Hairstyles"][0], "f": rows["Hairstyles"][1]}))
            if "Faces" in rows:
                styles.append(dict(base, kind="face", coupon="Mystery Face Coupon", nx=int(fx.replace(",", "")),
                                   names={"m": rows["Faces"][0], "f": rows["Faces"][1]}))
    return crates, styles


def main():
    man = json.load(open("data/cash-shop-manual.json", encoding="utf-8"))
    witems = json.load(open("data/wiki.json", encoding="utf-8"))["items"] if os.path.exists("data/wiki.json") else {}
    for c in man["crates"]:
        for it in c["items"]:
            it["g"] = gender_of(it.get("id"))
    old = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    got = wiki_part(witems)
    if got is None:
        wcrates, wstyles = [x for x in old.get("crates", []) if x.get("source") == "MapleClassic Wiki"], [x for x in old.get("styles", []) if x.get("kind") != "skin"]
    else:
        wcrates, wstyles = got
    for c in wcrates:
        for it in c["items"]:
            it["g"] = gender_of(it.get("id"))
    crates = sorted(man["crates"] + wcrates, key=lambda c: c["start"], reverse=True)
    styles = sorted(man.get("styles", []) + wstyles, key=lambda x: x["start"], reverse=True)
    doc = {"updatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "note": "NX / Cash Shop items, kept apart from items bought with mesos. Crate pieces can be traded for mesos.",
           "credit": "Nexon's Cash Shop posts; MapleClassic Wiki (CC BY-NC-SA 4.0); game data from maplestory.io.",
           "crates": crates, "sales": man.get("sales", []), "styles": styles}
    json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("wrote", OUT, "| crates:", [(c["start"][:10], len(c["items"])) for c in crates],
          "| styles:", [(s["start"][:10], s["kind"]) for s in styles])


if __name__ == "__main__":
    main()
