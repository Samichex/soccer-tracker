(function () {
    var dataEl = document.getElementById("rank-history-data");
    var chartEl = document.getElementById("rank-history-chart");
    var tooltipEl = document.getElementById("rank-history-tooltip");
    if (!dataEl || !chartEl) return;

    var data = JSON.parse(dataEl.textContent);
    var weeks = data.weeks;
    var teams = data.teams;
    if (!weeks.length || !teams.length) return;

    var RANK_MIN = 1;
    var RANK_MAX = 25;
    var VIEW_W = 800;
    var VIEW_H = 340;
    var MARGIN = { top: 14, right: 14, bottom: 26, left: 30 };
    var plotW = VIEW_W - MARGIN.left - MARGIN.right;
    var plotH = VIEW_H - MARGIN.top - MARGIN.bottom;

    function xScale(i) {
        if (weeks.length <= 1) return MARGIN.left + plotW / 2;
        return MARGIN.left + (i / (weeks.length - 1)) * plotW;
    }
    function yScale(rank) {
        return MARGIN.top + ((rank - RANK_MIN) / (RANK_MAX - RANK_MIN)) * plotH;
    }

    function isDark() {
        var stored = null;
        try { stored = localStorage.getItem("ftTheme"); } catch (e) {}
        if (stored === "dark") return true;
        if (stored === "light") return false;
        return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
    function colorFor(index, total) {
        var hue = Math.round((360 / total) * index);
        return isDark() ? "hsl(" + hue + " 70% 62%)" : "hsl(" + hue + " 65% 42%)";
    }

    var svgNS = "http://www.w3.org/2000/svg";
    function el(tag, attrs) {
        var e = document.createElementNS(svgNS, tag);
        for (var k in attrs) e.setAttribute(k, attrs[k]);
        return e;
    }

    var svg = el("svg", {
        viewBox: "0 0 " + VIEW_W + " " + VIEW_H,
        role: "img",
        "aria-label": "Rank history for every team ranked this season",
    });

    // Gridlines + rank labels.
    [1, 5, 10, 15, 20, 25].forEach(function (rank) {
        var y = yScale(rank);
        svg.appendChild(el("line", {
            class: "rank-chart-axis",
            x1: MARGIN.left, x2: VIEW_W - MARGIN.right, y1: y, y2: y,
        }));
        var label = el("text", {
            class: "rank-chart-axis-text",
            x: MARGIN.left - 6, y: y + 3, "text-anchor": "end",
        });
        label.textContent = "#" + rank;
        svg.appendChild(label);
    });

    // Week ticks along the x-axis, thinned out so labels don't overlap.
    var tickStep = Math.max(1, Math.ceil(weeks.length / 8));
    weeks.forEach(function (week, i) {
        if (i % tickStep !== 0 && i !== weeks.length - 1) return;
        var x = xScale(i);
        var label = el("text", {
            class: "rank-chart-axis-text",
            x: x, y: VIEW_H - MARGIN.bottom + 16, "text-anchor": "middle",
        });
        var d = new Date(week + "T00:00:00");
        label.textContent = isNaN(d.getTime())
            ? week
            : d.toLocaleDateString(undefined, { month: "numeric", day: "numeric" });
        svg.appendChild(label);
    });

    // Team lines. Each team's `points` is [[weekIndex, rank], ...] ascending
    // by weekIndex, possibly with gaps -- split into contiguous runs so a
    // gap (a week a team wasn't ranked) is a break, not an interpolated line.
    var linesBySeo = {};
    var indexBySeo = {};
    var rankByWeekBySeo = {};

    teams.forEach(function (team, teamIndex) {
        indexBySeo[team.seo] = teamIndex;
        var byWeek = {};
        team.points.forEach(function (p) { byWeek[p[0]] = p[1]; });
        rankByWeekBySeo[team.seo] = byWeek;

        var runs = [];
        var current = [];
        team.points.forEach(function (p, i) {
            if (current.length && p[0] !== current[current.length - 1][0] + 1) {
                runs.push(current);
                current = [];
            }
            current.push(p);
        });
        if (current.length) runs.push(current);

        var color = colorFor(teamIndex, teams.length);
        var elements = [];
        runs.forEach(function (run) {
            if (run.length === 1) {
                var c = el("circle", {
                    class: "rank-line-point",
                    cx: xScale(run[0][0]), cy: yScale(run[0][1]), r: 3,
                    fill: color, "data-seo": team.seo,
                });
                var cHit = el("circle", {
                    class: "rank-line-hit",
                    cx: xScale(run[0][0]), cy: yScale(run[0][1]), r: 8,
                    fill: "transparent", "data-seo": team.seo,
                });
                svg.appendChild(c);
                svg.appendChild(cHit);
                elements.push({ visible: c, hit: cHit });
                return;
            }
            var d = run.map(function (p, i) {
                return (i === 0 ? "M" : "L") + xScale(p[0]) + "," + yScale(p[1]);
            }).join(" ");
            var path = el("path", { class: "rank-line", d: d, stroke: color, "data-seo": team.seo });
            var hit = el("path", {
                class: "rank-line-hit", d: d, "data-seo": team.seo,
                fill: "none", stroke: "transparent", "stroke-width": 10, "pointer-events": "stroke",
            });
            svg.appendChild(path);
            svg.appendChild(hit);
            elements.push({ visible: path, hit: hit });
        });
        linesBySeo[team.seo] = elements;
    });

    chartEl.appendChild(svg);

    function recolor() {
        teams.forEach(function (team, teamIndex) {
            var color = colorFor(teamIndex, teams.length);
            (linesBySeo[team.seo] || []).forEach(function (pair) {
                pair.visible.setAttribute(pair.visible.tagName === "circle" ? "fill" : "stroke", color);
            });
            var swatch = document.querySelector('.rank-history-swatch[data-seo="' + team.seo + '"]');
            if (swatch) swatch.style.background = color;
        });
    }
    recolor();

    // Highlighting, shared by chart hover/tap and table hover/tap.
    var activeSeo = null;
    var rows = {};
    document.querySelectorAll("#rank-history-table tbody tr[data-seo]").forEach(function (tr) {
        rows[tr.dataset.seo] = tr;
    });

    function setActiveTeam(seo) {
        activeSeo = seo;
        teams.forEach(function (team) {
            var active = seo === team.seo;
            (linesBySeo[team.seo] || []).forEach(function (pair) {
                pair.visible.classList.toggle("is-active", !!seo && active);
                pair.visible.classList.toggle("is-dimmed", !!seo && !active);
            });
            var row = rows[team.seo];
            if (row) row.classList.toggle("is-active", !!seo && active);
        });
    }

    function hideTooltip() {
        tooltipEl.hidden = true;
    }
    function showTooltipFor(seo, clientX, clientY) {
        var byWeek = rankByWeekBySeo[seo];
        var containerRect = chartEl.getBoundingClientRect();
        var relX = clientX - containerRect.left;
        var frac = Math.min(1, Math.max(0, (relX - MARGIN.left) / plotW));
        var weekIndex = Math.round(frac * (weeks.length - 1));
        var nearest = null;
        var nearestDist = Infinity;
        Object.keys(byWeek).forEach(function (wi) {
            var dist = Math.abs(Number(wi) - weekIndex);
            if (dist < nearestDist) { nearestDist = dist; nearest = Number(wi); }
        });
        var team = teams[indexBySeo[seo]];
        var rankText = nearest !== null ? "#" + byWeek[nearest] : "";
        tooltipEl.textContent = team.name + (rankText ? " · " + rankText : "");
        tooltipEl.hidden = false;
        tooltipEl.style.left = (clientX - containerRect.left + 12) + "px";
        tooltipEl.style.top = (clientY - containerRect.top + 12) + "px";
    }

    chartEl.style.position = "relative";

    function bindHitElement(pair, seo) {
        var hit = pair.hit;
        hit.addEventListener("pointerenter", function (e) {
            if (e.pointerType !== "mouse") return;
            setActiveTeam(seo);
            showTooltipFor(seo, e.clientX, e.clientY);
        });
        hit.addEventListener("pointermove", function (e) {
            if (e.pointerType !== "mouse") return;
            showTooltipFor(seo, e.clientX, e.clientY);
        });
        hit.addEventListener("pointerleave", function (e) {
            if (e.pointerType !== "mouse") return;
            setActiveTeam(null);
            hideTooltip();
        });
        hit.addEventListener("click", function (e) {
            e.stopPropagation();
            if (activeSeo === seo && !tooltipEl.hidden) {
                setActiveTeam(null);
                hideTooltip();
            } else {
                setActiveTeam(seo);
                showTooltipFor(seo, e.clientX, e.clientY);
            }
        });
    }
    Object.keys(linesBySeo).forEach(function (seo) {
        linesBySeo[seo].forEach(function (pair) { bindHitElement(pair, seo); });
    });

    Object.keys(rows).forEach(function (seo) {
        var tr = rows[seo];
        tr.addEventListener("pointerenter", function (e) {
            if (e.pointerType !== "mouse") return;
            setActiveTeam(seo);
        });
        tr.addEventListener("pointerleave", function (e) {
            if (e.pointerType !== "mouse") return;
            setActiveTeam(null);
        });
        tr.addEventListener("click", function (e) {
            if (e.target.closest("a")) return;
            e.stopPropagation();
            setActiveTeam(activeSeo === seo ? null : seo);
        });
    });

    document.addEventListener("click", function () {
        setActiveTeam(null);
        hideTooltip();
    });

    var themeSwitch = document.getElementById("ft-theme-switch");
    if (themeSwitch) themeSwitch.addEventListener("click", recolor);
})();
