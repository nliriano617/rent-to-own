# Rent To Own — Dynamic Inventory Server

This folder turns the previously static car-inventory site into a database-driven
site. Vehicle cards are generated **dynamically** from a local SQLite database
(`roadside.db`) that is cloned **read-only** from Podio. Nothing is ever written
back to Podio — the sync only ever reads (`GET` / `filter`).

## What's here

| File | Purpose |
|---|---|
| `app.py` | The web server (Python standard library only — no pip installs). Serves the site, the `/api/*` JSON API, the `/admin` console, and runs the background refresh scheduler. |
| `podio_sync.py` | Read-only Podio → SQLite sync (consolidates the old `clone_podio.py` + `clone_inventory.py`). Re-runnable. |
| `roadside.db` | The local SQLite clone (vehicles + settings + sync history). Git-ignored. |
| `config.json` | Podio credentials + admin password + host/port. **Git-ignored.** |
| `config.example.json` | Template for `config.json`. |
| `templates/admin.html` | The admin console UI. |

## Running

```bash
cd server
python3 app.py
```

Then open:

* Public RTO inventory: <http://127.0.0.1:8000/orlando.html>
* Public Retail inventory: <http://127.0.0.1:8000/orlandoRetail.html>
* Admin console: <http://127.0.0.1:8000/admin>

The admin password is the `admin_password` value in `config.json`
(default `rtoadmin` — change it).

## How it fits together

1. `app.py` serves the existing HTML/CSS/images from the repo root.
2. `orlando.html` and `orlandoRetail.html` no longer hard-code cars. Each contains
   `<div id="inventory-grid" data-feed="rto|retail">` which `js/inventory.js` fills
   by calling `GET /api/vehicles?feed=...`.
3. `/api/vehicles` reads the `inventory` table, filtered to the **public statuses**
   configured in the admin console, and maps each vehicle's RTO number to a local
   photo in `images/cars/`.
4. A background scheduler re-runs the read-only Podio sync on the **update frequency**
   set in the admin console.

## Admin settings

* **Update frequency (minutes)** — how often Podio is re-pulled.
* **Automatic updates** — enable/disable the scheduler.
* **Public statuses** — which Podio statuses appear on the site (default
  `Available, On Rent`).
* **Retail keyword** — vehicles whose comments contain this word also show on the
  Retail (cash sale) page.
* **Site phone**, **Show mileage** — display options.
* **Refresh now** — trigger an immediate read-only Podio pull.

## Security notes

* `config.json` (Podio credentials) and `roadside.db` are git-ignored.
* The Podio app token is **read scoped** to the Orlando space; the sync code only
  calls read endpoints.
* Change `admin_password` before exposing this server beyond localhost.
