// rodeo-cli Settings page: the credentials stored on this host. Data comes from the CGI script at
// ../admin (rodeo/builder/settings/cgi-bin/settings.py), which needs a user the web server logged
// in. Passwords and values typed here go only into the request that needs them: they are not kept
// in a variable, in storage or in the DOM once a form closes. Pure logic is in logic.js (RB).
"use strict";

const API = "../admin";
const $ = (sel) => document.querySelector(sel);
let store = { types: [], items: [], file_secrets: [] };
let deleting = null;
let toastTimer = null;

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

function fill(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
}

function toast(msg, bad) {
  $("#toastText").textContent = msg;
  $("#toast").classList.toggle("bad", !!bad);
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 7000);
}

// `{status, body}` of a call to the Settings API; a network failure is status 0.
async function call(action, payload) {
  const opts = payload === undefined ? { headers: { Accept: "application/json" } }
    : { method: "POST", body: JSON.stringify(payload), headers: { "Content-Type": "application/json", Accept: "application/json" } };
  try {
    const res = await fetch(API + "?" + new URLSearchParams({ action }).toString(), { credentials: "same-origin", cache: "no-store", ...opts });
    const body = await res.json().catch(() => ({ error: res.statusText }));
    return { status: res.status, body };
  } catch (err) {
    return { status: 0, body: { error: err.message } };
  }
}

function when(v) {
  if (!v) return "never";
  const d = new Date(v);
  return isNaN(d) ? String(v) : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function typeLabel(id) { const t = RB.vaultType(store, id); return t ? t.label : id; }

async function refresh() {
  const res = await call("list");
  $("#loadError").hidden = res.status === 200;
  if (res.status !== 200) {
    $("#loadError").textContent = "Could not load the credentials: " + (res.body.error || res.status) + ".";
    return;
  }
  store = res.body;
  $("#who").textContent = store.user ? "logged in as " + store.user : "";
  render();
}

// The application a credential is for: its type for rodeo's, the file name's `<app>-` part for other engines'.
function appOf(item) { return item.engine === "rodeo" ? typeLabel(item.type) : item.app || item.name; }

function render() {
  const items = (store.items || []).slice().sort((a, b) => appOf(a).localeCompare(appOf(b)) || a.name.localeCompare(b.name));
  fill($("#vaultList"), items.length ? items.map((item) => el("tr", { class: "vault-item" },
    el("th", { scope: "row", class: "vault-name", text: item.name }),
    el("td", {}, el("span", { class: "chip", text: appOf(item) })),
    el("td", { text: item.engine }),
    el("td", { text: item.encrypted === false ? "no" : "yes" }),
    el("td", { class: "vault-when", text: when(item.created) }),
    el("td", { class: "vault-when", text: when(item.used) }),
    el("td", { class: "cred-action" }, item.engine === "rodeo"
      ? el("button", { class: "btn sm", type: "button", text: "Delete", "aria-label": "Delete " + item.name, onclick: () => askDelete(item) })
      : el("span", { class: "vault-when", text: "read-only here", title: "Managed by " + item.engine }))))
    : el("tr", {}, el("td", { class: "vault-empty", colspan: "7", text: "No stored credentials yet." })));
  const keys = store.file_secrets || [];
  fill($("#fileSecretList"), keys.length ? keys.map((f) => el("li", {}, el("span", { class: "chip", text: f.key })))
    : el("li", { class: "vault-empty", text: "No keys there." }));
}

// A password input with a Show/Hide toggle; `what` names it for the toggle's label.
function secretInput(props, what) {
  const input = el("input", { class: "input mono", type: "password", autocomplete: "off", spellcheck: "false", ...props });
  const toggle = el("button", { class: "btn sm", type: "button", text: "Show", "aria-pressed": "false",
    "aria-label": "Show " + what, onclick: () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      toggle.textContent = show ? "Hide" : "Show";
      toggle.setAttribute("aria-pressed", String(show));
    } });
  return el("div", { class: "secret-input" }, input, toggle);
}

function fieldRow() {
  const row = el("div", { class: "vault-row" },
    el("input", { class: "input mono vault-key", "aria-label": "Field name", placeholder: "field_name", autocomplete: "off", spellcheck: "false" }),
    secretInput({ class: "input mono vault-value", "aria-label": "Field value" }, "the field value"),
    el("button", { class: "x-btn", type: "button", "aria-label": "Remove this field", text: "×", onclick: () => row.remove() }));
  return row;
}

function renderFields() {
  const type = RB.vaultType(store, $("#vaType").value);
  if (!type) { fill($("#vaFields")); return; }
  if (type.freeform) {
    const rows = el("div", { id: "vaRows" }, fieldRow());
    fill($("#vaFields"), el("span", { class: "label", text: "Fields" }), rows,
      el("button", { class: "btn sm vault-add-row", type: "button", text: "+ Add field", onclick: () => rows.append(fieldRow()) }));
    return;
  }
  fill($("#vaFields"), type.fields.flatMap((f) => {
    const id = "vaf-" + f.name, props = { id, dataset: { field: f.name } };
    const input = f.multiline ? el("textarea", { class: "textarea mono short", spellcheck: "false", ...props })
      : f.secret ? secretInput(props, f.label) : el("input", { class: "input mono", autocomplete: "off", spellcheck: "false", ...props });
    return [el("label", { class: "label", for: id, text: f.label + (f.optional ? " (optional)" : "") }), input];
  }));
}

// Empties every input of the add form (secret values included) and hides it.
function resetForm() {
  $("#vaultForm").reset();
  $("#vaultForm").hidden = true;
  $("#vaultAddBtn").hidden = false;
  $("#vaType").replaceChildren(...(store.types || []).map((t) => el("option", { value: t.id, text: t.label })));
  fill($("#vaFields"));
  $("#vaResult").textContent = "";
}

function openForm() {
  resetForm();
  renderFields();
  $("#vaultForm").hidden = false;
  $("#vaultAddBtn").hidden = true;
  $("#vaName").focus();
}

async function addCredential(ev) {
  ev.preventDefault();
  const type = RB.vaultType(store, $("#vaType").value);
  const form = {
    name: $("#vaName").value.trim(), password: $("#vaPass").value, repeat: $("#vaPass2").value,
    fields: Object.fromEntries([...$("#vaFields").querySelectorAll("[data-field]")].map((i) => [i.dataset.field, i.value])),
    rows: [...$("#vaFields").querySelectorAll(".vault-row")].map((r) => ({ key: r.querySelector(".vault-key").value, value: r.querySelector(".vault-value").value })),
  };
  const errors = RB.vaultErrors(type, form);
  if (errors.length) { $("#vaResult").textContent = errors.join(" "); return; }
  const btn = $("#vaultForm [type=submit]");
  btn.disabled = true;
  const res = await call("add", { name: form.name, type: type.id, fields: RB.vaultFields(type, form), password: form.password });
  btn.disabled = false;
  if (res.status !== 200) { $("#vaResult").textContent = "Not saved: " + (res.body.error || res.status); return; }
  resetForm();
  $("#vaultAddBtn").focus();
  toast("Stored " + form.name);
  await refresh();
}

function askDelete(item) {
  deleting = { item, back: document.activeElement };
  $("#delTitle").textContent = "Delete " + item.name;
  $("#delPass").value = "";
  $("#delError").textContent = "";
  $("#deletePrompt").hidden = false;
  $("#delPass").focus();
}

function closeDelete() {
  const back = deleting && deleting.back;
  deleting = null;
  $("#delPass").value = "";
  $("#delError").textContent = "";
  $("#deletePrompt").hidden = true;
  if (back && back.isConnected) back.focus();
}

async function deleteCredential(ev) {
  ev.preventDefault();
  if (!deleting) return;
  const password = $("#delPass").value;
  if (!password) { $("#delError").textContent = "Type the password."; return; }
  const item = deleting.item;
  $("#delSubmit").disabled = true;
  const res = await call("delete", { id: item.id, password });
  $("#delSubmit").disabled = false;
  if (res.status === 403 || res.status === 429) {
    $("#delError").textContent = res.status === 403 ? "Wrong password: try again." : res.body.error + ".";
    $("#delPass").select();
    return;
  }
  if (res.status !== 200 && res.status !== 404) { $("#delError").textContent = res.body.error || "Could not delete it (" + res.status + ")."; return; }
  closeDelete();
  toast("Deleted " + item.name);
  await refresh();
}

function showTheme() {
  const dark = window.RBTheme.current() === "dark";
  $("#themeBtn").textContent = dark ? "Light" : "Dark";
  $("#logo").src = dark ? "assets/horseshoe-mark.svg" : "assets/horseshoe-mark-light.svg";
}

function bind() {
  $("#themeBtn").addEventListener("click", () => { window.RBTheme.toggle(); showTheme(); });
  $("#vaultAddBtn").addEventListener("click", openForm);
  $("#vaType").addEventListener("change", renderFields);
  $("#vaultForm").addEventListener("submit", addCredential);
  $("#vaCancel").addEventListener("click", () => { resetForm(); $("#vaultAddBtn").focus(); });
  $("#delForm").addEventListener("submit", deleteCredential);
  $("#delCancel").addEventListener("click", closeDelete);
  $("#deletePrompt").addEventListener("mousedown", (ev) => { if (ev.target === ev.currentTarget) closeDelete(); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !$("#deletePrompt").hidden) closeDelete(); });
}

bind();
showTheme();
refresh().then(resetForm);
