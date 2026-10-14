"""rodeo builder — the Rodeo Builder web UI, live, with saving into your profiles.

The page published with the docs (built by scripts/build-builder-static.py) is
the static subset of this one: here the data is read live and "Save to
profiles" writes the rodeo straight into ~/.rodeo/profiles/<name>/, ready for
``rodeo up --profile <name>``.

    rodeo builder                      # http://127.0.0.1:8678/
    rodeo builder --labinabox ~/src/lab-in-a-box --no-fetch-sources

The lab-in-a-box catalogue comes from --labinabox, RODEO_LABINABOX_PATH, or the
latest lab-in-a-box release (fetched like a deploy does, into ~/.rodeo/vendor/).
When none can be read the page says why instead of showing an empty list.
"""
from __future__ import annotations

import ipaddress
import subprocess
import tempfile
import webbrowser
from pathlib import Path

import click
from rich.console import Console

from .. import __version__
from .. import labinabox_host as host
from ..builder.api import Api
from ..builder.discovery import LAB_BUILDER_URL, fetch_sources
from ..builder.server import LOOPBACK_HOSTS, make_server
from ..config import ConfigError

console = Console()


def _is_loopback(name: str) -> bool:
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _labinabox_checkout(given: Path | None) -> tuple[Path | None, str, str]:
    """(checkout, version, why it is missing): --labinabox, RODEO_LABINABOX_PATH, or the
    latest release fetched."""
    if given:
        return given.expanduser().resolve(), "local", ""
    override = host.local_override()
    if override:
        return override, "local", ""
    ref = ""
    try:
        repo, ref = host.source({})
        ref = host.resolve_ref(repo, ref)
        dest = host.checkout_dir(ref)
        dest.parent.mkdir(parents=True, exist_ok=True)
        console.print(f"Fetching lab-in-a-box {ref} from {repo}...")
        for cmd in host.fetch_commands(repo, ref, dest):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise ConfigError(f"lab-in-a-box {ref}: {(r.stderr or '').strip()[-300:]}")
    except (ConfigError, OSError, subprocess.TimeoutExpired) as exc:
        return None, ref, f"could not fetch lab-in-a-box: {exc}"
    return dest, ref, ""


@click.command("builder", short_help="Open the Rodeo Builder web UI and save rodeos into your profiles.")
@click.option("--host", "bind", default="127.0.0.1", show_default=True, help="Address to listen on.")
@click.option("--port", default=8678, show_default=True, type=click.IntRange(0, 65535), help="Port (0 picks a free one).")
@click.option("--expose", is_flag=True,
              help="Allow a non-loopback --host. Anyone who can reach it can save profiles as you.")
@click.option("--allow-host", "allow_hosts", multiple=True, metavar="NAME",
              help="Extra Host header name to accept (with --expose: the name or IP clients use).")
@click.option("--labinabox", "labinabox", type=click.Path(file_okay=False, path_type=Path),
              help="lab-in-a-box checkout for the catalogue (default: RODEO_LABINABOX_PATH, else the latest release).")
@click.option("--lab-builder-url", default=LAB_BUILDER_URL, show_default=True,
              help="lab-in-a-box lab-builder the page links to and embeds.")
@click.option("--source", "sources", multiple=True, metavar="ID=CHECKOUT",
              help="Local checkout for one chapter source in rodeo/builder/chapter_sources.yaml (repeatable).")
@click.option("--no-fetch-sources", is_flag=True, help="List only the chapter sources given with --source.")
@click.option("--open/--no-open", "open_browser", default=True, help="Open the page in a browser.")
def builder_cmd(bind: str, port: int, expose: bool, allow_hosts: tuple[str, ...], labinabox: Path | None,
                lab_builder_url: str, sources: tuple[str, ...], no_fetch_sources: bool, open_browser: bool) -> None:
    """Serve the Rodeo Builder on BIND:PORT until Ctrl-C."""
    if not _is_loopback(bind) and not expose:
        raise click.UsageError(f"--host {bind} is reachable from other machines; add --expose to allow that")
    given: dict[str, Path] = {}
    for item in sources:
        sid, sep, path = item.partition("=")
        if not sep or not path:
            raise click.UsageError(f"--source wants ID=CHECKOUT, got {item!r}")
        given[sid] = Path(path).expanduser().resolve()

    checkout, liab_version, liab_missing = _labinabox_checkout(labinabox)
    allowed = tuple(dict.fromkeys(LOOPBACK_HOSTS + tuple(h.lower() for h in allow_hosts)
                                  + (() if _is_loopback(bind) or bind in ("0.0.0.0", "::") else (bind.lower(),))))
    with tempfile.TemporaryDirectory(prefix="rodeo-builder-sources-") as tmp:
        checkouts = given
        if not no_fetch_sources:
            console.print("Fetching chapter sources...")
            checkouts, errors = fetch_sources(Path(tmp), given)
            for sid, err in errors.items():
                console.print(f"[yellow]⚠  chapter source {sid} left out: {err}[/yellow]")
        api = Api(checkout, lab_builder_url, liab_version, checkouts, can_save=True,
                  labinabox_missing=liab_missing)
        status, liab = api.dispatch("labinabox")
        if status == 200 and liab["error"]:
            console.print(f"[yellow]⚠  lab-in-a-box catalogue: {liab['error']}[/yellow]")
        server = make_server(api, bind, port, __version__, allowed, lab_builder_url)
        shown = "[{}]".format(bind) if ":" in bind else bind
        url = "http://{}:{}/".format("127.0.0.1" if bind in ("0.0.0.0", "::") else shown, server.server_address[1])
        console.print(f"\n[bold green]Rodeo Builder[/bold green] → [cyan]{url}[/cyan]  (Ctrl-C to stop)")
        console.print("[dim]Save to profiles writes ~/.rodeo/profiles/<name>/; deploy with rodeo up --profile <name>.[/dim]")
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
