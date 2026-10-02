# Building the smlm-workshop server image

The workshop lab boots its SUSE Multi-Linux Manager server from a pre-built
image with every software channel already synced. Syncing those channels takes
hours and tens of GB, so it happens here, once per image refresh, never during
a workshop deploy. `rodeo up` never runs any of this.

**Refresh the image** monthly, or when a new SMLM minor release ships.

## What you need

- **A subscription you bring yourself:** the bake starts from SUSE's own SUSE Multi-Linux
  Manager Server **BYOS** image and registers it with your SMLM registration code.
  Get the image from your SUSE account:
  - **KVM (default):** the SMLM Server qcow2. Give its download URL and sha256: an
    authenticated or mirror `https://` URL, or `file:///path` for a local copy.
    lab-in-a-box downloads it and checks it before first use.
  - **AWS (`variant: aws`):** the SMLM Server BYOS AMI ID in your region, plus
    AWS credentials for the account the bake runs in.
- **Your organization's SCC mirroring credentials**, used to sync the channels.
- **For KVM:** a host with internet access (~30 GiB RAM, ~300 GB free disk).

`rodeo up` asks once for everything it needs: `scc_regcode`, `scc_mirror_user`,
`scc_mirror_password`, and `smlm_byos_image_url` + `smlm_byos_image_sha256`, or for
AWS `aws_access_key_id`, `aws_secret_access_key` and `smlm_byos_ami`. It stores
them in `~/.rodeo/secrets.yaml`. They are never written to a plan or to git.

## Steps

1. Deploy the bake lab from this directory. For AWS, first set `variant: aws`
   under `lab_in_a_box:` in `rodeo-plan.yaml`. The bake registers SUSE's image with
   your code, installs SMLM as `smlm.rodeo.lab`, and adds every channel the
   workshop's activation keys use:

   ```bash
   cd <lab>/bake
   rodeo up
   ```

2. Wait for the channels to sync, then generalise the VM:

   ```bash
   rodeo ssh smlm 'bash -s' < generalise.sh
   ```

   `generalise.sh` waits until no channel sync has run for 30 minutes, checks
   that every channel log reports a completed sync, and refuses to continue
   while SCC credentials are still registered in SMLM. When that happens,
   remove them (`mgrctl exec -ti -- mgr-sync delete credentials` on the VM) and
   run it again. It then removes registry logins, spacecmd configs, SSH host
   keys and the machine id, re-arms first-boot setup, and powers the VM off.

3. Export the result, which stays **private** in your account: anyone inside it
   may use it, nobody outside.

   - **KVM:** `OUT_DIR=/srv/images ./export-image.sh` writes
     `smlm-workshop-server.qcow2` and its sha256. Store it privately. Because the
     location is private, `smlm_image_url` must work without a login: a
     time-limited signed URL from that account (e.g. an S3 presigned URL, still
     valid when the deploy downloads the image), or `file:///path` after copying
     the image onto the deploying host with that account's tools.
   - **AWS:** `AWS_REGION=<region> ./export-ami.sh` creates a private AMI from the
     stopped instance and prints its ID.

4. Hand the results to whoever deploys the workshop. The workshop plan lists them
   as `operator_secrets`:

   | Secret | Value |
   |---|---|
   | `smlm_image_url` + `smlm_image_sha256` | KVM: the signed URL (or file://) and the printed sha256 |
   | `smlm_image_ami` | AWS: the AMI ID `export-ami.sh` printed (workshop `variant: aws`) |
   | `smlm_image_admin_pass` | `smlm_image_admin_pass` from this host's `~/.rodeo/secrets.yaml` |

   Each workshop deploy replaces that admin password with its own.

## Not verified live yet

- The channel-sync completion check (the `Sync completed` log marker).
- The `mgr-sync list credentials` output format that `generalise.sh` inspects.
- The `mgradm install --ssl-*` subject flags that lab-in-a-box passes.
- Registering SUSE's BYOS image (`transactional-update register` on its SL Micro
  base), and that its KVM image takes Ignition/Combustion (lab-in-a-box's default
  `config_method`) rather than cloud-init.
- That removing `/boot/writable/firstboot_happened` makes the baked qcow2 apply each
  deploy's own network settings on its first boot (needed for lab instances).

Check all three on the first real bake.
