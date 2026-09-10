(function () {
    var menu = document.getElementById("ft-menu");
    var toggle = document.getElementById("ft-menu-toggle");
    var themeSwitch = document.getElementById("ft-theme-switch");
    if (!menu || !toggle) return;

    function closeMenu() {
        menu.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
    }
    function openMenu() {
        menu.classList.add("open");
        toggle.setAttribute("aria-expanded", "true");
    }

    toggle.addEventListener("click", function (e) {
        e.stopPropagation();
        if (menu.classList.contains("open")) {
            closeMenu();
        } else {
            openMenu();
        }
    });
    document.addEventListener("click", function (e) {
        if (!menu.contains(e.target)) closeMenu();
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && menu.classList.contains("open")) {
            closeMenu();
            toggle.focus();
        }
    });

    function isDark() {
        var stored = null;
        try { stored = localStorage.getItem("ftTheme"); } catch (e) {}
        if (stored === "dark") return true;
        if (stored === "light") return false;
        return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    }
    function reflectTheme() {
        if (!themeSwitch) return;
        var dark = isDark();
        themeSwitch.setAttribute("aria-checked", String(dark));
        themeSwitch.classList.toggle("is-dark", dark);
    }
    reflectTheme();
    if (themeSwitch) {
        themeSwitch.addEventListener("click", function () {
            var next = isDark() ? "light" : "dark";
            try { localStorage.setItem("ftTheme", next); } catch (e) {}
            document.documentElement.setAttribute("data-theme", next);
            reflectTheme();
        });
    }
})();
