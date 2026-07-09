/*
 * Inventory renderer (static, GitHub Pages friendly).
 * Renders vehicle cards into #inventory-grid from a committed JSON file that is
 * generated from the SQLite/Podio database by server/publish.py:
 *   <div id="inventory-grid" class="row" data-feed="rto"></div>    -> data/vehicles-rto.json
 *   <div id="inventory-grid" class="row" data-feed="retail"></div> -> data/vehicles-retail.json
 *
 * Also wires the homepage "See This Car" deep-link: opening orlando.html#rto<n>
 * scrolls to and briefly highlights that vehicle's card.
 */
(function () {
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function cardId(rto) {
    return "rto" + String(rto == null ? "" : rto).replace(/[^0-9A-Za-z]/g, "");
  }

  function card(v) {
    var hasPhoto = v.has_photo;
    if (hasPhoto === undefined) {
      hasPhoto = !!v.image && !/(no-photo\.svg|soon\.avif)$/i.test(v.image);
    }
    var imgSrc = hasPhoto ? v.image : "images/cars/no-photo.svg";

    // Sold cars get a red SOLD! stamp on the full-color photo (never on a
    // placeholder). Available cars get no stamp — just the short red caption.
    var showSold = v.sold && hasPhoto;
    var stamp = showSold ? '<div class="sold-stamp">SOLD!</div>' : "";

    var onerr =
      "this.onerror=null;this.src='images/cars/no-photo.svg';" +
      "var c=this.closest('.card');if(c){var s=c.querySelector('.sold-stamp');" +
      "if(s)s.remove();}";
    var img =
      '<img src="' + esc(imgSrc) + '" class="card-img-top" alt="' +
      esc(v.title) + '" loading="lazy" onerror="' + onerr + '" />';

    // Caption: RTO number, plus a short red down/cash line for available cars.
    var caption = "RTO" + esc(v.rto);
    if (!v.sold && v.caption) {
      caption += ' <span class="rto-price">' + esc(v.caption) + "</span>";
    }

    return (
      '<div class="col-md-4 mb-4">' +
        '<div class="card" id="' + cardId(v.rto) + '">' +
          stamp + img +
          '<div class="card-body">' +
            '<h5 class="card-title">' + esc(v.title) + "</h5>" +
            '<p class="card-text">' + caption + "</p>" +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  // "See This Car": scroll to + highlight the car named in location.hash.
  function focusCar() {
    var h = location.hash;
    if (!h || h.length < 2) return;
    var el = document.getElementById(h.slice(1).toLowerCase());
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.remove("car-highlight");
    void el.offsetWidth; // restart the animation if re-triggered
    el.classList.add("car-highlight");
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
        grid.innerHTML = data.vehicles.map(card).join("");
        focusCar(); // run after the cards exist
      })
      .catch(function () {
        grid.innerHTML =
          '<div class="col-12 text-center text-danger py-5">' +
          "Inventory is temporarily unavailable. Please call us at 321-319-4300.</div>";
      });
  }

  window.addEventListener("hashchange", focusCar);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
