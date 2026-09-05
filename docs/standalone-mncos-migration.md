# Reinitialize the Yocto part for standalone MNCOS

The MSAP1, KR260 demo and ZU demo manifests use `sources/openembedded-core`
and `sources/bitbake`, both at Yocto 5.0.18, with the standalone distro in
`meta-monutchee/meta-mncos`. Update the manifest and layer repositories together.

## Synchronize only Yocto

From a checkout of this repository, select the workspace **root**, not its
`yocto-build` subdirectory:

```sh
bash msap1/setupWorkspace --workspace /opt/monutchee/project/msap1 yocto scripts
```

Use `yocto` alone to synchronize only the Yocto repositories; `scripts` also
refreshes the local build toolkit and report collector. Applications are not
synchronized by either selector. Substitute the other product wrapper and
workspace root for KR260 demo or ZU demo.

The command runs `repo init` and `repo sync` under `yocto-build`. It does not
delete or reset the existing BitBake build directory. It also refreshes the
workspace guidance and launcher. Existing editor settings are user-owned;
replace their old ignored Poky repository entry with the two split repositories.

By default, source synchronization uses GitHub `main`. A locally edited setup
script does not make uncommitted manifest or layer changes available to repo.
Publish coordinated revisions before using the normal synchronization path.
For branch testing, use the shared script with explicit branch selection:

```sh
META_MONUTCHEE_BRANCH=codex/standalone-mncos \
  bash common/setupWorkspace --product msap1 \
  --workspace /opt/monutchee/project/msap1 \
  --branch codex/standalone-mncos yocto scripts
```

The branches must exist on the configured remotes. `MANIFEST_REPO_URL` can
select a local Git repository containing **committed** manifest changes;
layer remotes are still controlled by that manifest. Setup never publishes
changes automatically.

## Create fresh build configuration

Keep the old build directory and previous source revisions for rollback.
After source synchronization, move the old `yocto-build/build` directory to
an unused backup name. Preserve the separate download/sstate cache directories.
Then initialize from a fresh shell:

```sh
cd /opt/monutchee/project/msap1/yocto-build
source ./setupSDK --product msap1 build
bitbake-layers show-layers
```

Transfer reviewed settings from the previous `local.conf`: source selections,
cache locations and host build limits. Do not copy old `bblayers.conf` or edit
generated machine files. The setup helper rejects stale Poky layer paths.

Use the normal machine-configuration and image workflow to regenerate/install
the hardware configuration. Existing PL and RPU artifacts can be reused when
their XSA/OpenAMP provenance still matches:

```sh
cd /opt/monutchee/project/msap1
./mnc mconf build
./mnc yocto build
```

MSAP1's RPU and mconf artifacts are independent after the PL handoff. The demo
products retain their mconf-to-RPU dependency and need `./mnc RPU build` after
regenerating mconf.

## Policy and reports

`MNCOS_HEADLESS ?= "1"` is the default. Set it to `"0"` in build/product
configuration to retain the previous graphics policy. Capture, OpenAMP, DMA
and remote web access are preserved; capture may still require DRM helpers.
Vendor firmware and host build tools are outside this Linux graphics policy.

Normal `mnc yocto build` deliveries include CVE JSON, SPDX, package manifests,
kernel configuration, resolved manifests and source revisions under `metadata/`.
Missing required reports fail delivery assembly; CVE findings are initially
report-only. Do not treat dirty-tree source records or incomplete firmware CVE
coverage as a reproducible, fully reviewed production release.

The first CVE check downloads the NVD database and can take substantial time.
Preserve the download cache, including its `CVE_CHECK` directory, so later
builds can reuse and incrementally update it. An optional `NVDCVE_API_KEY` in
private build configuration enables NVD's higher request limits.

Record matching source revisions before and after migration. Validate all three
products, main/flash/Station outputs as applicable, SDK/eSDK generation, and
hardware boot/acquisition/network behavior before release. Roll back both source
repositories together and restore the preserved build configuration if needed.

See [implementation validation](standalone-mncos-validation.md) for completed
checks and the remaining release acceptance work.
