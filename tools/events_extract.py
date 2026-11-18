"""MapleStory Classic World events, sales, maintenance and update news for MapleCharts.

Builds data/events.json from:
  1. data/events-manual.json  - hand-checked events with exact start/end times and Nexon's official post.
  2. MapleClassic Wiki (CC BY-NC-SA 4.0): its "Update:" pages (each has Nexon's official post in source_url)
     and its "News/Coming up" list (dates Nexon announced ahead). Only items with an official link are kept.
  3. data/new-items.json - items an update added, shown with the update.

Runs in the "Build site" workflow every 6 hours. If the wiki can't be reached, the manual list is still written,
so the site never breaks. Usage: python3 tools/events_extract.py [data/events.json]
"""
import datetime, json, os, re, sys, time, urllib.parse, urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else "data/events.json"
API = "https://mapleclassic.wiki/api.php"
UA = {"User-Agent": "MapleCharts events (+https://github.com/DMOG189/meso-market-data)"}
OFFICIAL = ("https://www.nexon.com/", "https://nexon.com/", "https://www.twitch.tv/videos/", "https://maplestory.nexon.net/")
NOW = datetime.datetime.now(datetime.timezone.utc)
KEEP_DAYS = 45  # news older than this is dropped


def api(**params):
    params.update(format="json", formatversion="2")
    url = API + "?" + urllib.parse.urlencode(params)
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            print("wiki:", e)
            time.sleep(3 * (i + 1))
    return None


def field(text, name):
    m = re.search(r"^\|\s*%s\s*=\s*(.*)$" % re.escape(name), text, re.M)
    return m.group(1).strip() if m else ""


def plain(s):
    s = re.sub(r"\{\{(?:map|i|q|NPC|region)\|([^|}]*)(?:\|([^}]*))?\}\}", lambda m: m.group(2) or m.group(1), s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", s)
    s = re.sub(r"'{2,}", "", s)
    s = re.sub(r"\{\{[^}]*\}\}", "", s)
    return re.sub(r"\s+", " ", s).strip()


def official(url):
    return bool(url) and url.startswith(OFFICIAL)


def wiki_updates():
    """Update:<date> pages -> news items (date, title, summary, official link)."""
    j = api(action="query", list="prefixsearch", pssearch="Update:", pslimit="100")
    if not j:
        return None
    titles = [p["title"] for p in j["query"]["prefixsearch"] if p["title"] != "Update:Sandbox"]
    out = []
    for i in range(0, len(titles), 50):
        c = api(action="query", prop="revisions", rvprop="content", rvslots="main", titles="|".join(titles[i:i + 50]))
        if not c:
            continue
        for p in c["query"]["pages"]:
            if p.get("missing") or not p.get("revisions"):
                continue
            text = p["revisions"][0]["slots"]["main"]["content"]
            date, url = field(text, "date"), field(text, "source_url")
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date) or not official(url):
                continue
            cat = field(text, "category").lower()
            typ = {"event": "event", "cashshop": "sale", "maintenance": "maintenance"}.get(cat, "update")
            if "Maintenance" in p["title"]:
                typ = "maintenance"
            out.append({"id": "wiki-" + re.sub(r"[^a-z0-9]+", "-", p["title"].lower()).strip("-"), "type": typ, "news": True,
                        "title": {"en": field(text, "name_en") or p["title"][7:]}, "date": date,
                        "text": {"en": plain(field(text, "summary_en"))[:300]}, "url": url,
                        "wiki": "https://mapleclassic.wiki/wiki/" + urllib.parse.quote(p["title"].replace(" ", "_"))})
    return out


def wiki_coming():
    """News/Coming up -> dates Nexon announced ahead (official link from the matching News card)."""
    c = api(action="parse", page="News/Coming_up", prop="wikitext")
    n = api(action="parse", page="News", prop="wikitext")
    l = api(action="parse", page="News/Latest", prop="wikitext")
    if not c:
        return None
    cards = (n or {}).get("parse", {}).get("wikitext", "") + (l or {}).get("parse", {}).get("wikitext", "")
    links = {}
    for m in re.finditer(r"\{\{News card\|(.*?)\}\}\s*$", cards, re.M | re.S):
        body = m.group(1)
        t = re.search(r"\|title=\s*([^|]*)", body)
        s = re.search(r"\|source=\[(https?://\S+)", body)
        if t and s:
            links[t.group(1).strip()] = s.group(1)
    out = []
    for line in c["parse"]["wikitext"].splitlines():
        if not line.startswith("{{Coming up entry|"):
            continue
        body = re.sub(r"\{\{(?:map|i|q|NPC|region)\|([^|}]*)(?:\|([^}]*))?\}\}", lambda x: x.group(2) or x.group(1), line)
        body = re.sub(r"\}\}(<noinclude>.*)?$", "", body[len("{{Coming up entry|"):])
        parts = dict(kv.split("=", 1) for kv in body.split("|") if "=" in kv)
        date, url = parts.get("date", "").strip(), links.get(parts.get("card", "").strip(), "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date) or not official(url):
            continue
        out.append({"id": "coming-" + date, "type": "update", "coming": True, "dateOnly": True, "date": date,
                    "when": parts.get("when", "").strip(), "title": {"en": plain(parts.get("title", ""))},
                    "text": {"en": plain(parts.get("text", ""))}, "url": url})
    return out


def main():
    manual = json.load(open("data/events-manual.json", encoding="utf-8"))["items"]
    new_items = json.load(open("data/new-items.json", encoding="utf-8")).get("updates", []) if os.path.exists("data/new-items.json") else []
    news, coming = wiki_updates(), wiki_coming()
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8"))
        except ValueError:
            old = {}
    if news is None:  # wiki down: keep what we had
        news = old.get("news", [])
    if coming is None:
        coming = old.get("coming", [])
    cutoff = (NOW - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    have_urls = {x["url"] for x in manual}
    news = sorted([x for x in news if x["date"] >= cutoff and x["url"] not in have_urls], key=lambda x: x["date"], reverse=True)[:12]
    manual_days = {x["start"][:10] for x in manual if x.get("type") == "update"}
    coming = [x for x in coming if x["date"] >= NOW.strftime("%Y-%m-%d") and x["date"] not in manual_days]
    doc = {"updatedAt": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "credit": "Times and links: Nexon's official MapleStory posts. News list: MapleClassic Wiki (CC BY-NC-SA 4.0).",
           "items": manual, "coming": coming, "news": news, "newItems": new_items}
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("wrote", OUT, "| events:", len(manual), "coming:", len(coming), "news:", len(news),
          "new items:", sum(len(u["items"]) for u in new_items))


if __name__ == "__main__":
    main()
