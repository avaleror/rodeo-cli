# Changelog

## [0.17.0](https://github.com/avaleror/rodeo-cli/compare/v0.16.1...v0.17.0) (2026-09-12)


### Features

* **aws:** auto-manage the security group instead of requiring an operator-supplied one ([787df16](https://github.com/avaleror/rodeo-cli/commit/787df169a12c59077a90e7b3c25b87148d083029))
* **engine:** actually run custom/scripts/ — documented since config_dir shipped, never executed ([9ae7650](https://github.com/avaleror/rodeo-cli/commit/9ae7650dbae8fc58b75a06f79dd39f3a14a35d16))
* **harvester-aws:** raise guest RAM to 24 GiB/Harvester-node, 16 GiB Rancher ([4a74a79](https://github.com/avaleror/rodeo-cli/commit/4a74a798eb5375ac31080ab4ce52c18101538588))
* new harvester-aws profile — 3-node Harvester+Rancher pre-tuned for AWS ([23683cf](https://github.com/avaleror/rodeo-cli/commit/23683cf4903ef8e0f03489c4962beedb5c6d74f8))
* new virt-workshop-aws profile — pre-lab state for suse-virt-workshop's exercises ([524ad34](https://github.com/avaleror/rodeo-cli/commit/524ad348eaedb50f95224411313ca5acfed4aacb))
* **virt-workshop-aws:** pre-create daily-batch-processor for full chapter-4 parity ([8d5e9f2](https://github.com/avaleror/rodeo-cli/commit/8d5e9f27770c5b984aa9cc9439d9b3c7c340efa2))


### Bug Fixes

* **aws:** disk floor is a flat 300GB/Harvester-node, 60GB/Rancher-node, not a pool budget ([6d5e240](https://github.com/avaleror/rodeo-cli/commit/6d5e240907ae87fe688a89a0cceaae7be073b39d))
* **aws:** harvester-2n needs 32 vCPU/128 GiB, not 8/64 — resize to m8id.8xlarge ([aed0368](https://github.com/avaleror/rodeo-cli/commit/aed0368f9d0e8fe97de75156c257529405a75bc9))
* **aws:** harvester-aws must fit m8id.8xlarge too, not m8id.12xlarge ([b2763e5](https://github.com/avaleror/rodeo-cli/commit/b2763e5bddeff31f3ea624867781b06d14605f2c))
* **aws:** managed-SG description used an em dash, which EC2's GroupDescription rejects ([96aca57](https://github.com/avaleror/rodeo-cli/commit/96aca573c7cd8044e81d114ffbeaae790873c751))
* **aws:** NVMe disk floor was per Harvester node, not per pool — 3x overshoot ([192ee1b](https://github.com/avaleror/rodeo-cli/commit/192ee1bd3ad4795a24d6fd5356bb115b8904343b))
* **virt-workshop-aws:** size webserver-prod's boot disk from image virtualSize ([7a74ece](https://github.com/avaleror/rodeo-cli/commit/7a74ece727ea6170fab7277af34e2b4f50d00166))


### Documentation

* live-validate harvester-aws on m8id.8xlarge — full success ([2e1d9a1](https://github.com/avaleror/rodeo-cli/commit/2e1d9a14a3cba6cb0ebd363fc01304b34e959949))

## [0.16.1](https://github.com/avaleror/rodeo-cli/compare/v0.16.0...v0.16.1) (2026-09-10)


### Bug Fixes

* **engine:** stale libvirt-python availability check inside a single rodeo up run ([2e365a7](https://github.com/avaleror/rodeo-cli/commit/2e365a7aec1d998c9680a213b7e29a5929567d64))


### Documentation

* **roadmap:** check off Phase E live validation ([7e0c39e](https://github.com/avaleror/rodeo-cli/commit/7e0c39e5b25565e7508c4ee0c3f07ac51b5e49bc))

## [0.16.0](https://github.com/avaleror/rodeo-cli/compare/v0.15.0...v0.16.0) (2026-09-10)


### Features

* **aws:** fail closed when a subnet can't give a reachable public IP ([8f55a9d](https://github.com/avaleror/rodeo-cli/commit/8f55a9db2523026ba323b3b117f911448a2d2cb3))
* modular engine ([#48](https://github.com/avaleror/rodeo-cli/issues/48)) ([deff147](https://github.com/avaleror/rodeo-cli/commit/deff147cb65e427eee9e6c64ca0f721317b5bfdc))
* **remote:** --ref to pin (and actually refresh) rodeo-cli on remote hosts ([ac5838e](https://github.com/avaleror/rodeo-cli/commit/ac5838ecfa4fa4d73d130309515bf2621b11d3e4))


### Bug Fixes

* **aws:** default AMI filter matched no images in any region ([05083f9](https://github.com/avaleror/rodeo-cli/commit/05083f902ec3340f6bcc3ede6f1ef38372980680))
* **aws:** default to SLES 16 PAYG — Leap + i7i could never launch ([fff85ac](https://github.com/avaleror/rodeo-cli/commit/fff85ac6630582ce9dac9bedf79d999ed46f8b70))
* **aws:** key-pair fingerprint compare ignored base64 padding ([53d20dc](https://github.com/avaleror/rodeo-cli/commit/53d20dc5267b6c23f9b44426a8ba62a2bba63a08))
* **aws:** remote deploy never applied the aws host context ([50b6253](https://github.com/avaleror/rodeo-cli/commit/50b6253a37921475e6a38b96899e22c1270ceabf))
* **aws:** the [aws] extra needs boto3[crt] for `aws login` credentials ([dd31380](https://github.com/avaleror/rodeo-cli/commit/dd31380c0374caa39fbc555e45ec9aa472112afc))
* **install:** don't abort on BASH_SOURCE when piped through bash ([2cf9b76](https://github.com/avaleror/rodeo-cli/commit/2cf9b769cd418ba3456a536b8189125da61ced81))
* **kvm_host:** NVMe pool never mounted on btrfs roots (SLES 16 / openSUSE) ([e12e8cf](https://github.com/avaleror/rodeo-cli/commit/e12e8cfe34ed3f10f90f0b1679ccf653b4b0d0d0))
* **preflight:** count the NVMe pool kvm_host is about to mount ([5f1221b](https://github.com/avaleror/rodeo-cli/commit/5f1221bbd93e749f9708499901a6e6503c1e5153))

## [0.15.0](https://github.com/avaleror/rodeo-cli/compare/v0.14.2...v0.15.0) (2026-09-09)


### Features

* **aws:** instance tiers, capacity check, NVMe host context, managed SSH ([0b9bd15](https://github.com/avaleror/rodeo-cli/commit/0b9bd150725988593299bf0d21f37166d9a3dc43))
* **cli:** add rodeo install-extensions to reconcile UI extensions post-deploy ([081ad8a](https://github.com/avaleror/rodeo-cli/commit/081ad8a6b2801cc7dde37ec2b660d77a36930b1a))
* **cli:** add rodeo set-password to rotate credentials post-deploy ([8f1ef68](https://github.com/avaleror/rodeo-cli/commit/8f1ef689e1cbbf95c69224e81b9f566cd1e12374))
* **docs:** add rodeo-cli logo (Horseshoe Prompt mark) + favicons ([e6a4c3b](https://github.com/avaleror/rodeo-cli/commit/e6a4c3b5658fb485b093fab0cbd69d3f4c90d9d3))
* **fleet:** F0/F1 — rodeo doctor/status --output json, fleet fan-out over SSH ([9d683a1](https://github.com/avaleror/rodeo-cli/commit/9d683a1840abdab61f1a16a57bb29bc7fedc269b))
* **fleet:** F2 — deploy, retry, and access sheet over OpenSSH ([f911cda](https://github.com/avaleror/rodeo-cli/commit/f911cdaa07b451525ab560a6ddd917ae0f723089))
* **fleet:** F2.1 — rodeo fleet diagnose, failure forensics at scale ([a558c94](https://github.com/avaleror/rodeo-cli/commit/a558c940f6754e1c3873a0ec3f8084b03b57c35e))
* **providers:** AWS host-acquire for Fleet F4a and single-host up --target aws ([486a56a](https://github.com/avaleror/rodeo-cli/commit/486a56a36ba927774ad9afb1d46808e7737ff128))
* **reconcile:** make VM drift reconciliation the default (B2 step 5) ([23f0513](https://github.com/avaleror/rodeo-cli/commit/23f0513e650e84a9756e9ae0e0139365e19679f9))
* **suse-edge:** install the OS Manager (Elemental) Rancher UI extension ([69b3866](https://github.com/avaleror/rodeo-cli/commit/69b38665006346967b9f9e1837d572deeef62717))


### Bug Fixes

* **aws:** IMDSv2 detect, restart exit code, accurate up finish message ([2e6a4da](https://github.com/avaleror/rodeo-cli/commit/2e6a4daa72a577dc7ec147108524ef4c727f4cb2))
* **ci:** pin ruff to 0.15.16, unpinned dep silently broke CI ([cce5e6c](https://github.com/avaleror/rodeo-cli/commit/cce5e6ca023ca216bedd700a68dfb3c766343604))
* **clean:** explicit --refresh default for newer Click ([232079d](https://github.com/avaleror/rodeo-cli/commit/232079d34bba080fcb69c973afed9ead13c93fc3))
* **docs:** give the horseshoe mark real nail holes (4 per branch) ([32628bc](https://github.com/avaleror/rodeo-cli/commit/32628bc18613b3f25a473447a1974d88eb3f67ba))
* **fleet:** scope access URLs to lab.components, add script syntax regression tests ([5abc81a](https://github.com/avaleror/rodeo-cli/commit/5abc81a0b7dbaf75f926db83573d4e4ed7fc6adc))
* **fleet:** treat apply as no_cache when checking lab complete ([44b8906](https://github.com/avaleror/rodeo-cli/commit/44b8906f7f21b9e167dfba3df44ab41a24b8ad73))
* **kvm_host:** auto-correct sudo secure_path on SUSE hosts ([9083f90](https://github.com/avaleror/rodeo-cli/commit/9083f9017c8f1e24026ac06592bc18d0113a41c7))
* **rancher:** clear first-login setting even when password already matches ([33327d6](https://github.com/avaleror/rodeo-cli/commit/33327d6d8238608906ce3f3e4e81dd2598c44cf5))
* **rancher:** reconcile UI extensions for standalone labs too ([4a456d3](https://github.com/avaleror/rodeo-cli/commit/4a456d3c170e3f7c2120a2aee21cf4c281050327))
* **rancher:** restore Harvester UI extension declaration in bundled profiles ([d90c1b1](https://github.com/avaleror/rodeo-cli/commit/d90c1b1656557865891c72596c3212531c5319e8))
* **rancher:** retry Harvester password change, set it regardless of auto-import ([8d4606b](https://github.com/avaleror/rodeo-cli/commit/8d4606bc0e7e18d00e1509b70a7c03213aecee48))
* **secrets:** remove hardcoded fallback passwords, fail loud when missing ([7cd8424](https://github.com/avaleror/rodeo-cli/commit/7cd8424b890fee3a519c376d6745991af197a02c))
* **ssh:** detect an unreadable /root on Python 3.13+, not just &lt;=3.12 ([6296a3a](https://github.com/avaleror/rodeo-cli/commit/6296a3ac7ecaf40c568e137b9bcf123326e8958b))
* **ssh:** handle PermissionError from stat(), not just unreadable files ([abf6509](https://github.com/avaleror/rodeo-cli/commit/abf650971fc5f4bc98a5c516f161a51d879e6497))
* **ssh:** stop nested-VM SSH from silently degrading to a password prompt ([5174222](https://github.com/avaleror/rodeo-cli/commit/5174222ff41b939ab912c6142661972fb4ddbc5e))


### Refactoring

* **ansible:** ansible-lint clean roles, per-node DHCP drift, dedup curl/ssh-key tasks ([8bb5de6](https://github.com/avaleror/rodeo-cli/commit/8bb5de62378c28d83e7f4325359e7d8e58edf141))


### Documentation

* add GitHub Pages site (mkdocs-material, andresvalero.tech design) ([67a97bd](https://github.com/avaleror/rodeo-cli/commit/67a97bd21822ba1978e3dd5fc9cc044c93fac75d))
* add Harvester admin password recovery when the live value is unknown ([10af0c3](https://github.com/avaleror/rodeo-cli/commit/10af0c3e1c197acd6e53f76c38d083c325f9d74d))
* clean up wording across docs and site copy ([8784d73](https://github.com/avaleror/rodeo-cli/commit/8784d736dfce69c11762bd57a9626a3bd1e13746))
* document rancher.ui_extensions and rodeo install-extensions ([e050ab9](https://github.com/avaleror/rodeo-cli/commit/e050ab9cd11431d3749cd948973331adc5555465))
* **examples:** add AWS single-host + fleet live smoke-test checklist for Claude Code ([855ee5f](https://github.com/avaleror/rodeo-cli/commit/855ee5f3ee8c2c4f3b6ddcfc247367805b13763b))
* fix layout — hero font-size bug, dead grid space, uneven card grids ([5a41b3a](https://github.com/avaleror/rodeo-cli/commit/5a41b3aaecd5badee6b7c3f86ae8dfaa2f1b286a))
* **fleet:** add Roadmap subsection for F3 MCP and F4 host-acquire ([d982d61](https://github.com/avaleror/rodeo-cli/commit/d982d615513fcec6f784059f6940fe7da6bc42da))
* **fleet:** F4 host-acquire plan — AWS then GCP then Hetzner ([8fd1ad8](https://github.com/avaleror/rodeo-cli/commit/8fd1ad88803f653b56dcbe2622f60836b9eca301))
* hygiene pass — sync versions, test counts, command reference ([02c1447](https://github.com/avaleror/rodeo-cli/commit/02c1447fd4142e079e71ba8c02ea0c449db66e0c))
* move historical audits under docs/archive/ ([1b3fd93](https://github.com/avaleror/rodeo-cli/commit/1b3fd938fa856e0e04b6037a1f2df603a51a63ef))

## [0.14.2](https://github.com/avaleror/rodeo-cli/compare/v0.14.1...v0.14.2) (2026-07-17)


### Bug Fixes

* **instruqt:** print hostimage checklist on success, correct Save-timing docs ([1eaf7f9](https://github.com/avaleror/rodeo-cli/commit/1eaf7f93be90ab579956f5346f73b2536156f12d))

## [0.14.1](https://github.com/avaleror/rodeo-cli/compare/v0.14.0...v0.14.1) (2026-07-17)


### Bug Fixes

* **instruqt:** open agent ports 15778/15779, fixing console stuck on "Please Wait" ([b4b425e](https://github.com/avaleror/rodeo-cli/commit/b4b425e433e3a723d1504f7051380a5ba78d2b5d))


### Documentation

* **instruqt:** correct stale firewalld-timing guidance ([2099ba4](https://github.com/avaleror/rodeo-cli/commit/2099ba43ce3433c8592568d3585bb6b5a5afa422))
* **instruqt:** document rodeo start-if-needed for attendee instance boot ([45663b2](https://github.com/avaleror/rodeo-cli/commit/45663b25fe159ddd78f416bc60602be7b89070c0))

## [0.14.0](https://github.com/avaleror/rodeo-cli/compare/v0.13.0...v0.14.0) (2026-07-16)


### Features

* **deploy:** opt-in --reconcile for VM memory/vCPU drift ([#38](https://github.com/avaleror/rodeo-cli/issues/38)) ([3887012](https://github.com/avaleror/rodeo-cli/commit/38870122d4c9201a6a461bede3863371df70dc04))
* **sizing:** Instruqt host-aware guest resource presets ([#43](https://github.com/avaleror/rodeo-cli/issues/43)) ([db75772](https://github.com/avaleror/rodeo-cli/commit/db757726d14ac0a7d3abef7093d6deb78e888525))
* **vms:** Instruqt-friendly guest disk cache defaults ([#42](https://github.com/avaleror/rodeo-cli/issues/42)) ([1b18387](https://github.com/avaleror/rodeo-cli/commit/1b1838702442e90e51be6c8f3c8fa7b25e0baff2))


### Bug Fixes

* **config:** fail closed on an unresolved ??key in rancher_tls.email ([49275c9](https://github.com/avaleror/rodeo-cli/commit/49275c98d99f64cef11bb2a27f263f811454495c))
* **deploy:** stop raw tool output from crashing the deploy via Rich markup ([74dca9f](https://github.com/avaleror/rodeo-cli/commit/74dca9fc29cb7ade3c79e9ed5225312740d5cd3a))
* **engine:** fail loud on inventory errors in runner and cluster ([#41](https://github.com/avaleror/rodeo-cli/issues/41)) ([e527fa6](https://github.com/avaleror/rodeo-cli/commit/e527fa64f6f72528ef1498f186bdfbce8a60d4c7))
* **harvester:** bump node disk to 320GB, fix Longhorn stability, neutral domain ([630e1a0](https://github.com/avaleror/rodeo-cli/commit/630e1a0194e9caf2a9fd1fe3beeac99fbaaaa07d))
* **rancher:** correct nonexistent elemental-register Hauler image reference ([615a955](https://github.com/avaleror/rodeo-cli/commit/615a9558e1fe925833ac825c7471118287ed4cec))
* **rancher:** download Leap Micro files via curl, add to hauler with lowercase names ([fe5672f](https://github.com/avaleror/rodeo-cli/commit/fe5672f6025247eefbb8a83e0dc1e545cca4055b))
* **rancher:** drop the redundant "git" arg in the git-in-container wrapper ([ed556fc](https://github.com/avaleror/rodeo-cli/commit/ed556fccab448e83d24c15beac7f2558a940af28))
* **rancher:** grant write:user token scope; fail loud on real repo-create errors ([0165700](https://github.com/avaleror/rodeo-cli/commit/016570081b07a1d2461cf472c382cc0ba430c7f9))
* **rancher:** make _deploy_gitea retry-safe (container name + already-exists) ([a09e29c](https://github.com/avaleror/rodeo-cli/commit/a09e29c2cc5c01a570b8215664db756e81929775))
* **rancher:** pass Helm bootstrapPassword via values file ([#40](https://github.com/avaleror/rodeo-cli/issues/40)) ([bf6f8d1](https://github.com/avaleror/rodeo-cli/commit/bf6f8d177949ba0b73031a20f1d610b06e01bb6e))
* **rancher:** run git via a container on the eib VM instead of zypper install ([efcb6f8](https://github.com/avaleror/rodeo-cli/commit/efcb6f83ce90f9b3d162572a63bc9194ddf65b03))
* **rancher:** wait for hauler-fileserver to actually listen before curling it ([de2732e](https://github.com/avaleror/rodeo-cli/commit/de2732ec8aeb73d16f94191ac860ac04064ae9a0))
* **runner:** skip diskless edge nodes in stream_boot instead of crashing ([3f19250](https://github.com/avaleror/rodeo-cli/commit/3f1925086206a545f9d110f9897b23523b99a008))
* **secrets:** add rancher_vm_password, dedupe init_cmd's own secrets writer ([c0de3f9](https://github.com/avaleror/rodeo-cli/commit/c0de3f9dbc455ab3d3f35bc25e64319e0bda0be8))
* **self-update:** force-fetch tags so a rewritten history can't strand a host ([f77d5bf](https://github.com/avaleror/rodeo-cli/commit/f77d5bf9e4337d2b20c51b41096d2b4524ba9854))
* **suse-edge:** switch edge-node base images from SLE Micro to openSUSE Leap Micro 6.2 ([757af39](https://github.com/avaleror/rodeo-cli/commit/757af396182abced37b97c70ec2ef5e510f653dc))
* **vms:** drop unfixable+unnecessary virt-customize step from eib_image.yml ([48072c3](https://github.com/avaleror/rodeo-cli/commit/48072c30c8193ce96175032c2e780ad6ca4f724b))


### Documentation

* **roadmap:** mark Instruqt builder validation complete ([#39](https://github.com/avaleror/rodeo-cli/issues/39)) ([fba7337](https://github.com/avaleror/rodeo-cli/commit/fba7337cdd56d255b4d87eb37618a0bb35ff28f8))

## [0.13.0](https://github.com/avaleror/rodeo-cli/compare/v0.12.0...v0.13.0) (2026-07-14)


### Features

* **harvester:** bump node sizing to 10 vCPU / 20 GiB memory ([8eab857](https://github.com/avaleror/rodeo-cli/commit/8eab85760e0bfe13cf8eb535c9f67e86850f24dd))
* **install-deps:** add invoking user to the libvirt group ([38b3600](https://github.com/avaleror/rodeo-cli/commit/38b36008dd274496cbacfe0436dc3248c4eb72eb))


### Bug Fixes

* audit quick wins [#6](https://github.com/avaleror/rodeo-cli/issues/6) [#8](https://github.com/avaleror/rodeo-cli/issues/8) [#9](https://github.com/avaleror/rodeo-cli/issues/9) [#10](https://github.com/avaleror/rodeo-cli/issues/10) ([d4d1f3e](https://github.com/avaleror/rodeo-cli/commit/d4d1f3e5467c49b78a93fcbd628516c0cef72847))
* centralize ~/.rodeo path resolution under sudo, fix plan flavor lookup, propagate rancher cancellation ([d099f9b](https://github.com/avaleror/rodeo-cli/commit/d099f9ba9e696a49340814d6e209a44c020b924d))
* **downloads:** use curl -4 --http1.1 for Harvester ISO + PXE artifacts ([e662131](https://github.com/avaleror/rodeo-cli/commit/e662131ecc5c58787a55e6d5d9bbd86614e95239))
* **plan:** flag drift on phases already marked done; document re-run semantics ([b3fae05](https://github.com/avaleror/rodeo-cli/commit/b3fae0500fcfdea2f96d4eee3b64cabef1d7262a))
* **preflight:** skip RAM/disk check on a vms-already-deployed re-run ([03291ba](https://github.com/avaleror/rodeo-cli/commit/03291ba440a05edf0d3c9e40dcf37cf3941f7d16))
* **privilege:** hand ~/.rodeo back to the invoking user after self-escalation ([4bf1002](https://github.com/avaleror/rodeo-cli/commit/4bf10026e88ff7fe84419735fb12e3cc6a0ea3c1))
* **vms:** guard default-network redefinition; plan Phase B2 auto-reconciliation ([de128f8](https://github.com/avaleror/rodeo-cli/commit/de128f8031643ef9d9d54b612dbe91dd0771552f))


### Documentation

* **audit:** log ownership handback follow-up on fix [#1](https://github.com/avaleror/rodeo-cli/issues/1) ([39cc7a6](https://github.com/avaleror/rodeo-cli/commit/39cc7a66512559fa5512cb58649df411424647c3))
* **custom-rodeos:** correct manifests/helm claims to match apply-phase reality ([8b4321a](https://github.com/avaleror/rodeo-cli/commit/8b4321a4700f4d5266de5e551e1bebc73e5da9f8))
* sync contributor docs for audit fix [#7](https://github.com/avaleror/rodeo-cli/issues/7) ([55b85f6](https://github.com/avaleror/rodeo-cli/commit/55b85f6dbcf00948bf64b3dc7b6acb861dfc6efc))

## [0.12.0](https://github.com/avaleror/rodeo-cli/compare/v0.11.8...v0.12.0) (2026-07-10)


### Features

* **rancher:** reconcile declarative Rancher UI extensions to pinned versions ([#34](https://github.com/avaleror/rodeo-cli/issues/34)) ([1f71e3e](https://github.com/avaleror/rodeo-cli/commit/1f71e3e1d396c391183750c409fd3b3c5e92b660))

## [0.11.8](https://github.com/avaleror/rodeo-cli/compare/v0.11.7...v0.11.8) (2026-07-10)


### Bug Fixes

* **clean:** cover OVMF vars, cloud-init ISOs, edge/eib artifacts + temp files ([#32](https://github.com/avaleror/rodeo-cli/issues/32)) ([f9e0ae8](https://github.com/avaleror/rodeo-cli/commit/f9e0ae87a5d548b0560eb68685314690852b9117))

## [0.11.7](https://github.com/avaleror/rodeo-cli/compare/v0.11.6...v0.11.7) (2026-07-09)


### Bug Fixes

* **rancher:** correct auto-import cacerts (served CA) + default auto-import OFF ([#30](https://github.com/avaleror/rodeo-cli/issues/30)) ([6325380](https://github.com/avaleror/rodeo-cli/commit/63253807f425fa784cf840a69c6d1d7b402dc30a))

## [0.11.6](https://github.com/avaleror/rodeo-cli/compare/v0.11.5...v0.11.6) (2026-07-08)


### Bug Fixes

* **vms:** balanced quotes in Leap download task; guard against split_args aborts ([#28](https://github.com/avaleror/rodeo-cli/issues/28)) ([54308c2](https://github.com/avaleror/rodeo-cli/commit/54308c2231ad1fa1db5ec87df8fe8fd9dd31831a))

## [0.11.5](https://github.com/avaleror/rodeo-cli/compare/v0.11.4...v0.11.5) (2026-07-08)


### Bug Fixes

* **apply:** run kubectl under sudo with the node kubeconfig; fix demo manifest ([#25](https://github.com/avaleror/rodeo-cli/issues/25)) ([732d923](https://github.com/avaleror/rodeo-cli/commit/732d92398d39d42bbb801d34a764fd4a684a8a1a))
* **install:** self-heal remote refspec on update, never strand a host ([#26](https://github.com/avaleror/rodeo-cli/issues/26)) ([a5c29b5](https://github.com/avaleror/rodeo-cli/commit/a5c29b53aed974ff4fe88fd2f7d9189c1b217a60))

## [0.11.4](https://github.com/avaleror/rodeo-cli/compare/v0.11.3...v0.11.4) (2026-07-08)


### Bug Fixes

* **clean:** make CLI refresh opt-in, never silently change the version ([#23](https://github.com/avaleror/rodeo-cli/issues/23)) ([d0c278b](https://github.com/avaleror/rodeo-cli/commit/d0c278bb45406ab43a3f7fd27aec26431036335a))
* **profiles:** pin Harvester 1.8.1 explicitly in the test profile ([#22](https://github.com/avaleror/rodeo-cli/issues/22)) ([65a851e](https://github.com/avaleror/rodeo-cli/commit/65a851eaa0d11687ea5d8be9de45746492ccc6cb))
* **vms:** make Leap image downloads resilient to opensuse HTTP/2 flakes ([#21](https://github.com/avaleror/rodeo-cli/issues/21)) ([805132c](https://github.com/avaleror/rodeo-cli/commit/805132cbaa1d9a2b11b95948769349115cb914d8))

## [0.11.3](https://github.com/avaleror/rodeo-cli/compare/v0.11.2...v0.11.3) (2026-07-08)


### Bug Fixes

* **self-update:** guarantee alignment to origin/main, never strand a host ([#20](https://github.com/avaleror/rodeo-cli/issues/20)) ([8082bfe](https://github.com/avaleror/rodeo-cli/commit/8082bfebceb902f49bdf7997be73ecc9dbb36d65))


### Refactoring

* derive edge topology and VM lists from the definition, not hardcoded ([#18](https://github.com/avaleror/rodeo-cli/issues/18)) ([96d59e9](https://github.com/avaleror/rodeo-cli/commit/96d59e902636fbd9bd212e8e7c4205117be63a8e))

## [0.11.2](https://github.com/avaleror/rodeo-cli/compare/v0.11.1...v0.11.2) (2026-07-08)


### Bug Fixes

* **start:** start --all discovers defined VMs, no phantom harvester3 ([#16](https://github.com/avaleror/rodeo-cli/issues/16)) ([6de915f](https://github.com/avaleror/rodeo-cli/commit/6de915fb343fa5984fad53ec0bbf2d93186abdc9))

## [0.11.1](https://github.com/avaleror/rodeo-cli/compare/v0.11.0...v0.11.1) (2026-07-07)


### Bug Fixes

* **kvm_host:** keep DNAT-accept above libvirt guest_input reject ([#11](https://github.com/avaleror/rodeo-cli/issues/11)) ([d4d0f6c](https://github.com/avaleror/rodeo-cli/commit/d4d0f6c96a90e3e7f29c175fed975c3f5506ab47))
* **kvm_host:** re-assert DNAT-accept after libvirt settles in finalise ([#13](https://github.com/avaleror/rodeo-cli/issues/13)) ([3854fc5](https://github.com/avaleror/rodeo-cli/commit/3854fc51bd9baa859b4d3e6019e7580b31bcd0b7))

## [0.11.0](https://github.com/avaleror/rodeo-cli/compare/v0.10.6...v0.11.0) (2026-07-07)


### Features

* default Harvester rodeos to v1.8.1 ([#9](https://github.com/avaleror/rodeo-cli/issues/9)) ([021b298](https://github.com/avaleror/rodeo-cli/commit/021b298922bb32128bb25acd976e9b42b0cf3371))

## [0.10.6](https://github.com/avaleror/rodeo-cli/compare/v0.10.5...v0.10.6) (2026-07-06)


### Build & Release

* automate releases with release-please; drop manual version bumping ([#6](https://github.com/avaleror/rodeo-cli/issues/6)) ([041aa40](https://github.com/avaleror/rodeo-cli/commit/041aa40761d464ec4e552cbe28fcceff351aaa3e))
