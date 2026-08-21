#!/usr/bin/env bash

if [[ -n "${MONUTCHEE_BUILD_LIB_LOADED:-}" ]]; then
    return 0
fi
readonly MONUTCHEE_BUILD_LIB_LOADED=1

BUILD_TOOLKIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ARTIFACT_HELPER="${BUILD_TOOLKIT_DIR}/artifact.py"

log() {
    printf '[monutchee] %s\n' "$*"
}

warn() {
    printf '[monutchee] warning: %s\n' "$*" >&2
}

# Optional machine-readable event channel used by mnc --tui. The descriptor is
# inherited by every build child. It is deliberately best-effort: closing the
# TUI or running a stage directly must never turn a healthy build into a
# failure. Tabs/newlines are removed so one event is always one TSV record.
mnc_event() {
    local kind="${1:-}" stage="${2:-}" percent="${3:-}" message="${4:-}"
    local fd="${MNC_EVENT_FD:-}"

    [[ "${fd}" =~ ^[0-9]+$ ]] || return 0
    kind="${kind//$'\t'/ }"; kind="${kind//$'\n'/ }"
    stage="${stage//$'\t'/ }"; stage="${stage//$'\n'/ }"
    percent="${percent//$'\t'/ }"; percent="${percent//$'\n'/ }"
    message="${message//$'\t'/ }"; message="${message//$'\n'/ }"
    printf 'MNC_EVENT\t%s\t%s\t%s\t%s\n' \
        "${kind}" "${stage}" "${percent}" "${message}" \
        >&"${fd}" 2>/dev/null || true
}

build_progress() {
    local percent="${1:-}" message="${2:-}"
    local stage="${MNC_STAGE_NAME:-build}"

    if [[ -n "${percent}" ]] && \
       { [[ ! "${percent}" =~ ^[0-9]+$ ]] || ((percent < 0 || percent > 100)); }; then
        warn "Ignoring invalid progress percentage: ${percent}"
        percent=""
    fi
    mnc_event progress "${stage}" "${percent}" "${message}"
}

# A stage's concise handoff to mnc's final report. It is visible when the
# make_*.sh script is run directly and also written to the private summary file
# mnc supplies for a parented build.
build_summary() {
    local message="$*"
    local summary_file="${MNC_STAGE_SUMMARY_FILE:-}"

    log "Summary: ${message}"
    if [[ -n "${summary_file}" ]]; then
        printf '%s\n' "${message}" >> "${summary_file}" 2>/dev/null || \
            warn "Could not write stage summary: ${summary_file}"
    fi
    mnc_event summary "${MNC_STAGE_NAME:-build}" "" "${message}"
}

build_elapsed() {
    local total="${1:-0}"

    printf '%02d:%02d:%02d' \
        $((total / 3600)) $(((total % 3600) / 60)) $((total % 60))
}

die() {
    printf '[monutchee] error: %s\n' "$*" >&2
    exit 1
}

require_file() {
    [[ -f "$1" ]] || die "Missing $2: $1"
}

require_dir() {
    [[ -d "$1" ]] || die "Missing $2: $1"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command was not found: $1"
}

canonical_path() {
    readlink -f -- "$1"
}

default_workspace_root() {
    if [[ "$(basename -- "${BUILD_TOOLKIT_DIR}")" == ".monutchee-build" ]]; then
        dirname -- "${BUILD_TOOLKIT_DIR}"
    else
        printf '%s\n' "${WORKSPACE_ROOT:-${PWD}}"
    fi
}

normalize_product() {
    local requested="$1"

    case "${requested}" in
        zudemo|zuboard) printf 'zudemo\n' ;;
        kr260demo|kr260) printf 'kr260demo\n' ;;
        *)
            case "${requested}" in
                ""|*[!a-z0-9-]*|-*|*--*|*-)
                    die "Invalid product identifier '${requested}'"
                    ;;
            esac
            if [[ ! -f "${BUILD_TOOLKIT_DIR}/products/${requested}.conf" ]]; then
                die "Unsupported product '${requested}'; missing products/${requested}.conf"
            fi
            printf '%s\n' "${requested}"
            ;;
    esac
}

resolve_product() {
    local requested="${1:-}"
    local marker

    if [[ -n "${requested}" ]]; then
        normalize_product "${requested}"
        return
    fi
    if [[ -n "${MONUTCHEE_PRODUCT:-}" ]]; then
        normalize_product "${MONUTCHEE_PRODUCT}"
        return
    fi

    # Workspace-root marker first, matching setupWorkspace's own precedence,
    # then the marker setupWorkspace writes beside these scripts when it
    # installs them, then the Yocto client's. Between them the product resolves
    # on a workspace whose Yocto client has not been synced yet, which is what
    # lets the root command be a plain symlink with no product baked in.
    for marker in \
        "${WORKSPACE_ROOT}/.monutchee-workspace" \
        "${BUILD_TOOLKIT_DIR}/.product" \
        "${WORKSPACE_ROOT}/yocto-build/.mncos-product"; do
        if [[ -r "${marker}" ]]; then
            normalize_product "$(tr -d '[:space:]' < "${marker}")"
            return
        fi
    done

    die "Unable to determine product; pass --product or set MONUTCHEE_PRODUCT"
}

load_product_profile() {
    local requested="${1:-}"
    local profile

    PRODUCT="$(resolve_product "${requested}")"
    profile="${BUILD_TOOLKIT_DIR}/products/${PRODUCT}.conf"
    require_file "${profile}" "product build profile"
    # shellcheck disable=SC1090
    source "${profile}"

    RUNTIME_DIR="${WORKSPACE_ROOT}/runtime-generated"
    BIN_FILE_DIR="${RUNTIME_DIR}/bin_file"
    SDT_DIR="${RUNTIME_DIR}/vivado_SDT_out"
    APPLICATIONS_ROOT="${WORKSPACE_ROOT}/applications"
    YOCTO_ROOT="${WORKSPACE_ROOT}/yocto-build"
    YOCTO_BUILD_DIR="${YOCTO_ROOT}/build"
    APU_ROOT="${APPLICATIONS_ROOT}/${APU_REPO_DIR}"
    RPU_ROOT="${APPLICATIONS_ROOT}/${RPU_REPO_DIR}"
    PL_ROOT="${APPLICATIONS_ROOT}/${PL_REPO_DIR}"
    WEB_ROOT=""
    if [[ -n "${WEB_REPO_DIR:-}" ]]; then
        WEB_ROOT="${APPLICATIONS_ROOT}/${WEB_REPO_DIR}"
    fi
    XSA_PATH="${BIN_FILE_DIR}/${PL_XSA_BASENAME}"

    mkdir -p -- "${BIN_FILE_DIR}"
}

load_xilinx_environment() {
    local version="${XILINX_VERSION:-2025.2}"
    local settings="${XILINX_SETTINGS:-/opt/Xilinx/${version}/settings64.sh}"
    local command
    local -a commands=("$@")

    if ((${#commands[@]} == 0)); then
        commands=("${VIVADO:-vivado}" "${SDTGEN:-sdtgen}" "${VITIS:-vitis}")
    fi
    for command in "${commands[@]}"; do
        if ! command -v "${command}" >/dev/null 2>&1; then
            require_file "${settings}" "Xilinx settings script"
            # shellcheck disable=SC1090
            source "${settings}"
            return
        fi
    done
}

# Vivado does not lock projects: a live session saves its own in-memory state
# over any batch edit, so a stage that mutates the project must not run while
# one is open. Restricted to this user's processes so a shared build machine
# does not block on somebody else's session.
vivado_session_running() {
    pgrep -u "$(id -u)" -x vivado >/dev/null 2>&1
}

new_temp_dir() {
    local label="$1"
    mkdir -p -- "${RUNTIME_DIR}/.work"
    mktemp -d "${RUNTIME_DIR}/.work/${label}.XXXXXX"
}

artifact_create() {
    local stage="$1"
    local payload="$2"
    local output="$3"
    shift 3
    require_file "${ARTIFACT_HELPER}" "artifact helper"
    python3 "${ARTIFACT_HELPER}" create \
        --stage "${stage}" \
        --product "${PRODUCT}" \
        --payload-root "${payload}" \
        --output "${output}" \
        "$@"
}

artifact_create_hashed() {
    local stage="$1"
    local payload="$2"
    local output_base="$3"
    shift 3
    require_file "${ARTIFACT_HELPER}" "artifact helper"
    python3 "${ARTIFACT_HELPER}" create \
        --stage "${stage}" \
        --product "${PRODUCT}" \
        --payload-root "${payload}" \
        --output "${output_base}" \
        --hash-filename \
        "$@"
}

artifact_stage_output_base() {
    local stage="$1"

    case "${stage}" in
        pl_sdtgen|mconf|rpu|yocto)
            printf '%s/%s_%s.tar.gz\n' \
                "${BIN_FILE_DIR}" "${PRODUCT}" "${stage}"
            ;;
        *)
            die "Unsupported artifact stage for cleanup: ${stage}"
            ;;
    esac
}

artifact_prune_family() {
    local output_base="$1"
    local keep="${2:-}"
    local removed=""
    local -a arguments=(
        prune
        --output-base "${output_base}"
    )

    require_file "${ARTIFACT_HELPER}" "artifact helper"
    if [[ -n "${keep}" ]]; then
        arguments+=(--keep "${keep}")
    fi
    if ! removed="$(python3 "${ARTIFACT_HELPER}" "${arguments[@]}")"; then
        return 1
    fi
    while IFS= read -r artifact; do
        [[ -z "${artifact}" ]] || log "Removed obsolete artifact: ${artifact}"
    done <<< "${removed}"
}

artifact_finalize_hashed() {
    local stage="$1"
    local output_base="$2"
    local published="$3"
    local canonical_base
    local downstream
    local -a downstream_stages=()

    canonical_base="$(artifact_stage_output_base "${stage}")"
    require_file "${published}" "new ${stage} artifact"

    # Verify before pruning. If publication somehow produced a malformed
    # archive, remove only that new output and preserve the previous set.
    if ! python3 "${ARTIFACT_HELPER}" verify \
        --stage "${stage}" \
        --product "${PRODUCT}" \
        --archive "${published}" >/dev/null; then
        rm -f -- "${published}"
        return 1
    fi

    artifact_prune_family "${output_base}" "${published}" || return 1

    # A custom --artifact target is an export/test path, not the canonical
    # waterfall. Prune only its siblings and leave the workspace chain intact.
    if [[ "$(readlink -m -- "${output_base}")" != \
          "$(readlink -m -- "${canonical_base}")" ]]; then
        log "Custom artifact family finalized; canonical downstream artifacts were preserved"
        return 0
    fi

    case "${stage}" in
        pl_sdtgen) downstream_stages=(mconf rpu yocto) ;;
        mconf)
            if [[ "${RPU_DEPENDS_ON_MCONF:-true}" == true ]]; then
                downstream_stages=(rpu yocto)
            else
                downstream_stages=(yocto)
            fi
            ;;
        rpu) downstream_stages=(yocto) ;;
        yocto) downstream_stages=() ;;
    esac
    for downstream in "${downstream_stages[@]}"; do
        artifact_prune_family \
            "$(artifact_stage_output_base "${downstream}")" || return 1
    done
}

artifact_select_latest() {
    local pattern="$1"
    require_file "${ARTIFACT_HELPER}" "artifact helper"
    python3 "${ARTIFACT_HELPER}" select \
        --directory "${BIN_FILE_DIR}" \
        --pattern "${pattern}"
}

artifact_metadata() {
    local stage="$1"
    local archive="$2"
    local key="$3"
    require_file "${archive}" "${stage} artifact"
    require_file "${ARTIFACT_HELPER}" "artifact helper"
    python3 "${ARTIFACT_HELPER}" metadata \
        --stage "${stage}" \
        --product "${PRODUCT}" \
        --archive "${archive}" \
        --key "${key}"
}

artifact_extract() {
    local stage="$1"
    local archive="$2"
    local destination="$3"
    require_file "${archive}" "${stage} artifact"
    require_file "${ARTIFACT_HELPER}" "artifact helper"
    python3 "${ARTIFACT_HELPER}" extract \
        --stage "${stage}" \
        --product "${PRODUCT}" \
        --archive "${archive}" \
        --directory "${destination}"
}

copy_tree_fresh() {
    local source="$1"
    local destination="$2"
    require_dir "${source}" "source directory"
    rm -rf -- "${destination}"
    mkdir -p -- "${destination}"
    cp -a -- "${source}/." "${destination}/"
}

install_machine_conf_payload() {
    local payload_conf="$1"
    local active_conf="${YOCTO_BUILD_DIR}/conf"
    local file

    require_file "${payload_conf}/machine/${MACHINE}.conf" "generated machine configuration"
    require_dir "${payload_conf}/machine/include/${MACHINE}" "generated machine includes"
    require_dir "${payload_conf}/dts/${MACHINE}" "generated machine DTS directory"
    mkdir -p -- "${active_conf}/machine/include" "${active_conf}/multiconfig" "${active_conf}/dts"

    rm -rf -- "${active_conf}/machine/include/${MACHINE}" "${active_conf}/dts/${MACHINE}"
    rm -f -- "${active_conf}/machine/${MACHINE}.conf"
    while IFS= read -r -d '' file; do
        rm -f -- "${file}"
    done < <(find "${active_conf}/multiconfig" -maxdepth 1 -type f -name "${MACHINE}-*.conf" -print0)

    cp -a -- "${payload_conf}/machine/${MACHINE}.conf" "${active_conf}/machine/"
    cp -a -- "${payload_conf}/machine/include/${MACHINE}" "${active_conf}/machine/include/"
    cp -a -- "${payload_conf}/dts/${MACHINE}" "${active_conf}/dts/"
    if [[ -d "${payload_conf}/multiconfig" ]]; then
        cp -a -- "${payload_conf}/multiconfig/." "${active_conf}/multiconfig/"
    fi
}

source_yocto_sdk() {
    local restore_nounset=false
    require_file "${YOCTO_ROOT}/setupSDK" "Yocto setupSDK"
    cd "${YOCTO_ROOT}"
    if [[ "$-" == *u* ]]; then
        restore_nounset=true
        set +u
    fi
    # shellcheck disable=SC1091
    source ./setupSDK --product "${PRODUCT}" build >/dev/null
    if [[ "${restore_nounset}" == true ]]; then
        set -u
    fi
}

record_git_metadata_args() {
    local repo label sha dirty
    for label in manifest PL RPU meta; do
        case "${label}" in
            manifest) repo="${MANIFEST_SOURCE_ROOT:-}" ;;
            PL) repo="${PL_ROOT:-}" ;;
            RPU) repo="${RPU_ROOT:-}" ;;
            meta) repo="${YOCTO_ROOT:-}/sources/meta-monutchee" ;;
        esac
        if [[ -n "${repo}" ]] && git -C "${repo}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            sha="$(git -C "${repo}" rev-parse HEAD)"
            dirty="false"
            [[ -n "$(git -C "${repo}" status --porcelain)" ]] && dirty="true"
            printf -- '--metadata\0%s_sha=%s\0--metadata\0%s_dirty=%s\0' \
                "${label}" "${sha}" "${label}" "${dirty}"
        fi
    done
}
