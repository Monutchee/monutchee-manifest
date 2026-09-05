# Standalone MNCOS implementation validation

Validation used an isolated workspace under `/tmp/mncos-migration-work`.
The existing `/opt/monutchee/project/msap1/yocto-build` configuration was not
reinitialized or edited. Its hardware configuration and source inputs were
read for comparison; download and sstate caches were copied for build testing.

## Completed checks

- Manifest/workflow suite: 166 tests passed, including report collection,
  product manifests, toolkit installation and existing deployment behavior.
- Layer suite: 19 tests passed, including the real BitBake configuration
  parser, headless feature removal after later appends, graphics opt-out,
  native/SDK isolation, invalid values, renamed graphics packages, missing
  reports, capture helper dependencies and Bash/Zsh setup.
- Full isolated `msap1-image` build passed: all 9,915 tasks succeeded, with
  7,359 reused. Its 57 warnings were CVE findings under the report-only policy.
  The actual headless rootfs guard ran successfully. The image contains 386
  binary packages, including the APU, web, DMA and DFX packages.
- Image CVE JSON (127 recipe records), SPDX archive, package manifest, deployed
  kernel configuration, and kernel/U-Boot/TF-A CVE reports were generated.
  Delivery metadata collection passed using those real reports.
- MSAP1 Station archive generation and the existing `mnc-artifact.py verify`
  command passed, including manifest and payload digest validation.
- `msap1-production-flash-image` passed all 9,938 tasks, including its headless
  rootfs guard, disabled-development-account check and release reports. Its
  incremental multiconfig run emitted deferred-task recovery warnings; BitBake
  recovered automatically and every task succeeded.
- Fresh OE-Core/BitBake setup and global configuration parsed for MSAP1,
  KR260 demo and ZU demo. The demo checks used their bootstrap machine;
  they do not replace builds using regenerated hardware configuration.
- MSAP1 image, kernel and native metadata parsed with headless enabled.
  Image and kernel metadata also parsed with graphics policy restored.
  The standalone FSBL multiconfig retained its vendor distro and excluded
  the Linux MNCOS policy. Invalid `MNCOS_HEADLESS` failed as expected.
- Effective non-graphics distro and SDK settings matched the previous
  configuration, including identity/version, systemd, RPM, SDK paths,
  boot dependencies, strict checksums and hash equivalence. Build-specific
  hash-server socket paths were normalized for comparison.
- The actual AMD 6.18.10 kernel Kconfig resolved the new fragment with all
  20 selected display/GPU symbols disabled. Checked capture, media, Xilinx
  framebuffer DMA, DMA/CMA, remoteproc and RPMsg settings were unchanged.
  DRM core and DisplayPort receiver helpers remained enabled as required.
  The real BitBake kernel configuration and configuration-check tasks also
  passed, and their result matched these protected settings.
- Kernel compile/link exposed a missing vendor Kconfig dependency: DisplayPort
  capture's DRM protocol code also needs `DRM_KMS_HELPER`. The layer now carries
  that dependency fix. A separate complete kernel `Image` compile/link passed
  with the correction and all 20 selected display/GPU symbols disabled.
  The corrected image build also passed BitBake's kernel patch, configuration
  and compile tasks.
- The delivery provenance collector read all 14 actual source repositories
  in the validation workspace, including three with submodule records.
  Resolved manifest export also passed for the existing Yocto and application
  repo clients (with tracing disabled to avoid writing to those checkouts).
- Shell syntax, Python compilation and both repositories' `git diff --check`
  passed.

OE-Core was checked at `ece80784b493c8b7493478fa2ba0dc1d6d80aa79` and
BitBake at `82abbfcdbda949851a03bb2cb2049ea689564ad6`, both selected by
`refs/tags/yocto-5.0.18`.

The first NVD database download completed successfully. The reusable copy is
at `/tmp/mncos-migration-work/reused-cache/downloads/CVE_CHECK/nvdcve_2-2.db`.
To avoid downloading it again, preserve this file before cleaning the temporary
validation workspace and copy it, retaining its modification time, into your
chosen download cache's `CVE_CHECK` directory. Do not overwrite a newer database.

The layer's parser tests require BitBake's Python modules and OE-Core:

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/path/to/sources/bitbake/lib \
MNCOS_TEST_OECORE=/path/to/sources/openembedded-core \
python3 -m unittest discover -s scripts/tests -v
```

## Release acceptance still required

Complete normal and production-flash image builds for KR260 demo and ZU demo,
and SDK/eSDK builds. Inspect their packages
and reports, and exercise serial boot, storage/network,
FPGA overlays, RPU/OpenAMP, acquisition/capture and web services on hardware.
Metadata and Kconfig checks do not establish these runtime results.
