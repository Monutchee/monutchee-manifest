# kr260demo project readme

## Introduction

kr260demo

## Initialization

### APU

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| KR260Demo_APU        | The APU (running on A53 core) source code                 | [Link](https://github.com/Monutchee/KR260Demo_APU)  |

### RPU

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| KR260Demo_RPU        | The RPU (running on R5 core) source code                  | [Link](https://github.com/Monutchee/KR260Demo_RPU)  |

### PL

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| KR260Demo_PL         | The PL (FPGA) source code                                 | [Link](https://github.com/Monutchee/KR260Demo_PL)   |

### Yocto 
| Name                       | Description                                               | Link                                                     |
|----------------------------|-----------------------------------------------------------|----------------------------------------------------------|
| monutchee-manifest/kr260demo  | Initialize the yocto building enviroment using repo tools | [Link](https://github.com/Monutchee/monutchee-manifest/tree/main/kr260demo)   |
| meta-monutchee             | A yocto distro layer of the project                       | [Link](https://github.com/Monutchee/meta-monutchee)  


### Project dir Initialization

The following command will download and initialze a the workspace for you

```bash
curl -fsSL "https://raw.githubusercontent.com/Monutchee/monutchee-manifest/main/kr260demo/kr260demo-setupWorkspace" | bash -s -- all
```

### VS Code initialization

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

## Build Steps

For a more detailed build guide, Please refer to [kr260demo-readme](https://github.com/Monutchee/meta-monutchee/blob/main/meta-kr260demo/README.md) for main reference.

## Advance configuration

To config the yocto use the local compiled binary, insert the following line to `conf/local.conf`
```bash
APU_RPU_CTL_SRC = "local"
ZUBOARD_FIRMWARE_SRC = "local"
APU_RPU_CTL_GIT_BRANCH = "main"
```
