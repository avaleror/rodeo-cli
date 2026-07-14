# Changelog

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
