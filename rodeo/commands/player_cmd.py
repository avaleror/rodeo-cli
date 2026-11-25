"""rodeo player: the Instruqt-like player for one lab (virt rodeo first).

Runs on the KVM host:

    sudo rodeo player setup --track-ref <sha>   # install player, terminal, Caddy
    rodeo player serve --config FILE            # what the systemd unit runs
    sudo rodeo player test                      # setup/check/solve/check per chapter

New and opt-in: nothing else in rodeo calls it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click


@click.group("player")
def player_cmd() -> None:
    """Instruqt-like player for one lab (assignment, tabs, Check)."""


@player_cmd.command("serve")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False),
              default="/etc/rodeo-player/config.json", show_default=True)
def serve_cmd(config_path: str) -> None:
    """Serve the player on loopback (Caddy is the public listener)."""
    from ..player.server import load_config, serve

    serve(load_config(config_path))


@player_cmd.command("setup")
@click.option("--track-ref", required=True, help="suse-virt-rodeo commit SHA to pin (40 hex).")
@click.option("--track-repo", default=None, help="Track git URL (default: suse-virt-rodeo).")
@click.option("--base", default=None,
              help="Player hostname; hv.<base> and rancher.<base> are the UIs. "
                   "Default: <public-ip>.sslip.io from AWS instance metadata.")
@click.option("--harvester-upstream", default="192.168.122.10:443", show_default=True)
@click.option("--rancher-upstream", default="192.168.122.9:30002", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def setup_cmd(track_ref: str, track_repo: str | None, base: str | None, harvester_upstream: str,
              rancher_upstream: str, as_json: bool) -> None:
    """Install the player on this lab host (root, after rodeo up)."""
    from ..player import setup as s

    if os.geteuid() != 0:
        raise click.ClickException("run with sudo: setup installs users, units and binaries")
    try:
        base = base or s.sslip_base(s.aws_public_ip())
    except OSError as exc:
        raise click.ClickException(f"no --base and no AWS metadata: {exc}") from exc
    opts = s.SetupOptions(
        base=base, track_ref=track_ref, track_repo=track_repo or s.TRACK_REPO,
        harvester_upstream=harvester_upstream, rancher_upstream=rancher_upstream,
        sandbox_id=base.split(".", 1)[0],
    )
    try:
        result = s.run_setup(opts, sudo_user=os.environ.get("SUDO_USER"))
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(result))
        return
    click.echo(f"Lab link:   {result['access_url']}")
    click.echo("            (give it only to this lab's student; it signs a browser in for 24 h)")
    click.echo(f"Player:     {result['player']}")
    click.echo(f"Harvester:  {result['harvester']}")
    click.echo(f"Rancher:    {result['rancher']}")
    click.echo(f"Chapters:   {result['chapters']}")
    click.echo(f"Variables:  {', '.join(result['variables_seeded']) or 'none found in secrets.yaml'}")
    click.echo(f"Caddy CA:   {result['ca_root']} (trust it in the tester's browser)")


@player_cmd.command("link")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False),
              default="/etc/rodeo-player/config.json", show_default=True)
@click.option("--rotate", is_flag=True, help="New link; every link and browser signed in so far is logged out.")
def link_cmd(config_path: str, rotate: bool) -> None:
    """Print this lab's access link (root)."""
    from ..player import auth
    from ..player.server import load_config

    if os.geteuid() != 0:
        raise click.ClickException("run with sudo: the link is a secret only root can read")
    cfg = load_config(config_path)
    if not cfg.auth_file:
        raise click.ClickException("this player has no login configured (re-run rodeo player setup)")
    a = auth.rotate(cfg.auth_file) if rotate else auth.load(cfg.auth_file)
    if rotate:
        import subprocess

        subprocess.run(["systemctl", "restart", "rodeo-player"], check=False)
    click.echo(f"https://{cfg.hosts['player']}/enter?t={a.token}")


@player_cmd.command("test")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False),
              default="/etc/rodeo-player/config.json", show_default=True)
@click.option("--keep-going", is_flag=True, help="Do not stop at the first failing chapter.")
def test_cmd(config_path: str, keep_going: bool) -> None:
    """Instruqt-style track test against the live lab.

    Per chapter: setup, check, solve, check, cleanup. It solves every
    chapter on the real cluster, so run it after the manual walk, not
    before. Progress is kept apart from the player's own state.
    """
    from ..player.runner import Runner, track_test
    from ..player.server import load_config

    cfg = load_config(config_path)
    runner = Runner(cfg.track_dir, Path(cfg.state_dir).parent / "test-state", host=cfg.host,
                    sandbox_id=cfg.sandbox_id, home=cfg.home, kubeconfig=cfg.kubeconfig)
    # Same variables the player renders with (seeded passwords, agent sets).
    from ..player import agent

    for key, value in agent.read_vars(Path(cfg.state_dir) / "vars.json").items():
        runner.set_variable(key, value)
    report = track_test(runner, stop_on_failure=not keep_going)
    failed = False
    for t in report:
        def fmt(r):  # noqa: ANN001, ANN202
            return "-" if r is None else ("ok" if r.ok else ("timeout" if r.timed_out else f"exit {r.exit_code}"))
        click.echo(f"{t.chapter:<45} setup {fmt(t.setup):<8} check {fmt(t.first_check):<8} "
                   f"solve {fmt(t.solve):<8} check {fmt(t.final_check):<8} cleanup {fmt(t.cleanup)}")
        if not t.ok:
            failed = True
            final = t.final_check or t.solve or t.setup
            if final is not None and final.kind == "check":
                click.echo("    " + final.output.strip().replace("\n", "\n    ")[-2000:])
    sys.exit(1 if failed else 0)
