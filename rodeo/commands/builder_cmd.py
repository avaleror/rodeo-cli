"""rodeo builder — the Rodeo Builder web UI, live, with saving into your profiles.

The page published with the docs (built by scripts/build-builder-static.py) is
the static subset of this one: here the data is read live and "Save to
profiles" writes the rodeo straight into ~/.rodeo/profiles/<name>/, ready for
``rodeo up --profile <name>``.

    rodeo builder                      # http://127.0.0.1:8678/
    rodeo builder --labinabox ~/src/lab-in-a-box --no-fetch-sources
    rodeo builder --host 0.0.0.0 --expose   # https://<this host's FQDN>:8678/

The lab-in-a-box catalogue comes from --labinabox, RODEO_LABINABOX_PATH, or the
latest lab-in-a-box release (fetched like a deploy does, into ~/.rodeo/vendor/).
When none can be read the page says why instead of showing an empty list.

On loopback the builder speaks plain HTTP. On any other address it speaks HTTPS:
with --tls-cert and --tls-key, or with a certificate signed by a CA of this
operator (rodeo/builder/tls.py, ~/.rodeo/builder/tls/), whose certificate clients
import once (served at /ca.pem).
"""
from __future__ import annotations

import fcntl
import ipaddress
import socket
import subprocess
import tempfile
import webbrowser
from pathlib import Path

import click
from rich.console import Console

from .. import __version__
from .. import labinabox_host as host
from ..builder.api import Api
from ..builder import labbuilder, tls
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


WILDCARD_BINDS = ("0.0.0.0", "::")


def _own_names() -> tuple[str, ...]:
    """This machine's host names and the addresses on its interfaces, read now (they may
    change between starts): the Host names clients use for a server bound to every
    interface. Link-local IPv6 is left out (a browser can't put its zone in Host)."""
    names: list[str] = []
    try:
        full = socket.getfqdn()
        names += [socket.gethostname(), full, full.split(".", 1)[0]]
    except OSError:
        pass
    addrs: list[str] = []
    try:
        out = subprocess.run(["ip", "-o", "addr", "show"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            fields = line.split()
            if len(fields) > 3 and fields[2] in ("inet", "inet6"):
                addrs.append(fields[3].split("/", 1)[0])
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not addrs:
        try:
            addrs = [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None)]
        except OSError:
            pass
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr.split("%", 1)[0])
        except ValueError:
            continue
        if not ip.is_link_local:
            names.append(str(ip))
    return tuple(dict.fromkeys(n.lower() for n in names if n))


def _labinabox_checkout(given: Path | None) -> tuple[Path | None, str, str]:
    """(checkout, version, why it is missing): --labinabox, RODEO_LABINABOX_PATH, or the
    latest release fetched. The fetch holds ``<checkout>.lock``, so builders started
    together fetch one after the other instead of failing on git's own locks."""
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
        with open(dest.parent / f"{dest.name}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
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
              help="Allow a non-loopback --host, served over HTTPS. Anyone who can reach it can save profiles "
                   "as you; Publish then uses github.com pages instead of this host's GitHub login; "
                   "deploying and stored credentials need a login (rodeo logins add <user>).")
@click.option("--allow-host", "allow_hosts", multiple=True, metavar="NAME",
              help="Extra Host header name to accept (with --expose: the name or IP clients use); "
                   "also put in the generated certificate.")
@click.option("--tls-cert", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="PEM certificate (with its chain) to serve HTTPS with, instead of one signed by rodeo's own CA. "
                   "Needs --tls-key.")
@click.option("--tls-key", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="PEM private key of --tls-cert.")
@click.option("--labinabox", "labinabox", type=click.Path(file_okay=False, path_type=Path),
              help="lab-in-a-box checkout for the catalogue (default: RODEO_LABINABOX_PATH, else the latest release).")
@click.option("--lab-builder-url", default=None, envvar="RODEO_LAB_BUILDER_URL", show_envvar=True,
              help="lab-in-a-box lab-builder the page links to and embeds, for example one you run on an "
                   "automation node. Default: the published one (" + LAB_BUILDER_URL + "), or the "
                   "checkout's own with --start-lab-builder.")
@click.option("--start-lab-builder", is_flag=True,
              help="Start the lab-in-a-box checkout's own (dynamic) lab-builder on --lab-builder-port. Loopback "
                   "--host only: it has no login or token, so any site you browse can save labs through it.")
@click.option("--lab-builder-port", default=labbuilder.DEFAULT_PORT, show_default=True, type=click.IntRange(1, 65535),
              help="Port for the lab-builder --start-lab-builder starts (same address as --host).")
@click.option("--source", "sources", multiple=True, metavar="ID=CHECKOUT",
              help="Local checkout for one chapter source in rodeo/builder/chapter_sources.yaml (repeatable).")
@click.option("--no-fetch-sources", is_flag=True, help="List only the chapter sources given with --source.")
@click.option("--open/--no-open", "open_browser", default=True, help="Open the page in a browser.")
def builder_cmd(bind: str, port: int, expose: bool, allow_hosts: tuple[str, ...], tls_cert: Path | None,
                tls_key: Path | None, labinabox: Path | None, lab_builder_url: str | None,
                start_lab_builder: bool, lab_builder_port: int, sources: tuple[str, ...],
                no_fetch_sources: bool, open_browser: bool) -> None:
    """Serve the Rodeo Builder on BIND:PORT until Ctrl-C."""
    if not _is_loopback(bind) and not expose:
        raise click.UsageError(f"--host {bind} is reachable from other machines; add --expose to allow that")
    if start_lab_builder and not _is_loopback(bind):
        raise click.UsageError("--start-lab-builder needs a loopback --host: the lab-builder has no login")
    if bool(tls_cert) != bool(tls_key):
        raise click.UsageError("--tls-cert and --tls-key go together")
    given: dict[str, Path] = {}
    for item in sources:
        sid, sep, path = item.partition("=")
        if not sep or not path:
            raise click.UsageError(f"--source wants ID=CHECKOUT, got {item!r}")
        given[sid] = Path(path).expanduser().resolve()

    checkout, liab_version, liab_missing = _labinabox_checkout(labinabox)
    if _is_loopback(bind):
        own: tuple[str, ...] = ()
    elif bind in WILDCARD_BINDS:
        own = _own_names()
    else:
        own = (bind.lower(),)
    ssl_context, ca_pem, served_as = None, None, ""
    if tls_cert and tls_key:
        ssl_context = tls.ssl_context(tls_cert, tls_key)
    elif not _is_loopback(bind):
        made = tls.ensure(bind, allow_hosts + own)
        ssl_context = tls.ssl_context(made.cert, made.key)
        ca_pem = made.ca.read_bytes()
        served_as = made.names[0] if made.names else ""
        own = own + tuple(made.names) + tuple(str(ip) for ip in made.ips)
        if made.ca_created:
            console.print(f"Made a CA for this builder: {made.ca} (import it once in each browser)")
        for name in made.uncovered:
            console.print(f"[yellow]⚠  {name} is outside the names the CA at {made.ca} was made for; browsers "
                          f"refuse the certificate for it. Remove ca.pem and ca.key there to make a new CA[/yellow]")
    allowed = tuple(dict.fromkeys(LOOPBACK_HOSTS + tuple(h.lower() for h in allow_hosts) + own))
    with tempfile.TemporaryDirectory(prefix="rodeo-builder-sources-") as tmp:
        checkouts = given
        if not no_fetch_sources:
            console.print("Fetching chapter sources...")
            checkouts, errors = fetch_sources(Path(tmp), given)
            for sid, err in errors.items():
                console.print(f"[yellow]⚠  chapter source {sid} left out: {err}[/yellow]")
        local_builder = None
        if start_lab_builder and not lab_builder_url and checkout is not None:
            try:
                local_builder = labbuilder.start(checkout, bind, lab_builder_port)
                lab_builder_url = local_builder.url
            except RuntimeError as exc:
                console.print(f"[yellow]⚠  {exc}; linking to the published lab-builder instead[/yellow]")
        lab_builder_url = lab_builder_url or LAB_BUILDER_URL
        try:
            api = Api(checkout, lab_builder_url, liab_version, checkouts, can_save=True,
                      labinabox_missing=liab_missing, can_publish=_is_loopback(bind),
                      can_deploy=True, need_login=not _is_loopback(bind))
            status, liab = api.dispatch("labinabox")
            if status == 200 and liab["error"]:
                console.print(f"[yellow]⚠  lab-in-a-box catalogue: {liab['error']}[/yellow]")
            server = make_server(api, bind, port, __version__, allowed, lab_builder_url,
                                 ssl_context=ssl_context, ca_pem=ca_pem)
            shown = "[{}]".format(bind) if ":" in bind else bind
            if served_as:
                shown = served_as
            elif bind in WILDCARD_BINDS:
                shown = "127.0.0.1"
            url = "{}://{}:{}/".format("https" if ssl_context else "http", shown, server.server_address[1])
            console.print(f"\n[bold green]Rodeo Builder[/bold green] → [cyan]{url}[/cyan]  (Ctrl-C to stop)")
            if bind in WILDCARD_BINDS:
                console.print(f"[dim]Answers on every interface as: {', '.join(own) or '(none found)'}; "
                              "add others with --allow-host.[/dim]")
            if ca_pem:
                console.print(f"[dim]CA certificate for browsers: {url}ca.pem[/dim]")
            if local_builder:
                console.print(f"[dim]lab-in-a-box lab-builder {liab_version} → {url.rsplit(':', 1)[0]}:{lab_builder_port}/[/dim]")
            else:
                console.print(f"[dim]lab-in-a-box lab-builder → {lab_builder_url}[/dim]")
            console.print("[dim]Save to profiles writes ~/.rodeo/profiles/<name>/; deploy with rodeo up --profile <name>.[/dim]")
            if open_browser:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        finally:
            if local_builder:
                local_builder.stop()
