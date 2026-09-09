# Monutchee shared workspace tools

This repository maintains one shared setup implementation and vendor build
backends. Project manifests, profiles, hardware definitions and project guidance
live in independent `<project>-manifest` repositories, each with its own access
permissions.

```text
monutchee-manifest/
├── common/
│   ├── bootstrap
│   ├── setupWorkspace
│   ├── tests/
│   └── build/
│       └── xilinx-initialization/
└── projects/                         ignored local checkouts or symlinks
    └── <project>-manifest/
```

## Initialize with curl

Use the public bootstrap URL for both public and private projects:

Replace `<project-name>` with the selected project's name:

```sh
PROJECT_NAME="<project-name>"
curl -fsSL \
  "https://raw.githubusercontent.com/Monutchee/monutchee-manifest/main/common/bootstrap" |
  sh -s -- \
    --project "$PROJECT_NAME" \
    --manifest-url "git@github.com:Monutchee/${PROJECT_NAME}-manifest.git" \
    --workspace "/opt/monutchee/project/${PROJECT_NAME}"
```

No shared checkout is required. The POSIX shell bootstrap starts Bash, clones
the shared tools and selected project into temporary directories, then invokes
the generic setup script. Temporary sources are removed on completion or
failure; the installed workspace remains. Each invocation fetches the current
selected branches, so rerunning the command also refreshes build scripts.

The bootstrap itself must be publicly readable for unauthenticated `curl`.
Private project files are fetched through Git using your SSH identity or HTTPS
credential helper. A private project's raw `setupWorkspace` URL cannot be read
by unauthenticated `curl`; use this public entry point instead. The project
wrappers remain local-checkout entry points.

`--shared-branch NAME` selects the shared tooling branch; `--branch NAME`
selects the project manifest branch. If testing an unpublished-to-main change,
use its pushed branch in both the raw bootstrap URL and `--shared-branch`.
`--shared-url` overrides the shared Git repository. Passing `scripts` installs
only the toolkit; this curl entry point still fetches its source repositories.
For offline updates, use a local checkout as described below.

The bootstrap and selected source branches must be committed and pushed before
the remote command can use them. Git and Bash must already be installed; full
Xilinx initialization also requires Google's `repo` tool.

## Select a project

Clone a project into `projects/` or beside this repository. Discovery recognizes
`*-manifest` directories containing `product.conf`, deduplicates symlinks and
never queries a remote registry or executes configurations while listing.

```sh
bash common/setupWorkspace --list
bash common/setupWorkspace --project <project> --workspace /opt/monutchee/project/<project-name> scripts
bash common/setupWorkspace --project <project> --workspace /opt/monutchee/project/<project-name> all
```

Use `--manifest-dir /absolute/path/to/project-manifest` for other layouts or to
resolve duplicate project names. `MONUTCHEE_PROJECTS_DIR` or `--projects-dir`
changes the local project directory. The default sibling search still applies.

The project's `setupWorkspace` wrapper delegates to the same common script.
`MONUTCHEE_MANIFEST_ROOT` tells the wrapper where to find this shared checkout
when it is neither a sibling nor the parent of `projects/`.

## Fetch and authentication

An explicitly requested missing project can be cloned:

```sh
bash common/setupWorkspace --project <project> --fetch \
  --manifest-url git@github.com:Monutchee/<project>-manifest.git \
  --workspace /opt/monutchee/project/<project-name> scripts
```

Without `--manifest-url`, fetching uses
`${MONUTCHEE_PROJECT_REMOTE_BASE:-https://github.com/Monutchee}/<project>-manifest.git`.
Existing local checkouts are never pulled or switched implicitly. Synchronization
uses the selected project's actual `origin`, unless overridden with
`--manifest-url`. `--branch` selects its manifest branch, defaulting to `main`;
shared scripts always come from the current shared checkout.

```sh
bash common/setupWorkspace --project <project> --check-access
```

Fetch/access checks report a terminal error and return nonzero if the remote or
branch is unavailable. The error explains that access rights, credentials, URL,
network connectivity or an unpublished branch may be responsible. A failed
clone is cleaned up. Component sync failures retain the underlying Git/repo
error and add the same guidance. Configure SSH keys or an HTTPS credential
helper for private repositories; credentials do not belong in project files.

Commit and push each new project's manifests before `all`/component setup:
Google's `repo` initializes from the remote, so uncommitted local XML changes
are not synchronized. Explicit `scripts` setup works offline and installs the
local profile and resources. Fetching does not publish anything.

## Project contract

A project contains:

```text
<project>-manifest/
├── setupWorkspace               thin Bash wrapper
├── product.conf                 trusted Bash configuration
├── applications.xml             Xilinx applications repo manifest
├── yocto.xml                    Xilinx Yocto repo manifest
├── definition/                  optional project resources
└── AGENTS.md                    optional generated-workspace guidance
```

`product.conf` must define `PRODUCT` and `VENDOR`. Identifiers use lowercase
letters, digits and single internal hyphens. The filename's project identifier
must match `PRODUCT` when using `--project`. Configuration is executable trusted
code, like the project's setup wrapper; only select checkouts you trust.

All existing project profiles use `VENDOR="xilinx"`. Their remaining settings
include component repository names, the machine/image targets, hardware handoff
paths and `MNC_CHAIN`. Those values belong in the project repository.

## Vendor backends

`common/setupWorkspace` loads `common/build/${VENDOR}-initialization/workspace.sh`
and calls `initialize_workspace` with the selected component arguments. It
provides `SHARED_ROOT`, `VENDOR_DIR`, `MANIFEST_DIR`, `MONUTCHEE_PRODUCT`,
`WORKSPACE_ROOT`, `MANIFEST_REPO_URL`, `MANIFEST_BRANCH`, the loaded project
configuration, and the `fail`/`check_remote` helpers.

The backend owns component parsing, vendor initialization and installation.
Future NXP or STM32 support can add a backend without changing project discovery.
These vendors are not implemented yet; unknown vendors fail explicitly.

The Xilinx backend contains the current Vivado/Vitis, OpenAMP, Yocto, artifact,
Station deployment and `mnc` tooling. It installs the runtime into the existing
`.monutchee-build/` layout, so `./mnc` and stage commands remain compatible.
Only the selected project's profile/resources are installed. Tests and backend
setup functions are not copied into generated workspaces.

Xilinx components: `all`, `yocto`, `apu`, `rpu`, `pl`, `web`, `scripts`.
With no component, new workspaces initialize fully and existing workspaces
refresh scripts. Presets and existing editor settings are preserved.

## Tests

```sh
python3 -m unittest discover -s common/tests
python3 -m unittest discover -s common/build/xilinx-initialization/tests
```

Product-specific integration tests and historical product documentation moved
with the project repositories. Run their tests as documented there.

This migration moves current files only. It does not rewrite Git history or
change repository visibility. Product-specific Yocto layers are unchanged.

## Query build status

```sh
./mnc RPU status
./mnc mconf status
./mnc yocto status
./mnc all status
./mnc --from RPU all status
```

Queries use the target selected in `MncBuildPreset.yaml`. RPU reports packaged
firmware and exported ELF consistency; mconf checks installed configuration;
Yocto reports its packaged image and consumed RPU/mconf artifacts. All three
compare recorded XSA, contract and dependency digests where applicable. They
read archive manifests without extracting or validating the full payload, and
cannot establish whether current source edits have been built. Artifact dates
are successful package dates, not the result of the latest build attempt.

These three queries do not launch Vitis/BitBake, acquire the build lock, or
create build directories/reports. `all status` also calls the existing PL
query, which opens Vivado read-only and writes its usual query log. HLS is
reported as having no status implementation. Unsupported target stages are
reported and skipped; `--from`/`--to` select a chain range. Missing, stale or
invalid artifacts are verdicts, not command failures. Query execution failures
produce a nonzero chain exit after the remaining queries have run.

## Discover workspace commands and hardware

`./mnc help` (also `--help`) explains workspace commands, stage commands and
options. `./mnc <stage> help` gives that stage's complete argument reference.
`./mnc list-build-target` lists enabled hardware targets with their machine and
supported stages, followed by unavailable definitions and their reasons.
Discovery works even when the
current preset selects an unavailable target; edit `build_target` in
`MncBuildPreset.yaml` to select a listed target. `./mnc --list` remains the
selected-workspace/stage-script overview.

`openTmux` creates root, Yocto, APU, RPU, PL and optional WEB windows. It no
longer creates a TFTP window. Existing tmux sessions are attached unchanged.
