// The rodeo lab player: guide (folded or open), steps rail, lab tabs,
// chapter cover and chapter complete views. Talks to the player's own API:
// GET /api/state, GET /api/chapter/<n>, POST /api/enter|check|next|skip.
(() => {
  const $ = (id) => document.getElementById(id);
  const root = document.documentElement;
  let state = null, view = null, viewing = null, activeTab = 0, pendingSkip = null, polling = null;
  let stepNow = 0, lastShown = null, overlay = null;  // overlay: null | "lobby" | "cover" | "complete"
  const frames = new Map();  // url -> iframe, kept alive across chapters

  const ICON = {
    check: '<svg class="i" viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg>',
    skipped: '<svg class="i" viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    alert: '<svg class="i" viewBox="0 0 24 24"><path d="M12 7v6M12 17h.01"/></svg>',
    copy: '<svg class="i" viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>',
    lock: '<svg class="i" viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
    terminal: '<svg class="i" viewBox="0 0 24 24"><path d="m5 8 4 4-4 4M12 16h7"/></svg>',
    service: '<svg class="i" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/></svg>',
    chev: '<svg class="i chev" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
    guide: '<svg class="i" viewBox="0 0 24 24"><path d="M4 5h11a3 3 0 0 1 3 3v12H7a3 3 0 0 1-3-3zM18 8h2v12"/></svg>',
    auto: '<svg class="i" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/></svg>',
    light: '<svg class="i" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    dark: '<svg class="i" viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/></svg>',
  };
  // Browser storage holds per-viewer comforts only (theme, guide width, step
  // ticks, covers seen); the page works the same when it is blocked. Each lab
  // has its own host name, so these never mix between labs.
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} },
    json(k, dflt) { try { return JSON.parse(this.get(k)) ?? dflt; } catch (e) { return dflt; } },
  };
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls;
    if (html !== undefined) n.innerHTML = html; return n; };
  const text = (tag, cls, t) => { const n = el(tag, cls); n.textContent = t; return n; };

  // -- theme: auto (follow the system), light, dark ------------------------
  const THEMES = ["auto", "light", "dark"];
  const THEME_LABEL = {auto: "Theme follows your system", light: "Light theme", dark: "Dark theme"};
  function applyTheme(t) {
    if (t === "auto") delete root.dataset.theme; else root.dataset.theme = t;
    const b = $("theme");
    b.innerHTML = ICON[t]; b.title = `${THEME_LABEL[t]}. Click to change.`; b.setAttribute("aria-label", b.title);
  }
  let theme = store.get("rodeo-theme") || "auto";
  if (!THEMES.includes(theme)) theme = "auto";
  applyTheme(theme);
  $("theme").onclick = () => {
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    store.set("rodeo-theme", theme === "auto" ? null : theme);
    applyTheme(theme);
    fixAllContrast();
  };
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", fixAllContrast);

  // -- guide: open or folded, and its width ---------------------------------
  const main = document.querySelector("main");
  function setFolded(folded) {
    root.classList.toggle("guide-folded", folded);
    store.set("rodeo-guide", folded ? "folded" : null);
    $("guideBtn").classList.toggle("on", !folded);
    $("guideBtn").setAttribute("aria-expanded", String(!folded));
    const label = folded ? "Open the guide" : "Fold the guide";
    $("guideBtn").title = label; $("guideBtn").setAttribute("aria-label", label);
  }
  setFolded(root.classList.contains("guide-folded"));
  $("guideBtn").onclick = () => setFolded(!root.classList.contains("guide-folded"));
  $("foldBtn").onclick = () => setFolded(true);
  $("floatGuide").onclick = () => { setFolded(false); openStep(stepNow); };

  function setWidth(px) {
    const w = Math.max(340, Math.min(Math.round(px), Math.max(340, main.clientWidth - 420)));
    main.style.setProperty("--guide", `${w}px`);
    $("splitter").setAttribute("aria-valuenow", w);
    return w;
  }
  const savedW = parseFloat(store.get("rodeo-guide-w"));
  if (savedW) setWidth(savedW);
  $("splitter").addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const sp = $("splitter"), left = $("guide").getBoundingClientRect().left;
    sp.setPointerCapture(e.pointerId);
    document.body.classList.add("dragging");
    const move = (ev) => setWidth(ev.clientX - left);
    const up = (ev) => {
      document.body.classList.remove("dragging");
      sp.removeEventListener("pointermove", move); sp.removeEventListener("pointerup", up);
      store.set("rodeo-guide-w", String(setWidth(ev.clientX - left)));
    };
    sp.addEventListener("pointermove", move); sp.addEventListener("pointerup", up);
  });
  $("splitter").addEventListener("dblclick", () => { main.style.removeProperty("--guide"); store.set("rodeo-guide-w", null); });
  $("splitter").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const cur = $("guide").getBoundingClientRect().width;
    store.set("rodeo-guide-w", String(setWidth(cur + (e.key === "ArrowLeft" ? -24 : 24))));
  });

  // -- track content --------------------------------------------------------
  // Track text is trusted SUSE content, but a chapter must not be able to
  // press buttons on its own: drop scripts, embedded frames and inline
  // handlers, keep the markup and the rodeo-tab-button elements.
  function clean(html) {
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
    doc.querySelectorAll("script, iframe, object, embed, form, base, meta, link").forEach((n) => n.remove());
    doc.querySelectorAll("*").forEach((node) => [...node.attributes].forEach((a) => {
      if (/^on/i.test(a.name) || (/^(href|src|action|formaction)$/i.test(a.name) && /^\s*javascript:/i.test(a.value)))
        node.removeAttribute(a.name);
    }));
    return doc.body.firstChild.innerHTML;
  }
  // A variable inside a fenced code block comes back from marked escaped, so
  // turn the runner's two exact markups back into elements (escaped twice:
  // runner, then marked; unescape once).
  const unescapeOnce = (s) => s.replace(/&amp;/g, "&");
  function restoreVars(html) {
    return html
      .replace(/&lt;code class=&quot;rodeo-var&quot; title=&quot;Click to copy&quot;&gt;([\s\S]*?)&lt;\/code&gt;/g,
        (_, v) => `<code class="rodeo-var" title="Click to copy">${unescapeOnce(v)}</code>`)
      .replace(/&lt;span class=&quot;rodeo-var-missing&quot;&gt;([\s\S]*?)&lt;\/span&gt;/g,
        (_, v) => `<span class="rodeo-var-missing">${unescapeOnce(v)}</span>`);
  }
  // Track <style> blocks (often in the first section) style every section, so
  // they live once in the page, outside any card that can be hidden.
  const trackStyles = el("div"); trackStyles.hidden = true; document.body.append(trackStyles);
  function content(markdown, cls) {
    const box = el("div", `content${cls ? " " + cls : ""}`);
    box.innerHTML = clean(window.marked ? restoreVars(marked.parse(markdown || "")) : (markdown || ""));
    box.querySelectorAll("style").forEach((s) => trackStyles.append(s));
    decorate(box);
    return box;
  }

  function decorate(box) {
    box.querySelectorAll("pre").forEach((pre) => {
      if (pre.parentElement.classList.contains("my-3")) return;
      const wrap = el("div", "my-3"), bar = el("div", "copybar");
      const b = el("button", "copy", `${ICON.copy}<span class="label">Copy</span>`);
      b.type = "button"; b.title = "Copy to clipboard"; b.setAttribute("aria-label", "Copy to clipboard");
      // Paste writes into the lab terminal without pressing Enter: the student
      // reads the command at the prompt and runs it.
      const p = el("button", "paste", `${ICON.terminal}<span class="label">Paste in terminal</span>`);
      p.type = "button"; p.title = "Paste into the terminal tab (it does not press Enter)";
      bar.append(b, p);
      pre.replaceWith(wrap); wrap.append(bar, pre);
    });
    box.querySelectorAll("blockquote").forEach((q) => {
      const p = q.firstElementChild, t = p && p.firstChild;
      const m = t && t.nodeType === Node.TEXT_NODE && t.textContent.match(/^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/i);
      if (!m) return;
      const kind = m[1].toLowerCase();
      t.textContent = t.textContent.slice(m[0].length);
      if (p.firstChild && p.firstChild.nodeName === "BR") p.firstChild.remove();
      const title = text("span", "callout-title", kind[0].toUpperCase() + kind.slice(1));
      q.classList.add("callout", kind); q.prepend(title);
    });
    box.querySelectorAll("img").forEach((img) => { img.loading = "lazy"; img.decoding = "async"; });
  }

  // Tracks bring their own colours (brand accents, white-on-dark highlights).
  // After render and on every theme change, pull text under 4.5:1 contrast
  // toward the theme ink until it reads. Generic: no per-track CSS.
  const rgb = (c) => (c.match(/[\d.]+/g) || [0, 0, 0, 1]).map(Number);
  const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; })
    .reduce((a, v, k) => a + v * [.2126, .7152, .0722][k], 0);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
  function bgBehind(node) {
    const layers = [];
    for (let n = node; n; n = n.parentElement) {
      const c = rgb(getComputedStyle(n).backgroundColor), a = c.length > 3 ? c[3] : 1;
      if (a > 0) layers.push([...c.slice(0, 3), a]);
      if (a >= .99) break;
    }
    return layers.reduceRight((base, [r, g, b, a]) => [r, g, b].map((v, k) => v * a + base[k] * (1 - a)),
      rgb(getComputedStyle(document.body).backgroundColor));
  }
  function fixContrast(box) {
    if (!box || box.closest("[hidden]")) return;
    box.querySelectorAll("[data-fixed-color]").forEach((n) => {
      n.style.removeProperty("color"); if (n.dataset.fixedColor) n.style.setProperty("color", n.dataset.fixedColor);
      delete n.dataset.fixedColor;
    });
    const ink = rgb(getComputedStyle(box).color);
    box.querySelectorAll("*").forEach((n) => {
      if (n.closest("pre, svg") || ![...n.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim())) return;
      const cs = getComputedStyle(n), bg = bgBehind(n);
      if (ratio(rgb(cs.color), bg) >= 4.5) return;
      n.dataset.fixedColor = n.style.getPropertyValue("color");
      const fg = rgb(cs.color);
      for (const keep of [.6, .4, .2, 0]) {
        const mix = [0, 1, 2].map((k) => Math.round(fg[k] * keep + ink[k] * (1 - keep)));
        n.style.setProperty("color", `rgb(${mix.join(",")})`, "important");
        if (ratio(mix, bg) >= 4.5) break;
      }
    });
  }
  function fixAllContrast() {
    document.querySelectorAll("#guideBody .content, #coverStory, #completeWhy").forEach(fixContrast);
  }

  // The terminal tab is ttyd on this same origin, so its xterm can take the
  // text as a paste (bracketed when the shell asks for it). No Enter is sent.
  // Anything else (another origin, terminal not loaded): copy instead.
  function terminalTab() { return (state.tabsNow || []).find((t) => t.type === "terminal" && t.url); }
  function pasteToTerminal(t, node) {
    const tab = terminalTab();
    if (!tab) { copyText(t, node); return; }
    showTab(tab.index);
    const f = frames.get(tab.url);
    const done = (ok) => {
      const label = node.querySelector(".label");
      node.classList.add("copied");
      if (label) label.textContent = ok ? "Pasted, press Enter" : "Copied, paste it";
      setTimeout(() => { node.classList.remove("copied"); if (label) label.textContent = "Paste in terminal"; }, 2200);
    };
    const attempt = (tries) => {
      let term = null;
      try { term = f && f.contentWindow && f.contentWindow.term; } catch (e) { term = null; }  // other origin
      if (term && typeof term.paste === "function") {
        term.paste(t.replace(/\n+$/, ""));
        try { term.focus(); f.focus(); } catch (e) { /* focus is a comfort */ }
        done(true);
      } else if (tries > 0) {
        setTimeout(() => attempt(tries - 1), 300);  // the terminal may still be loading
      } else {
        copyText(t, el("span")); done(false);
      }
    };
    attempt(20);
  }

  function copyText(t, node) {
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(t).then(() => {
      node.classList.add("copied");
      const label = node.querySelector(".label");
      if (label) label.textContent = "Copied";
      setTimeout(() => { node.classList.remove("copied"); if (label) label.textContent = "Copy"; }, 1400);
    }).catch(() => {});
  }

  async function api(path, body) {
    const opts = body === undefined ? {} : {method: "POST", headers: {"X-Rodeo-Player": "1",
      "Content-Type": "application/json"}, body: JSON.stringify(body)};
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  // -- lab tabs -------------------------------------------------------------
  function setTabs(tabs) {
    const bar = $("tabs");
    bar.replaceChildren();
    tabs.forEach((t) => {
      const b = el("button", "tab", ICON[t.type === "terminal" ? "terminal" : "service"]);
      b.type = "button"; b.setAttribute("role", "tab"); b.dataset.index = t.index;
      b.append(document.createTextNode(t.title));
      b.addEventListener("click", () => showTab(t.index));
      bar.append(b);
    });
    state.tabsNow = tabs;
    showTab(Math.max(0, Math.min(activeTab, tabs.length - 1)));
  }

  function showTab(i) {
    activeTab = i;
    const tabs = state.tabsNow || [];
    document.querySelectorAll("#tabs .tab").forEach((b) => {
      const on = Number(b.dataset.index) === i;
      b.classList.toggle("active", on); b.setAttribute("aria-selected", on);
    });
    const tab = tabs[i], want = tab && tab.url;
    $("popTab").hidden = $("reloadTab").hidden = !want;
    if (want) $("popTab").href = want;
    // Load a tab the first time it is shown: product UIs are heavy.
    if (want && !frames.has(want)) {
      const f = document.createElement("iframe");
      f.src = want; f.title = tab.title;
      f.setAttribute("allow", "clipboard-read; clipboard-write");
      const cover = el("div", "loading");
      cover.append(el("span", "spin"), text("span", "", `Opening ${tab.title}…`));
      const hint = setTimeout(() => {
        const a = el("a"); a.href = want; a.target = "_blank"; a.rel = "noopener"; a.textContent = "open it in a new tab";
        const p = el("p"); p.style.margin = "0";
        p.append("Still blank? Reload it, or ", a, ".");
        cover.append(p);
      }, 10000);
      f.addEventListener("load", () => { clearTimeout(hint); cover.remove(); });
      f.cover = cover;
      $("frames").append(f, cover);
      frames.set(want, f);
    }
    frames.forEach((f, url) => { f.hidden = url !== want; if (f.cover) f.cover.hidden = url !== want; });
    let empty = $("frames").querySelector(".empty");
    if (!want) {
      if (!empty) { empty = el("p", "empty"); $("frames").append(empty); }
      empty.textContent = "This tab is not set up on this lab.";
    } else if (empty) { empty.remove(); }
  }
  $("reloadTab").onclick = () => {
    const tab = (state.tabsNow || [])[activeTab], f = tab && frames.get(tab.url);
    if (f) f.src = tab.url;
  };

  // -- chapters: trail and menu in the header -------------------------------
  const nameOf = (c) => c.name || c.title;
  function chapterState(c) {
    if (c.index === state.current) return "current";
    if (state.completed.includes(c.index)) return "done";
    return c.index > state.readable ? "ahead" : "done";
  }
  const STATE_HINT = {done: "passed", current: "you are here", ahead: "skip to here"};

  function renderChapters() {
    const segs = $("segments"), menu = $("chapterMenu");
    segs.replaceChildren(); menu.replaceChildren();
    state.chapters.forEach((c) => {
      const s = chapterState(c), label = `Chapter ${c.index + 1}: ${nameOf(c)}, ${STATE_HINT[s]}`;
      const seg = el("button", s); seg.type = "button"; seg.title = label; seg.setAttribute("aria-label", label);
      if (c.index === viewing) seg.setAttribute("aria-current", "page");
      if (c.index === viewing && viewing !== state.current) seg.classList.add("viewing");
      seg.addEventListener("click", () => pickChapter(c.index));
      segs.append(seg);

      const li = el("li"), b = el("button", s);
      b.type = "button";
      if (c.index === viewing) b.classList.add("viewing");
      b.innerHTML = `<span class="n">${s === "done" ? ICON.check : c.index + 1}</span><span></span>` +
        `<span class="hint">${s === "ahead" ? ICON.lock : STATE_HINT[s]}</span>`;
      b.children[1].textContent = nameOf(c);
      b.title = label;
      b.addEventListener("click", () => { closeMenu(); pickChapter(c.index); });
      li.append(b); menu.append(li);
    });
    const shown = viewing === null ? state.current : viewing;
    $("where").textContent = `Chapter ${shown + 1} of ${state.chapters.length}`;
    const c = state.chapters[shown];
    $("chapterTitle").textContent = c ? `${shown + 1} · ${nameOf(c)}` : "";
  }

  function openMenu() {
    $("chapterMenu").hidden = false; $("chapterMenuBtn").setAttribute("aria-expanded", "true");
    const first = $("chapterMenu").querySelector("button.viewing") || $("chapterMenu").querySelector("button");
    if (first) first.focus();
  }
  function closeMenu() { $("chapterMenu").hidden = true; $("chapterMenuBtn").setAttribute("aria-expanded", "false"); }
  $("chapterMenuBtn").onclick = (e) => { e.stopPropagation(); $("chapterMenu").hidden ? openMenu() : closeMenu(); };
  document.addEventListener("click", (e) => { if (!e.target.closest("#chapterMenu")) closeMenu(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("chapterMenu").hidden) { closeMenu(); $("chapterMenuBtn").focus(); }
  });

  function pickChapter(i) {
    // The menu sits above the lobby, cover and complete screens; what it opens
    // (a chapter, the skip question) is in the guide, under them.
    if (overlay) showOverlay(null);
    if (i <= state.readable) { pendingSkip = null; $("confirm").hidden = true; loadChapter(i); return; }
    askSkip(i);
  }

  function askSkip(i) {
    if (state.busy) return;
    pendingSkip = i;
    setFolded(false);
    $("confirmTitle").textContent = i === state.current + 1 ? `Skip chapter ${state.current + 1}?`
                                                             : `Skip to chapter ${i + 1}?`;
    $("confirmText").textContent = i === state.current + 1
      ? `Its solution script runs first, so your lab ends up as if you had done it. Then chapter ${i + 1} ` +
        "opens. This can take a few minutes."
      : "Every chapter before it is set up and solved first, in order, so your lab ends up as if you had " +
        "done them. This can take several minutes.";
    $("confirm").hidden = false;
    $("confirmYes").focus();
  }

  // -- steps: what the student works through ---------------------------------
  // Done marks are per browser (the chapter check is the only real check).
  // A passed chapter shows every step done.
  const doneKey = (i) => `rodeo-steps-${i}`;
  const doneSet = (i) => new Set(store.json(doneKey(i), []));
  function isDone(si) {
    return state.completed.includes(viewing) || doneSet(viewing).has(si);
  }
  function setDone(si, on) {
    const s = doneSet(viewing); on ? s.add(si) : s.delete(si);
    store.set(doneKey(viewing), JSON.stringify([...s]));
  }
  function firstOpenStep() {
    const steps = view.steps;
    const i = steps.findIndex((s, k) => s.kind === "task" && !isDone(k));
    return i >= 0 ? i : Math.max(0, steps.findIndex((s, k) => !isDone(k)));
  }
  const stepLabel = (s, k) => s.kind === "optional" ? "Optional" : `Step ${s.number || k + 1}`;
  const stepName = (s) => s.name || (view && nameOf(view)) || "This chapter";

  function renderGuide() {
    const body = $("guideBody");
    body.replaceChildren();
    trackStyles.replaceChildren();
    const sec = view.sections;
    // The cover's own text is shown on the cover; its styles still apply here.
    sec.filter((s) => s.kind === "cover").forEach((s) => content(s.markdown));
    sec.forEach((s, i) => {
      if (s.kind !== "credentials") return;
      // Open on the first chapter; later chapters keep it one click away.
      const d = el("details", "gcard"); d.open = view.index === 0;
      d.innerHTML = `<summary><span class="gtitle"></span>${ICON.chev}</summary>`;
      d.querySelector(".gtitle").textContent = s.title;
      const b = el("div", "gbody"); b.append(content(s.markdown)); d.append(b);
      body.append(d);
    });
    view.steps.forEach((st, k) => {
      const card = el("section", `step ${st.kind}`); card.dataset.step = k;
      const head = el("button", "stephead"); head.type = "button";
      head.innerHTML = `<span class="badge"></span><span><span class="tag"></span><br><span class="sname"></span></span>${ICON.chev}`;
      head.querySelector(".tag").textContent = stepLabel(st, k);
      head.querySelector(".sname").textContent = stepName(st);
      head.addEventListener("click", () => openStep(k === stepNow && card.classList.contains("open") ? -1 : k));
      const sb = el("div", "sbody");
      st.sections.forEach((si) => {
        const s = sec[si];
        if (s.kind === "section" && s.title) sb.append(text("h3", "", s.title));
        sb.append(content(s.markdown));
      });
      const foot = el("div", "stepfoot");
      const mark = el("button", "btn outline small"); mark.type = "button"; mark.dataset.mark = k;
      const next = el("button", "btn quiet small"); next.type = "button"; next.dataset.next = k;
      next.textContent = "Next step";
      foot.append(mark, next);
      const res = el("div", "sresult"); res.setAttribute("aria-live", "polite");
      sb.prepend(res);  // a failed check shows first, where the student is looking
      sb.append(foot);
      card.append(head, sb);
      body.append(card);
    });
    const wrap = sec.filter((s) => s.kind === "why" || s.kind === "more");
    if (wrap.length) {
      const d = el("details", "gcard");
      d.innerHTML = `<summary><span class="gtitle">About this chapter</span>${ICON.chev}</summary>`;
      const b = el("div", "gbody");
      wrap.forEach((s) => { b.append(text("h3", "", s.title)); b.append(content(s.markdown)); });
      d.append(b); body.append(d);
    }
    const nTask = view.steps.filter((s) => s.kind === "task").length;
    const nOpt = view.steps.length - nTask;
    $("guideMeta").textContent = `Chapter ${view.index + 1} · ${nTask} ${nTask === 1 ? "task" : "tasks"}` +
      (nOpt ? ` + ${nOpt} optional` : "");
    $("storyBtn").hidden = !sec.some((s) => s.kind === "cover") && !view.teaser;
  }

  function openStep(k, scroll) {
    if (k >= 0) { stepNow = k; store.set(`rodeo-step-${viewing}`, String(k)); }
    document.querySelectorAll("#guideBody .step").forEach((c) => {
      c.classList.toggle("open", Number(c.dataset.step) === k);
    });
    updateSteps();
    const card = document.querySelector(`#guideBody .step[data-step="${k}"]`);
    if (card) {
      card.querySelectorAll(".content").forEach(fixContrast);
      if (scroll !== false) card.scrollIntoView({block: "start", behavior: "smooth"});
    }
  }

  function updateSteps() {
    if (!view) return;
    const failed = checkFailed();
    const rail = $("railSteps");
    rail.replaceChildren();
    view.steps.forEach((st, k) => {
      const done = isDone(k), cur = k === stepNow;
      const fk = failed && k === failStep();
      const b = el("button", `railbtn ${st.kind}${done ? " done" : ""}${fk && !done ? " failed" : cur && !done ? " current" : ""}`);
      b.type = "button";
      b.innerHTML = done ? ICON.check : String(st.kind === "optional" ? "+" : (st.number || k + 1));
      const label = `${stepLabel(st, k)}: ${stepName(st)}${done ? ", done" : cur ? ", current" : ""}`;
      b.title = label; b.setAttribute("aria-label", label);
      b.addEventListener("click", () => { setFolded(false); openStep(k); });
      rail.append(b);
    });
    document.querySelectorAll("#guideBody .step").forEach((c) => {
      const k = Number(c.dataset.step), done = isDone(k);
      c.classList.toggle("done", done);
      c.classList.toggle("failed", failed && k === failStep());
      c.querySelector(".badge").innerHTML = done ? ICON.check : String(view.steps[k].kind === "optional" ? "+" : (view.steps[k].number || k + 1));
      const mark = c.querySelector("[data-mark]");
      const passed = state.completed.includes(viewing);
      mark.hidden = passed;
      mark.textContent = done ? "Done · undo" : "Mark as done";
      c.querySelector("[data-next]").hidden = k >= view.steps.length - 1;
    });
    renderFloat();
  }

  $("guideBody").addEventListener("click", (e) => {
    const m = e.target.closest("[data-mark]");
    if (m) {
      const k = Number(m.dataset.mark), was = isDone(k);
      setDone(k, !was);
      if (!was && k === stepNow) { const n = firstOpenStep(); openStep(n === k ? k : n); } else updateSteps();
      return;
    }
    const n = e.target.closest("[data-next]");
    if (n) { openStep(Number(n.dataset.next) + 1); return; }
    const b = e.target.closest(".rodeo-tab-button");
    if (b) { showTab(Number(b.dataset.tab)); return; }
    const c = e.target.closest(".copy");
    if (c) { const pre = c.closest(".my-3").querySelector("pre"); copyText(pre.innerText.replace(/\n$/, ""), c); return; }
    const p = e.target.closest(".paste");
    if (p) { const pre = p.closest(".my-3").querySelector("pre"); pasteToTerminal(pre.innerText.replace(/\n$/, ""), p); return; }
    const v = e.target.closest(".rodeo-var");
    if (v) copyText(v.textContent, v);
  });

  // -- the floating card (guide folded) --------------------------------------
  function excerpt(k) {
    const card = document.querySelector(`#guideBody .step[data-step="${k}"] .sbody`);
    const p = card && [...card.querySelectorAll("p")].find((x) => x.textContent.trim().length > 20);
    const t = p ? p.textContent.trim().replace(/\s+/g, " ") : "";
    return t.length > 180 ? t.slice(0, 177).replace(/\s+\S*$/, "") + "…" : t;
  }
  function renderFloat() {
    if (!view || !state) return;
    const st = view.steps[stepNow] || view.steps[0];
    $("floatTag").textContent = `${stepLabel(st, stepNow)} of ${view.steps.length}`;
    $("floatName").textContent = stepName(st);
    $("floatExcerpt").textContent = excerpt(stepNow);
    $("floatOpen").innerHTML = `${ICON.guide}<span></span>`;
    $("floatOpen").lastChild.textContent = `${stepLabel(st, stepNow)} · ${stepName(st)}`;
    const failed = checkFailed(), last = state.last;
    $("float").classList.toggle("failed", failed);
    const note = $("floatNote");
    note.hidden = !(failed || (last && !last.ok && !state.busy));
    note.textContent = failed ? "Not passed yet. Open the guide to see what the check found."
                              : "That step did not finish. Open the guide for details.";
    const p = primaryAction(), btn = $("floatPrimary");
    mirrorPrimary(btn, p);
  }
  $("floatMin").onclick = () => { $("float").classList.add("mini"); store.set("rodeo-float", "mini"); };
  $("floatOpen").onclick = () => { $("float").classList.remove("mini"); store.set("rodeo-float", null); };
  if (store.get("rodeo-float") === "mini") $("float").classList.add("mini");

  // -- chapter cover and chapter complete -----------------------------------
  const seenKey = (i) => `rodeo-cover-${i}`;
  function showOverlay(name) {
    overlay = name;
    $("lobby").hidden = name !== "lobby";
    document.body.classList.toggle("in-lobby", name === "lobby");
    $("cover").hidden = name !== "cover";
    $("complete").hidden = name !== "complete";
  }

  function showCover(v) {
    const cover = v.sections.find((s) => s.kind === "cover");
    const nTask = v.steps.filter((s) => s.kind === "task").length, nOpt = v.steps.length - nTask;
    $("coverEyebrow").textContent = v.number ? `Chapter ${v.number}` : `Chapter ${v.index + 1}`;
    $("coverTitle").textContent = nameOf(v);
    $("coverLead").textContent = v.teaser || "";
    $("coverLead").hidden = !v.teaser;
    const stats = $("coverStats"); stats.replaceChildren();
    const stat = (k, val) => { const d = el("div"); d.append(text("span", "", k), text("span", "", val)); stats.append(d); };
    stat("Your mission", `${nTask} ${nTask === 1 ? "task" : "tasks"}${nOpt ? ` + ${nOpt} optional` : ""}`);
    if (v.difficulty) stat("Level", v.difficulty[0].toUpperCase() + v.difficulty.slice(1));
    const story = $("coverStory"); story.replaceChildren();
    if (cover) story.append(...content(cover.markdown).childNodes);
    const starting = !state.entered && v.index === state.current;
    $("coverStart").textContent = starting ? "Start mission" : "Continue mission";
    showOverlay("cover");
    fixContrast(story);
    $("coverStart").focus({preventScroll: true});
    $("cover").scrollTop = 0;
  }
  function closeCover(startIfNeeded) {
    if (view) store.set(seenKey(view.index), "1");
    showOverlay(null);
    if (startIfNeeded && !state.entered && viewing === state.current && !state.busy) run("enter");
  }
  $("coverStart").onclick = () => closeCover(true);
  $("coverSkip").onclick = () => closeCover(true);
  $("storyBtn").onclick = () => { if (view) showCover(view); };

  async function showComplete(index) {
    const v = await api(`/api/chapter/${index}`);
    const trackDone = state.completed.includes(state.chapters.length - 1);
    const wholeTrack = trackDone && index === state.chapters.length - 1;
    $("completeEyebrow").textContent = wholeTrack ? "Track complete" : `Chapter ${index + 1} complete`;
    $("completeTitle").textContent = wholeTrack ? state.title : nameOf(v);
    const did = $("completeDid"); did.replaceChildren();
    // The whole track: every chapter. One chapter: its tasks.
    // A chapter the student skipped was solved for them: say so, not a tick.
    const skipped = new Set(state.skipped || []);
    const lines = wholeTrack ? state.chapters.map((c) => [`${c.index + 1} · ${nameOf(c)}`, skipped.has(c.index)])
                             : v.steps.filter((s) => s.kind === "task" && s.name).map((s) => [s.name, false]);
    lines.forEach(([line, skip]) => {
      const li = el("li", skip ? "skipped" : "", skip ? ICON.skipped : ICON.check);
      li.append(text("span", "", line));
      if (skip) li.append(text("span", "tag", "skipped"));
      did.append(li);
    });
    if (!did.children.length) { const li = el("li", "", ICON.check); li.append(text("span", "", "Finished the chapter")); did.append(li); }
    const next = state.chapters[state.current];
    const hasNext = !trackDone && next && next.index !== index;
    $("completeNextBox").hidden = !hasNext;
    if (hasNext) $("completeNext").textContent = `${next.index + 1} · ${nameOf(next)}`;
    const why = $("completeWhy"); why.replaceChildren();
    v.sections.filter((s) => s.kind === "why" || s.kind === "more").forEach((s, k) => {
      const d = el("details"); d.open = k === 0 && s.kind === "why";
      d.append(text("summary", "", s.title), content(s.markdown));
      why.append(d);
    });
    $("completeGo").textContent = hasNext ? "Continue" : "Back to the lab";
    showOverlay("complete");
    fixContrast(why);
    $("completeGo").focus({preventScroll: true});
    $("complete").scrollTop = 0;
  }
  $("completeGo").onclick = () => {
    showOverlay(null);
    if (view && view.index === state.current && !store.get(seenKey(state.current))) showCover(view);
  };

  // -- lobby (first visit) and lab health -----------------------------------
  // The lobby shows once per browser, before chapter 1 starts: each lab tab
  // answering, and the chapters ahead. Health comes from GET /api/health,
  // which probes the lab from the host and caches the answer.
  const LOBBY_PATIENCE = 90000;  // then "Continue anyway" (a slow probe never locks anyone out)
  let lobbyTimer = null, lobbySince = 0, healthTimer = null;
  const wantsLobby = () => !store.get("rodeo-lobby") && state.current === 0 && !state.entered && !state.completed.length;

  function showLobby() {
    $("lobbyEyebrow").textContent = state.title;
    const ol = $("lobbyChapters"); ol.replaceChildren();
    if (state.chapters.length) {
      $("lobbyCount").textContent = `${state.chapters.length} chapters`;
      state.chapters.forEach((c) => ol.append(text("li", "", nameOf(c))));
    } else {
      // No track: what the lab holds, and where the instructions are.
      $("lobbyCount").textContent = "Your lab";
      (state.tabs || []).forEach((t) => ol.append(text("li", "", t.title)));
      $("lobbyKnow").textContent = state.guide_url
        ? "The workshop guide opens from the guide panel, next to your credentials."
        : "Your credentials are in the guide panel and under Credentials at the top.";
    }
    showOverlay("lobby");
    lobbySince = Date.now();
    const tick = async () => {
      try { renderLobby(await api("/api/health")); } catch (e) { /* next tick */ }
      if (Date.now() - lobbySince > LOBBY_PATIENCE) $("lobbyAnyway").hidden = false;
    };
    tick();
    lobbyTimer = setInterval(tick, 3000);
  }

  function renderLobby(h) {
    const rows = $("lobbyRows"); rows.replaceChildren();
    const late = Date.now() - lobbySince > LOBBY_PATIENCE;
    h.items.forEach((i) => {
      const kind = i.ok === true ? "ok" : i.ok === false ? (late ? "bad" : "wait") : "";
      const r = el("div", `lrow ${kind}`, `<span class="ic">${i.ok ? ICON.check : ""}</span>`);
      r.append(text("span", "name", i.name),
        text("span", "st", i.ok === true ? "Ready" : i.ok === false ? (late ? "Not answering yet" : "Starting") : "Not checked"));
      rows.append(r);
    });
    const checked = h.items.filter((i) => i.ok !== null), up = checked.filter((i) => i.ok).length;
    const bar = el("div"); bar.id = "lobbyBar";
    const fill = el("span"); fill.style.width = `${checked.length ? Math.round(100 * up / checked.length) : 100}%`;
    bar.append(fill); rows.append(bar);
    const ready = h.ready || !h.checked;
    $("lobbyTitle").textContent = ready ? "Your lab is ready" : "Your lab is getting ready";
    $("lobbyGo").disabled = !ready;
    $("lobbyNote").hidden = ready;
    if (ready) $("lobbyAnyway").hidden = true;
  }
  function leaveLobby() {
    store.set("rodeo-lobby", "1");
    clearInterval(lobbyTimer); lobbyTimer = null;
    showOverlay(null);
    if (view && !store.get(seenKey(view.index))) showCover(view);
  }
  $("lobbyGo").onclick = leaveLobby;
  $("lobbyAnyway").onclick = leaveLobby;

  function renderHealth(h) {
    const b = $("health");
    b.hidden = !h.checked;
    if (!h.checked) return;
    const down = h.items.filter((i) => i.ok === false);
    b.classList.toggle("ok", !down.length); b.classList.toggle("warn", !!down.length);
    b.querySelector(".txt").textContent = down.length ? `${down.map((i) => i.name).join(", ")} not answering` : "Lab healthy";
    const label = h.items.map((i) => `${i.name}: ${i.ok === true ? "answering" : i.ok === false ? "not answering" : "not checked"}`).join("\n");
    b.title = label; b.setAttribute("aria-label", `Lab health. ${label.replace(/\n/g, ". ")}`);
  }
  async function checkHealth() {
    if (document.hidden) return;
    try { renderHealth(await api("/api/health")); } catch (e) { /* keep the last answer */ }
  }
  $("health").onclick = checkHealth;
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkHealth(); });

  // -- credentials (header popover; the guide panel too on a lab without a track)
  let creds = null;
  async function loadCredentials() {
    if (creds === null) {
      try { creds = (await api("/api/credentials")).credentials || []; } catch (e) { creds = []; }
    }
    return creds;
  }
  function credsList(list) {
    const box = el("div", "creds");
    list.forEach((c) => {
      const item = el("div", "c");
      item.append(text("b", "", c.label));
      [["User", c.user], ["Password", c.password]].forEach(([k, v]) => {
        const row = el("div", "kv");
        const btn = el("button", "copy", `${ICON.copy}<span class="label">Copy</span>`);
        btn.type = "button"; btn.title = `Copy the ${k.toLowerCase()}`;
        btn.onclick = () => copyText(v, btn);
        row.append(text("span", "", k), text("code", "", v), btn);
        item.append(row);
      });
      box.append(item);
    });
    return box;
  }
  async function toggleCreds(open) {
    const pop = $("credsPop");
    const show = open === undefined ? pop.hidden : open;
    if (show) {
      const list = await loadCredentials();
      pop.replaceChildren(list.length ? credsList(list) : text("span", "", "No credentials for this lab."));
    }
    pop.hidden = !show;
    $("credsBtn").setAttribute("aria-expanded", String(show));
  }
  $("credsBtn").onclick = (e) => { e.stopPropagation(); toggleCreds(); };
  document.addEventListener("click", (e) => {
    if (!$("credsPop").hidden && !e.target.closest("#credsPop")) toggleCreds(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("credsPop").hidden) { toggleCreds(false); $("credsBtn").focus(); }
  });

  // A lab without a track: no chapters, steps or checks. The tabs, a terminal,
  // the credentials and the web guide, with the guide folded at first.
  let freeShown = false;
  async function showFreeLab() {
    document.body.classList.add("free");
    $("chapterTitle").textContent = "Your lab";
    $("guideMeta").textContent = "Your lab";
    if (!freeShown) {
      freeShown = true;
      if (!store.get("rodeo-free-seen")) { store.set("rodeo-free-seen", "1"); setFolded(true); }
      setTabs(state.tabs || []);
    }
    const box = el("div", "freebox");
    if (state.guide_url) {
      const g = el("div", "fcard");
      g.append(text("h3", "", "Workshop guide"),
        text("p", "", "The exercises for this lab are in the workshop guide. It opens in a new browser tab."));
      const a = el("a", "btn primary", `${ICON.guide}<span>Open the workshop guide</span>`);
      a.href = state.guide_url; a.target = "_blank"; a.rel = "noopener noreferrer";
      g.append(a); box.append(g);
    }
    const list = await loadCredentials();
    if (list.length) {
      const c = el("div", "fcard");
      c.append(text("h3", "", "Sign in"), credsList(list));
      box.append(c);
    }
    if (!state.guide_url) {
      const n = el("div", "fcard");
      n.append(text("h3", "", "No step-by-step guide here"),
        text("p", "", "Use the tabs to reach each part of the lab and the terminal for the command line."));
      box.append(n);
    }
    $("guideBody").replaceChildren(box);
    // The floating card (guide folded): where the guide and credentials are.
    $("floatTag").textContent = "Your lab";
    $("floatName").textContent = state.guide_url ? "Workshop guide and credentials" : "Credentials";
    $("floatExcerpt").textContent = state.guide_url
      ? "Open the guide for the exercises. Your user names and passwords are there too."
      : "Open the guide panel for your user names and passwords.";
    $("floatOpen").innerHTML = `${ICON.guide}<span>Your lab</span>`;
    $("floatGuide").textContent = "Credentials";
    $("floatNote").hidden = true;
    const fp = $("floatPrimary");
    fp.hidden = !state.guide_url;
    fp.disabled = false;
    fp.classList.remove("done");
    fp.textContent = "Workshop guide";
    fp.onclick = () => window.open(state.guide_url, "_blank", "noopener,noreferrer");
  }

  // -- loading a chapter ----------------------------------------------------
  async function loadChapter(i) {
    view = await api(`/api/chapter/${i}`);
    viewing = i;
    renderGuide();
    const saved = Number(store.get(`rodeo-step-${i}`));
    stepNow = Number.isInteger(saved) && saved >= 0 && saved < view.steps.length && !isDone(saved)
      ? saved : firstOpenStep();
    openStep(stepNow, false);
    $("guideBody").scrollTop = 0;
    const past = i !== state.current;
    $("reading").hidden = !past;
    if (past) {
      $("readingText").textContent = `You are reading chapter ${i + 1}. Your lab is on chapter ${state.current + 1}.`;
      $("tocurrent").textContent = `Back to chapter ${state.current + 1}`;
    }
    renderChapters();
    renderActions();
    setTabs(view.tabs);
  }

  // -- actions --------------------------------------------------------------
  const BUSY = {enter: "Setting up the chapter", check: "Checking your work", next: "Moving on",
                skip: "Skipping ahead"};
  function primaryAction() {
    const ch = state.chapters[state.current], n = state.current + 1;
    if (state.completed.includes(state.chapters.length - 1)) return {label: "Track complete", action: null};
    if (!state.entered) {
      const retry = state.last && state.last.action === "enter" && !state.last.ok;
      return {label: retry ? "Retry chapter setup" : `Start chapter ${n}`, action: "enter"};
    }
    if (ch.check) return {label: viewing !== null && viewing !== state.current ? `Check chapter ${n}` : "Check my work",
      action: "check"};
    if (state.current === state.chapters.length - 1) return {label: "Finish the track", action: "next"};
    return {label: "Next chapter", action: "next"};
  }
  // Reading a past chapter: its page has no action of its own, and the
  // current chapter's ("Finish the track") would read as this one's.
  const readingPast = () => viewing !== null && state && viewing !== state.current;

  function mirrorPrimary(btn, p) {
    btn.hidden = readingPast();
    if (state.busy) {
      btn.innerHTML = '<span class="spin"></span>';
      btn.append(document.createTextNode(`${BUSY[state.busy] || "Working"}, ${state.busy_seconds || 0} s`));
    } else {
      btn.textContent = p.label;
    }
    btn.classList.toggle("done", !p.action);
    btn.disabled = !!state.busy || !p.action;
    btn.onclick = () => { if (p.action) run(p.action); };
  }

  // A failed check on the chapter the student is on, not yet followed by a pass.
  function checkFailed() {
    const last = state && state.last;
    return !!(last && !state.busy && last.action === "check" && !last.ok && viewing === state.current);
  }
  const failKey = (i) => `rodeo-fails-${i}`;
  // The step the student was on when the check failed: the card and the
  // orange mark stay there, even if they tick other steps meanwhile.
  const failStepKey = (i) => `rodeo-failstep-${i}`;
  function failStep() {
    const k = Number(store.get(failStepKey(viewing)));
    return Number.isInteger(k) ? k : stepNow;
  }

  function card(kind, title, detail) {
    const c = el("div", `card ${kind}`, `<span class="ic">${kind === "good" ? ICON.check : ICON.alert}</span>`);
    const t = text("span", "t", title); c.append(t);
    if (detail) c.append(text("span", "d", detail));
    return c;
  }

  function renderActions() {
    if (!state) return;
    const p = primaryAction();
    document.body.classList.toggle("is-busy", !!state.busy);
    mirrorPrimary($("primary"), p);
    const skip = $("skip");
    skip.hidden = !p.action || state.current >= state.chapters.length - 1 || !!state.busy || readingPast();
    skip.onclick = () => askSkip(state.current + 1);

    const status = $("status");
    status.replaceChildren();
    if (state.busy) status.append(el("span", "spin"), document.createTextNode(`${BUSY[state.busy] || state.busy}, ${state.busy_seconds || 0} s`));

    const res = $("result"), last = state.last;
    const stepRes = document.querySelector(`#guideBody .step[data-step="${failStep()}"] .sresult`);
    if (state.busy) { res.replaceChildren(); if (stepRes) stepRes.replaceChildren(); lastShown = null; renderFloat(); return; }
    const key = last ? `${last.action}:${last.finished}` : "";
    if (key !== lastShown) {
      lastShown = key;
      res.replaceChildren();
      document.querySelectorAll("#guideBody .sresult").forEach((r) => r.replaceChildren());
      if (last) showResult(last, res, stepRes, key);
    }
    updateSteps();
  }

  function showResult(last, res, stepRes, key) {
    const check = last.scripts.find((s) => s.kind === "check");
    const cur = state.chapters[state.current];
    if (last.ok) {
      if (last.action === "check") {
        const passedNow = state.completed[state.completed.length - 1];
        store.set(failKey(passedNow), null); store.set(failStepKey(passedNow), null);
      }
      let title = last.message || "Ready.", detail = "";
      if (last.action === "skip") { title = `Skipped to chapter ${state.current + 1}`; detail = nameOf(cur); }
      else if (last.action === "enter") {
        title = `Chapter ${state.current + 1} is ready`;
        detail = cur.check ? "Work through the steps, then press Check my work." : "Work through the steps, then move on.";
      } else if (last.message === "track complete") { title = "Track complete. Well done!"; }
      else return;  // a passed chapter gets its own screen
      const good = card("good", title, detail);
      res.append(good);
      setTimeout(() => good.remove(), 10000);
      return;
    }
    const isCheck = last.action === "check" && check && check.exit !== 0;
    if (isCheck) {
      // Count attempts once per finished check, per browser.
      const seen = store.get("rodeo-last-fail");
      let n = Number(store.get(failKey(state.current))) || 0;
      const fresh = seen !== key;
      if (fresh) {
        n += 1; store.set(failKey(state.current), String(n)); store.set("rodeo-last-fail", key);
        store.set(failStepKey(state.current), String(stepNow));
      }
      const c = card("bad", "Not passed yet",
        check.timed_out ? "The check ran out of time. Try again in a moment." :
          "Here is what the check found. Fix it in the lab, then check again.");
      const count = text("span", "count", `Attempt ${n}`);
      c.querySelector(".t").append(count);
      if (check.output) { const out = text("pre", "output", check.output); c.append(out); }
      const box = fresh ? document.querySelector(`#guideBody .step[data-step="${stepNow}"] .sresult`) : stepRes;
      (box || res).append(c);
      if (fresh && !root.classList.contains("guide-folded")) c.scrollIntoView({block: "nearest", behavior: "smooth"});
      return;
    }
    const failed = last.scripts.filter((s) => s.exit !== 0 || s.timed_out).map((s) =>
      s.timed_out ? `The ${s.kind} script timed out.` : `The ${s.kind} script exited with ${s.exit}.`);
    res.append(card("bad", "That step did not finish", [last.message, ...failed].filter(Boolean).join(" ")));
  }

  // -- state, polling and moves ---------------------------------------------
  async function refresh(reloadChapter) {
    const prev = state;
    state = {...await api("/api/state"), tabsNow: state && state.tabsNow};
    $("product").textContent = state.title;
    document.title = state.title;
    $("credsBtn").hidden = !state.credentials;
    if (!state.chapters.length) {
      await showFreeLab();
      if (!overlay && wantsLobby()) showLobby();
      return;
    }
    const moved = prev && prev.current !== state.current;
    if (reloadChapter || viewing === null || moved) await loadChapter(state.current);
    // A cover still open from before the move belongs to the old chapter.
    if (moved && overlay === "cover") showOverlay(null);
    renderChapters();
    renderActions();
    if (state.busy && !polling) polling = setInterval(poll, 2000);
    if (state.busy) return;
    // A chapter just passed: its own screen first, then the next cover.
    const passed = prev ? state.completed.filter((c) => !prev.completed.includes(c)) : [];
    if (passed.length && state.last && state.last.ok && state.last.action !== "skip") {
      await showComplete(passed[passed.length - 1]);
    } else if (!overlay && wantsLobby()) {
      showLobby();
    } else if (!overlay && viewing === state.current && !store.get(seenKey(state.current))) {
      showCover(view);
    }
  }

  async function poll() {
    try { await refresh(false); } catch (e) { return; }  // a dropped poll retries on the next tick
    if (!state.busy) { clearInterval(polling); polling = null; }
  }

  function showError(message) {
    lastShown = null;
    $("result").replaceChildren(card("bad", "That did not work", message));
  }

  async function run(action, target) {
    try {
      await api(`/api/${action}`, target === undefined ? {} : {target});
    } catch (e) { setFolded(false); showError(e.message); return; }
    await refresh(false);
  }

  $("confirmYes").onclick = () => { $("confirm").hidden = true; if (pendingSkip !== null) run("skip", pendingSkip); };
  $("confirmNo").onclick = () => { pendingSkip = null; $("confirm").hidden = true; };
  $("tocurrent").onclick = () => loadChapter(state.current);
  refresh(true).catch((e) => showError(e.message));
  checkHealth();
  healthTimer = setInterval(checkHealth, 30000);
})();
