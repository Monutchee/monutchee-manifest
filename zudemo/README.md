# zudemo project readme

## Introduction

zudemo

## Initialization

### APU

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| ZuBoardDemo_APU        | The APU (running on A53 core) source code                 | [Link](https://github.com/Monutchee/ZuBoardDemo_APU)  |

### RPU

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| ZuBoardDemo_RPU        | The RPU (running on R5 core) source code                  | [Link](https://github.com/Monutchee/ZuBoardDemo_RPU)  |

### PL

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| ZuBoardDemo_PL         | The PL (FPGA) source code                                 | [Link](https://github.com/Monutchee/ZuBoardDemo_PL)   |

### Yocto 
| Name                       | Description                                               | Link                                                     |
|----------------------------|-----------------------------------------------------------|----------------------------------------------------------|
| monutchee-manifest/zudemo  | Initialize the yocto building enviroment using repo tools | [Link](https://github.com/Monutchee/monutchee-manifest/tree/main/zudemo)   |
| meta-monutchee             | A yocto distro layer of the project                       | [Link](https://github.com/Monutchee/meta-monutchee)  

## Project dir Initialization

The following command will download and initialze a the workspace for you

```bash
curl -fsSL "https://raw.githubusercontent.com/Monutchee/monutchee-manifest/main/zudemo/setupWorkspace" | bash -s -- all
```

## VS Code initialization

Add the following lines to `.vscode/settings.json` to prevent to many yocto files generate crash the vscode

<details>

<summary><b>VScode recommended setting </b></summary>

```
    "files.exclude": {
        "yocto-build/build/**": false
    },
    "search.exclude": {
        "yocto-build/build/**": true
    },
    "files.watcherExclude": {
        "**/yocto-build/build/**": true
    },
    "C_Cpp.files.exclude": {
        "**/yocto-build/build/**": true
    }
```

</details>


## Build Steps

For a more detailed build guide, Please refer to [zudemo-readme](https://github.com/Monutchee/meta-monutchee/blob/main/meta-zuboard/README.md) for main reference.

The workspace root also provides the automated build pipeline:

```bash
./make_PL.sh
./make_mconf.sh
./make_RPU.sh
./make_yocto.sh
```

First export a bitstream-inclusive XSA from Vivado as
`runtime-generated/bin_file/ZuBoardDemo_PL.xsa`. The PL stage consumes that file
without opening Vivado and packages only SDTGen output in
`zudemo_pl_sdtgen.tar.gz`. Use `make_PL.sh --xsa FILE` for a different XSA
location. The mconf stage also generates and packages both per-core
`amd_platform_info.h` files. The RPU stage consumes those headers and the XSA
without invoking Yocto or BitBake. The Yocto stage consumes `zudemo_mconf.tar.gz` and
`zudemo_rpu.tar.gz`; the latter contains only the two
R5 ELF files. All archives are under `runtime-generated/bin_file`.

The ZUBoard Vivado project requires the
`avnet.com:zuboard_1cg:part0:1.0` board definition to be installed on the build
host when creating the XSA.


### Advance configuration

To config the yocto use the local compiled binary, insert the following line to `conf/local.conf`
```bash
APU_RPU_CTL_SRC = "local"
ZUBOARD_FIRMWARE_SRC = "local"
APU_RPU_CTL_GIT_BRANCH = "main"
```
