/*
 * ACG Control Tower — command-palette search.
 * Ctrl/Cmd+K opens it from anywhere; searches orders and purchase orders
 * live via /api/search, arrow keys navigate, Enter opens the result.
 */
(function () {
  "use strict";

  var root = null, input = null, list = null, items = [], activeIndex = -1, debounceTimer = null;

  function build() {
    root = document.createElement("div");
    root.id = "acgSearchRoot";
    root.innerHTML =
      '<div class="acgsearch-backdrop"></div>' +
      '<div class="acgsearch-box">' +
      '  <div class="acgsearch-input-row">' +
      '    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>' +
      '    <input type="text" placeholder="Search orders, POs, vendors&hellip;" autocomplete="off" spellcheck="false">' +
      '    <kbd>Esc</kbd>' +
      '  </div>' +
      '  <div class="acgsearch-results"></div>' +
      '</div>';
    document.body.appendChild(root);
    input = root.querySelector("input");
    list = root.querySelector(".acgsearch-results");
    root.querySelector(".acgsearch-backdrop").addEventListener("click", close);
    input.addEventListener("input", onInput);
    input.addEventListener("keydown", onKey);
  }

  function open() {
    if (!root) build();
    root.classList.add("on");
    input.value = "";
    renderEmpty();
    setTimeout(function () { input.focus(); }, 10);
  }

  function close() {
    if (root) root.classList.remove("on");
  }

  function renderEmpty() {
    list.innerHTML = '<div class="acgsearch-hint">Type at least 2 characters &mdash; searches job number, customer, equipment, PO number, item and vendor.</div>';
    items = [];
    activeIndex = -1;
  }

  function renderResults(data) {
    items = [];
    var html = "";
    if (data.jobs && data.jobs.length) {
      html += '<div class="acgsearch-group">Orders</div>';
      data.jobs.forEach(function (j) {
        var idx = items.length;
        items.push({url: "/jobs/" + j.id});
        html += '<div class="acgsearch-item" data-idx="' + idx + '">' +
          '<b class="mono">' + esc(j.job_no) + '</b> ' + esc(j.customer) +
          '<span class="acgsearch-meta">' + esc(j.equipment_type) + ' &middot; ' + esc(j.status) + '</span></div>';
      });
    }
    if (data.purchase_orders && data.purchase_orders.length) {
      html += '<div class="acgsearch-group">Purchase orders</div>';
      data.purchase_orders.forEach(function (p) {
        var idx = items.length;
        items.push({url: p.job_id ? ("/jobs/" + p.job_id) : "/procurement"});
        html += '<div class="acgsearch-item" data-idx="' + idx + '">' +
          '<b class="mono">' + esc(p.po_no) + '</b> ' + esc(p.item) +
          '<span class="acgsearch-meta">' + esc(p.vendor) + (p.job_no ? (' &middot; ' + esc(p.job_no)) : '') + '</span></div>';
      });
    }
    if (!items.length) {
      html = '<div class="acgsearch-hint">No matches.</div>';
    }
    list.innerHTML = html;
    activeIndex = items.length ? 0 : -1;
    highlight();
    Array.prototype.forEach.call(list.querySelectorAll(".acgsearch-item"), function (el) {
      el.addEventListener("mouseenter", function () { activeIndex = parseInt(el.dataset.idx, 10); highlight(); });
      el.addEventListener("click", function () { go(parseInt(el.dataset.idx, 10)); });
    });
  }

  function highlight() {
    Array.prototype.forEach.call(list.querySelectorAll(".acgsearch-item"), function (el) {
      el.classList.toggle("on", parseInt(el.dataset.idx, 10) === activeIndex);
    });
  }

  function go(idx) {
    if (idx < 0 || idx >= items.length) return;
    window.location.href = items[idx].url;
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function onInput() {
    var q = input.value.trim();
    clearTimeout(debounceTimer);
    if (q.length < 2) { renderEmpty(); return; }
    debounceTimer = setTimeout(function () {
      fetch("/api/search?q=" + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(renderResults)
        .catch(function () { list.innerHTML = '<div class="acgsearch-hint">Search failed. Try again.</div>'; });
    }, 150);
  }

  function onKey(e) {
    if (e.key === "Escape") { close(); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); if (items.length) { activeIndex = (activeIndex + 1) % items.length; highlight(); } }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (items.length) { activeIndex = (activeIndex - 1 + items.length) % items.length; highlight(); } }
    else if (e.key === "Enter") { e.preventDefault(); go(activeIndex); }
  }

  document.addEventListener("keydown", function (e) {
    var mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === "k") {
      e.preventDefault();
      open();
    }
  });

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("acgSearchTrigger");
    if (btn) btn.addEventListener("click", open);
  });

  window.ACGSearch = {open: open, close: close};
})();
