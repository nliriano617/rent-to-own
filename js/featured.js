/*
 * Featured Vehicles showroom (homepage).
 * Renders 2-3 "showroom" cars into #featured-grid, chosen from the public RTO
 * feed (data/vehicles-rto.json).
 *
 * WHICH cars appear is driven by the "featured" block in that JSON:
 *   "featured": {
 *     "enabled": true,
 *     "mode": "deals" | "newest" | "random" | "manual",
 *     "count": 3,                 // 2 or 3
 *     "max_payment": 260,         // optional filter, null/"" = no limit
 *     "manual_rtos": ["9035", …]  // used only when mode === "manual"
 *   }
 * If the block is absent, sensible defaults are used so the showroom still
 * renders. The whole section hides itself when disabled or when no available
 * vehicles match.
 */
(function () {
  var INVENTORY_PAGE = "orlando.html";

  var DEFAULTS = {
    enabled: true,
    mode: "deals",
    count: 3,
    max_payment: null,
    manual_rtos: [],
  };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function num(v) {
    var n = parseFloat(String(v == null ? "" : v).replace(/[^0-9.]/g, ""));
    return isNaN(n) ? null : n;
  }

  function hideSection() {
    var sec = document.getElementById("featured");
    if (sec) sec.style.display = "none";
  }

  // Only cars a customer can actually get: available (not sold) and with a photo.
  function isShowable(v) {
    var hasPhoto = v.has_photo;
    if (hasPhoto === undefined) {
      hasPhoto = !!v.image && !/(no-photo\.svg|soon\.avif)$/i.test(v.image);
    }
    return !v.sold && hasPhoto;
  }

  function pickFeatured(vehicles, cfg) {
    var pool = vehicles.filter(isShowable);

    var maxPay = num(cfg.max_payment);
    if (maxPay != null) {
      pool = pool.filter(function (v) {
        var p = num(v.payment);
        return p != null && p <= maxPay;
      });
    }

    var count = cfg.count === 2 ? 2 : 3;

    if (cfg.mode === "manual") {
      var wanted = (cfg.manual_rtos || []).map(function (r) {
        return String(r).trim();
      }).filter(Boolean);
      var byRto = {};
      pool.forEach(function (v) { byRto[String(v.rto)] = v; });
      var picked = wanted.map(function (r) { return byRto[r]; }).filter(Boolean);
      return picked.slice(0, count);
    }

    if (cfg.mode === "newest") {
      pool.sort(function (a, b) { return (num(b.year) || 0) - (num(a.year) || 0); });
      return pool.slice(0, count);
    }

    if (cfg.mode === "random") {
      var arr = pool.slice();
      for (var i = arr.length - 1; i > 0; i--) {
        var j = Math.floor(Math.random() * (i + 1));
        var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
      }
      return arr.slice(0, count);
    }

    // "deals" (default): most affordable first. Cars without a payment sink last.
    pool.sort(function (a, b) {
      var pa = num(a.payment), pb = num(b.payment);
      if (pa == null) return 1;
      if (pb == null) return -1;
      return pa - pb;
    });
    return pool.slice(0, count);
  }

  function card(v) {
    var onerr =
      "this.onerror=null;this.src='images/cars/no-photo.svg';";
    var img =
      '<img src="' + esc(v.image) + '" class="card-img-top" alt="' +
      esc(v.title) + '" loading="lazy" onerror="' + onerr + '" />';

    var line = "RTO" + esc(v.rto);
    var extras = [];
    if (v.payment && String(v.payment).trim()) {
      extras.push("RTO from $" + esc(v.payment) + "/mo");
    }
    if (extras.length) {
      line += ' <span style="color:#e11d2a"> ' + extras.join(" &middot; ") + " </span>";
    }

    return (
      '<div class="col-md-4 mb-4">' +
        '<div class="card h-100">' +
          img +
          '<div class="card-body d-flex flex-column">' +
            '<h5 class="card-title">' + esc(v.title) + "</h5>" +
            '<p class="card-text">' + line + "</p>" +
            '<a href="' + INVENTORY_PAGE + '" class="btn btn-accent btn-sm mt-auto">' +
              "See This Car</a>" +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  function render() {
    var grid = document.getElementById("featured-grid");
    if (!grid) return;

    fetch("data/vehicles-rto.json")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var cfg = Object.assign({}, DEFAULTS, data.featured || {});
        if (cfg.enabled === false || String(cfg.enabled) === "false") {
          hideSection();
          return;
        }
        var picks = pickFeatured(data.vehicles || [], cfg);
        if (!picks.length) { hideSection(); return; }
        grid.innerHTML = picks.map(card).join("");
      })
      .catch(function () { hideSection(); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
