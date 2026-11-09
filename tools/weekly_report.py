"""The weekly MapleCharts market report: biggest risers and fallers, most traded, best NPC flips, best crafting profit.

Used by build_site.py (the /report/ page, its Hebrew copy, the share image og/report.png and a draft post text).
The week is the last full Monday–Sunday week in the price history. Only items with enough sales count.

  python3 tools/weekly_report.py market.json      # prints the report as JSON (for checking)
"""
import datetime, json, re, sys

FEE = 0.03
MIN_SALES_DAY = 3      # a day's median counts only with at least this many sales
MIN_SALES_WEEK = 20    # risers/fallers need at least this many sales in the week


def norm(name):
    s = str(name or "").lower().replace("&", " and ")
    s = re.sub(r"['’`]", "", s)
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w]
    words = [w[:-1] if (len(w) > 3 and w.endswith("s") and not w.endswith("ss")) else w for w in words]
    return " ".join(words)


def week_bounds(updated_day):
    """Monday and Sunday of the last full week that ended on or before updated_day (YYYY-MM-DD)."""
    d = datetime.date.fromisoformat(updated_day)
    # Sunday is weekday 6. If today is Sunday the week isn't over yet, so use the one before.
    end = d - datetime.timedelta(days=(d.weekday() + 1) % 7 or 7)
    return end - datetime.timedelta(days=6), end


def day_price(h, day, before=True):
    """Median on `day`, or the closest earlier (before=True) / later day with enough sales, within 3 days."""
    by = {x[0]: x for x in h}
    d = datetime.date.fromisoformat(day)
    for i in range(4):
        k = (d - datetime.timedelta(days=i) if before else d + datetime.timedelta(days=i)).isoformat()
        x = by.get(k)
        if x and x[1] and (x[2] or 0) >= MIN_SALES_DAY:
            return x[1]
    return None


def build(market, static, wiki, low_data=5, start=None, end=None):
    items = [it for it in market.get("items", []) if it.get("s")]
    updated = (market.get("updatedAt") or "")[:10] or datetime.date.today().isoformat()
    if not start:
        s, e = week_bounds(updated)
        start, end = s.isoformat(), e.isoformat()
    prev_day = (datetime.date.fromisoformat(start) - datetime.timedelta(days=1)).isoformat()

    moves, traded = [], []
    for it in items:
        h = it.get("h") or []
        week = [x for x in h if start <= x[0] <= end]
        sales = sum((x[2] or 0) for x in week)
        if sales:
            traded.append({"n": it["n"], "s": it["s"], "sales": sales, "p": day_price(h, end) or it.get("p")})
        if sales < MIN_SALES_WEEK:
            continue
        a, b = day_price(h, prev_day), day_price(h, end)
        if not a or not b:
            continue
        moves.append({"n": it["n"], "s": it["s"], "from": a, "to": b, "pct": round((b - a) / a * 100, 1), "sales": sales})

    by_norm = {norm(it["n"]): it for it in items}
    flips = []
    for it in items:
        st = static.get(norm(it["n"])) or {}
        buy, p = st.get("buy"), it.get("p")
        if not buy or p is None or (it.get("sold") or 0) < max(low_data, 10):
            continue
        profit = p * (1 - FEE) - buy[0]
        if profit > 0 and profit / buy[0] >= 0.05:
            flips.append({"n": it["n"], "s": it["s"], "npc": buy[0], "shop": buy[1], "p": p, "profit": round(profit), "pct": round(profit / buy[0] * 100)})

    def buy_price(name):
        k = norm(name)
        st, it = static.get(k) or {}, by_norm.get(k)
        opts = [x for x in ((st.get("buy") or [None])[0], it and it.get("p")) if x]
        return min(opts) if opts else None

    def sell_value(name):
        k = norm(name)
        st, it = static.get(k) or {}, by_norm.get(k)
        if it and it.get("p") and (it.get("sold") or 0) >= low_data:
            return max(it["p"] * (1 - FEE), st.get("sell") or 0), it
        return (st.get("sell"), None) if st.get("sell") is not None else (None, None)

    crafts = []
    for r in wiki.get("recipes", []):
        prof, lvl, out, n, mfee, cata, ings = r[0], r[1], r[3], r[4] or 1, r[5] or 0, r[6], r[7]
        cost, ok = mfee, True
        for ing in ings:
            b = buy_price(ing[0])
            if not b:
                ok = False
                break
            cost += b * ing[1]
        if ok and cata:
            b = buy_price(cata)
            ok = bool(b)
            cost += b or 0
        v, it = sell_value(out)
        if not ok or not v:
            continue
        profit = v * n - cost
        if profit > 0:
            crafts.append({"n": out, "s": it["s"] if it else None, "prof": prof, "lvl": lvl, "cost": round(cost), "profit": round(profit)})

    moves.sort(key=lambda m: -m["pct"])
    return {
        "week": [start, end], "updatedAt": market.get("updatedAt"),
        "risers": [m for m in moves if m["pct"] > 0][:8],
        "fallers": sorted([m for m in moves if m["pct"] < 0], key=lambda m: m["pct"])[:8],
        "traded": sorted(traded, key=lambda t: -t["sales"])[:8],
        "flips": sorted(flips, key=lambda f: -f["profit"])[:8],
        "crafts": sorted(crafts, key=lambda c: -c["profit"])[:6],
    }


def num(n):
    if n is None:
        return "—"
    if abs(n) >= 10_000_000:
        return ("%.2f" % (n / 1_000_000)).rstrip("0").rstrip(".") + "M"
    return "{:,.0f}".format(n)


def post_text(rep, site_url):
    """A short post for Discord / Facebook groups. Daniel approves before anything is posted."""
    lines = ["MapleStory Classic World market, week %s to %s" % tuple(rep["week"]), ""]
    if rep["risers"]:
        lines.append("Up: " + ", ".join("%s +%g%%" % (m["n"], m["pct"]) for m in rep["risers"][:3]))
    if rep["fallers"]:
        lines.append("Down: " + ", ".join("%s %g%%" % (m["n"], m["pct"]) for m in rep["fallers"][:3]))
    if rep["flips"]:
        f = rep["flips"][0]
        lines.append("Best NPC flip: %s, buy %s from %s, players pay %s" % (f["n"], num(f["npc"]), f["shop"] or "an NPC", num(f["p"])))
    if rep["crafts"]:
        c = rep["crafts"][0]
        lines.append("Best craft: %s (%s), about +%s each" % (c["n"], c["prof"], num(c["profit"])))
    lines += ["", "Full report: %sreport/" % site_url]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import os
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    m = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "market.json"))
    st = json.load(open("web/items-static.json"))
    wk = json.load(open("data/wiki.json"))
    print(json.dumps(build(m, st, wk, m.get("lowDataBelow") or 5), indent=1))
