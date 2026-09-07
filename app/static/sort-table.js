document.querySelectorAll("table.sortable").forEach((table) => {
    const tbody = table.tBodies[0];

    table.querySelectorAll("th[data-sort]").forEach((th) => {
        th.addEventListener("click", () => {
            const key = th.dataset.sort;
            const nextDir = th.classList.contains("sort-asc")
                ? "desc"
                : th.classList.contains("sort-desc")
                ? "asc"
                : th.dataset.sortDefault || "asc";

            table
                .querySelectorAll("th[data-sort]")
                .forEach((h) => h.classList.remove("sort-asc", "sort-desc"));
            th.classList.add(nextDir === "asc" ? "sort-asc" : "sort-desc");

            const rows = Array.from(tbody.querySelectorAll("tr"));
            rows.sort((a, b) => {
                const av = a.querySelector(`[data-sort-key="${key}"]`).dataset.sortValue;
                const bv = b.querySelector(`[data-sort-key="${key}"]`).dataset.sortValue;
                const an = parseFloat(av);
                const bn = parseFloat(bv);
                const cmp =
                    !isNaN(an) && !isNaN(bn) ? an - bn : av.localeCompare(bv);
                return nextDir === "asc" ? cmp : -cmp;
            });
            rows.forEach((r) => tbody.appendChild(r));
        });
    });
});
