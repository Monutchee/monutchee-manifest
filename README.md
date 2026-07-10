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

# Reference
[Xilinx yocto-manifests](https://github.com/Xilinx/yocto-manifests)
