// Narrow screens list the bracket one round at a time (see the bracket rules in
// style.css). The round tabs are plain ?round=N links, so they work without
// this script; with it, a tab swaps the visible round in place instead of
// reloading, and keeps the address bar's ?round= in step so a refresh or a
// shared link lands on the same round.
(function () {
    var tabs = document.querySelectorAll(".bracket-tab");
    var rounds = document.querySelectorAll(".bracket-round");
    if (!tabs.length) return;

    tabs.forEach(function (tab) {
        tab.addEventListener("click", function (event) {
            event.preventDefault();
            var n = tab.dataset.round;
            tabs.forEach(function (t) {
                var on = t === tab;
                t.classList.toggle("active", on);
                if (on) t.setAttribute("aria-current", "true");
                else t.removeAttribute("aria-current");
            });
            rounds.forEach(function (r) {
                r.classList.toggle("is-selected", r.dataset.round === n);
            });
            try {
                window.history.replaceState(null, "", tab.getAttribute("href"));
            } catch (e) {}
            // The tab bar sticks to the top while scrolling; a new round starts
            // at its first game, not wherever the old round was scrolled to.
            var bar = tab.parentElement.getBoundingClientRect();
            if (bar.top <= 1) tab.parentElement.scrollIntoView({ block: "start" });
        });
    });
})();
