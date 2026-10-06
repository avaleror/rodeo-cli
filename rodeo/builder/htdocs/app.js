// Rodeo Builder — the page. Data comes through apiGet(action) only: a live
// server answers it, and the static build (scripts/build-builder-static.py)
// replaces it with the embedded answers. Pure logic lives in logic.js (RB).
"use strict";

async function apiGet(action, params = {}) {
  const q = new URLSearchParams({ action, ...params });
  const res = await fetch("api?" + q.toString(), { headers: { Accept: "application/json" } });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

const $ = (sel) => document.querySelector(sel);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c);
  return node;
}

const state = {
  engines: [], capabilities: [], workshops: [], liab: { builder_url: "", addons: [], imports: [] },
  engine: "rancher", base: null, planEdited: null, mode: "link", importProfile: "", labAddons: [], labJson: null,
  chapters: [], custom: [], name: "my-rodeo", title: "My rodeo", lang: "en", target: "baremetal",
  variant: "", extraVariants: [], query: "", open: {}, newWorkshop: "custom", fileView: "rodeo-plan.yaml",
  editing: -1, edTab: "spans", drag: null,
};

function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", !!bad);
}

// ── derived ─────────────────────────────────────────────────────────────

function engine() { return state.engines.find((e) => e.name === state.engine); }
function importBase() { return state.liab.imports.find((i) => i.profile === state.importProfile); }
function isLiab() { return state.engine === "lab-in-a-box"; }
function liabImported() { return isLiab() && state.mode === "import" && !!importBase(); }

function provided() {
  if (!isLiab()) return new Set(engine() ? engine().provides : []);
  return new Set(liabImported() ? importBase().addons : state.labAddons);
}

function missing(chapter) { const p = provided(); return (chapter.needs || []).filter((n) => !p.has(n)); }

function libraryGroups() {
  const groups = state.workshops.map((w) => ({ id: w.id, title: w.title, sub: w.source, chapters: w.chapters,
    engine: w.engine, profile: w.profile, plan: w.plan }));
  groups.push({ id: "custom", title: "My chapters", sub: "created in this builder", chapters: state.custom });
  return groups;
}

// The engine and base profile a library group's chapters run on.
function groupMatches(g) {
  if (!g.engine || g.engine !== state.engine) return false;
  if (g.engine === "lab-in-a-box") return liabImported() && importBase().profile === g.profile;
  return baseProfile() === g.profile;
}

function useGroup(g) {
  if (!confirmPlanDrop()) return;
  state.engine = g.engine;
  state.planEdited = null;
  if (g.engine === "lab-in-a-box") {
    state.base = null;
    state.mode = "import";
    state.importProfile = g.profile;
    state.labJson = null;
  } else {
    state.base = g.profile === engine().base ? null : { profile: g.profile, plan: g.plan };
  }
  render();
  toast("Lab engine " + g.engine + " with the " + g.profile + " profile, as " + g.title + " needs");
}

function confirmPlanDrop() {
  return state.planEdited === null ||
    window.confirm("plan.yaml was edited by hand. Changing the lab engine regenerates it and drops those edits. Continue?");
}

function libKey(groupId, chapterId) { return groupId + "/" + chapterId; }
function inRodeo(groupId, chapterId) { return state.chapters.some((c) => c.from === libKey(groupId, chapterId)); }

function allVariants() {
  const set = new Set([...RB.variantsIn(state.chapters), ...state.extraVariants]);
  return [...set].sort();
}

function baseProfile() {
  if (isLiab()) return liabImported() ? importBase().profile : "";
  if (state.base) return state.base.profile;
  return engine() ? engine().base : "";
}

function story() { return { language: state.lang, id: state.variant }; }

function planOptions() { return { name: state.name, target: state.target, story: story() }; }

function generatedPlan() {
  if (liabImported()) return RB.planFromBase(importBase().plan, planOptions());
  if (isLiab()) return RB.planLabinabox({ ...planOptions(), addons: state.labAddons });
  return RB.planFromBase(state.base ? state.base.plan : engine().plan, planOptions());
}

// A hand-edited plan keeps every edit; name, deployment_target and story follow the rodeo tab.
function plan() {
  return state.planEdited === null ? generatedPlan() : RB.planFromBase(state.planEdited, planOptions());
}

function rodeo() {
  return {
    name: state.name, title: state.title, base: baseProfile(), plan: plan(),
    definition: isLiab() && !liabImported() ? engine().definition : "",
    chapters: state.chapters, variants: allVariants(), story: story(), labJson: state.labJson,
  };
}

// ── library ─────────────────────────────────────────────────────────────

function addChapter(group, ch, at) {
  if (inRodeo(group.id, ch.id)) return;
  const used = new Set(state.chapters.map((c) => c.id));
  const copy = { ...ch, id: RB.uniqueId(ch.id, used), workshop: group.title, from: libKey(group.id, ch.id) };
  if (at === undefined || at < 0) state.chapters.push(copy); else state.chapters.splice(at, 0, copy);
  render();
}

function renderLibrary() {
  const box = $("#library");
  box.replaceChildren();
  const q = state.query.trim().toLowerCase();
  let total = 0;
  for (const g of libraryGroups()) {
    const list = g.chapters.filter((c) => !q || (c.title + " " + c.id).toLowerCase().includes(q));
    total += g.chapters.length;
    if (q && !list.length) continue;
    const open = state.open[g.id] !== false;
    const head = el("div", { class: "group-bar" },
      el("button", { class: "group-head", type: "button", "aria-expanded": String(open),
        onclick: () => { state.open[g.id] = !open; renderLibrary(); } },
        el("span", { class: "chev", text: open ? "▾" : "▸" }),
        el("span", { class: "group-text" }, el("span", { class: "group-title", text: g.title }),
          el("span", { class: "group-sub", text: g.sub }))),
      el("div", { class: "group-actions" },
        g.engine ? el("span", { class: "chip" + (groupMatches(g) ? " ok" : ""), title: "Runs on the " + g.engine +
          " engine with the " + g.profile + " profile", text: g.engine + (g.profile && g.profile !== g.engine ? " · " + g.profile : "") }) : null,
        g.engine && !groupMatches(g) ? el("button", { class: "btn sm outline", type: "button", text: "use",
          title: "Switch the lab engine to " + g.engine + " (" + g.profile + ")", onclick: () => useGroup(g) }) : null,
        g.chapters.length ? el("button", { class: "btn sm", type: "button", text: "+ all " + g.chapters.length,
          onclick: () => { for (const c of g.chapters) addChapter(g, c); } }) : null));
    const cards = el("div", { class: "cards" });
    if (open) {
      if (!list.length) cards.append(el("div", { class: "empty", text: g.id === "custom" ? "No chapters yet: + New." : "No chapters." }));
      for (const c of list) {
        const added = inRodeo(g.id, c.id), lacks = missing(c).length > 0;
        cards.append(el("button", {
          class: "card" + (added ? " added" : "") + (lacks ? " lacks" : ""), type: "button", draggable: "true",
          title: added ? "Already in this rodeo" : "Add to this rodeo",
          onclick: () => addChapter(g, c),
          ondragstart: (ev) => { state.drag = { kind: "lib", group: g, chapter: c }; ev.dataTransfer.setData("text/plain", c.id); },
          ondragend: () => { state.drag = null; clearDrop(); },
        }, el("span", { class: "dot" }), el("span", { class: "card-title", text: c.title }),
        el("span", { class: "card-meta", text: c.mins + " min" + (c.needs.length ? " · " + c.needs.join(", ") : "") +
          (added ? " · in rodeo" : "") + (c.isNew ? " · new" : "") })));
      }
    }
    box.append(el("div", { class: "group" }, head, cards));
  }
  $("#libCount").textContent = String(total);
}

// ── engines ─────────────────────────────────────────────────────────────

function gib(mib) { return Math.round(mib / 1024) + " GiB"; }

function selectEngine(name) {
  if (name === state.engine && !state.base) return;
  if (!confirmPlanDrop()) return;
  state.engine = name;
  state.base = null;
  state.planEdited = null;
  render();
}

function engineSummary(e) {
  if (e.external) return "VMs, clusters and add-ons you choose";
  const r = e.resources;
  return r.nodes + (r.nodes === 1 ? " VM" : " VMs") + " · " + gib(r.memory_mib) + " RAM";
}

function renderEngines() {
  const box = $("#engines");
  box.replaceChildren(...state.engines.map((e) => {
    const on = e.name === state.engine;
    return el("button", {
      class: "engine" + (on ? " on" : ""), type: "button", role: "radio", "aria-checked": String(on),
      onclick: () => selectEngine(e.name),
    }, el("span", { class: "engine-top" }, el("span", { class: "radio", "aria-hidden": "true" }),
      el("span", { class: "engine-name", text: e.name }), on ? el("span", { class: "selected", text: "selected" }) : null),
    el("span", { class: "engine-title", text: e.title }),
    el("span", { class: "engine-sub", text: engineSummary(e) }),
    e.provides.length ? el("span", { class: "engine-provides", text: e.provides.join(" · ") }) : null);
  }));
  const e = engine();
  $("#engineNow").textContent = e ? "· " + e.name + (baseProfile() && baseProfile() !== e.name ? " · " + baseProfile() : "") : "";
  renderEnginePanel();
}

function chipList(names, cls) { return el("div", { class: "chips" }, names.map((n) => el("span", { class: "chip " + (cls || ""), text: n }))); }

function neededAddons() {
  const p = provided(), need = new Set();
  for (const c of state.chapters) for (const n of c.needs || []) if (!p.has(n)) need.add(n);
  return [...need].sort();
}

function addonTargets(name) {
  const a = state.liab.addons.find((x) => x.name === name);
  return a ? a.targets : [];
}

function addAddon(name) {
  name = name.trim().toLowerCase().replace(/^install_/, "");
  if (!name || state.labAddons.includes(name)) return;
  state.labAddons.push(name);
  render();
}

function labBuilderUrl(extra) {
  const url = new URL(state.liab.builder_url, location.href);
  if (state.labAddons.length) url.searchParams.set("addons", state.labAddons.join(","));
  for (const [k, v] of Object.entries(extra || {})) url.searchParams.set(k, v);
  return url.toString();
}

function readLabJson(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const lab = JSON.parse(reader.result);
      if (!lab || typeof lab.nodes !== "object") throw new Error("no nodes in this file");
      useLab(lab, file.name);
    } catch (err) { toast("Not a lab-in-a-box lab.json: " + err.message, true); }
  };
  reader.readAsText(file);
}

function useLab(lab, source) {
  state.labJson = lab;
  state.labAddons = RB.labJsonAddons(lab);
  state.importProfile = "";
  toast("Lab from " + source + ": " + (state.labAddons.join(", ") || "no add-ons"));
  render();
}

function renderEnginePanel() {
  const box = $("#enginePanel"), e = engine();
  box.replaceChildren();
  if (!e) return;
  if (!e.external) {
    const r = e.resources;
    box.append(el("h3", { text: e.title }),
      el("p", { class: "mono muted", text: r.nodes + " VMs · " + gib(r.memory_mib) + " RAM · " + r.vcpu + " vCPU (" + e.base + " profile)" }),
      el("p", { text: e.note }));
    if (state.base) {
      box.append(el("p", {}, "Base profile ", el("strong", { class: "mono", text: state.base.profile }),
        ": the chapters of that workshop need its extra setup. ",
        el("button", { class: "btn link", type: "button", text: "Use " + e.base + " instead",
          onclick: () => { if (confirmPlanDrop()) { state.base = null; state.planEdited = null; render(); } } })));
    } else {
      box.append(el("p", {}, "Base profile ", el("strong", { class: "mono", text: e.base })));
    }
    box.append(el("span", { class: "label", text: "Provides" }), chipList(e.provides, "ok"));
    return;
  }
  const modes = [["link", "Open in Lab Builder"], ["embed", "Embed here"], ["import", "Import existing lab"]];
  box.append(el("h3", { text: "Lab built by lab-in-a-box" }), el("p", { text: e.note }),
    el("div", { class: "seg", role: "tablist" }, modes.map(([m, label]) => el("button", {
      type: "button", class: state.mode === m ? "on" : "", "aria-selected": String(state.mode === m),
      onclick: () => { state.mode = m; render(); }, text: label }))));
  const upload = el("label", { class: "btn sm" }, "Upload lab.json",
    el("input", { type: "file", accept: ".json,application/json", class: "sr-only",
      onchange: (ev) => ev.target.files[0] && readLabJson(ev.target.files[0]) }));
  if (state.mode === "link") {
    box.append(el("p", { text: "Design the lab in lab-in-a-box's lab-builder (this rodeo's add-ons are prefilled), download its lab.json, then upload it here to bring the add-ons back." }),
      el("div", { class: "row2 chips" }, el("a", { class: "btn outline sm", href: labBuilderUrl(), target: "_blank", rel: "noopener", text: "Open lab-builder ↗" }), upload));
  } else if (state.mode === "embed") {
    box.append(el("p", { text: "Opens the lab-builder inside this page; \"Use this lab\" there hands the lab back. Needs a lab-in-a-box release with the embed hand-off; until then, use Download in the lab-builder and Upload here." }),
      el("div", { class: "chips" }, el("button", { class: "btn outline sm", type: "button", text: "Open embedded lab-builder", onclick: openEmbed }), upload));
  } else {
    box.append(el("p", { text: "Start from a bundled lab-in-a-box rodeo, or from a lab.json." }),
      el("div", { class: "imports" }, state.liab.imports.map((i) => el("button", {
        class: "engine" + (state.importProfile === i.profile ? " on" : ""), type: "button",
        onclick: () => { state.importProfile = state.importProfile === i.profile ? "" : i.profile; state.labJson = null; render(); },
      }, el("span", { class: "engine-name", text: i.profile }), el("span", { class: "engine-sub", text: i.addons.join(", ") })))), upload);
  }
  if (state.labJson) box.append(el("p", { class: "mono muted", text: "lab.json loaded: kept in the download for reference." }));
  box.append(el("span", { class: "label", text: "Lab add-ons" }));
  if (liabImported()) {
    box.append(chipList(importBase().addons, "ok"),
      el("p", { class: "muted", text: "Add-ons of an imported rodeo are edited in its rodeo-plan.yaml after install." }));
  } else {
    box.append(el("div", { class: "chips" }, state.labAddons.length ? state.labAddons.map((a) => el("span", { class: "chip ok" },
      a, addonTargets(a).length && !addonTargets(a).some((t) => t === "vm" || t === "baremetal")
        ? el("span", { class: "muted", text: "(cluster)" }) : null,
      el("button", { class: "x", type: "button", "aria-label": "Remove " + a, text: "×",
        onclick: () => { state.labAddons = state.labAddons.filter((x) => x !== a); render(); } })))
      : el("span", { class: "muted mono", text: "none yet" })));
    const input = el("input", { class: "input mono", list: "addonOptions", placeholder: "add-on name", "aria-label": "Add-on name",
      onkeydown: (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addAddon(input.value); } } });
    box.append(el("datalist", { id: "addonOptions" }, state.liab.addons.map((a) => el("option", { value: a.name }))),
      el("div", { class: "addon-add" }, input, el("button", { class: "btn sm", type: "button", text: "Add", onclick: () => addAddon(input.value) })));
  }
  const need = neededAddons();
  if (need.length && !liabImported()) {
    box.append(el("span", { class: "label", text: "Needed by chapters" }),
      el("div", { class: "chips" }, need.map((n) => el("button", { class: "chip need", type: "button", text: "+ " + n, onclick: () => addAddon(n) }))));
  }
}

// ── embedded lab-builder ────────────────────────────────────────────────

function openEmbed() {
  const frame = $("#embedFrame");
  frame.src = labBuilderUrl({ embed: "1", origin: location.origin });
  $("#embedNote").textContent = "Use this lab in the lab-builder hands the lab back here.";
  $("#embedOverlay").hidden = false;
}

window.addEventListener("message", (ev) => {
  if (!state.liab.builder_url) return;
  const origin = new URL(state.liab.builder_url, location.href).origin;
  if (ev.origin !== origin || !ev.data || ev.data.type !== "labinabox:lab") return;
  useLab(ev.data.lab, "the lab-builder");
  $("#embedOverlay").hidden = true;
  $("#embedFrame").src = "about:blank";
});

// ── chapter list ────────────────────────────────────────────────────────

function clearDrop() {
  document.querySelectorAll(".drop-before").forEach((n) => n.classList.remove("drop-before"));
  document.querySelectorAll(".dropzone.over").forEach((n) => n.classList.remove("over"));
}

function dropAt(index) {
  const d = state.drag;
  state.drag = null;
  clearDrop();
  if (!d) return;
  if (d.kind === "lib") { addChapter(d.group, d.chapter, index); return; }
  const [moved] = state.chapters.splice(d.index, 1);
  state.chapters.splice(index > d.index ? index - 1 : index, 0, moved);
  render();
}

function move(i, delta) {
  const j = i + delta;
  if (j < 0 || j >= state.chapters.length) return;
  [state.chapters[i], state.chapters[j]] = [state.chapters[j], state.chapters[i]];
  render();
  const row = document.querySelectorAll(".row")[j];
  if (row) row.focus();
}

function renderChapters() {
  const box = $("#chapterList");
  box.replaceChildren();
  state.chapters.forEach((c, i) => {
    const miss = missing(c), spans = RB.parseSpans(c.body).spans.length;
    const row = el("div", {
      class: "row", tabindex: "0", draggable: "true", "aria-label": "Chapter " + (i + 1) + ": " + c.title + ". Alt+Up/Down to move.",
      ondragstart: (ev) => { state.drag = { kind: "row", index: i }; ev.dataTransfer.setData("text/plain", c.id); },
      ondragend: () => { state.drag = null; clearDrop(); },
      ondragover: (ev) => { ev.preventDefault(); clearDrop(); row.classList.add("drop-before"); },
      ondrop: (ev) => { ev.preventDefault(); dropAt(i); },
      onkeydown: (ev) => { if (ev.altKey && ev.key === "ArrowUp") { ev.preventDefault(); move(i, -1); }
        if (ev.altKey && ev.key === "ArrowDown") { ev.preventDefault(); move(i, 1); } },
    },
    el("span", { class: "grip", "aria-hidden": "true", text: "⠿" }),
    el("span", { class: "row-num", text: String(i + 1).padStart(2, "0") }),
    el("div", {}, el("div", { class: "row-title", text: c.title }),
      el("div", { class: "row-meta", text: [c.workshop, c.mins + " min", c.check ? "checks/check-" + c.id + ".sh" : ""].filter(Boolean).join(" · ") }),
      el("div", { class: "chips" }, (c.needs || []).map((n) => el("span", { class: "chip " + (miss.includes(n) ? "missing" : "ok"), text: miss.includes(n) ? "missing " + n : n })))),
    el("div", { class: "row-actions" },
      el("button", { class: "btn sm", type: "button", text: spans + " spans ✎", onclick: () => openEditor(i) }),
      el("button", { class: "btn sm", type: "button", "aria-label": "Move up", text: "↑", disabled: i === 0, onclick: () => move(i, -1) }),
      el("button", { class: "btn sm", type: "button", "aria-label": "Move down", text: "↓", disabled: i === state.chapters.length - 1, onclick: () => move(i, 1) }),
      el("button", { class: "btn sm", type: "button", "aria-label": "Remove " + c.title, text: "×",
        onclick: () => { state.chapters.splice(i, 1); render(); } })));
    box.append(row);
  });
  const zone = el("div", { class: "dropzone", text: state.chapters.length ? "Drop here to append" : "Drag chapters here, or click them in the library",
    ondragover: (ev) => { ev.preventDefault(); clearDrop(); zone.classList.add("over"); },
    ondragleave: () => zone.classList.remove("over"),
    ondrop: (ev) => { ev.preventDefault(); dropAt(state.chapters.length); } });
  box.append(zone);
  const need = neededAddons(), cov = $("#coverage");
  cov.textContent = !state.chapters.length ? "" : need.length ? "lab missing: " + need.join(", ") : "lab covers every chapter";
  cov.classList.toggle("bad", need.length > 0);
}

// ── right panel ─────────────────────────────────────────────────────────

function renderSettings() {
  $("#name").value = state.name;
  $("#title").value = state.title;
  $("#lang").value = state.lang;
  $("#target").value = state.target;
  const box = $("#variants");
  const options = [["", "all spans"], ...allVariants().map((v) => [v, v])];
  if (state.variant && !allVariants().includes(state.variant)) state.variant = "";
  box.replaceChildren(...options.map(([v, label]) => el("button", {
    type: "button", class: "chip" + (state.variant === v ? " on" : ""), "aria-pressed": String(state.variant === v),
    text: label, onclick: () => { state.variant = v; render(); } })));
  const mins = state.chapters.reduce((n, c) => n + (c.mins || 0), 0);
  $("#estimate").textContent = "Estimated run time: " + mins + " min (" + state.chapters.length + " chapters)";
  $("#counts").textContent = state.chapters.length + " chapters · " + mins + " min · " + state.engine;
}

function renderPlanEditor(text) {
  const ed = $("#planEditor"), edited = state.planEdited !== null;
  if (document.activeElement !== ed && ed.value !== text) ed.value = text;
  const tabs = /^\t/m.test(ed.value);
  const st = $("#planState");
  st.textContent = tabs ? "YAML does not allow tab indentation" : edited ? "edited by hand" +
    (isLiab() && !liabImported() ? " · add-on changes no longer apply" : "") : "generated from the rodeo tab";
  st.classList.toggle("warn", tabs);
  $("#planReset").hidden = !edited;
}

function onPlanInput(ev) {
  const text = ev.target.value;
  state.planEdited = text;
  const name = /^name:[ \t]*(.+?)[ \t]*(?:#.*)?$/m.exec(text);
  if (name && RB.slugify(name[1].replace(/^["']|["']$/g, ""))) state.name = RB.slugify(name[1].replace(/^["']|["']$/g, ""));
  const target = /^deployment_target:[ \t]*["']?([\w-]+)/m.exec(text);
  if (target) {
    const sel = $("#target");
    if (![...sel.options].some((o) => o.value === target[1])) sel.append(el("option", { value: target[1], text: target[1] }));
    state.target = target[1];
  }
  render();
}

function renderFiles() {
  const files = RB.files(rodeo());
  renderPlanEditor(files[0].content);
  if (!files.some((f) => f.path === state.fileView)) state.fileView = "rodeo-plan.yaml";
  $("#fileList").replaceChildren(...files.map((f) => el("li", {}, el("button", {
    type: "button", class: state.fileView === f.path ? "on" : "", text: state.name + "/" + f.path,
    onclick: () => { state.fileView = f.path; renderFiles(); } }))));
  const current = files.find((f) => f.path === state.fileView);
  $("#fileName").textContent = current.path;
  $("#filePreview").textContent = current.content;
}

function render() {
  renderLibrary();
  renderEngines();
  renderChapters();
  renderSettings();
  renderFiles();
}

// ── new chapter ─────────────────────────────────────────────────────────

const ncNeeds = new Set();

function chapterTemplate(id) {
  return "# Chapter title\n\n" +
    '<span lang="en" id="' + id + '.1">Describe what the student does here. Keep facts that must never be ' +
    'translated in invariant spans, such as <span no id="' + id + '.2">{{ rancher_url }}</span>.</span>\n';
}

function openNewChapter() {
  ncNeeds.clear();
  $("#ncTitle").value = "";
  $("#ncMins").value = "10";
  $("#ncCheck").checked = false;
  $("#ncAppend").checked = true;
  $("#ncBody").value = chapterTemplate("new-chapter");
  renderNewChapterChips();
  $("#newModal").hidden = false;
  $("#ncTitle").focus();
}

function renderNewChapterChips() {
  $("#ncWorkshops").replaceChildren(...libraryGroups().map((g) => el("button", {
    type: "button", class: "chip" + (state.newWorkshop === g.id ? " on" : ""), text: g.title,
    onclick: () => { state.newWorkshop = g.id; renderNewChapterChips(); } })));
  $("#ncNeeds").replaceChildren(...state.capabilities.map((c) => el("button", {
    type: "button", class: "chip" + (ncNeeds.has(c) ? " on" : ""), text: c, "aria-pressed": String(ncNeeds.has(c)),
    onclick: () => { if (ncNeeds.has(c)) ncNeeds.delete(c); else ncNeeds.add(c); renderNewChapterChips(); } })));
}

function createChapter(ev) {
  ev.preventDefault();
  const title = $("#ncTitle").value.trim();
  if (!title) return;
  const group = libraryGroups().find((g) => g.id === state.newWorkshop) || libraryGroups().at(-1);
  const used = new Set(libraryGroups().flatMap((g) => g.chapters.map((c) => c.id)));
  const id = RB.uniqueId(RB.slugify(title) || "chapter", used);
  let body = $("#ncBody").value.replace(/^# .*$/m, "# " + title).replaceAll('id="new-chapter.', 'id="' + id + ".");
  if (!/^# /m.test(body)) body = "# " + title + "\n\n" + body;
  const ch = { id, file: "", title, mins: Math.max(1, parseInt($("#ncMins").value, 10) || 10), needs: [...ncNeeds],
    check: $("#ncCheck").checked, spans: 0, body, isNew: true };
  if (group.id === "custom") state.custom.push(ch);
  else state.workshops.find((w) => w.id === group.id).chapters.push(ch);
  $("#newModal").hidden = true;
  if ($("#ncAppend").checked) addChapter(group, ch); else render();
  toast("Chapter \"" + title + "\" created as " + id);
}

// ── story editor ────────────────────────────────────────────────────────

let pop = null;

function editing() { return state.chapters[state.editing]; }

function openEditor(i) {
  state.editing = i;
  $("#editor").hidden = false;
  $("#edSource").value = editing().body;
  renderEditor();
  $("#edSource").focus();
}

function closeEditor() {
  closePop();
  $("#editor").hidden = true;
  state.editing = -1;
  render();
}

function setBody(body, keepSel) {
  const ta = $("#edSource"), s = ta.selectionStart, e = ta.selectionEnd;
  editing().body = body;
  ta.value = body;
  if (keepSel) ta.setSelectionRange(s, e);
  renderEditor();
}

function renderEditor() {
  const ch = editing(), body = ch.body, { spans } = RB.parseSpans(body);
  const warnings = RB.validate(state.chapters).filter((w) => w.chapter === ch.id);
  $("#edChapter").textContent = ch.title;
  $("#edPath").textContent = "story/" + RB.chapterFile(state.editing + 1, ch.id);
  const tagged = spans.filter((s) => RB.kind(s.attrs) !== "plain").length;
  const sum = $("#edSummary");
  sum.textContent = tagged + " tagged spans · " + warnings.length + " warnings";
  sum.classList.toggle("warn", warnings.length > 0);
  $("#edHist").replaceChildren(...allVariants().map((v) => el("button", {
    type: "button", class: "chip", text: v, title: "Toggle hist=\"" + v + "\" on the span at the cursor",
    onclick: () => toggleHist(v) })));
  renderPreview(body, spans);
  renderSpanList(body, spans, warnings);
  renderStories();
  renderTranslations();
  document.querySelectorAll("[data-edtab]").forEach((b) => b.classList.toggle("on", b.dataset.edtab === state.edTab));
  for (const t of ["spans", "stories", "translations"]) $("#edtab-" + t).hidden = state.edTab !== t;
}

// The preview shows the source with tags hidden. Every text piece carries its
// source offset (data-s) so a selection there maps back to the markdown.
function renderPreview(body, spans) {
  const root = el("div"), stack = [root];
  const events = [];
  for (const sp of spans) { events.push([sp.start, sp.openEnd, "open", sp]); events.push([sp.closeStart, sp.end, "close", sp]); }
  events.sort((a, b) => a[0] - b[0]);
  let pos = 0;
  const text = (to) => { if (to > pos) stack.at(-1).append(el("span", { class: "seg", dataset: { s: String(pos) }, text: body.slice(pos, to) })); };
  for (const [at, after, type, sp] of events) {
    text(at);
    if (type === "open") {
      const k = RB.kind(sp.attrs);
      const node = el("span", { class: "sp k-" + k + (sp.attrs.hist && !sp.attrs.no ? " hist" : ""), dataset: { i: String(sp.index) },
        title: (sp.attrs.id || "no id") + " · " + k + (sp.attrs.hist ? " · hist=" + sp.attrs.hist : "") },
      sp.attrs.id ? el("span", { class: "badge", "aria-hidden": "true", text: sp.attrs.id }) : null);
      stack.at(-1).append(node);
      stack.push(node);
    } else stack.pop();
    pos = after;
  }
  text(body.length);
  $("#edPreview").replaceChildren(...root.childNodes);
}

function previewOffset(node, offset) {
  const seg = (node.nodeType === 3 ? node.parentElement : node).closest(".seg");
  return seg ? parseInt(seg.dataset.s, 10) + offset : -1;
}

function renderSpanList(body, spans, warnings) {
  const list = $("#spanList");
  list.replaceChildren();
  for (const sp of spans) {
    const k = RB.kind(sp.attrs), id = sp.attrs.id || "";
    const idInput = el("input", { class: "input mono", value: id, "aria-label": "Span id", placeholder: RB.needsId(sp.attrs) ? "id required" : "no id needed",
      onchange: () => { const fresh = RB.parseSpans(editing().body).spans[sp.index]; setBody(RB.retag(editing().body, fresh, { ...fresh.attrs, id: idInput.value.trim() || undefined })); } });
    const mine = warnings.filter((w) => (w.id && w.id === id) || (!w.id && w.at === sp.start));
    list.append(el("li", { class: "span-item d" + Math.min(sp.depth, 3) },
      el("div", { class: "span-top" }, el("span", { class: "kind k-" + k, text: k }),
        sp.attrs.hist ? el("span", { class: "chip", text: "hist=" + sp.attrs.hist }) : null,
        el("button", { class: "btn sm", type: "button", text: "edit", onclick: (ev) => editPop(sp.index, ev.clientX, ev.clientY) }),
        el("button", { class: "btn sm", type: "button", "aria-label": "Remove span", text: "×", onclick: () => setBody(RB.unwrap(body, sp)) })),
      el("div", { class: "span-snip", text: RB.inner(body, sp).replace(/<[^>]+>/g, "") }), idInput,
      mine.map((w) => el("div", { class: "warn", text: w.msg }))));
  }
  if (!spans.length) list.append(el("li", { class: "empty", text: "No spans yet: select text and mark it." }));
}

function renderStories() {
  const box = $("#storiesView");
  box.replaceChildren(...allVariants().map((v) => el("div", {},
    el("span", { class: "label", text: "story/stories/" + v + ".yaml" }),
    el("div", { class: "term" }, el("pre", { text: RB.storyYaml(v, RB.storyIndex(state.chapters, v)) })))));
  if (!allVariants().length) box.append(el("p", { class: "empty", text: "No variants: add one, then tag spans with it." }));
}

function renderTranslations() {
  const ids = [];
  for (const ch of state.chapters) for (const sp of RB.parseSpans(ch.body).spans) {
    if (sp.attrs.id && RB.kind(sp.attrs) === "translatable" && !ids.includes(sp.attrs.id)) ids.push(sp.attrs.id);
  }
  const langs = RB.LANGUAGES.filter((l) => l !== RB.SOURCE_LANGUAGE);
  $("#translationsView").replaceChildren(
    el("p", { class: "muted", text: "Translations live in rmstory's translation store; this static builder cannot read or fill it. After install, `rodeo story render --language <lang> --engine <engine>` translates the missing strings." }),
    el("table", { class: "matrix" }, el("thead", {}, el("tr", {}, el("th", { text: "id" }), langs.map((l) => el("th", { text: l })))),
      el("tbody", {}, ids.map((id) => el("tr", {}, el("td", { text: id }), langs.map(() => el("td", { class: "missing", text: "missing" })))))));
}

function closePop() { $("#popover").hidden = true; pop = null; }

function placePop(x, y) {
  const p = $("#popover");
  p.hidden = false;
  const w = p.offsetWidth, h = p.offsetHeight;
  p.style.left = Math.max(8, Math.min(x, innerWidth - w - 8)) + "px";
  p.style.top = Math.max(8, Math.min(y + 12, innerHeight - h - 8)) + "px";
}

function usedIds() { return RB.idsIn(state.chapters); }

function autoId(target) {
  const base = target.parent && target.parent.attrs.id ? target.parent.attrs.id : editing().id;
  return RB.nextId(base, usedIds());
}

function applyPop(s, e, x, y) {
  const body = editing().body, { spans } = RB.parseSpans(body);
  const target = RB.wrapTarget(body, spans, s, e);
  if (!target.ok) { toast(target.reason, true); return; }
  if (target.same) { editPop(target.same.index, x, y); return; }
  pop = { mode: "apply", s: target.s, e: target.e, hist: "" };
  const id = autoId(target);
  const p = $("#popover");
  p.replaceChildren(el("h4", { text: "Apply span" }),
    el("div", { class: "chips" }, [["translatable", "Translatable"], ["invariant", "Invariant"], ["nolang", "No-lang"]].map(([k, label]) =>
      el("button", { class: "btn sm", type: "button", text: label, onclick: () => applySpan(k, id) }))),
    allVariants().length ? el("div", { class: "row2" }, el("span", { class: "muted mono", text: "hist" }), el("span", { class: "chips" },
      allVariants().map((v) => { const b = el("button", { class: "chip", type: "button", text: v,
        onclick: () => { pop.hist = pop.hist === v ? "" : v; p.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c.textContent === pop.hist)); } }); return b; }))) : null,
    el("div", { class: "row2 mono muted", text: "id: " + id }));
  placePop(x, y);
}

function applySpan(type, id) {
  const attrs = RB.withKind({}, type);
  if (type !== "nolang" || pop.hist) attrs.id = id;
  if (type === "invariant" && !pop.hist) delete attrs.id;
  if (pop.hist) attrs.hist = pop.hist;
  const body = RB.wrap(editing().body, pop.s, pop.e, attrs);
  closePop();
  setBody(body);
}

function spanAtCursor() {
  const body = editing().body, pos = $("#edSource").selectionStart;
  const inside = RB.parseSpans(body).spans.filter((sp) => pos >= sp.openEnd && pos <= sp.closeStart);
  return inside.length ? inside.reduce((a, b) => (b.depth > a.depth ? b : a)) : null;
}

function toggleHist(v) {
  const sp = spanAtCursor();
  if (!sp) { toast("Place the cursor inside a span first", true); return; }
  const attrs = { ...sp.attrs };
  if (attrs.hist === v) delete attrs.hist; else attrs.hist = v;
  if (attrs.hist && !attrs.id) attrs.id = autoId({ parent: RB.parseSpans(editing().body).spans[sp.parent] });
  setBody(RB.retag(editing().body, sp, attrs), true);
}

function editPop(index, x, y) {
  const body = editing().body, sp = RB.parseSpans(body).spans[index];
  if (!sp) return;
  pop = { mode: "edit", index, attrs: { ...sp.attrs } };
  const text = RB.inner(body, sp);
  const p = $("#popover");
  const idInput = el("input", { class: "input mono", value: sp.attrs.id || "", "aria-label": "Span id" });
  const lang = el("select", { class: "select", "aria-label": "Language", disabled: RB.kind(pop.attrs) !== "translatable" },
    RB.LANGUAGES.map((l) => el("option", { value: l, text: l, selected: pop.attrs.lang === l })));
  const hist = el("select", { class: "select", "aria-label": "Story variant" }, el("option", { value: "", text: "no variant" }),
    allVariants().map((v) => el("option", { value: v, text: v, selected: pop.attrs.hist === v })));
  const known = [];
  for (const ch of state.chapters) for (const s of RB.parseSpans(ch.body).spans) {
    if (s.attrs.id && s.attrs.id !== sp.attrs.id && !known.some((k) => k.id === s.attrs.id)) known.push({ id: s.attrs.id, same: RB.inner(ch.body, s) === text });
  }
  known.sort((a, b) => (b.same - a.same) || a.id.localeCompare(b.id));
  const kinds = [["translatable", "Translatable"], ["invariant", "Invariant"], ["nolang", "No-lang"]];
  p.replaceChildren(el("h4", { text: "Edit span" }),
    el("div", { class: "chips" }, kinds.map(([k, label]) => el("button", {
      class: "btn sm" + (RB.kind(pop.attrs) === k ? " outline" : ""), type: "button", text: label,
      onclick: () => { pop.attrs = RB.withKind(pop.attrs, k); editPopSave(idInput, lang, hist, true); } }))),
    el("div", { class: "row2" }, lang, hist),
    el("div", { class: "row2" }, idInput, el("button", { class: "btn sm", type: "button", text: "Auto",
      onclick: () => { idInput.value = autoId({ parent: RB.parseSpans(body).spans[sp.parent] }); } })),
    known.length ? el("div", { class: "ids" }, known.map((k) => el("button", { type: "button", class: k.same ? "same" : "shares",
      text: k.id + (k.same ? " · same text" : " · shares"), title: k.same ? "Same text: reuses the translation" : "Different text: shares the stored translation",
      onclick: () => { idInput.value = k.id; } }))) : null,
    el("div", { class: "row2" }, el("button", { class: "btn sm primary", type: "button", text: "Apply", onclick: () => editPopSave(idInput, lang, hist) }),
      el("button", { class: "btn sm", type: "button", text: "Remove span", onclick: () => { closePop(); setBody(RB.unwrap(editing().body, sp)); } })));
  placePop(x, y);
}

function editPopSave(idInput, lang, hist, keepOpen) {
  const attrs = { ...pop.attrs };
  if (RB.kind(attrs) === "translatable" && !lang.disabled) attrs.lang = lang.value;
  if (hist.value) attrs.hist = hist.value; else delete attrs.hist;
  if (idInput.value.trim()) attrs.id = idInput.value.trim(); else delete attrs.id;
  const { index } = pop, sp = RB.parseSpans(editing().body).spans[index];
  setBody(RB.retag(editing().body, sp, attrs));
  if (keepOpen) { const r = $("#popover").getBoundingClientRect(); editPop(index, r.left, r.top - 12); } else closePop();
}

function bindEditor() {
  const ta = $("#edSource");
  ta.addEventListener("input", () => { closePop(); editing().body = ta.value; renderEditor(); });
  ta.addEventListener("mouseup", (ev) => { if (ta.selectionEnd > ta.selectionStart) applyPop(ta.selectionStart, ta.selectionEnd, ev.clientX, ev.clientY); });
  ta.addEventListener("dblclick", (ev) => { const sp = spanAtCursor(); if (sp) { ev.preventDefault(); editPop(sp.index, ev.clientX, ev.clientY); } });
  const pv = $("#edPreview");
  pv.addEventListener("mouseup", (ev) => {
    const sel = getSelection();
    if (!sel || sel.isCollapsed) return;
    const a = previewOffset(sel.anchorNode, sel.anchorOffset), b = previewOffset(sel.focusNode, sel.focusOffset);
    if (a < 0 || b < 0) return;
    applyPop(Math.min(a, b), Math.max(a, b), ev.clientX, ev.clientY);
  });
  pv.addEventListener("dblclick", (ev) => {
    const node = ev.target.closest(".sp");
    if (!node) return;
    getSelection().removeAllRanges();
    editPop(parseInt(node.dataset.i, 10), ev.clientX, ev.clientY);
  });
  document.querySelectorAll("[data-mark]").forEach((b) => b.addEventListener("click", (ev) => {
    if (ta.selectionEnd <= ta.selectionStart) { toast("Select text in the source first", true); return; }
    const { spans } = RB.parseSpans(editing().body);
    const target = RB.wrapTarget(editing().body, spans, ta.selectionStart, ta.selectionEnd);
    if (!target.ok) { toast(target.reason, true); return; }
    pop = { mode: "apply", s: target.s, e: target.e, hist: "" };
    applySpan(b.dataset.mark, autoId(target));
  }));
  $("#edRemove").addEventListener("click", () => {
    const sp = spanAtCursor();
    if (sp) setBody(RB.unwrap(editing().body, sp)); else toast("Place the cursor inside a span first", true);
  });
  document.querySelectorAll("[data-edtab]").forEach((b) => b.addEventListener("click", () => { state.edTab = b.dataset.edtab; renderEditor(); }));
  $("#addVariant").addEventListener("click", () => {
    const v = RB.slugify($("#newVariant").value);
    if (v && !allVariants().includes(v)) state.extraVariants.push(v);
    $("#newVariant").value = "";
    renderEditor();
  });
  $("#edDone").addEventListener("click", closeEditor);
  document.addEventListener("mousedown", (ev) => { if (pop && !ev.target.closest("#popover")) closePop(); });
}

// ── download ────────────────────────────────────────────────────────────

function download() {
  const name = RB.slugify(state.name);
  if (!name) { toast("Give the rodeo a name first", true); $("#name").focus(); return; }
  state.name = name;
  const r = rodeo();
  const warnings = RB.validate(r.chapters);
  const entries = RB.files(r).map((f) => ({ ...f, path: name + "/" + f.path }));
  const blob = new Blob([RB.zip(entries)], { type: "application/zip" });
  const a = el("a", { href: URL.createObjectURL(blob), download: name + ".zip" });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  toast(name + ".zip: " + entries.length + " files" + (warnings.length ? " · " + warnings.length + " span warnings (open ✎ to fix)" : ""), warnings.length > 0);
}

// ── init ────────────────────────────────────────────────────────────────

function bind() {
  $("#search").addEventListener("input", (ev) => { state.query = ev.target.value; renderLibrary(); });
  $("#name").addEventListener("change", (ev) => { state.name = RB.slugify(ev.target.value) || state.name; render(); });
  $("#title").addEventListener("input", (ev) => { state.title = ev.target.value; renderFiles(); });
  $("#lang").replaceChildren(...RB.LANGUAGES.map((l) => el("option", { value: l, text: l + (l === RB.SOURCE_LANGUAGE ? " (source)" : "") })));
  $("#lang").addEventListener("change", (ev) => { state.lang = ev.target.value; render(); });
  $("#target").addEventListener("change", (ev) => { state.target = ev.target.value; render(); });
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("[data-tab]").forEach((x) => x.classList.toggle("on", x === b));
    for (const t of ["rodeo", "plan", "story"]) $("#tab-" + t).hidden = t !== b.dataset.tab;
  }));
  const themeBtn = $("#themeBtn"), logo = $("#logo");
  const showTheme = () => {
    const dark = window.RBTheme.current() === "dark";
    themeBtn.textContent = dark ? "Light" : "Dark";
    logo.src = dark ? "assets/horseshoe-mark.svg" : "assets/horseshoe-mark-light.svg";
  };
  themeBtn.addEventListener("click", () => { window.RBTheme.toggle(); showTheme(); });
  showTheme();
  $("#downloadBtn").addEventListener("click", download);
  $("#planEditor").addEventListener("input", onPlanInput);
  $("#planEditor").addEventListener("blur", () => renderFiles());
  $("#planReset").addEventListener("click", () => { state.planEdited = null; render(); toast("plan.yaml regenerated"); });
  $("#newChapterBtn").addEventListener("click", openNewChapter);
  $("#newForm").addEventListener("submit", createChapter);
  $("#ncCancel").addEventListener("click", () => { $("#newModal").hidden = true; });
  $("#newModal").addEventListener("mousedown", (ev) => { if (ev.target === ev.currentTarget) $("#newModal").hidden = true; });
  $("#embedClose").addEventListener("click", () => { $("#embedOverlay").hidden = true; $("#embedFrame").src = "about:blank"; });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (pop) closePop(); else if (!$("#newModal").hidden) $("#newModal").hidden = true;
  });
  bindEditor();
}

async function init() {
  bind();
  try {
    const [engines, workshops, liab] = await Promise.all([apiGet("engines"), apiGet("workshops"), apiGet("labinabox")]);
    state.engines = engines.engines;
    state.capabilities = engines.capabilities;
    state.workshops = workshops.workshops;
    state.liab = liab;
    render();
  } catch (err) {
    toast("Could not load the builder data: " + err.message, true);
  }
}

document.addEventListener("DOMContentLoaded", init);
