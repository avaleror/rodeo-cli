# Lab player

The rodeo player gives each student one browser window for their lab: the
chapter text on the left, then tabs for a terminal, Harvester and Rancher, plus
**Check**, **Next** and **Skip**. It runs the SUSE Virtualization Rodeo track
(`suse-virt-rodeo`) on your own cloud labs, with the same setup, check and solve
scripts the track already has.

It is opt-in. Nothing in rodeo runs it unless you call `rodeo player` or
`rodeo fleet player-setup`. Any lab profile can use it: what the player shows
comes from the profile's `player.yaml` (see [Player profiles](#player-profiles)).
Today `virt-rodeo` is the profile that ships one.

## How it fits together

On each lab host:

- `rodeo-player` serves the page and runs the chapter scripts, on loopback only.
- `rodeo-player-ttyd` is the browser terminal, as the unprivileged `student` user.
- `rodeo-player-caddy` is the only public listener (port 443). Every request
  goes through the player's login first, then to the player, the terminal,
  Harvester or Rancher.

A student signs in once with their lab link (`/enter?t=...`). The link is a
secret: anyone who has it can use that lab. Students can come from any network
and any IP: the link is the lock, not the address. After 20 wrong links in 10
minutes from one address, that address gets "Too many tries" (HTTP 429) for a
while, so a bot cannot load the lab host by hammering `/enter`.

## What the student sees

1. **Lobby**, on the first visit: each part of the lab (one row per tab) as it
   comes up, and the chapters ahead. **Enter lab** turns on when every part
   answers; after 90 seconds a "Continue anyway" link appears.
2. **Chapter cover**, the first time a chapter is opened in that browser:
   chapter number and name, the one-line mission (`teaser`), how many tasks,
   the level (`difficulty`), **Start mission**, then the chapter's story.
3. **Working view**: the lab takes the screen. A narrow rail on the left has
   one button per step. The guide opens as a resizable panel next to the lab
   or folds away; when folded, a small card shows the current step and the
   chapter's main button.
4. **Guide**: the credentials card, one card per step, and "About this
   chapter" at the end. Code blocks have **Copy** and **Paste in terminal**
   (it types the command at the prompt and never presses Enter). A failed
   check shows in the step where it was run, with what the check printed.
5. **Chapter complete**, after a pass: what was done, what comes next, and the
   chapter's "why it matters" and links.

The header shows the chapter trail, a "Lab healthy" indicator (the host
checks each tab every 30 seconds while the page is open) and the theme
switch. Step ticks, covers seen and the guide's width are kept in the
student's browser; progress through the chapters is kept on the lab.

## Writing a track for the player

The player reads the structure authors already write, so any rodeo track
works. Split each chapter's `assignment.md` with top-level headings (a line
underlined with `===`, or `# Title`):

| Heading | Becomes |
|---|---|
| `Chapter N: Name`, first in the file, plus the text up to the next heading | The chapter cover and its story |
| a heading with `Credentials` in it | The credentials card |
| `Task N: ...` or `Step N: ...` | One step each |
| `Bonus ...` or a title ending in `(optional)` | An optional step |
| `Why ...` | "Why it matters" on the chapter complete screen |
| `More information`, `Learn more`, `Resources` | Links on the chapter complete screen |
| anything else | Shown with the step that follows it |

Leading emoji in headings are left out of the rail and the cover. A chapter
with no task headings is shown as a single step, so older tracks still work.
The front matter's `teaser` and `difficulty` fill the cover.

## Every fleet is a workshop

`rodeo fleet` gives students the player and nothing else: no SSH, no UI ports
on the internet. `fleet open-access` opens 443 only, the UIs sit behind the
player's lab login, and the portal hands out one "Open your lab" link per lab.

What decides it, for each fleet:

| Fleet | Player |
|---|---|
| AWS, lab profile with a `player.yaml` | yes, by default |
| `player: false` in workshop.yaml, or `fleet up --no-player` | no: students get the UI URLs and passwords |
| `kvm-hosts` profile | no: KVM clusters for operators, not students |
| a profile with no `player.yaml` (custom, lab-in-a-box) | no, unless asked for, and then an error |
| GCP (and any cloud whose provider has `supports_player = False`) | no, for now |
| static hosts (no `provider:`) | only with a `player:` block |

A profile with a track gets its chapters. A profile without one gets the lab
tabs, a terminal and the credentials, with the guide folded; the guide panel
has a button to the workshop guide when the profile names one (`guide_url`).
Credentials (from the profile's `credentials:`) are always under
**Credentials** in the header.

The student's terminal runs as `student`: no sudo, cannot read `/root`. New
fleets get `per_host_ssh_key: true`, so root on one lab opens no other.

## One lab, test mode

For a quick test on one lab you do not need a domain. Names are
`<ip-dashed>.sslip.io` and Caddy uses its own CA, so the tester trusts that CA
in their browser.

```bash
sudo rodeo player setup --track-ref <40-char suse-virt-rodeo commit>   # a profile with a track
sudo rodeo player setup --profile virt-workshop                           # one without
sudo rodeo player link        # prints the lab link
sudo rodeo player test        # solves every chapter: run it last
```

sslip.io is on the Public Suffix List, so this mode is for testers only, never
for students.

## How long a check may run

Each chapter check is stopped after 120 seconds and counts as a failed check.
A track whose checks wait for something slow (a VM booting, a migration) can
allow more, from 10 to 3600 seconds:

```bash
sudo rodeo player setup --track-ref <sha> --check-timeout 300            # one lab
rodeo fleet player-setup -f workshop.yaml --track-ref <sha> --check-timeout 300   # every lab
```

The value lands in the player config (`/etc/rodeo-player/config.json`,
`check_timeout`); `rodeo player test` uses the same value.

## A fleet with your own domain

For students, use a DNS zone you own in Route 53 (not on the Public Suffix
List) and one wildcard certificate per event. Without one, `fleet up` warns
and runs in test mode: browsers do not trust Caddy's own CA, so the lab tabs
stay blank. Add a `player:` block to `workshop.yaml` (or pass
`fleet up --player-domain <zone>` when it writes a new one):

```yaml
player:
  domain: rodeocli.com      # a public Route 53 hosted zone in the fleet's AWS account
  acme_email: you@example.com   # optional, for Let's Encrypt expiry mail
  # staging: true           # first try: Let's Encrypt staging (untrusted, no rate limit)
provider:
  per_host_ssh_key: true    # one root key per lab (fleet up writes it for new workshops)
portal:
  enabled: true             # the portal hands out each lab's link
```

Each lab gets a random 8-character id and one name per UI under the zone: `<id>`
for the player, then `<ui>-<id>` for each product UI in the profile's
`player.yaml` (`hv-<id>` and `rancher-<id>` for `virt-rodeo`).

One command does the whole run, and a failed run resumes with the same command:

```bash
rodeo fleet up -f workshop.yaml --track-ref <sha> --images-bucket <private image bucket>
rodeo fleet up -f workshop.yaml                  # a profile without a track needs neither
```

It waits until every lab is ready, then fetches the images, writes the lab
names, reuses or issues the certificate, installs the player, opens 443 and
publishes the labs. `--images-bucket` is only for tracks that need private
images. The same steps one by one:

```bash
rodeo fleet provision   -f workshop.yaml
rodeo fleet deploy      -f workshop.yaml
rodeo fleet lab-images  -f workshop.yaml --bucket <private image bucket> --run
rodeo fleet dns         -f workshop.yaml   # ids + A records
rodeo fleet cert        -f workshop.yaml   # *.<zone>, once per event
rodeo fleet player-setup -f workshop.yaml --track-ref <sha>   # --track-ref only with a track
rodeo fleet open-access -f workshop.yaml   # opens 443 only
rodeo fleet portal publish -f workshop.yaml
```

The claim portal gets its own name there too, `portal-<id>.<zone>`, with a
random id per portal (written as `portal.labels.portal_id`, re-picked if the
name already exists), so several portals can run at once without sharing or
guessing names. `fleet portal up` writes its record before installing, and
Caddy on the portal gets the certificate for that one name. An explicit
`portal.hostname` still wins.

`rodeo fleet destroy` also removes the lab names (and the portal's, unless
`--keep-portal`) from the zone.

Before a lab is open to students, the operator's own address can already reach
its player on 443 (like the UI ports), to check it.

`lab-images` signs S3 links on your laptop, and a link dies with the
credentials that signed it. `aws login` credentials live about 15 minutes, so
fetch the images right after the deploy with `--run`. Without `--run`, the
command refuses when your credentials end within the hour.

### DNS records

Route 53 cannot tag a single record, so every name rodeo writes also gets a
TXT record of the same name:

```text
"managed-by=rodeo fleet=<workshop name> host=<host id> region=<aws region>"
```

That TXT is how rodeo knows a name is its own:

- `fleet dns` refuses a name that already holds anything rodeo did not mark
  for that lab, and changes nothing for that lab.
- `fleet dns --delete` and `fleet destroy` delete only names marked for this
  workshop. Records you made by hand are never touched.
- Re-runs are safe. A lab with a new address gets its records moved.
- A lab the dead-man switch terminated never runs `fleet destroy`, so its names
  would stay behind, pointing at an AWS address someone else may get next.
  `fleet dns`, `fleet up` and `fleet destroy` first prune every rodeo-marked
  name (any workshop) whose address is not a live rodeo instance in the
  mark's region. `fleet dns --prune` does only that. If the instance list
  cannot be read, nothing is pruned.

Your AWS identity needs `route53:ListHostedZonesByName`,
`ListResourceRecordSets`, `ChangeResourceRecordSets` and `GetChange` on the zone,
plus `ec2:DescribeInstances` for the prune.

### The certificate

`rodeo fleet cert` runs on your laptop. It downloads a pinned
[lego](https://go-acme.github.io/lego/) release once (checked against a
sha256 in rodeo), then gets `*.<zone>` from Let's Encrypt with a DNS-01
challenge in your zone. lego gets your current AWS credentials as short-lived
environment variables, so an `aws login` session works. The files stay in
`~/.rodeo/certs/<zone>/`; a re-run reuses the certificate until it is due for
renewal.

Let's Encrypt allows 50 certificates per registered domain per week. One
wildcard per event keeps you far from that limit. Use `staging: true` for
your first tries.

`fleet player-setup` pushes the certificate and key to each lab over the SSH
session's stdin. On the lab they live in `/etc/rodeo-player/tls/`, root-only
(directory `0700`, files `0600`). Caddy reads them through systemd
`LoadCredential`, so neither the Caddy user nor the student can open the key
file. Lab hosts never run ACME and never get AWS credentials.

## Player profiles

The player is the same for every SUSE product. What changes per lab sits in a
`player.yaml` next to the lab profile's `rodeo-plan.yaml`
(`rodeo/data/examples/<profile>/player.yaml`). This is the `virt-rodeo` one:

```yaml
title: SUSE Virtualization Rodeo
track_repo: https://github.com/SUSE-Technical-Marketing/suse-virt-rodeo.git  # optional
# guide_url: https://.../workshop-guide/  # without a track: the web guide
kubeconfig: harvester-kubeconfig          # in ~/.rodeo, written by rodeo up (optional)
student_ssh_key: /opt/suse-virt-workshop/lab-vm-key   # optional
uis:
  - key: hv                               # name: hv-<id>.<zone>
    label: SUSE Virtualization (Harvester)
    upstream: 192.168.122.10:443          # on the lab network
    # scheme: http                        # default https (self-signed is fine)
    tab_port: 8443                        # the port the track's tabs use
  - key: rancher
    label: SUSE Rancher Prime
    upstream: 192.168.122.9:30002
    tab_port: 30002
credentials:                              # shown under Credentials in the player
  - label: SUSE Rancher Prime
    user: admin
    password: ??rancher_admin_password
variables:                                # track variable: ??secrets.yaml key
  RANCHER_PASSWORD: ??rancher_admin_password
  HARVESTER_PASSWORD: ??harvester_admin_password
```

Shipped profiles: `virt-rodeo` (track), `virt-workshop`, `virt-workshop-2n`,
`suse-edge` and `suse-telco` (web guide), `harvester`, `harvester-2n`,
`rancher-test`, `rancher-lab-config` (no instructions).

- **uis**: one to eight product UIs. Each gets its own name, its own Caddy site
  behind the lab login, and the track tabs on `tab_port` open it.
- **kubeconfig** and **student_ssh_key**: leave them out when the track does
  not use them. The student gets a copy of each.
- **student_libvirt** and **student_vm_access**: for labs whose guide drives
  VMs from the host. The first puts the student in the `libvirt` group with
  `qemu:///system` as the default (root-equivalent on that host, so `fleet
  player-setup` refuses it without `per_host_ssh_key: true`). The second makes
  the student their own key and installs it for root on the listed lab VMs,
  using root's key, which the student never sees. suse-edge's content phase
  makes that key early and adds it to the edge3/edge4 EIB definitions, so the
  nodes the student builds later trust it too.
- **student_rodeo_commands**: rodeo subcommands the guide runs on the host as
  root (suse-edge: `pull-edge-image`, `eject-iso`). The player writes a sudoers
  rule for exactly those; `rodeo` escalates on its own, so the guide's commands
  stay the same. Needs `student_libvirt: true`, which is already root-equivalent.
- **credentials**: what the student signs in with, resolved on the lab into a
  root-only file the player serves behind the lab login. `??<key>` only.
- **variables**: passwords the track shows, read from the host's
  `~/.rodeo/secrets.yaml`. Always `??<key>`, never the password itself.

`rodeo fleet dns` and `rodeo fleet player-setup` read the profile from
`lab.profile` in `workshop.yaml`. On one host, pass it yourself:

```bash
sudo rodeo player setup --profile <profile> [--track-ref <sha>]
```

A profile without a `player.yaml` is an error: the player never borrows
another product's UIs. To point a UI at another address on a single lab, use
`--upstream <ui>=<ip>:<port>`.

