#!/usr/bin/env python3
"""
Read-only Podio -> SQLite sync for the Rent To Own website.

This module clones the Podio "Roadside" and "Inventory" (Vehicle) apps into the
local SQLite database (roadside.db). It is a strict READ-ONLY mirror: it only
ever calls GET / filter (read) endpoints on Podio and NEVER creates, updates or
deletes anything on the Podio side.

It is a re-runnable consolidation of the original clone_podio.py +
clone_inventory.py scripts, with two additions used by the website:

  * a `settings`  table  (admin-adjustable site settings)
  * a `sync_log`  table  (history of every sync run)

Credentials are read from config.json (never hard-coded here). Run directly to
perform a one-off sync, or import `run_sync()` from the web app / scheduler.
"""
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "roadside.db")
CONFIG_PATH = os.path.join(HERE, "config.json")
API = "https://api.podio.com"

# The static site lives one level up from server/. Downloaded photos go into
# images/cars/podio/ so they never clobber the hand-curated images/cars/ files.
SITE_ROOT = os.path.dirname(HERE)
PHOTO_DIR = os.path.join(SITE_ROOT, "images", "cars", "podio")

# Inventory app is referenced by the Roadside app's `rto` field.
INVENTORY_APP_ID = "29475815"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            "config.json not found. Copy config.example.json to config.json "
            "and fill in your Podio credentials."
        )
    with open(CONFIG_PATH) as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# Podio HTTP helpers (READ-ONLY)
# --------------------------------------------------------------------------- #
def api_request(url, token=None, method="GET", body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        url, data=body.encode() if body else None, headers=headers, method=method
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def authenticate(cfg):
    """App auth against the Roadside app (read scope on the Orlando space)."""
    data = urllib.parse.urlencode(
        {
            "grant_type": "app",
            "app_id": cfg["roadside_app_id"],
            "app_token": cfg["app_token"],
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        }
    ).encode()
    req = urllib.request.Request(
        "https://podio.com/oauth/token", data=data, method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["access_token"]


def fetch_all_items(token, app_id):
    """Read every item from an app via the (read-only) filter endpoint."""
    items, offset, limit = [], 0, 100
    while True:
        body = json.dumps({"limit": limit, "offset": offset})
        page = api_request(f"{API}/item/app/{app_id}/filter/", token, "POST", body)
        items.extend(page["items"])
        if len(items) >= page["total"] or not page["items"]:
            break
        offset += limit
        time.sleep(0.2)
    return items


# --------------------------------------------------------------------------- #
# Field rendering
# --------------------------------------------------------------------------- #
def col_name(external_id):
    return external_id.replace("-", "_")


def render_value(ftype, values, item_id, cur):
    if not values:
        return None
    if ftype in ("text", "number"):
        out = []
        for v in values:
            val = v.get("value", "")
            if ftype == "number" and val not in (None, ""):
                try:
                    fv = float(val)
                    val = int(fv) if fv == int(fv) else fv
                except (TypeError, ValueError):
                    pass
            out.append(str(val))
        return "; ".join(out)
    if ftype == "category":
        return "; ".join(v["value"]["text"] for v in values)
    if ftype == "date":
        out = []
        for v in values:
            s = v.get("start_date") or v.get("start")
            e = v.get("end_date")
            out.append(f"{s} - {e}" if e else s)
        return "; ".join(x for x in out if x)
    if ftype == "image":
        names = []
        for v in values:
            img = v["value"]
            names.append(img.get("name") or str(img.get("file_id")))
            cur.execute(
                "INSERT INTO inventory_pictures VALUES (?,?,?,?)",
                (item_id, img.get("file_id"), img.get("name"), img.get("link")),
            )
        return "; ".join(names)
    if ftype == "app":
        # app-reference (e.g. roadside.rto -> inventory vehicle)
        return "; ".join(str(v["value"].get("item_id")) for v in values if v.get("value"))
    return json.dumps(values, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Clone one app into a table
# --------------------------------------------------------------------------- #
def clone_app(con, token, app_id, table, extra_link_tables=None):
    cur = con.cursor()
    app = api_request(f"{API}/app/{app_id}", token)
    fields = app["fields"]
    items = fetch_all_items(token, app_id)

    drop = [table, f"{table}_app_fields", f"{table}_category_options"]
    if table == "inventory":
        drop.append("inventory_pictures")
    if extra_link_tables:
        drop += extra_link_tables
    for t in drop:
        cur.execute(f"DROP TABLE IF EXISTS {t}")

    cur.execute(
        f"""CREATE TABLE {table}_app_fields (
        position INTEGER, field_id INTEGER, external_id TEXT, label TEXT, type TEXT)"""
    )
    for pos, f in enumerate(fields):
        cur.execute(
            f"INSERT INTO {table}_app_fields VALUES (?,?,?,?,?)",
            (pos, f["field_id"], f["external_id"], f["config"]["label"], f["type"]),
        )

    cur.execute(
        f"""CREATE TABLE {table}_category_options (
        field_external_id TEXT, option_id INTEGER, text TEXT, color TEXT, status TEXT)"""
    )
    for f in fields:
        if f["type"] == "category":
            for o in f["config"]["settings"]["options"]:
                cur.execute(
                    f"INSERT INTO {table}_category_options VALUES (?,?,?,?,?)",
                    (f["external_id"], o["id"], o["text"], o.get("color"), o.get("status")),
                )

    if table == "inventory":
        cur.execute(
            """CREATE TABLE inventory_pictures (
            item_id INTEGER, file_id INTEGER, name TEXT, link TEXT)"""
        )

    cols = ["item_id INTEGER PRIMARY KEY", "app_item_id INTEGER", "podio_title TEXT"]
    for f in fields:
        cols.append('"' + col_name(f["external_id"]) + '" TEXT')
    cols += ["created_on TEXT", "link TEXT"]
    cur.execute(f"CREATE TABLE {table} ({', '.join(cols)})")

    types = {f["external_id"]: f["type"] for f in fields}
    order = [f["external_id"] for f in fields]

    for it in items:
        fv = {f["external_id"]: f["values"] for f in it["fields"]}
        row = {
            "item_id": it["item_id"],
            "app_item_id": it.get("app_item_id"),
            "podio_title": it.get("title"),
            "created_on": it.get("created_on"),
            "link": it.get("link"),
        }
        for ext in order:
            row[col_name(ext)] = render_value(
                types[ext], fv.get(ext, []), it["item_id"], cur
            )
        colnames = ", ".join('"' + k + '"' for k in row)
        ph = ", ".join("?" for _ in row)
        cur.execute(f"INSERT INTO {table} ({colnames}) VALUES ({ph})", list(row.values()))

    con.commit()
    return len(items)


# --------------------------------------------------------------------------- #
# Settings + sync log (created/preserved across syncs)
# --------------------------------------------------------------------------- #
def ensure_meta_tables(con):
    cur = con.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT, finished_at TEXT, status TEXT,
            inventory_count INTEGER, roadside_count INTEGER, message TEXT)"""
    )
    con.commit()


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Photo download (READ-ONLY: GET /file/{id}/raw)
# --------------------------------------------------------------------------- #
def _safe_rto(rto):
    return "".join(c for c in str(rto) if c.isalnum())


def _ext_from_name(name):
    ext = os.path.splitext(name or "")[1].lower()
    return ext if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif") else ".jpg"


def download_file(token, file_id, dest):
    """Download one Podio file's raw bytes to dest (read-only)."""
    req = urllib.request.Request(
        f"{API}/file/{file_id}/raw", headers={"Authorization": "Bearer " + token}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def download_photos(con, token):
    """Download the primary photo of each *public* vehicle from Podio.

    Only re-downloads when the Podio photo (file_id) has changed since last sync,
    so routine syncs stay fast. Returns the number of photos (re)downloaded.
    """
    cur = con.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS photo_cache (
            item_id INTEGER PRIMARY KEY, rto TEXT, file_id INTEGER, filename TEXT)"""
    )
    con.commit()

    statuses = [
        s.strip()
        for s in (get_setting(con, "public_statuses", "Available,On Rent") or "").split(",")
        if s.strip()
    ]
    if not statuses:
        return 0
    placeholders = ",".join("?" for _ in statuses)

    # one row per public vehicle that has at least one picture (its first picture)
    rows = cur.execute(
        f"""SELECT i.item_id, i.rto,
                   (SELECT p.file_id FROM inventory_pictures p
                     WHERE p.item_id = i.item_id ORDER BY p.rowid LIMIT 1) AS file_id,
                   (SELECT p.name FROM inventory_pictures p
                     WHERE p.item_id = i.item_id ORDER BY p.rowid LIMIT 1) AS name
            FROM inventory i
            WHERE i.status IN ({placeholders})""",
        statuses,
    ).fetchall()

    os.makedirs(PHOTO_DIR, exist_ok=True)
    downloaded = 0
    for item_id, rto, file_id, name in rows:
        if not file_id or not rto:
            continue
        filename = _safe_rto(rto) + _ext_from_name(name)
        dest = os.path.join(PHOTO_DIR, filename)
        cached = cur.execute(
            "SELECT file_id, filename FROM photo_cache WHERE item_id=?", (item_id,)
        ).fetchone()
        # skip if unchanged and the file is still present
        if cached and cached[0] == file_id and os.path.exists(dest):
            continue
        try:
            download_file(token, file_id, dest)
        except Exception:
            continue  # a single bad photo must not fail the whole sync
        # if the extension changed, remove the old file
        if cached and cached[1] and cached[1] != filename:
            old = os.path.join(PHOTO_DIR, cached[1])
            if os.path.exists(old):
                try:
                    os.remove(old)
                except OSError:
                    pass
        cur.execute(
            "INSERT INTO photo_cache (item_id, rto, file_id, filename) VALUES (?,?,?,?) "
            "ON CONFLICT(item_id) DO UPDATE SET rto=excluded.rto, "
            "file_id=excluded.file_id, filename=excluded.filename",
            (item_id, str(rto), file_id, filename),
        )
        downloaded += 1
        time.sleep(0.05)
    con.commit()
    return downloaded


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def run_sync(reason="manual"):
    """Run a full read-only sync. Returns a dict describing the result."""
    cfg = load_config()
    con = sqlite3.connect(DB_PATH)
    ensure_meta_tables(con)
    started = now_iso()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO sync_log (started_at, status, message) VALUES (?,?,?)",
        (started, "running", f"reason={reason}"),
    )
    log_id = cur.lastrowid
    con.commit()

    try:
        token = authenticate(cfg)
        roadside_n = clone_app(
            con, token, cfg["roadside_app_id"], "roadside",
            extra_link_tables=["roadside_rto_references"],
        )
        _build_roadside_references(con, token, cfg["roadside_app_id"])
        inventory_n = clone_app(con, token, INVENTORY_APP_ID, "inventory")
        photos_n = download_photos(con, token)
        finished = now_iso()
        cur.execute(
            "UPDATE sync_log SET finished_at=?, status=?, inventory_count=?, "
            "roadside_count=?, message=? WHERE id=?",
            (finished, "success", inventory_n, roadside_n,
             f"ok; {photos_n} photo(s) updated", log_id),
        )
        set_setting(con, "last_sync_at", finished)
        set_setting(con, "last_sync_status", "success")
        con.commit()
        result = {
            "status": "success",
            "inventory": inventory_n,
            "roadside": roadside_n,
            "photos": photos_n,
            "finished_at": finished,
        }
    except Exception as e:  # noqa: BLE001 - report any failure back to the admin
        finished = now_iso()
        cur.execute(
            "UPDATE sync_log SET finished_at=?, status=?, message=? WHERE id=?",
            (finished, "error", str(e), log_id),
        )
        set_setting(con, "last_sync_status", "error")
        con.commit()
        result = {"status": "error", "message": str(e), "finished_at": finished}
    finally:
        con.close()
    return result


def _build_roadside_references(con, token, roadside_app_id):
    """Mirror the roadside.rto app-reference into a link table (read-only)."""
    cur = con.cursor()
    cur.execute("DROP TABLE IF EXISTS roadside_rto_references")
    cur.execute(
        """CREATE TABLE roadside_rto_references (
            item_id INTEGER, ref_item_id INTEGER, ref_title TEXT,
            ref_app_item_id INTEGER, ref_link TEXT)"""
    )
    items = fetch_all_items(token, roadside_app_id)
    for it in items:
        for f in it["fields"]:
            if f["type"] == "app":
                for v in f["values"]:
                    ref = v.get("value") or {}
                    cur.execute(
                        "INSERT INTO roadside_rto_references VALUES (?,?,?,?,?)",
                        (
                            it["item_id"],
                            ref.get("item_id"),
                            ref.get("title"),
                            ref.get("app_item_id"),
                            ref.get("link"),
                        ),
                    )
    con.commit()


# --------------------------------------------------------------------------- #
# Settings helpers (shared with the web app)
# --------------------------------------------------------------------------- #
def get_setting(con, key, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(con, key, value):
    con.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    con.commit()


if __name__ == "__main__":
    print("Running read-only Podio -> SQLite sync...")
    print(json.dumps(run_sync(reason="cli"), indent=2))
