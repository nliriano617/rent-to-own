#!/usr/bin/env python3
"""
Build a self-contained static PREVIEW of the site into ../dbtest/.

The live site is dynamic (needs the Python backend for /api/...). GitHub Pages
is static, so this script bakes the current data into static files so the owner
can browse the proposed changes at e.g. /dbtest/Orlando2.html without any server:

  * inventory pages read data/vehicles-*.json instead of the live API
  * car photos are resized (cards are ~350px wide) to keep the upload small
  * the admin dashboard renders from an embedded snapshot (controls are inert)
  * all absolute paths are rewritten to relative so it works under /dbtest/

Run:  python3 build_dbtest.py
Then upload the resulting ../dbtest/ folder to the GitHub repo.
"""
import json
import os
import re
import shutil
import subprocess
import urllib.parse

import app

SITE = app.SITE_ROOT
OUT = os.path.join(SITE, "dbtest")
MAXW = "900"  # resized photo max dimension

BANNER = (
    '<div style="background:#102e6f;color:#fff;text-align:center;padding:8px 12px;'
    "font:600 14px/1.5 'Segoe UI',Arial,sans-serif;position:relative;z-index:9999\">"
    "PREVIEW of proposed changes — not the live site."
    '&nbsp; <a style="color:#ffd24d" href="index.html">Home</a>'
    '&nbsp;·&nbsp; <a style="color:#ffd24d" href="Orlando2.html">RTO Inventory</a>'
    '&nbsp;·&nbsp; <a style="color:#ffd24d" href="OrlandoRetail2.html">Retail</a>'
    '&nbsp;·&nbsp; <a style="color:#ffd24d" href="how-it-works.html">How It Works</a>'
    '&nbsp;·&nbsp; <a style="color:#ffd24d" href="submit-an-application.html">Apply</a>'
    '&nbsp;·&nbsp; <a style="color:#ffd24d" href="admin.html">Admin Dashboard</a>'
    "</div>"
)


def rewrite(htmltext):
    """Make a live-site page work standalone under /dbtest/."""
    # dynamic inventory script -> the static preview script (relative, no ?v=)
    htmltext = re.sub(r'/js/inventory\.js(\?[^"\']*)?', "js/inventory.js", htmltext)
    # home link
    htmltext = re.sub(r'href="/"', 'href="index.html"', htmltext)
    # absolute internal page links -> relative
    htmltext = re.sub(r'(href|src)="/([\w\-./]+\.html)"', r'\1="\2"', htmltext)
    # absolute asset roots -> relative
    htmltext = re.sub(r'(href|src)="/(images|css|js)/', r"\1=\"\2/", htmltext)
    # rename the inventory pages everywhere they're linked
    htmltext = htmltext.replace("orlandoRetail.html", "OrlandoRetail2.html")
    htmltext = htmltext.replace("orlando.html", "Orlando2.html")
    # inject the preview banner right after <body ...>
    htmltext = re.sub(r"(<body[^>]*>)", r"\1\n" + BANNER, htmltext, count=1)
    return htmltext


def read(path):
    with open(os.path.join(SITE, path), encoding="utf-8") as f:
        return f.read()


def write(rel, text):
    full = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)


def resize_photo(src_rel, out_name):
    """Resize a source photo into dbtest/images/cars/<out_name> (jpeg)."""
    src = os.path.join(SITE, urllib.parse.unquote(src_rel).lstrip("/"))
    dst = os.path.join(OUT, "images", "cars", out_name)
    if os.path.exists(dst):
        return
    if not os.path.isfile(src):
        return
    subprocess.run(
        ["sips", "-Z", MAXW, "-s", "format", "jpeg", src, "--out", dst],
        check=False, capture_output=True,
    )


def build_feed(feed):
    d = app.vehicles_feed(feed)
    for v in d["vehicles"]:
        if v.get("has_photo"):
            out_name = re.sub(r"[^A-Za-z0-9]", "", str(v["rto"])) + ".jpg"
            resize_photo(v["image"], out_name)
            v["image"] = "images/cars/" + out_name
        else:
            v["image"] = "images/cars/no-photo.svg"
    write("data/vehicles-%s.json" % feed, json.dumps(d))
    return d["count"]


def build_inventory_js():
    js = read("js/inventory.js")
    js = js.replace(
        '"/api/vehicles?feed=" + encodeURIComponent(feed)',
        '"data/vehicles-" + feed + ".json"',
    )
    js = js.replace("'/images/cars/no-photo.svg'", "'images/cars/no-photo.svg'")
    write("js/inventory.js", js)


def admin_snapshot():
    con = app.db()
    app.podio_sync.ensure_meta_tables(con)
    settings = app.get_settings_dict()
    counts = {}
    for r in con.execute("SELECT status, COUNT(*) c FROM inventory GROUP BY status"):
        counts[r["status"] or "(blank)"] = r["c"]
    logs = [
        dict(r)
        for r in con.execute(
            "SELECT started_at, finished_at, status, inventory_count, "
            "roadside_count, message FROM sync_log ORDER BY id DESC LIMIT 10"
        )
    ]
    last = app.podio_sync.get_setting(con, "last_sync_at")
    status = app.podio_sync.get_setting(con, "last_sync_status")
    con.close()
    return {
        "settings": settings, "status_counts": counts, "sync_log": logs,
        "sync_status": {"last_sync_at": last, "last_sync_status": status, "running": False},
    }


def build_admin():
    htmltext = read("server/templates/admin.html")
    htmltext = re.sub(r'(href|src)="/(images|css|js)/', r"\1=\"\2/", htmltext)
    snap = admin_snapshot()
    mock = (
        "<script>(function(){"
        "var P=%s,C=%s,L=%s,S=%s;"
        "var real=window.fetch?window.fetch.bind(window):null;"
        "function J(o){return Promise.resolve({ok:true,status:200,"
        "json:function(){return Promise.resolve(o);}});}"
        "window.fetch=function(u,o){u=String(u);"
        "if(u.indexOf('/api/login')>=0)return J({ok:true,token:'preview'});"
        "if(u.indexOf('/api/sync-status')>=0)return J(S);"
        "if(u.indexOf('/api/refresh')>=0)return J({ok:true});"
        "if(u.indexOf('/api/settings')>=0)return J({settings:P,status_counts:C,sync_log:L,running:false});"
        "return real?real(u,o):J({});};"
        "try{localStorage.setItem('rto_admin_token','preview');}catch(e){}"
        "})();</script>"
    ) % (
        json.dumps(snap["settings"]), json.dumps(snap["status_counts"]),
        json.dumps(snap["sync_log"]), json.dumps(snap["sync_status"]),
    )
    note = (
        '<div style="background:#fff7e0;border:1px solid #ffd24d;color:#7a5b00;'
        'padding:10px 14px;margin:14px auto;max-width:1180px;border-radius:10px;'
        "font:600 14px/1.5 'Segoe UI',Arial,sans-serif\">This is a visual preview of "
        "the admin dashboard. It shows a snapshot of real data; the buttons are inactive "
        "here (they work on the live server).</div>"
    )
    htmltext = htmltext.replace("</head>", mock + "</head>", 1)
    htmltext = re.sub(r"(<body[^>]*>)", r"\1\n" + BANNER, htmltext, count=1)
    # drop the note just inside the dashboard wrap
    htmltext = htmltext.replace('<div class="wrap">', '<div class="wrap">\n' + note, 1)
    write("admin.html", htmltext)


def main():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    app.seed_settings()
    app.build_image_index()

    # shared assets
    write("css/site.css", read("css/site.css"))
    os.makedirs(os.path.join(OUT, "images", "cars"), exist_ok=True)
    for img in ["my-logo2.png", "my-logo.png"]:
        shutil.copy(os.path.join(SITE, "images", img), os.path.join(OUT, "images", img))
    for img in ["camaro-hero.jpg", "camaro-hero.webp", "no-photo.svg"]:
        s = os.path.join(SITE, "images", "cars", img)
        if os.path.isfile(s):
            shutil.copy(s, os.path.join(OUT, "images", "cars", img))

    # design pages (copied + path-rewritten)
    for src, dst in [
        ("index.html", "index.html"),
        ("how-it-works.html", "how-it-works.html"),
        ("submit-an-application.html", "submit-an-application.html"),
        ("orlando.html", "Orlando2.html"),
        ("orlandoRetail.html", "OrlandoRetail2.html"),
    ]:
        write(dst, rewrite(read(src)))

    # static data + preview scripts
    n_rto = build_feed("rto")
    n_ret = build_feed("retail")
    build_inventory_js()
    build_admin()

    write("README.txt",
          "Static preview of the proposed Rent To Own site changes.\n"
          "Upload this whole 'dbtest' folder to the GitHub repo, then visit:\n"
          "  /dbtest/index.html        (start here)\n"
          "  /dbtest/Orlando2.html     (RTO inventory)\n"
          "  /dbtest/OrlandoRetail2.html (retail inventory)\n"
          "  /dbtest/admin.html        (admin dashboard preview)\n")

    photos = len([f for f in os.listdir(os.path.join(OUT, "images", "cars"))])
    print("Built dbtest/ — RTO cars: %d, Retail cars: %d, images: %d"
          % (n_rto, n_ret, photos))


if __name__ == "__main__":
    main()
