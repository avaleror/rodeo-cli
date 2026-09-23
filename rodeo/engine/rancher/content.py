"""Lab content seeding: local Gitea + the demo-app Fleet GitRepo.

This is workshop material, not infrastructure — kept in its own module so it
can move behind an external deployer (e.g. a lab-in-a-box addon) later without
touching the cluster orchestration code.
"""
from __future__ import annotations

from typing import Generator

from ..runner import DeployEvent, LogLine


class LabContentMixin:
    """Seed the Gitea mirror and Fleet GitRepo used by lab exercises."""

    def _eib_elemental_packages_block(self) -> str:
        """operatingSystem.packages for the two Elemental-path definitions
        (edge1/edge2).

        Default: side-load the elemental-register/elemental-system-agent RPMs
        already staged at /home/eib-config/rpms/ (see hauler.py) via EIB's
        rpms/ directory convention — no SUSE Customer Center entitlement
        needed by anyone. noGPGCheck is required because we don't import the
        openSUSE signing key for these dev-channel packages.

        A plan can instead set elemental.scc_registration_code to use a real
        SCC-registered zypper install if one is available — useful when the
        side-loaded RPMs' version doesn't match a pinned elemental-operator.
        """
        code = self.elemental_scc_registration_code
        # An unresolved "??..." secrets placeholder (never added to secrets.yaml)
        # is not a real code — treat it the same as unset.
        if code and not code.startswith("??"):
            return f"  packages:\n    sccRegistrationCode: {code}\n"
        return "  packages:\n    noGPGCheck: true\n"

    def _create_alien_geeko_fleet(self) -> Generator[DeployEvent, None, bool]:
        """Create a Fleet GitRepo for the demo app declared in cfg["alien_geeko"].

        Defaults to Alien-Geeko (https://github.com/SUSE-Technical-Marketing/Alien-Geeko), a
        Node.js CRT terminal web app showing Kubernetes cluster vitals, but every name/label here
        comes from self.alien_geeko_* (set from cfg["alien_geeko"] in __init__) so a rodeo-plan.yaml
        override can point this at a different demo app entirely.

        Participants label their edge cluster after Elemental registers + provisions it.
        The GitRepo is ready in advance so deployment kicks in the moment the label appears.
        """
        labels_yaml = "".join(
            f'          {k}: "{v}"\n' for k, v in self.alien_geeko_target_labels.items()
        )
        selector_yaml = ", ".join(
            f"{k}={v}" for k, v in self.alien_geeko_target_labels.items()
        )
        manifest = (
            "apiVersion: fleet.cattle.io/v1alpha1\n"
            "kind: GitRepo\n"
            "metadata:\n"
            f"  name: {self.alien_geeko_fleet_name}\n"
            f"  namespace: {self.alien_geeko_fleet_namespace}\n"
            "spec:\n"
            f"  repo: http://{self.eib_ip}:{self.gitea_port}/{self.gitea_user}/{self.alien_geeko_fleet_name}.git\n"
            "  branch: main\n"
            "  targets:\n"
            "    - name: x86-edge-clusters\n"
            "      clusterSelector:\n"
            "        matchLabels:\n"
            f"{labels_yaml}"
        )
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"cat <<'__GITREPO__' | kubectl apply -f -\n"
            f"{manifest}"
            "__GITREPO__\n"
        )
        yield LogLine(f"Creating Fleet GitRepo for {self.alien_geeko_fleet_name} demo app...")
        r = self._ssh_script(script, timeout=30)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Fleet GitRepo creation failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine(
            f"  Fleet GitRepo '{self.alien_geeko_fleet_name}' created in {self.alien_geeko_fleet_namespace}.\n"
            f"  To deploy: label an edge cluster with  {selector_yaml}\n"
            f"  Image served from Hauler: http://{self.eib_ip}:5000 (docker.io mirror)"
        )
        return True

    def _deploy_gitea(self) -> Generator[DeployEvent, None, bool]:
        """Deploy Gitea as a rootless Podman container on the EIB VM.

        Gitea runs on port 3000 alongside Hauler (port 5000/8080). The Alien-Geeko
        repo is mirrored from GitHub once at deploy time using Gitea's migration API
        (no git binary needed on the host). After deploy, Fleet syncs exclusively from
        local Gitea — no GitHub access needed during lab exercises.

        Credentials: admin_user from definition.yaml, password from secrets.yaml.
        """
        image = f"docker.io/gitea/gitea:{self.gitea_version}-rootless"
        gitea_url = f"http://localhost:{self.gitea_port}"
        # Same filenames _populate_hauler stages onto the eib VM (self.iso_fname /
        # self.raw_fname, set once in RancherPhase.__init__) — the .raw (not
        # .raw.xz) is what actually lands in base-images/ after decompression.
        iso_fname = self.iso_fname
        raw_fname = self.raw_fname
        reg_name = f"{self.elemental_reg_prefix}-reg-1"

        # NMState network-config, one file per edge node, generated from the
        # definition (name + IP + MAC + prefix + gateway + DNS). No node names,
        # IPs or MACs are hardcoded here — add/remove/renumber edge nodes in
        # definition.yaml and these regenerate to match.
        nmstate_blocks = ""
        for e in self.edge_nodes:
            nmstate_blocks += (
                f"cat > \"$EIB_REPO/network-configs/{e['name']}.yaml\" << 'NM_EOF'\n"
                "interfaces:\n  - name: eth0\n    type: ethernet\n    state: up\n"
                # nmc (EIB's network config generator) rejects any ethernet
                # interface with no mac-address: "Detected Ethernet interfaces
                # without a MAC address" (confirmed live) — needed so EIB can
                # match this static config to the right physical node at boot.
                f"    mac-address: {e['mac']}\n"
                f"    ipv4:\n      address:\n        - ip: {e['ip']}\n          prefix-length: {self.net_prefix}\n"
                "      dhcp: false\n      enabled: true\n"
                "routes:\n  config:\n    - destination: 0.0.0.0/0\n"
                f"      next-hop-address: {self.gateway}\n      next-hop-interface: eth0\n"
                # NMState's dns-resolver.config field is "server" (singular) —
                # EIB's nmc tool rejects "servers": "unknown field `servers`,
                # expected one of `server`, `search`, `options`" (confirmed live).
                f"dns-resolver:\n  config:\n    server:\n      - {self.dns_server}\n"
                "NM_EOF\n\n"
            )

        script = (
            "set -euo pipefail\n"
            f"GITEA_URL={gitea_url}\n"
            f"GITEA_USER={self.gitea_user}\n"
            f'GITEA_PASS="{self.gitea_password}"\n\n'
            # Start Gitea container (rootless, no SSH, SQLite backend).
            # --replace: a retry after a failure further down this same script
            # (e.g. the git-push step) leaves this container running under the
            # same name — confirmed live ("container name 'gitea' is already in
            # use"). gitea-data is a named volume, so replacing the container
            # keeps all prior state (users, repos) intact; every step below that
            # creates something already-created-by-a-prior-attempt tolerates that
            # for the same reason.
            f"podman run -d --name gitea --replace --restart=always \\\n"
            f"  -p {self.gitea_port}:{self.gitea_port} \\\n"
            "  -v gitea-data:/data \\\n"
            f'  -e GITEA__security__INSTALL_LOCK=true \\\n'
            f'  -e GITEA__server__ROOT_URL="http://{self.eib_ip}:{self.gitea_port}" \\\n'
            f"  -e GITEA__server__HTTP_PORT={self.gitea_port} \\\n"
            "  -e GITEA__server__DISABLE_SSH=true \\\n"
            f'  "{image}"\n\n'
            # Wait up to 60 s for the API to respond
            'echo "Waiting for Gitea..."\n'
            "for i in $(seq 1 30); do\n"
            '  curl -sf "$GITEA_URL/api/v1/version" >/dev/null 2>&1 && break\n'
            "  sleep 2\n"
            "done\n"
            'curl -sf "$GITEA_URL/api/v1/version" >/dev/null || '
            '{ echo "Gitea did not start in time"; exit 1; }\n\n'
            # Create admin user via the Gitea CLI inside the container. Tolerate
            # "already exists" (persisted in gitea-data from a prior attempt).
            "podman exec --user git gitea /usr/local/bin/gitea admin user create \\\n"
            '  --username "$GITEA_USER" --password "$GITEA_PASS" \\\n'
            "  --email gitea@rodeo.local --admin --must-change-password=false \\\n"
            '  || echo "  (admin user already exists, continuing)"\n\n'
            # Generate API token for setup calls. write:repository alone covers
            # the /api/v1/repos/migrate call (alien-geeko) but NOT
            # /api/v1/user/repos (eib-config, further down) — confirmed live:
            # that endpoint 403s with "token does not have at least one of
            # required scope(s): [write:user]" without it.
            "TOKEN=$(curl -sf -X POST "
            '"$GITEA_URL/api/v1/users/$GITEA_USER/tokens" \\\n'
            '  -u "$GITEA_USER:$GITEA_PASS" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            "  -d '{\"name\":\"setup\",\"scopes\":[\"write:repository\",\"write:user\"]}' \\\n"
            "  | python3 -c "
            "\"import sys,json; print(json.load(sys.stdin)['sha1'])\")\n\n"
            # Mirror the demo app repo from GitHub via Gitea's migration API.
            # Gitea clones the repo internally — no git binary needed on the host.
            # This is the one internet call that happens at deploy time.
            # Tolerate a genuine 409 "already exists" (a prior attempt may have
            # migrated it successfully before failing at a later step) but fail
            # loud on anything else — see the eib-config creation below for why
            # a blanket `|| echo` is the wrong tool here.
            "RC=$(curl -s -o /tmp/alien-geeko-migrate.json -w '%{http_code}' "
            '-X POST "$GITEA_URL/api/v1/repos/migrate" \\\n'
            '  -H "Authorization: token $TOKEN" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            f'  -d \'{{"clone_addr":"{self.alien_geeko_fleet_repo}",'
            f'"repo_name":"{self.alien_geeko_fleet_name}","private":false,"mirror":false}}\')\n'
            'if [ "$RC" = "409" ]; then\n'
            f'  echo "  ({self.alien_geeko_fleet_name} repo already exists, continuing)"\n'
            'elif [ "$RC" != "200" ] && [ "$RC" != "201" ]; then\n'
            f'  echo "  {self.alien_geeko_fleet_name} migrate failed (HTTP $RC): $(cat /tmp/alien-geeko-migrate.json)"\n'
            "  exit 1\n"
            "fi\n\n"
            f'echo "  {self.alien_geeko_fleet_name}: http://{self.eib_ip}:{self.gitea_port}/$GITEA_USER/{self.alien_geeko_fleet_name}.git"\n\n'
            # ---- eib-config Gitea repo with EIB definition templates ----
            # Leap Micro 6.2's transactional-update model means `zypper install
            # git` silently no-ops (exit 0, "please use transactional-update to
            # update or modify the system", git still absent — confirmed live:
            # every subsequent `git` call below then failed "command not found",
            # aborting the whole script under set -e with no clear error surfaced
            # up top, since stdout/stderr get concatenated and reordered by the
            # time this method's caller prints them). Installing via
            # transactional-update needs a reboot mid-deploy, so run git in a
            # throwaway container instead — podman is already required and
            # working here for the Gitea container itself. Mounting $EIB_REPO at
            # the *same* path means every existing `git -C "$EIB_REPO" ...` call
            # below needs no changes.
            # --network host: the git push below targets http://localhost:3000/...
            # (Gitea on the host's own network namespace) — without this the
            # container gets its own network namespace and "localhost" would
            # resolve to itself, not the host, and the push would fail to connect.
            # No literal "git" before "$@": docker.io/alpine/git's own image
            # config sets ENTRYPOINT ["git"] (confirmed live via the registry
            # API) — passing "git" again here means the container actually runs
            # `git git -C ... init`, which fails ("'git' is not a git command").
            "git() {\n"
            '  podman run --rm --network host -v "$EIB_REPO:$EIB_REPO:Z" docker.io/alpine/git:latest "$@"\n'
            "}\n\n"
            # Tolerate a genuine 409 "already exists" (see the --replace note
            # above) but fail loud on anything else — a blanket `|| echo` here
            # previously masked a real 403 (missing write:user token scope,
            # fixed above) as if it were a harmless already-exists, so the
            # actual error only surfaced several steps later as a confusing
            # git-push 403 instead of at its real source.
            "RC=$(curl -s -o /tmp/eib-config-create.json -w '%{http_code}' "
            '-X POST "$GITEA_URL/api/v1/user/repos" \\\n'
            "  -H \"Authorization: token $TOKEN\" \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{\"name\":\"eib-config\",\"description\":\"AeroGrid EIB image definitions, network configs and combustion scripts\",\"private\":false,\"auto_init\":false}')\n"
            'if [ "$RC" = "409" ]; then\n'
            '  echo "  (eib-config repo already exists, continuing)"\n'
            'elif [ "$RC" != "200" ] && [ "$RC" != "201" ]; then\n'
            '  echo "  eib-config repo creation failed (HTTP $RC): $(cat /tmp/eib-config-create.json)"\n'
            "  exit 1\n"
            "fi\n\n"
            "EIB_REPO=/tmp/eib-config-repo\n"
            "rm -rf \"$EIB_REPO\"\n"
            "mkdir -p \"$EIB_REPO/network-configs\" \"$EIB_REPO/scripts-available\" "
            "\"$EIB_REPO/elemental\" \"$EIB_REPO/network\"\n\n"
            # .gitignore — keep build outputs and the transient network/ and
            # custom/ dirs out of git. custom/scripts/ is per-build state (like
            # network/): EIB auto-discovers and runs EVERYTHING under it with no
            # way to select a subset, so it must hold only the current node's
            # scripts, copied in from scripts-available/ right before each build
            # — never pre-populated here with every node's scripts at once
            # (confirmed live: edge1's Elemental build with all nodes' scripts
            # present ran edge3's hostnamectl combustion script too, which fails
            # this early in boot and drops the node into emergency mode).
            "cat > \"$EIB_REPO/.gitignore\" << 'GITIGNORE_EOF'\n"
            "*.iso\n*.raw\n*.qcow2\nnetwork/\ncustom/\n.eib/\n"
            "GITIGNORE_EOF\n\n"
            # Stage combustion scripts in scripts-available/ (like
            # network-configs/) — not auto-discovered by EIB from here, so
            # staging them is safe; each build copies only what it needs into
            # custom/scripts/ right before running.
            "cp /home/eib-config/scripts/99-k3s-registries.sh \"$EIB_REPO/scripts-available/\"\n\n"
            # Hostname combustion scripts (edge3 and edge4 standalone path).
            # Numbered 60- (not 10-): EIB reserves 00-49 for its own internal
            # combustion scripts.
            "cat > \"$EIB_REPO/scripts-available/60-hostname-edge3.sh\" << 'HNAME3_EOF'\n"
            "#!/bin/bash\nhostnamectl set-hostname edge3\nHNAME3_EOF\n"
            "chmod +x \"$EIB_REPO/scripts-available/60-hostname-edge3.sh\"\n\n"
            "cat > \"$EIB_REPO/scripts-available/60-hostname-edge4.sh\" << 'HNAME4_EOF'\n"
            "#!/bin/bash\nhostnamectl set-hostname edge4\nHNAME4_EOF\n"
            "chmod +x \"$EIB_REPO/scripts-available/60-hostname-edge4.sh\"\n\n"
            # NMState network config templates — one per edge node, generated
            # above from the definition (see nmstate_blocks).
            + nmstate_blocks +
            # Elemental registration config — filled in during Exercise 2, section 2.4.
            # Lives in elemental/elemental_config.yaml — EIB's own dedicated,
            # auto-detected directory for this (NOT the generic os-files/
            # convention: confirmed against EIB's docs that only elemental/
            # triggers EIB's Elemental-aware package resolution, which is what
            # actually bundles elemental-register/elemental-system-agent into
            # the image and wires up registration on first boot. A file dropped
            # via os-files/ instead lands on disk but EIB never resolves those
            # packages for it — confirmed live: install completed cleanly with
            # correct networking, but the node never contacted the Elemental
            # Operator at all, because elemental-register was never installed).
            # No YAML field references this directory; EIB auto-discovers it.
            "cat > \"$EIB_REPO/elemental/elemental_config.yaml\" << 'ELEM_EOF'\n"
            "# Filled in during Exercise 2, section 2.4.\n"
            "# On the eib VM, after cloning this repo:\n"
            f"#   REGURL=$(ssh root@{self.rancher_ip} \\\n"
            f"#     \"kubectl get machineregistration {reg_name} \\\n"
            "#      -n fleet-default -o jsonpath='{.status.registrationURL}'\")\n"
            "#   curl -k \"$REGURL\" > elemental/elemental_config.yaml\n"
            "ELEM_EOF\n\n"
            # EIB definition files — schema notes (EIB 1.3.3, apiVersion 1.2):
            #  - embeddedArtifactRegistry.registries needs apiVersion >= 1.2, and
            #    EIB unconditionally requires a non-empty username/password on
            #    every registry entry (pkg/image/validation/registry.go), even
            #    for this unauthenticated local Hauler mirror — the values below
            #    are placeholders EIB requires syntactically, not real secrets.
            #  - there is no operatingSystem.files field; the elemental/
            #    directory above (not os-files/) is what actually bundles the
            #    registration config for an Elemental build.
            #  - there is no operatingSystem.scripts field either; combustion
            #    scripts are auto-discovered from custom/scripts/ (populated above).
            # EIB definition files — Elemental ISO path (edge1, edge2). By default
            # these side-load the elemental-register/elemental-system-agent RPMs
            # hauler.py already staged at /home/eib-config/rpms/ (mounted at
            # /eib/rpms — see the podman run command in Exercise 3), so no SUSE
            # Customer Center entitlement is needed. A plan can opt into a real
            # SCC-registered install instead via elemental.scc_registration_code.
            "cat > \"$EIB_REPO/elemental-edge1-definition.yaml\" << '__DEF1__'\n"
            "apiVersion: 1.2\n\n"
            "image:\n  imageType: iso\n  arch: x86_64\n"
            f"  baseImage: {iso_fname}\n"
            "  outputImageName: elemental-edge1.iso\n\n"
            "operatingSystem:\n  kernelArgs:\n    - net.ifnames=0\n"
            f"{self._eib_elemental_packages_block()}\n"
            "embeddedArtifactRegistry:\n  registries:\n"
            f"    - uri: {self.eib_ip}:5000\n"
            "      authentication:\n        username: hauler\n        password: hauler\n"
            "__DEF1__\n\n"
            "cat > \"$EIB_REPO/elemental-edge2-definition.yaml\" << '__DEF2__'\n"
            "apiVersion: 1.2\n\n"
            "image:\n  imageType: iso\n  arch: x86_64\n"
            f"  baseImage: {iso_fname}\n"
            "  outputImageName: elemental-edge2.iso\n\n"
            "operatingSystem:\n  kernelArgs:\n    - net.ifnames=0\n"
            f"{self._eib_elemental_packages_block()}\n"
            "embeddedArtifactRegistry:\n  registries:\n"
            f"    - uri: {self.eib_ip}:5000\n"
            "      authentication:\n        username: hauler\n        password: hauler\n"
            "__DEF2__\n\n"
            # EIB definition files — standalone cluster RAW path (edge3 RKE2, edge4 K3s).
            # No Elemental involved, so no SCC registration code is needed here.
            # rawConfiguration.diskSize expands the base RAW disk before embedding
            # content — without it, the image stays at the base OS's original size
            # and the build fails ("insufficient available disk space", confirmed
            # live: the base image needed ~1.3 GB more just for RKE2 + its images).
            "cat > \"$EIB_REPO/rke2-edge3-definition.yaml\" << '__DEF3__'\n"
            "apiVersion: 1.2\n\n"
            "image:\n  imageType: raw\n  arch: x86_64\n"
            f"  baseImage: {raw_fname}\n"
            "  outputImageName: rke2-edge3.raw\n\n"
            "operatingSystem:\n  kernelArgs:\n    - net.ifnames=0\n"
            "  rawConfiguration:\n    diskSize: 15G\n\n"
            "kubernetes:\n  version: v1.35.3+rke2r3\n\n"
            "embeddedArtifactRegistry:\n  registries:\n"
            f"    - uri: {self.eib_ip}:5000\n"
            "      authentication:\n        username: hauler\n        password: hauler\n"
            "__DEF3__\n\n"
            "cat > \"$EIB_REPO/k3s-edge4-definition.yaml\" << '__DEF4__'\n"
            "apiVersion: 1.2\n\n"
            "image:\n  imageType: raw\n  arch: x86_64\n"
            f"  baseImage: {raw_fname}\n"
            "  outputImageName: k3s-edge4.raw\n\n"
            "operatingSystem:\n  kernelArgs:\n    - net.ifnames=0\n"
            "  rawConfiguration:\n    diskSize: 15G\n\n"
            "kubernetes:\n  version: v1.35.5+k3s1\n\n"
            "embeddedArtifactRegistry:\n  registries:\n"
            f"    - uri: {self.eib_ip}:5000\n"
            "      authentication:\n        username: hauler\n        password: hauler\n"
            "__DEF4__\n\n"
            # Commit and push to local Gitea
            "git -C \"$EIB_REPO\" init\n"
            "git -C \"$EIB_REPO\" config user.email \"rodeo@rodeo.local\"\n"
            "git -C \"$EIB_REPO\" config user.name \"AeroGrid Lab\"\n"
            "git -C \"$EIB_REPO\" add .\n"
            "git -C \"$EIB_REPO\" commit -m \"initial EIB config templates for AeroGrid edge nodes\"\n"
            f"git -C \"$EIB_REPO\" remote add origin \"http://$GITEA_USER:$GITEA_PASS@localhost:{self.gitea_port}/gitea/eib-config.git\"\n"
            "git -C \"$EIB_REPO\" push -u origin HEAD:main\n"
            "rm -rf \"$EIB_REPO\"\n\n"
            f'echo "  eib-config: http://{self.eib_ip}:{self.gitea_port}/$GITEA_USER/eib-config.git"\n'
        )
        yield LogLine(
            f"Deploying Gitea {self.gitea_version} on eib VM ({self.eib_ip}:{self.gitea_port})..."
        )
        r = self._eib_ssh_script(script, timeout=300)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Gitea deployment failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine(
            f"  Gitea ready. {self.alien_geeko_fleet_name} and eib-config repos initialised.\n"
            f"  Fleet GitRepo: http://{self.eib_ip}:{self.gitea_port}"
            f"/{self.gitea_user}/{self.alien_geeko_fleet_name}.git\n"
            f"  EIB workspace: http://{self.eib_ip}:{self.gitea_port}"
            f"/{self.gitea_user}/eib-config.git"
        )
        return True
