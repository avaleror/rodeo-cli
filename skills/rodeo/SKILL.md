---
name: rodeo
description: Deploy, operate and tear down rodeo-cli labs (Rancher, Harvester, SUSE Edge on nested KVM, on a Linux host or on AWS), and author the workshop that goes with one — compose a rodeo and its story chapters, translate them, retell the exercises as a different story, publish and install it. Use when the user mentions rodeo, rodeo-cli, a rodeo lab or profile, the rodeo builder, a rodeo workshop, its chapters, story or translations, or wants a Rancher or Harvester lab or workshop built for them.
---

# rodeo-cli

rodeo-cli builds hands-on labs as nested KVM VMs from a profile. It runs in one of two modes:

- **KVM host**: rodeo runs on the Linux machine that hosts the VMs (SLES 16 / Leap 16).
- **Control machine**: rodeo runs on a laptop (macOS or any Linux) and deploys to an EC2 host with `--target aws`.

Ask which mode applies before the first deploy if it is not obvious. `rodeo doctor` on the machine answers it.

The docs are the source of truth. Read the matching one before acting: `README.md`, `docs/guide-rancher.md`, `docs/guide-harvester.md`, `docs/guide-suse-edge.md`, `docs/examples/aws-macos.md`, `docs/runbook.md`, `docs/reference/plan.md`. Online: https://avaleror.github.io/rodeo-cli/

When this skill's shell access is not available, the same operations are reachable as MCP tools: `rodeo mcp` (read-only) or `rodeo mcp --allow-write` (lifecycle too). See `docs/mcp.md`.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

Update an existing install with `rodeo self-update`.

## Deploy

Pick the profile with `rodeo profiles` (lists bundled and custom ones) and `rodeo doctor` (recommends the largest that fits the host RAM). Then:

```bash
rodeo up --profile <profile> --yes                # on a KVM host
rodeo up --profile <profile> --target aws --yes   # from a laptop
```

For AWS, the user needs a valid session first: `aws login`, then `aws sts get-caller-identity` to confirm the account. `aws login` opens a browser, so the user runs it, not the agent.

A deploy takes 20 minutes or more. `rodeo up` wraps itself in tmux (`rodeo-<profile>`), so it survives a dropped terminal. If your shell tool has a timeout or kills processes when a call ends, do not run `rodeo up` yourself: give the user the exact command to run in their terminal, then follow progress with `rodeo status`. Never pass `--no-tmux` for a long deploy.

A failed deploy resumes from the failed phase: re-run the same `rodeo up` command, or `rodeo deploy --from <phase>`. Phases: `kvm_host`, `vms`, `boot` or `pxe_server` and `cluster`, `rancher`, `elemental`, `apply`, `finalise`.

## Operate

Run lab commands from the lab directory (`~/rodeo-labs/<profile>` by default) or pass `--config-dir <lab dir>`.

| Need | Command |
|---|---|
| URLs, credentials, VM states, phase progress | `rodeo status` |
| Preview what a deploy would change | `rodeo plan` |
| Shell on a lab VM | `rodeo ssh <vm>` |
| VM serial log, support bundle | `rodeo logs <vm>`, `rodeo logs --bundle` |
| Stop or start the lab | `rodeo stop`, `rodeo start` |
| Restart one VM | `rodeo restart <vm>` |
| Rotate admin passwords | `rodeo set-password` |

Rancher UI: `https://<host-ip>:30002`, user `admin`, self-signed certificate. The password is `rancher_admin_password` in `~/.rodeo/secrets.yaml`. Do not print passwords in the conversation unless the user asks for them.

## AWS rules

- Every cloud host terminates itself `provider.ttl_hours` after launch (default 6, max 168). Tell the user the expiry when a lab comes up. To change it, set `provider.ttl_hours` in `rodeo-plan.yaml` before `rodeo up`.
- The security group admits only the public IP of the last `rodeo up`. If the UI or SSH times out after a network change, re-run the same `rodeo up` command: it moves the rule and skips finished phases.
- In a shared AWS account, the key pair `rodeo` may belong to someone else. Never delete a key pair, instance or security group the user did not create. Use a personal key pair name instead, as described in `docs/examples/aws-macos.md` ("Key pair already exists in a shared account").
- Capacity or region errors: see `docs/runbook.md` ("AWS: login, region and capacity errors"). rodeo never downsizes the instance on its own.

## Workshops

A rodeo is a lab plus the workshop students read. The lab is the plan and definition; the workshop is **chapters** (rmstory-tagged markdown), the **stories** they tell, their **translations** and their **checks**. The two are kept apart and ship apart. `docs/rodeo-builder.md` is the source of truth for everything below; read it before composing or changing a workshop.

### Create

`rodeo builder` opens the composer on `http://127.0.0.1:8678/` (installed on Linux as `rodeo-builder.service`; the published, read-only page is at https://avaleror.github.io/rodeo-cli/builder/). In it: pick the lab engine and base profile, drag chapters in from the library (bundled examples, chapter sources, every published rodeo in the catalog), **Import** a workshop from a git URL (a rodeo `story/`, an Instruqt track, or plain markdown), **Start from** a whole workshop, or **+ New** a chapter. With `rmstory` installed (`sudo rodeo install-deps --story`), **Draft with an engine** writes a new chapter from a description, told what the lab provides and which chapters come before it. Review (step 03) checks that the lab covers every chapter and that span ids are valid.

Then install what it produced:

```bash
rodeo new <name> --from-zip <name>.zip              # or Save to profiles on the live builder
rodeo new <name> --from https://github.com/<owner>/<repo>[@<ref>]
rodeo up --profile <name>
```

Hand the user the commands; do not deploy a workshop lab on your own initiative.

### Tag the text

Span tags decide what happens to each piece of text, so get them right before translating or retelling:

- **translatable** — translated, not retold. Plain exercise text.
- **story** (a span with a story id) — the narrative. Translated *and* retold.
- **no / no-lang** — kept word for word. Put every Jinja fact inside one: `<span no>{{ rancher_url }}</span>`, so translation never touches it.

Facts available to a chapter come from `story_facts()` in `rodeo/story.py` — that dict is the contract story authors write against; keep it stable.

### Translate

Translations live in rmstory's store, `story/strings/<lang>/`, and a published rodeo keeps each language on its own git branch (`main` is the source language, `en`). Write or review them in the story editor's **translations** tab; **Translate missing** fills the gaps with an rmstory engine and stores them as `draft`, leaving reviewed text alone.

```bash
rodeo story render                                  # source language, all spans
rodeo story render --language es -o es.md           # Spanish hand-out
rodeo story render --language fr --engine gemini    # translate what is still missing
rodeo story check                                   # which language branches are behind main
```

`rodeo story check` exits 1 when a branch is behind, so it belongs in CI (with full history, `fetch-depth: 0`). Engine keys come from the environment `rodeo builder` runs in or from a stored credential; never put a key in a plan or a committed file, and never paste one into the conversation.

### Change the story

The same exercises, a different story: in the builder's rodeo tab, **New story** asks what the new story is about and runs `rmstory generate rewrite --theme` with an engine that can generate text. Only story spans reach the engine — exercise spans are never sent, and fixed text is kept word for word. Retold chapters get ids `<chapter>-<theme>` and story span ids `<theme>.<id>`; exercise and fixed spans keep their ids and their translations. The retold set replaces the rodeo's chapters, with **Undo**, and lives only in the browser draft until you download, save to profiles or publish.

One rodeo can carry several variants: `story/stories/<id>.yaml` per story, picked with `rodeo story render --story-id <id>` or the plan's `story.id`.

### Deploy the instructions

Instructions can come from the rodeo's own chapters or from their own repository (the plan's `content:` block; `workshop:` points at an Instruqt track instead). `rodeo up` renders them after the lab is up, installs the repository's `checks/` and runs its `custom/scripts/`. To update them on a running lab, without redeploying:

```bash
rodeo content apply
```

### Publish

**Publish** in the builder pushes the rodeo to a public GitHub repository of the user's and opens a catalog pull request on rodeo-cli; it uses the GitHub login already on the host. It is an outward-facing, public action: run it only when the user asks for it in the conversation, and tell them it creates a public repository and a PR under their account.

## Destructive commands

Run these only when the user asked for that action in the current conversation, and confirm the lab directory first:

- `rodeo destroy --cloud --yes --config-dir <lab dir>`: terminates the EC2 host.
- `rodeo clean`: deletes lab VMs, disks and state. `rodeo clean --all --yes --secrets` resets the whole host.

## When something fails

Check `docs/runbook.md` for the symptom first. If it is not there, collect `rodeo logs --bundle` and report it at https://github.com/avaleror/rodeo-cli/issues with the steps to reproduce.
