# Contributing to rodeo-cli

Thanks for your interest. rodeo-cli is an open-source project built by SUSE Principal Technology Advocates. Contributions are welcome — bug fixes, documentation, new profiles, and new deployment targets.

## Before you start

For anything bigger than a one-line fix, open an issue first. It saves everyone time if we agree on the approach before you write code.

For security issues, do not open a public issue. See [SECURITY.md](SECURITY.md).

## Ways to contribute

- **Bug reports** — detailed, reproducible steps with `rodeo logs --bundle` output attached
- **Documentation fixes** — typos, unclear steps, missing examples
- **Bug fixes** — link to the issue in the PR
- **New profiles** — SUSE Edge, Telco Cloud, custom topologies (see [ROADMAP.md](ROADMAP.md))
- **New deployment targets** — AWS, GCP (see [ROADMAP.md](ROADMAP.md) Phase E)
- **Test coverage** — especially the Instruqt validation queue items in ROADMAP.md

## Development setup

```bash
git clone https://github.com/avaleror/rodeo-cli.git
cd rodeo-cli
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Check everything passes before touching code:

```bash
ruff check rodeo tests
pytest tests/ -v
```

## Code style

`ruff` is the linter and formatter. CI enforces it. Run it before pushing:

```bash
ruff check rodeo tests
ruff format rodeo tests   # optional; we do not enforce formatting style yet
```

No new comments that describe *what* the code does — names should do that. Comments are for non-obvious *why*: hidden constraints, fragile invariants, workarounds for specific bugs.

## Testing

Unit tests live in `tests/`. They do not require KVM and run on any OS.

For changes that touch the Ansible roles (`rodeo/data/ansible/`), the PXE boot chain, or the phase pipeline, a live KVM regression is required before merge. See [ROADMAP.md — Standing constraints](ROADMAP.md#standing-constraints) for the fragile files list.

CI (`.github/workflows/ci.yml`) runs `ruff check` and `pytest` on Ubuntu for Python 3.10 and 3.12. It does not require KVM. For changes that touch the Ansible roles, PXE boot chain, or phase pipeline, a live KVM regression on SLES 16 is required before merge — see [ROADMAP.md — Standing constraints](ROADMAP.md#standing-constraints).

## Submitting a pull request

1. Fork the repo and create a branch from `main`.
2. Make your changes. Add or update tests for anything you changed.
3. Run `ruff check` and `pytest tests/ -v` — both must pass.
4. Push and open a PR against `main`.
5. Sign off every commit with the [Developer Certificate of Origin](https://developercertificate.org/) (DCO):

```bash
git commit -s -m "fix: correct etcd join gap calculation"
```

The DCO sign-off (`Signed-off-by: Your Name <your@email.com>`) is your statement that you have the right to submit the contribution under the GPL-3.0-or-later license. It is not a CLA.

## Commit message format

Use the [Conventional Commits](https://www.conventionalcommits.org/) style:

```
<type>: <short description>

<optional body>
```

Types: `fix`, `feat`, `perf`, `docs`, `refactor`, `build`, `ci`, `test`, `chore`.

**PR titles matter.** PRs are squash-merged, so the **PR title** becomes the
commit on `main`. It must be a valid Conventional Commit — a CI check enforces
this — because [release-please](RELEASING.md) reads those commits to decide the
next version and build the changelog. `fix:` → patch, `feat:` → minor,
`feat!:` / `BREAKING CHANGE:` → major.

Releases are fully automated from this history — see [RELEASING.md](RELEASING.md).
You never bump a version or write release notes by hand.

## What we review for

- Does it work? (tested on real KVM if it touches deploy logic)
- Does it break existing profiles? (no regressions)
- Is the change minimal? (no scope creep in a PR)
- Does it follow the conventions in [architecture.md](docs/architecture.md)?

## License

By contributing you agree that your contributions are licensed under the GPL-3.0-or-later license. See [LICENSE](LICENSE).
