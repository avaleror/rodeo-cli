# Building the smlm-workshop server image

The workshop lab boots its SUSE Multi-Linux Manager server from a pre-built
image with every software channel already synced. Syncing those channels takes
hours and tens of GB, so it happens here, once per image refresh, never during
a workshop deploy. `rodeo up` never runs any of this.

**Refresh the image** monthly, or when a new SMLM minor release ships.

## What you need

- **SLES 15 SP7 and your SMLM registration code:** the bake starts from SLES 15 SP7
  and registers it with your SMLM registration code: the base product, the Containers
  module and the SUSE Multi-Linux Manager Server extension.
  - **KVM (default):** the `SLES15-SP7-Minimal-VM.x86_64-Cloud-GM.qcow2` image from your
    SUSE account. Give its download URL and sha256: an authenticated or mirror `https://`
    URL, or `file:///path` for a local copy. lab-in-a-box downloads it and checks it
    before first use. SUSE publishes the sha256 next to the image (`<image>.sha256`).
  - **AWS (`variant: aws`):** a SLES 15 SP7 BYOS AMI ID in your region, AWS
    credentials for the account the bake runs in, a security group in the region's
    default VPC (an empty one will do: lab-in-a-box adds SSH from this host), and an
    Ubuntu 24.04 AMI for the small DNS VM lab-in-a-box creates next to the lab.
- **Your organization's SCC mirroring credentials**, used to sync the channels.
- **For KVM:** a host with internet access (~30 GiB RAM, ~450 GB free disk). The synced
  server uses about 215 GB, and `export-image.sh` writes a compressed copy of similar size.

`rodeo up` asks once for everything it needs: `scc_regcode`, `scc_mirror_user`,
`scc_mirror_password`, and `sles15sp7_image_url` + `sles15sp7_image_sha256`, or for
AWS `aws_access_key_id`, `aws_secret_access_key`, `sles15sp7_ami`,
`aws_security_group_id` and `ubuntu2404_ami`. It stores
them in `~/.rodeo/secrets.yaml`. They are never written to a plan or to git.

## Steps

1. Deploy the bake lab from this directory. For AWS, first set `variant: aws`
   under `lab_in_a_box:` in `rodeo-plan.yaml`. The bake registers SLES 15 SP7 with
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
   | `smlm_image_admin_pass` | `smlm_image_admin_pass` from the bake lab's `.rodeo-secrets.yaml` (next to its `rodeo-plan.yaml`) |

   Each workshop deploy replaces that admin password with its own.

## Not verified live yet

The KVM bake has run on a SLES 16 host (nested KVM): registration, the SMLM install,
adding the channels and syncing them all (about 207 GiB of VM disk) work. Still
unchecked:

- That `cloud-init clean` in `generalise.sh` makes the baked qcow2 apply each
  deploy's own network settings on its first boot (needed for lab instances).
- The `aws` variant.
