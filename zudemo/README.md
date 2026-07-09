# zudemo project readme

## Introduction

zudemo

## Initialization

### APU

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| ZuBoardDemo_APU        | The APU (running on A53 core) source code                 | [Link](https://github.com/lesterlo/ZuBoardDemo_APU)  |

### RPU

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| ZuBoardDemo_RPU        | The RPU (running on R5 core) source code                  | [Link](https://github.com/lesterlo/ZuBoardDemo_RPU)  |

### PL

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| ZuBoardDemo_PL         | The PL (FPGA) source code                                 | [Link](https://github.com/lesterlo/ZuBoardDemo_PL)   |

### Yocto 
| Name                       | Description                                               | Link                                                     |
|----------------------------|-----------------------------------------------------------|----------------------------------------------------------|
| monutchee-manifest/zudemo  | Initialize the yocto building enviroment using repo tools | [Link](https://github.com/lesterlo/monutchee-manifest/tree/main/zudemo)   |
| meta-monutchee             | A yocto distro layer of the project                       | [Link](https://github.com/lesterlo/meta-monutchee)  

## Project dir Initialization

The following command will download and initialze a the workspace for you

```bash
curl -fsSL "https://raw.githubusercontent.com/lesterlo/monutchee-manifest/main/zudemo/zudemo-setupWorkspace" | bash -s -- all
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

For a more detailed build guide, Please refer to [zudemo-readme](https://github.com/lesterlo/meta-monutchee/blob/main/meta-zuboard/README.md) for main reference.


### Advance configuration

To config the yocto use the local compiled binary, insert the following line to `conf/local.conf`
```bash
APU_RPU_CTL_SRC = "local"
ZUBOARD_FIRMWARE_SRC = "local"
APU_RPU_CTL_GIT_BRANCH = "main"
```
