document.querySelectorAll(".team-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".team-tab").forEach((t) => {
            t.classList.toggle("active", t === tab);
            t.setAttribute("aria-selected", String(t === tab));
        });
        document.querySelectorAll(".team-tab-panel").forEach((panel) => {
            panel.hidden = panel.dataset.panel !== tab.dataset.tab;
        });
    });
});
