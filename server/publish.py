#!/usr/bin/env python3
"""
Publish the current inventory to the STATIC live site (GitHub Pages).

GitHub Pages can't run this backend, so the live site reads committed files:
  * data/vehicles-rto.json + data/vehicles-retail.json  (inventory + Featured block)
  * images/inventory/<rto>.jpg                           (resized web photos)

This script regenerates those from the local SQLite DB. Typical flow:
  1. python3 app.py           # (or the admin "Refresh now") to sync Podio -> DB
  2. python3 publish.py       # regenerate data/ + images/inventory/
  3. git add -A && git commit && git push    # go live

Run it after a sync; then commit + push so the live site reflects Podio.
"""
import json
import os
import re
import subprocess
import urllib.parse

import app

ROOT = app.SITE_ROOT
DATA_DIR = os.path.join(ROOT, "data")
IMG_DIR = os.path.join(ROOT, "images", "inventory")
MAXW = "900"  # resized web photo max dimension (cards are ~350px)


def resize(src_web_path, out_name):
    """Resize a source photo into images/inventory/<out_name>; return True if present."""
    src = os.path.join(ROOT, urllib.parse.unquote(src_web_path).lstrip("/"))
    dst = os.path.join(IMG_DIR, out_name)
    if os.path.exists(dst):
        return True
    if not os.path.isfile(src):
        return False
    subprocess.run(
        ["sips", "-Z", MAXW, "-s", "format", "jpeg", src, "--out", dst],
        check=False, capture_output=True,
    )
    return os.path.exists(dst)


def main():
    app.seed_settings()
    app.build_image_index()
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(IMG_DIR, exist_ok=True)

    for feed in ("rto", "retail"):
        d = app.vehicles_feed(feed)
        for v in d["vehicles"]:
            if v.get("has_photo"):
                name = re.sub(r"[^A-Za-z0-9]", "", str(v["rto"])) + ".jpg"
                if resize(v["image"], name):
                    v["image"] = "images/inventory/" + name
                else:
                    v["image"] = "images/cars/no-photo.svg"
                    v["has_photo"] = False
            else:
                v["image"] = "images/cars/no-photo.svg"
        with open(os.path.join(DATA_DIR, "vehicles-%s.json" % feed), "w") as f:
            json.dump(d, f)
        n_sold = sum(1 for v in d["vehicles"] if v["sold"])
        print("data/vehicles-%s.json: %d vehicles (%d sold, %d available)"
              % (feed, d["count"], n_sold, d["count"] - n_sold))

    print("images/inventory/: %d photos" % len(os.listdir(IMG_DIR)))
    print("Done. Now: git add -A && git commit && git push")


if __name__ == "__main__":
    main()
