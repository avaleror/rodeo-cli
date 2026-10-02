"""Workshop content from an Instruqt track repository.

A plan's ``workshop:`` block names the track a lab is built for:

    workshop:
      repo: https://github.com/SUSE-Technical-Marketing/instruqt-SMLM
      branch: main
      track: smlms
      ref: 778cbc8f363c8c9136fa77656bfd380e5a974dc7   # optional: full commit SHA or tag
      skip_vms: [zbastion]        # track machines the lab deliberately replaces
      facts:                      # values for the assignments' [[ Instruqt-Var ]]
        SMLM_URL: https://smlm.smlm.lab
      replace:                    # literal text swaps when rendering the guide
        "smlm.${_SANDBOX_ID}.instruqt.io": smlm.rodeo.lab
      pdf: true                   # also build workshop-guide.pdf with the track's own
                                  # tools/build_pdf.py (needs pandoc + Chromium)

rodeo never copies track content into this repo (workshop content lives in its
own repository): the track is fetched into the lab directory at deploy time, the
fetched commit is recorded, and it is checked against the lab topology so a
drift between the two shows up as a deploy warning. The track is optional
content, never a build input: a failed fetch only warns.

render_guide() writes the assignments with the Instruqt markup replaced by the
lab's own values, so the lab can be followed without Instruqt.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .config import ConfigError
from .labinabox_host import fetch_commands

_TRACK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VAR_RE = re.compile(r'\[\[\s*Instruqt-Var\s+key="([A-Za-z0-9_]+)"')


def workshop_spec(cfg: dict) -> dict | None:
    """Validated workshop block, or None when the plan has none."""
    spec = cfg.get("workshop")
    if not spec:
        return None
    repo = str(spec.get("repo", ""))
    track = str(spec.get("track", ""))
    branch = str(spec.get("branch", "main"))
    if not repo.startswith("https://"):
        raise ConfigError("workshop.repo must be an https:// git URL")
    if not _TRACK_RE.match(track):
        raise ConfigError(f"workshop.track '{track}' is not a plain directory name")
    if not _TRACK_RE.match(branch.replace("/", "-")):
        raise ConfigError(f"workshop.branch '{branch}' is not a valid branch name")
    ref = str(spec.get("ref") or "")
    if ref and not _TRACK_RE.match(ref):
        raise ConfigError(f"workshop.ref '{ref}' is not a valid commit or tag")
    return {
        "repo": repo,
        "branch": branch,
        "ref": ref,
        "track": track,
        "skip_vms": [str(v) for v in spec.get("skip_vms") or []],
        "facts": {str(k): str(v) for k, v in (spec.get("facts") or {}).items()},
        "replace": {str(k): str(v) for k, v in (spec.get("replace") or {}).items()},
        "pdf": bool(spec.get("pdf", False)),
    }


def checkout_path(lab_dir: Path) -> Path:
    return lab_dir / "workshop"


def track_fetch_commands(spec: dict, dest: Path) -> list[list[str]]:
    return fetch_commands(spec["repo"], spec["ref"] or spec["branch"], dest)


def lock_text(spec: dict, commit: str) -> str:
    """What was fetched, for <lab>/.labinabox/workshop.lock."""
    return yaml.safe_dump({
        "repo": spec["repo"], "branch": spec["branch"], "ref": spec["ref"] or None,
        "track": spec["track"], "commit": commit,
    }, sort_keys=False)


_VAR_TAG_RE = re.compile(r'\[\[\s*Instruqt-Var\s+key="([A-Za-z0-9_]+)"[^\]]*\]\]')


def render_text(text: str, spec: dict) -> str:
    """One assignment with Instruqt variables and replace-map entries filled in."""
    for old, new in spec["replace"].items():
        text = text.replace(old, new)
    return _VAR_TAG_RE.sub(lambda m: spec["facts"].get(m.group(1), f"<{m.group(1)}>"), text)


def render_guide(track_dir: Path, spec: dict, out_dir: Path) -> list[Path]:
    """Write every challenge's assignment, rendered, to out_dir/<challenge>.md.

    Owner-only: facts can include lab passwords (e.g. UNIVERSAL_PWD).
    """
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    written = []
    for challenge in parse_track(track_dir)["challenges"]:
        dest = out_dir / f"{challenge}.md"
        dest.touch(mode=0o600, exist_ok=True)
        dest.chmod(0o600)
        dest.write_text(render_text((track_dir / challenge / "assignment.md").read_text(errors="replace"), spec))
        written.append(dest)
    return written


def parse_track(track_dir: Path) -> dict:
    """Summarise an Instruqt track directory (tracks/<track>/)."""
    config_file = track_dir / "config.yml"
    track_file = track_dir / "track.yml"
    if not config_file.is_file():
        raise ConfigError(f"{config_file} not found — is workshop.track correct?")
    config = yaml.safe_load(config_file.read_text()) or {}
    track = yaml.safe_load(track_file.read_text()) if track_file.is_file() else {}
    challenges = sorted(
        p.name for p in track_dir.iterdir()
        if p.is_dir() and (p / "assignment.md").is_file()
    )
    variables: set[str] = set()
    for name in challenges:
        variables.update(_VAR_RE.findall((track_dir / name / "assignment.md").read_text(errors="replace")))
    return {
        "title": (track or {}).get("title", track_dir.name),
        "vms": [vm["name"] for vm in config.get("virtualmachines") or [] if vm.get("name")],
        "browsers": [b["name"] for b in config.get("virtualbrowsers") or [] if b.get("name")],
        "secrets": [s["name"] for s in config.get("secrets") or [] if s.get("name")],
        "challenges": challenges,
        "variables": sorted(variables),
    }


def topology_warnings(track: dict, spec: dict, lab_vm_names: list[str]) -> list[str]:
    """Differences between the track's machines/variables and the lab."""
    lab_short = {n.split(".")[0] for n in lab_vm_names}
    warnings = []
    missing = [v for v in track["vms"] if v not in lab_short and v not in spec["skip_vms"]]
    if missing:
        warnings.append(
            "track machine(s) with no lab VM: " + ", ".join(missing)
            + " — add them to the definition or list them in workshop.skip_vms"
        )
    unset = [v for v in track["variables"] if v not in spec["facts"]]
    if unset:
        warnings.append(
            "assignment variable(s) with no workshop.facts value: " + ", ".join(unset)
        )
    return warnings


PDF_TOOL = Path("tools") / "build_pdf.py"


def prepare_pdf_build(track_dir: Path, guide_dir: Path, build_dir: Path) -> Path | None:
    """A copy of the track whose assignments are the rendered guide, for the
    track's own tools/build_pdf.py — so the PDF carries this lab's values
    instead of Instruqt placeholders. Returns the tool's path in the copy,
    or None when the track has no such tool.
    """
    import shutil

    if not (track_dir / PDF_TOOL).is_file():
        return None
    if build_dir.exists():
        shutil.rmtree(build_dir)
    shutil.copytree(track_dir, build_dir, ignore=shutil.ignore_patterns(".build"))
    for rendered in guide_dir.glob("*.md"):
        target = build_dir / rendered.stem / "assignment.md"
        if target.is_file():
            target.write_text(rendered.read_text())
    return build_dir / PDF_TOOL
