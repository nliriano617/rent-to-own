#!/usr/bin/env python3
"""
Rent To Own Auto Centers — dynamic website server.

A dependency-free (Python standard library only) web server that:
  * serves the existing static site (HTML / CSS / images) from the repo root,
  * exposes a JSON API that renders the vehicle inventory dynamically from the
    local SQLite database (roadside.db) cloned read-only from Podio,
  * provides a password-protected /admin page to adjust settings — most
    importantly the frequency of the read-only Podio data refresh,
  * runs a background scheduler that re-pulls Podio on that frequency.

Run:  python3 app.py
Then: http://127.0.0.1:8000/orlando.html   (public site)
      http://127.0.0.1:8000/admin          (admin console)
"""
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote, unquote

import podio_sync

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_ROOT = os.path.dirname(HERE)            # the rent-to-own/ folder (static site)
DB_PATH = podio_sync.DB_PATH
CARS_DIR = os.path.join(SITE_ROOT, "images", "cars")

# ---- defaults seeded into the settings table on first run ----------------- #
DEFAULT_SETTINGS = {
    "sync_frequency_minutes": "1440",          # daily
    "auto_sync_enabled": "true",
    "public_statuses": "Available,On Rent",    # which Podio statuses are public
    "sold_statuses": "On Rent",                # statuses shown as SOLD
    "sold_window_months": "2",                 # show a sold car for N months, then drop it
    "retail_keyword": "cash",                  # comments containing this => retail feed
    "site_phone": "321-319-4300",
    # Homepage "Featured Vehicles" showroom (read by js/featured.js)
    "featured_enabled": "true",
    "featured_mode": "deals",                  # deals | newest | random | manual
    "featured_count": "3",                     # 2 or 3
    "featured_max_payment": "",                # blank = no limit
    "featured_manual_rtos": "",                # comma-separated RTOs for manual mode
    # Vehicles taken down from the whole public site by RTO number (comma-
    # separated), even if Podio still lists them. Managed from the admin console.
    # Seeded with 8173 (flagged STOLEN in Podio) so it stays hidden across syncs.
    "hidden_rtos": "8173",
}

# in-memory admin sessions: token -> expiry epoch
SESSIONS = {}
SESSION_TTL = 8 * 3600


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def seed_settings():
    con = db()
    podio_sync.ensure_meta_tables(con)
    for k, v in DEFAULT_SETTINGS.items():
        if podio_sync.get_setting(con, k) is None:
            podio_sync.set_setting(con, k, v)
    con.close()


def get_settings_dict():
    con = db()
    podio_sync.ensure_meta_tables(con)
    rows = con.execute("SELECT key, value FROM settings").fetchall()
    con.close()
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in rows})
    return out


def reconcile_orphaned_syncs():
    """Mark any sync left 'running' by a previous (killed/restarted) process as
    'interrupted', so the history reflects what actually happened instead of
    showing a frozen run."""
    con = db()
    podio_sync.ensure_meta_tables(con)
    n = con.execute(
        "UPDATE sync_log SET status='interrupted', finished_at=?, "
        "message=COALESCE(message,'') || ' (interrupted: server stopped mid-sync)' "
        "WHERE status='running'",
        (podio_sync.now_iso(),),
    ).rowcount
    con.commit()
    con.close()
    if n:
        print(f"Reconciled {n} orphaned sync run(s) -> interrupted")
    return n


# --------------------------------------------------------------------------- #
# Image resolution: map an RTO number to a local image file
# --------------------------------------------------------------------------- #
_image_index = None


def build_image_index():
    """Map RTO base number -> best web path under images/cars[/sold]."""
    global _image_index
    idx = {}

    def consider(folder, webprefix):
        if not os.path.isdir(folder):
            return
        for fn in sorted(os.listdir(folder)):
            full = os.path.join(folder, fn)
            if not os.path.isfile(full):
                continue
            base = os.path.splitext(fn)[0]
            # normalize "9006 1", "9006_1" -> "9006"
            key = base.replace("_", " ").split(" ")[0].strip().lower()
            if not key:
                continue
            # URL-encode the filename so names with spaces/parens (e.g.
            # "9017 1.JPG", "9006 (3).JPG") resolve instead of 404-ing.
            web = webprefix + quote(fn)
            # prefer an exact "{rto}.ext" over a "{rto} 1.ext" variant
            is_exact = base.lower() == key
            prev = idx.get(key)
            if prev is None or (is_exact and not prev[1]):
                idx[key] = (web, is_exact)

    # Photos downloaded from Podio (images/cars/podio/) take precedence so the
    # site reflects Podio on every sync; the hand-curated images/cars/ files are
    # the fallback. The /sold subfolder (SOLD baked into the image) is skipped on
    # purpose — we draw the SOLD badge with CSS instead.
    consider(os.path.join(CARS_DIR, "podio"), "/images/cars/podio/")
    consider(CARS_DIR, "/images/cars/")
    _image_index = {k: v[0] for k, v in idx.items()}


NO_PHOTO = "/images/cars/no-photo.svg"


def image_for_rto(rto):
    """Return the web path of this car's photo, or None if there isn't one."""
    if _image_index is None:
        build_image_index()
    if not rto:
        return None
    return _image_index.get(str(rto).strip().lower())


# --------------------------------------------------------------------------- #
# Vehicle feed
# --------------------------------------------------------------------------- #
def _digits(s):
    m = re.search(r"[\d,]+", str(s or ""))
    return m.group(0).replace(",", "") if m else None


def build_caption(orig_fee, comments):
    """Rebuild the original site's short caption: down payment + cash price.

    Down payment comes from the `orig_fee` field; the cash price is pulled out of
    the free-text `comments` (e.g. "Cash Price $4995 30-day warranty ..."). We
    deliberately expose only these two numbers — never the raw comments text.
    """
    down = _digits(orig_fee)
    if down in ("0", ""):
        down = None
    cash = None
    m = re.search(r"cash\s*price[^\d]*([\d,]+)", comments or "", re.I)
    if m:
        cash = m.group(1).replace(",", "")
    if down and cash:
        return "RTO with $%s Down or Buy Cash $%s" % (down, cash)
    if down:
        return "RTO with $%s Down" % down
    if cash:
        return "Buy Cash $%s" % cash
    return ""


def _months_ago(months):
    """Calendar date `months` before today (clamped to a valid day)."""
    import calendar
    from datetime import date
    today = date.today()
    m, y = today.month - months, today.year
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(today.day, calendar.monthrange(y, m)[1]))


def vehicles_feed(feed="rto"):
    settings = get_settings_dict()
    statuses = [s.strip() for s in settings["public_statuses"].split(",") if s.strip()]
    sold_statuses = [
        s.strip() for s in settings.get("sold_statuses", "On Rent").split(",") if s.strip()
    ]
    try:
        window = max(0, int(float(settings.get("sold_window_months", "2"))))
    except ValueError:
        window = 2
    # A rented ("sold") car is shown as SOLD only for `window` months after its
    # rental started (roadside.date_submitted); after that it's dropped. window=0
    # disables the rule (sold cars always shown).
    cutoff = _months_ago(window).isoformat() if window else None

    con = db()
    placeholders = ",".join("?" for _ in statuses) or "''"
    rows = con.execute(
        f"""SELECT i.item_id, i.rto, i.year, i.make, i.model, i.status,
                   i.payment, i.orig_fee, i.comments, i.comment,
                   (SELECT MAX(r.date_submitted) FROM roadside_rto_references x
                     JOIN roadside r ON r.item_id = x.item_id
                     WHERE x.ref_item_id = i.item_id) AS rental_start
            FROM inventory i
            WHERE i.status IN ({placeholders})
            ORDER BY CAST(i.rto AS INTEGER) DESC""",
        statuses,
    ).fetchall()
    con.close()

    keyword = (settings.get("retail_keyword") or "").strip().lower()
    # Cars taken down from the whole site via the admin "Hidden Vehicles" setting.
    hidden = {
        h.strip().lower()
        for h in (settings.get("hidden_rtos", "") or "").split(",")
        if h.strip()
    }
    out = []
    for r in rows:
        if str(r["rto"] or "").strip().lower() in hidden:
            continue  # hidden from the public site by admin
        comments = (r["comments"] or "").strip()
        comment = (r["comment"] or "").strip()
        if feed == "retail":
            if not keyword or keyword not in (comments + " " + comment).lower():
                continue
        is_sold = r["status"] in sold_statuses
        if is_sold and cutoff is not None:
            start = (r["rental_start"] or "")[:10]
            if not start or start < cutoff:
                continue  # rented over `window` months ago (or unknown) -> remove
        title_bits = [r["year"], r["make"], r["model"]]
        title = " ".join(b for b in title_bits if b and b != "None").strip()
        img = image_for_rto(r["rto"])
        # Caption: sold cars carry none (the SOLD stamp says it all). Available
        # cars use the owner-typed "Comment" field verbatim if present (so they
        # can write e.g. "New Arrival RTO $2500 Down!"); otherwise a short
        # down/cash line is auto-built. No other internal info is exposed.
        manual = (r["comment"] or "").strip()
        if is_sold:
            caption = ""
        elif manual:
            caption = manual
        else:
            caption = build_caption(r["orig_fee"], r["comments"])
        out.append(
            {
                "item_id": r["item_id"],
                "rto": r["rto"],
                "title": title or (r["model"] or "Vehicle"),
                "status": r["status"],
                "sold": is_sold,
                "has_photo": img is not None,
                "image": img or NO_PHOTO,
                "caption": caption,
                # payment/year are used by the homepage Featured showroom
                # (js/featured.js) for its filters; the inventory cards don't
                # display them.
                "payment": r["payment"],
                "year": r["year"],
            }
        )
    try:
        f_count = int(settings.get("featured_count", "3") or 3)
    except ValueError:
        f_count = 3
    featured = {
        "enabled": settings.get("featured_enabled", "true") == "true",
        "mode": settings.get("featured_mode", "deals"),
        "count": f_count,
        "max_payment": (settings.get("featured_max_payment", "").strip() or None),
        "manual_rtos": [
            s.strip() for s in settings.get("featured_manual_rtos", "").split(",") if s.strip()
        ],
    }
    return {
        "feed": feed,
        "count": len(out),
        "featured": featured,
        "vehicles": out,
        "phone": settings.get("site_phone", ""),
    }


# --------------------------------------------------------------------------- #
# Background scheduler (read-only Podio refresh on a configurable frequency)
# --------------------------------------------------------------------------- #
class Scheduler(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        self.lock = threading.Lock()
        self.running = False

    def run(self):
        # small startup delay so the web server is up first
        self._stop.wait(5)
        while not self._stop.is_set():
            settings = get_settings_dict()
            enabled = settings.get("auto_sync_enabled", "true") == "true"
            try:
                freq = max(5, int(float(settings.get("sync_frequency_minutes", "1440"))))
            except ValueError:
                freq = 1440
            if enabled and self._due(freq):
                self.sync_now(reason="scheduled")
            # check every 60s whether a sync is due
            self._stop.wait(60)

    def _due(self, freq_minutes):
        con = db()
        last = podio_sync.get_setting(con, "last_sync_at")
        con.close()
        if not last:
            return True
        try:
            from datetime import datetime
            last_dt = datetime.fromisoformat(last)
            age_min = (datetime.now(last_dt.tzinfo) - last_dt).total_seconds() / 60
            return age_min >= freq_minutes
        except Exception:
            return True

    def sync_now(self, reason="manual"):
        with self.lock:
            if self.running:
                return {"status": "busy", "message": "A sync is already running."}
            self.running = True
        try:
            result = podio_sync.run_sync(reason=reason)
        finally:
            self.running = False
            build_image_index()  # refresh image map in case new cars arrived
        return result

    def stop(self):
        self._stop.set()


SCHEDULER = Scheduler()


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "RTOServer/1.0"

    # ---- helpers ---------------------------------------------------------- #
    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode())
        except Exception:
            return {}

    def _token(self):
        # accept token via Authorization header or cookie
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if part.strip().startswith("rto_admin="):
                return part.strip()[len("rto_admin="):]
        return None

    def _is_admin(self):
        tok = self._token()
        exp = SESSIONS.get(tok)
        if exp and exp > time.time():
            return True
        return False

    def log_message(self, fmt, *args):  # quieter logging
        pass

    # ---- routing ---------------------------------------------------------- #
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/vehicles":
            qs = parse_qs(parsed.query)
            feed = (qs.get("feed", ["rto"])[0]).lower()
            return self._send_json(vehicles_feed(feed))
        if path == "/api/settings":
            if not self._is_admin():
                return self._send_json({"error": "unauthorized"}, 401)
            return self._send_json(self._admin_state())
        if path == "/api/sync-status":
            con = db()
            podio_sync.ensure_meta_tables(con)
            last = podio_sync.get_setting(con, "last_sync_at")
            status = podio_sync.get_setting(con, "last_sync_status")
            con.close()
            return self._send_json(
                {"last_sync_at": last, "last_sync_status": status,
                 "running": SCHEDULER.running}
            )
        if path in ("/admin", "/admin/"):
            return self._serve_file(os.path.join(HERE, "templates", "admin.html"))
        # static site
        return self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            data = self._read_body()
            cfg = podio_sync.load_config()
            if data.get("password") and data["password"] == cfg.get("admin_password"):
                tok = secrets.token_urlsafe(24)
                SESSIONS[tok] = time.time() + SESSION_TTL
                return self._send_json({"ok": True, "token": tok})
            return self._send_json({"ok": False, "error": "bad password"}, 403)

        if path == "/api/settings":
            if not self._is_admin():
                return self._send_json({"error": "unauthorized"}, 401)
            data = self._read_body()
            con = db()
            podio_sync.ensure_meta_tables(con)
            allowed = set(DEFAULT_SETTINGS.keys())
            for k, v in data.items():
                if k in allowed:
                    podio_sync.set_setting(con, k, v)
            con.close()
            return self._send_json({"ok": True, "settings": self._admin_state()})

        if path == "/api/refresh":
            if not self._is_admin():
                return self._send_json({"error": "unauthorized"}, 401)
            # run the read-only sync in a background thread, return immediately
            threading.Thread(
                target=SCHEDULER.sync_now, kwargs={"reason": "admin"}, daemon=True
            ).start()
            return self._send_json({"ok": True, "message": "Refresh started."})

        return self._send_json({"error": "not found"}, 404)

    # ---- admin state ------------------------------------------------------ #
    def _admin_state(self):
        con = db()
        podio_sync.ensure_meta_tables(con)
        logs = con.execute(
            "SELECT started_at, finished_at, status, inventory_count, "
            "roadside_count, message FROM sync_log ORDER BY id DESC LIMIT 10"
        ).fetchall()
        counts = {}
        try:
            for r in con.execute(
                "SELECT status, COUNT(*) c FROM inventory GROUP BY status"
            ):
                counts[r["status"] or "(blank)"] = r["c"]
        except Exception:
            pass
        # Removed ("hidden") vehicles, with a display title looked up from the
        # inventory table so the admin can show "#8173 — 2011 Ford Flex" even
        # though hidden cars are excluded from the public /api/vehicles feed.
        settings = get_settings_dict()
        hidden_nums = [
            h.strip() for h in (settings.get("hidden_rtos", "") or "").split(",") if h.strip()
        ]
        titles = {}
        if hidden_nums:
            try:
                qs = ",".join("?" for _ in hidden_nums)
                for r in con.execute(
                    f"SELECT rto, year, make, model FROM inventory WHERE rto IN ({qs})",
                    hidden_nums,
                ):
                    t = " ".join(
                        b for b in (r["year"], r["make"], r["model"]) if b and b != "None"
                    ).strip()
                    titles[str(r["rto"])] = t
            except Exception:
                pass
        hidden_vehicles = [{"rto": n, "title": titles.get(n, "")} for n in hidden_nums]
        con.close()
        return {
            "settings": settings,
            "sync_log": [dict(r) for r in logs],
            "status_counts": counts,
            "hidden_vehicles": hidden_vehicles,
            "running": SCHEDULER.running,
        }

    # ---- static files ----------------------------------------------------- #
    def _serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        # decode %20 etc. so filenames with spaces/parens resolve to real files
        path = unquote(path)
        # prevent directory traversal (decode first so encoded ../ is caught)
        rel = os.path.normpath(path.lstrip("/"))
        if rel.startswith(".."):
            return self._send_json({"error": "forbidden"}, 403)
        full = os.path.join(SITE_ROOT, rel)
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            return self._send_json({"error": "not found", "path": path}, 404)
        return self._serve_file(full)

    def _serve_file(self, full):
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            return self._send_json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if ctype.startswith("text/html"):
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main():
    seed_settings()
    reconcile_orphaned_syncs()
    build_image_index()
    cfg = podio_sync.load_config()
    host = cfg.get("host", "127.0.0.1")
    port = int(cfg.get("port", 8000))
    SCHEDULER.start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Rent To Own server running at http://{host}:{port}/")
    print(f"  Public inventory : http://{host}:{port}/orlando.html")
    print(f"  Admin console    : http://{host}:{port}/admin")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        SCHEDULER.stop()
        httpd.shutdown()


if __name__ == "__main__":
    main()
