# Monutchee OS (MNCos) Project collection


## Introduction

This is a project collection of monutchee for building yocto on Xilinx devices.



## Included project

| Project      | Link                              |
| :----------  | :------------------------------   |
| zudemo       | [zudemo](zudemo/README.md)        |
| kr260demo    | [kr260demo](kr260demo/README.md)  |
| msap1        | [msap1](msap1/README.md)          |



## Initialize a product workspace

Each fresh product workspace contains three top-level directories:

- `applications/` is a repo client initialized from the product's
  `applications.xml`. It contains the APU, RPU, PL, and optional WEB
  repositories.
- `yocto-build/` is an independent repo client initialized from `yocto.xml`.
- `runtime-generated/` contains local build handoff files and is not managed
  by repo.

```bash
mkdir workspace && cd workspace
curl -fsSL "https://raw.githubusercontent.com/Monutchee/monutchee-manifest/main/<product>/setupWorkspace" | sh
```

Use `zudemo`, `kr260demo`, or `msap1` for `<product>`. The same command is
both installer and updater: in an empty directory it performs the complete
product setup; in an initialized workspace it refreshes only the generated
build scripts, updater, and workspace guidance. It does not switch or sync
component repositories during an update.

Test workflow changes from another manifest branch without changing the
installer URL:

```bash
curl -fsSL "https://raw.githubusercontent.com/Monutchee/monutchee-manifest/main/<product>/setupWorkspace" \
  | sh -s -- --branch feat/add_hex_on_artifact
```

On their first sync,
setup starts local `main` branches for the application repositories and
`yocto-build/sources/meta-monutchee`. Later setup runs preserve existing active
branches. Pinned Git submodules are initialized but remain outside the
applications manifest project set. Create or change one coordinated feature
branch across the application repositories with:

```bash
cd applications
repo start <branch> --all
repo checkout <branch>
```

Initialize a fresh directory. Setup deliberately refuses to adopt existing
standalone component clones inside `applications/` when that directory has no
`.repo`.

## Automated hardware-to-Yocto build

`setupWorkspace ... all` installs four product-aware commands in the workspace
root. The commands are also refreshed independently with the `scripts`
component.

```bash
./make_PL.sh
./make_mconf.sh
./make_RPU.sh
./make_yocto.sh
```

The CI handoff is deliberately split:

1. Export the bitstream-inclusive XSA from Vivado to
   `runtime-generated/bin_file/<ProjectPrefix>_PL.xsa`. `make_PL.sh` consumes
   that existing XSA without opening Vivado, then publishes
   `<product>_pl_sdtgen_<sha256[:6]>.tar.gz`, whose payload contains only
   SDTGen output.
   Use `--xsa FILE` when the exported XSA is stored elsewhere.
2. `make_mconf.sh` consumes the SDTGen archive and publishes
   `<product>_mconf_<sha256[:6]>.tar.gz`. It contains portable generated Yocto
   `conf` fragments, SDTGen files, and the generated `amd_platform_info.h` for
   each R5 core.
3. `make_RPU.sh` consumes only the raw XSA and mconf archive, creates the Vitis
   platform, and publishes `<product>_rpu_<sha256[:6]>.tar.gz`, containing only
   `R5c0.elf` and `R5c1.elf`. It does not source Yocto or run BitBake.
   After the platform has been created once, use `make_RPU.sh --elf-only` to
   rebuild both applications and publish the same artifact without recreating
   the platform or requiring the XSA.
4. `make_yocto.sh` consumes the mconf and RPU archives, runs the normal
   BitBake command, and publishes selected disk/boot/JTAG outputs as
   `<product>_yocto_<sha256[:6]>.tar.gz`.

Every archive includes a manifest and checksums, validates its product/stage,
rejects unsafe archive paths, and verifies a hash suffix when one is present.
By default, each consuming stage selects the newest matching hash-named input
artifact and warns when more than one match exists. Pass the explicit input
artifact option to pin a particular handoff. The RPU stage verifies that its
raw XSA matches the selected mconf lineage, and the Yocto stage rejects an RPU
artifact built from a different mconf artifact. Use `--help` on each command
for artifact paths and BitBake argument passthrough.

For coordinated feature testing, operate on the two repo clients separately:

```bash
(cd applications && repo start feature/add-compile-command-script --all)
(cd yocto-build && repo start feature/add-compile-command-script \
  sources/meta-monutchee)
```

The build commands support `zudemo`, `kr260demo`, and `msap1` through product
profiles in `common/build/products`.

# Reference
[Xilinx yocto-manifests](https://github.com/Xilinx/yocto-manifests)
