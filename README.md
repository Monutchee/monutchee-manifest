# Monutchee OS (MNCos) Project collection


## Introduction

This is a project collection of monutchee for building yocto on Xilinx devices.



## Included project

| Project      | Link                              |
| :----------  | :------------------------------   |
| zudemo       | [zudemo](zudemo/README.md)        |
| kr260demo    | [kr260demo](kr260demo/README.md)  |
| msap1        | [msap1](msap1/README.md)          |



## Initialize the project

```bash
mkdir <your folder name>
cd <your folder name>
# Fetch the manifest and checkout the target release version
repo init -u https://github.com/Monutchee/monutchee-manifest.git -b <branch name> [ -m <release manifest>]
# Fetch all the source from the repositories in the manifest
repo sync


# OPTIONAL: Create a development branch on each repo
repo start <Your-Branch-Name> --all

# OPTIONAL: Set our repositories in branch HEAD rather than detached
repo forall -p meta-zuboard meta-mncos mncos-scripts -c 'git switch main'

# Set to my email
repo forall -p meta-zuboard meta-mncos mncos-scripts -c 'git config user.email 21245380+lesterlo@users.noreply.github.com'
```

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
4. `make_yocto.sh` consumes the mconf and RPU archives, runs the normal
   BitBake command, and publishes selected disk/boot/JTAG outputs as
   `<product>_yocto.tar.gz`.

Every archive includes a manifest and checksums, validates its product/stage,
and rejects unsafe archive paths. Use `--help` on each command for explicit
artifact paths and BitBake argument passthrough.

For coordinated feature testing, select matching repository branches without
editing the release manifests:

```bash
MANIFEST_BRANCH=feature/add_compile_cmd_script \
RPU_BRANCH=feature/add_compile_cmd_script \
META_MONUTCHEE_BRANCH=feature/add_compile_cmd_script \
  ./zudemo/setupWorkspace --workspace /path/to/workspace all
```

The build commands support `zudemo`, `kr260demo`, and `msap1` through product
profiles in `common/build/products`.

# Reference
[Xilinx yocto-manifests](https://github.com/Xilinx/yocto-manifests)
