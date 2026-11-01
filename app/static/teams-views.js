// Phone Teams page: switch which columns show and re-sort to that view's default.
// Needs sort-table.js loaded first -- it owns the header click-to-sort.
document.querySelectorAll(".teams-mobile").forEach((table) => {
    const tabs = document.querySelectorAll(".teams-view-tab");
    const tbody = table.tBodies[0];
    // Conf. ranks by conference points; every other view by overall points.
    const defaultSort = { conf: "conf" };

    tabs.forEach((tab) => {
        tab.addEventListener("click", () => {
            const view = tab.dataset.view;
            tabs.forEach((t) => {
                t.classList.toggle("active", t === tab);
                t.setAttribute("aria-selected", String(t === tab));
            });
            table.dataset.view = view;

            // Back to server order first so ties in the new sort stay put.
            Array.from(tbody.rows)
                .sort((a, b) => a.dataset.i - b.dataset.i)
                .forEach((r) => tbody.appendChild(r));
            table.querySelectorAll("th[data-sort]").forEach((h) => h.classList.remove("sort-asc", "sort-desc"));
            table.querySelector(`th[data-sort="${defaultSort[view] || "overall"}"]`).click();
        });
    });
});
