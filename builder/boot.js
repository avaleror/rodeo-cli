// Startup watch for the builder page, loaded before app.js so it works when app.js can't run.
// While #loading shows: a script error or a script that fails to load turns it into
// "The page couldn't start: <reason>." with a Reload button, and after window.RB_BOOT_WAIT ms
// (default 30 s) it names the startup calls still waiting for the server.
// app.js reports its startup calls with RBBoot.start(name) / RBBoot.done(name), and a failed
// startup with RBBoot.fail(reason).
(function () {
  const waiting = new Set();
  let failed = false;

  function show(text, bad) {
    const box = document.getElementById("loading");
    if (!box || box.hidden) return;
    const reload = document.createElement("button");
    reload.type = "button";
    reload.className = "btn sm";
    reload.textContent = "Reload";
    reload.addEventListener("click", () => location.reload());
    box.classList.toggle("bad", !!bad);
    box.replaceChildren(document.createTextNode(text + " "), reload);
  }

  function fail(reason) {
    failed = true;
    show("The page couldn't start: " + reason + ".", true);
  }

  window.addEventListener("error", (ev) => {
    const node = ev.target !== window ? ev.target : null;
    if (node && !["SCRIPT", "LINK"].includes(node.tagName)) return;
    fail(node ? (node.src || node.href).split("/").pop() + " did not load" : ev.message || "a script error");
  }, true);
  window.addEventListener("unhandledrejection", (ev) => fail(String((ev.reason && ev.reason.message) || ev.reason)));

  setTimeout(function tick() {
    const box = document.getElementById("loading");
    if (failed || !box || box.hidden) return;
    show(waiting.size ? "Still waiting for the server: " + [...waiting].join(", ") + "."
      : "The page is taking long to start.");
    setTimeout(tick, 2000);
  }, window.RB_BOOT_WAIT || 30000);

  window.RBBoot = {
    start(name) { waiting.add(name); },
    done(name) { waiting.delete(name); },
    fail,
  };
})();
