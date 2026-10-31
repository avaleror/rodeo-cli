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

test("story counts, the main story and making every story one", () => {
  const body = '<span lang="en" id="a.1" hist="b">x<span lang="en" id="a.2" hist="c">y</span></span><span lang="en" id="a.3" hist="b">z</span>';
  const chapters = [{ id: "a", body }, { id: "n", body: "plain" }];
  assert.deepEqual(RB.storyCounts(chapters), { b: 2, c: 1 });
  assert.equal(RB.mainStory(chapters), "b");
  assert.equal(RB.mainStory([{ id: "t", body: '<span id="1" hist="z">1</span><span id="2" hist="y">2</span>' }]), "y");
  assert.equal(RB.mainStory([chapters[1]]), "");
  const one = RB.renameStories(body, "b");
  assert.deepEqual(RB.storyCounts([{ id: "a", body: one }]), { b: 3 });
  assert.equal(one, body.replace('hist="c"', 'hist="b"'));
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

test("missing_addon chapters are listed as work needed in the README", () => {
  const rodeo = { name: "demo", title: "Demo", base: "", plan: "type: rancher\n", definition: "", variants: [],
    story: { language: "en" }, labJson: null,
    chapters: [{ id: "later", title: "Not yet possible", mins: 5, needs: [RB.MISSING_ADDON], check: false, body: "# x\n" },
      { id: "now", title: "Possible", mins: 5, needs: ["rancher"], check: false, body: "# y\n" }] };
  const readme = RB.files(rodeo).find((f) => f.path === "README.md").content;
  assert.match(readme, /## Work needed[\s\S]*- Not yet possible \(`later`\)/);
  assert.doesNotMatch(readme, /- Possible/);
  assert.doesNotMatch(RB.files({ ...rodeo, chapters: [rodeo.chapters[1]] }).find((f) => f.path === "README.md").content, /Work needed/);
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

test("warningsByChapter counts validate() output per chapter", () => {
  const chapters = [
    { id: "a", body: '<span lang="en">no id</span> <span lang="en">again</span>' },
    { id: "b", body: '<span lang="en" id="x">ok</span>' },
  ];
  assert.deepEqual(RB.warningsByChapter(RB.validate(chapters)), { a: 2 });
  assert.deepEqual(RB.warningsByChapter([]), {});
});

test("engineNeeds lists the engines the chapters' workshops run on", () => {
  const groups = [
    { id: "virt", title: "Virtualization workshop", engine: "suse-virt" },
    { id: "smlm", title: "SMLM workshop", engine: "lab-in-a-box" },
    { id: "custom", title: "My chapters" },
  ];
  const ch = (from) => ({ from });
  assert.deepEqual(RB.engineNeeds([ch("virt/a"), ch("virt/b"), ch("custom/c")], groups),
    [{ engine: "suse-virt", workshops: ["Virtualization workshop"] }]);
  assert.deepEqual(RB.engineNeeds([ch("virt/a"), ch("smlm/b")], groups).map((n) => n.engine), ["lab-in-a-box", "suse-virt"]);
  assert.deepEqual(RB.engineNeeds([ch("custom/c"), {}], groups), []);
});

test("renumber gives drafted spans the chapter's ids, nested under their parent", () => {
  const draft = '<span lang="en" id="ch3.open">Go to <span no id="ch3.url">{{ rancher_url }}</span> now.</span>\n' +
    '<span lang="en">Log in.</span> plain <span no>x</span>\n';
  const out = RB.renumber(draft, "login");
  const { spans } = RB.parseSpans(out);
  assert.deepEqual(spans.map((s) => s.attrs.id), ["login.1", "login.1.1", "login.2", undefined]);
  assert.ok(out.includes("{{ rancher_url }}") && out.includes("Log in."));
  assert.deepEqual(RB.validate([{ id: "login", body: out }]), []);
});

test("translation rows: ids, stored source with placeholders, missing placeholders", () => {
  const chapters = [{ id: "w", body: WELCOME }];
  assert.deepEqual(RB.translatableIds(chapters), ["w.intro", "w.task"]);
  const src = RB.sourceTexts(chapters);
  assert.equal(src["w.intro"], 'Saddle up, <span id="w.user"/>.');
  assert.deepEqual(RB.missingPlaceholders(src["w.intro"], "Aufsitzen."), ['<span id="w.user"/>']);
  assert.deepEqual(RB.missingPlaceholders(src["w.intro"], 'Aufsitzen, <span id="w.user"/>.'), []);
});

test("the translation store ships as rmstory's filesystem layout", () => {
  const strings = {
    de: { "w.intro": { content: 'Aufsitzen, <span id="w.user"/>.', status: "reviewed", updated_by: "me" },
      "gone.1": { content: "weg", status: "draft" } },
    en: { "w.intro": { content: "Saddle up", status: "draft" } },
  };
  const out = RB.stringsFiles(strings, ["w.intro", "w.task"], "2026-10-19T00:00:00Z");
  assert.deepEqual(out.map((f) => f.path), ["story/strings/de/w.intro/@default/content.json"]);
  assert.deepEqual(JSON.parse(out[0].content), { content: 'Aufsitzen, <span id="w.user"/>.', original_language: null,
    status: "reviewed", source_checksum: null, updated_by: "me", date_updated: "2026-10-19T00:00:00Z" });
  const rodeo = { name: "ride", title: "Ride", base: "", plan: "type: rancher\n", definition: "", labJson: null,
    chapters: [{ id: "w", title: "W", body: WELCOME }], variants: [], story: { language: "en", id: "" }, strings, date: "d" };
  const files = RB.files(rodeo);
  assert.ok(files.some((f) => f.path === "story/strings/de/w.intro/@default/content.json"));
  assert.match(files.find((f) => f.path === "README.md").content, /Translations: `de`, in `story\/strings\/<lang>\/` \(a published repository keeps each one in a branch/);
  assert.deepEqual(RB.translatedLanguages(strings, ["w.task"]), []);
});

test("availableLanguages lists the source first, then the rodeo's own languages, never a fixed list", () => {
  assert.deepEqual(RB.availableLanguages({}, []), ["en"]);
  assert.deepEqual(RB.availableLanguages({ es: {}, de: { x: {} }, en: {} }, ["fr", "", "es"]), ["en", "de", "es", "fr"]);
});

test("newLanguageError takes language-branch tags not already in the rodeo", () => {
  assert.equal(RB.newLanguageError("pt-br", ["en", "es"]), "");
  assert.match(RB.newLanguageError("", []), /Type a language code/);
  assert.match(RB.newLanguageError("Spanish", []), /two lowercase letters/);
  assert.match(RB.newLanguageError("spa", []), /two lowercase letters/);
  assert.match(RB.newLanguageError("en", ["en"]), /source language/);
  assert.match(RB.newLanguageError("es", ["en", "es"]), /has es already/);
});

test("labUnknownFields reports what a lab-in-a-box schema lacks, like rodeo's unknown_fields", () => {
  const old = { common: ["ISO_IMAGE"], nodes: ["myip", "addons"], kclusters: ["clu_type"],
    addons: { smlm: ["smlm_admin_pass"] } };
  const lab = { common: { ISO_IMAGE: "x", VM_ROOT_PASS: "p", ISO_URL: "https://x" },
    nodes: { a: { myip: "1", ssh_pwauth: "true", ISO_SHA256_URL: "https://x/s",
      addons: ["smlm", { client_registration: { x: 1 } }, { smlm: { smlm_admin_pass: "p", smlm_new: 1 } }] } },
    kclusters: { k: { clu_type: "rke2", clu_rel: "v1" } },
    smlm: { smlm_admin_pass: "p", smlm_preinstalled: "true" }, pxe: {} };
  assert.deepEqual(RB.labUnknownFields(lab, old), [
    "common.VM_ROOT_PASS", "nodes.a.ssh_pwauth", "nodes.a.addons.client_registration", "nodes.a.addons.smlm.smlm_new",
    "kclusters.k.clu_rel", "smlm.smlm_preinstalled", "pxe"]);
  assert.deepEqual(RB.labUnknownFields({ nodes: {} }, { common: [], nodes: [] }), []);
});

test("withStrings adds a library group's rows for the chapter's spans, keeping the rodeo's own", () => {
  const mine = { es: { "w.intro": { content: "Mío", status: "reviewed" } } };
  const add = {
    es: { "w.intro": { content: "Suyo", status: "draft" }, "w.task": { content: "Tarea", status: "draft" }, "x.1": { content: "x" } },
    de: { "w.task": { content: "Aufgabe", status: "draft" } },
    fr: { "x.1": { content: "x" } },
    en: { "w.task": { content: "Task" } },
  };
  assert.deepEqual(RB.withStrings(mine, add, ["w.intro", "w.task"]), {
    es: { "w.intro": { content: "Mío", status: "reviewed" }, "w.task": { content: "Tarea", status: "draft" } },
    de: { "w.task": { content: "Aufgabe", status: "draft" } },
  });
  assert.deepEqual(mine, { es: { "w.intro": { content: "Mío", status: "reviewed" } } });
  assert.deepEqual(RB.withStrings({}, undefined, ["w.intro"]), {});
});

test("draftPrompt grounds the brief in the rodeo and the lab", () => {
  const p = RB.draftPrompt("  Import a cluster.  ", { title: "Import", rodeo: "Ride", provides: ["rancher", "fleet"],
    chapters: ["Welcome"], mins: 15 });
  assert.match(p, /Chapter title: Import\./);
  assert.match(p, /The lab provides: rancher, fleet\./);
  assert.match(p, /Chapters before this one: Welcome\./);
  assert.match(p, /about 15 minutes/);
  assert.match(p, /<span no>/);
  assert.ok(p.endsWith("Import a cluster."));
});

test("parseSourceUrl and importGroupId match the server's (rodeo/builder/imports.py)", () => {
  assert.deepEqual(RB.parseSourceUrl("https://github.com/acme/labs/tree/v2/docs/exercises/"),
    { host: "github.com", owner: "acme", repo: "labs", ref: "v2", path: "docs/exercises" });
  assert.deepEqual(RB.parseSourceUrl("https://github.com/acme/labs.git"),
    { host: "github.com", owner: "acme", repo: "labs", ref: "", path: "" });
  for (const bad of ["http://github.com/a/b", "https://u:p@github.com/a/b", "https://github.com/a",
    "https://github.com/a/b/tree/main/../x", "https://github.com/a/b?x=1", "nope"]) {
    assert.throws(() => RB.parseSourceUrl(bad), Error, bad);
  }
  assert.equal(RB.importGroupId(RB.parseSourceUrl("https://github.com/acme/labs/tree/main/ws")), "url-acme-labs-ws");
});

test("detectFormat over a repository's file list", () => {
  assert.deepEqual(RB.detectFormat(["ws/story/01-a.md", "ws/README.md"], "ws"), { format: "rmstory", dir: "ws/story" });
  assert.deepEqual(RB.detectFormat(["ws/chapters.yaml", "ws/01-a.md"], "ws"), { format: "rmstory", dir: "ws" });
  assert.deepEqual(RB.detectFormat(["t/01-x/assignment.md", "t/track.yml"], "t"), { format: "instruqt", dir: "t" });
  assert.deepEqual(RB.detectFormat(["README.md", "docs/x.md"], ""), { format: "markdown", dir: "" });
  assert.throws(() => RB.detectFormat(["ws/notes.txt", "ws/deep/x.md"], "ws"), /no chapters found/);
});

test("chapters from markdown, Instruqt and rodeo story files", () => {
  const md = RB.chapterFromMarkdown("02-go.md", "# Go <b>now</b>\n\n**Time:** 25 min\n");
  assert.deepEqual([md.id, md.title, md.mins, md.check], ["go", "Go now", 25, false]);
  const iq = RB.chapterFromInstruqt("01-intro", "---\ntitle: \"Intro\"\nslug: intro\n---\n\nText\n", "#!/bin/sh\n");
  assert.deepEqual([iq.id, iq.title, iq.mins, iq.check, iq.body], ["intro", "Intro", 20, true, "Text\n"]);
  const meta = RB.chaptersMeta("title: Acme ride\nchapters:\n  - file: 01-hello.md\n    mins: 7\n    needs: [rancher, fleet]\n");
  assert.equal(meta.title, "Acme ride");
  const st = RB.chapterFromStory("01-hello.md", '# Hello\n<span lang="en" id="hello.1">Hi</span>\n', meta);
  assert.deepEqual([st.id, st.mins, st.needs, st.spans], ["hello", 7, ["rancher", "fleet"], 1]);
  assert.deepEqual(RB.storyIds("# comment\n- hello.1\n- \"hello.2\"\n"), ["hello.1", "hello.2"]);
});

test("storyRole follows the author's tags: hist is story, lang is exercise, no/nolang is fixed", () => {
  assert.equal(RB.storyRole({ lang: "en", hist: "main" }), "story");
  assert.equal(RB.storyRole({ hist: "main" }), "story");
  assert.equal(RB.storyRole({ lang: "en" }), "exercise");
  assert.equal(RB.storyRole({ no: true, hist: "main" }), "fixed");
  assert.equal(RB.storyRole({ lang: "nolang" }), "fixed");
  assert.deepEqual(RB.storyParts([{ body: '<span lang="en" hist="a" id="s">x</span><span lang="en" id="e">y</span><span no>z</span>' }]),
    { story: 1, exercise: 1, fixed: 1 });
});

test("retelling hides exercises from the engine, restores them and renames only story ids", () => {
  const body = '# Ride\n<span lang="en" hist="main" id="ride.1">Saddle up, <span id="ride.2"/></span>\n' +
    '<span lang="en" id="ride.2">Open Cluster Management</span>\n<span lang="en" id="ride.3">Run <span no id="ride.4">kubectl get nodes</span></span>\n' +
    '<span lang="en" hist="main" id="ride.5">The trail <span lang="en" id="ride.6">Click Charts</span> ends.</span>\n';
  const masked = RB.maskExercises(body);
  assert.ok(!masked.body.includes("Cluster Management") && !masked.body.includes("kubectl"));
  assert.ok(masked.body.includes("Click Charts"));
  const fromEngine = masked.body.replace("Saddle up, ", "Boots on, ").replace("The trail ", "The vault ").replace("Click Charts", "Crack the safe");
  const [out] = RB.retoldChapters([{ id: "ride", title: "Ride", mins: 5, body }], { ride: fromEngine }, "heist", { ride: masked.kept });
  assert.equal(out.id, "ride-heist");
  assert.equal(out.mins, 5);
  assert.equal(out.body, '# Ride\n<span lang="en" hist="main" id="heist.ride.1">Boots on, <span id="ride.2"/></span>\n' +
    '<span lang="en" id="ride.2">Open Cluster Management</span>\n<span lang="en" id="ride.3">Run <span no id="ride.4">kubectl get nodes</span></span>\n' +
    '<span lang="en" hist="main" id="heist.ride.5">The vault <span lang="en" id="ride.6">Click Charts</span> ends.</span>\n');
});

test("nameMissing names only the spans that need an id and have none", () => {
  const body = '<span lang="en" id="a.1">x</span> <span lang="en">y</span> <span no>z</span> <span hist="s">w</span>';
  assert.equal(RB.nameMissing(body, "a"), '<span lang="en" id="a.1">x</span> <span lang="en" id="a.2">y</span> <span no>z</span> <span id="a.3" hist="s">w</span>');
});

test("publishLinks builds github.com pages that use the browser's login", () => {
  const l = RB.publishLinks({ user: "rider", name: "ride", title: "Ride & go", engine: "rancher", languages: ["en", "es"] });
  assert.equal(l.repo, "https://github.com/rider/ride");
  assert.equal(l.create, "https://github.com/new?name=ride&description=Ride%20%26%20go&visibility=public");
  assert.equal(l.upload, "https://github.com/rider/ride/upload/main");
  const u = new URL(l.catalog);
  assert.equal(u.origin + u.pathname, "https://github.com/avaleror/rodeo-cli/new/main");
  assert.equal(u.searchParams.get("filename"), "rodeo/data/catalog.d/ride.yaml");
  assert.equal(u.searchParams.get("value"), 'name: "ride"\ntitle: "Ride & go"\nrepo: "https://github.com/rider/ride"\nref: main\nengine: "rancher"\nlanguages: ["en", "es"]\n');
});

test("contentBlock and planFromBase put the instructions' repository in the plan", () => {
  assert.equal(RB.contentBlock(null), "");
  assert.equal(RB.contentBlock({ repo: "https://github.com/a/b", ref: "v1", path: "story", format: "rmstory" }),
    'content:\n  repo: "https://github.com/a/b"\n  ref: v1\n  path: story\n  format: rmstory\n');
  const base = "type: rancher\nname: x\ncontent:\n  repo: https://old/a/b\n  format: markdown\n";
  const opts = { name: "ride", target: "baremetal", story: { language: "en" } };
  assert.ok(RB.planFromBase(base, opts).includes("repo: https://old/a/b"));
  const out = RB.planFromBase(base, { ...opts, content: { repo: "https://github.com/a/b", format: "rmstory" } });
  assert.ok(!out.includes("https://old/a/b") && out.endsWith('content:\n  repo: "https://github.com/a/b"\n  format: rmstory\n'));
  assert.equal(RB.planFromBase("type: rancher\n", opts), RB.planFromBase("type: rancher\n", { ...opts, content: null }));
});

test("partFiles splits a rodeo into infrastructure and instructions, each with its own README", () => {
  const rodeo = { name: "ride", title: "Ride", base: "rancher", plan: "type: rancher\n", definition: "", labJson: null, strings: {},
    chapters: [{ id: "intro", title: "Intro", mins: 5, needs: [], body: "# Intro\n", check: true, check_script: "#!/bin/sh\n" }],
    variants: [], story: { language: "en", id: "" }, content: null };
  const infra = RB.partFiles(rodeo, "infrastructure").map((f) => f.path);
  const instr = RB.partFiles(rodeo, "instructions").map((f) => f.path);
  assert.deepEqual(infra, ["rodeo-plan.yaml", "builder.yaml", "README.md"]);
  assert.deepEqual(instr, ["story/01-intro.md", "story/chapters.yaml", "checks/check-intro.sh", "README.md"]);
  assert.match(RB.partFiles(rodeo, "instructions").at(-1).content, /rodeo content apply/);
  assert.match(RB.partFiles(rodeo, "infrastructure").at(-1).content, /not part of this download/);
  assert.equal(RB.partOf("story/strings/es/a/@default/content.json"), "instructions");
});

// The preview tree as compact markup: [text] for a text leaf, <SPn> (<SPn+> for a continuation) for a rodeo span,
// {label} for a generated label.
function previewMarkup(body) {
  const { spans } = RB.parseSpans(body);
  const show = (nodes) => nodes.map((n) => {
    if (n.label !== undefined) return "{" + n.label + "}";
    if (n.span !== undefined) return "<SP" + n.span + (n.cont ? "+" : "") + ">" + show(n.children) + "</SP>";
    if (n.tag) return "<" + n.tag + ">" + show(n.children) + "</" + n.tag + ">";
    return "[" + (n.text !== undefined ? n.text : body.slice(n.s, n.e)) + "]";
  }).join("");
  return show(RB.previewTree(body, spans).nodes);
}

test("previewFormat: HTML when the first content is a block-level tag", () => {
  assert.equal(RB.previewFormat("# Title\n\ntext"), "markdown");
  assert.equal(RB.previewFormat("plain text"), "markdown");
  assert.equal(RB.previewFormat("Some <b>inline</b> html"), "markdown");
  assert.equal(RB.previewFormat('---\nslug: x\n---\n<!-- c -->\n<span lang="en" id="a"><p>x</p></span>'), "html");
  assert.equal(RB.previewFormat("<!DOCTYPE html><html>"), "html");
});

test("previewTree renders Markdown and keeps every text leaf on its source", () => {
  assert.equal(previewMarkup("---\nslug: x\n---\n# Hi *there*\n\n- a\n- **b**\n\n```\n<span no>x</span>\n```\n"),
    "<pre>[---\nslug: x\n---]</pre><h1>[Hi ]<em>[there]</em></h1><ul><li><p>[a]</p></li><li><p><strong>[b]</strong></p></li></ul>" +
    "<pre><code><SP0>[x]</SP></code></pre>");
  assert.equal(previewMarkup("> q\n\n1. one\n2. `c` \\*e\\*\n\n---\n[l](https://x) &amp;"),
    "<blockquote><p>[q]</p></blockquote><ol><li><p>[one]</p></li><li><p><code>[c]</code>[ ][*e][*]</p></li></ol><hr></hr>" +
    "<p><a>[l]</a>[ ][&]</p>");
});

test("previewTree splits a span at block boundaries and crossing tags, badge part first", () => {
  assert.equal(previewMarkup('<span lang="en" id="a">\nOne.\n\nTwo.\n</span>'), "<p><SP0>[One.]</SP></p><p><SP0+>[Two.\n]</SP></p>");
  assert.equal(previewMarkup("**a <span no>b** c</span>"), "<p><strong>[a ]<SP0>[b]</SP></strong><SP0+>[ c]</SP></p>");
});

test("previewTree renders setext headings; their underline is hidden", () => {
  assert.equal(previewMarkup("Title\n====="), "<h1>[Title]</h1>");
  assert.equal(previewMarkup("Two\nlines\n  ---  \n\nnext"), "<h2>[Two\nlines]</h2><p>[next]</p>");
  assert.equal(previewMarkup("Sub\n-"), "<h2>[Sub]</h2>");
  const sample = '🗺 Explore <b class="smlmext">SUSE Multi-Linux Manager</b> 🗺\n=====================================';
  assert.equal(previewMarkup(sample), "<h1>[🗺 Explore ]<b>[SUSE Multi-Linux Manager]</b>[ 🗺]</h1>");
  assert.equal(previewMarkup('<span lang="en" id="t">Tagged</span>\n==='), "<h1><SP0>[Tagged]</SP></h1>");
});

test("previewTree: setext underlines versus rules, list items and lazy lines", () => {
  assert.equal(previewMarkup("Para\n\n---"), "<p>[Para]</p><hr></hr>");
  assert.equal(previewMarkup("Para\n- - -"), "<p>[Para]</p><hr></hr>");
  assert.equal(previewMarkup("Para\n***"), "<p>[Para]</p><hr></hr>");
  assert.equal(previewMarkup("Para\n- item"), "<p>[Para]</p><ul><li><p>[item]</p></li></ul>");
  assert.equal(previewMarkup("Para\n    ==="), "<p>[Para\n    ===]</p>");
  assert.equal(previewMarkup("Para\n= ="), "<p>[Para\n= =]</p>");
  assert.equal(previewMarkup("- item\n---"), "<ul><li><p>[item]</p></li></ul><hr></hr>");
  assert.equal(previewMarkup("- item\n-"), "<ul><li><p>[item]</p></li><li></li></ul>");
  assert.equal(previewMarkup("- item\n  ---"), "<ul><li><h2>[item]</h2></li></ul>");
  assert.equal(previewMarkup("> quote\n---"), "<blockquote><p>[quote]</p></blockquote><hr></hr>");
  assert.equal(previewMarkup("> quote\nlazy\n==="), "<blockquote><p>[quote\nlazy\n===]</p></blockquote>");
  assert.equal(previewMarkup("> quote\n> ==="), "<blockquote><h1>[quote]</h1></blockquote>");
});

test("previewTree renders GitHub alerts as callouts with a generated title", () => {
  const kinds = { NOTE: "Note", tip: "Tip", Important: "Important", WARNING: "Warning", caution: "Caution" };
  for (const [marker, title] of Object.entries(kinds)) {
    const body = "> [!" + marker + "]  \n> Body *text*.";
    assert.equal(previewMarkup(body), "<blockquote><p>{" + title + "}</p><p>[Body ]<em>[text]</em>[.]</p></blockquote>");
    const bq = RB.previewTree(body, []).nodes[0];
    assert.equal(bq.attrs.class, "callout callout-" + marker.toLowerCase());
    assert.equal(bq.children[0].attrs.class, "callout-title");
  }
  assert.equal(previewMarkup("> [!NOTE]\n> If you wish to access the lab,\n> use the link."),
    "<blockquote><p>{Note}</p><p>[If you wish to access the lab,\n][use the link.]</p></blockquote>");
  assert.equal(previewMarkup("> [!TIP] Same line.\n> More."), "<blockquote><p>{Tip}</p><p>[Same line.\n][More.]</p></blockquote>");
  assert.equal(previewMarkup('> [!WARNING]\n> <span lang="en" id="w">Careful</span>'),
    "<blockquote><p>{Warning}</p><p><SP0>[Careful]</SP></p></blockquote>");
  assert.equal(previewMarkup("> [!NOTE]x"), "<blockquote><p>[[!NOTE]x]</p></blockquote>");
  assert.equal(previewMarkup("> [!OTHER]\n> x"), "<blockquote><p>[[!OTHER]\n][x]</p></blockquote>");
  assert.equal(previewMarkup("> text\n> [!NOTE]"), "<blockquote><p>[text\n][[!NOTE]]</p></blockquote>");
  assert.deepEqual(RB.previewTree("> plain", []).nodes[0].attrs, {});
});

test("previewTree keeps only allowed HTML", () => {
  const body = '<p onclick="x()">A <a href="javascript:x()">bad</a> <a href="https://ok">ok</a></p><script>s()</script>' +
    '<iframe src="x"></iframe><img src="https://i/x.png" alt="pic"><font>kept</font>';
  assert.equal(previewMarkup(body), "<p>[A ]<a>[bad]</a>[ ]<a>[ok]</a></p><a>[pic]</a>[kept]");
  const { nodes } = RB.previewTree(body, []);
  assert.deepEqual(nodes[0].attrs, {});
  assert.deepEqual(nodes[0].children[1].attrs, {});
  assert.deepEqual(nodes[0].children[3].attrs, { href: "https://ok", rel: "noopener", target: "_blank" });
  assert.equal(nodes[1].attrs.class, "md-img");
  assert.equal(RB.safeHref("java\tscript:alert(1)"), null);
  assert.equal(RB.safeHref("docs/x.md#a"), "docs/x.md#a");
});

const VAULT = {
  types: [
    { id: "aws", label: "AWS", fields: [{ name: "access_key_id", label: "Access key ID", secret: false },
      { name: "secret_access_key", label: "Secret access key", secret: true }],
      keys: { aws_access_key_id: "access_key_id", aws_secret_access_key: "secret_access_key" } },
    { id: "scc", label: "SCC", fields: [{ name: "regcode", label: "Registration code", secret: true },
      { name: "email", label: "Email", optional: true }], keys: { scc_regcode: "regcode" } },
    { id: "generic", label: "Generic", fields: [], keys: {}, freeform: true },
  ],
  items: [{ id: "a1", name: "work aws", type: "aws" }, { id: "s1", name: "scc", type: "scc" },
    { id: "g1", name: "misc", type: "generic" }, { id: "x1", name: "gone", type: "removed-type" }],
};

test("vaultCandidates matches a secret to the types that map it, plus every freeform credential", () => {
  const ids = (key) => RB.vaultCandidates(VAULT, key).map((i) => i.id);
  assert.deepEqual(ids("aws_secret_access_key"), ["a1", "g1"]);
  assert.deepEqual(ids("scc_regcode"), ["s1", "g1"]);
  assert.deepEqual(ids("toString"), ["g1"]);
  assert.deepEqual(RB.vaultCandidates({ ...VAULT, items: VAULT.items.slice(0, 2) }, "other"), []);
  assert.deepEqual(RB.vaultCandidates(null, "scc_regcode"), []);
});

test("vaultFill maps an opened credential onto the secrets it provides", () => {
  const aws = RB.vaultType(VAULT, "aws"), generic = RB.vaultType(VAULT, "generic");
  assert.deepEqual(RB.vaultFill(aws, { access_key_id: "AK", secret_access_key: "SK" }, ["aws_access_key_id", "aws_secret_access_key", "scc_regcode"]),
    { aws_access_key_id: "AK", aws_secret_access_key: "SK" });
  assert.deepEqual(RB.vaultFill(aws, { access_key_id: "AK", secret_access_key: "" }, ["aws_secret_access_key"]), {});
  assert.deepEqual(RB.vaultFill(generic, { scc_regcode: "R", other: "O" }, ["scc_regcode", "aws_access_key_id"]), { scc_regcode: "R" });
  assert.deepEqual(RB.vaultFill(generic, {}, ["constructor"]), {});
});

test("vaultFields keeps the filled fields, or the freeform rows", () => {
  const scc = RB.vaultType(VAULT, "scc"), generic = RB.vaultType(VAULT, "generic");
  assert.deepEqual(RB.vaultFields(scc, { fields: { regcode: "R", email: "", stray: "x" } }), { regcode: "R" });
  assert.deepEqual(RB.vaultFields(generic, { rows: [{ key: " token ", value: "T" }] }), { token: "T" });
});

test("vaultErrors checks name, required fields, freeform keys and the password", () => {
  const scc = RB.vaultType(VAULT, "scc"), generic = RB.vaultType(VAULT, "generic");
  const ok = { name: "scc", fields: { regcode: "R" }, password: "longenough", repeat: "longenough" };
  assert.deepEqual(RB.vaultErrors(scc, ok), []);
  assert.deepEqual(RB.vaultErrors(scc, { ...ok, name: " " }), ["Give the credential a name."]);
  assert.deepEqual(RB.vaultErrors(scc, { ...ok, fields: { email: "e" } }), ["Fill in Registration code."]);
  assert.deepEqual(RB.vaultErrors(scc, { ...ok, password: "short", repeat: "short" }), ["The password needs at least 8 characters."]);
  assert.deepEqual(RB.vaultErrors(scc, { ...ok, repeat: "different1" }), ["The passwords don't match."]);
  assert.deepEqual(RB.vaultErrors(null, ok), ["Pick a type."]);
  const free = { ...ok, rows: [{ key: "token", value: "T" }] };
  assert.deepEqual(RB.vaultErrors(generic, free), []);
  assert.deepEqual(RB.vaultErrors(generic, { ...free, rows: [] }), ["Add at least one field."]);
  assert.equal(RB.vaultErrors(generic, { ...free, rows: [{ key: "Bad-Key", value: "x" }] }).length, 1);
  assert.deepEqual(RB.vaultErrors(generic, { ...free, rows: [{ key: "a", value: "1" }, { key: "a", value: "2" }] }), ["Field a is there twice."]);
  assert.deepEqual(RB.vaultErrors(generic, { ...free, rows: [{ key: "a", value: "" }] }), ["Fill in a."]);
  assert.ok(RB.VAULT_KEY_RE.test("a" + "b".repeat(63)) && !RB.VAULT_KEY_RE.test("a" + "b".repeat(64)));
});

test("vaultFileKeys puts the secrets.yaml keys sharing a word with the secret first", () => {
  const all = ["alpha", "harvester_token", "my_regcode", "scc_mirror_password", "zeta"];
  const vault = { file_secrets: ["zeta", "scc_mirror_password", "alpha", "my_regcode", "harvester_token"].map((key) => ({ key })) };
  assert.deepEqual(RB.vaultFileKeys(vault, "scc_regcode"), ["my_regcode", "scc_mirror_password", "alpha", "harvester_token", "zeta"]);
  assert.deepEqual(RB.vaultFileKeys(vault, "other"), all);
  assert.deepEqual(RB.vaultFileKeys(vault, "_"), all);
  assert.deepEqual(RB.vaultFileKeys({ items: [] }, "scc_regcode"), []);
  assert.deepEqual(RB.vaultFileKeys(null, "scc_regcode"), []);
});

test("labJsonProvides lists add-ons, Kubernetes cluster types and other sections", () => {
  const lab = { common: {}, schema_versions: { lab: "2.5" }, pxe: {}, smlm: {},
    nodes: { "a.lab": { addons: ["smlm"] } }, kclusters: { k1: { clu_type: "rke2", addons: ["rancher", { longhorn: {} }] } } };
  assert.deepEqual(RB.labJsonProvides(lab), ["longhorn", "pxe", "rancher", "rke2", "smlm"]);
  assert.deepEqual(RB.labJsonProvides(null), []);
});

test("labWithSecretRefs turns credential values into ??placeholders", () => {
  const lab = {
    common: { ROOT_PWD_HASH: "$6$abc" },
    nodes: { "a.lab": { addons: [{ client_registration: { client_registration_admin_pass: "s3cret", client_registration_activation_key: "1-sles" } }] } },
    kclusters: { k1: { clu_type: "rke2", clu_token: "tok-one-1234567890" }, k2: { clu_type: "rke2", clu_token: "tok-two-1234567890" } },
    smlm: {
      smlm_admin_pass: "??smlm_admin_pass", smlm_scc_regcode: "REG-1", smlm_ssl_password: "",
      smlm_users: [{ username: "a", password: "s3cret" }, { username: "b", password: "other" }],
    },
  };
  const { lab: out, replaced } = RB.labWithSecretRefs(lab, ["smlm_scc_regcode"]);
  assert.equal(out.common.ROOT_PWD_HASH, "??ROOT_PWD_HASH");
  assert.equal(out.nodes["a.lab"].addons[0].client_registration.client_registration_admin_pass, "??client_registration_admin_pass");
  assert.equal(out.nodes["a.lab"].addons[0].client_registration.client_registration_activation_key, "1-sles");
  assert.equal(out.kclusters.k1.clu_token, "??clu_token");
  assert.equal(out.kclusters.k2.clu_token, "??clu_token_2");
  assert.equal(out.smlm.smlm_admin_pass, "??smlm_admin_pass");
  assert.equal(out.smlm.smlm_scc_regcode, "??smlm_scc_regcode_2");
  assert.equal(out.smlm.smlm_ssl_password, "");
  assert.equal(out.smlm.smlm_users[0].password, "??client_registration_admin_pass");
  assert.equal(out.smlm.smlm_users[1].password, "??smlm_users_password");
  assert.deepEqual(replaced, ["ROOT_PWD_HASH", "client_registration_admin_pass", "clu_token", "clu_token_2", "smlm_scc_regcode_2", "smlm_users_password"]);
  assert.ok(!JSON.stringify(out).includes("s3cret"));
});

test("labWithNodeRefs links literal node names and addresses, keeps stored values", () => {
  const lab = {
    common: { mydomain: "rodeo.lab" },
    nodes: {
      "smlm.rodeo.lab": { myip: "192.168.122.20" },
      "c1.rodeo.lab": { myip: "192.168.122.2", addons: [{ client_registration: { client_registration_server: "smlm.rodeo.lab" } }] },
    },
    smlm: { url: "https://smlm.rodeo.lab/pub", ip: "192.168.122.20", near: "192.168.122.201", gw: "192.168.122.1",
      mail: "admin@rodeo.lab", other: "db.rodeo.lab", kept: "smlm.rodeo.lab" },
  };
  const before = { smlm: { kept: "smlm.rodeo.lab" } };
  const { lab: out, warnings } = RB.labWithNodeRefs(lab, before);
  assert.equal(out.smlm.url, "https://${node_fqdn:smlm}/pub");
  assert.equal(out.smlm.ip, "${node_ip:smlm}");
  assert.equal(out.smlm.near, "192.168.122.201");
  assert.equal(out.smlm.mail, "admin@rodeo.lab");
  assert.equal(out.smlm.kept, "smlm.rodeo.lab");
  assert.equal(out.nodes["c1.rodeo.lab"].addons[0].client_registration.client_registration_server, "${node_fqdn:smlm}");
  assert.equal(out.nodes["c1.rodeo.lab"].myip, "192.168.122.2");
  assert.deepEqual(warnings, [
    "smlm.near: 192.168.122.201 is in the lab's network but no node has it",
    "smlm.gw: 192.168.122.1 is in the lab's network but no node has it",
    "smlm.other: db.rodeo.lab is in the lab's domain but no node has that name",
  ]);
});

test("addOperatorSecrets adds keys to the plan's operator_secrets", () => {
  assert.equal(RB.addOperatorSecrets("type: x\n", ["a"]), "type: x\n\noperator_secrets:\n  - a\n");
  assert.equal(RB.addOperatorSecrets("operator_secrets: [a]\nb: 1\n", ["a", "c"]), "operator_secrets: [a, c]\nb: 1\n");
  const block = "operator_secrets:\n  - a   # note\n  - b\n\nresources: {}\n";
  assert.equal(RB.addOperatorSecrets(block, ["b", "c"]), "operator_secrets:\n  - a   # note\n  - b\n  - c\n\nresources: {}\n");
  assert.equal(RB.addOperatorSecrets(block, ["a"]), block);
});

test("labSummary and labDriveWarnings describe a lab", () => {
  const lab = { pxe: {}, kclusters: { hv: { clu_type: "harvester" } },
    nodes: { "h1.lab": { myip: "10.0.0.5", VM_CPU: "8", VM_MEM: "16384", kcluster: "hv", addons: ["rancher", { longhorn: {} }] }, "b.lab": {} } };
  const s = RB.labSummary(lab);
  assert.deepEqual(s.nodes[0], { name: "h1.lab", ip: "10.0.0.5", cpu: "8", mem: "16384", addons: ["rancher", "longhorn"], kcluster: "hv" });
  assert.deepEqual(s.kclusters, [{ name: "hv", type: "harvester" }]);
  const w = RB.labDriveWarnings(lab);
  assert.ok(w[0].startsWith("Harvester nodes (h1.lab)") && w[1].startsWith("pxe:"));
  assert.deepEqual(RB.labDriveWarnings({ nodes: { "a.lab": {} } }), []);
});
