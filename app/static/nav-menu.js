(function () {
    var menus = Array.prototype.slice.call(document.querySelectorAll(".menu"));
    var themeSwitch = document.getElementById("ft-theme-switch");

    function closeMenu(menu) {
        var toggle = menu.querySelector(".menu-toggle");
        menu.classList.remove("open");
        if (toggle) toggle.setAttribute("aria-expanded", "false");
    }
    function openMenu(menu) {
        menus.forEach(function (m) {
            if (m !== menu) closeMenu(m);
        });
        var toggle = menu.querySelector(".menu-toggle");
        menu.classList.add("open");
        if (toggle) toggle.setAttribute("aria-expanded", "true");
    }

    menus.forEach(function (menu) {
        var toggle = menu.querySelector(".menu-toggle");
        if (!toggle) return;
        toggle.addEventListener("click", function (e) {
            e.stopPropagation();
            if (menu.classList.contains("open")) {
                closeMenu(menu);
            } else {
                openMenu(menu);
            }
        });
    });
    document.addEventListener("click", function (e) {
        menus.forEach(function (menu) {
            if (!menu.contains(e.target)) closeMenu(menu);
        });
    });
    document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape") return;
        menus.forEach(function (menu) {
            if (!menu.classList.contains("open")) return;
            closeMenu(menu);
            var toggle = menu.querySelector(".menu-toggle");
            if (toggle) toggle.focus();
        });
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
