// Rodeo Builder — pure functions (no DOM): rmstory spans, validation, story
// indexes, generated files and the zip download. Loaded by index.html as a
// plain script (window.RB) and by the Node tests as a CommonJS module.
"use strict";

const RB = (() => {
  const SOURCE_LANGUAGE = "en";
  // A chapter need that marks unfinished work: something no engine provides yet. It is never
  // provided, never added to a lab and never blocks; the README lists the chapters that carry it.
  const MISSING_ADDON = "missing_addon";
  const LANGUAGES = ["en", "de", "es", "fr"];
  const LIAB_REPO = "https://github.com/SUSE-Technical-Marketing/lab-in-a-box";
  const LIAB_IMAGE = "openSUSE-Leap-15.6-Minimal-VM.x86_64-Cloud.qcow2";
  const LIAB_IMAGE_URL =
    "https://download.opensuse.org/distribution/leap/15.6/appliances/" + LIAB_IMAGE;

  function slugify(text) {
    return String(text || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
  }

  function uniqueId(base, used) {
    if (!used.has(base)) return base;
    for (let n = 2; ; n++) if (!used.has(base + "-" + n)) return base + "-" + n;
  }

  // ── spans ────────────────────────────────────────────────────────────────

  const ATTR_RE = /([a-zA-Z_][\w-]*)(?:\s*=\s*"([^"]*)")?/g;
  const TAG_RE = /<\/span\s*>|<span\b([^>]*?)(\/?)>/g;

  function parseAttrs(src) {
    const attrs = {};
    let m;
    ATTR_RE.lastIndex = 0;
    while ((m = ATTR_RE.exec(src))) attrs[m[1]] = m[2] === undefined ? true : m[2];
    return attrs;
  }

  // Every <span ...>…</span> pair, in document order, with offsets:
  // start (at "<span"), openEnd (after ">"), closeStart (at "</span"), end.
  // Self-closing placeholders (<span id="x"/>) are not spans, and neither is
  // anything inside an HTML comment (rmstory scans with Python's HTMLParser).
  function parseSpans(text) {
    const spans = [], stack = [], errors = [];
    const scan = text.replace(/<!--[\s\S]*?(-->|$)/g, (c) => " ".repeat(c.length));
    let m;
    TAG_RE.lastIndex = 0;
    while ((m = TAG_RE.exec(scan))) {
      if (m[0][1] === "/") {
        const open = stack.pop();
        if (!open) { errors.push({ at: m.index, msg: "</span> without an opening tag" }); continue; }
        open.closeStart = m.index;
        open.end = m.index + m[0].length;
      } else if (m[2] !== "/") {
        const span = {
          index: spans.length, start: m.index, openEnd: m.index + m[0].length,
          closeStart: -1, end: -1, attrs: parseAttrs(m[1]), depth: stack.length,
          parent: stack.length ? stack[stack.length - 1].index : -1,
        };
        spans.push(span);
        stack.push(span);
      }
    }
    for (const open of stack) errors.push({ at: open.start, msg: "<span> is never closed" });
    return { spans: spans.filter((s) => s.end >= 0), errors };
  }

  function kind(attrs) {
    if (attrs.no) return "invariant";
    if (attrs.lang === "nolang") return "nolang";
    if (attrs.lang || attrs.hist) return "translatable";
    return "plain";
  }

  // rmstory.attributes.needs_id: translatable, or story-tracked (hist without no).
  function needsId(attrs) {
    const translatable = !attrs.no && attrs.lang !== "nolang" && !!(attrs.lang || attrs.hist);
    const tracked = !!attrs.hist && !attrs.no;
    return translatable || tracked || (!!attrs.lang && attrs.lang !== "nolang");
  }

  function openTag(attrs) {
    const parts = ["<span"];
    if (attrs.no) parts.push("no");
    if (attrs.lang) parts.push('lang="' + attrs.lang + '"');
    if (attrs.id) parts.push('id="' + attrs.id + '"');
    if (attrs.hist) parts.push('hist="' + attrs.hist + '"');
    for (const [k, v] of Object.entries(attrs)) {
      if (["no", "lang", "id", "hist"].includes(k)) continue;
      parts.push(v === true ? k : k + '="' + v + '"');
    }
    return parts.join(" ") + ">";
  }

  function inner(text, span) { return text.slice(span.openEnd, span.closeStart); }

  // Attributes for a span type: Invariant drops lang and sets no; Translatable
  // drops no and sets lang (default en); No-lang sets lang="nolang".
  function withKind(attrs, type, lang) {
    const out = { ...attrs };
    delete out.no;
    if (type === "invariant") { delete out.lang; out.no = true; }
    else if (type === "nolang") out.lang = "nolang";
    else out.lang = lang || (attrs.lang && attrs.lang !== "nolang" ? attrs.lang : SOURCE_LANGUAGE);
    return out;
  }

  function insideTag(text, pos) {
    const lt = text.lastIndexOf("<", pos - 1), gt = text.lastIndexOf(">", pos - 1);
    return lt > gt;
  }

  // Where a selection may be wrapped: trimmed, not cutting a tag, and either
  // containing whole spans or sitting inside one span's content.
  function wrapTarget(text, spans, s, e) {
    while (s < e && /\s/.test(text[s])) s++;
    while (e > s && /\s/.test(text[e - 1])) e--;
    if (s >= e) return { ok: false, reason: "empty selection" };
    if (insideTag(text, s) || insideTag(text, e)) return { ok: false, reason: "selection cuts through a tag" };
    let parent = null;
    for (const sp of spans) {
      const disjoint = e <= sp.start || s >= sp.end;
      const contains = s <= sp.start && e >= sp.end;
      const within = s >= sp.openEnd && e <= sp.closeStart;
      if (!(disjoint || contains || within)) return { ok: false, reason: "selection partially overlaps a span" };
      if (within && (!parent || sp.depth > parent.depth)) parent = sp;
    }
    const same = spans.find((sp) => sp.openEnd === s && sp.closeStart === e);
    return { ok: true, s, e, parent, same };
  }

  function nextId(base, used) {
    for (let n = 1; ; n++) if (!used.has(base + "." + n)) return base + "." + n;
  }

  function wrap(text, s, e, attrs) {
    return text.slice(0, s) + openTag(attrs) + text.slice(s, e) + "</span>" + text.slice(e);
  }

  function retag(text, span, attrs) {
    return text.slice(0, span.start) + openTag(attrs) + text.slice(span.openEnd);
  }

  function unwrap(text, span) {
    return text.slice(0, span.start) + inner(text, span) + text.slice(span.end);
  }

  function idsIn(chapters) {
    const used = new Set();
    for (const ch of chapters) for (const sp of parseSpans(ch.body).spans) if (sp.attrs.id) used.add(sp.attrs.id);
    return used;
  }

  // rmstory validate, client-side: unclosed tags, missing required ids,
  // same id with different text, hist ignored next to no.
  function validate(chapters) {
    const warnings = [], seen = new Map();
    for (const ch of chapters) {
      const { spans, errors } = parseSpans(ch.body);
      for (const err of errors) warnings.push({ chapter: ch.id, id: "", msg: err.msg });
      for (const sp of spans) {
        const id = sp.attrs.id, text = inner(ch.body, sp);
        if (!id && needsId(sp.attrs)) warnings.push({ chapter: ch.id, id: "", msg: "id required", at: sp.start });
        if (sp.attrs.hist && sp.attrs.no) warnings.push({ chapter: ch.id, id: id || "", msg: "hist ignored: no is present" });
        if (!id) continue;
        const prev = seen.get(id);
        if (prev !== undefined && prev !== text) warnings.push({ chapter: ch.id, id, msg: "same id, different text" });
        if (prev === undefined) seen.set(id, text);
      }
    }
    return warnings;
  }

  function variantsIn(chapters) {
    const out = new Set();
    for (const ch of chapters) for (const sp of parseSpans(ch.body).spans) if (sp.attrs.hist) out.add(sp.attrs.hist);
    return [...out].sort();
  }

  // A story index: the ids of the variant's spans (hist without no), in chapter order.
  function storyIndex(chapters, variant) {
    const ids = [];
    for (const ch of chapters) {
      for (const sp of parseSpans(ch.body).spans) {
        if (sp.attrs.hist === variant && !sp.attrs.no && sp.attrs.id && !ids.includes(sp.attrs.id)) ids.push(sp.attrs.id);
      }
    }
    return ids;
  }

  // ── generated files ──────────────────────────────────────────────────────

  function yamlScalar(v) {
    const s = String(v);
    return /^[\w./@-]+$/.test(s) && !/^(true|false|yes|no|null|~|\d+(\.\d+)?)$/i.test(s) ? s : JSON.stringify(s);
  }

  function yamlList(items) { return "[" + items.map(yamlScalar).join(", ") + "]"; }

  function setTopKey(text, key, value) {
    const line = key + ": " + yamlScalar(value);
    const re = new RegExp("^" + key + ":.*$", "m");
    if (re.test(text)) return text.replace(re, line);
    const type = /^type:.*$/m.exec(text);
    if (type) return text.slice(0, type.index + type[0].length) + "\n" + line + text.slice(type.index + type[0].length);
    return line + "\n" + text;
  }

  function dropTopBlock(text, key) {
    const lines = text.split("\n"), out = [];
    let skipping = false;
    for (const l of lines) {
      if (new RegExp("^" + key + ":").test(l)) { skipping = true; continue; }
      if (skipping && /^[^\s#]/.test(l)) skipping = false;
      if (!skipping) out.push(l);
    }
    return out.join("\n");
  }

  function storyBlock(story) {
    const lines = ["story:", "  language: " + yamlScalar(story.language || SOURCE_LANGUAGE)];
    if (story.id) lines.push("  id: " + yamlScalar(story.id));
    return lines.join("\n") + "\n";
  }

  // A bundled profile's plan with this rodeo's name, target and story.
  function planFromBase(basePlan, opts) {
    let text = setTopKey(basePlan, "name", opts.name);
    text = setTopKey(text, "deployment_target", opts.target);
    text = dropTopBlock(text, "story").replace(/\s*$/, "\n");
    return text + "\n" + storyBlock(opts.story);
  }

  // A lab-in-a-box plan for the platform's one-VM definition (node vm1).
  function planLabinabox(opts) {
    const r = opts.resources || {};
    const lines = [
      "# rodeo-plan.yaml — generated by the Rodeo Builder.",
      "type: lab-in-a-box",
      "name: " + yamlScalar(opts.name),
      "deployment_target: " + yamlScalar(opts.target),
      "",
      "resources:",
      "  vm:",
      "    memory_mib: " + (r.memory_mib || 4096),
      "    vcpu: " + (r.vcpu || 2),
      "    disk_gb: " + (r.disk_gb || 40),
      "",
      "lab_in_a_box:",
      "  source:",
      "    repo: " + LIAB_REPO,
      "    ref: latest",
      '  root_password: "??universal_pwd"',
      "  iso_image: " + LIAB_IMAGE,
      "  images:",
      "    - name: " + LIAB_IMAGE,
      "      url: " + LIAB_IMAGE_URL,
      "      sha256_url: " + LIAB_IMAGE_URL + ".sha256",
      "  nodes:",
      "    vm1:",
      "      addons: " + yamlList(opts.addons || []),
      "",
    ];
    return lines.join("\n") + storyBlock(opts.story);
  }

  function chapterFile(n, id) { return String(n).padStart(2, "0") + "-" + id + ".md"; }

  function chaptersYaml(title, chapters) {
    const lines = ["# Chapter metadata for the Rodeo Builder; rmstory ignores this file.",
      "title: " + yamlScalar(title), "chapters:"];
    chapters.forEach((ch, i) => {
      lines.push("  - file: " + chapterFile(i + 1, ch.id));
      lines.push("    mins: " + (ch.mins || 10));
      lines.push("    needs: " + yamlList(ch.needs || []));
      if (ch.check) lines.push("    check: " + yamlScalar("check-" + ch.id + ".sh"));
    });
    return lines.join("\n") + "\n";
  }

  function storyYaml(variant, ids) {
    return "# Ordered span ids for the \"" + variant + "\" story variant.\n" +
      "# Assemble with:  rodeo story render --story-id " + variant + "\n" +
      (ids.length ? ids.map((i) => "- " + yamlScalar(i)).join("\n") + "\n" : "[]\n");
  }

  // The chapter's own check script when its source has one, else a stub.
  function checkScript(ch) {
    if (ch.check_script) return ch.check_script;
    return "#!/bin/bash\n# Self-check for \"" + ch.title.replace(/"/g, "'") + "\": exit 0 when the chapter is done.\n" +
      "# Run from the lab host:  ./checks/check-" + ch.id + ".sh\nset -uo pipefail\n\n" +
      "echo \"check-" + ch.id + ": no check written yet\" >&2\nexit 1\n";
  }

  function readme(rodeo) {
    const name = rodeo.name, dir = "~/.rodeo/profiles/" + name;
    const lines = ["# " + (rodeo.title || name), "", "Generated by the Rodeo Builder.", "", "## Install", ""];
    lines.push((rodeo.base ? "Installs into `" + dir + "`, on top of the `" + rodeo.base + "` profile (named in `builder.yaml`):"
      : "Installs into `" + dir + "`:"), "", "```bash", "rodeo new " + name + " --from-zip " + name + ".zip", "```");
    const todo = rodeo.chapters.filter((ch) => (ch.needs || []).includes(MISSING_ADDON));
    if (todo.length) {
      lines.push("", "## Work needed", "", "These chapters need something no lab engine provides yet (`" + MISSING_ADDON + "`):", "",
        ...todo.map((ch) => "- " + ch.title + " (`" + ch.id + "`)"));
    }
    lines.push("", "## Deploy", "", "```bash", "rodeo up --profile " + name, "```", "",
      "Render the story: `rodeo story render --config-dir " + dir + "`" +
      (rodeo.story.id ? " (variant `" + rodeo.story.id + "`)" : "") + ".");
    if (rodeo.labJson) {
      lines.push("", "`lab.json` is the lab designed in lab-in-a-box's lab-builder, kept for reference:",
        "rodeo builds its own lab.json from `rodeo-plan.yaml` and `definition.yaml`.");
    }
    return lines.join("\n") + "\n";
  }

  // Every file of the rodeo, paths relative to the profile directory.
  function files(rodeo) {
    const out = [];
    out.push({ path: "rodeo-plan.yaml", content: rodeo.plan });
    if (rodeo.definition) out.push({ path: "definition.yaml", content: rodeo.definition });
    rodeo.chapters.forEach((ch, i) => out.push({ path: "story/" + chapterFile(i + 1, ch.id), content: ch.body }));
    if (rodeo.chapters.length) out.push({ path: "story/chapters.yaml", content: chaptersYaml(rodeo.title, rodeo.chapters) });
    for (const v of rodeo.variants) out.push({ path: "story/stories/" + v + ".yaml", content: storyYaml(v, storyIndex(rodeo.chapters, v)) });
    for (const ch of rodeo.chapters) if (ch.check) out.push({ path: "checks/check-" + ch.id + ".sh", content: checkScript(ch), mode: 0o755 });
    if (rodeo.labJson) out.push({ path: "lab.json", content: JSON.stringify(rodeo.labJson, null, 2) + "\n" });
    if (rodeo.base) out.push({ path: "builder.yaml", content: "# The profile this rodeo is built on: rodeo new --from-zip copies it first.\nbase: " + rodeo.base + "\n" });
    out.push({ path: "README.md", content: readme(rodeo) });
    return out;
  }

  // Add-on names of a lab-in-a-box lab.json (strings or {name: config}).
  function labJsonAddons(lab) {
    const names = new Set();
    for (const node of Object.values((lab && lab.nodes) || {})) {
      for (const a of (node && node.addons) || []) names.add(typeof a === "string" ? a : Object.keys(a)[0]);
    }
    return [...names].filter(Boolean).sort();
  }

  // ── zip (stored, no compression) ─────────────────────────────────────────

  const CRC_TABLE = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })();

  function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  function zip(entries, date) {
    const enc = new TextEncoder(), d = date || new Date();
    const time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
    const day = ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
    const chunks = [], central = [];
    let offset = 0;
    for (const e of entries) {
      const name = enc.encode(e.path), data = typeof e.content === "string" ? enc.encode(e.content) : e.content;
      const crc = crc32(data);
      const local = new DataView(new ArrayBuffer(30));
      local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
      local.setUint16(8, 0, true); local.setUint16(10, time, true); local.setUint16(12, day, true);
      local.setUint32(14, crc, true); local.setUint32(18, data.length, true); local.setUint32(22, data.length, true);
      local.setUint16(26, name.length, true); local.setUint16(28, 0, true);
      chunks.push(new Uint8Array(local.buffer), name, data);
      const cd = new DataView(new ArrayBuffer(46));
      cd.setUint32(0, 0x02014b50, true); cd.setUint16(4, 0x0314, true); cd.setUint16(6, 20, true);
      cd.setUint16(8, 0x0800, true); cd.setUint16(10, 0, true); cd.setUint16(12, time, true); cd.setUint16(14, day, true);
      cd.setUint32(16, crc, true); cd.setUint32(20, data.length, true); cd.setUint32(24, data.length, true);
      cd.setUint16(28, name.length, true); cd.setUint16(30, 0, true); cd.setUint16(32, 0, true);
      cd.setUint16(34, 0, true); cd.setUint16(36, 0, true);
      cd.setUint32(38, (((e.mode || 0o644) | 0o100000) << 16) >>> 0, true); cd.setUint32(42, offset, true);
      central.push(new Uint8Array(cd.buffer), name);
      offset += 30 + name.length + data.length;
    }
    const cdSize = central.reduce((n, c) => n + c.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, entries.length, true); end.setUint16(10, entries.length, true);
    end.setUint32(12, cdSize, true); end.setUint32(16, offset, true);
    const parts = [...chunks, ...central, new Uint8Array(end.buffer)];
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const p of parts) { out.set(p, at); at += p.length; }
    return out;
  }

  return {
    MISSING_ADDON, SOURCE_LANGUAGE, LANGUAGES, slugify, uniqueId, parseSpans, parseAttrs, kind, needsId, openTag, inner,
    withKind, wrapTarget, nextId, wrap, retag, unwrap, idsIn, validate, variantsIn, storyIndex,
    setTopKey, dropTopBlock, storyBlock, planFromBase, planLabinabox, chapterFile, chaptersYaml,
    storyYaml, checkScript, readme, files, labJsonAddons, crc32, zip,
  };
})();

if (typeof module !== "undefined" && module.exports) module.exports = RB;
else window.RB = RB;
