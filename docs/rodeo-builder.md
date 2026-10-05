# Rodeo Builder

**[→ Open the Rodeo Builder](../builder/){ .md-button .md-button--primary }**

The Rodeo Builder composes a new rodeo in the browser: pick the lab engine, put
story chapters in order, tag their text for translation and story variants, and
download the result as a profile you install with `rodeo`. It is a static page:
nothing you do there leaves your browser.

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
- **Chapters.** The library lists the chapters of the bundled examples that have
  a `story/` directory, plus the ones you create with **+ New**. Drag them into
  the rodeo or click them; reorder by dragging or with Alt+↑/↓. A chapter that
  needs something the lab doesn't provide is marked, and for lab-in-a-box one
  click adds the missing add-on.
- **Story.** **N spans ✎** opens the story editor: select text and mark it
  Translatable, Invariant or No-lang; double-click a span to change its type,
  language, id or story variant. Ids follow rmstory's rules (`<chapter>.N`,
  `<parent>.N` when nested) and the editor shows the same warnings as
  `rmstory validate`. Translations are filled after install, by
  `rodeo story render --language <lang>`.

The right-hand panel sets the name, story title, language, deployment target and
story variant, and previews every file of the download.

## Install what you downloaded

The zip holds one profile directory, `<name>/`, with `rodeo-plan.yaml`, the
chapters (`story/NN-<id>.md`), `story/chapters.yaml` (minutes, needs and check
script per chapter), one `story/stories/<variant>.yaml` per story variant,
`checks/check-<id>.sh` stubs and a README with the exact commands.

For `suse-virt`, `rancher`, `suse-edge` or an imported lab-in-a-box rodeo, the
files go on top of the bundled profile they start from:

```bash
rodeo new <name> --from <base>
unzip -o <name>.zip -d ~/.rodeo/profiles/
rodeo up --profile <name>
```

A new lab-in-a-box rodeo is complete on its own (it carries its
`definition.yaml`):

```bash
unzip <name>.zip -d ~/.rodeo/profiles/
rodeo up --profile <name>
```

## How it is built

The page lives in `rodeo/builder/htdocs/` (plain HTML, CSS and JavaScript, no
build step). `scripts/build-builder-static.py` embeds what
`rodeo/builder/api.py` answers (engines, workshops and chapters from
`rodeo/data/`, and the add-ons of lab-in-a-box's latest release) into the page,
and the docs workflow publishes it at `builder/`. Build it locally:

```bash
python3 scripts/build-builder-static.py --output /tmp/builder [--labinabox /path/to/lab-in-a-box]
python3 -m http.server -d /tmp/builder 8000
```
