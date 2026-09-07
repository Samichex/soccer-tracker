document.querySelectorAll(".leaders-toggle").forEach((btn) => {
    const collapseLabel = btn.dataset.collapseLabel || "Show top 3";
    btn.addEventListener("click", () => {
        const expanded = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", String(!expanded));
        btn.closest(".boxscore-card, .toggle-section").classList.toggle("expanded", !expanded);
        btn.querySelector(".leaders-toggle-label").textContent = expanded ? "Show all" : collapseLabel;
    });
});
