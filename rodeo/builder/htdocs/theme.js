// Rodeo Builder theme: dark (default) or light, remembered per browser when
// storage is available. Runs in <head> so the page never flashes the other theme.
"use strict";
(function () {
  const KEY = "rodeo-builder-theme";
  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
  }
  apply(stored() === "light" ? "light" : "dark");
  window.RBTheme = {
    current: () => document.documentElement.getAttribute("data-theme"),
    toggle() {
      const next = this.current() === "dark" ? "light" : "dark";
      apply(next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* storage unavailable */ }
      return next;
    },
  };
})();
