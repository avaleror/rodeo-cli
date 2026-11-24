# Releasing and validation

Users install rodeo with one command, and that command has to give them a version
that works. A tagged release isn't proof of that: some breakages only show up on
a real deploy. So rodeo has two steps: **release** (automatic) and **validation**
(a live deploy, then a reviewed PR).

| | Who installs it |
|---|---|
| `STABLE` (a file on `main`, one tag such as `v0.32.2`) | `install.sh` by default, `rodeo self-update` by default, and GitHub's "Latest" badge |
| A newer tag that isn't in `STABLE` yet | only `--ref vX.Y.Z` |
| `main` | only `--ref main` / `self-update --branch main` |

A KVM or cloud host that rodeo sets up installs the same release as the laptop
driving it, so validating a release covers both sides.

## 1. Release

release-please keeps a release PR open on `main`. Merging it creates the tag and
the GitHub release. The new release is **not** the default yet. The release
workflow puts the "Latest" badge back on the `STABLE` release, so the releases
page shows what users actually get.

Before merging the release PR, clean the notes: merge commits list every change
twice.

## 2. Validate

From a laptop with AWS credentials (`aws login`):

```bash
scripts/smoke-aws.sh v0.33.0                # rancher-test: about 15 min, under 1 USD
```

It deploys `rancher-test` on a fresh EC2 host running exactly that tag, follows
it with `rodeo status --cloud`, destroys the host whatever happens, and writes
`smoke-v0.33.0-rancher-test.md`. Exit 0 means the deploy finished with exit 0 and
printed "Your lab is up".

Then test what the release changed. If it touched a profile or a workshop, deploy
that one too and walk its checks, for example
`scripts/smoke-aws.sh v0.33.0 virt-workshop`. Every evidence file goes in the
promotion PR.

## 3. Promote

```bash
scripts/promote-stable.sh v0.33.0 smoke-v0.33.0-rancher-test.md [more.md ...]
```

It refuses evidence without a PASS, writes the tag to `STABLE` on a new branch,
and opens `chore(stable): promote v0.33.0` with the evidence as the body. A second
person reviews and merges it. From that merge on, new installs and
`self-update` get the release, and GitHub's "Latest" badge moves to it.

## Rolling back

If a promoted release turns out broken, revert the promotion PR. `STABLE` goes
back to the previous release, new installs get that one, and
`rodeo self-update` takes anyone already on the broken release back to it.

## Why a file

`STABLE` is read from the clone `install.sh` already makes: no GitHub API call,
so no rate limit for a room full of students behind one IP. Promoting is a
reviewed PR, so the history says who promoted what and with which evidence.
release-please is untouched.
