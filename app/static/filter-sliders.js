(function () {
    var formatters = {
        money: function (v) { return "$" + Math.round(v).toLocaleString("en-US"); },
        percent: function (v) { return Math.round(v * 100) + "%"; },
        count: function (v) { return Math.round(v).toLocaleString("en-US"); },
    };

    document.querySelectorAll("input[type=range][data-format]").forEach(function (input) {
        var out = document.getElementById(input.dataset.output);
        var fmt = formatters[input.dataset.format];
        if (!out || !fmt) return;
        var update = function () { out.textContent = fmt(Number(input.value)); };
        input.addEventListener("input", update);
        update();
    });
})();
