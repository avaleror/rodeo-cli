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

## KVM host (Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
rodeo up
```

`install.sh` needs root or passwordless sudo. It installs `python3`, `pip` and `git`
with zypper, apt-get or dnf, clones rodeo to `/opt/rodeo-cli`, creates a virtualenv
there and links `/usr/local/bin/rodeo`. Pin a version with `--ref <tag|branch|sha>`.
You normally never run this by hand on cloud hosts: `rodeo up --target aws` and
`rodeo fleet deploy` run it on the host for you.

## Control machine on Linux

Two ways:

- **`install.sh`** (as above), then add the AWS extra, which `install.sh` does not
  install:

    ```bash
    sudo /opt/rodeo-cli/.venv/bin/pip install -e '/opt/rodeo-cli[aws]'
    ```

- **A user checkout**, the same as on macOS below (recommended if you change rodeo
  itself, or do not want a system-wide install). Use your distribution's `python3`
  (3.10 or newer) instead of Homebrew.

## Control machine on macOS

`install.sh` does not support macOS (it only knows zypper, apt-get and dnf). Install
from a checkout instead:

```bash
# 1. Prerequisites
xcode-select --install                 # git + OpenSSH, if not installed yet
brew install python@3.13 awscli        # Python 3.10+ and AWS CLI v2

# 2. rodeo in its own virtualenv, editable
git clone https://github.com/avaleror/rodeo-cli.git ~/GitHub/rodeo-cli
cd ~/GitHub/rodeo-cli
python3.13 -m venv .venv
.venv/bin/pip install -e '.[aws]'

# 3. Put `rodeo` on your PATH
mkdir -p ~/.local/bin
ln -sf ~/GitHub/rodeo-cli/.venv/bin/rodeo ~/.local/bin/rodeo
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc   # if ~/.local/bin is not on PATH yet
rodeo --version
```

To update: `git -C ~/GitHub/rodeo-cli pull`. The install is editable, so whatever
branch is checked out in that directory is what `rodeo` runs.

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
| Installer | `install.sh` (root, `/opt/rodeo-cli`) | Manual checkout + virtualenv (above) |
| Python | Distribution `python3` 3.10+ | System `python3` is 3.9: too old. Use Homebrew's |
| `[aws]` extra | Install it separately (see above) | Part of the `pip install -e '.[aws]'` step |
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
