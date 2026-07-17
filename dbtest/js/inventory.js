/*
 * Dynamic inventory renderer.
 * Renders vehicle cards into #inventory-grid from /api/vehicles, so the cars
 * shown on the site are generated from the SQLite database (cloned read-only
 * from Podio) instead of being hard-coded in the HTML.
 *
 * The container element controls which feed it pulls:
 *   <div id="inventory-grid" class="row" data-feed="rto"></div>
 *   <div id="inventory-grid" class="row" data-feed="retail"></div>
 */
(function () {
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function card(v, showMileage) {
    // The SOLD stamp is only drawn on cards that actually have a photo, so it
    // never lands on top of the "No photo available" placeholder. Fall back to
    // detecting a placeholder image when the server didn't send has_photo.
    var hasPhoto = v.has_photo;
    if (hasPhoto === undefined) {
      hasPhoto = !!v.image && !/(no-photo\.svg|soon\.avif)$/i.test(v.image);
    }
    var showSold = v.sold && hasPhoto;

    // Known placeholders (incl. the old COMING SOON) are normalized client-side.
    var imgSrc = hasPhoto ? v.image : "/images/cars/no-photo.svg";
    // If the image fails to load, drop to the clean placeholder AND strip the
    // SOLD stamp, so a broken photo never shows "SOLD over a placeholder".
    var onerr =
      "this.onerror=null;this.src='images/cars/no-photo.svg';" +
      "var c=this.closest('.card');if(c){c.classList.remove('sold-card');" +
      "var b=c.querySelector('.sold-badge');if(b)b.remove();}";
    var img =
      '<img src="' + esc(imgSrc) + '" class="card-img-top" alt="' +
      esc(v.title) + '" loading="lazy" onerror="' + onerr + '" />';
    var badge = showSold ? '<div class="sold-badge">SOLD!</div>' : "";
    var cardClass = showSold ? "card sold-card" : "card";

    // Price/blurb only for available cars (not for sold ones).
    var text = "RTO" + esc(v.rto);
    if (!v.sold) {
      var parts = [];
      if (v.blurb) parts.push(esc(v.blurb));
      if (v.payment && String(v.payment).trim()) {
        parts.push("RTO from $" + esc(v.payment) + "/mo");
      }
      if (showMileage && v.mileage && String(v.mileage).trim()) {
        parts.push(Number(v.mileage).toLocaleString() + " miles");
      }
      if (parts.length) {
        text += ' <span style="color:red"> ' + parts.join(" &middot; ") + " </span>";
      }
    }

    return (
      '<div class="col-md-4 mb-4">' +
        '<div class="' + cardClass + '">' +
          badge + img +
          '<div class="card-body">' +
            '<h5 class="card-title">' + esc(v.title) + "</h5>" +
            '<p class="card-text">' + text + "</p>" +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  function render() {
    var grid = document.getElementById("inventory-grid");
    if (!grid) return;
    var feed = grid.getAttribute("data-feed") || "rto";
    grid.innerHTML =
      '<div class="col-12 text-center text-muted py-5">Loading inventory…</div>';
    fetch("data/vehicles-" + feed + ".json")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.vehicles || !data.vehicles.length) {
          grid.innerHTML =
            '<div class="col-12 text-center text-muted py-5">' +
            "No vehicles are currently available. Please check back soon or call us.</div>";
          return;
        }
        grid.innerHTML = data.vehicles
          .map(function (v) { return card(v, data.show_mileage); })
          .join("");
      })
      .catch(function () {
        grid.innerHTML =
          '<div class="col-12 text-center text-danger py-5">' +
          "Inventory is temporarily unavailable. Please call us at 321-319-4300.</div>";
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
