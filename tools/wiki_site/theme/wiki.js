// Wiki site: theme toggle, mobile sidebar, TOC scroll spy, Mermaid drawing,
// tracker charts and client-side search over assets/search-index.json (titles, headings, text).
// Adapted from nand2anything tools/wiki_site/theme/wiki.js @ 085ef4b (same
// owner; see PROVENANCE.md): search reads a JSON index and matches page text.
(function () {
  "use strict";
  var doc = document.documentElement;
  var root = doc.getAttribute("data-root") || "";

  // ---- dark mode: a stored choice wins; otherwise follow the OS ----
  function stored() { try { return localStorage.getItem("theme"); } catch (e) { return null; } }
  function setTheme(theme) {
    doc.dataset.theme = theme;
    drawDiagrams();
    if (window.drawTrackerCharts) window.drawTrackerCharts();
  }
  document.querySelector(".theme-toggle").addEventListener("click", function () {
    var next = doc.dataset.theme === "dark" ? "light" : "dark";
    setTheme(next);
    try { localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
  });
  var media = matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", function (e) {
    if (!stored()) setTheme(e.matches ? "dark" : "light");
  });

  // ---- Mermaid diagrams: load the vendored bundle only on pages that have
  // one, and redraw from the kept source in the current theme ----
  var diagrams = Array.prototype.slice.call(document.querySelectorAll("pre.mermaid"));
  var drawing = Promise.resolve();
  function drawDiagrams() {
    if (!window.mermaid || !diagrams.length) return;
    drawing = drawing.then(function () {
      diagrams.forEach(function (d) {
        if (d.dataset.source === undefined) d.dataset.source = d.textContent;
        d.removeAttribute("data-processed");
        d.textContent = d.dataset.source;
      });
      window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict",
        theme: doc.dataset.theme === "dark" ? "dark" : "default" });
      return window.mermaid.run({ nodes: diagrams });
    }).catch(function (e) { if (window.console) console.error(e); });
  }
  if (diagrams.length) {
    var ms = document.createElement("script");
    ms.src = root + "assets/mermaid.min.js";
    ms.onload = drawDiagrams;
    document.head.appendChild(ms);
  }

  // ---- tracker charts: load the vendored Chart.js, then tracker.js, only on
  // pages that carry tracker data ----
  function loadScript(name, then) {
    var s = document.createElement("script");
    s.src = root + "assets/" + name;
    s.onload = then;
    document.head.appendChild(s);
  }
  if (document.getElementById("tracker-data")) {
    loadScript("chart.umd.js", function () { loadScript("tracker.js"); });
  }

  // ---- mobile sidebar ----
  document.querySelector(".navbar-toggle").addEventListener("click", function () {
    document.body.classList.toggle("sidebar-open");
  });
  // Centre the current page in the sidebar without scrolling the window.
  var sidebar = document.querySelector(".sidebar");
  var active = sidebar.querySelector(".menu a.active");
  if (active) {
    var a = active.getBoundingClientRect(), s = sidebar.getBoundingClientRect();
    sidebar.scrollTop += a.top - s.top - s.height / 2;
  }

  // ---- "On this page" scroll spy ----
  var tocLinks = Array.prototype.slice.call(document.querySelectorAll(".toc a"));
  var heads = tocLinks.map(function (a) { return document.getElementById(decodeURIComponent(a.hash.slice(1))); });
  function spy() {
    var current = -1;
    for (var i = 0; i < heads.length; i++) {
      if (heads[i] && heads[i].getBoundingClientRect().top < 100) current = i;
    }
    if (current < 0 && heads.length) current = 0;
    tocLinks.forEach(function (a, i) { a.classList.toggle("active", i === current); });
  }
  if (tocLinks.length) {
    var queued = false;
    window.addEventListener("scroll", function () {
      if (!queued) { queued = true; requestAnimationFrame(function () { queued = false; spy(); }); }
    }, { passive: true });
    spy();
  }

  // ---- search: titles rank before headings before page text ----
  var input = document.querySelector(".search input");
  var list = document.querySelector(".search-results");
  var selected = 0;
  var index = null;
  function load(then) {
    if (index) return then();
    fetch(root + "assets/search-index.json")
      .then(function (r) { return r.json(); })
      .then(function (data) { index = data; then(); })
      .catch(function () { index = []; then(); });
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }
  function render() {
    var q = input.value.trim().toLowerCase();
    list.textContent = "";
    if (!q) { list.hidden = true; return; }
    var words = q.split(/\s+/);
    function match(t) { t = t.toLowerCase(); return words.every(function (w) { return t.indexOf(w) >= 0; }); }
    var hits = [];
    (index || []).forEach(function (p) {
      if (match(p.title)) hits.push({ score: 0, title: p.title, page: p.url, url: p.url });
      p.headings.forEach(function (h) {
        if (match(h[0])) hits.push({ score: 1, title: h[0], page: p.title, url: p.url + "#" + h[1] });
      });
      if (!match(p.title) && match(p.text)) {
        var at = p.text.toLowerCase().indexOf(words[0]);
        hits.push({ score: 2, title: p.title, text: p.text.slice(Math.max(0, at - 30), at + 90), url: p.url });
      }
    });
    hits.sort(function (a, b) { return a.score - b.score; });
    hits = hits.slice(0, 20);
    selected = 0;
    if (!hits.length) list.appendChild(el("li", "empty", "No matches"));
    hits.forEach(function (h, i) {
      var li = el("li"), a = el("a", i === 0 ? "selected" : "", h.title);
      a.href = root + h.url;
      if (h.page) a.appendChild(el("span", "hit-page", h.page));
      if (h.text) a.appendChild(el("span", "hit-text", h.text));
      li.appendChild(a);
      list.appendChild(li);
    });
    list.hidden = false;
  }
  input.addEventListener("focus", function () { load(render); });
  input.addEventListener("input", function () { load(render); });
  input.addEventListener("keydown", function (e) {
    var links = list.querySelectorAll("a");
    if (e.key === "Escape") { input.value = ""; render(); input.blur(); }
    else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && links.length) {
      e.preventDefault();
      links[selected].classList.remove("selected");
      selected = (selected + (e.key === "ArrowDown" ? 1 : links.length - 1)) % links.length;
      links[selected].classList.add("selected");
      links[selected].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && links.length) { window.location.href = links[selected].href; }
  });
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".search")) list.hidden = true;
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== input && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) {
      e.preventDefault();
      input.focus();
    }
  });
})();
