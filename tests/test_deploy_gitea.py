"""_deploy_gitea's git dependency on Leap Micro 6.2 (transactional, no zypper installs)."""
from __future__ import annotations

import subprocess

from rodeo.engine.rancher import RancherPhase


def _cfg():
    return {
        "network": {"vip": "10.0.0.10", "rancher_ip": "10.0.0.9",
                    "gateway": "10.0.0.1", "dns_domain": "lab.example"},
        "credentials": {"harvester_admin_password": "x", "rancher_admin_password": "x"},
        "vms": {"rancher": {}},
    }


def _captured_gitea_script():
    phase = RancherPhase(_cfg())
    captured = {}

    def fake_run(cmd, **kw):
        captured["script"] = kw.get("input", "")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    phase._run = fake_run
    gen = phase._deploy_gitea()
    try:
        while True:
            next(gen)
    except StopIteration:
        pass
    return captured["script"]


def test_no_zypper_install_git_on_transactional_leap_micro():
    """Regression: `zypper install git` silently no-ops on Leap Micro 6.2's
    transactional-update image (exit 0, "please use transactional-update...",
    git still absent) — confirmed live. Every subsequent bare `git` call must
    not depend on git being installed on the host."""
    script = _captured_gitea_script()
    assert "zypper install" not in script


def test_git_wrapper_runs_containerized_with_host_networking():
    """git must run via a throwaway container (podman is already required/working
    for Gitea itself) with --network host — the git push targets
    http://localhost:3000/..., which only resolves to the host's Gitea container
    if the git container shares the host's network namespace."""
    script = _captured_gitea_script()
    assert "git() {" in script
    wrapper_line = next(line for line in script.splitlines() if "docker.io/alpine/git" in line)
    assert "--network host" in wrapper_line


def test_git_wrapper_does_not_double_up_the_entrypoint():
    """Regression: docker.io/alpine/git's own image config sets
    ENTRYPOINT ["git"] (confirmed live via the registry API) — passing "git" a
    second time as the first arg makes the container run `git git -C ... init`,
    which fails ("'git' is not a git command. See 'git --help'.")."""
    script = _captured_gitea_script()
    wrapper_line = next(line for line in script.splitlines() if "docker.io/alpine/git" in line)
    assert wrapper_line.rstrip().endswith('docker.io/alpine/git:latest "$@"')


def test_git_dash_c_calls_reference_the_same_mounted_path():
    """The EIB_REPO volume must be mounted at the identical host path so the
    existing `git -C "$EIB_REPO" ...` calls need no changes to work inside
    the wrapper's container."""
    script = _captured_gitea_script()
    assert 'podman run --rm --network host -v "$EIB_REPO:$EIB_REPO:Z"' in script
    assert 'git -C "$EIB_REPO" init' in script
    assert 'git -C "$EIB_REPO" push -u origin HEAD:main' in script


def test_gitea_container_replace_and_idempotent_setup_calls():
    """Regression: a retry after a later step fails (e.g. the git push) leaves
    the gitea container running under the same name — confirmed live
    ("container name 'gitea' is already in use"). gitea-data is a named
    volume, so a --replace'd container keeps prior state (admin user, migrated
    repos), meaning admin-user-create and the two repo-creation calls also
    need to tolerate "already exists" rather than aborting the whole script."""
    script = _captured_gitea_script()
    assert "podman run -d --name gitea --replace" in script
    assert 'admin user create \\\n  --username "$GITEA_USER" --password "$GITEA_PASS" \\\n  --email gitea@aerogrid.local --admin --must-change-password=false \\\n  || echo' in script
    assert '"repo_name":"alien-geeko"' in script
    lines = script.splitlines()
    migrate_idx = next(i for i, line in enumerate(lines) if "repos/migrate" in line)
    migrate_block = "\n".join(lines[migrate_idx:migrate_idx + 6])
    assert "|| echo" in migrate_block
    eib_repo_create_line = next(line for line in lines if '"name":"eib-config"' in line)
    assert "|| echo" in eib_repo_create_line
