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

### WEB

| Name                   | Description                                               | Link                                                 |
|------------------------|-----------------------------------------------------------|------------------------------------------------------|
| MSAP1_WEB        | The React/TypeScript product frontend                     | [Link](https://github.com/Monutchee/MSAP1_WEB)  |

### Yocto 
| Name                       | Description                                               | Link                                                     |
|----------------------------|-----------------------------------------------------------|----------------------------------------------------------|
| monutchee-manifest (This)  | Initialize the yocto building enviroment using repo tools | [Link](https://github.com/Monutchee/monutchee-manifest/tree/main/msap1)   |
| meta-monutchee             | A yocto distro layer of the project                       | [Link](https://github.com/Monutchee/meta-monutchee)  

## Project dir Initialization

Run the following command from a fresh workspace directory:

```bash
curl -fsSL "https://raw.githubusercontent.com/Monutchee/monutchee-manifest/main/msap1/setupWorkspace" | bash -s -- all
```

This creates `applications/`, `yocto-build/`, and `runtime-generated/`.
`applications/` is a repo client initialized from `msap1/applications.xml`
and contains `MSAP1_APU`, `MSAP1_RPU`, `MSAP1_PL`, and `MSAP1_WEB`.
`yocto-build/` is a separate repo client initialized from `msap1/yocto.xml`.
Fresh application checkouts and `yocto-build/sources/meta-monutchee` are
automatically started on local `main` branches; later setup runs preserve
active branches.

APU's OpenAMP-helper, WebEngine library, and Glaze repositories and RPU's
OpenAMP-helper repository remain pinned Git submodules. They are fetched during
sync but are not included in applications `--all` branch operations.
`MSAP1_WEB` is the separate product frontend and is a top-level applications
manifest project.

```bash
cd applications
repo start <branch> --all
repo checkout <branch>
```

Use a fresh directory for the first run. Existing standalone component clones
inside `applications/` are not adopted automatically. The `yocto` and
`scripts` selectors remain available when only the Yocto repo client or build
wrappers are needed.

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



## Build Steps


### yocto
Get the newest `msap1_yocto_<sha256[:6]>.tar.gz` Yocto artifact. The suffix is
the first six hexadecimal characters of that archive's SHA-256.

```bash
artifact="$(ls -1t msap1_yocto_*.tar.gz | head -n 1)"
tar -xzvf "${artifact}" && (cd monutchee-artifact-v1/payload/msap1_yocto/jtag && xsdb load-jtag-image.tcl 127.0.0.1 192.168.61.147)
```

For a more detailed build guide, Please refer to [msap1-readme](https://github.com/Monutchee/meta-monutchee/blob/main/meta-msap1/README.md) for main reference.

### Advance configuration

The generated MSAP1 build template already selects the local APU checkout. To
override it in `conf/local.conf`, use:
```bash
APU_RPU_CTL_SRC = "local"
APU_RPU_CTL_GIT_BRANCH = "main"
APU_RPU_CTL_LOCAL_DIR = "${TOPDIR}/../../applications/MSAP1_APU"
```
