// Rodeo Builder — the page. Data comes through apiGet(action) only: the live
// server (`rodeo builder`) answers it, and the static build
// (scripts/build-builder-static.py) replaces it with the embedded answers and
// sets window.RB_STATIC, which keeps the server-only controls hidden.
// Pure logic lives in logic.js (RB).
"use strict";

async function apiGet(action, params = {}) {
  const q = new URLSearchParams({ action, ...params });
  const res = await fetch("api?" + q.toString(), { headers: { Accept: "application/json" } });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

// POST to the live server; the write token comes from the meta tag it serves.
// A server restarted since the page loaded has a new token: its 403 asks for a reload.
async function apiPost(action, payload) {
  const meta = document.querySelector('meta[name="rodeo-builder-token"]');
  const res = await fetch("api?" + new URLSearchParams({ action }).toString(), {
    method: "POST", body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json", Accept: "application/json", "X-Rodeo-Builder-Token": meta ? meta.content : "" },
  });
  const body = await res.json();
  if (res.status === 403 && /X-Rodeo-Builder-Token/.test(body.error || "")) {
    body.error = "the builder was restarted: reload the page to continue";
  }
  return { status: res.status, body };
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

// replaceChildren() for el()-style children: arrays are flattened, null/false skipped.
function fill(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
}

const state = {
  engines: [], capabilities: [], targets: [], contentFormats: [], workshops: [], server: null,
  liab: { builder_url: "", error: "", addons: [], infrastructure: [], kclusters: [], imports: [] },
  engine: "rancher", base: null, planEdited: null, mode: "link", importProfile: "", labAddons: [], labJson: null,
  chapters: [], custom: [], name: "my-rodeo", title: "My rodeo", lang: "en", target: "baremetal",
  variant: "", extraVariants: [], query: "", open: {}, info: null, details: false, filters: { engine: false, added: false, runnable: false }, newWorkshop: "custom", fileView: "rodeo-plan.yaml",
  editing: -1, edTab: "spans", drag: null, cursor: -1, undo: null, copied: false,
  strings: {}, story: null, trLang: "", trEngine: "", trEdit: null, busy: "", rmCheck: null, imported: [],
  contentUrl: "", contentFormat: "rmstory",
  deploy: null, deployLogOpen: false, savedAs: "", vault: null,
};

let toastTimer = 0;

// One message at a time, above every overlay; `undo` adds an Undo button.
function toast(msg, bad, undo) {
  const t = $("#toast");
  $("#toastText").textContent = msg;
  t.classList.toggle("bad", !!bad);
  state.undo = undo || null;
  $("#toastUndo").hidden = !undo;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; state.undo = null; }, 7000);
}

// ── draft autosave (this browser only) ──────────────────────────────────

const DRAFT_KEY = "rodeo-builder-draft-v1";
const DRAFT_FIELDS = ["engine", "base", "planEdited", "mode", "importProfile", "labAddons", "labJson", "chapters",
  "custom", "name", "title", "lang", "target", "variant", "extraVariants", "strings", "imported", "contentUrl", "contentFormat"];
let draftOn = true, draftTimer = 0;

function scheduleDraft() {
  if (!draftOn) return;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraft, 500);
}

function saveDraft() {
  const saved = {};
  for (const k of DRAFT_FIELDS) saved[k] = state[k];
  try { localStorage.setItem(DRAFT_KEY, JSON.stringify({ at: Date.now(), state: saved })); } catch (err) { /* no storage: no draft */ }
}

// A saved draft worth offering: it has chapters and its engine still exists.
function readDraft() {
  try {
    const d = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
    if (d && d.state && Array.isArray(d.state.chapters) && d.state.chapters.length &&
      state.engines.some((e) => e.name === d.state.engine)) return d;
  } catch (err) { /* unreadable draft: ignored */ }
  return null;
}

function offerDraft() {
  const d = readDraft();
  if (!d) return;
  draftOn = false;
  const at = new Date(d.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("#draftText").replaceChildren(el("strong", { text: "Restore your draft? " }),
    d.state.chapters.length + " chapters in \"" + d.state.name + "\", saved " + at + ".");
  $("#draftBanner").hidden = false;
  $("#draftRestore").onclick = () => {
    for (const k of DRAFT_FIELDS) if (k in d.state) state[k] = d.state[k];
    state.cursor = -1;
    draftDecided();
    toast("Draft restored");
  };
  $("#draftFresh").onclick = draftDecided;
}

function draftDecided() {
  $("#draftBanner").hidden = true;
  draftOn = true;
  render();
}

// ── derived ─────────────────────────────────────────────────────────────

function engine() { return state.engines.find((e) => e.name === state.engine); }
function importBase() { return state.liab.imports.find((i) => i.profile === state.importProfile); }
function isLiab() { return state.engine === "lab-in-a-box"; }
function liabImported() { return isLiab() && !!importBase(); }

// The lab-in-a-box lab of this rodeo: the one from the lab-builder or a lab.json, else the imported rodeo's.
function currentLab() { return state.labJson || (liabImported() ? importBase().lab : null); }

function provided() {
  if (!isLiab()) return new Set(engine() ? engine().provides : []);
  if (state.labJson) return new Set(RB.labJsonAddons(state.labJson));
  return new Set(liabImported() ? importBase().addons : state.labAddons);
}

function missing(chapter) { const p = provided(); return (chapter.needs || []).filter((n) => !isWorkNeed(n) && !p.has(n)); }

// A need no lab engine provides yet: missing_addon, or a tag no engine offers (only decided while the
// lab-in-a-box catalogue is loaded; without it an unknown need stays a need the lab lacks).
function isWorkNeed(n) {
  return n === RB.MISSING_ADDON || (!state.liab.error && !offeredNeeds().includes(n));
}

function workNeeds(chapter) { return (chapter.needs || []).filter(isWorkNeed); }

function workNeeded() { return state.chapters.filter((c) => workNeeds(c).length); }

function needChip(n, miss) {
  if (n === RB.MISSING_ADDON) return el("span", { class: "chip todo", title: "Placeholder: this chapter needs something no engine provides yet", text: "work needed" });
  if (isWorkNeed(n)) return el("span", { class: "chip todo", title: "No lab engine provides " + n + " yet: work needed", text: n });
  return el("span", { class: "chip " + (miss.includes(n) ? "missing" : "ok"), text: n,
    title: miss.includes(n) ? "The " + state.engine + " lab engine doesn't provide " + n : null });
}

function liabNames(kinds) {
  return (kinds || ["addons", "infrastructure", "kclusters"]).flatMap((k) => (state.liab[k] || []).map((a) => a.name));
}

// What a chapter can ask for: the native engines' capabilities plus lab-in-a-box's catalogue.
function offeredNeeds() { return [...[...new Set([...state.capabilities, ...liabNames()])].sort(), RB.MISSING_ADDON]; }

function catalogueHint() {
  return window.RB_STATIC ? "This page was built without it: rebuild with build-builder-static.py --labinabox <checkout>."
    : "Restart rodeo builder with --labinabox <checkout>, or check that this machine reaches GitHub.";
}

function libraryGroups() {
  const groups = [...state.workshops, ...state.imported].map((w) => ({ id: w.id, title: w.title, sub: w.source, chapters: w.chapters,
    engine: w.engine, profile: w.profile, plan: w.plan, variants: w.variants, imported: !!w.imported }));
  groups.push({ id: "custom", title: "My chapters", sub: "created in this builder", chapters: state.custom });
  return groups;
}

// The engine and base profile a library group's chapters run on.
function groupMatches(g) {
  if (!g.engine || g.engine !== state.engine) return false;
  if (g.engine === "lab-in-a-box") return liabImported() && importBase().profile === g.profile;
  return baseProfile() === g.profile;
}

// Switches the lab to the group's engine and profile and replaces the rodeo's chapters with the group's.
function useGroup(g) {
  if (!confirmPlanDrop()) return;
  const keys = ["engine", "base", "planEdited", "mode", "importProfile", "labJson", "chapters", "cursor"];
  const before = Object.fromEntries(keys.map((k) => [k, state[k]]));
  const used = new Set();
  state.chapters = g.chapters.map((ch) => {
    const id = RB.uniqueId(ch.id, used);
    used.add(id);
    return { ...ch, id, workshop: g.title, from: libKey(g.id, ch.id) };
  });
  state.cursor = -1;
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
  toast(g.title + ": lab engine " + g.engine + " with the " + g.profile + " profile, " + g.chapters.length +
    (g.chapters.length === 1 ? " chapter" : " chapters"), false, () => { Object.assign(state, before); render(); });
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

// The plan's content: (where the instructions live) from the Instructions repository field; null when
// the field is empty or not a repository URL (contentError() says why).
function contentSource() {
  if (!state.contentUrl.trim()) return null;
  try {
    const s = RB.parseSourceUrl(state.contentUrl);
    return { repo: "https://" + s.host + "/" + s.owner + "/" + s.repo, ref: s.ref, path: s.path, format: state.contentFormat };
  } catch (_) { return null; }
}

function contentError() {
  if (!state.contentUrl.trim()) return "";
  try { RB.parseSourceUrl(state.contentUrl); return ""; } catch (err) { return err.message; }
}

// Shown names of the instructions formats the server lists; one not named here shows its id.
const FORMAT_LABELS = { rmstory: "rmstory story/", instruqt: "Instruqt track" };

// The Deployment target and Instructions format choices: what the server lists (the registered
// deployment targets and content formats), plus the rodeo's own value when it is not among them.
function fillChoices() {
  const opts = (values, cur, label) => [...new Set([...values, cur].filter(Boolean))]
    .map((v) => el("option", { value: v, text: label(v) }));
  $("#target").replaceChildren(...opts(state.targets, state.target, (v) => v));
  $("#target").value = state.target;
  $("#contentFormat").replaceChildren(...opts(state.contentFormats, state.contentFormat, (v) => FORMAT_LABELS[v] || v));
  $("#contentFormat").value = state.contentFormat;
}

function planOptions() { return { name: state.name, target: state.target, story: story(), content: contentSource() }; }

// Plan text `text` with the lab from the lab-builder (or a lab.json) as its lab_in_a_box.lab.
function withLab(text) { return state.labJson ? RB.setStoredLab(text, state.labJson) : text; }

function generatedPlan() {
  if (liabImported()) return RB.planFromBase(withLab(importBase().plan), planOptions());
  if (isLiab()) return withLab(RB.planLabinabox({ ...planOptions(), addons: state.labAddons }));
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
    chapters: state.chapters, variants: allVariants(), story: story(), labJson: state.labJson, strings: state.strings,
    content: contentSource(), workNeeds: [...new Set(state.chapters.flatMap(workNeeds))],
  };
}

// ── rmstory on the live server ──────────────────────────────────────────

// The server's rmstory ({engines}) when this is the live builder and rmstory runs there.
function storyTools() { return !window.RB_STATIC && state.story && state.story.available ? state.story : null; }

function storyEngines(generate) {
  const tools = storyTools();
  return tools ? tools.engines.filter((e) => !generate || e.generate).map((e) => e.name) : [];
}

function chapterPayload() { return state.chapters.map((c) => ({ id: c.id, body: c.body })); }

function chaptersKey() { return state.chapters.map((c) => c.id + "\u0000" + c.body).join("\u0001"); }

// POSTs a rmstory action; resolves to the answer, or rejects with the server's error.
async function storyPost(action, payload) {
  const res = await apiPost(action, payload);
  if (res.status !== 200) throw new Error(res.body.error || "HTTP " + res.status);
  return res.body;
}

async function rmstoryCheck() {
  if (!state.chapters.length) { toast("Add at least one chapter first.", true); return; }
  state.busy = "validate";
  renderReview();
  try {
    state.rmCheck = { ...(await storyPost("validate", { chapters: chapterPayload() })), key: chaptersKey() };
  } catch (err) { toast("rmstory check failed: " + err.message, true); }
  state.busy = "";
  renderReview();
}

// ── library ─────────────────────────────────────────────────────────────

// Inserts at `at`, else after the row cursor (which then moves to the new row), else at the end.
function addChapter(group, ch, at) {
  if (inRodeo(group.id, ch.id)) return;
  const used = new Set(state.chapters.map((c) => c.id));
  const copy = { ...ch, id: RB.uniqueId(ch.id, used), workshop: group.title, from: libKey(group.id, ch.id) };
  if (at === undefined && state.cursor >= 0) { at = state.cursor + 1; state.cursor = at; }
  if (at === undefined || at < 0) state.chapters.push(copy); else state.chapters.splice(at, 0, copy);
  render();
}

function removeChapter(i) {
  const snapshot = state.chapters.slice(), title = state.chapters[i].title;
  state.chapters.splice(i, 1);
  state.cursor = -1;
  render();
  toast("Removed \"" + title + "\"", false, () => { state.chapters = snapshot; render(); });
}

// A library group's (i) panel: where its chapters come from, what they run on and need.
function groupInfo(g) {
  const mins = g.chapters.reduce((n, c) => n + (c.mins || 0), 0);
  const needs = [...new Set(g.chapters.flatMap((c) => c.needs || []))].sort();
  const variants = Object.keys(g.variants || {});
  const row = (label, ...value) => [el("dt", { text: label }), el("dd", {}, ...value)];
  const parts = String(g.sub || "").replace(/^https:\/\//, "").split("/").flatMap((s, i) => (i ? ["/", el("wbr"), s] : [s]));
  const source = /^https:\/\//.test(g.sub || "") ? el("a", { href: g.sub, target: "_blank", rel: "noopener" }, parts) : parts;
  return el("div", { class: "info-pop group-info" + (state.info === g.id ? " open" : ""), id: "info-" + g.id, role: "region", "aria-label": "About " + g.title },
    el("dl", { class: "info-card" },
      row(g.imported ? "Imported from" : "Source", source),
      g.engine ? row("Lab engine", g.engine + (groupMatches(g) ? " (this rodeo's)" : "")) : null,
      g.profile ? row("Base profile", g.profile) : null,
      row("Chapters", g.chapters.length + (g.chapters.length === 1 ? " chapter, " : " chapters, ") + mins + " min"),
      needs.length ? row("Needs", el("span", { class: "chips" }, needs.map((n) => needChip(n, missing({ needs }))))) : null,
      variants.length ? row("Story variants", variants.join(", ")) : null));
}

function toggleInfo(id) {
  state.info = state.info === id ? null : id;
  renderLibrary();
  renderEngines();
}

// A chapter card's tooltip: its time and needs, what this lab lacks for it, what a click does.
function cardTip(c, added) {
  const miss = missing(c), lines = [c.mins + " min" + (c.isNew ? ", new" : "")];
  if ((c.needs || []).length) lines.push("Needs " + c.needs.join(", "));
  if (miss.length) lines.push("The " + state.engine + " lab engine doesn't provide " + miss.join(", "));
  lines.push(added ? "In the rodeo: click to remove" : "Click to add to this rodeo");
  return lines.join("\n");
}

function shownChapters(g, q) {
  const f = state.filters;
  if (f.engine && g.engine && !groupMatches(g)) return [];
  return g.chapters.filter((c) => (!q || (c.title + " " + c.id).toLowerCase().includes(q))
    && !(f.added && inRodeo(g.id, c.id)) && !(f.runnable && missing(c).length));
}

function renderLibrary() {
  const box = $("#library");
  box.replaceChildren();
  const q = state.query.trim().toLowerCase();
  const narrowed = !!q || Object.values(state.filters).some(Boolean);
  let total = 0, shown = 0;
  for (const g of libraryGroups()) {
    const list = shownChapters(g, q);
    total += g.chapters.length;
    shown += list.length;
    if (narrowed && !list.length) continue;
    const open = q ? true : state.open[g.id] ?? !g.engine;
    const head = el("div", { class: "group-bar" },
      el("div", { class: "group-line" },
        el("button", { class: "group-head", type: "button", "aria-expanded": String(open),
          onclick: () => { state.open[g.id] = !open; renderLibrary(); } },
          el("span", { class: "chev", text: open ? "▾" : "▸" }),
          el("span", { class: "group-title", text: g.title })),
        el("button", { class: "info-btn", type: "button", text: "i", title: "About " + g.title, "aria-label": "About " + g.title,
          "aria-expanded": String(state.info === g.id), "aria-controls": "info-" + g.id, onclick: () => toggleInfo(g.id) })),
      groupInfo(g),
      el("div", { class: "group-actions" },
        g.engine ? el("button", { class: "btn sm outline", type: "button", text: "use",
          title: "Use this workshop: the " + g.engine + " lab engine with the " + g.profile + " profile, and its " +
            g.chapters.length + " chapters in place of the rodeo's", onclick: () => useGroup(g) }) : null,
        g.chapters.length ? el("button", { class: "btn sm", type: "button", text: "+ all " + g.chapters.length,
          onclick: () => { for (const c of g.chapters) addChapter(g, c); } }) : null,
        g.imported ? el("button", { class: "btn sm", type: "button", text: "×", "aria-label": "Remove " + g.title + " from the library",
          title: "Remove this imported workshop from the library", onclick: () => removeImported(g.id) }) : null));
    const cards = el("div", { class: "cards" });
    if (open) {
      if (!list.length) cards.append(el("div", { class: "empty", text: g.id === "custom" ? "No chapters yet: + New." : "No chapters." }));
      for (const c of list) {
        const added = inRodeo(g.id, c.id), lacks = missing(c).length > 0;
        cards.append(el("button", {
          class: "card" + (added ? " added" : "") + (lacks ? " lacks" : ""), type: "button", draggable: "true",
          title: cardTip(c, added),
          onclick: () => (added ? removeChapter(state.chapters.findIndex((x) => x.from === libKey(g.id, c.id))) : addChapter(g, c)),
          ondragstart: (ev) => { state.drag = { kind: "lib", group: g, chapter: c }; ev.dataTransfer.setData("text/plain", c.id); },
          ondragend: () => { state.drag = null; clearDrop(); },
        }, el("span", { class: "dot" }), el("span", { class: "card-title", text: c.title }),
        state.details ? el("span", { class: "card-meta", text: c.mins + " min" + (c.needs.length ? " · " + c.needs.join(", ") : "") +
          (c.isNew ? " · new" : "") + (added ? " · added ✓" : "") }) : null));
      }
    }
    box.append(el("div", { class: "group" }, head, cards));
  }
  if (narrowed && !shown) box.append(el("p", { class: "empty", text: "No chapter matches the search and filters." }));
  $("#libCount").textContent = String(total);
  const active = Object.values(state.filters).filter(Boolean).length;
  $("#filterCount").hidden = !active;
  $("#filterCount").textContent = String(active);
  $("#filterBtn").classList.toggle("outline", active > 0);
  $("#detailsBtn").setAttribute("aria-pressed", String(state.details));
}

function setFilterMenu(open) {
  $("#filterMenu").hidden = !open;
  $("#filterBtn").setAttribute("aria-expanded", String(open));
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

// A lab engine's (i) panel: what it builds, its size, base profile and what it provides.
function engineInfo(e) {
  const id = "engine:" + e.name, row = (label, ...value) => [el("dt", { text: label }), el("dd", {}, ...value)];
  const r = e.resources;
  return el("div", { class: "info-pop engine-info" + (state.info === id ? " open" : ""), id: "info-" + id, role: "region", "aria-label": "About " + e.name },
    el("dl", { class: "info-card" },
      row("Builds", e.note),
      row("Size", e.external ? "the lab you design" : r.nodes + " VMs, " + gib(r.memory_mib) + " RAM, " + r.vcpu + " vCPU"),
      e.base ? row("Base profile", e.base) : null,
      e.provides.length ? row("Provides", chipList(e.provides, "ok")) : null));
}

function renderEngines() {
  const box = $("#engines");
  box.replaceChildren(...state.engines.map((e) => {
    const on = e.name === state.engine, id = "engine:" + e.name;
    return el("div", { class: "engine-card" },
      el("button", {
        class: "engine" + (on ? " on" : ""), type: "button", role: "radio", "aria-checked": String(on), tabindex: on ? "0" : "-1",
        onclick: () => selectEngine(e.name), onkeydown: engineKey,
      }, el("span", { class: "engine-top" }, el("span", { class: "radio", "aria-hidden": "true" }),
        el("span", { class: "engine-name", text: e.name })),
      el("span", { class: "engine-title", text: e.title })),
      el("button", { class: "info-btn", type: "button", text: "i", title: "About " + e.name, "aria-label": "About " + e.name,
        "aria-expanded": String(state.info === id), "aria-controls": "info-" + id, onclick: () => toggleInfo(id) }),
      engineInfo(e));
  }));
  const e = engine();
  $("#engineNow").textContent = e ? "· " + e.name + (baseProfile() && baseProfile() !== e.name ? " · " + baseProfile() : "") : "";
  renderEnginePanel();
}

// Arrow keys move between the engine cards; Enter/Space selects (native button click).
function engineKey(ev) {
  const step = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[ev.key];
  if (!step) return;
  ev.preventDefault();
  const cards = [...document.querySelectorAll("#engines .engine")];
  const j = (cards.indexOf(ev.currentTarget) + step + cards.length) % cards.length;
  cards.forEach((c, k) => c.setAttribute("tabindex", k === j ? "0" : "-1"));
  cards[j].focus();
}

function chipList(names, cls) { return el("div", { class: "chips" }, names.map((n) => el("span", { class: "chip " + (cls || ""), text: n }))); }

function neededAddons() {
  const need = new Set();
  for (const c of state.chapters) for (const n of missing(c)) need.add(n);
  return [...need].sort();
}

function addonTargets(name) {
  const a = state.liab.addons.find((x) => x.name === name);
  return a ? a.targets : [];
}

function addAddon(name) {
  name = name.trim().toLowerCase().replace(/^install_/, "");
  if (!name || state.labAddons.includes(name)) return;
  if (state.liab.error) { toast("The lab-in-a-box catalogue is unavailable, so add-ons cannot be checked. " + catalogueHint(), true); return; }
  if (!liabNames(["addons"]).includes(name)) { toast(name + " is not a lab-in-a-box add-on" + (state.liab.version ? " in " + state.liab.version : ""), true); return; }
  state.labAddons.push(name);
  render();
}

// Longest lab-builder link that carries this rodeo's lab (GitHub Pages answers 414 above ~8 KB).
const LAB_URL_MAX = 8000;

// A "{host}" in the lab-builder URL (rodeo builder's own lab-builder) is the host this page came from.
function labBuilderBase() { return new URL(String(state.liab.builder_url).replace("{host}", location.hostname), location.href); }

function base64url(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// The lab-builder link: with this rodeo's lab (?lab=) when it fits in the link, else its add-ons
// (?addons=); `extra` adds query parameters. The embedded lab-builder gets the lab by postMessage.
function labBuilderUrl(extra, withLabParam = true) {
  const url = labBuilderBase(), lab = currentLab();
  for (const [k, v] of Object.entries(extra || {})) url.searchParams.set(k, v);
  if (lab && withLabParam) {
    const linked = new URL(url);
    linked.searchParams.set("lab", base64url(JSON.stringify(lab)));
    if (linked.toString().length <= LAB_URL_MAX) return linked.toString();
  }
  if (lab && !withLabParam) return url.toString();
  const addons = lab ? RB.labJsonAddons(lab) : state.labAddons;
  if (addons.length) url.searchParams.set("addons", addons.join(","));
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

// A lab from the lab-builder or a lab.json becomes the plan's lab_in_a_box.lab; an imported rodeo stays.
function useLab(lab, source) {
  state.labJson = lab;
  state.labAddons = RB.labJsonAddons(lab);
  if (state.planEdited !== null) state.planEdited = RB.setStoredLab(state.planEdited, lab);
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
    box.append(el("p", { text: "Design the lab in lab-in-a-box's lab-builder (this rodeo's lab, or its add-ons when the lab is too large for a link, are prefilled), download its lab.json, then upload it here to bring the lab back." }),
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
  if (state.labJson) box.append(el("p", { class: "mono muted", text: "Lab from the lab-builder: saved in rodeo-plan.yaml as lab_in_a_box.lab." }));
  if (state.liab.error) {
    box.append(el("div", { class: "alert", role: "alert" }, el("strong", { text: "lab-in-a-box catalogue unavailable. " }),
      state.liab.error + ". " + catalogueHint()));
  }
  box.append(el("span", { class: "label", text: "Lab add-ons" }));
  if (liabImported() || state.labJson) {
    box.append(chipList([...provided()], "ok"),
      el("p", { class: "muted", text: "Add-ons of this lab are edited in the lab-builder (Embed here or Open in Lab Builder)." }));
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
  if (need.length && !liabImported() && !state.labJson) {
    const addons = liabNames(["addons"]), inBuilder = liabNames(["infrastructure", "kclusters"]);
    box.append(el("span", { class: "label", text: "Needed by chapters" }),
      el("div", { class: "chips" }, need.map((n) => addons.includes(n)
        ? el("button", { class: "chip need", type: "button", text: "+ " + n, onclick: () => addAddon(n) })
        : el("span", { class: "chip missing", title: inBuilder.includes(n) ? "Set it up in the lab-builder, then upload the lab.json"
          : "No engine provides it yet", text: n + (inBuilder.includes(n) ? " · lab-builder" : " · not available") }))));
  }
  const more = [["Infrastructure", state.liab.infrastructure], ["Kubernetes clusters", state.liab.kclusters]].filter(([, l]) => l && l.length);
  for (const [label, list] of more) box.append(el("span", { class: "label", text: label + " (in the lab-builder)" }), chipList(list.map((a) => a.name)));
}

// ── embedded lab-builder ────────────────────────────────────────────────

function closeEmbed() {
  $("#embedOverlay").hidden = true;
  $("#embedFrame").onload = null;
  $("#embedFrame").src = "about:blank";
}

// Opens the lab-builder in the overlay and, once it has loaded, hands it this rodeo's lab.
function openEmbed() {
  const frame = $("#embedFrame"), lab = currentLab();
  frame.onload = lab ? () => frame.contentWindow.postMessage({ type: "labinabox:load", lab }, labBuilderBase().origin) : null;
  frame.src = labBuilderUrl({ embed: "1", origin: location.origin }, false);
  $("#embedNote").textContent = "Use this lab in the lab-builder hands the lab back here.";
  $("#embedOverlay").hidden = false;
}

window.addEventListener("message", (ev) => {
  if (!state.liab.builder_url) return;
  const origin = labBuilderBase().origin;
  if (ev.origin !== origin || !ev.data || ev.data.type !== "labinabox:lab") return;
  useLab(ev.data.lab, "the lab-builder");
  closeEmbed();
});

// ── chapter list ────────────────────────────────────────────────────────

function clearDrop() {
  document.querySelectorAll(".drop-before").forEach((n) => n.classList.remove("drop-before"));
  document.querySelectorAll(".dropzone.over, .lab.over").forEach((n) => n.classList.remove("over"));
}

function dropAt(index) {
  const d = state.drag;
  state.drag = null;
  state.cursor = -1;
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
  if (state.cursor >= state.chapters.length) state.cursor = -1;
  const warned = RB.warningsByChapter(RB.validate(state.chapters));
  state.chapters.forEach((c, i) => {
    const miss = missing(c), spans = RB.parseSpans(c.body).spans.length, w = warned[c.id] || 0;
    const row = el("div", {
      class: "row" + (state.cursor === i ? " cursor" : ""), tabindex: "0", draggable: "true",
      "aria-label": "Chapter " + (i + 1) + ": " + c.title + ". Alt+Up/Down to move.",
      onclick: (ev) => { if (ev.target.closest("button")) return; state.cursor = state.cursor === i ? -1 : i; renderChapters(); },
      ondragstart: (ev) => { state.drag = { kind: "row", index: i }; ev.dataTransfer.setData("text/plain", c.id); },
      ondragend: () => { state.drag = null; clearDrop(); },
      ondragover: (ev) => { ev.preventDefault(); clearDrop(); row.classList.add("drop-before"); },
      ondrop: (ev) => { ev.preventDefault(); ev.stopPropagation(); dropAt(i); },
      onkeydown: (ev) => { if (ev.altKey && ev.key === "ArrowUp") { ev.preventDefault(); move(i, -1); }
        if (ev.altKey && ev.key === "ArrowDown") { ev.preventDefault(); move(i, 1); } },
    },
    el("span", { class: "grip", "aria-hidden": "true", text: "⠿" }),
    el("span", { class: "row-num", text: String(i + 1).padStart(2, "0") }),
    el("div", {}, el("div", { class: "row-title", text: c.title }),
      el("div", { class: "row-meta", text: [c.workshop, c.mins + " min", c.check ? "checks/check-" + c.id + ".sh" : ""].filter(Boolean).join(" · ") }),
      el("div", { class: "chips" }, (c.needs || []).map((n) => needChip(n, miss)))),
    el("div", { class: "row-actions" },
      el("button", { class: "btn sm" + (w ? " warn" : ""), type: "button", text: "Edit" + (w ? " · " + w + " ⚠" : ""),
        title: "Edit this chapter's story: " + spans + (spans === 1 ? " span" : " spans") + (w ? ", " + w + (w === 1 ? " warning" : " warnings") : ""),
        onclick: () => openEditor(i) }),
      el("button", { class: "btn sm", type: "button", "aria-label": "Move up", text: "↑", disabled: i === 0, onclick: () => move(i, -1) }),
      el("button", { class: "btn sm", type: "button", "aria-label": "Move down", text: "↓", disabled: i === state.chapters.length - 1, onclick: () => move(i, 1) }),
      el("button", { class: "btn sm", type: "button", "aria-label": "Remove " + c.title, text: "×",
        onclick: () => removeChapter(i) })));
    box.append(row);
  });
  const zone = el("div", { class: "dropzone", text: state.chapters.length ? "Drop here to append" : "Drag chapters here, or click them in the library",
    ondragover: (ev) => { ev.preventDefault(); clearDrop(); zone.classList.add("over"); },
    ondragleave: () => zone.classList.remove("over"),
    ondrop: (ev) => { ev.preventDefault(); ev.stopPropagation(); dropAt(state.chapters.length); } });
  box.append(zone);
  $("#insertHint").textContent = state.cursor >= 0
    ? "Inserting after " + String(state.cursor + 1).padStart(2, "0") + ". Click that row again to append at the end."
    : "Drag from the library or click a chapter. Click a row to choose where new ones go.";
  const need = neededAddons(), cov = $("#coverage"), todo = workNeeded().length;
  cov.textContent = !state.chapters.length ? "" : (need.length ? "lab missing: " + need.join(", ") : "lab covers every chapter") +
    (todo ? " · work needed in " + todo + (todo === 1 ? " chapter" : " chapters") : "");
  cov.classList.toggle("bad", need.length > 0);
}

// Shown when the chapters come from workshops that all run on one other engine.
// Several engines: no banner (multi-engine rodeos are planned).
function renderEngineBanner() {
  const box = $("#engineBanner"), needs = RB.engineNeeds(state.chapters, libraryGroups());
  const show = needs.length === 1 && needs[0].engine !== state.engine;
  box.hidden = !show;
  if (!show) { box.replaceChildren(); return; }
  const { engine: name, workshops } = needs[0];
  const group = libraryGroups().find((g) => g.engine === name && workshops.includes(g.title));
  box.replaceChildren(el("span", { class: "banner-text", text: "These chapters come from " + workshops.join(", ") +
    " and run on " + name + ", not " + state.engine + "." }),
  el("button", { class: "btn primary sm", type: "button", text: "Switch to " + name, onclick: () => useGroup(group) }));
}

function scrollToReview() {
  const review = $("#review"), center = $(".center");
  const box = center.scrollHeight > center.clientHeight ? center : $(".cols");
  box.scrollTop += review.getBoundingClientRect().top - box.getBoundingClientRect().top - 12;
}

function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

function renderWarnings(count) {
  for (const id of ["#warnBtn", "#stickyWarn"]) {
    const b = $(id);
    b.hidden = count === 0;
    b.textContent = count + " ⚠ " + (count === 1 ? "warning" : "warnings");
  }
}

function renderReview() {
  const box = $("#reviewPanel"), name = RB.slugify(state.name) || "my-rodeo", n = state.chapters.length;
  const warnings = RB.validate(state.chapters), need = neededAddons();
  const mins = state.chapters.reduce((t, c) => t + (c.mins || 0), 0);
  renderWarnings(warnings.length);
  const check = (ok, text) => el("div", { class: "check " + (ok ? "ok" : "bad") },
    el("span", { class: "check-icon", "aria-hidden": "true", text: ok ? "✓" : "⚠" }), el("span", { text }));
  const files = RB.files(rodeo()).map((f) => f.path);
  const cmd = "rodeo new " + name + " --from-zip " + name + ".zip\nrodeo up --profile " + name;
  const copy = el("button", { class: "btn sm", type: "button", text: state.copied ? "Copied ✓" : "Copy",
    onclick: () => {
      Promise.resolve().then(() => navigator.clipboard.writeText(cmd)).catch(() => toast("Copy failed: select the commands instead", true));
      state.copied = true;
      renderReview();
      setTimeout(() => { state.copied = false; renderReview(); }, 1800);
    } });
  fill(box,
    el("div", { class: "checks-list" },
      check(n > 0, plural(n, "chapter") + " · " + mins + " min · engine " + state.engine),
      check(!need.length, need.length ? "The lab is missing " + need.join(", ") + " (does not block the download)"
        : "The lab provides everything the chapters need"),
      check(!warnings.length, warnings.length ? plural(warnings.length, "span warning") + " (same checks as rmstory validate)"
        : "All span ids valid")),
    warnings.map((w) => {
      const i = state.chapters.findIndex((c) => c.id === w.chapter);
      return el("div", { class: "alert warn-row" }, el("strong", { text: i >= 0 ? state.chapters[i].title : w.chapter }),
        el("span", { class: "warn-msg", text: (w.id ? w.id + ": " : "") + w.msg }),
        i >= 0 ? el("button", { class: "btn sm warn", type: "button", text: "Fix ✎", onclick: () => openEditor(i) }) : null);
    }),
    renderRmstoryCheck(),
    el("button", { class: "btn sm zip-toggle", type: "button", id: "zipFilesBtn", "aria-expanded": String(!!state.zipOpen),
      "aria-controls": "zipFiles", text: (state.zipOpen ? "▾ " : "▸ ") + plural(files.length, "file") + " in " + name + ".zip",
      onclick: () => { state.zipOpen = !state.zipOpen; renderReview(); } }),
    el("ul", { class: "zip-files", id: "zipFiles", hidden: state.zipOpen ? null : true }, files.map((f) => el("li", { class: RB.partOf(f) === "instructions" ? "story" : "",
      title: RB.partOf(f), text: name + "/" + f }))),
    el("div", { class: "term install" }, el("div", { class: "term-bar" }, el("i"), el("i"), el("i"), el("span", { text: "install" }), copy),
      el("pre", { text: cmd })),
    el("div", { class: "review-foot" },
      el("button", { class: "btn primary" + (n ? "" : " dim"), type: "button", text: "Download " + name + ".zip", onclick: () => download() }),
      el("button", { class: "btn outline sm", type: "button", id: "dlInfra", text: "Infrastructure only",
        title: "The lab: plan, definition, lab-in-a-box lab (" + name + "-infrastructure.zip)", onclick: () => download("infrastructure") }),
      el("button", { class: "btn outline sm" + (n ? "" : " dim"), type: "button", id: "dlInstructions", text: "Instructions only",
        title: "The chapters and checks, laid out as a content repository (" + name + "-instructions.zip)", onclick: () => download("instructions") }),
      state.server && state.server.save ? el("span", { class: "muted", text: "Save to profiles (top right) writes it straight into your profiles." }) : null));
}

// The live builder's "Validate": rmstory's own validate on the chapters.
function renderRmstoryCheck() {
  if (!storyTools()) return null;
  const res = state.rmCheck, stale = res && res.key !== chaptersKey();
  const head = el("div", { class: "rm-check" },
    el("button", { class: "btn sm", type: "button", id: "rmCheckBtn", disabled: state.busy ? true : null,
      text: state.busy === "validate" ? "Validating…" : "Validate", onclick: rmstoryCheck }),
    el("span", { class: "muted", text: res ? res.summary + (stale ? " · the chapters changed since: check again" : "")
      : "Runs rmstory validate on the builder's host" }));
  if (!res || stale) return head;
  return [head, res.warnings.map((w) => {
    const i = state.chapters.findIndex((c) => c.id === w.chapter);
    return el("div", { class: "alert warn-row" }, el("strong", { text: (i >= 0 ? state.chapters[i].title : w.chapter) + " · line " + w.line }),
      el("span", { class: "warn-msg", text: "rmstory " + (w.error ? "error" : "warning") + ": " + w.message }),
      i >= 0 ? el("button", { class: "btn sm warn", type: "button", text: "Fix ✎", onclick: () => openEditor(i) }) : null);
  })];
}

// ── right panel ─────────────────────────────────────────────────────────

function renderNewStory() {
  const engines = storyEngines(true), select = $("#nsEngine"), picked = select.value;
  $("#newStory").hidden = !engines.length;
  if (select.options.length !== engines.length) select.replaceChildren(...engines.map((e) => el("option", { value: e, text: e })));
  if (engines.includes(picked)) select.value = picked;
  const parts = RB.storyParts(state.chapters), btn = $("#nsBtn");
  $("#nsParts").textContent = parts.story
    ? "Retells " + plural(parts.story, "story span") + "; keeps " + plural(parts.exercise, "exercise span") + " and "
      + plural(parts.fixed, "fixed span") + " word for word."
    : "Nothing to retell yet: in the story editor, give the story's spans a story variant.";
  btn.disabled = !!state.busy || !parts.story;
  btn.textContent = state.busy === "retell" ? "Retelling…" : "Retell";
}

// "New story": rmstory retells the rodeo's story spans about a theme (exercise spans never
// reach the engine, see RB.maskExercises); the retold chapters become a library group and
// replace the rodeo's chapters (Undo puts the originals back).
async function retellStory() {
  const theme = $("#nsTheme").value.trim(), engineName = $("#nsEngine").value;
  if (!theme) { toast("Say what the new story is about", true); $("#nsTheme").focus(); return; }
  if (!state.chapters.length) { toast("Add at least one chapter first.", true); return; }
  const used = new Set(libraryGroups().map((g) => g.id));
  const slug = RB.slugify(theme).slice(0, 20).replace(/-+$/, "") || "story", id = RB.uniqueId("story-" + slug, used);
  const tagged = state.chapters.map((c) => ({ ...c, body: RB.nameMissing(c.body, c.id) }));
  const masked = Object.fromEntries(tagged.map((c) => [c.id, RB.maskExercises(c.body)]));
  state.busy = "retell";
  render();
  try {
    const res = await storyPost("retell", { chapters: tagged.map((c) => ({ id: c.id, body: masked[c.id].body })), theme, engine: engineName });
    const bodies = Object.fromEntries(res.chapters.map((c) => [c.id, c.body]));
    const kept = Object.fromEntries(tagged.map((c) => [c.id, masked[c.id].kept]));
    const retold = RB.retoldChapters(tagged, bodies, id.slice(6), kept);
    const group = { id, title: theme, source: "retold from " + (state.title || "this rodeo") + " with " + engineName, format: "rmstory",
      engine: "", profile: "", plan: "", variants: {}, imported: true,
      chapters: retold.map(({ workshop, from, ...ch }) => ch) };
    const before = { chapters: state.chapters, imported: state.imported };
    state.imported = [...state.imported, group];
    state.chapters = group.chapters.map((ch) => ({ ...ch, workshop: group.title, from: libKey(group.id, ch.id) }));
    state.cursor = -1;
    state.busy = "";
    render();
    toast("New story: " + plural(group.chapters.length, "chapter") + " retold. Review them before you download", false,
      () => { state.chapters = before.chapters; state.imported = before.imported; render(); });
  } catch (err) {
    state.busy = "";
    render();
    toast("Retelling failed: " + err.message, true);
  }
}

function renderSettings() {
  $("#name").value = state.name;
  $("#title").value = state.title;
  $("#lang").value = state.lang;
  fillChoices();
  const box = $("#variants");
  const options = [["", "all spans"], ...allVariants().map((v) => [v, v])];
  if (state.variant && !allVariants().includes(state.variant)) state.variant = "";
  box.replaceChildren(...options.map(([v, label]) => el("button", {
    type: "button", class: "chip" + (state.variant === v ? " on" : ""), "aria-pressed": String(state.variant === v),
    text: label, onclick: () => { state.variant = v; render(); } })));
  renderNewStory();
  $("#contentUrl").value = state.contentUrl;
  const c = contentSource(), bad = contentError();
  $("#contentNote").textContent = bad ? bad : c ? "The plan gets content: " + c.repo + (c.ref ? " @ " + c.ref : "") + (c.path ? " /" + c.path : "")
    + ". rodeo up renders it; rodeo content apply updates it on a running lab."
    : "Optional. Empty: the instructions are this rodeo's own story/ (the chapters above).";
  $("#contentNote").classList.toggle("bad-text", !!bad);
  const mins = state.chapters.reduce((n, c) => n + (c.mins || 0), 0);
  $("#estimate").textContent = "Estimated run time: " + mins + " min (" + state.chapters.length + " chapters)";
  $("#counts").textContent = state.chapters.length + " chapters · " + mins + " min · " + state.engine;
  $("#stickyCounts").textContent = state.chapters.length + " chapters · " + mins + " min";
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
  renderDeploy();
  renderLibrary();
  renderEngines();
  renderChapters();
  renderEngineBanner();
  renderSettings();
  renderFiles();
  renderReview();
  scheduleDraft();
}

// ── import a workshop by URL ────────────────────────────────────────────
// The live builder fetches any public https git repository (rodeo/builder/imports.py);
// the published page reads public github.com repositories through GitHub's API.

function openImport() {
  $("#imUrl").value = "";
  $("#imNote").textContent = window.RB_STATIC
    ? "A public github.com repository: a rodeo story/ directory, an Instruqt track (<challenge>/assignment.md) or markdown files (*.md). GitHub allows 60 requests an hour without a login; rodeo builder imports from any git host."
    : "A public https git repository: a rodeo story/ directory, an Instruqt track (<challenge>/assignment.md) or markdown files (*.md).";
  $("#importModal").hidden = false;
  $("#imUrl").focus();
}

function closeImport() {
  $("#importModal").hidden = true;
  $("#importBtn").focus();
}

async function ghFetch(url, json) {
  const res = await fetch(url, json ? { headers: { Accept: "application/vnd.github+json" } } : {});
  if (res.status === 403 || res.status === 429) throw new Error("GitHub refused: without a login it allows 60 requests an hour. Try later, or import with rodeo builder");
  if (res.status === 404) throw new Error("not found on GitHub (only public repositories can be imported)");
  if (!res.ok) throw new Error("GitHub answered " + res.status);
  return json ? res.json() : res.text();
}

// The published page's import: one tree listing, then the raw chapter files.
async function importFromGitHub(url) {
  const src = RB.parseSourceUrl(url);
  if (src.host !== "github.com") throw new Error("the published builder imports from github.com only; other git hosts need rodeo builder");
  const api = "https://api.github.com/repos/" + src.owner + "/" + src.repo;
  const ref = src.ref || (await ghFetch(api, true)).default_branch;
  const tree = await ghFetch(api + "/git/trees/" + encodeURIComponent(ref) + "?recursive=1", true);
  const files = tree.tree.filter((t) => t.type === "blob").map((t) => t.path);
  const { format, dir } = RB.detectFormat(files, src.path);
  const pre = dir ? dir + "/" : "";
  const raw = (path) => ghFetch("https://raw.githubusercontent.com/" + src.owner + "/" + src.repo + "/" +
    encodeURIComponent(ref) + "/" + path.split("/").map(encodeURIComponent).join("/"), false);
  const direct = files.filter((f) => f.startsWith(pre) && !f.slice(pre.length).includes("/")).sort();
  let title = src.path ? src.path.split("/").pop() : src.repo, chapters = [];
  const variants = {};
  if (format === "rmstory") {
    const meta = direct.includes(pre + "chapters.yaml") ? RB.chaptersMeta(await raw(pre + "chapters.yaml")) : null;
    chapters = await Promise.all(direct.filter((f) => f.endsWith(".md")).map(async (f) => RB.chapterFromStory(f.slice(pre.length), await raw(f), meta)));
    for (const f of files.filter((f) => /^stories\/[^/]+\.yaml$/.test(f.slice(pre.length)) && f.startsWith(pre)).sort()) {
      variants[f.slice(pre.length + 8, -5)] = RB.storyIds(await raw(f));
    }
    if (meta && meta.title) title = meta.title;
  } else if (format === "instruqt") {
    const dirs = [...new Set(files.filter((f) => f.startsWith(pre) && /^[^/]+\/assignment\.md$/.test(f.slice(pre.length)))
      .map((f) => f.slice(pre.length).split("/")[0]))].sort();
    chapters = await Promise.all(dirs.map(async (d) => {
      const check = files.filter((f) => f.startsWith(pre + d + "/check-")).sort()[0];
      return RB.chapterFromInstruqt(d, await raw(pre + d + "/assignment.md"), check ? await raw(check) : "");
    }));
  } else {
    chapters = await Promise.all(direct.filter((f) => f.endsWith(".md")).map(async (f) => RB.chapterFromMarkdown(f.slice(pre.length), await raw(f))));
  }
  if (chapters.length > 200) throw new Error("too many chapters (" + chapters.length + " > 200)");
  return { id: RB.importGroupId(src), title, source: "https://github.com/" + src.owner + "/" + src.repo + (src.path ? "/tree/" + ref + "/" + src.path : ""),
    format, engine: "", profile: "", plan: "", chapters, variants, imported: true };
}

async function importWorkshop(ev) {
  ev.preventDefault();
  const url = $("#imUrl").value.trim(), btn = $("#imSubmit");
  if (!url) return;
  btn.disabled = true;
  btn.textContent = "Importing…";
  try {
    let group;
    if (window.RB_STATIC) group = await importFromGitHub(url);
    else {
      const res = await apiPost("import", { url });
      if (res.status !== 200) throw new Error(res.body.error || "HTTP " + res.status);
      group = res.body.workshop;
    }
    state.imported = [...state.imported.filter((g) => g.id !== group.id), group];
    state.open[group.id] = true;
    closeImport();
    render();
    toast("Imported " + plural(group.chapters.length, "chapter") + " (" + group.format + ") from " + group.title);
  } catch (err) { toast("Import failed: " + err.message, true); }
  btn.disabled = false;
  btn.textContent = "Import";
}

function removeImported(id) {
  const before = state.imported;
  state.imported = state.imported.filter((g) => g.id !== id);
  render();
  toast("Imported workshop removed from the library", false, () => { state.imported = before; render(); });
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
  $("#ncNeedInput").value = "";
  $("#ncTitle").value = "";
  $("#ncMins").value = "10";
  $("#ncCheck").checked = false;
  $("#ncAppend").checked = true;
  $("#ncBody").value = chapterTemplate("new-chapter");
  const engines = storyEngines(true);
  $("#ncDraft").hidden = !engines.length;
  $("#ncEngine").replaceChildren(...engines.map((e) => el("option", { value: e, text: e })));
  renderNewChapterChips();
  $("#newModal").hidden = false;
  $("#ncTitle").focus();
}

function renderNewChapterChips() {
  $("#ncWorkshops").replaceChildren(...libraryGroups().map((g) => el("button", {
    type: "button", class: "chip" + (state.newWorkshop === g.id ? " on" : ""), text: g.title, "aria-pressed": String(state.newWorkshop === g.id),
    onclick: () => { state.newWorkshop = g.id; renderNewChapterChips(); } })));
  $("#ncNeeds").replaceChildren(...[...ncNeeds].map((n) => el("span", {
    class: "chip " + (isWorkNeed(n) ? "todo" : "on"), title: isWorkNeed(n) ? "No lab engine provides " + n + " yet: work needed" : null },
  n, el("button", { class: "x", type: "button", text: "×", "aria-label": "Remove " + n,
    onclick: () => { ncNeeds.delete(n); renderNewChapterChips(); $("#ncNeedInput").focus(); } }))));
  renderNeedOptions();
}

// The needs field: typing lists the offered needs that match; Enter or a click adds the highlighted one,
// or the typed text as a new need (work needed) when nothing offered matches it.
const needPick = { open: false, active: 0 };

function needOptions() {
  const q = $("#ncNeedInput").value.trim().toLowerCase();
  const slug = q.replace(/[^a-z0-9_-]+/g, "_").replace(/^[_-]+|[_-]+$/g, "").slice(0, 48);
  const free = offeredNeeds().filter((n) => !ncNeeds.has(n) && n.toLowerCase().includes(q));
  const opts = [...free.filter((n) => n.toLowerCase().startsWith(q)), ...free.filter((n) => !n.toLowerCase().startsWith(q))]
    .map((n) => ({ need: n, text: n }));
  if (slug && !offeredNeeds().includes(slug) && !ncNeeds.has(slug)) opts.push({ need: slug, text: "Add \u201c" + slug + "\u201d as a new need", fresh: true });
  return opts;
}

function renderNeedOptions() {
  const list = $("#ncNeedList"), input = $("#ncNeedInput"), opts = needPick.open ? needOptions() : [];
  needPick.active = Math.min(needPick.active, Math.max(0, opts.length - 1));
  list.replaceChildren(...opts.map((o, i) => el("li", {
    id: "ncNeedOpt" + i, role: "option", class: (o.fresh ? "fresh" : "") + (i === needPick.active ? " on" : ""),
    "aria-selected": String(i === needPick.active), text: o.text,
    onmousedown: (ev) => { ev.preventDefault(); addNeed(o.need); } })));
  list.hidden = !opts.length;
  input.setAttribute("aria-expanded", String(opts.length > 0));
  if (opts.length) input.setAttribute("aria-activedescendant", "ncNeedOpt" + needPick.active);
  else input.removeAttribute("aria-activedescendant");
  const on = list.querySelector(".on");
  if (on) on.scrollIntoView({ block: "nearest" });
}

function addNeed(n) {
  if (!n) return;
  ncNeeds.add(n);
  $("#ncNeedInput").value = "";
  needPick.active = 0;
  renderNewChapterChips();
}

function needKey(ev) {
  const opts = needOptions();
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault();
    if (!needPick.open) needPick.open = true;
    else if (opts.length) needPick.active = (needPick.active + (ev.key === "ArrowDown" ? 1 : -1) + opts.length) % opts.length;
  } else if (ev.key === "Enter" || ev.key === ",") {
    ev.preventDefault();
    if (needPick.open && opts.length) addNeed(opts[needPick.active].need);
    return;
  } else if (ev.key === "Escape" && needPick.open) {
    ev.preventDefault();
    ev.stopPropagation();
    needPick.open = false;
  } else if (ev.key === "Backspace" && !ev.target.value && ncNeeds.size) {
    ncNeeds.delete([...ncNeeds].at(-1));
    renderNewChapterChips();
    return;
  } else return;
  renderNeedOptions();
}

// Drafts the chapter body with a generating rmstory engine, from the brief and the rodeo.
async function draftChapter() {
  const brief = $("#ncBrief").value.trim();
  if (!brief) { toast("Describe the chapter first", true); $("#ncBrief").focus(); return; }
  const title = $("#ncTitle").value.trim() || "Chapter title", btn = $("#ncDraftBtn");
  const prompt = RB.draftPrompt(brief, { title, rodeo: state.title, provides: [...provided()].sort(),
    chapters: state.chapters.map((c) => c.title), mins: parseInt($("#ncMins").value, 10) || 0 });
  btn.disabled = true;
  btn.textContent = "Drafting…";
  try {
    const res = await storyPost("draft", { prompt, lang: RB.SOURCE_LANGUAGE, engine: $("#ncEngine").value });
    $("#ncBody").value = "# " + title + "\n\n" + RB.renumber(res.body, "new-chapter");
    toast("Draft ready: review it before you create the chapter");
  } catch (err) { toast("Draft failed: " + err.message, true); }
  btn.disabled = false;
  btn.textContent = "Draft";
}

function closeNewChapter() {
  $("#newModal").hidden = true;
  $("#newChapterBtn").focus();
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
  else [...state.workshops, ...state.imported].find((w) => w.id === group.id).chapters.push(ch);
  closeNewChapter();
  if ($("#ncAppend").checked) addChapter(group, ch); else render();
  toast("Chapter \"" + title + "\" created as " + id);
}

// ── story editor ────────────────────────────────────────────────────────

let pop = null;

function editing() { return state.chapters[state.editing]; }

// What Cancel restores: every chapter's text (translating can rename span ids in any
// chapter), the stored translations and the story variants, as they were when the editor opened.
let edSnapshot = null;
// Ctrl+Z / Ctrl+Shift+Z (or Ctrl+Y) history of the open chapter's text. Typing is grouped by pauses.
const edHistory = { past: [], future: [], typedAt: 0 };
const HISTORY_MAX = 200;

function remember(before) {
  edHistory.past.push(before);
  if (edHistory.past.length > HISTORY_MAX) edHistory.past.shift();
  edHistory.future = [];
}

function stepHistory(from, to) {
  if (!from.length) return;
  to.push(editing().body);
  showBody(from.pop());
  edHistory.typedAt = 0;
}

function editorChanged() {
  return state.chapters.some((c, i) => c.body !== edSnapshot.bodies[i]) || JSON.stringify(state.strings) !== edSnapshot.strings
    || state.extraVariants.join() !== edSnapshot.variants.join();
}

function cancelEditor() {
  if (editorChanged() && !window.confirm("Discard the changes made in the story editor?")) return;
  state.chapters.forEach((c, i) => { c.body = edSnapshot.bodies[i]; });
  state.strings = JSON.parse(edSnapshot.strings);
  state.extraVariants = edSnapshot.variants;
  closeEditor();
  scheduleDraft();
}

function openEditor(i) {
  state.editing = i;
  edSnapshot = { bodies: state.chapters.map((c) => c.body), strings: JSON.stringify(state.strings), variants: state.extraVariants.slice() };
  edHistory.past = [];
  edHistory.future = [];
  edHistory.typedAt = 0;
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
  if (body !== editing().body) remember(editing().body);
  edHistory.typedAt = 0;
  showBody(body, keepSel);
}

function showBody(body, keepSel) {
  const ta = $("#edSource"), s = ta.selectionStart, e = ta.selectionEnd;
  editing().body = body;
  ta.value = body;
  if (keepSel) ta.setSelectionRange(s, e);
  renderEditor();
  scheduleDraft();
}

function removeSpan(sp) {
  const ch = editing(), before = ch.body;
  closePop();
  setBody(RB.unwrap(before, sp));
  toast("Span removed", false, () => { ch.body = before; if (editing() === ch) setBody(before); else render(); });
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
    type: "button", class: "chip", text: v, title: "Toggle hist=\"" + v + "\" on the span at the cursor", "aria-pressed": "false",
    dataset: { hist: v }, onclick: () => toggleHist(v) })));
  updateCaret();
  renderPreview(body, spans);
  renderSpanList(body, spans, warnings);
  renderStories();
  renderTranslations();
  document.querySelectorAll("[data-edtab]").forEach((b) => {
    b.classList.toggle("on", b.dataset.edtab === state.edTab);
    b.setAttribute("aria-selected", String(b.dataset.edtab === state.edTab));
  });
  for (const t of ["spans", "stories", "translations"]) $("#edtab-" + t).hidden = state.edTab !== t;
}

// Selection info and the variant chips' pressed state follow the caret.
function updateCaret() {
  const ta = $("#edSource"), s = ta.selectionStart, e = ta.selectionEnd, sp = state.editing >= 0 ? spanAtCursor() : null;
  $("#edSel").textContent = (e > s ? (e - s) + " chars selected" : "caret at " + s) + " · Alt+T / I / N tags";
  document.querySelectorAll("#edHist .chip").forEach((c) => {
    const on = !!sp && sp.attrs.hist === c.dataset.hist;
    c.classList.toggle("on", on);
    c.setAttribute("aria-pressed", String(on));
  });
}

// The preview renders the chapter as Markdown or HTML (RB.previewTree) with its spans
// highlighted. Every text piece carries its source offset (data-s, plus data-e when it shows
// decoded text such as an entity) so a selection there maps back to the source; generated
// labels (callout titles) are plain text outside any piece.
function renderPreview(body, spans) {
  const node = (n) => {
    if (n.label !== undefined) return n.label;
    if (n.span !== undefined) {
      const sp = spans[n.span], k = RB.kind(sp.attrs);
      return el("span", { class: "sp k-" + k + (sp.attrs.hist && !sp.attrs.no ? " hist" : ""), dataset: { i: String(sp.index) },
        title: (sp.attrs.id || "no id") + " · " + k + (sp.attrs.hist ? " · hist=" + sp.attrs.hist : "") },
      sp.attrs.id && !n.cont ? el("span", { class: "badge", "aria-hidden": "true", text: sp.attrs.id }) : null, n.children.map(node));
    }
    if (n.tag) return el(n.tag, n.attrs, n.children.map(node));
    return n.text === undefined ? el("span", { class: "seg", dataset: { s: String(n.s) }, text: body.slice(n.s, n.e) })
      : el("span", { class: "seg", dataset: { s: String(n.s), e: String(n.e) }, text: n.text });
  };
  const { format, nodes } = RB.previewTree(body, spans), pv = $("#edPreview");
  pv.dataset.format = format;
  pv.replaceChildren(...nodes.map(node));
}

const segStart = (seg) => parseInt(seg.dataset.s, 10);
const segEnd = (seg) => (seg.dataset.e ? parseInt(seg.dataset.e, 10) : segStart(seg) + seg.textContent.length);

// The source offset of a selection boundary (side "start" or "end") in the preview, -1 outside it.
// A boundary that is not inside a text piece (an element edge, a badge, a piece's far end) maps
// to the start of the next piece for "start" and to the end of the previous one for "end", so
// hidden markup between pieces stays out of the selection.
function previewOffset(node, offset, side) {
  const pv = $("#edPreview");
  if (!pv.contains(node)) return -1;
  const seg = node.nodeType === 3 ? node.parentElement.closest(".seg") : null;
  if (seg && (side === "start" ? offset < node.length : offset > 0)) {
    if (!seg.dataset.e) return segStart(seg) + offset;
    return side === "start" ? segStart(seg) : segEnd(seg);
  }
  const at = document.createRange();
  at.setStart(node, offset);
  const segs = [...pv.querySelectorAll(".seg")];
  if (side === "start") {
    const next = segs.find((s) => at.comparePoint(s, 0) >= 0);
    return next ? segStart(next) : -1;
  }
  const prev = segs.filter((s) => at.comparePoint(s, s.childNodes.length) <= 0).pop();
  return prev ? segEnd(prev) : -1;
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
        el("button", { class: "btn sm", type: "button", "aria-label": "Remove span", text: "×", onclick: () => removeSpan(sp) })),
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

function trLanguages() {
  return [...new Set([...RB.LANGUAGES, ...Object.keys(state.strings)])].filter((l) => l !== RB.SOURCE_LANGUAGE).sort();
}

function trRow(lang, id) { return (state.strings[lang] || {})[id]; }

function setTrRow(lang, id, row) {
  const rows = { ...(state.strings[lang] || {}) };
  if (row) rows[id] = row; else delete rows[id];
  state.strings = { ...state.strings, [lang]: rows };
  scheduleDraft();
  renderFiles();
}

// The Translations tab: rmstory's store for this rodeo, one row per translatable span.
// Every cell can be written by hand; the live builder also fills the gaps with an engine.
function renderTranslations() {
  const ids = RB.translatableIds(state.chapters), src = RB.sourceTexts(state.chapters), langs = trLanguages();
  if (!langs.includes(state.trLang)) state.trLang = langs[0];
  const engines = storyEngines(false), tools = storyTools();
  if (!engines.includes(state.trEngine)) state.trEngine = engines[0] || "";
  const intro = tools
    ? el("div", { class: "tr-bar" },
      el("select", { class: "select", "aria-label": "Language to fill", onchange: (ev) => { state.trLang = ev.target.value; } },
        langs.map((l) => el("option", { value: l, text: l, selected: l === state.trLang ? true : null }))),
      el("select", { class: "select", "aria-label": "rmstory engine", onchange: (ev) => { state.trEngine = ev.target.value; } },
        engines.map((e) => el("option", { value: e, text: e, selected: e === state.trEngine ? true : null }))),
      el("button", { class: "btn sm primary", type: "button", id: "trRun", disabled: state.busy ? true : null,
        text: state.busy === "translate" ? "Translating…" : "Translate missing", onclick: translateMissing }))
    : null;
  const note = el("p", { class: "muted note" }, tools
    ? "Translate missing fills the spans that have no translation in that language with the engine (stored as draft); what you wrote or reviewed is kept. The engine's key is read where rodeo builder runs."
    : window.RB_STATIC ? ["Click a cell to write a translation. Machine translation needs the live builder, ", el("code", { text: "rodeo builder" }), "."]
      : "Click a cell to write a translation. Machine translation needs rmstory on the builder's host: " + ((state.story && state.story.error) || "not available") + ".");
  const cell = (lang, id) => {
    const row = trRow(lang, id), on = state.trEdit && state.trEdit.lang === lang && state.trEdit.id === id;
    return el("td", {}, el("button", { type: "button", class: "tr-cell " + (row ? "st-" + (row.status || "draft") : "unstored") + (on ? " on" : ""),
      title: row ? row.content : "no " + lang + " translation stored", text: row ? row.status || "draft" : "—",
      onclick: () => { state.trEdit = on ? null : { lang, id }; renderTranslations(); } }));
  };
  fill($("#translationsView"), intro, note,
    ids.length ? el("table", { class: "matrix" }, el("thead", {}, el("tr", {}, el("th", { text: "id" }), langs.map((l) => el("th", { text: l })))),
      el("tbody", {}, ids.map((id) => el("tr", {}, el("td", { text: id }), langs.map((l) => cell(l, id))))))
      : el("p", { class: "empty", text: "No translatable spans yet: tag text as Translatable first." }),
    renderTrEdit(src));
}

function renderTrEdit(src) {
  const at = state.trEdit;
  if (!at || !(at.id in src)) return null;
  const row = trRow(at.lang, at.id), ta = el("textarea", { class: "textarea tr-text", id: "trText", "aria-label": at.lang + " translation of " + at.id });
  ta.value = row ? row.content : "";
  const status = el("select", { class: "select", "aria-label": "Status" },
    ["reviewed", "draft"].map((s) => el("option", { value: s, text: s, selected: (row ? row.status : "reviewed") === s ? true : null })));
  const save = () => {
    const text = ta.value.trim();
    if (!text) { toast("Write the translation first, or remove it", true); return; }
    const lacks = RB.missingPlaceholders(src[at.id], text);
    if (lacks.length) { toast("Keep " + lacks.join(" ") + " in the translation: it stands for a nested span", true); return; }
    setTrRow(at.lang, at.id, { content: text, status: status.value, updated_by: "rodeo-builder" });
    state.trEdit = null;
    renderTranslations();
  };
  return el("div", { class: "tr-edit" },
    el("span", { class: "label", text: at.id + " · " + RB.SOURCE_LANGUAGE }), el("pre", { class: "tr-src", text: src[at.id] }),
    el("span", { class: "label", text: at.lang }), ta,
    el("div", { class: "tr-actions" }, status,
      el("button", { class: "btn sm primary", type: "button", text: "Save", onclick: save }),
      row ? el("button", { class: "btn sm", type: "button", text: "Remove", onclick: () => { setTrRow(at.lang, at.id, null); state.trEdit = null; renderTranslations(); } }) : null,
      el("button", { class: "btn sm", type: "button", text: "Cancel", onclick: () => { state.trEdit = null; renderTranslations(); } })));
}

async function translateMissing() {
  if (!RB.translatableIds(state.chapters).length) { toast("No translatable spans yet: tag some text first.", true); return; }
  const lang = state.trLang, before = Object.keys(state.strings[lang] || {}).length;
  state.busy = "translate";
  renderTranslations();
  let res = null;
  try {
    res = await storyPost("translate", { chapters: chapterPayload(), to: lang, engine: state.trEngine, strings: state.strings });
  } catch (err) { toast("Translate failed: " + err.message, true); }
  state.busy = "";
  if (res) {
    state.strings = res.strings;
    let changed = 0;
    for (const c of res.chapters) {
      const ch = state.chapters.find((x) => x.id === c.id);
      if (ch && ch.body !== c.body) { ch.body = c.body; changed++; }
    }
    const added = Object.keys(state.strings[lang] || {}).length - before;
    toast(plural(added, lang + " translation") + " filled as draft with " + state.trEngine +
      (changed ? " · rmstory gave unnamed spans an id in " + plural(changed, "chapter") : ""));
    if (state.editing >= 0) $("#edSource").value = editing().body;
    render();
  }
  if (state.editing >= 0) renderEditor(); else renderTranslations();
}

function closePop() { $("#popover").hidden = true; pop = null; }

function popHead(title) {
  return el("div", { class: "pop-head" }, el("h4", { text: title }),
    el("button", { class: "x-btn", type: "button", "aria-label": "Close", text: "×", onclick: closePop }));
}

// A selection first shows a small chip; the apply menu opens when it is clicked.
// Selecting exactly an existing span opens its edit menu directly.
function tagChip(s, e, x, y) {
  const body = editing().body;
  const target = RB.wrapTarget(body, RB.parseSpans(body).spans, s, e);
  if (!target.ok) { toast(target.reason, true); return; }
  if (target.same) { editPop(target.same.index, x, y); return; }
  pop = { mode: "chip" };
  const p = $("#popover");
  p.className = "popover chip-pop";
  p.replaceChildren(el("button", { class: "btn primary sm", type: "button", text: "Tag selection ▸", onclick: () => applyPop(s, e, x, y) }));
  placePop(x, y);
}

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
  p.className = "popover";
  fill(p, popHead("Apply span"),
    el("div", { class: "chips" }, [["translatable", "Translatable"], ["invariant", "Invariant"], ["nolang", "No-lang"]].map(([k, label]) =>
      el("button", { class: "btn sm", type: "button", text: label, onclick: () => applySpan(k, id) }))),
    allVariants().length ? el("div", { class: "row2" }, el("span", { class: "muted mono", text: "hist" }), el("span", { class: "chips" },
      allVariants().map((v) => el("button", { class: "chip", type: "button", text: v, "aria-pressed": "false",
        onclick: () => {
          pop.hist = pop.hist === v ? "" : v;
          p.querySelectorAll(".chip").forEach((c) => { c.classList.toggle("on", c.textContent === pop.hist); c.setAttribute("aria-pressed", String(c.textContent === pop.hist)); });
        } })))) : null,
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
  const lang = RB.kind(pop.attrs) === "translatable" ? el("select", { class: "select", "aria-label": "Language" },
    RB.LANGUAGES.map((l) => el("option", { value: l, text: l, selected: pop.attrs.lang === l })))
    : el("span", { class: "muted mono hint", text: "Language only applies to translatable spans." });
  const hist = el("select", { class: "select", "aria-label": "Story variant" }, el("option", { value: "", text: "no variant" }),
    allVariants().map((v) => el("option", { value: v, text: v, selected: pop.attrs.hist === v })));
  const known = [];
  for (const ch of state.chapters) for (const s of RB.parseSpans(ch.body).spans) {
    if (s.attrs.id && s.attrs.id !== sp.attrs.id && !known.some((k) => k.id === s.attrs.id)) known.push({ id: s.attrs.id, same: RB.inner(ch.body, s) === text });
  }
  known.sort((a, b) => (b.same - a.same) || a.id.localeCompare(b.id));
  const kinds = [["translatable", "Translatable"], ["invariant", "Invariant"], ["nolang", "No-lang"]];
  p.className = "popover";
  fill(p, popHead("Edit span"),
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
      el("button", { class: "btn sm", type: "button", text: "Remove span", onclick: () => removeSpan(sp) })));
  placePop(x, y);
}

function editPopSave(idInput, lang, hist, keepOpen) {
  const attrs = { ...pop.attrs };
  if (RB.kind(attrs) === "translatable" && lang.tagName === "SELECT") attrs.lang = lang.value;
  if (hist.value) attrs.hist = hist.value; else delete attrs.hist;
  if (idInput.value.trim()) attrs.id = idInput.value.trim(); else delete attrs.id;
  const { index } = pop, sp = RB.parseSpans(editing().body).spans[index];
  setBody(RB.retag(editing().body, sp, attrs));
  if (keepOpen) { const r = $("#popover").getBoundingClientRect(); editPop(index, r.left, r.top - 12); } else closePop();
}

// Wraps the source selection in a span of `type` (toolbar buttons and Alt+T / I / N).
function markSelection(type) {
  const ta = $("#edSource"), { spans } = RB.parseSpans(editing().body);
  const target = RB.wrapTarget(editing().body, spans, ta.selectionStart, ta.selectionEnd);
  if (!target.ok) { toast(target.reason, true); return; }
  pop = { mode: "apply", s: target.s, e: target.e, hist: "" };
  applySpan(type, autoId(target));
}

function bindEditor() {
  const ta = $("#edSource");
  ta.addEventListener("input", () => {
    closePop();
    const now = Date.now();
    if (now - edHistory.typedAt > 800) remember(editing().body);
    edHistory.typedAt = now;
    editing().body = ta.value;
    renderEditor();
  });
  ta.addEventListener("mouseup", (ev) => { if (ta.selectionEnd > ta.selectionStart) tagChip(ta.selectionStart, ta.selectionEnd, ev.clientX, ev.clientY); });
  for (const type of ["select", "keyup", "click"]) ta.addEventListener(type, updateCaret);
  ta.addEventListener("keydown", (ev) => {
    const type = { KeyT: "translatable", KeyI: "invariant", KeyN: "nolang" }[ev.code];
    if (!ev.altKey || !type) return;
    ev.preventDefault();
    if (ta.selectionEnd <= ta.selectionStart) { toast("Select some text first (Shift+arrows), then press the shortcut.", true); return; }
    markSelection(type);
  });
  ta.addEventListener("dblclick", (ev) => { const sp = spanAtCursor(); if (sp) { ev.preventDefault(); editPop(sp.index, ev.clientX, ev.clientY); } });
  const pv = $("#edPreview");
  pv.addEventListener("mouseup", (ev) => {
    const sel = getSelection();
    if (!sel || sel.isCollapsed) return;
    const r = sel.getRangeAt(0), a = previewOffset(r.startContainer, r.startOffset, "start"), b = previewOffset(r.endContainer, r.endOffset, "end");
    if (a < 0 || b <= a) return;
    tagChip(a, b, ev.clientX, ev.clientY);
  });
  pv.addEventListener("dblclick", (ev) => {
    const node = ev.target.closest(".sp");
    if (!node) return;
    getSelection().removeAllRanges();
    editPop(parseInt(node.dataset.i, 10), ev.clientX, ev.clientY);
  });
  document.querySelectorAll("[data-mark]").forEach((b) => b.addEventListener("click", () => {
    if (ta.selectionEnd <= ta.selectionStart) { toast("Select text in the source first", true); return; }
    markSelection(b.dataset.mark);
  }));
  $("#edRemove").addEventListener("click", () => {
    const sp = spanAtCursor();
    if (sp) removeSpan(sp); else toast("Place the cursor inside a span first", true);
  });
  document.querySelectorAll("[data-edtab]").forEach((b) => b.addEventListener("click", () => { state.edTab = b.dataset.edtab; renderEditor(); }));
  $("#addVariant").addEventListener("click", () => {
    const v = RB.slugify($("#newVariant").value);
    if (v && !allVariants().includes(v)) state.extraVariants.push(v);
    $("#newVariant").value = "";
    renderEditor();
  });
  $("#edDone").addEventListener("click", closeEditor);
  $("#edCancel").addEventListener("click", cancelEditor);
  document.addEventListener("keydown", (ev) => {
    if ($("#editor").hidden || !(ev.ctrlKey || ev.metaKey) || ev.altKey) return;
    const t = ev.target;
    if (t !== $("#edSource") && (t.matches("input, textarea, select") || t.isContentEditable)) return;
    const key = ev.key.toLowerCase();
    if (key === "z" && !ev.shiftKey) { ev.preventDefault(); stepHistory(edHistory.past, edHistory.future); }
    else if ((key === "z" && ev.shiftKey) || key === "y") { ev.preventDefault(); stepHistory(edHistory.future, edHistory.past); }
  });
  document.addEventListener("mousedown", (ev) => { if (pop && !ev.target.closest("#popover")) closePop(); });
}

// ── download ────────────────────────────────────────────────────────────

// The whole rodeo, or one part ("infrastructure": the lab; "instructions": story/ and checks/).
function download(part) {
  part = RB.PARTS.includes(part) ? part : "";
  if (!state.chapters.length && part !== "infrastructure") { toast("Add at least one chapter first.", true); return; }
  const name = RB.slugify(state.name);
  if (!name) { toast("Give the rodeo a name first", true); $("#name").focus(); return; }
  state.name = name;
  const r = rodeo(), file = name + (part ? "-" + part : "");
  const warnings = part === "infrastructure" ? [] : RB.validate(r.chapters);
  const entries = (part ? RB.partFiles(r, part) : RB.files(r)).map((f) => ({ ...f, path: file + "/" + f.path }));
  const blob = new Blob([RB.zip(entries)], { type: "application/zip" });
  const a = el("a", { href: URL.createObjectURL(blob), download: file + ".zip" });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  toast(file + ".zip: " + entries.length + " files" + (warnings.length ? " · " + warnings.length + " span warnings (Fix ✎ in the review list)" : ""), warnings.length > 0);
}

// ── save (live server only) ─────────────────────────────────────────────

async function save(force) {
  const name = RB.slugify(state.name);
  if (!name) { toast("Give the rodeo a name first", true); $("#name").focus(); return; }
  state.name = name;
  if (!force && state.server.profiles.includes(name)) {
    if (window.confirm("A profile named " + name + " already exists in " + state.server.profiles_dir + ". Replace it?")) return save(true);
    return false;
  }
  const r = rodeo();
  const files = RB.files(r).map((f) => ({ path: f.path, content: f.content, executable: f.mode === 0o755 }));
  let res;
  try {
    res = await apiPost("save", { name, base: r.base || null, files, force: !!force });
  } catch (err) { toast("Save failed: " + err.message, true); return false; }
  if (res.status === 409 && !force) {
    if (window.confirm("A profile named " + name + " already exists in " + state.server.profiles_dir + ". Replace it?")) return save(true);
    return false;
  }
  if (res.status !== 200) { toast("Save failed: " + (res.body.error || res.status), true); return false; }
  if (!state.server.profiles.includes(name)) state.server.profiles.push(name);
  state.savedAs = name;
  renderDeploy();
  toast("Saved " + res.body.path + " · deploy with: rodeo up --profile " + name);
  return true;
}

// ── deploy without saving (live server only, rodeo/builder/deploywork.py) ──
// The deploy runs from a copy in ~/rodeo-labs/<name>-unsaved-<time>/: the page says so before,
// during and after, until the rodeo is saved.

let deployPoll = 0;

function deployReason() {
  if (window.RB_STATIC) return "Only available in rodeo builder, the dynamic version: run rodeo builder on the host that deploys the lab.";
  if (!state.deploy) return "Checking whether this builder can deploy…";
  return state.deploy.available ? "" : state.deploy.reason;
}

function renderDeploy() {
  const reason = deployReason(), btn = $("#deployBtn"), cur = state.deploy && state.deploy.current;
  btn.setAttribute("aria-disabled", reason ? "true" : "false");
  $("#deployWrap").title = reason || "Deploy this rodeo with rodeo up, without saving it first";
  const banner = $("#deployBanner");
  banner.hidden = !cur;
  if (!cur) return;
  const saved = state.savedAs ? " This rodeo is saved as the profile " + state.savedAs + "." : " This rodeo is not saved: the lab directory is a copy, not one of your profiles.";
  const text = {
    running: "Deploying an unsaved rodeo from " + cur.lab + "…",
    stopping: "Stopping the deploy in " + cur.lab + "…",
    succeeded: "Deployed from an unsaved rodeo: " + cur.lab + ".",
    failed: "Deploy failed (exit " + cur.rc + "). Fix it and rerun from the lab: cd " + cur.lab + " && rodeo up",
    stopped: "Deploy stopped. Resume from the lab: cd " + cur.lab + " && rodeo up",
  }[cur.status] || cur.status;
  $("#deployText").textContent = text + saved;
  banner.classList.toggle("ok", cur.status === "succeeded");
  banner.classList.toggle("bad", cur.status === "failed" || cur.status === "stopped");
  $("#deploySave").hidden = !!state.savedAs || !(state.server && state.server.save);
  $("#deployStop").hidden = cur.status !== "running";
  $("#deployLogBtn").setAttribute("aria-expanded", String(state.deployLogOpen));
  $("#deployLog").hidden = !state.deployLogOpen;
  $("#deployLog").textContent = cur.log || "(no output yet)";
}

// On an exposed builder deploying needs a login: GET login makes the browser ask for it, and
// sends it with every request after; deploy and vault are then read again with it.
async function deployLogin() {
  if (!state.deploy.login) return true;
  try {
    await apiGet("login");
  } catch (err) {
    toast("Deploying needs a login: " + err.message, true);
    return false;
  }
  state.deploy = await apiGet("deploy").catch(() => state.deploy);
  await refreshVault();
  return !state.deploy.login;
}

async function openDeploy() {
  const reason = deployReason();
  if (reason) { toast(reason, true); return; }
  if (!(await deployLogin())) return;
  const name = RB.slugify(state.name);
  if (!name) { toast("Give the rodeo a name first", true); $("#name").focus(); return; }
  if (!state.chapters.length) { toast("Add at least one chapter first.", true); return; }
  const cur = state.deploy.current;
  if (cur && (cur.status === "running" || cur.status === "stopping")) { toast("A deploy is already running: see the banner", true); return; }
  state.name = name;
  $("#dpWhere").textContent = "rodeo up runs on this builder's host (target " + state.target + ", as " + state.deploy.as
    + ") from a new lab directory, ~/rodeo-labs/" + name + "-unsaved-<time>/: a copy of what is on this page now.";
  $("#dpSecrets").hidden = true;
  $("#dpSecretFields").replaceChildren();
  $("#dpResult").textContent = "";
  $("#dpSaveDeploy").hidden = !(state.server && state.server.save);
  $("#deployModal").hidden = false;
  $("#dpSubmit").focus();
}

function closeDeploy() {
  $("#deployModal").hidden = true;
  $("#dpSecrets").hidden = true;
  $("#dpSecretFields").replaceChildren();
  $("#deployBtn").focus();
}

async function startDeploy(saveFirst) {
  if (saveFirst && !(await save(false))) return;
  const r = rodeo(), name = RB.slugify(state.name);
  const files = RB.files(r).map((f) => ({ path: f.path, content: f.content, executable: f.mode === 0o755 }));
  const inputs = [...$("#dpSecretFields").querySelectorAll("input")];
  const secrets = Object.fromEntries(inputs.filter((i) => !i.dataset.ref).map((i) => [i.name, i.value]));
  const refs = Object.fromEntries(inputs.filter((i) => i.dataset.ref).map((i) => [i.name, i.dataset.ref]));
  const payload = { name, base: r.base || null, files, secrets };
  if (Object.keys(refs).length) payload.secret_refs = refs;
  $("#dpSubmit").disabled = true;
  const res = await apiPost("deploy", payload).catch((err) => ({ status: 0, body: { error: err.message } }));
  $("#dpSubmit").disabled = false;
  if (res.status === 200 && res.body.secrets) {
    $("#dpSecrets").hidden = false;
    $("#dpSecretFields").replaceChildren(...res.body.secrets.map(deploySecret));
    $("#dpResult").textContent = "Fill in the secrets this lab needs, then deploy.";
    $("#dpSecretFields input").focus();
    return;
  }
  if (res.status !== 200) { $("#dpResult").textContent = "Deploy failed to start: " + (res.body.error || res.status); return; }
  if (!saveFirst) state.savedAs = "";
  state.deploy = { ...state.deploy, current: res.body.current };
  closeDeploy();
  renderDeploy();
  pollDeploy();
}

function pollDeploy() {
  clearTimeout(deployPoll);
  deployPoll = setTimeout(async () => {
    state.deploy = await apiGet("deploy").catch(() => state.deploy);
    renderDeploy();
    const cur = state.deploy.current;
    if (cur && (cur.status === "running" || cur.status === "stopping")) pollDeploy();
    else if (cur) toast(cur.status === "succeeded" ? "Deploy finished: " + cur.lab : "Deploy " + cur.status + ": see the banner", cur.status !== "succeeded");
  }, 2000);
}

async function stopDeploy() {
  if (!window.confirm("Stop the running deploy? rodeo up is interrupted like Ctrl-C; the lab may be half built.")) return;
  const res = await apiPost("deploy-stop", {}).catch((err) => ({ status: 0, body: { error: err.message } }));
  if (res.status !== 200) { toast("Stop failed: " + (res.body.error || res.status), true); return; }
  state.deploy = { ...state.deploy, current: res.body.current };
  renderDeploy();
  pollDeploy();
}

// ── stored credentials (live server, after a login on an exposed one; GET vault) ──
// Each credential is encrypted under its own password, asked every time it is opened. Neither
// passwords nor decrypted values are kept: not in state, not in storage, and not in the DOM once
// a dialog closes. They are added and deleted on the Settings page (/rodeo-builder/settings/ over
// HTTPS on this host, behind the web server's login), which the header's Settings link opens.

let vaultAsk = null;

function vaultOn() { return !window.RB_STATIC && !!state.vault && state.vault.available; }

async function refreshVault() {
  state.vault = await apiGet("vault").catch(() => state.vault);
}

function typeLabel(id) { const t = RB.vaultType(state.vault, id); return t ? t.label : id; }

// Asks for a credential's password and resolves to its decrypted fields, or null when cancelled.
// Another engine's credential stored without encryption (`encrypted: false`) opens without one.
async function openVault(item) {
  if (item.encrypted === false) {
    const res = await apiPost("vault-open", { id: item.id, password: "" }).catch((err) => ({ status: 0, body: { error: err.message } }));
    if (res.status === 200) return res.body.fields;
    toast("Could not open " + item.name + ": " + (res.body.error || res.status), true);
    return null;
  }
  return new Promise((resolve) => {
    vaultAsk = { item, resolve, back: document.activeElement };
    $("#vpTitle").textContent = "Password for " + item.name;
    $("#vpPass").value = "";
    $("#vpError").textContent = "";
    $("#vaultPrompt").hidden = false;
    $("#vpPass").focus();
  });
}

function closeVaultPrompt(fields) {
  const ask = vaultAsk;
  vaultAsk = null;
  $("#vpPass").value = "";
  $("#vpError").textContent = "";
  $("#vaultPrompt").hidden = true;
  if (!ask) return;
  if (ask.back && ask.back.isConnected) ask.back.focus();
  ask.resolve(fields || null);
}

async function unlockVault(ev) {
  ev.preventDefault();
  if (!vaultAsk) return;
  const password = $("#vpPass").value;
  if (!password) { $("#vpError").textContent = "Type the password."; return; }
  $("#vpSubmit").disabled = true;
  const res = await apiPost("vault-open", { id: vaultAsk.item.id, password }).catch((err) => ({ status: 0, body: { error: err.message } }));
  $("#vpSubmit").disabled = false;
  if (res.status === 200) { closeVaultPrompt(res.body.fields); return; }
  $("#vpError").textContent = res.status === 403 ? "Wrong password: try again." : (res.body.error || "Could not open it (" + res.status + ").");
  $("#vpPass").select();
}

// The secret input of the deploy dialog, with "Use stored…" when a stored credential or a
// secrets.yaml key could fill it.
function deploySecret(key) {
  const input = el("input", { class: "input mono", type: "password", name: key, autocomplete: "off", required: true });
  const label = el("label", { class: "label" }, key, input);
  const items = vaultOn() ? RB.vaultCandidates(state.vault, key) : [];
  const fileKeys = window.RB_STATIC ? [] : RB.vaultFileKeys(state.vault, key);
  if (!items.length && !fileKeys.length) return label;
  const note = el("p", { class: "note", role: "status" });
  const ref = el("p", { class: "dp-ref", hidden: true }, el("span"),
    el("button", { class: "btn sm", type: "button", text: "Clear", "aria-label": "Type " + key + " instead",
      onclick: () => { useFileSecret(input, ""); input.focus(); } }));
  const pick = el("select", { class: "select", "aria-label": "Use a stored credential for " + key },
    el("option", { value: "", text: "Use stored…" }),
    items.map((item) => el("option", { value: item.id, text: item.name + " (" + (item.engine && item.engine !== "rodeo" ? item.app + ", " + item.engine : typeLabel(item.type)) + ")" })),
    fileKeys.length ? el("optgroup", { label: "secrets.yaml" }, fileKeys.map((k) => el("option", { value: "file:" + k, text: k }))) : null);
  pick.addEventListener("change", () => {
    const item = items.find((i) => i.id === pick.value);
    const fileKey = pick.value.startsWith("file:") ? pick.value.slice(5) : "";
    pick.value = "";
    if (fileKey) { note.textContent = ""; useFileSecret(input, fileKey); }
    if (item) useStored(item, key, note);
  });
  return el("div", { class: "dp-secret" }, label, pick, fileKeys.length ? ref : null, note);
}

// Makes a deploy secret input stand for the secrets.yaml key `fileKey` (sent as a secret_refs
// name, no value), or back to a typed value when `fileKey` is "".
function useFileSecret(input, fileKey) {
  const ref = input.closest(".dp-secret").querySelector(".dp-ref");
  input.value = "";
  input.readOnly = !!fileKey;
  input.required = !fileKey;
  input.placeholder = fileKey ? "from secrets.yaml" : "";
  if (fileKey) input.dataset.ref = fileKey;
  else delete input.dataset.ref;
  ref.firstChild.textContent = fileKey ? "from secrets.yaml: " + fileKey : "";
  ref.hidden = !fileKey;
}

// Fills `key`, and every other secret of the dialog the credential maps, from the opened credential.
async function useStored(item, key, note) {
  const fields = await openVault(item);
  if (!fields) return;
  const inputs = [...$("#dpSecretFields").querySelectorAll("input")];
  const got = RB.vaultFill(RB.vaultType(state.vault, item.type), fields, inputs.map((i) => i.name));
  for (const i of inputs) {
    if (!(i.name in got)) continue;
    if (i.dataset.ref) useFileSecret(i, "");
    i.value = got[i.name];
  }
  note.textContent = key in got ? "Filled from " + item.name + (Object.keys(got).length > 1 ? ": " + Object.keys(got).join(", ") : "") + "."
    : "This credential has no " + key + " field.";
  note.classList.toggle("bad-text", !(key in got));
  refreshVault();
}

// ── publish to GitHub ───────────────────────────────────────────────────
// The live builder on loopback publishes with the host's GitHub login (rodeo/builder/publish.py),
// asking for a device-flow login when there is none; otherwise the dialog lists github.com
// pages that use the browser's GitHub login.

let pubPoll = 0;

function publishLanguages() {
  const r = rodeo();
  return [state.lang || RB.SOURCE_LANGUAGE, ...RB.translatedLanguages(r.strings, RB.translatableIds(r.chapters))]
    .filter((l, i, all) => all.indexOf(l) === i);
}

function pubLive() { return !window.RB_STATIC && state.github && state.github.available; }

async function openPublish() {
  const name = RB.slugify(state.name);
  if (!name) { toast("Give the rodeo a name first", true); $("#name").focus(); return; }
  state.name = name;
  $("#pubName").value = name;
  $("#pubUpdate").checked = false;
  $("#pubResult").replaceChildren();
  $("#publishModal").hidden = false;
  if (!window.RB_STATIC) state.github = await apiGet("github").catch((err) => ({ available: true, logged_in: false, error: err.message }));
  renderPublish();
  if (pubLive() && !state.github.logged_in && state.github.device && state.github.device.pending) pollGithub();
  (pubLive() ? $("#pubName") : $("#pubUser")).focus();
}

function closePublish() {
  clearTimeout(pubPoll);
  $("#publishModal").hidden = true;
  $("#publishBtn").focus();
}

function link(href, text) { return el("a", { href, target: "_blank", rel: "noopener", text }); }

function renderPublish() {
  const live = pubLive(), gh = state.github || {};
  $("#pubStatic").hidden = live;
  $("#pubLive").hidden = !live || !gh.logged_in;
  $("#pubSubmit").hidden = !live || !gh.logged_in;
  $("#pubLogin").hidden = !live || gh.logged_in;
  if (!live) {
    $("#pubAccount").textContent = "These github.com pages use your browser's GitHub login; GitHub asks you to log in if you are not.";
    renderPublishSteps();
    return;
  }
  $("#pubCatalog").textContent = gh.catalog || RB.CATALOG_REPO;
  const d = gh.device || {};
  $("#pubAccount").textContent = gh.logged_in ? "Publishing as " + gh.login + " (GitHub login from " + gh.source + ")."
    : gh.error || "No GitHub login on the builder's host yet.";
  $("#pubLoginBtn").disabled = !!d.pending;
  $("#pubLoginBtn").hidden = !gh.gh;
  $("#pubDevice").replaceChildren(...(d.pending && d.code
    ? ["Enter the code ", el("strong", { class: "mono", text: d.code }), " at ", link(d.url, d.url), ". This dialog notices the login."]
    : [d.error ? d.error + ". " : "", gh.gh ? "The GitHub CLI on the builder's host signs you in with a one-time code."
      : "Install the GitHub CLI (gh) on the builder's host and open Publish again, or run gh auth login or set GH_TOKEN there."]));
}

function renderPublishSteps() {
  const user = $("#pubUser").value.trim(), name = RB.slugify(state.name);
  const box = $("#pubSteps");
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(user)) {
    box.replaceChildren(el("li", { class: "muted", text: "Type your GitHub account to get the links." }));
    return;
  }
  const l = RB.publishLinks({ user, name, title: state.title, engine: state.engine, languages: publishLanguages() });
  box.replaceChildren(
    el("li", {}, link(l.create, "Create the repository " + user + "/" + name), " (public)."),
    el("li", {}, el("button", { class: "btn sm", type: "button", text: "Download rodeo", onclick: download }), " then ",
      link(l.upload, "upload the unzipped files"), ", or push them: ", el("code", { text: "git push " + l.repo + " main" }), "."),
    el("li", {}, link(l.catalog, "Add it to the rodeo catalog"), ": GitHub forks rodeo-cli and opens the pull request for you."));
}

async function githubLogin() {
  $("#pubLoginBtn").disabled = true;
  const res = await apiPost("github-login", {}).catch((err) => ({ status: 0, body: { error: err.message } }));
  if (res.status !== 200) { state.github = { ...state.github, error: res.body.error }; renderPublish(); return; }
  state.github = { ...state.github, device: res.body };
  renderPublish();
  if (res.body.code) window.open(res.body.url, "_blank", "noopener");
  pollGithub();
}

// Re-reads the login every 3 s while the dialog is open and gh's device login is pending.
function pollGithub() {
  clearTimeout(pubPoll);
  pubPoll = setTimeout(async () => {
    if ($("#publishModal").hidden) return;
    state.github = await apiGet("github").catch(() => state.github);
    renderPublish();
    if (!state.github.logged_in && state.github.device && state.github.device.pending) pollGithub();
  }, 3000);
}

async function publishRodeo(ev) {
  ev.preventDefault();
  const name = RB.slugify($("#pubName").value);
  if (!name) { $("#pubName").focus(); return; }
  state.name = name;
  render();
  const r = rodeo(), btn = $("#pubSubmit"), update = $("#pubUpdate").checked;
  const files = RB.files(r).map((f) => ({ path: f.path, content: f.content, executable: f.mode === 0o755 }));
  btn.disabled = true;
  btn.textContent = "Publishing…";
  $("#pubResult").textContent = "";
  try {
    const res = await apiPost("publish", { name, title: state.title, engine: state.engine, languages: publishLanguages(), files, update });
    if (res.status === 401) { state.github = { ...state.github, logged_in: false, error: res.body.error }; renderPublish(); }
    else if (res.status === 409) $("#pubResult").textContent = res.body.error + ": tick Update to push to it, or pick another name.";
    else if (res.status !== 200) $("#pubResult").textContent = "Publish failed: " + (res.body.error || res.status);
    else {
      const catalog = RB.publishLinks({ user: res.body.login, name, title: state.title, engine: state.engine,
        languages: publishLanguages(), catalog: state.github.catalog }).catalog;
      $("#pubResult").replaceChildren("Published ", link(res.body.repo, res.body.repo),
        ...(res.body.pull_request ? [" · catalog pull request ", link(res.body.pull_request, res.body.pull_request)] : []),
        ...(res.body.catalog_error ? [". The catalog pull request failed (" + res.body.catalog_error + "): ",
          link(catalog, "add it to the catalog on github.com")] : []), ".");
      toast("Published " + res.body.repo);
    }
  } catch (err) { $("#pubResult").textContent = "Publish failed: " + err.message; }
  btn.disabled = false;
  btn.textContent = "Publish";
}

// ── dialogs ─────────────────────────────────────────────────────────────

const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), iframe, [tabindex]:not([tabindex='-1'])";

function openDialog() {
  if (!$("#vaultPrompt").hidden) return $("#vaultPrompt .modal");
  if (!$("#newModal").hidden) return $("#newModal .modal");
  if (!$("#importModal").hidden) return $("#importModal .modal");
  if (!$("#publishModal").hidden) return $("#publishModal .modal");
  if (!$("#deployModal").hidden) return $("#deployModal .modal");
  if (!$("#embedOverlay").hidden) return $("#embedOverlay .embed");
  if (!$("#editor").hidden) return $("#editor");
  return null;
}

// Tab and Shift+Tab wrap inside the open dialog (the span popover is left free).
function trapTab(ev) {
  const dialog = openDialog();
  if (!dialog || (pop && document.activeElement.closest("#popover"))) return;
  const items = [...dialog.querySelectorAll(FOCUSABLE)].filter((n) => n.getClientRects().length);
  if (!items.length) return;
  const first = items[0], last = items.at(-1), active = document.activeElement;
  if (!dialog.contains(active)) { ev.preventDefault(); first.focus(); }
  else if (ev.shiftKey && active === first) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && active === last) { ev.preventDefault(); first.focus(); }
}

// Scrolling one of two panes scrolls the other to the same proportion of its height.
// The position set on the follower is remembered so its own scroll event doesn't echo back.
function linkScroll(a, b) {
  const placed = new Map();
  const follow = (src, dst) => () => {
    const echo = placed.get(src);
    placed.delete(src);
    if (echo !== undefined && Math.abs(echo - src.scrollTop) <= 1) return;
    const range = src.scrollHeight - src.clientHeight, room = dst.scrollHeight - dst.clientHeight;
    const top = range > 0 ? Math.round((src.scrollTop / range) * room) : 0;
    if (Math.abs(dst.scrollTop - top) <= 1) return;
    dst.scrollTop = top;
    placed.set(dst, dst.scrollTop);
  };
  a.addEventListener("scroll", follow(a, b), { passive: true });
  b.addEventListener("scroll", follow(b, a), { passive: true });
}

// ── column widths ───────────────────────────────────────────────────────

// Drag a separator, or focus it and use ←/→ (Shift: bigger steps); double-click resets.
// The widths are kept in this browser only.
const COLS_KEY = "rodeo-builder-cols";
const COL_LIMITS = { lw: [220, 560, 290], rw: [260, 600, 320] };
const colWidths = {};

function setColWidth(key, px) {
  const [min, max] = COL_LIMITS[key];
  colWidths[key] = Math.round(Math.min(max, Math.max(min, px)));
  $(".cols").style.setProperty("--" + key, colWidths[key] + "px");
  $(key === "lw" ? "#resizeLeft" : "#resizeRight").setAttribute("aria-valuenow", String(colWidths[key]));
}

function saveCols() {
  try { localStorage.setItem(COLS_KEY, JSON.stringify(colWidths)); } catch (err) { /* no storage: widths reset on reload */ }
}

function initResizers() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(COLS_KEY) || "{}") || {}; } catch (err) { saved = {}; }
  for (const [sel, key, dir] of [["#resizeLeft", "lw", 1], ["#resizeRight", "rw", -1]]) {
    const sep = $(sel), [min, max, initial] = COL_LIMITS[key];
    sep.setAttribute("aria-valuemin", String(min));
    sep.setAttribute("aria-valuemax", String(max));
    setColWidth(key, Number(saved[key]) || initial);
    sep.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      sep.setPointerCapture(ev.pointerId);
      const x0 = ev.clientX, w0 = colWidths[key];
      const move = (e) => setColWidth(key, w0 + dir * (e.clientX - x0));
      const end = () => {
        sep.removeEventListener("pointermove", move);
        document.body.classList.remove("resizing");
        saveCols();
      };
      document.body.classList.add("resizing");
      sep.addEventListener("pointermove", move);
      sep.addEventListener("pointerup", end, { once: true });
      sep.addEventListener("pointercancel", end, { once: true });
    });
    sep.addEventListener("dblclick", () => { setColWidth(key, initial); saveCols(); });
    sep.addEventListener("keydown", (ev) => {
      const step = { ArrowLeft: -1, ArrowRight: 1 }[ev.key];
      if (!step) return;
      ev.preventDefault();
      setColWidth(key, colWidths[key] + dir * step * (ev.shiftKey ? 64 : 16));
      saveCols();
    });
  }
}

// ── init ────────────────────────────────────────────────────────────────

function bind() {
  initResizers();
  linkScroll($("#edSource"), $("#edPreview"));
  document.addEventListener("mousedown", (ev) => {
    if (state.info && !ev.target.closest(".info-pop, .info-btn")) toggleInfo(state.info);
    if (!$("#filterMenu").hidden && !ev.target.closest("#filterMenu, #filterBtn")) setFilterMenu(false);
  });
  $("#filterBtn").addEventListener("click", () => setFilterMenu($("#filterMenu").hidden));
  $("#filterMenu").addEventListener("change", (ev) => { state.filters[ev.target.value] = ev.target.checked; renderLibrary(); });
  $("#detailsBtn").addEventListener("click", () => { state.details = !state.details; renderLibrary(); });
  $("#search").addEventListener("input", (ev) => { state.query = ev.target.value; renderLibrary(); });
  $("#name").addEventListener("change", (ev) => { state.name = RB.slugify(ev.target.value) || state.name; render(); });
  $("#title").addEventListener("input", (ev) => { state.title = ev.target.value; renderFiles(); });
  $("#lang").replaceChildren(...RB.LANGUAGES.map((l) => el("option", { value: l, text: l + (l === RB.SOURCE_LANGUAGE ? " (source)" : "") })));
  $("#lang").addEventListener("change", (ev) => { state.lang = ev.target.value; render(); });
  $("#target").addEventListener("change", (ev) => { state.target = ev.target.value; render(); });
  $("#contentUrl").addEventListener("change", (ev) => { state.contentUrl = ev.target.value.trim(); render(); });
  $("#contentFormat").addEventListener("change", (ev) => { state.contentFormat = ev.target.value; render(); });
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("[data-tab]").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", String(x === b)); });
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
  $("#ncDraftBtn").addEventListener("click", draftChapter);
  $("#stickyDownload").addEventListener("click", download);
  $("#warnBtn").addEventListener("click", scrollToReview);
  $("#stickyWarn").addEventListener("click", scrollToReview);
  $("#toastUndo").addEventListener("click", () => {
    const undo = state.undo;
    if (!undo) return;
    state.undo = null;
    undo();
    toast("Restored");
  });
  const lab = $("#chapterList");
  lab.addEventListener("dragover", (ev) => { if (!state.drag) return; ev.preventDefault(); if (state.drag.kind === "lib") lab.classList.add("over"); });
  lab.addEventListener("dragleave", (ev) => { if (!lab.contains(ev.relatedTarget)) lab.classList.remove("over"); });
  lab.addEventListener("drop", (ev) => { ev.preventDefault(); dropAt(state.chapters.length); });
  $("#saveBtn").addEventListener("click", () => save(false));
  $("#planEditor").addEventListener("input", onPlanInput);
  $("#planEditor").addEventListener("blur", () => renderFiles());
  $("#planReset").addEventListener("click", () => {
    const edited = state.planEdited;
    state.planEdited = null;
    render();
    toast("plan.yaml regenerated", false, () => { state.planEdited = edited; render(); });
  });
  $("#newChapterBtn").addEventListener("click", openNewChapter);
  $("#importBtn").addEventListener("click", openImport);
  $("#nsBtn").addEventListener("click", retellStory);
  $("#publishBtn").addEventListener("click", openPublish);
  $("#deployBtn").addEventListener("click", openDeploy);
  $("#deployForm").addEventListener("submit", (ev) => { ev.preventDefault(); startDeploy(false); });
  $("#dpSaveDeploy").addEventListener("click", () => startDeploy(true));
  $("#dpCancel").addEventListener("click", closeDeploy);
  $("#deployModal").addEventListener("mousedown", (ev) => { if (ev.target === ev.currentTarget) closeDeploy(); });
  $("#deploySave").addEventListener("click", () => save(false));
  $("#deployStop").addEventListener("click", stopDeploy);
  $("#deployLogBtn").addEventListener("click", () => { state.deployLogOpen = !state.deployLogOpen; renderDeploy(); });
  $("#vpForm").addEventListener("submit", unlockVault);
  $("#vpCancel").addEventListener("click", () => closeVaultPrompt());
  $("#vaultPrompt").addEventListener("mousedown", (ev) => { if (ev.target === ev.currentTarget) closeVaultPrompt(); });
  $("#publishForm").addEventListener("submit", publishRodeo);
  $("#pubCancel").addEventListener("click", closePublish);
  $("#pubLoginBtn").addEventListener("click", githubLogin);
  $("#pubUser").addEventListener("input", renderPublishSteps);
  $("#publishModal").addEventListener("mousedown", (ev) => { if (ev.target === ev.currentTarget) closePublish(); });
  $("#importForm").addEventListener("submit", importWorkshop);
  $("#imCancel").addEventListener("click", closeImport);
  $("#importModal").addEventListener("mousedown", (ev) => { if (ev.target === ev.currentTarget) closeImport(); });
  $("#newForm").addEventListener("submit", createChapter);
  $("#ncCancel").addEventListener("click", closeNewChapter);
  const needInput = $("#ncNeedInput");
  needInput.addEventListener("input", () => { needPick.open = true; needPick.active = 0; renderNeedOptions(); });
  needInput.addEventListener("focus", () => { needPick.open = true; renderNeedOptions(); });
  needInput.addEventListener("blur", () => { needPick.open = false; renderNeedOptions(); });
  needInput.addEventListener("keydown", needKey);
  $("#newModal").addEventListener("mousedown", (ev) => { if (ev.target === ev.currentTarget) closeNewChapter(); });
  $("#embedClose").addEventListener("click", closeEmbed);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Tab") { trapTab(ev); return; }
    if (ev.key !== "Escape") return;
    if (!$("#filterMenu").hidden) { setFilterMenu(false); $("#filterBtn").focus(); }
    else if (state.info) toggleInfo(state.info);
    else if (pop) closePop();
    else if (!$("#vaultPrompt").hidden) closeVaultPrompt();
    else if (!$("#newModal").hidden) closeNewChapter();
    else if (!$("#importModal").hidden) closeImport();
    else if (!$("#publishModal").hidden) closePublish();
    else if (!$("#deployModal").hidden) closeDeploy();
    else if (!$("#embedOverlay").hidden) closeEmbed();
    else if (!$("#editor").hidden) closeEditor();
  });
  bindEditor();
}

async function init() {
  bind();
  try {
    const [engines, workshops, liab] = await Promise.all([apiGet("engines"), apiGet("workshops"), apiGet("labinabox")]);
    state.engines = engines.engines;
    state.capabilities = engines.capabilities;
    state.targets = engines.targets;
    state.contentFormats = engines.content_formats;
    state.workshops = workshops.workshops;
    state.liab = { ...state.liab, ...liab };
    if (!window.RB_STATIC) {
      state.story = await apiGet("story").catch((err) => ({ available: false, error: err.message, engines: [] }));
      state.server = await apiGet("server");
      $("#saveBtn").hidden = !state.server.save;
      state.deploy = await apiGet("deploy").catch((err) => ({ available: false, reason: err.message, current: null }));
      if (state.deploy.current && state.deploy.current.status === "running") pollDeploy();
      state.vault = await apiGet("vault").catch((err) => ({ available: false, reason: err.message, types: [], items: [] }));
      $("#settingsBtn").href = "https://" + location.hostname + "/rodeo-builder/settings/";
      $("#settingsBtn").hidden = false;
      $("#saveBtn").title = "Write this rodeo to " + state.server.profiles_dir + "/<name>/";
    }
    offerDraft();
    render();
  } catch (err) {
    toast("Could not load the builder data: " + err.message, true);
  }
}

document.addEventListener("DOMContentLoaded", init);
