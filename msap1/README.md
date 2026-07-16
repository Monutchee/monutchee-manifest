# msap1 project readme

## Introduction

msap1

## Initialization

### APU

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| MSAP1_APU        | The APU (running on A53 core) source code                 | [Link](https://github.com/Monutchee/MSAP1_APU)  |

### RPU

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| MSAP1_RPU        | The RPU (running on R5 core) source code                  | [Link](https://github.com/Monutchee/MSAP1_RPU)  |

### PL

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| MSAP1_PL         | The PL (FPGA) source code                                 | [Link](https://github.com/Monutchee/MSAP1_PL)   |

### Yocto 
| Name                       | Description                                               | Link                                                     |
|----------------------------|-----------------------------------------------------------|----------------------------------------------------------|
| monutchee-manifest (This)  | Initialize the yocto building enviroment using repo tools | [Link](https://github.com/Monutchee/monutchee-manifest/tree/main/msap1)   |
| meta-monutchee             | A yocto distro layer of the project                       | [Link](https://github.com/Monutchee/meta-monutchee)  

## Project dir Initialization

Until the APU, RPU, and PL repositories exist, initialize the Yocto checkout
and install the shared build scripts only:

```bash
curl -fsSL "https://raw.githubusercontent.com/Monutchee/monutchee-manifest/main/msap1/setupWorkspace" | bash -s -- yocto scripts
```

Once all three component repositories are available, replace `yocto scripts`
with `all` to clone and configure the complete workspace.

Every MSAP1 `setupWorkspace` invocation also refreshes the workspace-root
`AGENTS.md` from `msap1/AGENTS.md`. The generated copy provides cross-repository
guidance for AI coding tools; edit the manifest source rather than the generated
workspace file.

# VS Code initialization

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
    },
    "git.ignoredRepositories": [
        "yocto-build/sources/meta-arm",
        "yocto-build/sources/meta-kria",
        "yocto-build/sources/meta-openamp",
        "yocto-build/sources/meta-openembedded",
        "yocto-build/sources/meta-virtualization",
        "yocto-build/sources/meta-xilinx",
        "yocto-build/sources/poky"
    ],
    "git.scanRepositories": [
        "yocto-build/sources/meta-monutchee"
    ]
```

</details>



# Build Steps

For a more detailed build guide, Please refer to [msap1-readme](https://github.com/Monutchee/meta-monutchee/blob/main/meta-msap1/README.md) for main reference.

### Advance configuration

The generated MSAP1 build template already selects the local APU checkout. To
override it in `conf/local.conf`, use:
```bash
APU_RPU_CTL_SRC = "local"
APU_RPU_CTL_GIT_BRANCH = "main"
APU_RPU_CTL_LOCAL_DIR = "${TOPDIR}/../../MSAP1_APU"
```
