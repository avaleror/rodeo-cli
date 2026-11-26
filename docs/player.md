# Lab player (Instruqt-like)

The rodeo player gives each student one browser window for their lab: the
chapter text on the left, then tabs for a terminal, Harvester and Rancher, plus
**Check**, **Next** and **Skip**. It runs the SUSE Virtualization Rodeo track
(`suse-virt-rodeo`) on your own cloud labs, with the same setup, check and solve
scripts the Instruqt track uses.

It is opt-in. Nothing in rodeo runs it unless you call `rodeo player` or
`rodeo fleet player-setup`, and the `virt-rodeo` profile is the only profile
that uses it today.

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

## One lab, test mode

For a quick test on one lab you do not need a domain. Names are
`<ip-dashed>.sslip.io` and Caddy uses its own CA, so the tester trusts that CA
in their browser.

```bash
sudo rodeo player setup --track-ref <40-char suse-virt-rodeo commit>
sudo rodeo player link        # prints the lab link
sudo rodeo player test        # solves every chapter: run it last
```

sslip.io is on the Public Suffix List, so this mode is for testers only, never
for students.

## A fleet with your own domain

For students, use a DNS zone you own in Route 53 (not on the Public Suffix
List) and one wildcard certificate per event. Add a `player:` block to
`workshop.yaml`:

```yaml
player:
  domain: rodeocli.com      # a public Route 53 hosted zone in the fleet's AWS account
  acme_email: you@example.com   # optional, for Let's Encrypt expiry mail
  # staging: true           # first try: Let's Encrypt staging (untrusted, no rate limit)
provider:
  per_host_ssh_key: true    # one root key per lab: strongly recommended with students
portal:
  enabled: true
  player: true              # the portal hands out each lab's link
```

Each lab gets a random 8-character id and three names: `<id>`, `hv-<id>` and
`rancher-<id>` under the zone.

Order of a fleet run:

```bash
rodeo fleet provision   -f workshop.yaml
rodeo fleet deploy      -f workshop.yaml
rodeo fleet lab-images  -f workshop.yaml --bucket <private image bucket> --run
rodeo fleet dns         -f workshop.yaml   # ids + A records
rodeo fleet cert        -f workshop.yaml   # *.<zone>, once per event
rodeo fleet player-setup -f workshop.yaml --track-ref <sha>
rodeo fleet open-access -f workshop.yaml   # opens 443 (with portal.player)
rodeo fleet portal publish -f workshop.yaml
```

`rodeo fleet destroy` also removes the lab names from the zone.

`lab-images` signs S3 links on your laptop, and a link dies with the
credentials that signed it. `aws login` credentials live about 15 minutes, so
fetch the images right after the deploy with `--run`. Without `--run`, the
command refuses when your credentials end within the hour.

### DNS records

Route 53 cannot tag a single record, so every name rodeo writes also gets a
TXT record of the same name:

```text
"managed-by=rodeo fleet=<workshop name> host=<host id>"
```

That TXT is how rodeo knows a name is its own:

- `fleet dns` refuses a name that already holds anything rodeo did not mark
  for that lab, and changes nothing for that lab.
- `fleet dns --delete` and `fleet destroy` delete only names marked for this
  workshop. Records you made by hand are never touched.
- Re-runs are safe. A lab with a new address gets its records moved.

Your AWS identity needs `route53:ListHostedZonesByName`,
`ListResourceRecordSets`, `ChangeResourceRecordSets` and `GetChange` on the zone.

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
