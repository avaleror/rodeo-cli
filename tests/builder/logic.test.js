// Rodeo Builder logic.js — run with `node --test tests/builder/logic.test.js` (tests/test_builder.py does).
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const RB = require(path.join(__dirname, "..", "..", "rodeo", "builder", "htdocs", "logic.js"));

const WELCOME = '# Welcome\n\n<span lang="en" id="w.intro">Saddle up, <span no id="w.user">admin</span>.</span>\n' +
  '<span lang="en" id="w.task" hist="first-ride">Your first ride.</span>\n';

test("parseSpans finds nested spans with offsets and parents", () => {
  const { spans, errors } = RB.parseSpans(WELCOME);
  assert.deepEqual(errors, []);
  assert.equal(spans.length, 3);
  assert.deepEqual(spans.map((s) => s.attrs.id), ["w.intro", "w.user", "w.task"]);
  assert.equal(spans[1].parent, 0);
  assert.equal(spans[1].depth, 1);
  assert.equal(RB.inner(WELCOME, spans[1]), "admin");
  assert.equal(spans[1].attrs.no, true);
});

test("spans inside HTML comments are ignored, offsets kept", () => {
  const text = '<!-- <span lang="en" id="doc"> notes -->\n<span lang="en" id="real">x</span>';
  const { spans, errors } = RB.parseSpans(text);
  assert.deepEqual(errors, []);
  assert.deepEqual(spans.map((s) => s.attrs.id), ["real"]);
  assert.equal(RB.inner(text, spans[0]), "x");
});

test("placeholders are not spans; unbalanced tags are reported", () => {
  assert.equal(RB.parseSpans('<span id="x"/> text').spans.length, 0);
  assert.equal(RB.parseSpans("<span lang=\"en\">open").errors[0].msg, "<span> is never closed");
  assert.equal(RB.parseSpans("text</span>").errors[0].msg, "</span> without an opening tag");
});

test("kind and needsId follow the rmstory truth table", () => {
  assert.equal(RB.kind({ lang: "en" }), "translatable");
  assert.equal(RB.kind({ no: true }), "invariant");
  assert.equal(RB.kind({ lang: "nolang" }), "nolang");
  assert.equal(RB.needsId({ lang: "en" }), true);
  assert.equal(RB.needsId({ lang: "en", no: true }), true);
  assert.equal(RB.needsId({ no: true }), false);
  assert.equal(RB.needsId({ lang: "nolang" }), false);
  assert.equal(RB.needsId({ lang: "nolang", hist: "v" }), true);
  assert.equal(RB.needsId({ hist: "v" }), true);
  assert.equal(RB.needsId({ hist: "v", no: true }), false);
});

test("openTag serialises no, lang, id, hist in that order", () => {
  assert.equal(RB.openTag({ id: "x", no: true }), '<span no id="x">');
  assert.equal(RB.openTag({ hist: "v", id: "x", lang: "en" }), '<span lang="en" id="x" hist="v">');
});

test("withKind switches span types", () => {
  assert.deepEqual(RB.withKind({ lang: "de", id: "x" }, "invariant"), { id: "x", no: true });
  assert.deepEqual(RB.withKind({ no: true, id: "x" }, "translatable"), { id: "x", lang: "en" });
  assert.deepEqual(RB.withKind({ lang: "en" }, "nolang"), { lang: "nolang" });
  assert.deepEqual(RB.withKind({ lang: "en" }, "translatable", "fr"), { lang: "fr" });
});

test("wrapTarget trims, refuses tags and partial overlaps, finds the parent", () => {
  const { spans } = RB.parseSpans(WELCOME);
  const at = (t) => WELCOME.indexOf(t);
  const inside = RB.wrapTarget(WELCOME, spans, at("Saddle"), at("Saddle") + 7);
  assert.equal(inside.ok, true);
  assert.equal(WELCOME.slice(inside.s, inside.e), "Saddle");
  assert.equal(inside.parent.attrs.id, "w.intro");
  assert.equal(RB.wrapTarget(WELCOME, spans, at("Saddle"), at("admin") - 1).reason, "selection cuts through a tag");
  assert.equal(RB.wrapTarget(WELCOME, spans, at("Saddle"), at("Your")).reason, "selection partially overlaps a span");
  const whole = RB.wrapTarget(WELCOME, spans, spans[0].start, spans[0].end);
  assert.equal(whole.ok, true);
  assert.equal(whole.parent, null);
  assert.equal(RB.wrapTarget(WELCOME, spans, spans[1].openEnd, spans[1].closeStart).same.attrs.id, "w.user");
  assert.equal(RB.wrapTarget(WELCOME, spans, at("\n\n"), at("\n\n") + 2).reason, "empty selection");
});

test("wrap, retag and unwrap edit the markup", () => {
  const text = "Hello world";
  const wrapped = RB.wrap(text, 6, 11, { lang: "en", id: "c.1" });
  assert.equal(wrapped, 'Hello <span lang="en" id="c.1">world</span>');
  const span = RB.parseSpans(wrapped).spans[0];
  assert.equal(RB.retag(wrapped, span, { no: true, id: "c.1" }), 'Hello <span no id="c.1">world</span>');
  assert.equal(RB.unwrap(wrapped, span), text);
  assert.equal(RB.nextId("c", new Set(["c.1", "c.2"])), "c.3");
});

test("validate reports missing ids, id clashes and hist with no", () => {
  const chapters = [
    { id: "a", body: '<span lang="en">x</span><span lang="en" id="s">one</span>' },
    { id: "b", body: '<span lang="en" id="s">two</span><span lang="en" id="t">t</span><span lang="en" id="t">t</span>' +
      '<span no hist="v" id="h">h</span>' },
  ];
  const msgs = RB.validate(chapters).map((w) => w.chapter + ":" + w.id + ":" + w.msg);
  assert.deepEqual(msgs, ["a::id required", "b:s:same id, different text", "b:h:hist ignored: no is present"]);
});

test("story indexes and variants", () => {
  const chapters = [{ id: "w", body: WELCOME }, { id: "z", body: '<span lang="en" id="z.1" hist="first-ride">z</span>' +
    '<span lang="en" id="z.2" hist="other">o</span><span no hist="first-ride" id="z.3">n</span>' }];
  assert.deepEqual(RB.variantsIn(chapters), ["first-ride", "other"]);
  assert.deepEqual(RB.storyIndex(chapters, "first-ride"), ["w.task", "z.1"]);
  assert.deepEqual([...RB.idsIn(chapters)].sort(), ["w.intro", "w.task", "w.user", "z.1", "z.2", "z.3"]);
});

test("planFromBase sets name and target and replaces the story block", () => {
  const base = "# comment\ntype: rancher\nname: rancher-lab\n\nstory:\n  id: old\n  language: de\nresources:\n  rancher: {vcpu: 4}\n";
  const plan = RB.planFromBase(base, { name: "my-rodeo", target: "instruqt", story: { language: "es", id: "first-ride" } });
  assert.match(plan, /^name: my-rodeo$/m);
  assert.match(plan, /^type: rancher\ndeployment_target: instruqt$/m);
  assert.doesNotMatch(plan, /id: old/);
  assert.match(plan, /^resources:\n {2}rancher: \{vcpu: 4\}$/m);
  assert.match(plan, /story:\n {2}language: es\n {2}id: first-ride\n$/);
  assert.match(plan, /^# comment$/m);
});

test("planLabinabox places add-ons on vm1 and quotes what YAML would misread", () => {
  const plan = RB.planLabinabox({ name: "liab", target: "baremetal", addons: ["smlm", "client_registration"],
    story: { language: "en" } });
  assert.match(plan, /^type: lab-in-a-box$/m);
  assert.match(plan, /^ {4}vm1:\n {6}addons: \[smlm, client_registration\]$/m);
  assert.match(plan, /ref: latest/);
  assert.equal(RB.storyBlock({ language: "no", id: "yes" }), 'story:\n  language: "no"\n  id: "yes"\n');
});

test("files lay out the profile directory", () => {
  const rodeo = {
    name: "demo", title: "Demo", base: "rancher", plan: "type: rancher\n", definition: "",
    chapters: [{ id: "welcome", title: "Welcome", mins: 10, needs: ["rancher"], check: true, body: WELCOME }],
    variants: ["first-ride"], story: { language: "en", id: "first-ride" }, labJson: null,
  };
  const out = Object.fromEntries(RB.files(rodeo).map((f) => [f.path, f]));
  assert.deepEqual(Object.keys(out).sort(), ["README.md", "builder.yaml", "checks/check-welcome.sh", "rodeo-plan.yaml",
    "story/01-welcome.md", "story/chapters.yaml", "story/stories/first-ride.yaml"]);
  assert.match(out["builder.yaml"].content, /^base: rancher$/m);
  assert.match(out["story/stories/first-ride.yaml"].content, /^- w\.task$/m);
  assert.match(out["story/chapters.yaml"].content, /file: 01-welcome\.md\n {4}mins: 10\n {4}needs: \[rancher\]\n {4}check: check-welcome\.sh/);
  assert.equal(out["checks/check-welcome.sh"].mode, 0o755);
  assert.match(out["README.md"].content, /rodeo new demo --from-zip demo\.zip/);
  const bare = RB.files({ ...rodeo, base: "", chapters: [], variants: [] }).map((f) => f.path);
  assert.deepEqual(bare.sort(), ["README.md", "rodeo-plan.yaml"]);
});

test("a chapter's own check script replaces the stub", () => {
  const stub = RB.checkScript({ id: "a", title: "A" });
  assert.match(stub, /no check written yet/);
  assert.equal(RB.checkScript({ id: "a", title: "A", check_script: "#!/bin/sh\nexit 0\n" }), "#!/bin/sh\nexit 0\n");
});

test("labJsonAddons reads add-on names from a lab.json", () => {
  const lab = { nodes: { "a.lab": { addons: ["smlm", { client_registration: {} }] }, "b.lab": {} } };
  assert.deepEqual(RB.labJsonAddons(lab), ["client_registration", "smlm"]);
});

test("zip writes a stored archive unzip can read", () => {
  const bytes = RB.zip([{ path: "x/a.txt", content: "hello\n" }, { path: "x/b.sh", content: "#!/bin/sh\n", mode: 0o755 }],
    new Date(2026, 9, 5, 12, 0, 0));
  const view = new DataView(bytes.buffer);
  assert.equal(view.getUint32(0, true), 0x04034b50);
  assert.equal(view.getUint32(bytes.length - 22, true), 0x06054b50);
  assert.equal(view.getUint16(bytes.length - 22 + 10, true), 2);
  assert.equal(RB.crc32(new TextEncoder().encode("hello\n")), 0x363a3020);
  if (process.env.RB_ZIP_OUT) require("node:fs").writeFileSync(process.env.RB_ZIP_OUT, bytes);
});

test("slugify and uniqueId", () => {
  assert.equal(RB.slugify("  Café: Fleet & GitOps!"), "cafe-fleet-gitops");
  assert.equal(RB.uniqueId("intro", new Set(["intro", "intro-2"])), "intro-3");
});
