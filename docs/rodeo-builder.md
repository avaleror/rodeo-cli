# Rodeo Builder

**[→ Open the Rodeo Builder](../builder/){ .md-button .md-button--primary }**

The Rodeo Builder composes a new rodeo in the browser: pick the lab engine, put
story chapters in order, tag their text for translation and story variants, and
download the result as a profile you install with `rodeo`.

It comes in two versions of the same page:

- **Published** (the button above): a static page built by GitHub Actions with
  the docs. Nothing you do there leaves your browser; you download a zip.
- **Live**, installed with rodeo-cli: `rodeo builder` serves the page on your
  machine, reads its data live and adds **Save to profiles**, which writes the
  rodeo straight into `~/.rodeo/profiles/<name>/`.

```bash
rodeo builder                 # opens http://127.0.0.1:8678/
rodeo up --profile <name>     # after Save to profiles
```

## What you compose

- **Lab engine.** `suse-virt`, `rancher` and `suse-edge` are rodeo's own
  engines; each shows what it provides (for example Harvester, KubeVirt,
  Longhorn, Rancher, Fleet). `lab-in-a-box` builds any lab lab-in-a-box can:
    - **Open in Lab Builder** opens lab-in-a-box's lab-builder with this rodeo's
      add-ons filled in. Download the lab.json there and upload it here to bring
      its add-ons back.
    - **Embed here** opens the lab-builder inside the page. Handing the lab back
      directly needs a lab-in-a-box release with the embed hand-off; until then,
      use Download there and Upload here.
    - **Import existing lab** starts from a bundled lab-in-a-box rodeo
      (`smlm-workshop`) or from a lab.json.
- **Chapters.** The library lists every workshop's chapters: the bundled
  examples' `story/` directories, the workshops whose chapters live in their own
  repository (listed in `rodeo/builder/chapter_sources.yaml`: the SMLM workshop's
  Instruqt track and the Virtualization workshop's exercises), and the chapters
  you create with **+ New**. Each workshop shows the lab engine and profile its
  chapters run on; **use** switches the rodeo to them (for example `suse-virt`
  with the `virt-workshop-aws` profile, whose extra setup the exercises need).
  Drag chapters into the rodeo (anywhere on the list) or click them; click a
  chapter again to take it out. Click a row in the rodeo to insert new chapters
  after it instead of at the end. Reorder by dragging or with Alt+↑/↓. Removing a
  chapter or a span offers **Undo** for a few seconds. When every chapter comes
  from workshops that run on one other engine, a banner offers to switch to it. A chapter that needs something the lab doesn't provide is marked, and
  for lab-in-a-box one click adds the missing add-on. Chapters that come with a
  check script bring it along. A new chapter can only ask for what an engine
  really provides: the native engines' capabilities and lab-in-a-box's catalogue
  (add-ons, infrastructure services, Kubernetes cluster types). If the
  lab-in-a-box catalogue could not be read, the page says why instead of showing
  an empty list. `missing_addon` is always offered: a placeholder for something no
  engine provides yet. It only marks the chapter as "work needed" (also listed in
  the download's README); it never blocks the rodeo and never reaches the lab.
- **Story.** **N spans ✎** opens the story editor (rows with span warnings show
  how many). Select text and click **Tag selection**, use the toolbar, or press
  Alt+T / Alt+I / Alt+N to mark it Translatable, Invariant or No-lang;
  double-click a span to change its type,
  language, id or story variant. Ids follow rmstory's rules (`<chapter>.N`,
  `<parent>.N` when nested) and the editor shows the same warnings as
  `rmstory validate`. Translations are filled after install, by
  `rodeo story render --language <lang>`.

**Review and download** (step 03) checks the rodeo before you download it: the
lab covers every chapter, the span ids are valid (each warning has a **Fix**
button), the exact files in the zip, and the commands to install it, ready to
copy. Missing lab features and span warnings never block the download.

The page keeps a draft of your rodeo in this browser. After a reload it offers
to restore it.

The right-hand panel sets the name, story title, language, deployment target and
story variant, and previews every file of the download. **plan.yaml** is an
editor: change anything and the download keeps it; `name`, `deployment_target`
and `story` stay in sync with the rodeo tab, and **Reset to generated** drops your
edits. Changing the lab engine regenerates the plan, after asking.

## Install what you downloaded

The zip holds one profile directory, `<name>/`, with `rodeo-plan.yaml`, the
chapters (`story/NN-<id>.md`), `story/chapters.yaml` (minutes, needs and check
script per chapter), one `story/stories/<variant>.yaml` per story variant,
`checks/check-<id>.sh` stubs, a README with the exact commands and, when the
rodeo starts from a profile (`suse-virt`, `rancher`, `suse-edge` or an imported
lab-in-a-box rodeo), `builder.yaml` naming that base profile.

```bash
rodeo new <name> --from-zip <name>.zip
rodeo up --profile <name>
```

`--from-zip` copies the base profile, lays the zip's files over it and writes
`~/.rodeo/profiles/<name>/`. A new lab-in-a-box rodeo carries its own
`definition.yaml` and needs no base. `--from <profile>` overrides the base and
`--force` replaces an existing profile. The zip may only hold the files the
builder writes; anything else (other paths, links, oversize files) is refused
before anything is written. **Save to profiles** in `rodeo builder` does the same
without the zip.

## The live builder

`rodeo builder` serves `rodeo/builder/htdocs/` and answers the page's requests
with `rodeo/builder/api.py` (`rodeo/builder/server.py`, standard library only).
At start it fetches the chapter sources (a source that can't be fetched is left
out, with a warning) and the lab-in-a-box catalogue: `--labinabox <checkout>`,
else `RODEO_LABINABOX_PATH`, else lab-in-a-box's latest release, fetched into
`~/.rodeo/vendor/lab-in-a-box/` the way a deploy does.

| Option | Default | |
|---|---|---|
| `--host`, `--port` | `127.0.0.1`, `8678` | Where to listen (`--port 0` picks a free port). |
| `--expose` | off | Required for a non-loopback `--host`: anyone who reaches it can save profiles as you. |
| `--allow-host NAME` | | Extra `Host` name to accept (the name or IP clients use with `--expose`). |
| `--labinabox`, `--lab-builder-url` | latest release, published lab-builder | Catalogue source and the lab-builder the page links to. |
| `--source ID=CHECKOUT`, `--no-fetch-sources` | fetch all | Local chapter sources, or none. |
| `--open/--no-open` | open | Open the page in a browser. |

Writes are guarded: the server only answers requests whose `Host` is an allowed
name (loopback names by default), so other sites can't reach it through DNS
rebinding, and **Save** sends a per-run token that only the served page has. The
page runs under a Content-Security-Policy that allows its own files, Google Fonts
and the lab-builder frame.

## How the published page is built

The published page is the live page's static subset. `scripts/build-builder-static.py`
embeds what `rodeo/builder/api.py` answers (engines and bundled chapters from
`rodeo/data/`, the chapter sources' chapters, and the catalogue of lab-in-a-box's
latest release) into the page and marks it static, which hides **Save to
profiles**; the docs workflow publishes it at `builder/`. The build fetches each
chapter source with a shallow, sparse git clone (only the chapter files and check
scripts) and fails if one can't be fetched, or if the lab-in-a-box catalogue
can't be read. Build it locally:

```bash
python3 scripts/build-builder-static.py --output /tmp/builder --labinabox /path/to/lab-in-a-box
python3 -m http.server -d /tmp/builder 8000
```

`--source <id>=<checkout>` uses a local checkout for one chapter source,
`--no-fetch-sources` builds without them (offline), and `--no-labinabox` builds
without the lab-in-a-box catalogue (the page then shows it as unavailable).
