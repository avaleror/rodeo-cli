# Install on Linux and macOS

rodeo runs in two roles, and they have different platform rules:

| Role | What it does | Linux | macOS |
|------|--------------|-------|-------|
| **KVM host** | Runs the lab itself: `rodeo up`, `status`, `stop`, `clean`, ... | Yes (SLES 16 / Leap 16 recommended) | No |
| **Control machine** | Drives remote hosts: `rodeo up --target aws`, `rodeo fleet ...`, `rodeo fleet portal ...`, `rodeo ssh` | Yes | Yes |

A Mac can never be a KVM host: labs need `/dev/kvm`, libvirt and Linux `/proc`.
`rodeo doctor` on a Mac reports 0 GiB RAM and no `/dev/kvm`, which is expected.
Everything else (cloud hosts, fleets, the claim portal) works the same from either
platform.

## One installer, two roles

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

`install.sh` looks at the machine and picks the role:

| Machine | Role | What it installs |
|---------|------|------------------|
| SLES 16 / Leap 16 | **KVM host** | Needs root. `python3`, `pip` and `git` with zypper, rodeo in `/opt/rodeo-cli`, `/usr/local/bin/rodeo`. Then: `rodeo up` |
| macOS, SLES/Leap 15, Ubuntu, Fedora, any other Linux | **Control machine** | No system packages and no root needed. rodeo with the `[aws]` extra in `~/.local/share/rodeo-cli`, `~/.local/bin/rodeo` (as root: `/opt/rodeo-cli`, `/usr/local/bin`). Then: `rodeo up --profile <name> --target aws` |

On a control machine the installer uses a Python 3.10+ that is already there
(Homebrew, `python311` on SLES 15, ...). If there is none, for example the
Python 3.9 of macOS or the 3.6 of SLES 15, it fetches a private Python with a
pinned [uv](https://github.com/astral-sh/uv) into the install directory. The
system Python is never touched and no shell profile is edited; the installer
prints the `PATH` line to add if `~/.local/bin` is not on it yet.

It needs `git` and `curl`. On macOS they come with the Xcode command line tools
(`xcode-select --install`); on Linux, as root, the installer adds them itself.

- `--ref <tag|branch|sha>` pins a version.
- `--mode kvm-host|control-plane` (or `RODEO_MODE`) overrides the automatic choice,
  for example a KVM host on another distribution at your own risk.
- Re-running the installer updates rodeo. An environment left on a Python that is
  too old is rebuilt.

You normally never run it by hand on cloud hosts: `rodeo up --target aws` and
`rodeo fleet deploy` run it on the host for you.

On a control machine, `rodeo up` without `--target aws` stops and says so instead
of starting a lab it cannot run (`--no-deploy` still sets the lab up).
`RODEO_ALLOW_ANY_KVM_HOST=1` lets you try a local lab on an unsupported Linux
anyway.

### From a checkout (if you change rodeo itself)

```bash
git clone https://github.com/avaleror/rodeo-cli.git ~/GitHub/rodeo-cli
cd ~/GitHub/rodeo-cli
bash install.sh --dev        # editable install with the dev extras, in place
```

The install is editable, so whatever branch is checked out in that directory is
what `rodeo` runs.

## AWS credentials (both platforms)

rodeo uses boto3 and never reads keys from `workshop.yaml` or `rodeo-plan.yaml`. Any
standard AWS credential source works: `aws login` (console session, used by SUSE
accounts), `aws sso login`, `~/.aws/credentials`, or environment variables.

```bash
aws login
aws sts get-caller-identity             # confirm before provisioning anything
```

`aws login` sessions need the `awscrt` package, which the `[aws]` extra installs
(`boto3[crt]`). Without it every AWS call fails with `MissingDependencyException`.
These sessions can also throw a transient `CreateOAuth2Token` error mid-command while
still being valid: re-run the command.

## Differences and things to know

| Topic | Linux | macOS |
|-------|-------|-------|
| Installer | `install.sh` (KVM host on SLES 16 / Leap 16, control machine elsewhere) | `install.sh` (control machine) |
| Python | Distribution `python3` 3.10+, else a private one via uv | Homebrew's if present, else a private one via uv (system `python3` is 3.9) |
| `[aws]` extra | Installed on control machines | Installed |
| Local labs (`rodeo up` without `--target aws`) | Yes, on a KVM host | No |
| SSH | OpenSSH | Built-in OpenSSH; nothing extra needed |
| rodeo's own SSH key | `~/.rodeo/ssh/id_ed25519`, created on first use | Same |

**Your laptop's public IP.** A rodeo-managed AWS security group only admits the
public IP of the machine that last ran `provision` (or `up --target aws`). Changing
network (another Wi-Fi, a VPN, tethering) locks you out: SSH and `rodeo fleet` time
out. Re-run `rodeo fleet provision -f workshop.yaml` (or `rodeo up --target aws`
again): it reuses the hosts and moves the rule to your new IP. It also closes any
student ports, so run `rodeo fleet open-access -f workshop.yaml` again afterwards.

**Laptop sleep during long runs.** A deploy runs on the hosts (in tmux), so a
sleeping laptop does not break it. What stops are the commands that follow it from
your machine, such as `rodeo fleet portal publish --watch`. Keep the machine awake
while they run:

```bash
# macOS
caffeinate -i rodeo fleet portal publish --watch -f workshop.yaml
# Linux (systemd)
systemd-inhibit --what=sleep rodeo fleet portal publish --watch -f workshop.yaml
```

If the watch stops anyway, just start it again: labs that are already published stay
published, and the instructor page warns when updates stop arriving.

**Which rodeo runs where.** Your local checkout only drives the control side. KVM
hosts install rodeo from GitHub (`main`, or the ref you pass with `--ref` /
`lab.ref`), so a local change reaches the hosts only after it is pushed. The one
exception is the claim portal: `rodeo fleet portal up` copies the portal code from
your local checkout to the portal VM.

**Scripts around rodeo.** macOS ships BSD tools and zsh, not GNU tools and bash.
Commands you write around rodeo may behave differently: there is no `timeout`
(`brew install coreutils` gives `gtimeout`), `sed -i` needs `sed -i ''`, and zsh does
not word-split unquoted variables. rodeo itself does not depend on any of these on
the control machine.
