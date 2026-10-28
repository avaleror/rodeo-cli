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

  // ── story preview (Markdown or HTML, rendered without a DOM) ─────────────
  //
  // previewTree(body, spans) returns { format, nodes } for the editor's preview. A node is
  // a text leaf { s, e } (shows body.slice(s, e); { s, e, text } when it shows decoded text,
  // e.g. an entity), an element { tag, attrs, children } (tags and attributes from an
  // allow-list only), a rodeo span { span: index, cont, children } (cont: a continuation
  // of a span split by a block boundary or a crossing tag) or a generated label { label }
  // (text that is not in the source, e.g. a callout title). Markup that is not shown (`**`,
  // `#`, tags, comments) produces no leaf, so every text leaf maps straight back to the source.

  const FRONT_MATTER_RE = /^---[ \t]*\n[\s\S]*?\n---[ \t]*(?:\n|$)/;
  const HTML_START_RE = /^<(?:!doctype\b|(?:html|head|body|p|div|h[1-6]|ul|ol|li|table|section|article|header|footer|main|nav|blockquote|pre|figure|details|dl|hr|br|img)\b)/i;
  const PREVIEW_BLOCK = new Set(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "hr",
    "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td", "dl", "dt", "dd", "section", "article", "header", "footer",
    "main", "nav", "aside", "figure", "figcaption", "details", "summary"]);
  const PREVIEW_INLINE = new Set(["a", "b", "strong", "i", "em", "u", "s", "del", "ins", "code", "kbd", "samp", "var",
    "sub", "sup", "mark", "small", "abbr", "cite", "q", "br", "img"]);
  // Dropped with everything inside them; any other unknown tag is dropped and its content kept.
  const PREVIEW_DROP = new Set(["script", "style", "iframe", "frame", "frameset", "object", "embed", "template", "noscript",
    "noembed", "svg", "math", "textarea", "select", "title", "head"]);
  const PREVIEW_VOID = new Set(["br", "hr", "img", "input", "meta", "link", "base", "col", "area", "source", "track", "wbr"]);
  // Opening the key closes an open element of one of these names on top of the stack.
  const AUTO_CLOSE = { li: ["li"], dt: ["dt", "dd"], dd: ["dt", "dd"], tr: ["tr", "td", "th"], td: ["td", "th"], th: ["td", "th"],
    thead: ["tbody", "tfoot"], tbody: ["thead", "tbody"], tfoot: ["thead", "tbody"] };
  const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'", nbsp: " " };
  const ENTITY_RE = /&(?:#(\d{1,7})|#x([0-9a-f]{1,6})|([a-z]+));/gi;
  const HIDDEN = "\u0000";
  // Containers whose whitespace-only text is dropped (it would only add stray text nodes).
  const STRUCTURAL = new Set(["ul", "ol", "dl", "table", "thead", "tbody", "tfoot", "tr"]);

  function frontMatterEnd(body) {
    const m = FRONT_MATTER_RE.exec(body);
    return m ? m[0].length : 0;
  }

  // "html" when the first thing after the front matter (HTML comments and <span> tags
  // ignored) is a block-level HTML tag or a doctype; anything else is "markdown".
  function previewFormat(body) {
    const rest = body.slice(frontMatterEnd(body)).replace(/<!--[\s\S]*?(?:-->|$)|<\/?span\b[^>]*>/gi, "").trimStart();
    return HTML_START_RE.test(rest) ? "html" : "markdown";
  }

  function decodeEntity(m, dec, hex, name) {
    const code = dec ? parseInt(dec, 10) : hex ? parseInt(hex, 16) : -1;
    if (code >= 0) return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : null;
    return Object.prototype.hasOwnProperty.call(ENTITIES, name.toLowerCase()) ? ENTITIES[name.toLowerCase()] : null;
  }

  function decodeEntities(text) {
    return text.replace(ENTITY_RE, (m, dec, hex, name) => decodeEntity(m, dec, hex, name) ?? m);
  }

  // http(s), mailto and scheme-less (relative, #anchor) URLs; anything else is no link.
  function safeHref(url) {
    const clean = decodeEntities(url).replace(/[\s\u0000-\u001f]/g, "");
    const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(clean);
    if (!clean) return null;
    return !scheme || ["http", "https", "mailto"].includes(scheme[1].toLowerCase()) ? clean : null;
  }

  function linkAttrs(url, extra) {
    const href = safeHref(url);
    return href === null ? { ...extra } : { ...extra, href, rel: "noopener", target: "_blank" };
  }

  // Builds the node tree from parser events; inline elements open lazily (at their first
  // text) and are reopened as continuations when a block boundary or a crossing close splits them.
  function previewBuilder(body) {
    const root = { children: [] }, stack = [{ node: root, inline: false }];
    let pending = [];
    const top = () => stack[stack.length - 1];
    const lift = () => {
      const lifted = [];
      while (top().inline) lifted.unshift(stack.pop().ev);
      pending = [...lifted, ...pending];
    };
    const materialize = () => {
      for (const ev of pending) {
        const node = ev.span !== undefined ? { span: ev.span, cont: !!ev.shown, children: [] } : { tag: ev.tag, attrs: ev.attrs, children: [] };
        ev.shown = true;
        top().node.children.push(node);
        stack.push({ node, ev, inline: true });
      }
      pending = [];
    };
    return {
      root,
      text(s, e, raw) {
        if (e <= s) return;
        if (!/\S/.test(body.slice(s, e)) && (pending.length || top().node === root || STRUCTURAL.has(top().node.tag))) return;
        materialize();
        const out = top().node.children;
        if (raw) { out.push({ s, e }); return; }
        let at = s, m;
        const src = body.slice(s, e);
        ENTITY_RE.lastIndex = 0;
        while ((m = ENTITY_RE.exec(src))) {
          const text = decodeEntity(...m);
          if (text === null) continue;
          if (m.index + s > at) out.push({ s: at, e: s + m.index });
          out.push({ s: s + m.index, e: s + m.index + m[0].length, text });
          at = s + m.index + m[0].length;
        }
        if (e > at) out.push({ s: at, e });
      },
      open(ev) {
        if (!ev.block) { pending.push(ev); return; }
        lift();
        const node = { tag: ev.tag, attrs: ev.attrs, children: [] };
        top().node.children.push(node);
        stack.push({ node, ev, inline: false });
      },
      close(ev) {
        const at = pending.indexOf(ev.ref);
        if (at >= 0) {
          pending.splice(at, 1);
          if (ev.ref.span !== undefined && !ev.ref.shown) top().node.children.push({ span: ev.ref.span, cont: false, children: [] });
          return;
        }
        const depth = stack.findIndex((x) => x.ev === ev.ref);
        if (depth < 1) return;
        const reopen = stack.splice(depth).slice(1).map((x) => x.ev);
        pending = [...reopen, ...pending];
      },
      void(ev) {
        if (ev.block) lift(); else materialize();
        top().node.children.push({ tag: ev.tag, attrs: ev.attrs, children: [] });
      },
      label(ev) {
        top().node.children.push({ label: ev.text });
      },
    };
  }

  // ── Markdown ──

  const MD_FENCE = /^( {0,3})(`{3,}|~{3,})([^`]*)$/;
  const MD_ATX = /^ {0,3}(#{1,6})(?=[ \t]|$)/;
  const MD_HR = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
  const MD_QUOTE = /^ {0,3}> ?/;
  const MD_SETEXT = /^ {0,3}(?:(=+)|-+)[ \t]*$/;
  // A GitHub alert: the first line of a block quote; what follows the marker is the callout body.
  const MD_ALERT = /^ {0,3}\[!(note|tip|important|warning|caution)\](?:[ \t]+|$)/i;
  const MD_ALERT_TITLE = { note: "Note", tip: "Tip", important: "Important", warning: "Warning", caution: "Caution" };
  const MD_ITEM = /^( {0,3})([-*+]|\d{1,9}[.)])([ \t]+|$)/;
  const MD_BLANK = /^\s*$/;
  const MD_HIDDEN_ONLY = /^\s*\u0000[\s\u0000]*$/;
  const MD_PUNCT = /[!-/:-@[-`{-~\u0000]/;
  const HTML_TAG_RE = /^<(\/?)([a-zA-Z][\w-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/;

  function mdStartsBlock(t) {
    return MD_FENCE.test(t) || MD_ATX.test(t) || MD_HR.test(t) || MD_QUOTE.test(t) || /^ {0,3}([-*+]|\d{1,9}[.)])[ \t]+\S/.test(t);
  }

  // A line without its first n characters; lazy marks a paragraph continuation line outside its container's markers.
  function mdStrip(line, n) { return { s: line.s + n, text: line.text.slice(n), lazy: line.lazy }; }

  function mdBlocks(lines, out) {
    const open = (tag, attrs, s) => { const ev = { t: "open", tag, attrs, block: true, at: s }; out.push(ev); return ev; };
    const close = (ref, at) => out.push({ t: "close", ref, at });
    const end = (ln) => ln.s + ln.text.length;
    for (let i = 0; i < lines.length;) {
      const ln = lines[i], t = ln.text;
      let m;
      if (MD_BLANK.test(t) || MD_HIDDEN_ONLY.test(t)) { i++; continue; }
      if ((m = MD_FENCE.exec(t))) {
        const mark = m[2], fence = new RegExp("^ {0,3}[" + mark[0] + "]{" + mark.length + ",}\\s*$");
        let j = i + 1;
        while (j < lines.length && !fence.test(lines[j].text)) j++;
        const pre = open("pre", {}, ln.s), code = { t: "open", tag: "code", attrs: {}, block: false, at: ln.s };
        out.push(code);
        for (let k = i + 1; k < j; k++) out.push({ t: "text", s: lines[k].s, e: end(lines[k]) + (k < j - 1 ? 1 : 0), raw: true });
        close(code, j < lines.length ? lines[j].s : end(lines[j - 1]));
        close(pre, j < lines.length ? end(lines[j]) : end(lines[j - 1]));
        i = j + 1;
      } else if ((m = MD_ATX.exec(t))) {
        let a = m[0].length;
        while (t[a] === " " || t[a] === "\t") a++;
        const tail = /(?:^|[ \t]+)#+[ \t]*$|[ \t]+$/.exec(t.slice(a));
        const h = open("h" + m[1].length, {}, ln.s);
        mdInline([{ s: ln.s + a, text: t.slice(a, tail ? a + tail.index : t.length) }], out);
        close(h, end(ln));
        i++;
      } else if (MD_HR.test(t)) {
        out.push({ t: "void", tag: "hr", attrs: {}, block: true, at: ln.s });
        i++;
      } else if (MD_QUOTE.test(t)) {
        const inner = [];
        let j = i;
        while (j < lines.length) {
          const q = MD_QUOTE.exec(lines[j].text);
          if (q) inner.push(mdStrip(lines[j], q[0].length));
          else if (MD_BLANK.test(lines[j].text) || MD_BLANK.test(inner[inner.length - 1].text) || mdStartsBlock(lines[j].text)) break;
          else inner.push({ ...lines[j], lazy: true });
          j++;
        }
        const alert = MD_ALERT.exec(inner[0].text);
        const bq = open("blockquote", alert ? { class: "callout callout-" + alert[1].toLowerCase() } : {}, ln.s);
        if (alert) {
          const title = open("p", { class: "callout-title" }, inner[0].s);
          out.push({ t: "label", text: MD_ALERT_TITLE[alert[1].toLowerCase()], at: inner[0].s });
          close(title, inner[0].s + alert[0].length);
          inner[0] = mdStrip(inner[0], alert[0].length);
        }
        mdBlocks(inner, out);
        close(bq, end(lines[j - 1]));
        i = j;
      } else if ((m = MD_ITEM.exec(t))) {
        const ordered = /\d/.test(m[2]), delim = m[2].slice(-1);
        const same = (x) => x && /\d/.test(x[2]) === ordered && x[2].slice(-1) === delim;
        const list = open(ordered ? "ol" : "ul", ordered && parseInt(m[2], 10) !== 1 ? { start: String(parseInt(m[2], 10)) } : {}, ln.s);
        let last = ln;
        while (i < lines.length) {
          const mm = MD_ITEM.exec(lines[i].text);
          if (!same(mm)) break;
          const pad = mm[3].length, marker = mm[1].length + mm[2].length;
          const indent = marker + (pad === 0 || pad > 4 ? 1 : pad);
          const inner = [mdStrip(lines[i], Math.min(indent, lines[i].text.length))];
          let j = i + 1;
          while (j < lines.length) {
            const lt = lines[j].text, ind = /^ */.exec(lt)[0].length;
            if (MD_BLANK.test(lt)) inner.push(mdStrip(lines[j], lt.length));
            else if (ind >= indent) inner.push(mdStrip(lines[j], indent));
            else if (!MD_BLANK.test(inner[inner.length - 1].text) && !mdStartsBlock(lt) && !same(MD_ITEM.exec(lt))) inner.push({ ...mdStrip(lines[j], ind), lazy: true });
            else break;
            j++;
          }
          while (inner.length > 1 && MD_BLANK.test(inner[inner.length - 1].text)) inner.pop();
          const li = open("li", {}, lines[i].s);
          mdBlocks(inner, out);
          last = inner[inner.length - 1];
          close(li, end(last));
          i = j;
          while (i < lines.length && MD_BLANK.test(lines[i].text) && i + 1 < lines.length && (MD_BLANK.test(lines[i + 1].text) || same(MD_ITEM.exec(lines[i + 1].text)))) i++;
        }
        close(list, end(last));
      } else {
        // A setext underline (not a lazy continuation line) turns the paragraph into a heading.
        const para = [ln];
        let j = i + 1, setext = null;
        while (j < lines.length && !MD_BLANK.test(lines[j].text)) {
          if (!lines[j].lazy && (setext = MD_SETEXT.exec(lines[j].text))) break;
          if (mdStartsBlock(lines[j].text)) break;
          para.push(lines[j++]);
        }
        const p = open(setext ? (setext[1] ? "h1" : "h2") : "p", {}, ln.s);
        mdInline(para, out);
        close(p, end(setext ? lines[j] : para[para.length - 1]));
        i = setext ? j + 1 : j;
      }
    }
  }

  // Inline Markdown over lines (each { s, text }), joined by their source newlines.
  function mdInline(lines, out) {
    const src = [];
    let str = "";
    lines.forEach((ln, n) => {
      for (let k = 0; k < ln.text.length; k++) src.push(ln.s + k);
      str += ln.text;
      if (n < lines.length - 1) { src.push(ln.s + ln.text.length); str += "\n"; }
    });
    src.push(src.length ? src[src.length - 1] + 1 : lines.length ? lines[0].s : 0);
    out.push(...mdRun(str, src, 0, str.length));
  }

  // Source text events for str[a, b), split where the source is not contiguous.
  function mdText(src, a, b, raw) {
    const evs = [];
    for (let k = a; k < b;) {
      let j = k + 1;
      while (j < b && src[j] === src[j - 1] + 1) j++;
      evs.push({ t: "text", s: src[k], e: src[j - 1] + 1, raw });
      k = j;
    }
    return evs;
  }

  // [text](url "title") at str[k] === "[": the ranges of its text and URL, or null.
  function mdLink(str, k, end) {
    let depth = 0, close = -1;
    for (let j = k; j < end; j++) {
      if (str[j] === "\\") { j++; continue; }
      if (str[j] === "[") depth++;
      else if (str[j] === "]" && --depth === 0) { close = j; break; }
    }
    if (close < 0 || str[close + 1] !== "(") return null;
    const m = /^\(\s*(<[^<>\n]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)(?:\s+("[^"]*"|'[^']*'|\([^)]*\)))?\s*\)/.exec(str.slice(close + 1, end));
    if (!m) return null;
    const angle = m[1].startsWith("<"), urlA = close + 1 + m[0].indexOf(m[1]) + (angle ? 1 : 0);
    const urlB = urlA + m[1].length - (angle ? 2 : 0);
    return { textA: k + 1, textB: close, urlA, urlB, title: m[2] ? m[2].slice(1, -1) : "", end: close + 1 + m[0].length };
  }

  // Inline HTML inside Markdown: allowed tags become elements, dropped-content tags hide what they wrap.
  function mdHtml(str, k, end, tags, evs, src) {
    const m = HTML_TAG_RE.exec(str.slice(k, end));
    if (!m) return 0;
    const name = m[2].toLowerCase(), at = src[k];
    if (name === "span") return 0;
    if (m[1]) {
      const at2 = tags.map((x) => x.tag).lastIndexOf(name);
      if (at2 >= 0) evs.push({ t: "close", ref: tags.splice(at2, 1)[0], at });
      return m[0].length;
    }
    if (PREVIEW_DROP.has(name)) {
      const closeAt = str.slice(k + m[0].length, end).toLowerCase().indexOf("</" + name);
      if (closeAt < 0) return end - k;
      const gt = str.indexOf(">", k + m[0].length + closeAt);
      return (gt < 0 || gt >= end ? end : gt + 1) - k;
    }
    if (!PREVIEW_INLINE.has(name) && !PREVIEW_BLOCK.has(name)) return m[0].length;
    const attrs = htmlAttrs(m[3]);
    if (name === "img") {
      const label = attrs.alt && attrs.alt.value ? attrs.alt : attrs.src;
      if (!label) return m[0].length;
      const a = { t: "open", tag: "a", attrs: linkAttrs(attrs.src ? attrs.src.value : "", { class: "md-img" }), block: false, at };
      const off = k + 1 + m[2].length;
      evs.push(a, ...mdText(src, off + label.s, off + label.e), { t: "close", ref: a, at });
      return m[0].length;
    }
    if (PREVIEW_VOID.has(name)) {
      if (name === "br" || name === "hr") evs.push({ t: "void", tag: name, attrs: {}, block: name === "hr", at });
      return m[0].length;
    }
    const ev = { t: "open", tag: name, attrs: htmlAllowed(name, attrs), block: false, at };
    evs.push(ev);
    tags.push(ev);
    return m[0].length;
  }

  function mdRun(str, src, start, end) {
    const toks = [], tags = [];
    let k = start, textStart = start, link;
    const flush = (to) => { if (to > textStart) toks.push({ type: "text", a: textStart, b: to }); };
    const evs = (list) => toks.push({ type: "evs", list });
    while (k < end) {
      const c = str[k];
      if (c === "\\" && k + 1 < end && /[!-/:-@[-`{-~]/.test(str[k + 1])) {
        flush(k);
        textStart = k + 1;
        k += 2;
      } else if (c === "`") {
        let n = 1;
        while (str[k + n] === "`") n++;
        let close = -1;
        for (let j = k + n; j < end; j++) {
          if (str[j] !== "`") continue;
          let r = 1;
          while (str[j + r] === "`") r++;
          if (r === n) { close = j; break; }
          j += r - 1;
        }
        if (close < 0) { k += n; continue; }
        flush(k);
        const code = { t: "open", tag: "code", attrs: {}, block: false, at: src[k] };
        evs([code, ...mdText(src, k + n, close, true), { t: "close", ref: code, at: src[close] }]);
        k = textStart = close + n;
      } else if (c === "*" || c === "_") {
        let n = 1;
        while (str[k + n] === c) n++;
        flush(k);
        const prev = k > start ? str[k - 1] : "\n", next = k + n < end ? str[k + n] : "\n";
        const left = !/\s/.test(next) && (!MD_PUNCT.test(next) || /\s/.test(prev) || MD_PUNCT.test(prev));
        const right = !/\s/.test(prev) && (!MD_PUNCT.test(prev) || /\s/.test(next) || MD_PUNCT.test(next));
        toks.push({ type: "delim", ch: c, a: k, total: n, openUsed: 0, closeUsed: 0, opens: [], closes: [],
          canOpen: c === "*" ? left : left && (!right || MD_PUNCT.test(prev)),
          canClose: c === "*" ? right : right && (!left || MD_PUNCT.test(next)) });
        k = textStart = k + n;
      } else if ((c === "[" || (c === "!" && str[k + 1] === "[")) && (link = mdLink(str, c === "!" ? k + 1 : k, end))) {
        const url = str.slice(link.urlA, link.urlB).split(HIDDEN).join("");
        flush(k);
        const a = { t: "open", tag: "a", attrs: linkAttrs(url, c === "!" ? { class: "md-img" } : link.title ? { title: link.title } : {}), block: false, at: src[k] };
        const body = c === "!" ? (link.textB > link.textA ? mdText(src, link.textA, link.textB) : mdText(src, link.urlA, link.urlB))
          : mdRun(str, src, link.textA, link.textB);
        evs([a, ...body, { t: "close", ref: a, at: src[link.textB] }]);
        k = textStart = link.end;
      } else if (c === "<") {
        const auto = /^<([a-zA-Z][a-zA-Z0-9+.-]{1,31}:[^\s<>]*)>/.exec(str.slice(k, end));
        if (auto) {
          flush(k);
          const a = { t: "open", tag: "a", attrs: linkAttrs(auto[1], {}), block: false, at: src[k] };
          evs([a, ...mdText(src, k + 1, k + 1 + auto[1].length), { t: "close", ref: a, at: src[k + auto[0].length - 1] }]);
          k = textStart = k + auto[0].length;
          continue;
        }
        const list = [], n = mdHtml(str, k, end, tags, list, src);
        if (!n) { k++; continue; }
        flush(k);
        evs(list);
        k = textStart = k + n;
      } else k++;
    }
    flush(end);
    for (let ci = 0; ci < toks.length; ci++) {
      const closer = toks[ci];
      if (closer.type !== "delim" || !closer.canClose) continue;
      for (let oi = ci - 1; oi >= 0 && closer.total - closer.openUsed - closer.closeUsed > 0;) {
        const opener = toks[oi], left = opener.type === "delim" ? opener.total - opener.openUsed - opener.closeUsed : 0;
        if (!left || !opener.canOpen || opener.ch !== closer.ch) { oi--; continue; }
        const use = left >= 2 && closer.total - closer.openUsed - closer.closeUsed >= 2 ? 2 : 1;
        opener.openUsed += use;
        const o = { t: "open", tag: use === 2 ? "strong" : "em", attrs: {}, block: false, at: src[opener.a + opener.total - opener.openUsed] };
        opener.opens.unshift(o);
        closer.closes.push({ t: "close", ref: o, at: src[closer.a + closer.closeUsed] });
        closer.closeUsed += use;
        for (let j = oi + 1; j < ci; j++) if (toks[j].type === "delim") toks[j].canOpen = toks[j].canClose = false;
      }
    }
    const out = [];
    for (const tok of toks) {
      if (tok.type === "text") out.push(...mdText(src, tok.a, tok.b));
      else if (tok.type === "evs") out.push(...tok.list);
      else out.push(...tok.closes, ...mdText(src, tok.a + tok.closeUsed, tok.a + tok.total - tok.openUsed), ...tok.opens);
    }
    for (const ev of tags.reverse()) out.push({ t: "close", ref: ev, at: src[end] });
    return out;
  }

  // ── HTML ──

  // Attributes of a tag as { name: { value, s, e } }, s/e being the value's offsets in src.
  function htmlAttrs(src) {
    const out = {}, re = /([a-zA-Z_:][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
    let m;
    while ((m = re.exec(src))) {
      const raw = m[2] ?? m[3] ?? m[4] ?? "", quoted = m[2] !== undefined || m[3] !== undefined;
      const s = m[2] === undefined && m[3] === undefined && m[4] === undefined ? m.index + m[0].length
        : m.index + m[0].length - raw.length - (quoted ? 1 : 0);
      out[m[1].toLowerCase()] = { value: raw, s, e: s + raw.length };
    }
    return out;
  }

  function htmlAllowed(name, attrs) {
    const value = (k) => attrs[k] && decodeEntities(attrs[k].value);
    const digits = (k) => (attrs[k] && /^\d{1,4}$/.test(attrs[k].value) ? attrs[k].value : undefined);
    if (name === "a") return linkAttrs(attrs.href ? attrs.href.value : "", value("title") ? { title: value("title") } : {});
    if (name === "abbr") return value("title") ? { title: value("title") } : {};
    if (name === "td" || name === "th") return { colspan: digits("colspan"), rowspan: digits("rowspan") };
    if (name === "ol") return { start: digits("start") };
    if (name === "details") return { open: attrs.open ? true : undefined };
    return {};
  }

  function htmlEvents(scan, from, out) {
    const re = /<(\/?)([a-zA-Z][\w-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>|<![^>]*>|<\?[^>]*>/g, tags = [];
    const closeTop = (at) => out.push({ t: "close", ref: tags.pop(), at });
    let last = from, m;
    re.lastIndex = from;
    while ((m = re.exec(scan))) {
      const name = (m[2] || "").toLowerCase();
      if (name === "span") continue;
      if (m.index > last) out.push({ t: "text", s: last, e: m.index });
      last = re.lastIndex;
      if (!name) continue;
      if (m[1]) {
        const at = tags.map((x) => x.tag).lastIndexOf(name);
        if (at >= 0) while (tags.length > at) closeTop(m.index);
        continue;
      }
      if (PREVIEW_DROP.has(name)) {
        const closeAt = scan.toLowerCase().indexOf("</" + name, re.lastIndex), gt = closeAt < 0 ? -1 : scan.indexOf(">", closeAt);
        last = re.lastIndex = gt < 0 ? scan.length : gt + 1;
        continue;
      }
      if (!PREVIEW_BLOCK.has(name) && !PREVIEW_INLINE.has(name)) continue;
      const top = tags[tags.length - 1];
      if (top && ((AUTO_CLOSE[name] || []).includes(top.tag) || (top.tag === "p" && PREVIEW_BLOCK.has(name)))) closeTop(m.index);
      const attrs = htmlAttrs(m[3]), block = PREVIEW_BLOCK.has(name), off = m.index + 1 + m[2].length;
      if (name === "img") {
        const label = attrs.alt && attrs.alt.value ? attrs.alt : attrs.src;
        if (!label) continue;
        const a = { t: "open", tag: "a", attrs: linkAttrs(attrs.src ? attrs.src.value : "", { class: "md-img" }), block: false, at: m.index };
        out.push(a, { t: "text", s: off + label.s, e: off + label.e }, { t: "close", ref: a, at: m.index });
      } else if (PREVIEW_VOID.has(name)) out.push({ t: "void", tag: name, attrs: {}, block, at: m.index });
      else {
        const ev = { t: "open", tag: name, attrs: htmlAllowed(name, attrs), block, at: m.index };
        out.push(ev);
        tags.push(ev);
      }
    }
    if (scan.length > last) out.push({ t: "text", s: last, e: scan.length });
    while (tags.length) closeTop(scan.length);
  }

  function previewTree(body, spans) {
    const rodeo = [];
    for (const sp of spans) {
      const ev = { t: "open", span: sp.index, block: false };
      rodeo.push([sp.start, ev, sp.openEnd], [sp.closeStart, { t: "close", ref: ev }, sp.end]);
    }
    rodeo.sort((a, b) => a[0] - b[0]);
    let scan = "", at = 0;
    for (const [s, , e] of rodeo) { scan += body.slice(at, s) + HIDDEN.repeat(e - s); at = e; }
    scan = (scan + body.slice(at)).replace(/<!--[\s\S]*?(?:-->|$)/g, (c) => HIDDEN.repeat(c.length));
    const events = [], fm = frontMatterEnd(body), format = previewFormat(body);
    if (fm) {
      const pre = { t: "open", tag: "pre", attrs: { class: "fm" }, block: true, at: 0 };
      events.push(pre, { t: "text", s: 0, e: body[fm - 1] === "\n" ? fm - 1 : fm, raw: true }, { t: "close", ref: pre, at: fm });
    }
    if (format === "html") htmlEvents(scan, fm, events);
    else {
      const lines = [];
      for (let s = fm; s <= scan.length;) {
        const n = scan.indexOf("\n", s), e = n < 0 ? scan.length : n;
        lines.push({ s, text: scan.slice(s, e) });
        if (n < 0) break;
        s = n + 1;
      }
      mdBlocks(lines, events);
    }
    const b = previewBuilder(body);
    let r = 0;
    const flush = (p, inclusive) => {
      while (r < rodeo.length && (rodeo[r][0] < p || (inclusive && rodeo[r][0] === p))) {
        const ev = rodeo[r++][1];
        if (ev.t === "open") b.open(ev); else b.close(ev);
      }
    };
    const shown = (s, e, raw) => {
      for (let k = s; k < e;) {
        if (scan[k] === HIDDEN) { k++; continue; }
        let j = k + 1;
        while (j < e && scan[j] !== HIDDEN) j++;
        b.text(k, j, raw);
        k = j;
      }
    };
    for (const ev of events) {
      if (ev.t === "text") {
        flush(ev.s, true);
        let cur = ev.s;
        while (r < rodeo.length && rodeo[r][0] < ev.e) {
          shown(cur, rodeo[r][0], ev.raw);
          cur = rodeo[r][0];
          flush(cur, true);
        }
        shown(cur, ev.e, ev.raw);
      } else {
        flush(ev.at, ev.t !== "close");
        b[ev.t](ev);
      }
    }
    flush(Infinity);
    return { format, nodes: b.root.children };
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

  // A string unquoted only when no YAML 1.1 reader takes it for a number, bool or null.
  function yamlString(s) {
    s = String(s);
    return /^[A-Za-z_/@][\w./@-]*$/.test(s) && !/^(true|false|yes|no|on|off|y|n|null)$/i.test(s) ? s : JSON.stringify(s);
  }

  function yamlValue(v) {
    if (v === null || v === undefined) return "null";
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    if (typeof v === "object") return Array.isArray(v) ? "[]" : "{}";
    return yamlString(v);
  }

  // `v` (a mapping or list) as YAML block lines indented by `pad`.
  function yamlLines(v, pad) {
    const nested = (x) => x && typeof x === "object" && Object.keys(x).length;
    const out = [];
    if (Array.isArray(v)) {
      for (const item of v) {
        if (!nested(item)) { out.push(pad + "- " + yamlValue(item)); continue; }
        const inner = yamlLines(item, pad + "  ");
        out.push(pad + "- " + inner[0].slice(pad.length + 2), ...inner.slice(1));
      }
      return out;
    }
    for (const [k, val] of Object.entries(v)) {
      if (nested(val)) out.push(pad + yamlString(k) + ":", ...yamlLines(val, pad + "  "));
      else out.push(pad + yamlString(k) + ": " + yamlValue(val));
    }
    return out;
  }

  // Plan text `text` with lab_in_a_box.lab set to lab-in-a-box lab `lab`: that block replaced,
  // or added first under lab_in_a_box: (which is added when the plan has none).
  function setStoredLab(text, lab) {
    const block = ["  lab:", ...yamlLines(lab, "    ")];
    const lines = text.split("\n");
    const top = lines.findIndex((l) => /^lab_in_a_box:\s*(#.*)?$/.test(l));
    if (top < 0) return text.replace(/\s*$/, "\n") + "\nlab_in_a_box:\n" + block.join("\n") + "\n";
    let end = top + 1;
    while (end < lines.length && !/^[^\s#]/.test(lines[end])) end++;
    const at = lines.slice(top + 1, end).findIndex((l) => /^  lab:/.test(l));
    if (at < 0) { lines.splice(top + 1, 0, ...block); return lines.join("\n"); }
    const start = top + 1 + at;
    let stop = start + 1;
    while (stop < end && (!lines[stop].trim() || /^ {3}/.test(lines[stop]))) stop++;
    while (stop > start + 1 && !lines[stop - 1].trim()) stop--;
    lines.splice(start, stop - start, ...block);
    return lines.join("\n");
  }

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

  // The plan's content: block (rodeo/content.py): where the instructions live, or "".
  function contentBlock(c) {
    if (!c || !c.repo) return "";
    const lines = ["content:", "  repo: " + yamlScalar(c.repo)];
    if (c.ref) lines.push("  ref: " + yamlScalar(c.ref));
    if (c.path) lines.push("  path: " + yamlScalar(c.path));
    lines.push("  format: " + yamlScalar(c.format || "rmstory"));
    return lines.join("\n") + "\n";
  }

  // A bundled profile's plan with this rodeo's name, target and story, and its content: block
  // when opts.content names a repository (without one, the plan's own content: is kept).
  function planFromBase(basePlan, opts) {
    let text = setTopKey(basePlan, "name", opts.name);
    text = setTopKey(text, "deployment_target", opts.target);
    text = dropTopBlock(text, "story");
    if (contentBlock(opts.content)) text = dropTopBlock(text, "content");
    text = text.replace(/\s*$/, "\n");
    return text + "\n" + storyBlock(opts.story) + (contentBlock(opts.content) ? "\n" + contentBlock(opts.content) : "");
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
    return lines.join("\n") + storyBlock(opts.story) + (contentBlock(opts.content) ? "\n" + contentBlock(opts.content) : "");
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
    const work = new Set([MISSING_ADDON, ...(rodeo.workNeeds || [])]);
    const todo = rodeo.chapters.filter((ch) => (ch.needs || []).some((n) => work.has(n)));
    if (todo.length) {
      const tags = (ch) => (ch.needs || []).filter((n) => n !== MISSING_ADDON && work.has(n));
      lines.push("", "## Work needed", "", "These chapters need something no lab engine provides yet (`" + MISSING_ADDON + "`, or a need named below):", "",
        ...todo.map((ch) => "- " + ch.title + " (`" + ch.id + "`)" + (tags(ch).length ? ": " + tags(ch).map((n) => "`" + n + "`").join(", ") : "")));
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
      lines.push("", "The lab designed in lab-in-a-box's lab-builder is `lab_in_a_box.lab` in `rodeo-plan.yaml`:",
        "rodeo deploys it as written, with this host's network, addresses and secrets.");
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
    if (rodeo.base) out.push({ path: "builder.yaml", content: "# The profile this rodeo is built on: rodeo new --from-zip copies it first.\nbase: " + rodeo.base + "\n" });
    out.push({ path: "README.md", content: readme(rodeo) });
    return out;
  }

  // ── infrastructure / instructions (F7) ───────────────────────────────────

  const PARTS = ["infrastructure", "instructions"];

  function partOf(path) { return /^(story|checks)\//.test(path) ? "instructions" : "infrastructure"; }

  function partReadme(rodeo, part) {
    const name = rodeo.name, title = rodeo.title || name;
    if (part === "instructions") {
      return ["# " + title + " · instructions", "", "Generated by the Rodeo Builder: the chapters (`story/`) and their checks",
        "(`checks/`) of the rodeo `" + name + "`, without its lab.", "", "Push this directory to a repository and point the",
        "infrastructure's plan at it, then render it on a deployed lab with `rodeo content apply`:", "", "```yaml",
        "content:", "  repo: https://github.com/<owner>/<repo>", "  path: story", "  format: rmstory", "```", ""].join("\n");
    }
    return ["# " + title + " · infrastructure", "", "Generated by the Rodeo Builder: the lab of the rodeo `" + name + "`",
      "(plan, definition, lab-in-a-box lab), without its chapters.", "", "```bash",
      "rodeo new " + name + " --from-zip " + name + "-infrastructure.zip", "rodeo up --profile " + name, "```", "",
      contentBlock(rodeo.content) ? "The plan's `content:` names the instructions; `rodeo up` renders them, `rodeo content apply` updates them."
        : "Its instructions are not part of this download: add a `content:` block to the plan, or install the whole rodeo.", ""].join("\n");
  }

  // The files of one part, its own README instead of the rodeo's.
  function partFiles(rodeo, part) {
    return files(rodeo).filter((f) => f.path !== "README.md" && partOf(f.path) === part)
      .concat({ path: "README.md", content: partReadme(rodeo, part) });
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

  // ── stored credentials (the live builder's vault) ───────────────────────
  // `vault` is the server's GET vault answer ({types, items}). A type maps plan secret names to
  // its field names in `keys`; a freeform type stores any `key: value` rows instead.

  const VAULT_KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;
  const VAULT_MIN_PASSWORD = 8;

  function vaultType(vault, id) { return ((vault && vault.types) || []).find((t) => t.id === id) || null; }

  // The stored credentials that can fill the plan secret `key`: those whose type maps it, and every
  // freeform one (its field names are only known once it is opened).
  function vaultCandidates(vault, key) {
    return ((vault && vault.items) || []).filter((item) => {
      const type = vaultType(vault, item.type);
      return !!type && (!!type.freeform || Object.prototype.hasOwnProperty.call(type.keys || {}, key));
    });
  }

  // {secret: value} for each of `keys` that an opened credential's `fields` provide.
  function vaultFill(type, fields, keys) {
    const out = {};
    for (const k of keys) {
      const field = type && type.freeform ? k : ((type && type.keys) || {})[k];
      if (field && Object.prototype.hasOwnProperty.call(fields, field) && typeof fields[field] === "string" && fields[field] !== "") out[k] = fields[field];
    }
    return out;
  }

  // The fields object vault-add stores: the type's non-empty fields, or a freeform form's rows.
  function vaultFields(type, form) {
    if (type && type.freeform) return Object.fromEntries((form.rows || []).map((r) => [r.key.trim(), r.value]));
    return Object.fromEntries((type ? type.fields : []).map((f) => [f.name, (form.fields || {})[f.name] || ""])
      .filter(([, v]) => v !== ""));
  }

  // What stops the add-credential form ({name, fields | rows, password, repeat}) from being saved.
  function vaultErrors(type, form) {
    const errors = [];
    if (!String(form.name || "").trim()) errors.push("Give the credential a name.");
    if (!type) errors.push("Pick a type.");
    else if (type.freeform) {
      const rows = form.rows || [], seen = new Set();
      if (!rows.length) errors.push("Add at least one field.");
      for (const r of rows) {
        const key = r.key.trim();
        if (!VAULT_KEY_RE.test(key)) errors.push("Field name \"" + key + "\": lowercase letters, digits and _, starting with a letter.");
        else if (seen.has(key)) errors.push("Field " + key + " is there twice.");
        else if (r.value === "") errors.push("Fill in " + key + ".");
        seen.add(key);
      }
    } else {
      for (const f of type.fields) if (!f.optional && !(form.fields || {})[f.name]) errors.push("Fill in " + f.label + ".");
    }
    if (String(form.password || "").length < VAULT_MIN_PASSWORD) errors.push("The password needs at least " + VAULT_MIN_PASSWORD + " characters.");
    else if (form.password !== form.repeat) errors.push("The passwords don't match.");
    return errors;
  }

  // The secrets.yaml key names of `vault.file_secrets` to offer for the plan secret `key`: those
  // sharing a `_`-separated word with it first, then the rest, each group alphabetical.
  function vaultFileKeys(vault, key) {
    const words = new Set(String(key).split("_").filter(Boolean));
    const keys = ((vault && vault.file_secrets) || []).map((f) => f.key).sort();
    const near = (k) => k.split("_").some((w) => words.has(w));
    return [...keys.filter(near), ...keys.filter((k) => !near(k))];
  }

  return {
    MISSING_ADDON, SOURCE_LANGUAGE, LANGUAGES, slugify, uniqueId, parseSpans, parseAttrs, kind, needsId, openTag, inner,
    withKind, wrapTarget, nextId, wrap, retag, unwrap, idsIn, validate, warningsByChapter, engineNeeds, variantsIn, storyIndex,
    setTopKey, setStoredLab, dropTopBlock, storyBlock, planFromBase, planLabinabox, chapterFile, chaptersYaml,
    storyYaml, checkScript, readme, files, labJsonAddons, crc32, zip,
    renumber, nameMissing, translatableIds, storedSource, sourceTexts, missingPlaceholders, stringsFiles, translatedLanguages, draftPrompt, storyRole, storyParts, maskExercises, retoldChapters, contentBlock, PARTS, partOf, partFiles, CATALOG_REPO, catalogEntry, publishLinks,
    parseSourceUrl, importGroupId, detectFormat, chapterFromMarkdown, chapterFromInstruqt, chaptersMeta, storyIds, chapterFromStory,
    previewFormat, previewTree, safeHref,
    VAULT_KEY_RE, VAULT_MIN_PASSWORD, vaultType, vaultCandidates, vaultFill, vaultFields, vaultErrors, vaultFileKeys,
  };
})();

if (typeof module !== "undefined" && module.exports) module.exports = RB;
else window.RB = RB;
