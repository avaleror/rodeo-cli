# Releasing rodeo-cli

Releases are **automated**. You never edit a version number, write a changelog,
or create a tag by hand. [release-please](https://github.com/googleapis/release-please)
does all of it from your commit history.

## How it works

1. **You merge PRs to `main`.** Each PR is squash-merged, so its **title** becomes
   the commit on `main`. Titles must be [Conventional Commits](https://www.conventionalcommits.org/)
   — the `PR title` workflow enforces this.
2. **release-please watches `main`.** It reads those commits and keeps a single
   open **release PR** (titled `chore(main): release X.Y.Z`) that:
   - bumps the version in `pyproject.toml` and the doc badges
     (`CONTEXT.md`, `docs/architecture.md` — the lines marked
     `<!-- x-release-please-version -->`),
   - updates `CHANGELOG.md`.
3. **You merge the release PR when you want to ship.** Merging it creates the
   git tag `vX.Y.Z` and the GitHub Release with generated notes — automatically.

That's the whole process. No `bump-version.sh`, no manual tagging.

## How the version number is chosen

release-please derives the bump from the commit types since the last release:

| Commit type | Example | Bump |
|-------------|---------|------|
| `fix:` | `fix(vms): guard ISO download` | patch (0.10.5 → 0.10.6) |
| `feat:` | `feat: add telco-cloud profile` | minor (0.10.5 → 0.11.0) |
| `feat!:` / `BREAKING CHANGE:` | breaking API/behavior change | major (0.10.5 → 1.0.0) |
| `docs:` `refactor:` `perf:` `build:` `ci:` `test:` `chore:` | housekeeping | no release on their own |

A release PR appears as soon as at least one `fix:`/`feat:`/breaking change has
landed since the last release.

## Version is a single source of truth

`pyproject.toml` `version` is authoritative (read at runtime via
`importlib.metadata`). The README shows a **dynamic** shields.io badge that
always reflects the latest GitHub release, so it can never drift. The
`CONTEXT.md` / `docs/architecture.md` badges are kept in lockstep by
release-please. Nothing about the version is maintained by hand.

## Cutting a release manually (rare)

If you ever need to force a specific version, edit `.release-please-manifest.json`
and add a commit like `chore: release 0.11.0` — but the normal path is just to
merge the release PR.

## Notes

- The release PR is opened by the `GITHUB_TOKEN`, so CI does not re-run on it
  (GitHub prevents workflow-triggered workflows). The PR only bumps version +
  changelog, which CI already validated on the source PRs.
- Installing a specific release: `install.sh --ref vX.Y.Z` (checks out the tag).
- Possible future upgrade: [setuptools-scm](https://setuptools-scm.readthedocs.io/)
  to derive dev-build versions (`0.10.6.dev3+g<sha>`) straight from git. Not
  needed today — release-please keeps `pyproject` authoritative.
