"""MapleCharts notifier (runs in GitHub Actions every 15 minutes).

1. Makes sure the web push keys exist (stored in the private app_secrets table on first run).
2. When the player prices changed, turns triggered price alerts into notifications (at most once a day per alert).
3. Sends web push for notifications that have not been pushed yet, and removes dead subscriptions.

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY. Skips quietly when they are not set.
Usage: python3 tools/notify.py market.json
"""
import base64, datetime, json, os, sys, urllib.parse, urllib.request

CONF = json.load(open("site.config.json")) if os.path.exists("site.config.json") else {}
URL = (os.environ.get("SUPABASE_URL") or CONF.get("supabaseUrl") or "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SITE_URL = (os.environ.get("SITE_URL") or CONF.get("siteUrl") or "https://dmog189.github.io/meso-market-data/").rstrip("/") + "/"
CONTACT = os.environ.get("VAPID_CONTACT", "mailto:maplecharts@users.noreply.github.com")
if not URL or not KEY:
    print("Supabase is not set up yet; nothing to do.")
    sys.exit(0)

HEAD = {"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json"}


def rest(method, path, body=None, prefer=None):
    req = urllib.request.Request(URL + "/rest/v1/" + path, method=method, headers=dict(HEAD))
    if prefer:
        req.add_header("Prefer", prefer)
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=30) as r:
        text = r.read().decode() or "null"
        return json.loads(text)


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 1. web push keys
secrets = {r["name"]: r["value"] for r in rest("GET", "app_secrets?select=name,value")}
if "vapid_private" not in secrets:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    priv = b64url(key.private_numbers().private_value.to_bytes(32, "big"))
    pub = b64url(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    rest("POST", "app_secrets", [{"name": "vapid_private", "value": priv}, {"name": "vapid_public", "value": pub}],
         prefer="resolution=merge-duplicates")
    secrets.update(vapid_private=priv, vapid_public=pub)
    print("Created web push keys.")

# ---------------------------------------------------------------- 2. price alerts
market_path = sys.argv[1] if len(sys.argv) > 1 else None
if market_path and os.path.exists(market_path):
    market = json.load(open(market_path))
    stamp = market.get("updatedAt", "")
    if secrets.get("alerts_checked_for") != stamp:
        prices = {it["s"]: it for it in market["items"] if it.get("s") and it.get("p") is not None}
        alerts = rest("GET", "alerts?active=is.true&select=id,user_id,item_slug,item_name,direction,price,last_sent_at")
        day_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=23)
        sent = 0
        for a in alerts:
            it = prices.get(a["item_slug"])
            if not it:
                continue
            p = it["p"]
            hit = (a["direction"] == "below" and p <= a["price"]) or (a["direction"] == "above" and p >= a["price"])
            if not hit:
                continue
            if a.get("last_sent_at"):
                last = datetime.datetime.fromisoformat(a["last_sent_at"].replace("Z", "+00:00"))
                if last > day_ago:
                    continue
            word = "dropped to" if a["direction"] == "below" else "rose to"
            rest("POST", "notifications", {
                "user_id": a["user_id"], "kind": "alert",
                "title": "%s %s %s mesos" % (a["item_name"], word, "{:,.0f}".format(p)),
                "body": "Your alert: %s %s mesos. Median player price today." % (a["direction"], "{:,.0f}".format(a["price"])),
                "url": "item/%s/" % a["item_slug"]})
            rest("PATCH", "alerts?id=eq.%d" % a["id"], {"last_sent_at": now_iso()})
            sent += 1
        rest("POST", "app_secrets", [{"name": "alerts_checked_for", "value": stamp}], prefer="resolution=merge-duplicates")
        print("Price alerts triggered:", sent)

# ---------------------------------------------------------------- 3. web push
since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
pending = rest("GET", "notifications?pushed_at=is.null&created_at=gt.%s&select=id,user_id,title,body,url&order=id&limit=500" % since)
if not pending:
    print("No notifications to push.")
    sys.exit(0)
users = sorted({n["user_id"] for n in pending})
subs = rest("GET", "push_subscriptions?select=endpoint,user_id,p256dh,auth&user_id=in.(%s)" % ",".join(users))
by_user = {}
for s in subs:
    by_user.setdefault(s["user_id"], []).append(s)

from pywebpush import webpush, WebPushException

pushed, dead = 0, set()
for n in pending:
    payload = json.dumps({"title": n["title"], "body": n.get("body") or "", "url": SITE_URL + (n.get("url") or "")})
    for s in by_user.get(n["user_id"], []):
        if s["endpoint"] in dead:
            continue
        try:
            webpush(subscription_info={"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}},
                    data=payload, vapid_private_key=secrets["vapid_private"], vapid_claims={"sub": CONTACT}, ttl=86400)
            pushed += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):
                dead.add(s["endpoint"])
            else:
                print("push failed:", code)
ids = ",".join(str(n["id"]) for n in pending)
rest("PATCH", "notifications?id=in.(%s)" % ids, {"pushed_at": now_iso()})
for ep in dead:
    rest("DELETE", "push_subscriptions?endpoint=eq." + urllib.parse.quote(ep, safe=""))
print("Pushed:", pushed, "| notifications:", len(pending), "| removed subscriptions:", len(dead))
