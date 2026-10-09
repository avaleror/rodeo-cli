# Changelog

## [0.25.0](https://github.com/rmahique/rodeo-cli/compare/v0.24.0...v0.25.0) (2026-10-09)


### Features

* add 'rodeo start-if-needed' idempotent boot guard ([1f7b4f3](https://github.com/rmahique/rodeo-cli/commit/1f7b4f3c5a43b48b6b5fe7617e99c7604c101f22))
* add global timer to deploy panel + per-VM timers to serial log windows ([1d72d06](https://github.com/rmahique/rodeo-cli/commit/1d72d068414a0c9bf4199e57c8ac83a115d450f7))
* add self-update command; auto-refresh CLI after rodeo clean ([4262ac9](https://github.com/rmahique/rodeo-cli/commit/4262ac902ce10deec6c04aeb07b004ecd3646ab0))
* **aws:** auto-manage the security group instead of requiring an operator-supplied one ([787df16](https://github.com/rmahique/rodeo-cli/commit/787df169a12c59077a90e7b3c25b87148d083029))
* **aws:** dead-man switch on every cloud host rodeo launches (6 h default) ([#83](https://github.com/rmahique/rodeo-cli/issues/83)) ([eb32709](https://github.com/rmahique/rodeo-cli/commit/eb3270955c0435a1f9372a355f6d9cf77470cf10))
* **aws:** fail closed when a subnet can't give a reachable public IP ([8f55a9d](https://github.com/rmahique/rodeo-cli/commit/8f55a9db2523026ba323b3b117f911448a2d2cb3))
* **aws:** install the AWS CLI for AWS deploys and require working AWS credentials ([d11f5eb](https://github.com/rmahique/rodeo-cli/commit/d11f5eb4a5edad85c11ee4b00dabc261744bce38))
* **aws:** instance tiers, capacity check, NVMe host context, managed SSH ([0b9bd15](https://github.com/rmahique/rodeo-cli/commit/0b9bd150725988593299bf0d21f37166d9a3dc43))
* beginner on-ramp — rodeo up/doctor, rancher profile, declarative custom rodeos ([43142ad](https://github.com/rmahique/rodeo-cli/commit/43142ad6b94923184ac3ef3c8341814480e4f408))
* builder server ([dbeae54](https://github.com/rmahique/rodeo-cli/commit/dbeae543db75bc74ed9320ee2b4f73e9e69f15fe))
* **builder:** live rodeo builder with save to profiles, rodeo new --from-zip, lab-in-a-box catalogue kinds and errors ([bc2f483](https://github.com/rmahique/rodeo-cli/commit/bc2f483003733f4d873cbc8c9e22cc49f71d011b))
* **builder:** missing_addon marks a chapter as work needed ([4da73f6](https://github.com/rmahique/rodeo-cli/commit/4da73f6a95704927b3c49439eb2600ea11c06b37))
* **builder:** static Rodeo Builder web UI, published with the docs ([3fa9418](https://github.com/rmahique/rodeo-cli/commit/3fa9418d765afba05af13f863716c919006cf915))
* bump Harvester 1.8.1 -&gt; 1.8.2, Rancher 2.14.1 -&gt; 2.14.5 (harvester-family only) ([e5ff956](https://github.com/rmahique/rodeo-cli/commit/e5ff95687db65ba04b2b81e172fcd90818d267f4))
* bundled 'harvester-ha' profile (3-node Harvester, no Rancher) ([8129f0c](https://github.com/rmahique/rodeo-cli/commit/8129f0c8b6ad3775f7b78f114b739f17bd05badd))
* **clean:** add --all --force-network --secrets for full 'reset the host' ([3226cf2](https://github.com/rmahique/rodeo-cli/commit/3226cf22e0edbbb4ddaa306345f218721c18b42c))
* **cli:** add rodeo install-extensions to reconcile UI extensions post-deploy ([081ad8a](https://github.com/rmahique/rodeo-cli/commit/081ad8a6b2801cc7dde37ec2b660d77a36930b1a))
* **cli:** add rodeo set-password to rotate credentials post-deploy ([8f1ef68](https://github.com/rmahique/rodeo-cli/commit/8f1ef689e1cbbf95c69224e81b9f566cd1e12374))
* close out audit backlog — v0.3.0 ([afd9b9a](https://github.com/rmahique/rodeo-cli/commit/afd9b9ab750612162c8b281b942db6b6d265419f))
* **cluster:** topology-driven ClusterPhase + conditional rancher phase (Phase C) ([a0e435e](https://github.com/rmahique/rodeo-cli/commit/a0e435e028f01d3ab48cfaeef5fbbdfd11f6de95))
* **cluster:** VM state snapshots and heartbeat file during long waits ([3fdebee](https://github.com/rmahique/rodeo-cli/commit/3fdebeef0ee9c3a3618e13aee4a460dbc5d0a6bf))
* default Harvester rodeos to v1.8.1 ([#9](https://github.com/rmahique/rodeo-cli/issues/9)) ([8241d87](https://github.com/rmahique/rodeo-cli/commit/8241d8732b304d78c3ec71eaa4790b25c897074d))
* **deploy:** opt-in --reconcile for VM memory/vCPU drift ([#38](https://github.com/rmahique/rodeo-cli/issues/38)) ([3887012](https://github.com/rmahique/rodeo-cli/commit/38870122d4c9201a6a461bede3863371df70dc04))
* **docs:** add rodeo-cli logo (Horseshoe Prompt mark) + favicons ([e6a4c3b](https://github.com/rmahique/rodeo-cli/commit/e6a4c3b5658fb485b093fab0cbd69d3f4c90d9d3))
* drop Harvester import from automation; make it a lab exercise ([f447c1c](https://github.com/rmahique/rodeo-cli/commit/f447c1c54aee96560841a973ba5db0b49f9ddff9))
* **elemental:** add extension repos + guard UI steps to suse-edge only ([235e041](https://github.com/rmahique/rodeo-cli/commit/235e04139cf854830f975fad67125fa0aa09d95c))
* **elemental:** install UI extension and create MachineRegistration endpoints ([30ae629](https://github.com/rmahique/rodeo-cli/commit/30ae629b91c17085d6f3f22a68927d5126e38547))
* **engine:** actually run custom/scripts/ — documented since config_dir shipped, never executed ([9ae7650](https://github.com/rmahique/rodeo-cli/commit/9ae7650dbae8fc58b75a06f79dd39f3a14a35d16))
* **engine:** Sprint 3 - ClusterPhase replaces start-vms.sh ([125eac5](https://github.com/rmahique/rodeo-cli/commit/125eac515d92a93de5bff5d0684237e791fc3e3a))
* Fix/labinabox common sizing ([a6720e2](https://github.com/rmahique/rodeo-cli/commit/a6720e2f69f0a224a3bf62886e5db5b456c8d38d))
* **fleet:** F0/F1 — rodeo doctor/status --output json, fleet fan-out over SSH ([9d683a1](https://github.com/rmahique/rodeo-cli/commit/9d683a1840abdab61f1a16a57bb29bc7fedc269b))
* **fleet:** F2 — deploy, retry, and access sheet over OpenSSH ([f911cda](https://github.com/rmahique/rodeo-cli/commit/f911cdaa07b451525ab560a6ddd917ae0f723089))
* **fleet:** F2.1 — rodeo fleet diagnose, failure forensics at scale ([a558c94](https://github.com/rmahique/rodeo-cli/commit/a558c940f6754e1c3873a0ec3f8084b03b57c35e))
* **fleet:** remove local leftovers of terminated cloud hosts ([#88](https://github.com/rmahique/rodeo-cli/issues/88)) ([832fe25](https://github.com/rmahique/rodeo-cli/commit/832fe25e824a46a297937df9b065c285e54d8fc7))
* **fleet:** scale down unclaimed labs safely; audit fixes ([#90](https://github.com/rmahique/rodeo-cli/issues/90)) ([fbc5c30](https://github.com/rmahique/rodeo-cli/commit/fbc5c30b242d506a3d7386db50ec0d5099a2ae6e))
* **fleet:** student claim portal (F5) ([#53](https://github.com/rmahique/rodeo-cli/issues/53)) ([e50ff2b](https://github.com/rmahique/rodeo-cli/commit/e50ff2ba8d43afdafaa00141c9c4b25a67e6d704))
* **generate:** add interactive  command (new generate_cmd.py with rich prompts, template customize from harvester-lab-config base, hybrid basic/advanced, full config-dir output, validation, next steps). Wire in cli. Update docs (user-guide install/first-time/commands, README quickstart/table, CONTEXT). Per user request for definition generation from questions. ([fa63b47](https://github.com/rmahique/rodeo-cli/commit/fa63b47afdea731c4c6c2f3c1975b934d6fc4c06))
* **harvester-aws:** raise guest RAM to 24 GiB/Harvester-node, 16 GiB Rancher ([4a74a79](https://github.com/rmahique/rodeo-cli/commit/4a74a798eb5375ac31080ab4ce52c18101538588))
* **harvester:** bump node sizing to 10 vCPU / 20 GiB memory ([0e2af0d](https://github.com/rmahique/rodeo-cli/commit/0e2af0d8f213509924bf713755ab60e13003ec31))
* **harvester:** declarative definition + inventory renderer (EIB-inspired); storage multi-disk; MAC/hostname gen on fly; P0/P1 fixes + docs/tests ([ca1d5c9](https://github.com/rmahique/rodeo-cli/commit/ca1d5c9dd6f2d6705ab2de98b2b1da5f6b607f1c))
* **harvester:** Phase 1 EIB plan — host_prep section in definition + full wiring (inventory, runner, comments) ([5ce5dbb](https://github.com/rmahique/rodeo-cli/commit/5ce5dbb81dabd1fb9b8ef1035f9e4f77a6240439))
* **i18n:** read-only lab-content string lookup via optional multilang ([f20cf47](https://github.com/rmahique/rodeo-cli/commit/f20cf47f8ef48262e225e47f44c699bec9937bcd))
* incorporate lab-in-a-box as an engine, changes to make it easier to test betas,etc.. ([c12188d](https://github.com/rmahique/rodeo-cli/commit/c12188d7644f2a086535940e18655f729d3ec805))
* **init:** add --profile (test/harvester) as primary way to seed labs; map test-&gt;harvester-lab-config (2 small nodes), harvester-&gt;full 3+1; always include pxe-server auxiliary; update bootstrap default and docs ([eef239c](https://github.com/rmahique/rodeo-cli/commit/eef239c51ac634fcf7f47c7b4e7a8add6bd20976))
* **init:** auto-generate rodeo-secrets.env on init; rewrite plan.yaml to use ??env: forms for easy sudo -E flows. Update example config dir with definition and plan samples. This allows starting fresh without manual secret copying. ([778a962](https://github.com/rmahique/rodeo-cli/commit/778a9627be2df68faab233b29e4d47bf05a8caff))
* initial rodeo-cli v0.1 ([fc66b7c](https://github.com/rmahique/rodeo-cli/commit/fc66b7c709a1b905497eaf610a7d8faf783bddf5))
* **install-deps:** add invoking user to the libvirt group ([eeeec30](https://github.com/rmahique/rodeo-cli/commit/eeeec303499ea1e203823a910a32d9830449af39))
* **install:** add install.sh — one-liner that hides the venv completely ([ad720a3](https://github.com/rmahique/rodeo-cli/commit/ad720a3fbe79d00fa6a6fc38cfc0a79d78b581ee))
* **install:** control-plane install on macOS and any Linux; rodeo refuses local labs it cannot host ([#84](https://github.com/rmahique/rodeo-cli/issues/84)) ([6de7780](https://github.com/rmahique/rodeo-cli/commit/6de778004fbdd5e191e156ce81a49734fd04302a))
* **kvm_host:** install Cockpit + cockpit-machines for graphical VM management ([89482d8](https://github.com/rmahique/rodeo-cli/commit/89482d86d03117f18c4a44890bcf43224c7af4cd))
* **lab-in-a-box:** deploy the latest release by default ([752a477](https://github.com/rmahique/rodeo-cli/commit/752a4771803c55ad86c051f7044eda170ef8743c))
* **lab-in-a-box:** deploy the latest release by default ([1e4c8e7](https://github.com/rmahique/rodeo-cli/commit/1e4c8e78064b881701fe56d521ab69de7c5b1830))
* **lab-in-a-box:** lab-in-a-box platform and smlm-workshop profile ([5143f9a](https://github.com/rmahique/rodeo-cli/commit/5143f9aa155db86a88f80a8ba226a59d1f04c1d0))
* modular engine ([#48](https://github.com/rmahique/rodeo-cli/issues/48)) ([deff147](https://github.com/rmahique/rodeo-cli/commit/deff147cb65e427eee9e6c64ca0f721317b5bfdc))
* new harvester-aws profile — 3-node Harvester+Rancher pre-tuned for AWS ([23683cf](https://github.com/rmahique/rodeo-cli/commit/23683cf4903ef8e0f03489c4962beedb5c6d74f8))
* new virt-workshop-aws profile — pre-lab state for suse-virt-workshop's exercises ([524ad34](https://github.com/rmahique/rodeo-cli/commit/524ad348eaedb50f95224411313ca5acfed4aacb))
* Option A — AWS is a target, not a topology; fix suse-edge TLS regression ([4811e34](https://github.com/rmahique/rodeo-cli/commit/4811e34c74586889deb04b635457571e43e49cf2))
* Phase 2 — --config-dir support (EIB config dir model) ([525066b](https://github.com/rmahique/rodeo-cli/commit/525066b9dfa50169035091484f58f0c426a821c0))
* Phase A — Terraform-style plan UX ([1debe1b](https://github.com/rmahique/rodeo-cli/commit/1debe1beb2f6bcf4ee836e1a31dbac21921dc1db))
* **profile:** add bundled 'harvester-ha' — 3-node Harvester, no Rancher ([cedc03e](https://github.com/rmahique/rodeo-cli/commit/cedc03e82890c3f19d03e3de22387188d3ee242b))
* **profiles:** add harvester-2n profile (2-node Harvester + Rancher Prime) ([3feb02c](https://github.com/rmahique/rodeo-cli/commit/3feb02c0821a36e7f31b360df46005b21add2cd3))
* **profiles:** add suse-edge to bundled profile registry in labseed.py ([30e7ca2](https://github.com/rmahique/rodeo-cli/commit/30e7ca291ee731be9e17fe276dfee1859d3f555e))
* **profiles:** declarative custom rodeos — 'rodeo new' + 'rodeo up --profile &lt;name&gt;' ([0ce252b](https://github.com/rmahique/rodeo-cli/commit/0ce252b25725a836824edd017334be5adde1ef11))
* **providers:** AWS host-acquire for Fleet F4a and single-host up --target aws ([486a56a](https://github.com/rmahique/rodeo-cli/commit/486a56a36ba927774ad9afb1d46808e7737ff128))
* rancher profiles with downstream K3s/RKE2 clusters + AWS without a provider block ([#81](https://github.com/rmahique/rodeo-cli/issues/81)) ([d3ee03d](https://github.com/rmahique/rodeo-cli/commit/d3ee03d23f2b700d794cd603968ff6724ea48463))
* **rancher:** add harvester_auto_import flag; disable for harvester rodeo ([9d0f521](https://github.com/rmahique/rodeo-cli/commit/9d0f521ce58fd3e64a1a37a371eb406b6b8ab281))
* **rancher:** add laptop-sized 'rancher' profile (Rancher Prime on K3s, no Harvester) ([36fc024](https://github.com/rmahique/rodeo-cli/commit/36fc024ff4dcbf81240eb94dfe31263075f80c9c))
* **rancher:** install Harvester UI Extension (v1.7.1) during rancher phase ([770e092](https://github.com/rmahique/rodeo-cli/commit/770e092ad660d557ed9677e41c7070553f6d65b1))
* **rancher:** port setup-rancher.sh to Python, retire all bash scripts ([cc38e2f](https://github.com/rmahique/rodeo-cli/commit/cc38e2f8859e59e862c9fe36548be430fc4f8bc8))
* **rancher:** reconcile declarative Rancher UI extensions to pinned versions ([#34](https://github.com/rmahique/rodeo-cli/issues/34)) ([5b9ccef](https://github.com/rmahique/rodeo-cli/commit/5b9cceffaefa62fc2bae25bb5be4201b9587ce60))
* **reconcile:** make VM drift reconciliation the default (B2 step 5) ([23f0513](https://github.com/rmahique/rodeo-cli/commit/23f0513e650e84a9756e9ae0e0139365e19679f9))
* reduce first-phase manual config on clean SLES ([74803f1](https://github.com/rmahique/rodeo-cli/commit/74803f1a8823e4689a49d54bca281836dac9f010))
* **remote:** --ref to pin (and actually refresh) rodeo-cli on remote hosts ([ac5838e](https://github.com/rmahique/rodeo-cli/commit/ac5838ecfa4fa4d73d130309515bf2621b11d3e4))
* **scripts:** add build-instruqt-image.sh — unattended Instruqt image builder ([26960ee](https://github.com/rmahique/rodeo-cli/commit/26960ee857810e8df083ae0968da3f8abb97d6fb))
* **secrets:** password sources + secret resolvers + ansible no_log ([b1d15ec](https://github.com/rmahique/rodeo-cli/commit/b1d15ec3fb658ff078d5765f0b9bb09000e15066))
* show credentials in TUI box and export env vars on deploy complete ([59b3a3c](https://github.com/rmahique/rodeo-cli/commit/59b3a3cc30a4f42827a84de1b0e28bd2edfe0d2f))
* **sizing:** Instruqt host-aware guest resource presets ([#43](https://github.com/rmahique/rodeo-cli/issues/43)) ([db75772](https://github.com/rmahique/rodeo-cli/commit/db757726d14ac0a7d3abef7093d6deb78e888525))
* Sprint 4 - per-plan state, preflight check, VM inventory from config, watch TTY guard ([ebc103f](https://github.com/rmahique/rodeo-cli/commit/ebc103fd8490f83145c30098c1502a65a496112b))
* **stop/start:** add 'rodeo stop' and 'rodeo start' for graceful infra-aware lab stop/restart (per definition: infra_type, components, start_order; simple ACPI VM shutdown + host services; wait on start). Update clean to run stop first (unless --hard). Add infra_type to definition templates in examples and profile. Update user-guide with new section, examples, integration. New files stop_cmd.py/start_cmd.py with docs. Prepares for clean stop before host reset. ([7995135](https://github.com/rmahique/rodeo-cli/commit/79951358ad60c5aa82b02bd0fbba111e86f0bebf))
* suse-edge-aws profile — AWS-tuned SUSE Edge 3.6 (Rancher+EIB+4 edge nodes) ([26afd56](https://github.com/rmahique/rodeo-cli/commit/26afd565a513e294e4c42d47484520dd9a51a0f7))
* **suse-edge:** add edge4 node to definition (MAC 02:00:00:0E:62:A4, IP 192.168.122.34) ([f536af2](https://github.com/rmahique/rodeo-cli/commit/f536af20fe552768238580b035f82583c793e5bc))
* **suse-edge:** add SUSE Edge 3.6 profile — Rancher Prime + Elemental + EIB + 3 edge nodes ([6aac6a9](https://github.com/rmahique/rodeo-cli/commit/6aac6a91e7f0dafba570c959bd700a90667721ee))
* **suse-edge:** airgap Hauler registry, edge MAC/IP visibility, Alien-Geeko Fleet demo ([28c6ee9](https://github.com/rmahique/rodeo-cli/commit/28c6ee9fd4d8438b8fe8550f0a552628061d9325))
* **suse-edge:** deterministic edgeN MAC/IP ordering + preflight before every deploy ([0cb6464](https://github.com/rmahique/rodeo-cli/commit/0cb64649fc7135a190d801e90bfd9a20477ca0b5))
* **suse-edge:** EIB VM infrastructure + Elemental Operator install phase ([c047e28](https://github.com/rmahique/rodeo-cli/commit/c047e282e64367d61c8ef3de4a2b1d82a031cab3))
* **suse-edge:** install the OS Manager (Elemental) Rancher UI extension ([69b3866](https://github.com/rmahique/rodeo-cli/commit/69b38665006346967b9f9e1837d572deeef62717))
* **suse-edge:** Leap Micro 6.2 EIB VM, Hauler airgap store, edge thin-clone workflow ([683986a](https://github.com/rmahique/rodeo-cli/commit/683986a3d3cdafb883d24905b2a55fa46fec89ea))
* **suse-edge:** local image seeding for edge nodes (raw/qcow2/iso) + eject-iso ([0be9e23](https://github.com/rmahique/rodeo-cli/commit/0be9e230fce5e921b67a77a725f0b9c5b45785e3))
* **suse-edge:** merge SUSE Edge 3.6 profile — fully validated on bare metal ([8aea9f3](https://github.com/rmahique/rodeo-cli/commit/8aea9f3416469ad509e4a79cf9ccb01600510923))
* **suse-edge:** sslip.io + Let's Encrypt for Rancher URL and certs ([6d199c0](https://github.com/rmahique/rodeo-cli/commit/6d199c0588a50f8b59c2f40d437796a67e4f5835))
* **suse-edge:** upgrade to SUSE Edge 3.7 ([#54](https://github.com/rmahique/rodeo-cli/issues/54)) ([06f0202](https://github.com/rmahique/rodeo-cli/commit/06f0202eac0f689e60d78eb1ec35df15d02bd7c0))
* tmux session wrap in rodeo up; remove GitHub Actions integration workflow ([9ea181a](https://github.com/rmahique/rodeo-cli/commit/9ea181ad37c055354eea23b3d0f9a87876008f54))
* **tui:** replace tabbed VM logs with split view showing all nodes ([0dd31ae](https://github.com/rmahique/rodeo-cli/commit/0dd31ae7716d0527d4976c93fd954073c4542d1e))
* **up:** add 'rodeo up' one-command on-ramp + 'rodeo doctor' ([76369f9](https://github.com/rmahique/rodeo-cli/commit/76369f98c93a8e85052326fff771df71f1677efe))
* v0.2 — Textual TUI, bundled Ansible, corrected pipeline ([2bec0c3](https://github.com/rmahique/rodeo-cli/commit/2bec0c32776aeb4d471652a37321f15661449bdf))
* **versions:** wire definition.yaml as version source of truth for suse-virt ([d6e6d6d](https://github.com/rmahique/rodeo-cli/commit/d6e6d6d3f89691c54d129aaab10377a5a5e44b4a))
* virt-workshop-aws-2n — genuine budget tier, 2-node Harvester on m8id.4xlarge ([810546f](https://github.com/rmahique/rodeo-cli/commit/810546fa9c1ee559d73300f7358eb2285d1a08d2))
* **virt-workshop-aws:** pre-create daily-batch-processor for full chapter-4 parity ([8d5e9f2](https://github.com/rmahique/rodeo-cli/commit/8d5e9f27770c5b984aa9cc9439d9b3c7c340efa2))
* **vms:** Instruqt-friendly guest disk cache defaults ([#42](https://github.com/rmahique/rodeo-cli/issues/42)) ([1b18387](https://github.com/rmahique/rodeo-cli/commit/1b1838702442e90e51be6c8f3c8fa7b25e0baff2))
* wire deployment_target through rodeo up, firewall, and success screen ([a8271be](https://github.com/rmahique/rodeo-cli/commit/a8271bee14ba9ccd35003fbf4793607f13139a3c))


### Bug Fixes

* add missing from ._options import config_options to watch.py (caused NameError on import when running any rodeo command after extending config-dir support). ([b679284](https://github.com/rmahique/rodeo-cli/commit/b67928420166f04e39883176c584d0faff0de567))
* add mustChangePassword: false to Rancher password change API call ([67e8bf1](https://github.com/rmahique/rodeo-cli/commit/67e8bf1f5649b06c96ce3439940f079b12973c4c))
* address remaining code review findings ([afd1ee6](https://github.com/rmahique/rodeo-cli/commit/afd1ee6cd5d1bc6dfcd25b1d9df9bfc7df9edaaf))
* always reposition ct status dnat accept before reject in guest_input ([9921838](https://github.com/rmahique/rodeo-cli/commit/992183801476c27f499094002e72b30c9d76bcfd))
* always use VIP for Harvester UI and import kubeconfig ([6ff1294](https://github.com/rmahique/rodeo-cli/commit/6ff12942001054d057bfddae7a26047e74041feb))
* **ansible:** SELinux permissive + dual serial ports for virsh console ([1b56711](https://github.com/rmahique/rodeo-cli/commit/1b567119c228a986cbff26eadfd9e175e74c0480))
* **ansible:** use curl -4 for Leap 16 image downloads to avoid IPv6 routing failure ([58f5c71](https://github.com/rmahique/rodeo-cli/commit/58f5c717deec011e8140d99081c72a718eeb346c))
* **app:** handle truncated UTF-8 in serial log tailer ([bc5ce56](https://github.com/rmahique/rodeo-cli/commit/bc5ce56adcac32584d46e2c58663098abf229fcc))
* **apply:** run kubectl under sudo with the node kubeconfig; fix demo manifest ([#25](https://github.com/rmahique/rodeo-cli/issues/25)) ([0e0d415](https://github.com/rmahique/rodeo-cli/commit/0e0d41512b88d6cbf819644b7e149f97f38cf017))
* audit quick wins [#6](https://github.com/rmahique/rodeo-cli/issues/6) [#8](https://github.com/rmahique/rodeo-cli/issues/8) [#9](https://github.com/rmahique/rodeo-cli/issues/9) [#10](https://github.com/rmahique/rodeo-cli/issues/10) ([734e10b](https://github.com/rmahique/rodeo-cli/commit/734e10bb2ed86bac1e5acf353bd07259a4962ea0))
* **aws:** default AMI filter matched no images in any region ([05083f9](https://github.com/rmahique/rodeo-cli/commit/05083f902ec3340f6bcc3ede6f1ef38372980680))
* **aws:** default to SLES 16 PAYG — Leap + i7i could never launch ([fff85ac](https://github.com/rmahique/rodeo-cli/commit/fff85ac6630582ce9dac9bedf79d999ed46f8b70))
* **aws:** disk floor is a flat 300GB/Harvester-node, 60GB/Rancher-node, not a pool budget ([6d5e240](https://github.com/rmahique/rodeo-cli/commit/6d5e240907ae87fe688a89a0cceaae7be073b39d))
* **aws:** harvester-2n needs 32 vCPU/128 GiB, not 8/64 — resize to m8id.8xlarge ([aed0368](https://github.com/rmahique/rodeo-cli/commit/aed0368f9d0e8fe97de75156c257529405a75bc9))
* **aws:** harvester-aws must fit m8id.8xlarge too, not m8id.12xlarge ([b2763e5](https://github.com/rmahique/rodeo-cli/commit/b2763e5bddeff31f3ea624867781b06d14605f2c))
* **aws:** IMDSv2 detect, restart exit code, accurate up finish message ([2e6a4da](https://github.com/rmahique/rodeo-cli/commit/2e6a4daa72a577dc7ec147108524ef4c727f4cb2))
* **aws:** key-pair fingerprint compare ignored base64 padding ([53d20dc](https://github.com/rmahique/rodeo-cli/commit/53d20dc5267b6c23f9b44426a8ba62a2bba63a08))
* **aws:** managed-SG description used an em dash, which EC2's GroupDescription rejects ([96aca57](https://github.com/rmahique/rodeo-cli/commit/96aca573c7cd8044e81d114ffbeaae790873c751))
* **aws:** NVMe disk floor was per Harvester node, not per pool — 3x overshoot ([192ee1b](https://github.com/rmahique/rodeo-cli/commit/192ee1bd3ad4795a24d6fd5356bb115b8904343b))
* **aws:** remote deploy never applied the aws host context ([50b6253](https://github.com/rmahique/rodeo-cli/commit/50b6253a37921475e6a38b96899e22c1270ceabf))
* **aws:** the [aws] extra needs boto3[crt] for `aws login` credentials ([dd31380](https://github.com/rmahique/rodeo-cli/commit/dd31380c0374caa39fbc555e45ec9aa472112afc))
* **builder:** list every workshop's chapters, mark the lab engine, ed… ([de08940](https://github.com/rmahique/rodeo-cli/commit/de089400a15974bd999ed0f1b3d5c828324516bc))
* **builder:** list every workshop's chapters, mark the lab engine, edit plan.yaml ([d55a22f](https://github.com/rmahique/rodeo-cli/commit/d55a22f6e94f4e9383a993d9bf9bad394f956008))
* **catalog:** drop the virt-workshop-aws budget tier ([2339d96](https://github.com/rmahique/rodeo-cli/commit/2339d96cc3374f11dbde87750913d19d10fb01c7))
* centralize ~/.rodeo path resolution under sudo, fix plan flavor lookup, propagate rancher cancellation ([f2d7121](https://github.com/rmahique/rodeo-cli/commit/f2d7121d6e601ef20f7d9382360acfdc96de9e2e))
* **ci:** pin ruff to 0.15.16, unpinned dep silently broke CI ([cce5e6c](https://github.com/rmahique/rodeo-cli/commit/cce5e6ca023ca216bedd700a68dfb3c766343604))
* **clean:** cover OVMF vars, cloud-init ISOs, edge/eib artifacts + temp files ([#32](https://github.com/rmahique/rodeo-cli/issues/32)) ([4a2ef38](https://github.com/rmahique/rodeo-cli/commit/4a2ef384ccf58d0fd3d0941525baf9efa2fff817))
* **clean:** explicit --refresh default for newer Click ([232079d](https://github.com/rmahique/rodeo-cli/commit/232079d34bba080fcb69c973afed9ead13c93fc3))
* **clean:** make CLI refresh opt-in, never silently change the version ([#23](https://github.com/rmahique/rodeo-cli/issues/23)) ([0b0a17e](https://github.com/rmahique/rodeo-cli/commit/0b0a17ee81bc5d6519284cb59323c573db81e355))
* **cli:** self-escalate deploy; remove source+sudo-E ritual from init ([40cdb4a](https://github.com/rmahique/rodeo-cli/commit/40cdb4a57186a7e078d2ce54e98c69346b88cda8))
* **cli:** self-escalate to root in clean/stop/start on SLES (sudo secure_path bypass) ([a1cfcd7](https://github.com/rmahique/rodeo-cli/commit/a1cfcd70bb3a0f5a0ae5281efaa204f9bc3410e3))
* cluster-registration-url import fails against self-signed Rancher on AWS ([334e986](https://github.com/rmahique/rodeo-cli/commit/334e986b7bc1301bae7294ab76e572ec89713e80))
* **cluster:** cap background rancher drain at 20 min after nodes Ready ([4c85f63](https://github.com/rmahique/rodeo-cli/commit/4c85f63ad8c2f229d8eb20fe7d00bc2b7b5162d5))
* **config:** fail closed on an unresolved ??key in rancher_tls.email ([49275c9](https://github.com/rmahique/rodeo-cli/commit/49275c98d99f64cef11bb2a27f263f811454495c))
* **config:** remember last lab dir so ssh/status/start work from any cwd ([cc7c283](https://github.com/rmahique/rodeo-cli/commit/cc7c2839d9e9a63729d1ee581cd0cf975282f09a))
* **config:** remove harvester_os_password from default credentials — non-Harvester profiles don't need it ([f15c06c](https://github.com/rmahique/rodeo-cli/commit/f15c06c14cc92c77a30252a6854de8cba32951ec))
* **deploy:** stop raw tool output from crashing the deploy via Rich markup ([74dca9f](https://github.com/rmahique/rodeo-cli/commit/74dca9fc29cb7ade3c79e9ed5225312740d5cd3a))
* **deps:** require python3-lxml + check it in preflight/doctor ([11ddcd1](https://github.com/rmahique/rodeo-cli/commit/11ddcd131e7754be6324fe5a854a8b3773804497))
* **docs:** give the horseshoe mark real nail holes (4 per branch) ([32628bc](https://github.com/rmahique/rodeo-cli/commit/32628bc18613b3f25a473447a1974d88eb3f67ba))
* **downloads:** use curl -4 --http1.1 for Harvester ISO + PXE artifacts ([5f83d09](https://github.com/rmahique/rodeo-cli/commit/5f83d09e4e346e64a82f5e47e8308785e065532d))
* **eib:** require raw base image + auto-convert qcow2 during pre-stage ([b1cbc86](https://github.com/rmahique/rodeo-cli/commit/b1cbc86f8637bb27b2ff9080fc0b17ba67828b0e))
* **elemental:** use plan name as registration prefix when registration_prefix is empty string ([4c5341a](https://github.com/rmahique/rodeo-cli/commit/4c5341a7cb61df76c6e668989ed0009daa9f8d2a))
* **engine:** fail loud on inventory errors in runner and cluster ([#41](https://github.com/rmahique/rodeo-cli/issues/41)) ([e527fa6](https://github.com/rmahique/rodeo-cli/commit/e527fa64f6f72528ef1498f186bdfbce8a60d4c7))
* **engine:** hardening pass + instruqt finalise guard ([7b76cd7](https://github.com/rmahique/rodeo-cli/commit/7b76cd702ad467020ae767a49c51eb367729bf84))
* **engine:** stale libvirt-python availability check inside a single rodeo up run ([2e365a7](https://github.com/rmahique/rodeo-cli/commit/2e365a7aec1d998c9680a213b7e29a5929567d64))
* **finalise:** enable firewalld so DNAT rules survive reboot ([5f413a3](https://github.com/rmahique/rodeo-cli/commit/5f413a3a46627cad21cffca51bd2fbacfe7edc60))
* **fleet:** scope access URLs to lab.components, add script syntax regression tests ([5abc81a](https://github.com/rmahique/rodeo-cli/commit/5abc81a0b7dbaf75f926db83573d4e4ed7fc6adc))
* **fleet:** treat apply as no_cache when checking lab complete ([44b8906](https://github.com/rmahique/rodeo-cli/commit/44b8906f7f21b9e167dfba3df44ab41a24b8ad73))
* **fleet:** use run_remote stdin for operator secrets ([f567eb6](https://github.com/rmahique/rodeo-cli/commit/f567eb648e3c8f629cd5290ed1ff43556298f3ce))
* harden DNAT forward path and ARP on multi-homed KVM hosts ([1348d2b](https://github.com/rmahique/rodeo-cli/commit/1348d2bf72caa483f3783174574cb3e4a362f285))
* **harvester:** bump node disk to 320GB, fix Longhorn stability, neutral domain ([630e1a0](https://github.com/rmahique/rodeo-cli/commit/630e1a0194e9caf2a9fd1fe3beeac99fbaaaa07d))
* **init:** show rodeo up as primary next step after init ([76181ce](https://github.com/rmahique/rodeo-cli/commit/76181ce1c0f6d7c3299c101e06868e9c056f6819))
* **install:** don't abort on BASH_SOURCE when piped through bash ([2cf9b76](https://github.com/rmahique/rodeo-cli/commit/2cf9b769cd418ba3456a536b8189125da61ced81))
* **install:** self-heal remote refspec on update, never strand a host ([#26](https://github.com/rmahique/rodeo-cli/issues/26)) ([40cb53e](https://github.com/rmahique/rodeo-cli/commit/40cb53e0bff7c9531ea5490eef5705f5a15a109c))
* **instruqt:** open agent ports 15778/15779, fixing console stuck on "Please Wait" ([b4b425e](https://github.com/rmahique/rodeo-cli/commit/b4b425e433e3a723d1504f7051380a5ba78d2b5d))
* **instruqt:** print hostimage checklist on success, correct Save-timing docs ([1eaf7f9](https://github.com/rmahique/rodeo-cli/commit/1eaf7f93be90ab579956f5346f73b2536156f12d))
* **kvm_host:** add guestfs-tools to host packages ([c41d2ae](https://github.com/rmahique/rodeo-cli/commit/c41d2ae457d2099ea0d062a323b37104dfd677e9))
* **kvm_host:** allow DNAT'd inbound through libvirt's firewall (host:8443 UI access) ([125b532](https://github.com/rmahique/rodeo-cli/commit/125b532ebbdf79da6db1d904fbeec8e5c007526c))
* **kvm_host:** auto-correct sudo secure_path on SUSE hosts ([9083f90](https://github.com/rmahique/rodeo-cli/commit/9083f9017c8f1e24026ac06592bc18d0113a41c7))
* **kvm_host:** insert ct status dnat accept at chain start, not before reject handle ([6571afc](https://github.com/rmahique/rodeo-cli/commit/6571afc6f91a76891352ff3dcecf0489ef505560))
* **kvm_host:** keep DNAT-accept above libvirt guest_input reject ([#11](https://github.com/rmahique/rodeo-cli/issues/11)) ([bf1541f](https://github.com/rmahique/rodeo-cli/commit/bf1541f6091667825817d812c64ca44d8e31363b))
* **kvm_host:** NVMe pool never mounted on btrfs roots (SLES 16 / openSUSE) ([e12e8cf](https://github.com/rmahique/rodeo-cli/commit/e12e8cfe34ed3f10f90f0b1679ccf653b4b0d0d0))
* **kvm_host:** re-assert DNAT-accept after libvirt settles in finalise ([#13](https://github.com/rmahique/rodeo-cli/issues/13)) ([59347b7](https://github.com/rmahique/rodeo-cli/commit/59347b7078e2a519c0715b6ff3ded952abee0cdb))
* **kvm_host:** replace virsh domstate with pgrep in qemu hook — prevents libvirt re-entrant deadlock ([a4c33df](https://github.com/rmahique/rodeo-cli/commit/a4c33df4ac6697b16ec9f296c180ade64d6fec79))
* **kvm_host:** retry libvirt network hook until reject rule exists before inserting DNAT accept ([d46e7cd](https://github.com/rmahique/rodeo-cli/commit/d46e7cd92cdc7ce702db852ef81997f007c93381))
* **lab-in-a-box:** write common VM_MEM/VM_CPU/VM_DSK that lab-in-a-box requires ([a41ec49](https://github.com/rmahique/rodeo-cli/commit/a41ec49e8e1c280241934700e5413290f4aae1ef))
* **labinabox:** rerun preflight credits the lab's running VMs; reinstall lab-in-a-box when its source changes ([7a3aa84](https://github.com/rmahique/rodeo-cli/commit/7a3aa84ed4714d17e4392e7355a246fd7ffd3644))
* **lint:** remove spurious f-prefix on string literal in success.py (F541) ([80d1b74](https://github.com/rmahique/rodeo-cli/commit/80d1b74c25763cd3e5daf6c67e30e4ec037db43b))
* match mgmt NIC by hwAddr in ISO-seed config (net.ifnames=1 breaks eth0 match) ([20f11d9](https://github.com/rmahique/rodeo-cli/commit/20f11d9a6086b445ea4247a4c0cf0c1e34ec5893))
* move module-level constants after imports to satisfy ruff E402 ([1761395](https://github.com/rmahique/rodeo-cli/commit/176139535da8ade60639121a4089459eee14e376))
* **plan:** flag drift on phases already marked done; document re-run semantics ([7652e1d](https://github.com/rmahique/rodeo-cli/commit/7652e1d5bad579cca90bcc9114e837f60a7528f4))
* **portal:** 6-letter workshop code by default, portal-wide wrong-code cap ([#77](https://github.com/rmahique/rodeo-cli/issues/77)) ([d663bed](https://github.com/rmahique/rodeo-cli/commit/d663beddd002bf230debea6c23e6051d4856bdd6))
* **portal:** serve student SSH on its own sshd, never open :22 ([#75](https://github.com/rmahique/rodeo-cli/issues/75)) ([9186577](https://github.com/rmahique/rodeo-cli/commit/9186577989f4537eaebe74ab9daa3be7ac30b0c1))
* **preflight:** count the NVMe pool kvm_host is about to mount ([5f1221b](https://github.com/rmahique/rodeo-cli/commit/5f1221bbd93e749f9708499901a6e6503c1e5153))
* **preflight:** skip RAM/disk check on a vms-already-deployed re-run ([fac0b0e](https://github.com/rmahique/rodeo-cli/commit/fac0b0eabcf549d15fcabdcbe491a7a7a5ae87ef))
* **preflight:** skip RAM/disk checks when --from bypasses the vms phase ([7d84273](https://github.com/rmahique/rodeo-cli/commit/7d84273536ba1e846929d12fa4eafe4f44921f4a))
* **privilege:** hand ~/.rodeo back to the invoking user after self-escalation ([65d7219](https://github.com/rmahique/rodeo-cli/commit/65d7219a4b5fcd9b2c60779b4e47b76fd3ce5b3e))
* **profiles:** pin Harvester 1.8.1 explicitly in the test profile ([#22](https://github.com/rmahique/rodeo-cli/issues/22)) ([16c769b](https://github.com/rmahique/rodeo-cli/commit/16c769b5ede74fca667de023dfcfa572c4fb3ad8))
* **profiles:** propagate mgmt_mac into cfg["vms"][name]["mac"] ([be4ed01](https://github.com/rmahique/rodeo-cli/commit/be4ed019fe55ebe5adae215fbc55cc07a41ad28a))
* **pxe:** log the installer kernel console to the serial file (ttyS1) ([9dd828d](https://github.com/rmahique/rodeo-cli/commit/9dd828d342d602f7c52d9379aa4c22a07257f1a6))
* **pxe:** MAC-based iPXE chain — fixes two-stage boot (nodes never booted) ([da79740](https://github.com/rmahique/rodeo-cli/commit/da7974097ca92cdfc95e28fd7b0deb77382ca963))
* **pxe:** match Harvester mgmt interface by MAC, not eth0 ([d7a2214](https://github.com/rmahique/rodeo-cli/commit/d7a2214d6dd6a735d81b6ff5deb2d560a778e02c))
* **pxe:** serve Harvester config YAMLs as 0644 (nginx 403 blocked install) ([5e2e632](https://github.com/rmahique/rodeo-cli/commit/5e2e632c75f70fe3dfc982515e3a10de361709d6))
* quiet 'Domain not found' libvirt errors in list_vms by using listAllDomains first (no per-name probes for missing VMs on clean host); make plan's _inspect_host catch broadly for graceful fallback on libvirt connect failures; enhance install-deps to start virt daemons and verify libvirt binding; add top-level --config-dir support in cli group + ctx fallback in commands so 'rodeo --config-dir foo plan' works. ([4cbdc12](https://github.com/rmahique/rodeo-cli/commit/4cbdc12423db156287acbcd200c939fd0ef54ac2))
* **rancher:** add 'boot' phase so the Rancher VM actually starts ([9e68f51](https://github.com/rmahique/rodeo-cli/commit/9e68f5143adf2e5e56e35a01595d05373c76c013))
* **rancher:** clear first-login setting even when password already matches ([33327d6](https://github.com/rmahique/rodeo-cli/commit/33327d6d8238608906ce3f3e4e81dd2598c44cf5))
* **rancher:** correct auto-import cacerts (served CA) + default auto-import OFF ([#30](https://github.com/rmahique/rodeo-cli/issues/30)) ([7c93a4b](https://github.com/rmahique/rodeo-cli/commit/7c93a4bb09664f1725c2ac313c8053d81c6c7ba8))
* **rancher:** correct nonexistent elemental-register Hauler image reference ([615a955](https://github.com/rmahique/rodeo-cli/commit/615a9558e1fe925833ac825c7471118287ed4cec))
* **rancher:** download Leap Micro files via curl, add to hauler with lowercase names ([fe5672f](https://github.com/rmahique/rodeo-cli/commit/fe5672f6025247eefbb8a83e0dc1e545cca4055b))
* **rancher:** drop the redundant "git" arg in the git-in-container wrapper ([ed556fc](https://github.com/rmahique/rodeo-cli/commit/ed556fccab448e83d24c15beac7f2558a940af28))
* **rancher:** enable Traefik when tls_source=letsEncrypt — required for HTTP01 ACME ingress ([d3645e7](https://github.com/rmahique/rodeo-cli/commit/d3645e7bc19bca0e6a33441dcfecf9f7ca33bafa))
* **rancher:** generate self-signed cert with IP SAN for clean agent TLS ([0626e8e](https://github.com/rmahique/rodeo-cli/commit/0626e8eff4d19a32d4f46e6398e2380a3f48292e))
* **rancher:** grant write:user token scope; fail loud on real repo-create errors ([0165700](https://github.com/rmahique/rodeo-cli/commit/016570081b07a1d2461cf472c382cc0ba430c7f9))
* **rancher:** install Harvester UI extension via Rancher API instead of Helm ([aedc214](https://github.com/rmahique/rodeo-cli/commit/aedc214a5b3a7995d39b6063de6ad3ee0a72b211))
* **rancher:** make _deploy_gitea retry-safe (container name + already-exists) ([a09e29c](https://github.com/rmahique/rodeo-cli/commit/a09e29c2cc5c01a570b8215664db756e81929775))
* **rancher:** make cluster import idempotent on retry ([a65dfce](https://github.com/rmahique/rodeo-cli/commit/a65dfcefae0d2a2d705c1889a958ee6292d2461b))
* **rancher:** pass Helm bootstrapPassword via values file ([#40](https://github.com/rmahique/rodeo-cli/issues/40)) ([bf6f8d1](https://github.com/rmahique/rodeo-cli/commit/bf6f8d177949ba0b73031a20f1d610b06e01bb6e))
* **rancher:** patch cattle-cluster-agent with CATTLE_INSECURE_TLS=true ([95f80d8](https://github.com/rmahique/rodeo-cli/commit/95f80d84851fa15c74224452ce8a27a5b8ea3504))
* **rancher:** poll for registration token instead of fetching immediately ([1d6c019](https://github.com/rmahique/rodeo-cli/commit/1d6c019898d0dc83bdda4db1479e0abbe9e160c7))
* **rancher:** reconcile UI extensions for standalone labs too ([4a456d3](https://github.com/rmahique/rodeo-cli/commit/4a456d3c170e3f7c2120a2aee21cf4c281050327))
* **rancher:** restore Harvester UI extension declaration in bundled profiles ([d90c1b1](https://github.com/rmahique/rodeo-cli/commit/d90c1b1656557865891c72596c3212531c5319e8))
* **rancher:** retry auth API after /ping — avoid premature login failure ([d56437a](https://github.com/rmahique/rodeo-cli/commit/d56437af0462b19b556f9b3bf5864966d4b62da3))
* **rancher:** retry Harvester password change, set it regardless of auto-import ([8d4606b](https://github.com/rmahique/rodeo-cli/commit/8d4606bc0e7e18d00e1509b70a7c03213aecee48))
* **rancher:** robustness pass on UI extension and Harvester import ([61e8d8c](https://github.com/rmahique/rodeo-cli/commit/61e8d8c9185d4cb10e5309b30fe8997fd9c51935))
* **rancher:** run git via a container on the eib VM instead of zypper install ([efcb6f8](https://github.com/rmahique/rodeo-cli/commit/efcb6f83ce90f9b3d162572a63bc9194ddf65b03))
* **rancher:** tolerate empty API responses + idempotent password setup ([b75ccde](https://github.com/rmahique/rodeo-cli/commit/b75ccde2fca59578dfda3e7c731a206b927818c4))
* **rancher:** use curl -sk to fetch import manifest (self-signed cert) ([315cb46](https://github.com/rmahique/rodeo-cli/commit/315cb463797e7ba3a82d0b9c971987f2b1b09341))
* **rancher:** use helm upgrade --install for idempotent retries ([7d43505](https://github.com/rmahique/rodeo-cli/commit/7d435056e1314e84b6615bcb74eeb50c27ea6b03))
* **rancher:** use provisioning.cattle.io/v1 Cluster API for Harvester import ([231e4da](https://github.com/rmahique/rodeo-cli/commit/231e4da67e8ecd500ed5ded63191ca648e4fb5f3))
* **rancher:** wait for hauler-fileserver to actually listen before curling it ([de2732e](https://github.com/rmahique/rodeo-cli/commit/de2732ec8aeb73d16f94191ac860ac04064ae9a0))
* **rancher:** wire Harvester import into both deploy paths; sync cacerts after Helm upgrade ([298b9a0](https://github.com/rmahique/rodeo-cli/commit/298b9a0914fb431a2a6bef80a1865d154ee67b31))
* remove lab_admin_password from defaults to avoid spurious warning in no-Rancher plans; bump test example to 16GB RAM ([20d7d1c](https://github.com/rmahique/rodeo-cli/commit/20d7d1cc56483b750143668072b280ed1e3c41da))
* remove non-existent 'python3-libvirt' and 'libvirtd.service' from SLES install-deps (only python3-libvirt-python and virt* daemons exist on SLES 16); suppress systemctl stderr for clean output on missing units. ([5edacc9](https://github.com/rmahique/rodeo-cli/commit/5edacc9ab6c752edf540b367240038e725e8eeab))
* **runner:** skip diskless edge nodes in stream_boot instead of crashing ([3f19250](https://github.com/rmahique/rodeo-cli/commit/3f1925086206a545f9d110f9897b23523b99a008))
* **runner:** tee subprocess output to ~/.rodeo/logs/&lt;plan&gt;.log ([00e1194](https://github.com/rmahique/rodeo-cli/commit/00e11941b8edceeffe04439f57b85e0abc3052d0))
* **scripts:** use install.sh for rodeo-cli install instead of duplicating it ([2320c3c](https://github.com/rmahique/rodeo-cli/commit/2320c3ce5cb33ec24e47d61335b868f6856524b6))
* **secrets:** add rancher_vm_password, dedupe init_cmd's own secrets writer ([c0de3f9](https://github.com/rmahique/rodeo-cli/commit/c0de3f9dbc455ab3d3f35bc25e64319e0bda0be8))
* **secrets:** remove hardcoded fallback passwords, fail loud when missing ([7cd8424](https://github.com/rmahique/rodeo-cli/commit/7cd8424b890fee3a519c376d6745991af197a02c))
* **security:** harden NFS export, ISO verification, CI supply chain; add plan ownership ([#56](https://github.com/rmahique/rodeo-cli/issues/56)) ([ffcf5af](https://github.com/rmahique/rodeo-cli/commit/ffcf5af41c001d65fd286275d4e631509066510d))
* seed_lab() verifies its own copy; custom_scripts warns on empty dir ([cd6c95b](https://github.com/rmahique/rodeo-cli/commit/cd6c95b1f3ef613fe2da11625c43d5e97fa1fae2))
* **self-update:** force-fetch tags so a rewritten history can't strand a host ([f77d5bf](https://github.com/rmahique/rodeo-cli/commit/f77d5bf9e4337d2b20c51b41096d2b4524ba9854))
* **self-update:** guarantee alignment to origin/main, never strand a host ([#20](https://github.com/rmahique/rodeo-cli/issues/20)) ([4b0ed62](https://github.com/rmahique/rodeo-cli/commit/4b0ed62d63530bd5a318d53cde57e5a34f19f41b))
* set Harvester+Rancher passwords correctly; explicit secrets keys; fix runner to skip stream_import ([44c698e](https://github.com/rmahique/rodeo-cli/commit/44c698e7e1ec8e4bc33e25c4b03920d5a591993b))
* skip drain loop when Rancher setup already finished during node wait ([b663a70](https://github.com/rmahique/rodeo-cli/commit/b663a70f27b9c8858b29e925dfdebe051603e714))
* **smlm-workshop:** bake and scratch build the SMLM server on SLES 15 SP7 ([f51528d](https://github.com/rmahique/rodeo-cli/commit/f51528d697c722a76e048f5ca5bb525a8e6f7c7a))
* **smlm-workshop:** drop smlm_ssl_email, mgradm has no --ssl-email ([a4dd5bd](https://github.com/rmahique/rodeo-cli/commit/a4dd5bd28fdfd0a0df359d4a5e3e770b04de220e))
* **smlm-workshop:** generalise.sh counts SCC credentials in the database, failing closed ([4f55001](https://github.com/rmahique/rodeo-cli/commit/4f550012ba1e325e921725198418df8ddb51c4b9))
* **smlm-workshop:** size the SMLM server disk for the synced channels (300 GB) ([e0e068c](https://github.com/rmahique/rodeo-cli/commit/e0e068c29cb2eae2a51c1d8af32aa7020ca711c5))
* **smlm-workshop:** use CentOS 7 channels for the EL7 clients ([d21cce9](https://github.com/rmahique/rodeo-cli/commit/d21cce977308168afc2fcfb75bd16b12ba3783cb))
* split rancher_api and rancher_server_url to avoid DNS dependency ([16963ce](https://github.com/rmahique/rodeo-cli/commit/16963cec29b4a885240e26ced7bd4f57541e059c))
* **ssh:** detect an unreadable /root on Python 3.13+, not just &lt;=3.12 ([6296a3a](https://github.com/rmahique/rodeo-cli/commit/6296a3ac7ecaf40c568e137b9bcf123326e8958b))
* **ssh:** handle PermissionError from stat(), not just unreadable files ([abf6509](https://github.com/rmahique/rodeo-cli/commit/abf650971fc5f4bc98a5c516f161a51d879e6497))
* **ssh:** make rodeo ssh host/vm actually authenticate the nested hop ([1d0ab91](https://github.com/rmahique/rodeo-cli/commit/1d0ab91920414e2b8be85abb24d18d46ccd13c03))
* **ssh:** quote remote_cmd across the host/vm nested hop ([91530b9](https://github.com/rmahique/rodeo-cli/commit/91530b9e388fb2af5f9375d164dcc981979879fd))
* **ssh:** stop nested-VM SSH from silently degrading to a password prompt ([5174222](https://github.com/rmahique/rodeo-cli/commit/5174222ff41b939ab912c6142661972fb4ddbc5e))
* **start:** start --all discovers defined VMs, no phantom harvester3 ([#16](https://github.com/rmahique/rodeo-cli/issues/16)) ([97e0b6f](https://github.com/rmahique/rodeo-cli/commit/97e0b6f2d3295a99bfd01e6453f60b0fc1ab5eae))
* **start:** start Rancher first, wait for API health before booting Harvester ([7f1f057](https://github.com/rmahique/rodeo-cli/commit/7f1f0570ea83747e9aea99c1a14404b70f14b423))
* **start:** wait for Harvester VIP before starting Rancher on resume ([2ef81bd](https://github.com/rmahique/rodeo-cli/commit/2ef81bdcc0bb67978ae4e8922ff99b5130f31c8c))
* stop and mask firewalld in packages.yml to prevent Instruqt connection drop ([d366283](https://github.com/rmahique/rodeo-cli/commit/d3662836f2a87b87a34e437f7c31f3d68ee3c1e0))
* **story:** refuse story packages without a .sha512; repeat lab-in-a-… ([53ba124](https://github.com/rmahique/rodeo-cli/commit/53ba124266bbe56ce35f7ffc980731fb7bd66df3))
* **story:** refuse story packages without a .sha512; repeat lab-in-a-box's summary when setup_lab.py fails ([df3c4ce](https://github.com/rmahique/rodeo-cli/commit/df3c4cecd952afeb3dc762217d705029e052b57a))
* **success:** make the success screen topology-aware ([d813495](https://github.com/rmahique/rodeo-cli/commit/d8134954a9dab01bcc343f3d3f2a8c20cfeb80ce))
* **success:** show suse-edge-specific content and correct letsEncrypt URL ([5fe5706](https://github.com/rmahique/rodeo-cli/commit/5fe57067b6b1bf1d4190fb90248d767597396406))
* support --config-dir at top level CLI group (so before subcommand works); make plan graceful on libvirt connect errors (no traceback on clean host); enhance install-deps to start virt daemons on SLES and verify libvirt binding; add config_dir support to more commands via ctx; update test guide for SLES gotchas (system-site-packages venv, daemon start, correct option order, env secrets). ([e33e26d](https://github.com/rmahique/rodeo-cli/commit/e33e26dbcb1405dd152899d6e7566c3c15213fec))
* surgical task batch — vars contract, checksum, import, exit codes ([7c73384](https://github.com/rmahique/rodeo-cli/commit/7c733844fbec2726de780a89cbf6384685f5f25c))
* **suse-edge:** add boot phase to start VMs after define — required before rancher SSH wait ([f9d4cd5](https://github.com/rmahique/rodeo-cli/commit/f9d4cd5ace1aa7d1606e0d080d24ba5438793c56))
* **suse-edge:** install elemental-ui from direct tarball URL, not Helm repo ([edee9c4](https://github.com/rmahique/rodeo-cli/commit/edee9c40e5df94bbfc8470c14ff00830d59dde31))
* **suse-edge:** move definition to data/platforms/suse-edge after rename refactor ([0d4370f](https://github.com/rmahique/rodeo-cli/commit/0d4370f582e673c4fe07ddd08a9e69cf171d996c))
* **suse-edge:** pass --devel to helm when installing elemental-ui pre-release ([fa5cdbe](https://github.com/rmahique/rodeo-cli/commit/fa5cdbe6d317afecebba396e677c4f138f8b94de))
* **suse-edge:** reconcile the Elemental UI extension after the operator ([#55](https://github.com/rmahique/rodeo-cli/issues/55)) ([64a6d2b](https://github.com/rmahique/rodeo-cli/commit/64a6d2bfcfc4529ed4e4ddbe00f9883ef6ee933f))
* **suse-edge:** repair EIB build pipeline, ISO seeding, and registration config ([1a591dd](https://github.com/rmahique/rodeo-cli/commit/1a591dd699529213c983518870eaf01c7c00db93))
* **suse-edge:** restore elemental/ as the registration config directory ([49d0a56](https://github.com/rmahique/rodeo-cli/commit/49d0a56e30182b3d95b36074318145db85c82867))
* **suse-edge:** ruff f-string + edge_node_names detection in start guard ([0c8e614](https://github.com/rmahique/rodeo-cli/commit/0c8e614c11c6ab1f4e22dbde928f4d9f6c265abe))
* **suse-edge:** set deployment_target to baremetal — this profile targets bare metal KVM hosts ([db2344d](https://github.com/rmahique/rodeo-cli/commit/db2344d1560104c6ebc22f50a271ae351ded836d))
* **suse-edge:** switch edge-node base images from SLE Micro to openSUSE Leap Micro 6.2 ([757af39](https://github.com/rmahique/rodeo-cli/commit/757af396182abced37b97c70ec2ef5e510f653dc))
* **suse-edge:** switch EIB image to Leap 16 Cloud variant; install podman at first boot ([e972eb2](https://github.com/rmahique/rodeo-cli/commit/e972eb28725c3bcf28c5e8894762a1f6a6e2485f))
* **suse-edge:** sync stale os-files/edge-definition.yaml text in success messages ([13009a2](https://github.com/rmahique/rodeo-cli/commit/13009a2810afba5ea4e575d7b3fd5cb7261d3b11))
* **suse-edge:** update cert-manager to v1.20.1 in example rodeo-plan.yaml ([9202457](https://github.com/rmahique/rodeo-cli/commit/920245746a1c9edd0cd5225e6a56f386ddd82fd7))
* **suse-edge:** update cert-manager to v1.20.1 per SUSE Edge 3.6 release notes ([1edb953](https://github.com/rmahique/rodeo-cli/commit/1edb95381cd658c0654d1686f0a384be33964e2a))
* **suse-edge:** upgrade elemental-ui to 3.0.2-rc.2 to fix JS load error on Rancher 2.14.1 ([3d008eb](https://github.com/rmahique/rodeo-cli/commit/3d008ebe49e8092cbd5e3c8d0c4baf3e269f4ee3))
* **suse-edge:** use ??rancher_letsencrypt_email in plan; validate email before LE registration ([f7850e3](https://github.com/rmahique/rodeo-cli/commit/f7850e3757504a4ec44bbcd9681776600109f76d))
* tee Python phase events to log file; use setpassword for Rancher admin ([12f19b5](https://github.com/rmahique/rodeo-cli/commit/12f19b568b3e06269867dfc334ecf8dff34dcaa8))
* **test profile:** bump Harvester disk to 250 GB (100 GB filled, RKE2 failed) ([6e2cd84](https://github.com/rmahique/rodeo-cli/commit/6e2cd84adf80dffc14e9875e0d5961243304cdde))
* **tests:** add --no-tmux to deploy test to prevent pytest process replacement ([6a2d2ae](https://github.com/rmahique/rodeo-cli/commit/6a2d2aee9f9dd7135901db1961883372799d624c))
* **tests:** isolate _LAST_LAB_FILE in autouse fixture ([417cbaf](https://github.com/rmahique/rodeo-cli/commit/417cbaf7c9295c3cbab66b09861d4f6be18de0af))
* **test:** update rancher profile phases assertion to include apply phase ([e7aee04](https://github.com/rmahique/rodeo-cli/commit/e7aee04eea24dd964446ccf73efa08b2a3c3a4de))
* **tmux:** clarify re-attach message — no sudo, as the user who ran rodeo up ([52e9ac2](https://github.com/rmahique/rodeo-cli/commit/52e9ac277e817e39a5b0de24ded0b50f6fd6a373))
* **tui:** strip ANSI sequences and batch serial log lines ([dbabd7a](https://github.com/rmahique/rodeo-cli/commit/dbabd7a768a133a291a9339fc32f76df5154abf3))
* **up:** _infer_lab_profile picks the longest match, not the first ([c64d00d](https://github.com/rmahique/rodeo-cli/commit/c64d00d461242da0aa2246144e92c6253f454dde))
* **up:** --profile wins over the last lab + AWS banner lists only the lab's UIs ([#82](https://github.com/rmahique/rodeo-cli/issues/82)) ([f9a4ae5](https://github.com/rmahique/rodeo-cli/commit/f9a4ae52e6528d96fb78ae6ec3841a3b843e228a))
* **up:** hand ~/.rodeo back to the invoking user on AWS deploys too ([252fe7b](https://github.com/rmahique/rodeo-cli/commit/252fe7b6dc478cf3373610c50dd128da48b81601))
* **up:** persist --target to existing lab plan; stop spurious prompt ([f216bf9](https://github.com/rmahique/rodeo-cli/commit/f216bf9dc25d19775a8aaba472c7d59dcb1e5b31))
* **up:** skip tmux on Instruqt; keep window open on exit so errors are readable ([e19a7d1](https://github.com/rmahique/rodeo-cli/commit/e19a7d11e45937d1cc10d4f1e14be60138f07ef7))
* use correct secret name tls-ca for Rancher privateCA ([edb7d8f](https://github.com/rmahique/rodeo-cli/commit/edb7d8ff41fcf69aba73dde7b58daacf8f347f02))
* use sslip.io hostname for rancher_api in NodePort TLS mode ([520e5a5](https://github.com/rmahique/rodeo-cli/commit/520e5a55ace942b9fa18cb95bfe68b1e9f0e5c1b))
* **virt-workshop-aws:** size webserver-prod's boot disk from image virtualSize ([7a74ece](https://github.com/rmahique/rodeo-cli/commit/7a74ece727ea6170fab7277af34e2b4f50d00166))
* **virt-workshop-aws:** switch cached image to Leap-16.0-Minimal-VM Cloud build ([3eb5884](https://github.com/rmahique/rodeo-cli/commit/3eb5884c191556abedb9372f50bb9260ec530b0a))
* **virt-workshop-aws:** sync bundled custom_scripts/checks with suse-virt-workshop ([9dfeb04](https://github.com/rmahique/rodeo-cli/commit/9dfeb04fff31294afe5aa5518a2e5e55d881899f))
* **vms:** balanced quotes in Leap download task; guard against split_args aborts ([#28](https://github.com/rmahique/rodeo-cli/issues/28)) ([af2a8bc](https://github.com/rmahique/rodeo-cli/commit/af2a8bc62af63c2bf1776f42dc0f8e8b5ca15ce8))
* **vms:** drop unfixable+unnecessary virt-customize step from eib_image.yml ([48072c3](https://github.com/rmahique/rodeo-cli/commit/48072c30c8193ce96175032c2e780ad6ca4f724b))
* **vms:** guard default-network redefinition; plan Phase B2 auto-reconciliation ([f97d4b5](https://github.com/rmahique/rodeo-cli/commit/f97d4b5400613f240e66c0ccf5c0365a0d2d7656))
* **vms:** inject cloud-init into the Rancher VM image (Leap 16 ships without it) ([977c465](https://github.com/rmahique/rodeo-cli/commit/977c4652379c3cd58c713213cf57b6fff4d21f92))
* **vms:** make Leap image downloads resilient to opensuse HTTP/2 flakes ([#21](https://github.com/rmahique/rodeo-cli/issues/21)) ([220fd52](https://github.com/rmahique/rodeo-cli/commit/220fd524fef887f220aec1541b4bd9bcd493acb7))
* **vms:** remove double-hyphen from XML comment in vm.xml.j2 — invalid XML ([d0db98b](https://github.com/rmahique/rodeo-cli/commit/d0db98bdc81ed5737159e288e9bf385204cf8a22))
* **vms:** skip Rancher VM image/cloud-init when the topology has no Rancher ([99631aa](https://github.com/rmahique/rodeo-cli/commit/99631aa37b3980ddc812eedc9d2934cbc21352a6))
* **vms:** use command:cp for async OVMF copy — copy module does not support async ([8e1d8db](https://github.com/rmahique/rodeo-cli/commit/8e1d8db6347386b3f89db17e95c1914fafdf5252))
* **vms:** use Leap 16 Cloud image with cloud-init pre-installed ([8bc37f4](https://github.com/rmahique/rodeo-cli/commit/8bc37f4ff53fc65d7c0785d17c82f75c2cb635d9))
* wait for harvester-ui-extension pods before import ([1ecde64](https://github.com/rmahique/rodeo-cli/commit/1ecde64f5e37908db900a67ae5e6db4e96d57f01))


### Performance

* parallel disk/ISO creation + overlap Rancher setup with node-ready wait ([ae7f3db](https://github.com/rmahique/rodeo-cli/commit/ae7f3db420c5f4934e452cdfa898d7e5dff81ce0))


### Refactoring

* **ansible:** ansible-lint clean roles, per-node DHCP drift, dedup curl/ssh-key tasks ([8bb5de6](https://github.com/rmahique/rodeo-cli/commit/8bb5de62378c28d83e7f4325359e7d8e58edf141))
* **cli:** de-leak profile, random init passwords, README sync ([6c30735](https://github.com/rmahique/rodeo-cli/commit/6c30735e554ff3581609836b0d7a032a2deeeb43))
* derive edge topology and VM lists from the definition, not hardcoded ([#18](https://github.com/rmahique/rodeo-cli/issues/18)) ([b81e0ca](https://github.com/rmahique/rodeo-cli/commit/b81e0ca1c00f7c3b29247dd9473a2af4384656d2))
* introduce profile scaffold for multi-product extensibility ([6e74964](https://github.com/rmahique/rodeo-cli/commit/6e749640f6643fdd66d7b6cffc2aa1f82aaf9e68))
* Phase 0+1 — DeployRunner, bug fixes, version 0.2.0 ([cdbb19a](https://github.com/rmahique/rodeo-cli/commit/cdbb19a12ad922c66667adab14f6d38e3b0b67fa))
* remove lab_admin_password; use harvester_admin_password + rancher_admin_password throughout ([550111e](https://github.com/rmahique/rodeo-cli/commit/550111e51fcddb76e008281b3c6919b0c9444c77))
* rename data/profiles → data/platforms for semantic clarity ([a306675](https://github.com/rmahique/rodeo-cli/commit/a3066757f8676e2bcf2c1095e819ace3bb714262))


### Documentation

* add CONTEXT.md — full AI handoff document ([54a91c9](https://github.com/rmahique/rodeo-cli/commit/54a91c99f54b67b34c3ee95f531fbeeb8f8b3956))
* add deployment guide and runbook ([522c0ee](https://github.com/rmahique/rodeo-cli/commit/522c0ee119a80ccdc232448325efd3f7f8e3322c))
* add generate to architecture design goals. ([63d5a6d](https://github.com/rmahique/rodeo-cli/commit/63d5a6d5f12275a19060c23a799d3534dd0fcd7c))
* add generate to CONTEXT commands table. ([5169f4d](https://github.com/rmahique/rodeo-cli/commit/5169f4d25f5a41458d985328bf98929644af767c))
* add GitHub Pages site (mkdocs-material, andresvalero.tech design) ([67a97bd](https://github.com/rmahique/rodeo-cli/commit/67a97bd21822ba1978e3dd5fc9cc044c93fac75d))
* add Harvester admin password recovery when the live value is unknown ([10af0c3](https://github.com/rmahique/rodeo-cli/commit/10af0c3e1c197acd6e53f76c38d083c325f9d74d))
* add Mermaid deployment phases diagram + enhance user-guide with visual bootstrap and phases flows for best possible documentation quality ([f9d8a67](https://github.com/rmahique/rodeo-cli/commit/f9d8a670d2d2f14b6ec7e5936a5b0d275fd2563d))
* **audit:** log ownership handback follow-up on fix [#1](https://github.com/rmahique/rodeo-cli/issues/1) ([cf3cb4b](https://github.com/rmahique/rodeo-cli/commit/cf3cb4b0ff8e2c48410deac6cdec2ac52fddcee3))
* clean up wording across docs and site copy ([8784d73](https://github.com/rmahique/rodeo-cli/commit/8784d736dfce69c11762bd57a9626a3bd1e13746))
* correct bootstrap usage to use 'bash -s --' and document RODEO_REF / --ref ([428be4c](https://github.com/rmahique/rodeo-cli/commit/428be4c29124043509a2975f38157d85126ea765))
* **custom-rodeos:** correct manifests/helm claims to match apply-phase reality ([d7f17a3](https://github.com/rmahique/rodeo-cli/commit/d7f17a371b3dedc1368c3cea8af56bfeccfd89ca))
* document rancher.ui_extensions and rodeo install-extensions ([e050ab9](https://github.com/rmahique/rodeo-cli/commit/e050ab9cd11431d3749cd948973331adc5555465))
* document tmux session wrap across all deployment guides ([6990d2f](https://github.com/rmahique/rodeo-cli/commit/6990d2f46597173181f59d30c4a9ab1e292d7314))
* **examples:** add AWS single-host + fleet live smoke-test checklist for Claude Code ([855ee5f](https://github.com/rmahique/rodeo-cli/commit/855ee5f3ee8c2c4f3b6ddcfc247367805b13763b))
* final polish to project docs (user-guide, README, architecture, CONTEXT) and code (generate/stop/start_cmd with enhanced technical docstrings: inputs/outputs, patterns, why created/logical reasons in project, outcomes, how works, fit in general picture per requirements; no prompts language). All functions documented for engineer understanding. ([5512700](https://github.com/rmahique/rodeo-cli/commit/5512700cc22fdc6f1bc865ffa1eebc4748845453))
* fix layout — hero font-size bug, dead grid space, uneven card grids ([5a41b3a](https://github.com/rmahique/rodeo-cli/commit/5a41b3aaecd5badee6b7c3f86ae8dfaa2f1b286a))
* fix stale version refs, install instructions, and profile count ([b6915a6](https://github.com/rmahique/rodeo-cli/commit/b6915a6fabaf343715db98a8837d8acdf910d4f3))
* **fleet:** add Roadmap subsection for F3 MCP and F4 host-acquire ([d982d61](https://github.com/rmahique/rodeo-cli/commit/d982d615513fcec6f784059f6940fe7da6bc42da))
* **fleet:** F4 host-acquire plan — AWS then GCP then Hetzner ([8fd1ad8](https://github.com/rmahique/rodeo-cli/commit/8fd1ad88803f653b56dcbe2622f60836b9eca301))
* **fleet:** run fleet doctor after deploy on provisioned hosts ([#65](https://github.com/rmahique/rodeo-cli/issues/65)) ([59b58f7](https://github.com/rmahique/rodeo-cli/commit/59b58f7ea6390c7c0c8977de5459eb0123e28798))
* hygiene pass — sync versions, test counts, command reference ([02c1447](https://github.com/rmahique/rodeo-cli/commit/02c1447fd4142e079e71ba8c02ea0c449db66e0c))
* **instruqt:** correct stale firewalld-timing guidance ([2099ba4](https://github.com/rmahique/rodeo-cli/commit/2099ba43ce3433c8592568d3585bb6b5a5afa422))
* **instruqt:** document rodeo start-if-needed for attendee instance boot ([45663b2](https://github.com/rmahique/rodeo-cli/commit/45663b25fe159ddd78f416bc60602be7b89070c0))
* live-validate harvester-aws on m8id.8xlarge — full success ([2e1d9a1](https://github.com/rmahique/rodeo-cli/commit/2e1d9a14a3cba6cb0ebd363fc01304b34e959949))
* minor architecture update referencing new bootstrap visual ([4f1f3bf](https://github.com/rmahique/rodeo-cli/commit/4f1f3bf33777847738a0fc184b418285a279578e))
* minor ROADMAP update for clean E2 heuristic now supporting --force-network/--all host reset. ([6c3bb33](https://github.com/rmahique/rodeo-cli/commit/6c3bb3344064c6db324b4105fde31b4779e21f8b))
* move historical audits under docs/archive/ ([1b3fd93](https://github.com/rmahique/rodeo-cli/commit/1b3fd938fa856e0e04b6037a1f2df603a51a63ef))
* note new commands in legacy deployer README. ([a8d1609](https://github.com/rmahique/rodeo-cli/commit/a8d16094d8490bc727a0b7b7066954dfe9e9046a))
* review and polish for v0.22 ([#86](https://github.com/rmahique/rodeo-cli/issues/86)) ([b1e2f21](https://github.com/rmahique/rodeo-cli/commit/b1e2f2142c723308f9bf9c283dfeecdf918e7e7b))
* rewrite README and add profile-specific user guides ([4060400](https://github.com/rmahique/rodeo-cli/commit/4060400489537fe63f6aa6f89d9f8afcfb9a6f79))
* **roadmap:** add Phase H — Hauler air-gap integration (3 levels) ([567aff8](https://github.com/rmahique/rodeo-cli/commit/567aff80c7eb51456db4518a60b92bb077a41b3e))
* **roadmap:** check off Phase E live validation ([7e0c39e](https://github.com/rmahique/rodeo-cli/commit/7e0c39e5b25565e7508c4ee0c3f07ac51b5e49bc))
* **roadmap:** mark Instruqt builder validation complete ([#39](https://github.com/rmahique/rodeo-cli/issues/39)) ([fba7337](https://github.com/rmahique/rodeo-cli/commit/fba7337cdd56d255b4d87eb37618a0bb35ff28f8))
* split README into user guide + architecture reference ([cf438a5](https://github.com/rmahique/rodeo-cli/commit/cf438a598490f39b4a2d3b3ea40ebe522cfe13f3))
* sync all docs to v0.6 (on-ramp, rancher profile, Phase C, live-test learnings) ([27ed2ed](https://github.com/rmahique/rodeo-cli/commit/27ed2ed22ed36644335a51e2251441bd9d0100b8))
* sync contributor docs for audit fix [#7](https://github.com/rmahique/rodeo-cli/issues/7) ([2266ce7](https://github.com/rmahique/rodeo-cli/commit/2266ce7b007b2a038abe6338356ffbd17dece8e7))
* sync root README and CONTEXT.md with enhanced 'rodeo clean --all --secrets --force-network' host reset (full VM/network/state/password cleanup; no package removal). Added details for repurposing/fresh start use cases, matching user-guide and Generated content. ([170e519](https://github.com/rmahique/rodeo-cli/commit/170e519422c9fbf994725768f63e7cbdb2acdefe))
* update all project docs and files for stop/start, bootstrap, clean --all/hard/secrets/force-network, infra_type in definitions. Added stop/start sections, examples, lifecycle flows, integration notes, regression examples. Synced README, user-guide, architecture, CONTEXT, example README, clean.py comments. ([da2618f](https://github.com/rmahique/rodeo-cli/commit/da2618f6061d2e458ad7ec55d17e4ede58ccef9b))
* update cli quickstart help for stop/start/clean lifecycle. ([d433511](https://github.com/rmahique/rodeo-cli/commit/d433511cfc1daa02a7a036826bc90245b00b9f33))
* update for recent changes (generate secrets guard, virsh uri in stop/start/clean fallbacks, tests added) ([17700c6](https://github.com/rmahique/rodeo-cli/commit/17700c6b5aef1632577fdeff0c608876cd8d5c5b))
* update for v0.9.0 — Harvester import is a lab exercise, not automated ([dcb0ef3](https://github.com/rmahique/rodeo-cli/commit/dcb0ef36a3bd62e608282704561936b8569db7ca))
* update roadmap — Instruqt validation queue, AWS target, SUSE Edge and Telco Cloud rodeos ([ebe8d5c](https://github.com/rmahique/rodeo-cli/commit/ebe8d5c42d714b3c530d0d94cf043e9411cee5df))
* update user-guide with generate in first-time and installation, enhance stop section. ([736d256](https://github.com/rmahique/rodeo-cli/commit/736d256f2302c946a96539027294fa0b7253f780))
* world-class documentation restructure ([620d68b](https://github.com/rmahique/rodeo-cli/commit/620d68ba26bb0d93ed220348180702c72ed7dc5b))


### Build & Release

* automate releases with release-please; drop manual version bumping ([#6](https://github.com/rmahique/rodeo-cli/issues/6)) ([1eea8f9](https://github.com/rmahique/rodeo-cli/commit/1eea8f9f9c624a23e185f1073ad0470e8a52ffb4))

## [0.24.0](https://github.com/avaleror/rodeo-cli/compare/v0.23.0...v0.24.0) (2026-10-09)


### Features

* **aws:** install the AWS CLI for AWS deploys and require working AWS credentials ([d11f5eb](https://github.com/avaleror/rodeo-cli/commit/d11f5eb4a5edad85c11ee4b00dabc261744bce38))
* builder server ([dbeae54](https://github.com/avaleror/rodeo-cli/commit/dbeae543db75bc74ed9320ee2b4f73e9e69f15fe))
* **builder:** live rodeo builder with save to profiles, rodeo new --from-zip, lab-in-a-box catalogue kinds and errors ([bc2f483](https://github.com/avaleror/rodeo-cli/commit/bc2f483003733f4d873cbc8c9e22cc49f71d011b))
* **builder:** missing_addon marks a chapter as work needed ([4da73f6](https://github.com/avaleror/rodeo-cli/commit/4da73f6a95704927b3c49439eb2600ea11c06b37))
* **fleet:** scale down unclaimed labs safely; audit fixes ([#90](https://github.com/avaleror/rodeo-cli/issues/90)) ([fbc5c30](https://github.com/avaleror/rodeo-cli/commit/fbc5c30b242d506a3d7386db50ec0d5099a2ae6e))


### Bug Fixes

* **labinabox:** rerun preflight credits the lab's running VMs; reinstall lab-in-a-box when its source changes ([7a3aa84](https://github.com/avaleror/rodeo-cli/commit/7a3aa84ed4714d17e4392e7355a246fd7ffd3644))
* **smlm-workshop:** generalise.sh counts SCC credentials in the database, failing closed ([4f55001](https://github.com/avaleror/rodeo-cli/commit/4f550012ba1e325e921725198418df8ddb51c4b9))

## [0.23.0](https://github.com/avaleror/rodeo-cli/compare/v0.22.1...v0.23.0) (2026-10-08)


### Features

* **fleet:** remove local leftovers of terminated cloud hosts ([#88](https://github.com/avaleror/rodeo-cli/issues/88)) ([832fe25](https://github.com/avaleror/rodeo-cli/commit/832fe25e824a46a297937df9b065c285e54d8fc7))

## [0.22.1](https://github.com/avaleror/rodeo-cli/compare/v0.22.0...v0.22.1) (2026-10-07)


### Documentation

* review and polish for v0.22 ([#86](https://github.com/avaleror/rodeo-cli/issues/86)) ([b1e2f21](https://github.com/avaleror/rodeo-cli/commit/b1e2f2142c723308f9bf9c283dfeecdf918e7e7b))

## [0.22.0](https://github.com/avaleror/rodeo-cli/compare/v0.21.0...v0.22.0) (2026-10-07)


### Features

* **install:** control-plane install on macOS and any Linux; rodeo refuses local labs it cannot host ([#84](https://github.com/avaleror/rodeo-cli/issues/84)) ([6de7780](https://github.com/avaleror/rodeo-cli/commit/6de778004fbdd5e191e156ce81a49734fd04302a))

## [0.21.0](https://github.com/avaleror/rodeo-cli/compare/v0.20.1...v0.21.0) (2026-10-07)


### Features

* **aws:** dead-man switch on every cloud host rodeo launches (6 h default) ([#83](https://github.com/avaleror/rodeo-cli/issues/83)) ([eb32709](https://github.com/avaleror/rodeo-cli/commit/eb3270955c0435a1f9372a355f6d9cf77470cf10))
* **builder:** static Rodeo Builder web UI, published with the docs ([3fa9418](https://github.com/avaleror/rodeo-cli/commit/3fa9418d765afba05af13f863716c919006cf915))
* Fix/labinabox common sizing ([a6720e2](https://github.com/avaleror/rodeo-cli/commit/a6720e2f69f0a224a3bf62886e5db5b456c8d38d))
* rancher profiles with downstream K3s/RKE2 clusters + AWS without a provider block ([#81](https://github.com/avaleror/rodeo-cli/issues/81)) ([d3ee03d](https://github.com/avaleror/rodeo-cli/commit/d3ee03d23f2b700d794cd603968ff6724ea48463))


### Bug Fixes

* **builder:** list every workshop's chapters, mark the lab engine, ed… ([de08940](https://github.com/avaleror/rodeo-cli/commit/de089400a15974bd999ed0f1b3d5c828324516bc))
* **builder:** list every workshop's chapters, mark the lab engine, edit plan.yaml ([d55a22f](https://github.com/avaleror/rodeo-cli/commit/d55a22f6e94f4e9383a993d9bf9bad394f956008))
* **smlm-workshop:** size the SMLM server disk for the synced channels (300 GB) ([e0e068c](https://github.com/avaleror/rodeo-cli/commit/e0e068c29cb2eae2a51c1d8af32aa7020ca711c5))
* **up:** --profile wins over the last lab + AWS banner lists only the lab's UIs ([#82](https://github.com/avaleror/rodeo-cli/issues/82)) ([f9a4ae5](https://github.com/avaleror/rodeo-cli/commit/f9a4ae52e6528d96fb78ae6ec3841a3b843e228a))

## [0.20.1](https://github.com/avaleror/rodeo-cli/compare/v0.20.0...v0.20.1) (2026-10-05)


### Bug Fixes

* **portal:** 6-letter workshop code by default, portal-wide wrong-code cap ([#77](https://github.com/avaleror/rodeo-cli/issues/77)) ([d663bed](https://github.com/avaleror/rodeo-cli/commit/d663beddd002bf230debea6c23e6051d4856bdd6))
* **portal:** serve student SSH on its own sshd, never open :22 ([#75](https://github.com/avaleror/rodeo-cli/issues/75)) ([9186577](https://github.com/avaleror/rodeo-cli/commit/9186577989f4537eaebe74ab9daa3be7ac30b0c1))

## [0.20.0](https://github.com/avaleror/rodeo-cli/compare/v0.19.0...v0.20.0) (2026-10-02)


### Features

* **lab-in-a-box:** deploy the latest release by default ([752a477](https://github.com/avaleror/rodeo-cli/commit/752a4771803c55ad86c051f7044eda170ef8743c))
* **lab-in-a-box:** deploy the latest release by default ([1e4c8e7](https://github.com/avaleror/rodeo-cli/commit/1e4c8e78064b881701fe56d521ab69de7c5b1830))

## [0.19.0](https://github.com/avaleror/rodeo-cli/compare/v0.18.0...v0.19.0) (2026-10-02)


### Features

* incorporate lab-in-a-box as an engine, changes to make it easier to test betas,etc.. ([c12188d](https://github.com/avaleror/rodeo-cli/commit/c12188d7644f2a086535940e18655f729d3ec805))


### Bug Fixes

* **fleet:** use run_remote stdin for operator secrets ([f567eb6](https://github.com/avaleror/rodeo-cli/commit/f567eb648e3c8f629cd5290ed1ff43556298f3ce))

## [0.18.0](https://github.com/avaleror/rodeo-cli/compare/v0.17.0...v0.18.0) (2026-10-02)


### Features

* **fleet:** student claim portal (F5) ([#53](https://github.com/avaleror/rodeo-cli/issues/53)) ([e50ff2b](https://github.com/avaleror/rodeo-cli/commit/e50ff2ba8d43afdafaa00141c9c4b25a67e6d704))


### Documentation

* **fleet:** run fleet doctor after deploy on provisioned hosts ([#65](https://github.com/avaleror/rodeo-cli/issues/65)) ([59b58f7](https://github.com/avaleror/rodeo-cli/commit/59b58f7ea6390c7c0c8977de5459eb0123e28798))

## [0.17.0](https://github.com/avaleror/rodeo-cli/compare/v0.16.1...v0.17.0) (2026-10-02)


### Features

* **aws:** auto-manage the security group instead of requiring an operator-supplied one ([787df16](https://github.com/avaleror/rodeo-cli/commit/787df169a12c59077a90e7b3c25b87148d083029))
* bump Harvester 1.8.1 -&gt; 1.8.2, Rancher 2.14.1 -&gt; 2.14.5 (harvester-family only) ([e5ff956](https://github.com/avaleror/rodeo-cli/commit/e5ff95687db65ba04b2b81e172fcd90818d267f4))
* **engine:** actually run custom/scripts/ — documented since config_dir shipped, never executed ([9ae7650](https://github.com/avaleror/rodeo-cli/commit/9ae7650dbae8fc58b75a06f79dd39f3a14a35d16))
* **harvester-aws:** raise guest RAM to 24 GiB/Harvester-node, 16 GiB Rancher ([4a74a79](https://github.com/avaleror/rodeo-cli/commit/4a74a798eb5375ac31080ab4ce52c18101538588))
* new harvester-aws profile — 3-node Harvester+Rancher pre-tuned for AWS ([23683cf](https://github.com/avaleror/rodeo-cli/commit/23683cf4903ef8e0f03489c4962beedb5c6d74f8))
* new virt-workshop-aws profile — pre-lab state for suse-virt-workshop's exercises ([524ad34](https://github.com/avaleror/rodeo-cli/commit/524ad348eaedb50f95224411313ca5acfed4aacb))
* Option A — AWS is a target, not a topology; fix suse-edge TLS regression ([4811e34](https://github.com/avaleror/rodeo-cli/commit/4811e34c74586889deb04b635457571e43e49cf2))
* suse-edge-aws profile — AWS-tuned SUSE Edge 3.6 (Rancher+EIB+4 edge nodes) ([26afd56](https://github.com/avaleror/rodeo-cli/commit/26afd565a513e294e4c42d47484520dd9a51a0f7))
* **suse-edge:** upgrade to SUSE Edge 3.7 ([#54](https://github.com/avaleror/rodeo-cli/issues/54)) ([06f0202](https://github.com/avaleror/rodeo-cli/commit/06f0202eac0f689e60d78eb1ec35df15d02bd7c0))
* virt-workshop-aws-2n — genuine budget tier, 2-node Harvester on m8id.4xlarge ([810546f](https://github.com/avaleror/rodeo-cli/commit/810546fa9c1ee559d73300f7358eb2285d1a08d2))
* **virt-workshop-aws:** pre-create daily-batch-processor for full chapter-4 parity ([8d5e9f2](https://github.com/avaleror/rodeo-cli/commit/8d5e9f27770c5b984aa9cc9439d9b3c7c340efa2))


### Bug Fixes

* **aws:** disk floor is a flat 300GB/Harvester-node, 60GB/Rancher-node, not a pool budget ([6d5e240](https://github.com/avaleror/rodeo-cli/commit/6d5e240907ae87fe688a89a0cceaae7be073b39d))
* **aws:** harvester-2n needs 32 vCPU/128 GiB, not 8/64 — resize to m8id.8xlarge ([aed0368](https://github.com/avaleror/rodeo-cli/commit/aed0368f9d0e8fe97de75156c257529405a75bc9))
* **aws:** harvester-aws must fit m8id.8xlarge too, not m8id.12xlarge ([b2763e5](https://github.com/avaleror/rodeo-cli/commit/b2763e5bddeff31f3ea624867781b06d14605f2c))
* **aws:** managed-SG description used an em dash, which EC2's GroupDescription rejects ([96aca57](https://github.com/avaleror/rodeo-cli/commit/96aca573c7cd8044e81d114ffbeaae790873c751))
* **aws:** NVMe disk floor was per Harvester node, not per pool — 3x overshoot ([192ee1b](https://github.com/avaleror/rodeo-cli/commit/192ee1bd3ad4795a24d6fd5356bb115b8904343b))
* **catalog:** drop the virt-workshop-aws budget tier ([2339d96](https://github.com/avaleror/rodeo-cli/commit/2339d96cc3374f11dbde87750913d19d10fb01c7))
* cluster-registration-url import fails against self-signed Rancher on AWS ([334e986](https://github.com/avaleror/rodeo-cli/commit/334e986b7bc1301bae7294ab76e572ec89713e80))
* **profiles:** propagate mgmt_mac into cfg["vms"][name]["mac"] ([be4ed01](https://github.com/avaleror/rodeo-cli/commit/be4ed019fe55ebe5adae215fbc55cc07a41ad28a))
* **security:** harden NFS export, ISO verification, CI supply chain; add plan ownership ([#56](https://github.com/avaleror/rodeo-cli/issues/56)) ([ffcf5af](https://github.com/avaleror/rodeo-cli/commit/ffcf5af41c001d65fd286275d4e631509066510d))
* seed_lab() verifies its own copy; custom_scripts warns on empty dir ([cd6c95b](https://github.com/avaleror/rodeo-cli/commit/cd6c95b1f3ef613fe2da11625c43d5e97fa1fae2))
* **ssh:** make rodeo ssh host/vm actually authenticate the nested hop ([1d0ab91](https://github.com/avaleror/rodeo-cli/commit/1d0ab91920414e2b8be85abb24d18d46ccd13c03))
* **ssh:** quote remote_cmd across the host/vm nested hop ([91530b9](https://github.com/avaleror/rodeo-cli/commit/91530b9e388fb2af5f9375d164dcc981979879fd))
* **suse-edge:** reconcile the Elemental UI extension after the operator ([#55](https://github.com/avaleror/rodeo-cli/issues/55)) ([64a6d2b](https://github.com/avaleror/rodeo-cli/commit/64a6d2bfcfc4529ed4e4ddbe00f9883ef6ee933f))
* **suse-edge:** repair EIB build pipeline, ISO seeding, and registration config ([1a591dd](https://github.com/avaleror/rodeo-cli/commit/1a591dd699529213c983518870eaf01c7c00db93))
* **suse-edge:** restore elemental/ as the registration config directory ([49d0a56](https://github.com/avaleror/rodeo-cli/commit/49d0a56e30182b3d95b36074318145db85c82867))
* **suse-edge:** sync stale os-files/edge-definition.yaml text in success messages ([13009a2](https://github.com/avaleror/rodeo-cli/commit/13009a2810afba5ea4e575d7b3fd5cb7261d3b11))
* **up:** _infer_lab_profile picks the longest match, not the first ([c64d00d](https://github.com/avaleror/rodeo-cli/commit/c64d00d461242da0aa2246144e92c6253f454dde))
* **up:** hand ~/.rodeo back to the invoking user on AWS deploys too ([252fe7b](https://github.com/avaleror/rodeo-cli/commit/252fe7b6dc478cf3373610c50dd128da48b81601))
* **virt-workshop-aws:** size webserver-prod's boot disk from image virtualSize ([7a74ece](https://github.com/avaleror/rodeo-cli/commit/7a74ece727ea6170fab7277af34e2b4f50d00166))
* **virt-workshop-aws:** switch cached image to Leap-16.0-Minimal-VM Cloud build ([3eb5884](https://github.com/avaleror/rodeo-cli/commit/3eb5884c191556abedb9372f50bb9260ec530b0a))
* **virt-workshop-aws:** sync bundled custom_scripts/checks with suse-virt-workshop ([9dfeb04](https://github.com/avaleror/rodeo-cli/commit/9dfeb04fff31294afe5aa5518a2e5e55d881899f))


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
