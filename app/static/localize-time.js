// Kickoff times are rendered server-side in ET (NCAA's feed timezone). Each
// [data-epoch] element carries the true UTC start_epoch alongside that text;
// here we swap the display to the viewer's own timezone when the browser
// supports it, leaving the ET fallback in place otherwise (no JS, old browser).
(function () {
    if (!window.Intl || !Intl.DateTimeFormat) return;

    var fmt = new Intl.DateTimeFormat(undefined, {
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
    });

    document.querySelectorAll("[data-epoch]").forEach(function (el) {
        var epoch = parseInt(el.dataset.epoch, 10);
        if (!epoch) return;

        var time = "";
        var tz = "";
        fmt.formatToParts(new Date(epoch * 1000)).forEach(function (p) {
            if (p.type === "timeZoneName") tz = p.value;
            else time += p.value;
        });
        time = time.trim();

        var tzEl = el.querySelector(".tz");
        if (tzEl) {
            el.firstChild.textContent = time + " ";
            tzEl.textContent = tz;
        } else {
            el.textContent = tz ? time + " " + tz : time;
        }
    });
})();
