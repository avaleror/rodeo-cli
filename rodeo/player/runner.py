"""Chapter lifecycle for a rodeo track on a rodeo lab.

Input is a track checkout: ``NN-slug/assignment.md`` with front matter, and
optional ``setup-<host>``, ``check-<host>``, ``solve-<host>`` and
``cleanup-<host>`` scripts beside it (``<host>`` is ``kvm-host`` for
suse-virt-rodeo). The runner uses them like this:

- entering a chapter runs its setup; a non-zero setup is a failed enter and
  Check refuses until a retry passes;
- Check runs the check script (60 s cap, no retry). Exit 0 completes the
  chapter: cleanup runs (a failure is recorded, the move still happens) and
  the next chapter's setup runs. Anything else stays put;
- a chapter with no check offers Next instead, never an invented success;
- Skip to a later chapter runs, for the current chapter and every one before
  the target, solve then cleanup, in order; a failing solve stops there.
  Then the target's setup;
- going back runs nothing.

Scripts run as a subprocess in their chapter directory with a wiped
environment: PATH (the ``agent`` shim first), HOME, KUBECONFIG and the vars
file location. Output is captured with a size cap and returned to the
caller (the check output is the student's feedback). The
history file records exit codes and timings only, never output, because
scripts print passwords.

No sockets, no root assumptions: the server (WP2) and ``rodeo player test``
drive this.
"""
from __future__ import annotations

import html
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import agent as _agent

SCRIPT_KINDS = ("setup", "check", "solve", "cleanup")
DEFAULT_HOST = "kvm-host"
CHECK_TIMEOUT = 60          # one check may run 60 s
SCRIPT_TIMEOUT = 1800       # setup / solve / cleanup
OUTPUT_CAP = 64 * 1024
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

_CHAPTER_DIR = re.compile(r"^\d+-[a-z0-9][a-z0-9-]*$")
_QUOTED_LINE = re.compile(r'^([A-Za-z_][\w-]*): "(.*)"\s*$')
_UNICODE_ESC = re.compile(r"\\U([0-9a-fA-F]{8})|\\u([0-9a-fA-F]{4})")
# The track's variable tag syntax, as the track files write it.
_VAR = re.compile(r'\[\[\s*Instruqt-Var\s+key="([A-Za-z_][A-Za-z0-9_]*)"[^\]]*\]\]')
# Non-greedy label: the real track wraps one label in a <span id="..."> with
# its own quotes, so the label ends at the '" variant=' or '"]' that follows.
_BUTTON = re.compile(r'\[button\s+label="(.*?)"(?:\s+variant="[^"]*")?\]\(tab-(\d+)\)')


class RunnerError(RuntimeError):
    """A move the runner refuses (wrong direction, setup not done, ...)."""


@dataclass(frozen=True)
class Tab:
    title: str
    type: str
    hostname: str = ""
    port: int | None = None
    path: str = "/"
    protocol: str = "https"


@dataclass(frozen=True)
class Chapter:
    index: int
    dirname: str
    slug: str
    title: str
    tabs: tuple[Tab, ...]
    body: str
    path: Path
    scripts: dict[str, Path] = field(default_factory=dict)

    def has(self, kind: str) -> bool:
        return kind in self.scripts


@dataclass
class ScriptResult:
    chapter: int
    kind: str
    exit_code: int
    output: str
    duration: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class MoveResult:
    """What a Check, Next, Skip or enter did, script by script."""

    ok: bool
    current: int
    results: list[ScriptResult] = field(default_factory=list)
    message: str = ""


# --------------------------------------------------------------------------
# Track loading
# --------------------------------------------------------------------------

def _repair_front_matter(text: str) -> str:
    """Make suse-virt-rodeo's front matter loadable as YAML.

    rmstory writes ``title: "<span id="assignment.7" ...>...</span>"``: a
    double-quoted scalar with bare double quotes inside, which strict YAML
    rejects. Re-quote those single lines with single quotes,
    decoding the ``\\UXXXXXXXX`` escapes double quotes would have decoded.
    """
    out = []
    for line in text.splitlines():
        m = _QUOTED_LINE.match(line)
        if m and '"' in m.group(2):
            inner = _UNICODE_ESC.sub(lambda e: chr(int(e.group(1) or e.group(2), 16)), m.group(2))
            line = f"{m.group(1)}: '" + inner.replace("'", "''") + "'"
        out.append(line)
    return "\n".join(out)


def parse_assignment(text: str) -> tuple[dict, str]:
    """Split ``assignment.md`` into (front matter, body)."""
    if not text.startswith("---"):
        return {}, text
    parts = re.split(r"^---\s*$", text, maxsplit=2, flags=re.M)
    if len(parts) < 3:
        raise ValueError("unterminated front matter")
    raw = parts[1]
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError:
        meta = yaml.safe_load(_repair_front_matter(raw))
    return (meta or {}), parts[2].lstrip("\n")


def _tabs(meta: dict) -> tuple[Tab, ...]:
    tabs = []
    for t in meta.get("tabs") or []:
        port = t.get("port")
        tabs.append(Tab(
            title=str(t.get("title", "")),
            type=str(t.get("type", "")),
            hostname=str(t.get("hostname", "")),
            port=int(port) if port is not None else None,
            path=str(t.get("path") or "/"),
            protocol=str(t.get("protocol") or "https"),
        ))
    return tuple(tabs)


def load_track(track_dir: Path | str, host: str = DEFAULT_HOST) -> list[Chapter]:
    """Chapters in directory-name order, with the scripts present for ``host``."""
    root = Path(track_dir)
    chapters: list[Chapter] = []
    dirs = sorted(d for d in root.iterdir() if d.is_dir() and _CHAPTER_DIR.match(d.name)
                  and (d / "assignment.md").is_file())
    for i, d in enumerate(dirs):
        meta, body = parse_assignment((d / "assignment.md").read_text())
        scripts = {k: d / f"{k}-{host}" for k in SCRIPT_KINDS if (d / f"{k}-{host}").is_file()}
        chapters.append(Chapter(
            index=i,
            dirname=d.name,
            slug=str(meta.get("slug") or d.name.split("-", 1)[1]),
            title=str(meta.get("title") or d.name),
            tabs=_tabs(meta),
            body=body,
            path=d,
            scripts=scripts,
        ))
    if not chapters:
        raise ValueError(f"no NN-slug/assignment.md chapters under {root}")
    return chapters


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

_SPAN_OPEN = re.compile(r"<span\b([^>]*)>")
_SPAN_CLOSE = re.compile(r"</span>")
_RMSTORY_ATTR = re.compile(r'\blang\s*=|\bhist\s*=|\bno\b')


def strip_rmstory_spans(text: str) -> str:
    """Remove rmstory tagging spans (lang=, hist=, no) and keep their content.

    The track's CI does exactly this before every publish
    (suse-virt-rodeo/.github/scripts/strip-rmstory-spans.py, ported here):
    Markdown wrapped in one of these spans is otherwise an HTML block that no
    Markdown renderer touches, so headings and **bold** showed raw. Stack
    based, so nested spans work and plain spans (no rmstory attribute) stay.
    """
    out: list[str] = []
    stack: list[bool] = []
    pos = 0
    while pos < len(text):
        m = _SPAN_OPEN.match(text, pos)
        if m:
            strip = bool(_RMSTORY_ATTR.search(m.group(1)))
            stack.append(strip)
            if not strip:
                out.append(m.group(0))
            pos = m.end()
            continue
        m = _SPAN_CLOSE.match(text, pos)
        if m:
            if not (stack.pop() if stack else False):
                out.append(m.group(0))
            pos = m.end()
            continue
        nxt = text.find("<", pos + 1)
        nxt = len(text) if nxt < 0 else nxt
        out.append(text[pos:nxt])
        pos = nxt
    return "".join(out)


# An Instruqt sandbox address, written literally or with the _SANDBOX_ID
# variable: https://<hostname>.<sandbox>.instruqt.io[:port][/path]
_INSTRUQT_URL = re.compile(
    r'https?://[A-Za-z0-9-]+\.(?:\[\[\s*Instruqt-Var\s+key="_SANDBOX_ID"[^\]]*\]\]|[A-Za-z0-9-]+)'
    r'\.instruqt\.io(?::(\d+))?(/[^\s"\'<>)\]]*)?')


def rewrite_instruqt_urls(body: str, urls: dict[str, str], home: str = "/") -> str:
    """Point Instruqt sandbox addresses at this lab: the tab URL for that port
    (8443 -> that UI's name), else the player itself. A lab run by rodeo
    has no instruqt.io name, so a link left as it was would lead nowhere."""
    def sub(m: re.Match) -> str:
        base = urls.get(m.group(1) or "443") or home
        return base.rstrip("/") + m.group(2) if m.group(2) else base
    return _INSTRUQT_URL.sub(sub, body)


def render_body(body: str, values: dict[str, str], urls: dict[str, str] | None = None) -> str:
    """Substitute the track's variables and tab buttons; leave the rest as is.

    Values are HTML-escaped (they come from scripts). A variable nobody set
    shows as a visible marker, not as an empty string the student would
    paste into a login box. Inside an HTML tag (an ``href``) a variable is
    plain text: a chip there would break the tag. With *urls* (tab port ->
    URL), Instruqt sandbox addresses point at this lab instead.
    """
    if urls is not None:
        body = rewrite_instruqt_urls(body, urls)

    def var(m: re.Match) -> str:
        key = m.group(1)
        before = m.string[:m.start()]
        if before.rfind("<") > before.rfind(">"):  # inside a tag
            return html.escape(values.get(key, ""), quote=True)
        if key in values:
            # A click-to-copy chip: students retype these into Harvester and Rancher.
            return f'<code class="rodeo-var" title="Click to copy">{html.escape(values[key])}</code>'
        return f'<span class="rodeo-var-missing">{html.escape(key)} not set</span>'

    def button(m: re.Match) -> str:
        # The label is track markup (it may hold a <span>), not script output.
        return f'<button type="button" class="rodeo-tab-button" data-tab="{int(m.group(2))}">{m.group(1)}</button>'

    return _BUTTON.sub(button, _VAR.sub(var, body))


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def _cap(data: bytes, cap: int) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(data) <= cap:
        return text
    tail = data[-cap:].decode("utf-8", errors="replace")
    return f"[... {len(data) - cap} bytes truncated ...]\n{tail}"


class Runner:
    """One lab's progress through one track. State lives in ``state_dir``."""

    def __init__(
        self,
        track_dir: Path | str,
        state_dir: Path | str,
        *,
        host: str = DEFAULT_HOST,
        sandbox_id: str = "",
        home: str = "/root",
        kubeconfig: str = "/root/.rodeo/harvester-kubeconfig",
        check_timeout: float = CHECK_TIMEOUT,
        script_timeout: float = SCRIPT_TIMEOUT,
        output_cap: int = OUTPUT_CAP,
        system_path: str = SYSTEM_PATH,
    ) -> None:
        self.chapters = load_track(track_dir, host)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.vars_file = self.state_dir / "vars.json"
        self.sandbox_id = sandbox_id
        self.home = home
        self.kubeconfig = kubeconfig
        self.check_timeout = check_timeout
        self.script_timeout = script_timeout
        self.output_cap = output_cap
        self.system_path = system_path
        self._bin = self._install_agent()
        self._state = self._load_state()

    # -- state --------------------------------------------------------------

    def _load_state(self) -> dict:
        p = self.state_dir / "state.json"
        if p.is_file():
            data = json.loads(p.read_text())
            if data.get("chapters") == [c.dirname for c in self.chapters]:
                return data
        # New lab, or the track changed under us: start at chapter 1, not entered.
        return {"chapters": [c.dirname for c in self.chapters], "current": 0,
                "entered": None, "completed": []}

    def _save_state(self) -> None:
        p = self.state_dir / "state.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=1))
        os.replace(tmp, p)

    @property
    def current(self) -> int:
        return int(self._state["current"])

    @property
    def entered(self) -> bool:
        """True once the current chapter's setup passed (or it has none)."""
        return self._state["entered"] == self.current

    @property
    def completed(self) -> list[int]:
        return list(self._state["completed"])

    def chapter(self, index: int | None = None) -> Chapter:
        return self.chapters[self.current if index is None else index]

    # -- variables ----------------------------------------------------------

    def variables(self) -> dict[str, str]:
        values = _agent.read_vars(self.vars_file)
        if self.sandbox_id:
            values.setdefault("_SANDBOX_ID", self.sandbox_id)
        return values

    def set_variable(self, key: str, value: str) -> None:
        """Seed a value the track-level setup would otherwise have set."""
        _agent.set_var(self.vars_file, key, value)

    def render(self, index: int | None = None, urls: dict[str, str] | None = None) -> str:
        return render_body(strip_rmstory_spans(self.chapter(index).body), self.variables(), urls)

    # -- subprocesses -------------------------------------------------------

    def _install_agent(self) -> Path:
        bin_dir = self.state_dir / "bin"
        bin_dir.mkdir(exist_ok=True)
        shim = bin_dir / "agent"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{Path(_agent.__file__).resolve()}" "$@"\n')
        shim.chmod(0o755)
        return bin_dir

    def script_env(self, *, with_agent: bool = True) -> dict[str, str]:
        path = f"{self._bin}:{self.system_path}" if with_agent else self.system_path
        env = {"PATH": path, "HOME": self.home, "LANG": "C.UTF-8"}
        if self.kubeconfig:
            env["KUBECONFIG"] = self.kubeconfig
        if with_agent:
            env[_agent.ENV_VARS_FILE] = str(self.vars_file)
        return env

    def run_command(self, argv: list[str], *, cwd: Path, timeout: float,
                    with_agent: bool = True) -> tuple[int, str, bool, float]:
        """Run ``argv`` in its own process group; kill the group on timeout."""
        start = time.monotonic()
        proc = subprocess.Popen(
            argv, cwd=cwd, env=self.script_env(with_agent=with_agent),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        timed_out = False
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, _ = proc.communicate()
        code = proc.returncode if not timed_out else -signal.SIGKILL
        return code, _cap(out or b"", self.output_cap), timed_out, time.monotonic() - start

    def run_script(self, index: int, kind: str) -> ScriptResult | None:
        """Run one chapter script, or return None when the chapter has none."""
        ch = self.chapters[index]
        script = ch.scripts.get(kind)
        if script is None:
            return None
        timeout = self.check_timeout if kind == "check" else self.script_timeout
        argv = [str(script)] if os.access(script, os.X_OK) else ["/bin/bash", str(script)]
        code, out, timed_out, took = self.run_command(argv, cwd=ch.path, timeout=timeout)
        if timed_out:
            out += f"\n[rodeo player] {kind} timed out after {timeout:g}s and was killed\n"
        result = ScriptResult(index, kind, code, out, took, timed_out)
        self._record(result)
        return result

    def _record(self, r: ScriptResult) -> None:
        line = {"ts": time.time(), "chapter": self.chapters[r.chapter].dirname, "kind": r.kind,
                "exit": r.exit_code, "timed_out": r.timed_out, "seconds": round(r.duration, 2)}
        with open(self.state_dir / "history.jsonl", "a") as fh:
            fh.write(json.dumps(line) + "\n")

    # -- moves --------------------------------------------------------------

    def enter(self) -> MoveResult:
        """Run the current chapter's setup (first visit, or a retry)."""
        r = self.run_script(self.current, "setup")
        ok = r is None or r.ok
        if ok:
            self._state["entered"] = self.current
        self._save_state()
        return MoveResult(ok, self.current, [r] if r else [],
                          "" if ok else "setup failed; retry before checking")

    def _require_entered(self) -> None:
        if not self.entered:
            raise RunnerError("the chapter setup has not passed yet; run enter() first")

    def _advance(self, results: list[ScriptResult]) -> MoveResult:
        """Complete the current chapter: cleanup, then the next one's setup."""
        here = self.current
        cleanup = self.run_script(here, "cleanup")
        if cleanup:
            results.append(cleanup)   # a failure is recorded, the move happens
        if here not in self._state["completed"]:
            self._state["completed"].append(here)
        if here + 1 >= len(self.chapters):
            self._state["entered"] = here
            self._save_state()
            return MoveResult(True, here, results, "track complete")
        self._state["current"] = here + 1
        self._state["entered"] = None
        entered = self.enter()
        results.extend(entered.results)
        return MoveResult(entered.ok, self.current, results, entered.message)

    def check(self) -> MoveResult:
        """Check button. Advances only on exit 0."""
        self._require_entered()
        if not self.chapter().has("check"):
            raise RunnerError("this chapter has no check; use next()")
        r = self.run_script(self.current, "check")
        if not r.ok:
            self._save_state()
            return MoveResult(False, self.current, [r], "check failed")
        return self._advance([r])

    def next(self) -> MoveResult:
        """Next button, offered only on a chapter with no check."""
        self._require_entered()
        if self.chapter().has("check"):
            raise RunnerError("this chapter has a check; use check()")
        return self._advance([])

    def skip(self, target: int) -> MoveResult:
        """Skip forward to ``target``, as if the student pressed Skip on each chapter.

        Every skipped chapter that was not entered yet runs its setup first,
        then solve, then cleanup. Setup is not optional: chapter 4's setup is
        what sets PAYMENT_GATEWAY_IP and creates daily-batch-processor, and a
        later chapter must find the lab in the state the story built.
        A failing setup or solve stops the skip on that chapter.
        """
        if not self.current < target < len(self.chapters):
            raise RunnerError(f"skip goes forward only: {self.current} -> {target}")
        results: list[ScriptResult] = []
        for i in range(self.current, target):
            already_entered = i == self.current and self.entered
            self._state["current"] = i
            if not already_entered:
                setup = self.run_script(i, "setup")
                if setup:
                    results.append(setup)
                    if not setup.ok:
                        self._state["entered"] = None
                        self._save_state()
                        return MoveResult(False, i, results, f"setup failed in {self.chapters[i].dirname}")
                self._state["entered"] = i
            solve = self.run_script(i, "solve")
            if solve:
                results.append(solve)
                if not solve.ok:
                    self._state["entered"] = None
                    self._save_state()
                    return MoveResult(False, i, results, f"solve failed in {self.chapters[i].dirname}")
            cleanup = self.run_script(i, "cleanup")
            if cleanup:
                results.append(cleanup)
            if i not in self._state["completed"]:
                self._state["completed"].append(i)
        self._state["current"] = target
        self._state["entered"] = None
        entered = self.enter()
        results.extend(entered.results)
        return MoveResult(entered.ok, self.current, results, entered.message)

    def back(self, target: int) -> MoveResult:
        """Go back to re-read a chapter. Runs nothing, re-solves nothing."""
        if not 0 <= target < self.current:
            raise RunnerError(f"back goes backward only: {self.current} -> {target}")
        self._state["current"] = target
        self._state["entered"] = target
        self._save_state()
        return MoveResult(True, target)


# --------------------------------------------------------------------------
# Track test (what `rodeo player test` runs on a live lab)
# --------------------------------------------------------------------------

@dataclass
class ChapterTest:
    chapter: str
    setup: ScriptResult | None = None
    first_check: ScriptResult | None = None
    solve: ScriptResult | None = None
    final_check: ScriptResult | None = None
    cleanup: ScriptResult | None = None
    # What the check must do before the solve: "pass", "fail", or "any".
    expect_first: str = "any"

    @property
    def first_check_as_expected(self) -> bool:
        if self.first_check is None or self.expect_first == "any":
            return True
        return self.first_check.ok == (self.expect_first == "pass")

    @property
    def ok(self) -> bool:
        steps = (self.setup, self.solve, self.final_check)
        return all(s is None or s.ok for s in steps) and self.first_check_as_expected


def load_expectations(path: Path | str) -> dict[str, str]:
    """``{chapter dirname: pass|fail|any}`` for the check before the solve."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    first = data.get("first_check") or {}
    out = {}
    for chapter, want in first.items():
        if want not in ("pass", "fail", "any"):
            raise ValueError(f"{chapter}: first_check must be pass, fail or any, not {want!r}")
        out[str(chapter)] = str(want)
    return out


def track_test(runner: Runner, *, stop_on_failure: bool = True,
               expect: dict[str, str] | None = None) -> list[ChapterTest]:
    """Track test: per chapter, setup, check, solve, check, cleanup.

    ``expect`` locks the check before the solve per chapter: a chapter whose
    check passes before the work is done (when it should fail) does not test
    the student, and one that fails on a healthy lab (when it should pass)
    blocks them. Chapters not listed are not judged.
    """
    expect = expect or {}
    report: list[ChapterTest] = []
    for ch in runner.chapters:
        t = ChapterTest(ch.dirname, expect_first=expect.get(ch.dirname, "any"))
        t.setup = runner.run_script(ch.index, "setup")
        if ch.has("check") and ch.has("solve"):
            t.first_check = runner.run_script(ch.index, "check")
            t.solve = runner.run_script(ch.index, "solve")
            t.final_check = runner.run_script(ch.index, "check")
        t.cleanup = runner.run_script(ch.index, "cleanup")
        report.append(t)
        if stop_on_failure and not t.ok:
            break
    return report
