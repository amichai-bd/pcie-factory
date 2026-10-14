// Tracker charts: draws every <canvas data-chart> from the page's
// <script id="tracker-data"> JSON with the vendored Chart.js (assets/chart.umd.js,
// loaded by wiki.js). Trends share one x axis, main commits in order, labelled
// by short SHA with the date in the tooltip; histograms are bar charts.
// Colours come from CSS custom properties, so a theme change redraws in the
// new palette. See wiki/tools/wiki_site/SPEC.md#tracker.
(function () {
  "use strict";
  var node = document.getElementById("tracker-data");
  if (!node || !window.Chart) return;
  var data = JSON.parse(node.textContent);
  var axis = data.axis || [];
  var drawn = [];

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function colour(c) { return css(/^\d+$/.test(c) ? "--series-" + c : "--status-" + c); }
  function fmt(v) {
    if (v === null || v === undefined) return "–";
    var a = Math.abs(v);
    var s = Number.isInteger(v) || a >= 1000 ? Math.round(v).toLocaleString("en-US")
      : a >= 100 ? v.toFixed(1) : String(Number(v.toPrecision(3)));
    return s.replace(/^-/, "−");
  }
  function commit(i) { var a = axis[Math.round(i)]; return a ? a[0] : ""; }

  function base(spec, ink, grid, xTitle) {
    return {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      plugins: {
        legend: { display: spec.series ? spec.series.length > 1 : false,
          position: "bottom", labels: { color: ink, usePointStyle: true, boxHeight: 8 } },
        tooltip: {}
      },
      scales: {
        x: { title: { display: true, text: xTitle, color: ink },
          ticks: { color: ink }, grid: { color: grid }, border: { color: grid } },
        y: { title: { display: true, text: spec.unit, color: ink },
          ticks: { color: ink, callback: function (v) { return fmt(v); } },
          grid: { color: grid }, border: { display: false } }
      }
    };
  }

  function trend(spec, ink, grid) {
    var opts = base(spec, ink, grid, "Commit on main (short SHA)");
    var xs = [];
    spec.series.forEach(function (s) { s.points.forEach(function (p) { xs.push(p[0]); }); });
    var lo = Math.min.apply(null, xs), hi = Math.max.apply(null, xs);
    opts.scales.x.type = "linear";
    opts.scales.x.min = lo - 0.5;
    opts.scales.x.max = hi + 0.5;
    if (spec.integer) opts.scales.y.ticks.precision = 0;
    opts.scales.x.ticks.stepSize = 1;
    opts.scales.x.ticks.autoSkip = true;
    opts.scales.x.ticks.maxRotation = 0;
    opts.scales.x.ticks.callback = function (v) { return commit(v); };
    opts.scales.x.grid.display = false;
    opts.scales.x.afterBuildTicks = function (scale) {  // one tick per commit; autoSkip thins labels
      var t = [];
      for (var i = Math.ceil(scale.min); i <= Math.floor(scale.max); i++) t.push({ value: i });
      scale.ticks = t;
    };
    opts.plugins.tooltip.callbacks = {
      title: function (items) {
        var a = axis[items[0].parsed.x];
        return a ? a[0] + " · " + a[1] : "";
      },
      label: function (item) { return item.dataset.label + ": " + fmt(item.parsed.y) + " " + spec.unit; }
    };
    return {
      type: "line",
      data: { datasets: spec.series.map(function (s) {
        var c = colour(s.color);
        return { label: s.label, data: s.points.map(function (p) { return { x: p[0], y: p[1] }; }),
          borderColor: c, backgroundColor: c, borderWidth: 2, showLine: s.line, spanGaps: true,
          pointStyle: s.shape, pointRadius: s.line ? 2.5 : 4.5, pointHoverRadius: 6,
          pointBorderWidth: s.shape === "crossRot" ? 2 : 1, tension: 0 };
      }) },
      options: opts
    };
  }

  function hist(spec, ink, grid) {
    var opts = base(spec, ink, grid, spec.unit);
    opts.scales.y.title.text = "Count";
    opts.scales.y.beginAtZero = true;
    opts.scales.y.ticks.precision = 0;
    opts.scales.x.grid.display = false;
    opts.plugins.tooltip.callbacks = {
      title: function (items) { return items[0].label + " " + spec.unit; },
      label: function (item) { return "Count: " + item.parsed.y; }
    };
    return {
      type: "bar",
      data: { labels: spec.labels, datasets: [{ label: "Count", data: spec.counts,
        backgroundColor: colour("1"), borderColor: css("--bg"), borderWidth: { left: 1, right: 1 },
        borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "bottom",
        categoryPercentage: 1, barPercentage: 1 }] },
      options: opts
    };
  }

  window.drawTrackerCharts = function () {
    drawn.forEach(function (c) { c.destroy(); });
    drawn = [];
    var ink = css("--muted"), grid = css("--chart-grid");
    Chart.defaults.font.family = css("--font");
    document.querySelectorAll("canvas[data-chart]").forEach(function (cv) {
      var spec = data.charts[cv.getAttribute("data-chart")];
      if (!spec) return;
      var cfg = spec.kind === "hist" ? hist(spec, ink, grid) : trend(spec, ink, grid);
      drawn.push(new Chart(cv, cfg));
    });
  };
  window.drawTrackerCharts();
})();
