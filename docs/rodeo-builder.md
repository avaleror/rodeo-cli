# Rodeo Builder

**[→ Open the Rodeo Builder](../builder/){ .md-button .md-button--primary }**

The Rodeo Builder composes a new rodeo in the browser: pick the lab engine, put
story chapters in order, tag their text for translation and with story ids, and
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

- **Lab engine.** The engines are the rodeo types installed (a plugin's
  included); the deployment targets and instructions formats offered are the
  ones rodeo knows, never a fixed list. `suse-virt`, `rancher` and `suse-edge`
  are rodeo's own engines; the **(i)** on each shows its size and what it provides (for example
  Harvester, KubeVirt, Longhorn, Rancher, Fleet). `lab-in-a-box` builds any lab lab-in-a-box can:
    - **Embed here** (the default) opens the lab-builder inside the page with
      this rodeo's lab loaded; "Send this lab" there hands it back.
    - **Import existing lab** starts from a bundled lab-in-a-box rodeo
      (`smlm-workshop`) or from a lab.json. An imported rodeo stays selected on
      every tab, so its lab is the one the lab-builder opens.
    - **Open in Lab Builder** opens lab-in-a-box's lab-builder in its own tab
      with this rodeo's lab filled in (only its add-ons when the lab is too large
      for a link). Download the lab.json there and upload it here to bring the lab back.

  **Provides** lists what the lab offers chapters, from lab-in-a-box's whole
  catalogue: add-ons, Kubernetes clusters (`rke2`, `k3s`, ...) and infrastructure
  (such as `pxe`). Type a name to add one. Add-ons go into the generated plan;
  Kubernetes clusters and infrastructure are set up in the lab-builder (Embed
  here), whose lab brings them back. With a lab, it lists that lab's add-ons
  (on VMs and Kubernetes clusters), Kubernetes cluster types and infrastructure.

  A lab brought back (sent, or uploaded as lab.json) becomes the plan's
  `lab_in_a_box.lab` (see the plan reference); the rest of the plan, such as an
  imported workshop's variants and `workshop:` block, is kept. Its add-ons are
  then edited in the lab-builder.

  No credential from that lab reaches the plan. Values of password, token,
  registration code, API key and secret key fields become `??<field>`
  placeholders, and those placeholders are added to the plan's
  `operator_secrets`, so `rodeo up` asks for them once; the toast names them.
  Node names and addresses written literally in add-on settings (for example
  `client_registration_server: smlm.rodeo.lab`) become `${node_fqdn:smlm}` /
  `${node_ip:smlm}`, which rodeo fills with each host's and instance's values;
  settings unchanged from the lab the rodeo already had stay as written. Addresses
  that look like the lab's but belong to no node are listed in the panel to check.

  With a lab, the panel lists its nodes (address, vCPU, RAM, add-ons, cluster)
  and Kubernetes clusters, read-only, and flags what rodeo deploys but does not
  fully drive: Harvester nodes and PXE-booted nodes. It also lists the lab's
  fields the builder's lab-in-a-box release doesn't know (its schema is read with
  the catalogue), which `rodeo up` would refuse. On the command line,
  `rodeo new <name> --from-lab-json <file>` does the same import.
- **Start from a workshop.** Above the chapter library, a list of whole workshops
  to start a rodeo from: the bundled examples, the chapter sources that come with
  a profile, every published rodeo in the catalog, and the bundled lab-in-a-box
  rodeos (also under the lab panel's **Import existing lab**). **Start from**
  replaces the rodeo's lab and chapters with the workshop's: its lab engine and
  profile (for example `suse-virt` with the `virt-workshop-aws` profile, whose
  extra setup the exercises need) and all its chapters (**Undo** restores them).
  A catalog rodeo is fetched when you start from it, so it brings everything it
  was published with: its plan, its lab-in-a-box lab, its chapters with their
  checks, and its translations, language branches included. The published page
  can't fetch it; there its entry shows `rodeo new <name> --from <repo>`.
- **Chapters.** The library lists every workshop's chapters, to add to the rodeo: the bundled
  examples' `story/` directories, the workshops whose chapters live in their own
  repository (listed in `rodeo/builder/chapter_sources.yaml`: the SMLM workshop's
  Instruqt track and the Virtualization workshop's exercises), the `story/` of
  every published rodeo in the catalog (`rodeo/data/catalog.d/`, unless
  `chapter_sources.yaml` already names its repository; its translations come
  along, from `story/strings/` and from each language branch, so adding one of its
  chapters adds the translations of its spans you don't have yet), and the chapters you
  create with **+ New**. Workshops start collapsed: click a workshop's title to
  show its chapters. The **(i)** next to it (hover, or click to keep it open)
  shows where the chapters come from, the lab engine and profile they run on,
  their count and time, and what they need. **+ all** adds its chapters to the
  rodeo's. A chapter card shows its title; hover over it for its
  time and needs, or turn on the list button next to the search to show them on
  every card. The funnel button next to it shows only the workshops for this lab
  engine, the chapters this lab can run, or the chapters not in the rodeo yet.
  Drag chapters into the rodeo (anywhere on the list) or click them; click a
  chapter again to take it out. Click a row in the rodeo to insert new chapters
  after it instead of at the end. Reorder by dragging or with Alt+↑/↓. Removing a
  chapter or a span offers **Undo** for a few seconds. When every chapter comes
  from workshops that run on one other engine, a banner offers to switch to it. A chapter that needs something the lab doesn't provide is marked, and
  for lab-in-a-box one click adds the missing add-on. Chapters that come with a
  check script bring it along. A new chapter's **Requires from the lab** field
  suggests, as you type, what an engine really provides: the native engines'
  capabilities and lab-in-a-box's catalogue (add-ons, infrastructure services,
  Kubernetes cluster types). If the lab-in-a-box catalogue could not be read, the
  page says why. When nothing matches, Enter adds what you typed as a new need: a
  placeholder for something no engine provides yet, like `missing_addon`. Such a
  need only marks the chapter as "work needed" (also listed, by name, in the
  download's README); it never blocks the rodeo and never reaches the lab.
- **Import.** **Import** above the library adds a workshop from a URL: a git
  repository or a directory in one (`https://<host>/<owner>/<repo>/tree/<ref>/<dir>`).
  The builder detects the format (a rodeo `story/`, an Instruqt track
  `<dir>/*/assignment.md`, or markdown `<dir>/*.md`) and lists its chapters as a
  new group; **×** removes the group again. The published page reads public
  github.com repositories through the GitHub API (60 unauthenticated requests an
  hour); the rodeo builder server clones any public https repository (shallow
  and sparse, no credentials). Imported groups are kept with the draft.
- **Story.** **Edit** on a chapter opens the story editor (rows with span
  warnings show how many). Select text and click **Tag selection**, use the toolbar, or press
  Alt+T / Alt+I / Alt+N to mark it Translatable, Don't translate or No-lang.
  **Story** (Alt+S) marks the text students get retold: it takes the chapter's
  story, or asks for the story's name, offering the one the other chapters tell.
  When the chapters tell more than one story, Review warns and offers **Make all
  the same story**. Double-click a span to change its type,
  language, id or story id. Ids follow rmstory's rules (`<chapter>.N`,
  `<parent>.N` when nested) and the editor shows the same warnings as
  `rmstory validate`. The source and the preview sit side by side and scroll
  together; the preview renders the chapter as Markdown or HTML (only safe tags,
  images as links), with its spans marked. Ctrl+Z / Ctrl+Shift+Z undo and redo;
  **Cancel** closes the editor and discards its changes, **Done** keeps them.
- **Translations.** The story editor's **translations** tab lists every
  translatable span per language of the rodeo. **Add new language…** (there, or
  at the end of the rodeo tab's Language list, which shows only the rodeo's own
  languages) asks for its code (`es`, `pt-br`: named like the language branch
  Publish puts it in) and starts it empty, as a draft copy of another
  translation or, on the live page, translated with an rmstory engine; it can
  also become the rodeo's language (`story.language`). Click a cell to write or review a
  translation; a nested span stays in it as `<span id="..."/>`. The download
  carries them in rmstory's translation store, `story/strings/`, and
  `rodeo story render --language <lang>` uses them (with `--engine` for the
  spans still missing).

**Review and download** (step 03) checks the rodeo before you download it: the
lab covers every chapter, the span ids are valid (each warning has a **Fix**
button), the exact files in the zip (listed when you click **N files in
<name>.zip**), and the commands to install it, ready to copy. Missing lab features and span warnings never block the download.

The page keeps a draft of your rodeo in this browser. After a reload it offers
to restore it.

### With rmstory on the live builder

When `rmstory` is installed where `rodeo builder` runs (`sudo rodeo install-deps
--story`), the live page also uses it:

- **Translate missing** (translations tab): fills the chosen language's missing
  translations with an rmstory engine (`rmstory extract --engine <e> --to <lang>`).
  They are stored as `draft`; what you wrote or reviewed is kept. rmstory also
  gives unnamed spans an id (`<chapter>.N`).
- **Validate** (step 03): rmstory's own `validate` on the chapters.
- **Draft with an engine** (New chapter): an engine that can generate text
  (`rmstory engines` says "translate + generate", for example `claude-code`,
  `ollama` or `gemini`) drafts the chapter from your description, told what the
  lab provides and which chapters come before it. Its spans are renumbered
  `new-chapter.N`; review the draft before you create the chapter.
- **New story** (rodeo tab): the same exercises told as a different story. You
  say what is what with the span tags (story editor):
  - **story**: spans tagged with a story id (`hist`): retold;
  - **exercise**: translatable spans without a story id: translated, never
    retold;
  - **fixed**: don't-translate (`no`) or no-lang spans, and text outside spans: kept
    word for word.

  Say what the new story is about and pick an engine that can generate text;
  `rmstory generate rewrite --theme` rewrites the story spans and the exercise
  spans never reach the engine. The retold chapters (ids `<chapter>-<theme>`,
  story span ids `<theme>.<id>`; exercise and fixed spans keep their ids and
  translations) become a library group and replace the rodeo's chapters;
  **Undo** puts the originals back. They live only in this browser (the page's
  draft) until you download, save to profiles or publish.

Engines read their keys from the environment `rodeo builder` runs in (for example
`DEEPL_AUTH_KEY`, `GEMINI_API_KEY`, `RMSTORY_OLLAMA_URL`, or a logged-in `claude`
command for `claude-code`) or from a stored credential: the Settings page stores
one per provider (*Google Gemini API key*, *DeepL API key*, …), encrypted under its
own password. An engine that needs a key and has neither is listed in red as
*no credential*; running it explains how to add one and links to the Settings
form with that type chosen. With stored credentials, the page asks which one
(when there are several) and its password; the key is decrypted on the server and
given only to rmstory, never to the page. Stored credentials work where the
deploy dialog's do (a builder on loopback, or behind the web server's login).
The published page writes translations by hand only.

The right-hand panel sets the name, story title, language, deployment target and
**Story told to students** (shown when the chapters have spans tagged with a story id: *All
stories together* renders the chapters whole; a story renders only that story's
spans, in order), and previews every file of the download. **plan.yaml** is an
editor: change anything and the download keeps it; `name`, `deployment_target`
and `story` stay in sync with the rodeo tab, and **Reset to generated** drops your
edits. Changing the lab engine regenerates the plan, after asking.

## Infrastructure and instructions

The page keeps the lab (**Infrastructure**: engine, base profile, plan,
definition, lab-in-a-box lab) apart from what students read (**Instructions**:
chapters, stories, translations, checks). Step 03 downloads either the
whole rodeo (**Download: All**) or one part: **Infrastructure definition only** (`<name>-infrastructure.zip`,
installable with `rodeo new --from-zip`) or **Instructions only**
(`<name>-instructions.zip`: `story/` and `checks/`, laid out to be pushed as a
content repository).

**Where students' instructions come from** (rodeo tab) is *This rodeo's chapters*
(the default: the chapters in the middle column, `story/` in the download) or *A
repository*, which points the plan at instructions kept in their own repository
(for example the `-instructions` one Publish creates); the chapters column then
says the deployed instructions come from there. The URL (`https://<host>/<owner>/<repo>[/tree/<ref>/<path>]`)
and format become the plan's `content:` block. `rodeo up` renders them after
the lab is up, installs the repository's `checks/` for every user of the host and
runs its `custom/scripts/`, and `rodeo content apply` updates them on a running lab without
redeploying (see [the plan reference](reference/plan.md#content-the-labs-instructions-apart-from-its-infrastructure)).

## Install what you downloaded

The zip holds one profile directory, `<name>/`, with `rodeo-plan.yaml`, the
chapters (`story/NN-<id>.md`), `story/chapters.yaml` (minutes, needs and check
script per chapter), one `story/stories/<story id>.yaml` per story,
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

A published rodeo (see [Publish to GitHub](#publish-to-github)) installs the same
way from its repository, and a catalog rodeo by its name alone:

```bash
rodeo new <name> --from https://github.com/<owner>/<repo>[@<ref>]
rodeo up --profile <catalog name>     # fetched and installed on first use
rodeo profiles                        # lists the catalog too
```

The repository is fetched shallow, without credentials, and only the files a
builder rodeo may hold are installed; `~/.rodeo/profiles/<name>/rodeo.lock`
records the repository, ref and commit. `--force` re-fetches.

## The live builder

`rodeo builder` serves `rodeo/builder/htdocs/` and answers the page's requests
with `rodeo/builder/api.py` (`rodeo/builder/server.py`, standard library only).
It links to and embeds lab-in-a-box's published static lab-builder, or the one
`--lab-builder-url` names. With `--start-lab-builder` it starts the checkout's own
dynamic lab-builder (`webui/run-local.py`) next to it on `--lab-builder-port`,
opened on the host name the browser used for the builder (the published one when
the checkout has none or it can't start). That lab-builder has no login or token,
so any site you browse can save labs through it: it is off by default and only
allowed on a loopback `--host`.
At start it fetches the chapter sources (a source that can't be fetched is left
out, with a warning) and the lab-in-a-box catalogue: `--labinabox <checkout>`,
else `RODEO_LABINABOX_PATH`, else lab-in-a-box's latest release, fetched into
`~/.rodeo/vendor/lab-in-a-box/` the way a deploy does.

| Option | Default | |
|---|---|---|
| `--host`, `--port` | `127.0.0.1`, `8678` | Where to listen (`--port 0` picks a free port). |
| `--expose` | off | Required for a non-loopback `--host`: anyone who reaches it can save profiles as you. **Publish** then uses the github.com pages, never this host's GitHub login; **Deploy** and stored credentials need a login. |
| `--allow-host NAME` | | Extra `Host` name to accept (the name or IP clients use with `--expose`). |
| `--labinabox` | latest release | lab-in-a-box checkout for the catalogue and the lab-builder. |
| `--lab-builder-url URL` (`RODEO_LAB_BUILDER_URL`) | the published lab-builder | A lab-builder you already run (for example on an automation node) for the page to link to and embed; nothing is started. |
| `--start-lab-builder` | off | Start the checkout's own dynamic lab-builder. Loopback `--host` only. |
| `--lab-builder-port` | `8677` | Port of the lab-builder `--start-lab-builder` starts, on the same address as `--host`. |
| `--source ID=CHECKOUT`, `--no-fetch-sources` | fetch all | Local chapter sources, or none. |
| `--open/--no-open` | open | Open the page in a browser. |

Writes are guarded: the server only answers requests whose `Host` is an allowed
name (loopback names by default), so other sites can't reach it through DNS
rebinding, and **Save** sends a per-run token that only the served page has. The
page runs under a Content-Security-Policy that allows only its own files (the fonts
ship in `htdocs/fonts/`) and the lab-builder frame.

## Deploy without saving

**Deploy** (header) runs the rodeo on the builder's host exactly as it is on the
page, without saving it to your profiles first. It needs root for `rodeo up`:
the builder runs as root or the user has passwordless sudo (`sudo -n true`).
Otherwise, and on the published page, the button is greyed out and says why on
hover.

- On an `--expose` builder, deploying and using stored credentials need a
  login from `/etc/rodeo-builder/htpasswd`: the first **Deploy** makes the
  browser ask for it (Cancel leaves the page as it was), and the browser sends
  it with every request after. The builder checks a login again after 5
  minutes. The builder serves plain HTTP, so the login crosses the network
  unencrypted. On loopback no login is asked.

- The dialog says plainly that the rodeo is not saved, and offers **Save to
  profiles and deploy** next to **Deploy without saving**.
- The deploy runs `rodeo up --yes --no-tmux --dir <lab>` from a copy in
  `~/rodeo-labs/<name>-unsaved-<UTC time>/` (base profile plus the page's files,
  as Save lays them out), its output in `<lab>/.builder-deploy.log`. Secrets
  rodeo can generate go to the lab's `.rodeo-secrets.yaml`; ones only you have
  (`operator_secrets`) are asked for in the dialog and written to
  `~/.rodeo/secrets.yaml`, never shown again.
- A banner stays while it runs and after it ends, with the lab directory, the
  log, **Stop** (interrupts `rodeo up` like Ctrl-C) and **Save to profiles**,
  and keeps saying the rodeo is not saved until you save it. One deploy runs at
  a time; rerun or resume from the lab directory with `rodeo up`.

## Settings and stored credentials

**Settings**, at the right of the header on the live builder, opens the
Settings page in a new tab: `https://<builder host>/rodeo-builder/settings/`.
It keeps the credentials you deploy with — SCC registration codes, registry and
Application Collection logins, AWS keys, SSH keys, GitHub tokens, or any
`key: value` fields — encrypted on that host, one file per credential in
root's `~/.rodeo/vault/`, the store the builder reads.

The page is served by the host's web server (Apache, set up by `install.sh` on a
KVM host, see [Install](install.md#settings-page)), over HTTPS only, and only to
a user who logs in: the browser asks for a user from
`/etc/rodeo-builder/htpasswd`, and **Cancel** shows a page with **Log in** (ask
again) and **Close**. Its API, `/rodeo-builder/admin?action=list|add|delete`,
answers `403` without HTTPS, `401` without a logged-in user and `415` to a POST
that is not `application/json`.

- Each credential has its own password, at least 8 characters, asked when it is
  added, every time it is used and to delete it: a delete with the wrong
  password is refused. Neither the password nor the values are kept by the
  page: a table shows each credential's name, application, engine (`rodeo` for
  the ones stored here), whether it is encrypted, when it was added and last
  used, never its values. The application of a rodeo credential is its type.
- Credentials of other engines are listed and used too, with their engine's
  name: today the lab-in-a-box engine's, from `/etc/lab_creation/credentials/`;
  their application is the part of the file name `<application>-<account>.yaml`
  before the first `-` (`aws-tmm.yaml`: `aws`).
  They are read-only in rodeo (no **Delete**). One stored without encryption
  opens without a password; an encrypted one asks for its passphrase.
- Failed passwords and logins wait longer from the fifth in a row on: 1 minute,
  then 2, 4, … up to 1 hour between tries. A success resets the count.
- The builder's own API adds and deletes credentials too, for scripts:
  `POST /api?action=vault-add` (`{name, type, fields, password}`) and
  `vault-delete` (`{id, password}`), with the page's `X-Rodeo-Builder-Token` and
  an `Authorization: Basic` login checked against `/etc/rodeo-builder/htpasswd`
  (Apache's `htpasswd` tool). Without a login they answer `401`. An `--expose`
  builder serves plain HTTP, so the login crosses the network unencrypted
  there: prefer the Settings page.
- In the deploy dialog, each secret the lab asks for offers **Use stored…** with
  the credentials that can fill it. After the password, the page fills that
  secret and every other one the same credential provides (an AWS credential
  fills both `aws_access_key_id` and `aws_secret_access_key`). A generic
  credential fills secrets whose names match its field names.
- The filled values go where typed ones go: into `~/.rodeo/secrets.yaml` for
  `rodeo up`. Deleting a stored credential does not remove them from there.
- The page also lists the keys already in root's `~/.rodeo/secrets.yaml`, names only
  and read-only: `rodeo up` reads that file directly. In the deploy dialog,
  **Use stored…** offers them under *secrets.yaml* (names sharing a word with
  the secret first). Picking one asks no password and locks the input
  ("from secrets.yaml: <key>", **Clear** to type instead); the builder copies the
  value on its host, so it never reaches the browser.

## Publish to GitHub

**Publish** puts the rodeo in a public GitHub repository of yours and opens a pull
request on rodeo-cli that adds it to the rodeo catalog
(`rodeo/data/catalog.d/<name>.yaml`: name, title, repository, ref, engine,
languages). The repository holds the files of the download.

- **`rodeo builder` on loopback** uses the GitHub login already on its host,
  without storing it: `GH_TOKEN` or `GITHUB_TOKEN`, else the GitHub CLI's login
  (`gh auth token`), else git's stored github.com credential. With none, **Log
  in to GitHub** runs `gh auth login --web` and shows its one-time code to enter
  at github.com/login/device; without `gh`, install it, run `gh auth login` or
  set `GH_TOKEN` before `rodeo builder`. Publishing creates
  `github.com/<you>/<name>`, pushes one commit on `main`, forks rodeo-cli when
  you don't own it and opens the catalog pull request. **Update my existing
  repository** pushes a new commit that replaces the files (the repository's
  `.github/` stays) and opens no pull request. `RODEO_CATALOG_REPO` points the
  pull request at another repository.

  Translations go to language branches. `main` holds the source language
  (`story.language: en`). Each translated language gets a branch named after it:
  `main` plus that language's `story/strings/<lang>/`, with `story.language:
  <lang>`. On an update, an existing language branch only gets its
  `story/strings/<lang>/` replaced. `main` also gets
  `.github/workflows/rodeo-languages.yml`: on every push to `main`, it opens a
  pull request `main` → `<lang>` for each language branch that is behind `main`
  (the check of `rodeo story check`), and you resolve any conflicts in it.
  Publish lets the repository's Actions open those pull requests (**Allow GitHub
  Actions to create and approve pull requests**). The result lists the language
  branches and warns about anything left out: the workflow, when the GitHub login
  can't write workflows (it needs the `workflow` scope); that setting, when GitHub
  refuses it; a language branch that couldn't be pushed.
- **The published page** (and `rodeo builder --expose`) links to github.com
  pages that use your browser's GitHub login, and GitHub asks you to log in when
  you aren't: create the repository, upload the unzipped download (or `git
  push`), and add the catalog file, which forks rodeo-cli and opens the pull
  request under your account.

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
