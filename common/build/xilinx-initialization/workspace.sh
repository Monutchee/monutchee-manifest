#!/usr/bin/env bash
# Xilinx workspace backend, sourced by common/setupWorkspace.

APPLICATIONS_DIR_NAME="${APPLICATIONS_DIR_NAME:-applications}"
YOCTO_DIR_NAME="${YOCTO_DIR_NAME:-yocto-build}"

# Per-product values; filled in by configure_product().
TMUX_SESSION=""
YOCTO_BUILD_DIR=""
APU_PROJECT_NAME=""
RPU_PROJECT_NAME=""
PL_PROJECT_NAME=""
WEB_PROJECT_NAME=""
META_MONUTCHEE_BRANCH="${META_MONUTCHEE_BRANCH:-main}"
PRODUCT_BRANCH="${PRODUCT_BRANCH:-main}"

DO_ALL=false
DO_YOCTO=false
DO_APU=false
DO_RPU=false
DO_PL=false
DO_WEB=false
DO_SCRIPTS=false
AUTO_MODE=false
AUTO_EXISTING_WORKSPACE=false

usage() {
    printf 'Xilinx components: all, yocto, apu, rpu, pl, web, scripts\n'
}

select_all() {
    DO_ALL=true
    DO_YOCTO=true
    DO_APU=true
    DO_RPU=true
    DO_PL=true
    DO_SCRIPTS=true
}

parse_arguments() {
    local selected=0

    while (($# > 0)); do
        case "$1" in
            all|ALL|All)
                select_all
                selected=1
                shift
                ;;

            yocto|YOCTO|Yocto)
                DO_YOCTO=true
                selected=1
                shift
                ;;

            apu|APU)
                DO_APU=true
                selected=1
                shift
                ;;

            rpu|RPU)
                DO_RPU=true
                selected=1
                shift
                ;;

            pl|PL)
                DO_PL=true
                selected=1
                shift
                ;;

            web|WEB|Web)
                DO_WEB=true
                selected=1
                shift
                ;;

            scripts|SCRIPTS|Scripts)
                DO_SCRIPTS=true
                selected=1
                shift
                ;;

            -h|--help)
                usage
                exit 0
                ;;

            *)
                printf 'Error: unknown option or component: %s\n\n' "$1" >&2
                usage >&2
                exit 2
                ;;
        esac
    done

    if ((selected == 0)); then
        AUTO_MODE=true
    fi
}

workspace_recorded_product() {
    local workspace_marker="${WORKSPACE_ROOT}/.monutchee-workspace"
    local yocto_marker="${WORKSPACE_ROOT}/${YOCTO_DIR_NAME}/.mncos-product"

    if [[ -f "${workspace_marker}" ]]; then
        head -n 1 -- "${workspace_marker}"
    elif [[ -f "${yocto_marker}" ]]; then
        head -n 1 -- "${yocto_marker}"
    fi
}

workspace_is_initialized() {
    [[ -f "${WORKSPACE_ROOT}/.monutchee-workspace" ]] || \
        {
            [[ -d "${WORKSPACE_ROOT}/${APPLICATIONS_DIR_NAME}/.repo" ]] && \
            [[ -d "${WORKSPACE_ROOT}/${YOCTO_DIR_NAME}/.repo" ]]
        }
}

select_automatic_mode() {
    local recorded_product=""

    [[ "${AUTO_MODE}" == true ]] || return 0

    recorded_product="$(workspace_recorded_product)"
    if [[ -n "${recorded_product}" && \
          "${recorded_product}" != "${MONUTCHEE_PRODUCT}" ]]; then
        printf 'Error: workspace belongs to product %s, not %s.\n' \
            "${recorded_product}" "${MONUTCHEE_PRODUCT}" >&2
        exit 2
    fi

    if workspace_is_initialized; then
        AUTO_EXISTING_WORKSPACE=true
        DO_SCRIPTS=true
        MONUTCHEE_SCRIPTS_ONLY_UPDATE=true
        printf 'Existing %s workspace detected; refreshing build scripts and guidance only.\n\n' \
            "${MONUTCHEE_PRODUCT}"
    else
        select_all
        printf 'No initialized %s workspace detected; performing the complete setup.\n\n' \
            "${MONUTCHEE_PRODUCT}"
    fi
}

record_workspace_product() {
    local destination="${WORKSPACE_ROOT}/.monutchee-workspace"
    local staging="${destination}.tmp.$$"

    printf '%s\n' "${MONUTCHEE_PRODUCT}" > "${staging}"
    chmod 0644 -- "${staging}"
    mv -f -- "${staging}" "${destination}"
}

configure_product() {
    # product.conf is loaded by the generic frontend; use its explicit repo names.
    : "${PROJECT_PREFIX:?product.conf requires PROJECT_PREFIX}"
    : "${APU_REPO_DIR:?product.conf requires APU_REPO_DIR}"
    : "${RPU_REPO_DIR:?product.conf requires RPU_REPO_DIR}"
    : "${PL_REPO_DIR:?product.conf requires PL_REPO_DIR}"
    TMUX_SESSION="${MONUTCHEE_PRODUCT}"
    WORKSPACE_BUILD_TARGET=""
    YOCTO_BUILD_DIR="build"
    if [[ -n "${DEFAULT_BUILD_TARGET:-}" ]]; then
        local resolved_target
        resolved_target="$(python3 "${VENDOR_DIR}/build_target.py" \
            --definitions "${MANIFEST_DIR}/definition/targets.json" \
            --preset "${WORKSPACE_ROOT}/MncBuildPreset.yaml" \
            --default "${DEFAULT_BUILD_TARGET}")" || return 1
        eval "${resolved_target}" # Fixed keys and shell-quoted values from build_target.py.
        WORKSPACE_BUILD_TARGET="${MNC_BUILD_TARGET}"
        YOCTO_BUILD_DIR="build-${MACHINE}"
    fi
    APPLICATIONS_MANIFEST_FILE="${APPLICATIONS_MANIFEST_FILE:-applications.xml}"
    YOCTO_MANIFEST_FILE="${YOCTO_MANIFEST_FILE:-yocto.xml}"
    APU_PROJECT_NAME="${APU_REPO_DIR}"
    RPU_PROJECT_NAME="${RPU_REPO_DIR}"
    PL_PROJECT_NAME="${PL_REPO_DIR}"
    WEB_PROJECT_NAME="${WEB_REPO_DIR:-}"
    if [[ "${DO_WEB}" == true && -z "${WEB_PROJECT_NAME}" ]]; then
        printf 'Error: project %s does not define a WEB repository.\n' "${MONUTCHEE_PRODUCT}" >&2
        exit 2
    fi
}

create_runtime_directories() {
    mkdir -p -- \
        "${WORKSPACE_ROOT}/runtime-generated${WORKSPACE_BUILD_TARGET:+/${WORKSPACE_BUILD_TARGET}}/vivado_SDT_out" \
        "${WORKSPACE_ROOT}/runtime-generated${WORKSPACE_BUILD_TARGET:+/${WORKSPACE_BUILD_TARGET}}/bin_file"

    printf 'Runtime directories are ready:\n'
    printf '  %s\n' "${WORKSPACE_ROOT}/runtime-generated${WORKSPACE_BUILD_TARGET:+/${WORKSPACE_BUILD_TARGET}}/vivado_SDT_out"
    printf '  %s\n' "${WORKSPACE_ROOT}/runtime-generated${WORKSPACE_BUILD_TARGET:+/${WORKSPACE_BUILD_TARGET}}/bin_file"
}

install_workspace_guidance() {
    if [[ -f "${MANIFEST_DIR}/AGENTS.md" ]]; then
        cp -- "${MANIFEST_DIR}/AGENTS.md" "${WORKSPACE_ROOT}/AGENTS.md"
        chmod 0644 -- "${WORKSPACE_ROOT}/AGENTS.md"
    fi
}

install_build_scripts() {
    local destination="${WORKSPACE_ROOT}/.monutchee-build"
    local staging="${destination}.tmp.$$"
    local relative
    rm -rf -- "${staging}"
    mkdir -p -- "${staging}/products" "${staging}/definitions/${MONUTCHEE_PRODUCT}" || return 1
    # Install only this vendor's runtime files, never tests or other projects.
    for relative in "${VENDOR_DIR}"/*.sh "${VENDOR_DIR}"/*.py "${VENDOR_DIR}"/*.bash; do
        [[ -f "${relative}" ]] || continue
        [[ "$(basename -- "${relative}")" == workspace.sh ]] && continue
        cp -a -- "${relative}" "${staging}/" || return 1
    done
    cp -a -- "${VENDOR_DIR}/templates" "${staging}/" || return 1
    cp -- "${MANIFEST_DIR}/product.conf" "${staging}/products/${MONUTCHEE_PRODUCT}.conf" || return 1
    if [[ -d "${MANIFEST_DIR}/definition" ]]; then
        cp -a -- "${MANIFEST_DIR}/definition/." "${staging}/definitions/${MONUTCHEE_PRODUCT}/" || return 1
    fi
    chmod +x -- "${staging}"/*.sh "${staging}"/*.py || return 1
    # Which product this toolkit was installed for. The generated per-stage
    # wrappers used to carry --product; mnc and any directly invoked stage
    # script read it from here instead. Written into the staging tree so it
    # arrives with the atomic mv, and refreshed on every install so it cannot
    # go stale. The workspace's own .monutchee-workspace marker still wins,
    # and is not written here because it doubles as the "workspace is
    # initialized" flag that selects automatic mode.
    printf '%s\n' "${MONUTCHEE_PRODUCT}" > "${staging}/.product" || return 1

    rm -rf -- "${destination}"
    mv -- "${staging}" "${destination}" || return 1
    rm -f -- "${WORKSPACE_ROOT}/updateBuildScripts.sh"

    # The workspace root holds one command. The five per-stage wrappers this
    # replaces were generated here too, so remove them the way the obsolete
    # updateBuildScripts.sh above is removed; the stage scripts they called are
    # still reachable as "mnc <target> <command>".
    for relative in make_deploy.sh make_HLS.sh make_PL.sh make_mconf.sh make_RPU.sh make_yocto.sh; do
        [[ -f "${WORKSPACE_ROOT}/${relative}" ]] || continue
        # Only a wrapper this script generated: something hand-written under the
        # same name is somebody's work, so warn and keep it.
        if ! grep -q '\.monutchee-build/make_' "${WORKSPACE_ROOT}/${relative}"; then
            printf 'Warning: %s is not a generated wrapper; leaving it in place.\n' \
                "${WORKSPACE_ROOT}/${relative}" >&2
            continue
        fi
        rm -f -- "${WORKSPACE_ROOT}/${relative}"
        printf 'Removed superseded wrapper: %s (use ./mnc instead)\n' \
            "${WORKSPACE_ROOT}/${relative}"
    done
    install_build_command || return 1
    install_build_preset || return 1
}

# A symlink rather than a generated wrapper: nothing to regenerate when the
# stage scripts change, and no product name baked into the workspace root
# (mnc reads the .monutchee-workspace marker). The target is relative so the
# workspace survives being moved or bind-mounted, and it is (re)created after
# .monutchee-build is replaced above, since the link resolves at use time.
install_build_command() {
    local launcher="${WORKSPACE_ROOT}/mnc"

    if [[ -e "${launcher}" && ! -L "${launcher}" ]]; then
        printf 'Error: %s exists and is not a symlink; move it aside and rerun.\n' \
            "${launcher}" >&2
        return 1
    fi
    if ! ln -sfn -- ".monutchee-build/mnc.sh" "${launcher}"; then
        printf 'Error: failed to create the build command symlink %s\n' \
            "${launcher}" >&2
        return 1
    fi
    printf 'Build command is ready:\n'
    printf '  %s -> .monutchee-build/mnc.sh\n' "${launcher}"
    printf '  run "./mnc --list" for the targets, "./mnc all build" for everything\n'
    if [[ -f "${WORKSPACE_ROOT}/.monutchee-build/mnc-completion.bash" ]]; then
        printf '  TAB completion installs itself on the first ./mnc run from\n'
        printf '  a terminal; for this shell: eval "$(./mnc --completion)"\n'
    fi
}

# User-owned workspace settings: seed once, then preserve them across every
# toolkit refresh just like .vscode/settings.json.
install_build_preset() {
    local destination="${WORKSPACE_ROOT}/MncBuildPreset.yaml"
    local template="${WORKSPACE_ROOT}/.monutchee-build/templates/MncBuildPreset.yaml"

    if [[ -e "${destination}" ]]; then
        printf 'Build preset already present; leaving %s untouched.\n' \
            "${destination}"
        return 0
    fi
    if [[ ! -f "${template}" ]]; then
        printf 'Error: missing build preset template: %s\n' "${template}" >&2
        return 1
    fi
    if ! cp -- "${template}" "${destination}"; then
        printf 'Error: failed to create build preset: %s\n' "${destination}" >&2
        return 1
    fi
    if [[ -n "${DEFAULT_BUILD_TARGET:-}" ]]; then
        printf '\nbuild_target: %s\n' "${DEFAULT_BUILD_TARGET}" >> "${destination}"
    fi
    chmod 0600 -- "${destination}"
    printf 'Build preset is ready:\n  %s\n' "${destination}"
}

# Editor settings for a fresh workspace. Never overwritten: unlike the
# generated guidance and build command, this file is expected to be edited by
# whoever works in the workspace.
install_vscode_settings() {
    local destination="${WORKSPACE_ROOT}/.vscode/settings.json"
    local template="${WORKSPACE_ROOT}/.monutchee-build/templates/vscode-settings.json"
    local staging="${destination}.tmp.$$"
    local -a filters=(-e "s/@PROJECT_PREFIX@/${PROJECT_PREFIX}/g")

    if [[ -e "${destination}" ]]; then
        printf 'Editor settings already present; leaving %s untouched.\n' \
            "${destination}"
        return 0
    fi
    if [[ ! -f "${template}" ]]; then
        printf 'No editor-settings template installed; skipping .vscode/settings.json.\n'
        return 0
    fi

    # Products without a WEB repository drop that entry. meta-monutchee is last
    # in the template's repository list so the deletion cannot leave a trailing
    # comma behind.
    if [[ -z "${WEB_PROJECT_NAME}" ]]; then
        filters+=(-e '/_WEB"/d')
    fi

    mkdir -p -- "$(dirname -- "${destination}")"
    rm -f -- "${staging}"
    if ! sed "${filters[@]}" -- "${template}" > "${staging}"; then
        printf 'Warning: failed to render editor settings; skipping.\n' >&2
        rm -f -- "${staging}"
        return 0
    fi
    if ! mv -f -- "${staging}" "${destination}"; then
        printf 'Warning: failed to install editor settings at %s; skipping.\n' \
            "${destination}" >&2
        rm -f -- "${staging}"
        return 0
    fi
    printf 'Editor settings are ready:\n'
    printf '  %s\n' "${destination}"
}

create_tmux_launcher() {
    local launcher="${WORKSPACE_ROOT}/openTmux"

    # The product-specific values are expanded here into the launcher header;
    # the static body below is a quoted heredoc that references them at runtime.
    {
        printf '#!/usr/bin/env bash\n\n'
        printf 'set -Eeuo pipefail\n\n'
        printf '# Generated by setupWorkspace. Opens a "%s" tmux session with one\n' "${TMUX_SESSION}"
        printf '# window per component. Run it from anywhere in the workspace root.\n\n'
        printf 'SESSION=%q\n' "${TMUX_SESSION}"
        printf 'PROJECT_PREFIX=%q\n' "${PROJECT_PREFIX}"
        printf 'WEB_PROJECT_NAME=%q\n' "${WEB_PROJECT_NAME}"
        printf 'APPLICATIONS_DIR=%q\n' "${APPLICATIONS_DIR_NAME}"
        printf 'YOCTO_DIR=%q\n' "${YOCTO_DIR_NAME}"
        printf 'YOCTO_BUILD_DIR=%q\n\n' "${YOCTO_BUILD_DIR}"
        cat <<'BODY'
WORKSPACE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

# Resolve the current preset when opening a new session, even after it changes.
if [[ -f "${WORKSPACE_ROOT}/.monutchee-build/libbuild.sh" ]]; then
    YOCTO_BUILD_DIR="$(
        source "${WORKSPACE_ROOT}/.monutchee-build/libbuild.sh"
        load_product_profile
        basename -- "${YOCTO_BUILD_DIR}"
    )"
fi

if ! command -v tmux >/dev/null 2>&1; then
    printf 'Error: tmux was not found in PATH.\n' >&2
    exit 1
fi

# If the session already exists, just attach to (or switch to) it.
if tmux has-session -t "${SESSION}" 2>/dev/null; then
    printf 'Session %s already exists; attaching.\n' "${SESSION}"
    if [[ -n "${TMUX:-}" ]]; then
        exec tmux switch-client -t "${SESSION}"
    else
        exec tmux attach-session -t "${SESSION}"
    fi
fi

open_window() {
    # open_window <name> <directory>
    local name="$1"
    local dir="$2"

    if [[ ! -d "${dir}" ]]; then
        printf 'Warning: %s does not exist yet; opening %s in workspace root.\n' \
            "${dir}" "${name}" >&2
        dir="${WORKSPACE_ROOT}"
    fi

    if [[ -z "${SESSION_CREATED:-}" ]]; then
        tmux new-session -d -s "${SESSION}" -n "${name}" -c "${dir}"
        SESSION_CREATED=1
    else
        tmux new-window -t "${SESSION}:" -n "${name}" -c "${dir}"
    fi
}

open_window root "${WORKSPACE_ROOT}"
open_window yocto "${WORKSPACE_ROOT}/${YOCTO_DIR}/sources/meta-monutchee"

# Keep meta-monutchee in the upper two-thirds of the Yocto window. The new
# lower pane preserves the original Yocto console and SDK initialization.
YOCTO_WINDOW_HEIGHT="$(
    tmux display-message -p -t "${SESSION}:yocto" '#{window_height}' \
        2>/dev/null || printf '24\n'
)"
if [[ ! "${YOCTO_WINDOW_HEIGHT}" =~ ^[0-9]+$ ]]; then
    YOCTO_WINDOW_HEIGHT=24
fi
YOCTO_BOTTOM_HEIGHT=$((YOCTO_WINDOW_HEIGHT / 3))
if ((YOCTO_BOTTOM_HEIGHT < 1)); then
    YOCTO_BOTTOM_HEIGHT=1
fi

YOCTO_SDK_PANE=""
if ! YOCTO_SDK_PANE="$(
    tmux split-window \
        -P -F '#{pane_id}' \
        -v -l "${YOCTO_BOTTOM_HEIGHT}" \
        -t "${SESSION}:yocto" \
        -c "${WORKSPACE_ROOT}/${YOCTO_DIR}"
)"; then
    printf 'Warning: failed to split the yocto window; using one pane.\n' >&2
    YOCTO_SDK_PANE="$(
        tmux display-message -p -t "${SESSION}:yocto" '#{pane_id}'
    )"
fi
if [[ -e "${WORKSPACE_ROOT}/${YOCTO_DIR}/setupSDK" ]]; then
    tmux send-keys -t "${YOCTO_SDK_PANE}" "source ./setupSDK ${YOCTO_BUILD_DIR}" C-m
else
    printf 'Warning: %s/setupSDK not found; skipping source.\n' "${YOCTO_DIR}" >&2
fi

open_window apu  "${WORKSPACE_ROOT}/${APPLICATIONS_DIR}/${PROJECT_PREFIX}_APU"
open_window rpu  "${WORKSPACE_ROOT}/${APPLICATIONS_DIR}/${PROJECT_PREFIX}_RPU"
open_window pl   "${WORKSPACE_ROOT}/${APPLICATIONS_DIR}/${PROJECT_PREFIX}_PL"
if [[ -n "${WEB_PROJECT_NAME}" ]]; then
    open_window web "${WORKSPACE_ROOT}/${APPLICATIONS_DIR}/${WEB_PROJECT_NAME}"
fi
open_window tftp "${WORKSPACE_ROOT}/${YOCTO_DIR}/${YOCTO_BUILD_DIR}/export/tftpboot"

# Start on the workspace-root window.
tmux select-window -t "${SESSION}:root"

if [[ -n "${TMUX:-}" ]]; then
    exec tmux switch-client -t "${SESSION}"
else
    exec tmux attach-session -t "${SESSION}"
fi
BODY
    } >"${launcher}"

    chmod +x -- "${launcher}"

    printf 'tmux launcher is ready:\n'
    printf '  %s\n' "${launcher}"
}

setup_applications() {
    local applications_root="${WORKSPACE_ROOT}/${APPLICATIONS_DIR_NAME}"
    local -a projects=()
    local -a selected_projects=()
    local -a projects_to_start=()
    local project_name
    local sync_products=false

    if ! command -v repo >/dev/null 2>&1; then
        printf 'Error: repo command was not found in PATH.\n' >&2
        return 1
    fi

    mkdir -p -- "${applications_root}"

    # A legacy applications directory has component clones but no repo
    # metadata. Refuse to adopt it implicitly because repo may otherwise move
    # or replace Git metadata while synchronizing.
    if [[ ! -d "${applications_root}/.repo" ]]; then
        for project_name in \
            "${APU_PROJECT_NAME}" \
            "${RPU_PROJECT_NAME}" \
            "${PL_PROJECT_NAME}" \
            "${WEB_PROJECT_NAME}"; do
            if [[ -n "${project_name}" && -e "${applications_root}/${project_name}" ]]; then
                printf 'Error: %s already exists but applications has no .repo.\n' \
                    "${applications_root}/${project_name}" >&2
                printf 'Create a fresh workspace or migrate the existing checkout manually.\n' >&2
                return 1
            fi
        done
    fi

    if [[ "${DO_ALL}" == true ]]; then
        selected_projects+=(
            "${APU_PROJECT_NAME}"
            "${RPU_PROJECT_NAME}"
            "${PL_PROJECT_NAME}"
        )
        if [[ -n "${WEB_PROJECT_NAME}" ]]; then
            selected_projects+=("${WEB_PROJECT_NAME}")
        fi
    else
        [[ "${DO_APU}" == true ]] && projects+=("${APU_PROJECT_NAME}")
        [[ "${DO_RPU}" == true ]] && projects+=("${RPU_PROJECT_NAME}")
        [[ "${DO_PL}" == true ]] && projects+=("${PL_PROJECT_NAME}")
        [[ "${DO_WEB}" == true ]] && projects+=("${WEB_PROJECT_NAME}")
        selected_projects=("${projects[@]}")
    fi
    for project_name in "${selected_projects[@]}"; do
        if [[ ! -e "${applications_root}/${project_name}/.git" ]]; then
            projects_to_start+=("${project_name}")
        fi
    done
    if [[ "${DO_ALL}" == true || "${DO_APU}" == true || \
          "${DO_RPU}" == true || "${DO_PL}" == true || \
          "${DO_WEB}" == true ]]; then
        sync_products=true
    fi

    printf 'Initializing applications repo client:\n'
    printf '  product:             %s\n' "${MONUTCHEE_PRODUCT}"
    printf '  manifest repository: %s\n' "${MANIFEST_REPO_URL}"
    printf '  manifest branch:     %s\n' "${MANIFEST_BRANCH}"
    printf '  manifest file:       %s\n' "${APPLICATIONS_MANIFEST_FILE}"
    printf '  destination:         %s\n' "${applications_root}"

    (
        cd "${applications_root}"

        repo init \
            -u "${MANIFEST_REPO_URL}" \
            -b "${MANIFEST_BRANCH}" \
            -m "${APPLICATIONS_MANIFEST_FILE}" \
            || exit 1

        if [[ "${sync_products}" != true ]]; then
            exit 0
        fi

        # Fetch submodules for build completeness without registering them as
        # manifest projects. Branch operations therefore affect only projects
        # declared by applications.xml.
        repo sync --fetch-submodules \
            "${projects[@]}" || exit 1

        # Repo normally leaves newly synchronized projects detached at the
        # manifest revision. Attach only new checkouts so a later setup run
        # never replaces a developer's active branch.
        if ((${#projects_to_start[@]} > 0)); then
            repo start "${PRODUCT_BRANCH}" "${projects_to_start[@]}" || exit 1
        fi
    ) || return 1

    # A filtered first sync may not discover a submodule until after its
    # parent worktree exists. Complete the owning repositories explicitly so
    # focused `apu` and `rpu` setup is as complete as `all`.
    if [[ "${DO_ALL}" == true || "${DO_APU}" == true ]]; then
        git -C "${applications_root}/${APU_PROJECT_NAME}" \
            submodule update --init --recursive || return 1
    fi
    if [[ "${DO_ALL}" == true || "${DO_RPU}" == true ]]; then
        git -C "${applications_root}/${RPU_PROJECT_NAME}" \
            submodule update --init --recursive || return 1
    fi

    if [[ "${sync_products}" == true ]]; then
        printf 'Applications synchronized successfully.\n'
    else
        printf 'Applications manifest initialized successfully.\n'
    fi
}

setup_yocto() {
    local yocto_dir="${WORKSPACE_ROOT}/${YOCTO_DIR_NAME}"
    local meta_monutchee_dir="${yocto_dir}/sources/meta-monutchee"
    local start_meta_monutchee=false

    if ! command -v repo >/dev/null 2>&1; then
        printf 'Error: repo command was not found in PATH.\n' >&2
        return 1
    fi

    if [[ ! -e "${meta_monutchee_dir}/.git" ]]; then
        start_meta_monutchee=true
    fi

    mkdir -p -- "${yocto_dir}"

    printf 'Synchronizing Yocto repo client:\n'
    printf '  product:             %s\n' "${MONUTCHEE_PRODUCT}"
    printf '  manifest repository: %s\n' "${MANIFEST_REPO_URL}"
    printf '  manifest branch:     %s\n' "${MANIFEST_BRANCH}"
    printf '  manifest file:       %s\n' "${YOCTO_MANIFEST_FILE}"
    printf '  destination:         %s\n' "${yocto_dir}"

    (
        cd "${yocto_dir}"
        repo init \
            -u "${MANIFEST_REPO_URL}" \
            -b "${MANIFEST_BRANCH}" \
            -m "${YOCTO_MANIFEST_FILE}" || exit 1
        repo sync || exit 1
    ) || return 1

    if [[ "${META_MONUTCHEE_BRANCH}" == "main" && \
          "${start_meta_monutchee}" == true ]]; then
        (
            cd "${yocto_dir}"
            repo start main sources/meta-monutchee || exit 1
        ) || return 1
    elif [[ "${META_MONUTCHEE_BRANCH}" != "main" ]]; then
        clone_or_checkout_branch_in_place() {
            local repository="$1"
            local remote="$2"
            local branch="$3"
            if git -C "${repository}" show-ref --verify --quiet "refs/heads/${branch}"; then
                git -C "${repository}" switch "${branch}"
            else
                git -C "${repository}" fetch "${remote}" "${branch}" &&
                    git -C "${repository}" switch --track "${remote}/${branch}"
            fi
        }
        clone_or_checkout_branch_in_place \
            "${meta_monutchee_dir}" Monutchee "${META_MONUTCHEE_BRANCH}" \
            || return 1
    fi

    # Record the product so `source ./setupSDK` works with no argument in every
    # new console.
    printf '%s\n' "${MONUTCHEE_PRODUCT}" > "${yocto_dir}/.mncos-product"
    printf 'Recorded product marker: %s (%s)\n' \
        "${MONUTCHEE_PRODUCT}" "${yocto_dir}/.mncos-product"

    printf 'Yocto workspace synchronized successfully.\n'
}

initialize_workspace() {
    local failures=0

    parse_arguments "$@"
    configure_product

    mkdir -p -- "${WORKSPACE_ROOT}"
    WORKSPACE_ROOT="$(cd -- "${WORKSPACE_ROOT}" && pwd -P)"
    select_automatic_mode
    if [[ "${DO_ALL}" == true || "${DO_YOCTO}" == true || "${DO_APU}" == true ||
          "${DO_RPU}" == true || "${DO_PL}" == true || "${DO_WEB}" == true ]]; then
        [[ -n "${MANIFEST_REPO_URL}" ]] || fail 'No manifest origin; supply --manifest-url.'
        check_remote "${MANIFEST_REPO_URL}"
    fi
    mkdir -p -- \
        "${WORKSPACE_ROOT}/${APPLICATIONS_DIR_NAME}" \
        "${WORKSPACE_ROOT}/${YOCTO_DIR_NAME}"

    printf 'Product:        %s\n' "${MONUTCHEE_PRODUCT}"
    printf 'Workspace root: %s\n\n' "${WORKSPACE_ROOT}"

    install_workspace_guidance
    printf '\n'

    create_runtime_directories
    printf '\n'

    if [[ "${MONUTCHEE_SCRIPTS_ONLY_UPDATE:-false}" != true ]]; then
        create_tmux_launcher
        printf '\n'
    fi

    if [[ "${DO_SCRIPTS}" == true ]]; then
        install_build_scripts || failures=$((failures + 1))
        printf '\n'

        # After the build scripts, because the template ships with them. Runs on
        # the scripts-only refresh too, so a workspace created before this
        # existed gains the file; it never overwrites an existing one.
        install_vscode_settings
        printf '\n'
    fi

    if [[ "${DO_ALL}" == true || "${DO_APU}" == true || \
          "${DO_RPU}" == true || "${DO_PL}" == true || \
          "${DO_WEB}" == true ]]; then
        setup_applications || failures=$((failures + 1))
        printf '\n'
    fi

    if [[ "${DO_YOCTO}" == true ]]; then
        setup_yocto || failures=$((failures + 1))
        printf '\n'
    fi

    if ((failures > 0)); then
        printf 'Workspace setup completed with %d failed component(s).\n' \
            "${failures}" >&2
        printf 'If a repository could not be fetched, check its URL, access rights, credentials, network, and revision in the Git/repo errors above.\n' >&2
        exit 1
    fi

    if [[ "${AUTO_MODE}" == true || "${DO_ALL}" == true ]]; then
        record_workspace_product
    fi

    if [[ "${MONUTCHEE_SCRIPTS_ONLY_UPDATE:-false}" == true ]]; then
        printf 'Workspace script update completed successfully.\n'
    else
        printf 'Workspace setup completed successfully.\n'
    fi
}
