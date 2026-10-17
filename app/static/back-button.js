(function () {
    // Legacy iOS flag; the (display-mode: standalone) media query in style.css
    // covers modern iOS, this covers the rest.
    if (window.navigator.standalone === true) {
        document.documentElement.classList.add("standalone");
    }
    var back = document.querySelector(".nav-back");
    if (!back) return;
    back.addEventListener("click", function (e) {
        // Real in-app history -> go back to wherever the user came from (the
        // team list, a game, a search...). Launched straight onto this page ->
        // history is empty, so let the link fall through to its parent page.
        if (window.history.length > 1) {
            e.preventDefault();
            window.history.back();
        }
    });
})();
