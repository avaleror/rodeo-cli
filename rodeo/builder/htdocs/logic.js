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

  // validate() output counted per chapter id.
  function warningsByChapter(warnings) {
    const counts = {};
    for (const w of warnings) counts[w.chapter] = (counts[w.chapter] || 0) + 1;
    return counts;
  }

  // The engines the rodeo's chapters run on, from the library groups they were
  // added from: [{engine, workshops: [titles]}] sorted by engine. Chapters from a
  // group without an engine (e.g. "My chapters") need none.
  function engineNeeds(chapters, groups) {
    const byId = new Map(groups.map((g) => [g.id, g])), need = new Map();
    for (const ch of chapters) {
      const g = byId.get(String(ch.from || "").split("/")[0]);
      if (!g || !g.engine) continue;
      if (!need.has(g.engine)) need.set(g.engine, []);
      if (!need.get(g.engine).includes(g.title)) need.get(g.engine).push(g.title);
    }
    return [...need].sort((a, b) => a[0].localeCompare(b[0])).map(([engine, workshops]) => ({ engine, workshops }));
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

  // Gives every span that has or needs an id the chapter's own scheme: <base>.N at the top
  // level, <id of the nearest ancestor with one>.N when nested. Drafts from an engine name
  // their spans freely; this makes them follow the builder's (and rmstory's) id rules.
  function renumber(text, base) {
    const { spans } = parseSpans(text);
    const ids = new Array(spans.length), count = new Map();
    for (const sp of spans) {
      if (!sp.attrs.id && !needsId(sp.attrs)) continue;
      let up = sp.parent;
      while (up >= 0 && !ids[up]) up = spans[up].parent;
      const prefix = up >= 0 ? ids[up] : base, n = (count.get(prefix) || 0) + 1;
      count.set(prefix, n);
      ids[sp.index] = prefix + "." + n;
    }
    let out = text;
    for (const sp of [...spans].sort((a, b) => b.start - a.start)) {
      if (ids[sp.index]) out = retag(out, sp, { ...sp.attrs, id: ids[sp.index] });
    }
    return out;
  }

  // `text` with an id ("<base>.N", unused) on every span that needs one and has none.
  function nameMissing(text, base) {
    const { spans } = parseSpans(text), used = new Set(spans.map((sp) => sp.attrs.id).filter(Boolean));
    const named = spans.filter((s) => !s.attrs.id && needsId(s.attrs)).map((sp) => {
      const id = nextId(base, used);
      used.add(id);
      return [sp, id];
    });
    let out = text;
    for (const [sp, id] of named.reverse()) out = retag(out, sp, { ...sp.attrs, id });
    return out;
  }

  // ── translations (rmstory's filesystem store) ────────────────────────────

  // Ids of the translatable spans, in chapter order: the rows a translation fills.
  function translatableIds(chapters) {
    const ids = [];
    for (const ch of chapters) for (const sp of parseSpans(ch.body).spans) {
      if (sp.attrs.id && kind(sp.attrs) === "translatable" && !ids.includes(sp.attrs.id)) ids.push(sp.attrs.id);
    }
    return ids;
  }

  // The text rmstory translates for a span: its content with each nested span replaced by
  // the placeholder <span id="..."/>, which a translation must keep unchanged.
  function storedSource(text, spans, span) {
    let out = "", at = span.openEnd;
    for (const child of spans) {
      if (child.parent !== span.index) continue;
      out += text.slice(at, child.start) + '<span id="' + (child.attrs.id || "") + '"/>';
      at = child.end;
    }
    return out + text.slice(at, span.closeStart);
  }

  // {id: source text} for every translatable span (the first occurrence of an id).
  function sourceTexts(chapters) {
    const out = {};
    for (const ch of chapters) {
      const { spans } = parseSpans(ch.body);
      for (const sp of spans) {
        if (sp.attrs.id && kind(sp.attrs) === "translatable" && !(sp.attrs.id in out)) out[sp.attrs.id] = storedSource(ch.body, spans, sp);
      }
    }
    return out;
  }

  // Placeholders a translation of *source* must carry, and those it lacks.
  function missingPlaceholders(source, translation) {
    const want = source.match(/<span id="[^"]*"\/>/g) || [];
    return want.filter((p) => !String(translation).includes(p));
  }

  // The store as rodeo files: story/strings/<lang>/<id>/@default/content.json for each row
  // of a language other than the source one whose span is still in the chapters (*ids*).
  function stringsFiles(strings, ids, date) {
    const keep = new Set(ids), out = [];
    for (const lang of Object.keys(strings || {}).sort()) {
      if (lang === SOURCE_LANGUAGE) continue;
      for (const id of Object.keys(strings[lang]).sort()) {
        const row = strings[lang][id];
        if (!keep.has(id) || !row || typeof row.content !== "string") continue;
        out.push({ path: "story/strings/" + lang + "/" + id + "/@default/content.json",
          content: JSON.stringify({ content: row.content, original_language: null, status: row.status || "draft",
            source_checksum: null, updated_by: row.updated_by || "rodeo-builder", date_updated: date }, null, 2) + "\n" });
      }
    }
    return out;
  }

  // Languages with at least one stored row for the rodeo's spans.
  function translatedLanguages(strings, ids) {
    const keep = new Set(ids);
    return Object.keys(strings || {}).filter((l) => l !== SOURCE_LANGUAGE && Object.keys(strings[l]).some((id) => keep.has(id))).sort();
  }

  // The prompt for drafting a chapter with an engine: the author's brief, grounded in the
  // rodeo (its title, earlier chapters and what its lab provides) and rmstory's fact rule.
  function draftPrompt(brief, ctx) {
    const lines = ["Write one chapter of a hands-on technical workshop as the student reads it: " +
      "short steps in the second person, with the commands to run.", "Chapter title: " + (ctx.title || "untitled") + "."];
    if (ctx.rodeo) lines.push("Workshop: " + ctx.rodeo + ".");
    if (ctx.provides && ctx.provides.length) lines.push("The lab provides: " + ctx.provides.join(", ") + ". Use only what it provides.");
    if (ctx.chapters && ctx.chapters.length) lines.push("Chapters before this one: " + ctx.chapters.join("; ") + ".");
    if (ctx.mins) lines.push("The student should need about " + ctx.mins + " minutes.");
    lines.push("Deployment facts are Jinja expressions such as {{ rancher_url }}: copy each one unchanged into its own <span no> span.",
      "", String(brief).trim());
    return lines.join("\n");
  }

  // A span's part in a retold story, set by the author: "story" (it has a story variant, hist,
  // and is retold), "exercise" (translatable without a story: kept word for word) or "fixed"
  // (invariant or no-lang: never translated or retold).
  function storyRole(attrs) {
    if (attrs.hist && !attrs.no) return "story";
    return kind(attrs) === "translatable" ? "exercise" : "fixed";
  }

  function storyParts(chapters) {
    const out = { story: 0, exercise: 0, fixed: 0 };
    for (const ch of chapters) for (const sp of parseSpans(ch.body).spans) out[storyRole(sp.attrs)]++;
    return out;
  }

  function roleAbove(spans, sp, role) {
    for (let up = sp.parent; up >= 0; up = spans[up].parent) if (storyRole(spans[up].attrs) === role) return true;
    return false;
  }

  // The body sent to rmstory generate rewrite: each exercise span outside a story span becomes
  // an HTML comment (rmstory skips comments, so the engine never sees it). Returns {body, kept}.
  function maskExercises(body) {
    const { spans } = parseSpans(body), kept = [];
    const masked = spans.filter((sp) => storyRole(sp.attrs) === "exercise" && !roleAbove(spans, sp, "story")
      && !roleAbove(spans, sp, "exercise"));
    let out = body;
    for (const sp of masked.sort((a, b) => b.start - a.start)) {
      kept.push(body.slice(sp.start, sp.end));
      out = out.slice(0, sp.start) + "<!--rodeo-keep:" + (kept.length - 1) + "-->" + out.slice(sp.end);
    }
    return { body: out, kept };
  }

  function unmaskExercises(body, kept) {
    return String(body).replace(/<!--rodeo-keep:(\d+)-->/g, (m, n) => (kept && n in kept ? kept[n] : m));
  }

  // Exercise spans inside story spans get their original text back, by id.
  function restoreExercises(retold, original) {
    const before = new Map();
    for (const sp of parseSpans(original).spans) {
      if (sp.attrs.id && storyRole(sp.attrs) === "exercise") before.set(sp.attrs.id, inner(original, sp));
    }
    const { spans } = parseSpans(retold);
    let out = retold;
    for (const sp of spans.filter((s) => storyRole(s.attrs) === "exercise" && before.has(s.attrs.id)
      && !roleAbove(spans, s, "exercise")).sort((a, b) => b.start - a.start)) {
      out = out.slice(0, sp.openEnd) + before.get(sp.attrs.id) + out.slice(sp.closeStart);
    }
    return out;
  }

  // The rodeo's chapters as the new story `slug`: `bodies` ({id: body}) are rmstory's retold
  // (masked) bodies, `kept` ({id: kept}) what maskExercises took out. Chapter ids become
  // "<id>-<slug>" and story span ids (and references to them) "<slug>.<id>", so the new
  // story's translations never mix with the original's; exercise and fixed spans keep their
  // ids and so their translations.
  function retoldChapters(chapters, bodies, slug, kept) {
    const storyIds = new Set();
    for (const c of chapters) {
      for (const sp of parseSpans(c.body).spans) if (sp.attrs.id && storyRole(sp.attrs) === "story") storyIds.add(sp.attrs.id);
    }
    return chapters.map((c) => {
      const retold = c.id in bodies ? restoreExercises(unmaskExercises(bodies[c.id], (kept || {})[c.id]), c.body) : c.body;
      return { ...c, id: (c.id + "-" + slug).slice(0, 63).replace(/-+$/, ""),
        body: retold.replace(/(<span\b[^>]*?\sid=")([^"]+)"/g, (m, head, id) => head + (storyIds.has(id) ? slug + "." : "") + id + '"') };
    });
  }

  // ── publish (rodeo/builder/publish.py does the same with the host's GitHub login) ──

  const CATALOG_REPO = "avaleror/rodeo-cli", CATALOG_DIR = "rodeo/data/catalog.d";

  // The catalog file rodeo/data/catalog.d/<name>.yaml.
  function catalogEntry(o) {
    const q = (v) => JSON.stringify(String(v));
    return ["name: " + q(o.name), "title: " + q(o.title || o.name), "repo: " + q(o.repo), "ref: main",
      "engine: " + q(o.engine || ""), "languages: [" + (o.languages || []).map(q).join(", ") + "]", ""].join("\n");
  }

  // github.com pages that publish with the browser's own GitHub login (GitHub asks to log in
  // when there is none): create the repository, upload the files, open the catalog pull request.
  function publishLinks(o) {
    const e = encodeURIComponent, repo = "https://github.com/" + o.user + "/" + o.name;
    const entry = catalogEntry({ ...o, repo });
    return {
      create: "https://github.com/new?name=" + e(o.name) + "&description=" + e(o.title || o.name) + "&visibility=public",
      upload: repo + "/upload/main",
      catalog: "https://github.com/" + (o.catalog || CATALOG_REPO) + "/new/main?filename=" + e(CATALOG_DIR + "/" + o.name + ".yaml")
        + "&value=" + e(entry) + "&message=" + e("catalog: add " + o.name),
      repo, entry,
    };
  }

  // ── workshops imported by URL (rodeo/builder/imports.py does the same on the server) ──

  const DEFAULT_MINUTES = 10, INSTRUQT_MINUTES = 20;

  // {host, owner, repo, ref, path} of https://<host>/<owner>/<repo>[/tree/<ref>/<path>];
  // ref is "" for the default branch. Throws on anything else.
  function parseSourceUrl(url) {
    let u;
    try { u = new URL(String(url || "").trim()); } catch (_) { throw new Error("give an https:// URL of a public git repository"); }
    if (u.protocol !== "https:" || u.username || u.password) throw new Error("give an https:// URL of a public git repository");
    if (/\/\.\.?(\/|$)/.test(String(url).split(/[?#]/)[0])) throw new Error("the URL's path has . or .. segments");
    const m = /^\/([^/]+)\/([^/]+?)(?:\.git)?(?:\/(?:tree|blob)\/([^/]+)(?:\/(.*))?)?\/?$/.exec(u.pathname);
    if (!m || u.search || u.hash) throw new Error("expected https://<host>/<owner>/<repo>[/tree/<ref>/<path>]");
    const path = decodeURIComponent((m[4] || "").replace(/^\/+|\/+$/g, ""));
    if (path && !path.split("/").every((s) => /^[A-Za-z0-9_][A-Za-z0-9._ -]{0,127}$/.test(s))) {
      throw new Error(JSON.stringify(path) + " is not a plain path in the repository");
    }
    return { host: u.host, owner: m[1], repo: m[2], ref: m[3] ? decodeURIComponent(m[3]) : "", path };
  }

  function importGroupId(src) {
    const tail = [src.owner, src.repo, src.path].filter(Boolean).join("/");
    return "url-" + (tail.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "source");
  }

  // {format, dir} for a repository's file list (paths from its root), looking under *path*:
  // rmstory (story/*.md, or *.md next to chapters.yaml), instruqt (<challenge>/assignment.md),
  // markdown (*.md). Throws when none fits.
  function detectFormat(files, path) {
    const base = path ? path + "/" : "", set = new Set(files);
    const direct = (dir) => files.filter((f) => f.startsWith(dir) && !f.slice(dir.length).includes("/"));
    if (direct(base + "story/").some((f) => f.endsWith(".md"))) return { format: "rmstory", dir: base + "story" };
    const md = direct(base).filter((f) => f.endsWith(".md"));
    if (set.has(base + "chapters.yaml") && md.length) return { format: "rmstory", dir: path };
    if (files.some((f) => f.startsWith(base) && /^[^/]+\/assignment\.md$/.test(f.slice(base.length)))) return { format: "instruqt", dir: path };
    if (md.length) return { format: "markdown", dir: path };
    throw new Error("no chapters found: expected story/*.md, <challenge>/assignment.md or *.md");
  }

  function titleOf(body, fallback) {
    const m = /^#\s+(.+?)\s*$/m.exec(body);
    return m ? m[1].replace(/<[^>]+>/g, "").trim() : fallback;
  }

  function baseChapter(id, file, body) {
    return { id, file, title: titleOf(body, id), mins: DEFAULT_MINUTES, needs: [], check: false, check_script: "",
      spans: parseSpans(body).spans.length, body };
  }

  // A markdown exercise: title from the first heading, minutes from "**Time:** N min".
  function chapterFromMarkdown(name, body) {
    const ch = baseChapter(name.replace(/\.md$/, "").replace(/^\d+-/, ""), name, body);
    const t = /^\*\*Time:\*\*\s*(\d+)\s*min/im.exec(body);
    if (t) ch.mins = parseInt(t[1], 10);
    return ch;
  }

  // An Instruqt challenge: front-matter title, the body after it, its check script if any.
  function chapterFromInstruqt(dir, text, checkScript) {
    const fm = /^---\n([\s\S]*?)\n---\n?/.exec(text), body = (fm ? text.slice(fm[0].length) : text).replace(/^\n+/, "");
    const ch = baseChapter(dir.replace(/^\d+-/, ""), dir + "/assignment.md", body);
    const title = fm && /^title:\s*(.+?)\s*$/m.exec(fm[1]);
    if (title) ch.title = title[1].replace(/^["']|["']$/g, "");
    ch.mins = INSTRUQT_MINUTES;
    if (checkScript) { ch.check = true; ch.check_script = checkScript; }
    return ch;
  }

  // The chapters.yaml fields the builder reads: {title, files: {file: {mins, needs, title}}}.
  function chaptersMeta(text) {
    const out = { title: "", files: {} };
    let cur = null;
    for (const line of String(text || "").split("\n")) {
      let m;
      if ((m = /^title:\s*(.+?)\s*$/.exec(line))) out.title = m[1].replace(/^["']|["']$/g, "");
      else if ((m = /^\s*-\s*file:\s*(\S+)/.exec(line))) { cur = { needs: [] }; out.files[m[1]] = cur; }
      else if (cur && (m = /^\s+mins:\s*(\d+)/.exec(line))) cur.mins = parseInt(m[1], 10);
      else if (cur && (m = /^\s+needs:\s*\[(.*)\]/.exec(line))) cur.needs = m[1].split(",").map((s) => s.trim()).filter(Boolean);
      else if (cur && (m = /^\s+title:\s*(.+?)\s*$/.exec(line))) cur.title = m[1].replace(/^["']|["']$/g, "");
    }
    return out;
  }

  // A story variant index (story/stories/<id>.yaml): its list of span ids.
  function storyIds(text) {
    return String(text || "").split("\n").map((l) => /^-\s*["']?([^"'\s#]+)/.exec(l)).filter(Boolean).map((m) => m[1]);
  }

  // A rodeo story chapter (story/NN-<id>.md) with its chapters.yaml entry.
  function chapterFromStory(name, body, meta) {
    const ch = baseChapter(name.replace(/\.md$/, "").replace(/^\d+-/, ""), name, body), info = (meta && meta.files[name]) || {};
    if (info.title) ch.title = info.title;
    if (info.mins) ch.mins = info.mins;
    ch.needs = info.needs || [];
    return ch;
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
    const langs = translatedLanguages(rodeo.strings, translatableIds(rodeo.chapters));
    if (langs.length) {
      lines.push("", "Translations in `story/strings/`: " + langs.map((l) => "`" + l + "`").join(", ") +
        ". Render one with `--language <lang>`; spans without a stored translation need `--engine <name>`.");
    }
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
    out.push(...stringsFiles(rodeo.strings, translatableIds(rodeo.chapters), rodeo.date || new Date().toISOString()));
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
    withKind, wrapTarget, nextId, wrap, retag, unwrap, idsIn, validate, warningsByChapter, engineNeeds, variantsIn, storyIndex,
    setTopKey, dropTopBlock, storyBlock, planFromBase, planLabinabox, chapterFile, chaptersYaml,
    storyYaml, checkScript, readme, files, labJsonAddons, crc32, zip,
    renumber, nameMissing, translatableIds, storedSource, sourceTexts, missingPlaceholders, stringsFiles, translatedLanguages, draftPrompt, storyRole, storyParts, maskExercises, retoldChapters, CATALOG_REPO, catalogEntry, publishLinks,
    parseSourceUrl, importGroupId, detectFormat, chapterFromMarkdown, chapterFromInstruqt, chaptersMeta, storyIds, chapterFromStory,
  };
})();

if (typeof module !== "undefined" && module.exports) module.exports = RB;
else window.RB = RB;
