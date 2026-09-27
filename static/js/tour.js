/*
 * ACG Lead Time Control Tower — guided product tour.
 * Vanilla JS, no dependencies. Spotlights a sidebar nav item or dashboard
 * panel per step; steps whose page differs from the current one trigger a
 * navigation and resume automatically on load via sessionStorage.
 */
(function () {
  "use strict";

  var CTX = window.ACG_CONTEXT || {role: "", isAdmin: false, endpoint: ""};

  var FUNCTIONAL_ROLES = ["Engineering", "Procurement", "Production", "Automation", "Quality", "Planner"];

  var STEPS = [
    {
      title: "Welcome to the Control Tower",
      body: "This is the digital backbone behind the Shirwal lead-time case: it reads plant data and turns it into what to fix today. This two-minute tour walks every page. Skip anytime &mdash; reopen it from the <b>?</b> icon top-right."
    },
    {
      path: "/", selector: '[data-tour="my-queue"]', roles: FUNCTIONAL_ROLES,
      title: "Your queue",
      body: "Filtered to your own role's stages and exceptions, so you open on what's yours to fix rather than the whole plant's. Everyone still shares the same numbers below &mdash; just prioritised for you first."
    },
    {
      path: "/", selector: '[data-tour="kpi-row"]',
      title: "Live KPIs",
      body: "Lead time, on-time delivery, first-time-right, cost of poor quality, WIP and vendor OTD &mdash; all computed live from uploaded plant data, never a stored number."
    },
    {
      path: "/", selector: '[data-tour="action-board"]',
      title: "Action board",
      body: "The daily huddle list: stages past plan and purchase orders overdue, ranked by exposure. This is what the Plant Head reviews each morning."
    },
    {
      path: "/", selector: '[data-tour="risk-table"]',
      title: "Delivery risk",
      body: "Every open order's forecast dispatch date against its customer commitment, banded On track &#8594; Watch &#8594; At risk &#8594; Critical."
    },
    {
      path: "/", selector: '[data-tour="bottleneck"]',
      title: "Bottleneck ranking",
      body: "Stages ranked by weeks over best-in-class, weighted by utilisation and logged delay &mdash; not simply the longest stage."
    },
    {
      path: "/jobs", selector: '[data-tour="nav-jobs"]',
      title: "Order Book",
      body: "Every order across the six stages &mdash; Design, Sourcing, Fabrication, Assembly, Electrical/Automation, Testing &amp; FAT &mdash; with a forecast dispatch date and risk band, filterable and exportable."
    },
    {
      path: "/schedule", selector: '[data-tour="nav-schedule"]',
      title: "Capacity &amp; Promise",
      body: "Stage load against WIP capacity for the next 8&#8211;20 weeks, the same bottleneck ranking, and a P80 capable-to-promise date for a new enquiry."
    },
    {
      path: "/schedule", selector: '[data-tour="quote-tool"]', roles: ["Sales", "Plant Head", "Admin", "Planner"],
      title: "Quote a new enquiry",
      body: "Enter an equipment type and customer to get today's honest P80 promise date, then log it. This is Sales' own tool for the moment a customer asks &ldquo;when can you deliver?&rdquo;"
    },
    {
      path: "/schedule", selector: '[data-tour="quote-log"]', roles: ["Sales", "Plant Head", "Admin"],
      title: "Quote log &amp; win rate",
      body: "Every quote given, and once marked Won or Lost, a live win rate by promised lead time &mdash; testing the deck's own assumption that a faster date wins more orders."
    },
    {
      path: "/floor", selector: '[data-tour="nav-floor"]',
      title: "Shop Floor",
      body: "Record a stage start or finish, rework and delay cause, signed and timestamped to your account &mdash; the same validation as an upload."
    },
    {
      path: "/procurement", selector: '[data-tour="nav-procurement"]',
      title: "Procurement",
      body: "Vendor scorecard, procurement cycle time by item category, and the expedite list of overdue purchase orders blocking an order."
    },
    {
      path: "/root-cause", selector: '[data-tour="nav-root-cause"]',
      title: "Root Cause",
      body: "The fishbone from the case, live: a Pareto of delay days, a cause-by-stage matrix, and where rework concentrates."
    },
    {
      path: "/root-cause", selector: '[data-tour="quality-holds"]', roles: ["Quality", "Plant Head", "Admin"],
      title: "Quality holds",
      body: "Quality can place a hold on any stage from an order's detail page. A held stage cannot be marked complete on Shop Floor until Quality releases it &mdash; real teeth for the deck's \"quality at source\" lever."
    },
    {
      path: "/benchmark", selector: '[data-tour="nav-benchmark"]',
      title: "Benchmarking",
      body: "Live stage times against industry and best-in-class ranges, plus the reference practices borrowed from Bosch, GE, Siemens and Danaher."
    },
    {
      path: "/simulator", selector: '[data-tour="nav-simulator"]',
      title: "Lever Simulator",
      body: "Drag adoption on the nine improvement levers and watch lead time, KPI position, benefit and payback rebuild live &mdash; the tool behind the deck's business case."
    },
    {
      path: "/roadmap", selector: '[data-tour="nav-roadmap"]',
      title: "Roadmap &amp; KPIs",
      body: "The three-year glide path, 34 &#8594; 27 &#8594; 22 &#8594; 17 weeks, tracked live against what the plant is actually running at."
    },
    {
      path: "/business-case", selector: '[data-tour="nav-business-case"]',
      title: "Business Case",
      body: "The &#8377;5.5 Cr investment, &#8377;6.4 Cr run-rate EBITDA, payback, scenario stress test and risk register &mdash; the numbers behind the pilot ask."
    },
    {
      path: "/data", selector: '[data-tour="nav-data"]',
      title: "Data Ops",
      body: "Upload a planning or procurement export, download templates, and see every ingestion with rejected rows kept for download and re-upload."
    },
    {
      path: "/setup", selector: '[data-tour="nav-setup"]', adminOnly: true,
      title: "Plant Setup",
      body: "Stages, WIP capacity, KPI targets and financial assumptions &mdash; edited here, not in code. Admin and Plant Head accounts only."
    },
    {
      path: "/audit", selector: '[data-tour="nav-audit"]', adminOnly: true,
      title: "Audit Trail",
      body: "Every floor entry, upload and setting change, attributed to a signed-in account &mdash; the governance record a plant roll-out needs."
    },
    {
      title: "That's the tour",
      body: "Explore any page from the sidebar. Reopen this tour anytime from the <b>?</b> icon. When ACG is ready, this same tool points at a live SAP extract instead of the demo dataset."
    }
  ];

  function getSteps() {
    return STEPS.filter(function (s) {
      if (s.adminOnly && !CTX.isAdmin) return false;
      if (s.roles && s.roles.indexOf(CTX.role) === -1) return false;
      return true;
    });
  }

  var SS_KEY = "acgTourState";
  var LS_PROMPT = "acgTourPromptSeen";
  var LS_DONE = "acgTourCompleted";

  function saveState(st) {
    try { sessionStorage.setItem(SS_KEY, JSON.stringify(st)); } catch (e) {}
  }
  function loadState() {
    try {
      var raw = sessionStorage.getItem(SS_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }
  function clearState() {
    try { sessionStorage.removeItem(SS_KEY); } catch (e) {}
  }

  var root = null;

  function removeUI() {
    if (root && root.parentNode) root.parentNode.removeChild(root);
    root = null;
    window.removeEventListener("resize", reposition);
    window.removeEventListener("scroll", reposition, true);
    document.removeEventListener("keydown", onKey);
  }

  function onKey(e) {
    if (e.key === "Escape") skip();
  }

  var currentEl = null;

  function reposition() {
    if (!root) return;
    var spot = root.querySelector(".acgtour-spot");
    var card = root.querySelector(".acgtour-card");
    if (!currentEl) {
      spot.style.display = "none";
      card.classList.add("center");
      return;
    }
    var r = currentEl.getBoundingClientRect();
    spot.style.display = "block";
    spot.style.top = Math.max(4, r.top - 6) + "px";
    spot.style.left = Math.max(4, r.left - 6) + "px";
    spot.style.width = (r.width + 12) + "px";
    spot.style.height = (r.height + 12) + "px";

    card.classList.remove("center");
    var cw = card.offsetWidth, ch = card.offsetHeight;
    var top = r.bottom + 14;
    if (top + ch > window.innerHeight - 12) top = Math.max(12, r.top - ch - 14);
    var left = r.left;
    if (left + cw > window.innerWidth - 12) left = window.innerWidth - cw - 12;
    if (left < 12) left = 12;
    card.style.top = top + "px";
    card.style.left = left + "px";
  }

  function render(index, steps) {
    if (!root) build();
    var step = steps[index];
    currentEl = step.selector ? document.querySelector(step.selector) : null;

    var card = root.querySelector(".acgtour-card");
    card.querySelector(".acgtour-title").innerHTML = step.title;
    card.querySelector(".acgtour-body").innerHTML = step.body;
    card.querySelector(".acgtour-count").textContent = (index + 1) + " / " + steps.length;
    var dots = card.querySelector(".acgtour-dots");
    dots.innerHTML = "";
    steps.forEach(function (_, i) {
      var d = document.createElement("i");
      if (i === index) d.className = "on";
      dots.appendChild(d);
    });
    card.querySelector(".acgtour-back").style.visibility = index === 0 ? "hidden" : "visible";
    card.querySelector(".acgtour-next").textContent = index === steps.length - 1 ? "Finish" : "Next";

    if (currentEl) {
      currentEl.scrollIntoView({behavior: "smooth", block: "center"});
      setTimeout(reposition, 220);
    } else {
      reposition();
    }
  }

  function build() {
    root = document.createElement("div");
    root.id = "acgTourRoot";
    root.innerHTML =
      '<div class="acgtour-spot"></div>' +
      '<div class="acgtour-card center">' +
      '  <div class="acgtour-title"></div>' +
      '  <div class="acgtour-body"></div>' +
      '  <div class="acgtour-foot">' +
      '    <div class="acgtour-dots"></div>' +
      '    <span class="acgtour-count"></span>' +
      '  </div>' +
      '  <div class="acgtour-actions">' +
      '    <button type="button" class="acgtour-skip">Skip tour</button>' +
      '    <div class="acgtour-nav">' +
      '      <button type="button" class="acgtour-back ghost">Back</button>' +
      '      <button type="button" class="acgtour-next">Next</button>' +
      '    </div>' +
      '  </div>' +
      '</div>';
    document.body.appendChild(root);
    root.querySelector(".acgtour-skip").addEventListener("click", skip);
    root.querySelector(".acgtour-next").addEventListener("click", function () { advance(1); });
    root.querySelector(".acgtour-back").addEventListener("click", function () { advance(-1); });
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    document.addEventListener("keydown", onKey);
  }

  function goTo(index) {
    var steps = getSteps();
    if (index < 0) index = 0;
    if (index >= steps.length) { finish(); return; }
    var step = steps[index];
    saveState({active: true, index: index});
    if (step.path && step.path !== window.location.pathname) {
      window.location.href = step.path;
      return;
    }
    render(index, steps);
  }

  function advance(delta) {
    var st = loadState() || {index: 0};
    goTo(st.index + delta);
  }

  function start() {
    goTo(0);
  }

  function finish() {
    clearState();
    try { localStorage.setItem(LS_DONE, "1"); } catch (e) {}
    removeUI();
  }

  function skip() {
    clearState();
    try { localStorage.setItem(LS_PROMPT, "1"); } catch (e) {}
    removeUI();
  }

  function showPrompt() {
    var toast = document.createElement("div");
    toast.className = "acgtour-toast";
    toast.innerHTML =
      '<div><b>New here?</b> Take the 2-minute guided tour of the Control Tower.</div>' +
      '<div class="acgtour-toast-actions">' +
      '  <button type="button" class="ghost acgtour-toast-dismiss">Not now</button>' +
      '  <button type="button" class="acgtour-toast-start">Take the tour</button>' +
      '</div>';
    document.body.appendChild(toast);
    try { localStorage.setItem(LS_PROMPT, "1"); } catch (e) {}
    toast.querySelector(".acgtour-toast-dismiss").addEventListener("click", function () {
      toast.parentNode.removeChild(toast);
    });
    toast.querySelector(".acgtour-toast-start").addEventListener("click", function () {
      toast.parentNode.removeChild(toast);
      start();
    });
    setTimeout(function () { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 15000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var helpBtn = document.getElementById("acgTourHelp");
    if (helpBtn) helpBtn.addEventListener("click", start);

    var state = loadState();
    if (state && state.active) {
      var steps = getSteps();
      var step = steps[state.index];
      if (step && (!step.path || step.path === window.location.pathname)) {
        render(state.index, steps);
        return;
      }
      clearState();
    }

    var promptSeen = false, tourDone = false;
    try {
      promptSeen = !!localStorage.getItem(LS_PROMPT);
      tourDone = !!localStorage.getItem(LS_DONE);
    } catch (e) {}
    if (!promptSeen && !tourDone && CTX.endpoint === "dashboard" && CTX.hasData) {
      setTimeout(showPrompt, 900);
    }
  });

  window.ACGTour = {start: start, skip: skip};
})();
