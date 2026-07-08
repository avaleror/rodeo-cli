# Changelog

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
