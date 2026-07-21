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

Each product uses one multi-manifest repo client. The workspace-root `main.xml`
manages the product APU, RPU, PL, and optional WEB repositories. A Yocto
submanifest uses `yocto.xml` and places all Yocto files under `yocto-build/`.

```bash
mkdir workspace && cd workspace
curl -fsSL "https://raw.githubusercontent.com/Monutchee/monutchee-manifest/main/<product>/setupWorkspace" | bash -s -- all
```

Use `zudemo`, `kr260demo`, or `msap1` for `<product>`. On their first sync,
setup starts local `main` branches for the top-level product repositories and
`yocto-build/sources/meta-monutchee`. Later setup runs preserve existing active
branches. Pinned Git submodules are initialized but remain outside the root
manifest project set. Create or change one coordinated feature branch across
the top-level product repositories with:

```bash
repo start <branch> --all --this-manifest-only
repo checkout <branch> --this-manifest-only
```

Repo stores the Yocto submanifest metadata beneath the root `.repo`; there is
intentionally no `yocto-build/.repo`. To operate only on Yocto projects, use
`repo --submanifest-path=yocto-build <command> --this-manifest-only --no-outer-manifest`.

Initialize a fresh directory. Setup deliberately refuses to adopt existing
standalone component clones when the workspace has no root `.repo`.

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
   `<product>_pl_sdtgen.tar.gz`, whose payload contains only SDTGen output.
   Use `--xsa FILE` when the exported XSA is stored elsewhere.
2. `make_mconf.sh` consumes the SDTGen archive and publishes
   `<product>_mconf.tar.gz`. It contains portable generated Yocto `conf`
   fragments, SDTGen files, and the generated `amd_platform_info.h` for each
   R5 core.
3. `make_RPU.sh` consumes only the raw XSA and mconf archive, creates the Vitis
   platform, and publishes `<product>_rpu.tar.gz`, containing only `R5c0.elf`
   and `R5c1.elf`. It does not source Yocto or run BitBake.
   After the platform has been created once, use `make_RPU.sh --elf-only` to
   rebuild both applications and publish the same artifact without recreating
   the platform or requiring the XSA.
4. `make_yocto.sh` consumes the mconf and RPU archives, runs the normal
   BitBake command, and publishes selected disk/boot/JTAG outputs as
   `<product>_yocto.tar.gz`.

Every archive includes a manifest and checksums, validates its product/stage,
and rejects unsafe archive paths. Use `--help` on each command for explicit
artifact paths and BitBake argument passthrough.

For coordinated feature testing, start one branch across the root manifest
projects. Operate on the Yocto submanifest separately when it needs the same
branch:

```bash
repo start feature/add-compile-command-script --all --this-manifest-only
repo --submanifest-path=yocto-build start feature/add-compile-command-script \
  sources/meta-monutchee --this-manifest-only --no-outer-manifest
```

The build commands support `zudemo`, `kr260demo`, and `msap1` through product
profiles in `common/build/products`.

# Reference
[Xilinx yocto-manifests](https://github.com/Xilinx/yocto-manifests)
