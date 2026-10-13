// Picks Cutzo's dark or light look before the page draws, so it never flashes the wrong one.
// The choice is "dark" (the default), "light" or "system" (match Windows) and is kept in
// this browser. Loaded as a plain script in <head>; screens use the helpers in js/ui.js.
// Every change fires a "cutzo-theme" event on window.
(function () {
  "use strict";
  var KEY = "cutzo-theme";
  var CHOICES = ["dark", "light", "system"];
  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var choice = read();

  function read() {
    var value = null;
    try {
      value = window.localStorage.getItem(KEY);
    } catch (e) {
      // Storage is blocked: use the default.
    }
    return CHOICES.indexOf(value) >= 0 ? value : "dark";
  }

  function apply(announce) {
    var dark = choice === "dark" || (choice === "system" && !!media && media.matches);
    root.setAttribute("data-theme", dark ? "dark" : "light");
    if (announce) window.dispatchEvent(new CustomEvent("cutzo-theme"));
  }

  window.cutzoTheme = {
    get: function () {
      return choice;
    },
    set: function (next) {
      if (CHOICES.indexOf(next) < 0) return;
      choice = next;
      try {
        window.localStorage.setItem(KEY, next);
      } catch (e) {
        // Not saved, but it still applies until the page closes.
      }
      apply(true);
    },
  };

  apply(false);

  // Follow Windows when the choice is "Match Windows", and other Cutzo tabs when they switch.
  if (media) {
    var follow = function () {
      if (choice === "system") apply(true);
    };
    if (media.addEventListener) media.addEventListener("change", follow);
    else if (media.addListener) media.addListener(follow);
  }
  window.addEventListener("storage", function (e) {
    if (e.key === KEY) {
      choice = read();
      apply(true);
    }
  });
})();
