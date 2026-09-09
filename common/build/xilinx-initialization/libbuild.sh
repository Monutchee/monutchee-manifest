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
        ""|*[!a-z0-9-]*|-*|*--*|*-)
            die "Invalid product identifier '${requested}'" ;;
    esac
    if [[ ! -f "${BUILD_TOOLKIT_DIR}/products/${requested}.conf" ]]; then
        die "Unsupported product '${requested}'; missing products/${requested}.conf"
    fi
    printf '%s\n' "${requested}"
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

require_target_stage() {
    local requested="${1,,}" stage
    [[ -n "${MNC_SUPPORTED_STAGES:-}" ]] || return 0
    for stage in ${MNC_SUPPORTED_STAGES}; do
        [[ "${stage,,}" != "${requested}" ]] || return 0
    done
    die "Target ${MNC_BUILD_TARGET} does not yet support ${1}; available stages: ${MNC_SUPPORTED_STAGES}. Complete the carrier integration before enabling downstream stages."
}

load_product_profile() {
    local requested="${1:-}"
    local read_only="${2:-false}"
    local profile

    PRODUCT="$(resolve_product "${requested}")"
    profile="${BUILD_TOOLKIT_DIR}/products/${PRODUCT}.conf"
    require_file "${profile}" "product build profile"
    # shellcheck disable=SC1090
    source "${profile}"

    if [[ -n "${DEFAULT_BUILD_TARGET:-}" ]]; then
        local resolved_target
        resolved_target="$(python3 "${BUILD_TOOLKIT_DIR}/build_target.py" \
            --definitions "${BUILD_TOOLKIT_DIR}/definitions/${PRODUCT}/targets.json" \
            --preset "${WORKSPACE_ROOT}/MncBuildPreset.yaml" \
            --default "${DEFAULT_BUILD_TARGET}" --selected "${MNC_BUILD_TARGET:-}")" || \
            die "Unable to resolve build target"
        eval "${resolved_target}" # Only fixed keys and shlex-quoted values from build_target.py.
        export MNC_BUILD_TARGET
        export MNC_BUILD_MACHINE="${MACHINE}"
    fi
    # Direct stage entrypoints must enforce the same target capability as mnc.
    local entrypoint="${0##*/}"
    if [[ "${read_only}" != true && "${entrypoint}" == make_*.sh ]]; then
        entrypoint="${entrypoint#make_}"
        require_target_stage "${entrypoint%.sh}"
    fi
    RUNTIME_DIR="${WORKSPACE_ROOT}/runtime-generated${MNC_BUILD_TARGET:+/${MNC_BUILD_TARGET}}"
    BIN_FILE_DIR="${RUNTIME_DIR}/bin_file"
    SDT_DIR="${RUNTIME_DIR}/vivado_SDT_out"
    APPLICATIONS_ROOT="${WORKSPACE_ROOT}/applications"
    YOCTO_ROOT="${WORKSPACE_ROOT}/yocto-build"
    YOCTO_BUILD_DIR="${YOCTO_ROOT}/build${MNC_BUILD_TARGET:+-${MACHINE}}"
    APU_ROOT="${APPLICATIONS_ROOT}/${APU_REPO_DIR}"
    RPU_ROOT="${APPLICATIONS_ROOT}/${RPU_REPO_DIR}"
    PL_ROOT="${APPLICATIONS_ROOT}/${PL_REPO_DIR}"
    WEB_ROOT=""
    if [[ -n "${WEB_REPO_DIR:-}" ]]; then
        WEB_ROOT="${APPLICATIONS_ROOT}/${WEB_REPO_DIR}"
    fi
    XSA_PATH="${BIN_FILE_DIR}/${PL_XSA_BASENAME}"
    PL_PROJECT_DIR="${PL_ROOT}/vivado_gen${MNC_BUILD_TARGET:+/${MNC_BUILD_TARGET}}"
    export MNC_PL_PROJECT_FILE="${PL_ROOT}/${PL_PROJECT_REL:-vivado_gen/${PL_XSA_BASENAME%.xsa}.xpr}"
    export MNC_PL_REPORT_DIR="${PL_PROJECT_DIR}/reports"
    HLS_WORKSPACE="${PL_ROOT}/SourceData/HLS_DesignFile"
    RPU_WORKSPACE="${RPU_ROOT}"
    if [[ -n "${MNC_BUILD_TARGET:-}" ]]; then
        HLS_WORKSPACE="${RUNTIME_DIR}/hls"
        RPU_WORKSPACE="${RUNTIME_DIR}/rpu"
    fi
    export MNC_HLS_IP_REPO="${HLS_WORKSPACE}/ip_repo"
    export MNC_RUNTIME_DIR="${RUNTIME_DIR}"
    export MNC_PL_SOURCE_DIR="${PL_ROOT}"
    export MNC_FPGA_PART="${PL_PART:-}"

    if [[ "${read_only}" != true ]]; then
        mkdir -p -- "${BIN_FILE_DIR}" "${RUNTIME_DIR}/artifact"
    fi
}

# Descriptor 9 is inherited through mnc's report/TUI children and stage shells.
# A single lock serializes mutable tool work across all targets in a workspace.
acquire_workspace_build_lock() {
    if [[ "${MNC_BUILD_LOCK_WORKSPACE:-}" == "${WORKSPACE_ROOT}" && -e /proc/$$/fd/9 ]]; then
        return 0
    fi
    require_command flock
    mkdir -p -- "${WORKSPACE_ROOT}/runtime-generated/.work"
    exec 9>"${WORKSPACE_ROOT}/runtime-generated/.work/build.lock"
    flock -n 9 || die "Another build/deployment is running in ${WORKSPACE_ROOT}"
    export MNC_BUILD_LOCK_WORKSPACE="${WORKSPACE_ROOT}"
}

prepare_vitis_workspace() {
    python3 "${BUILD_TOOLKIT_DIR}/prepare_vitis_workspace.py" \
        --source "$1" --workspace "$2"
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
    write_yocto_target_context
}

write_yocto_target_context() {
    if [[ -n "${MNC_BUILD_TARGET:-}" ]]; then
        python3 - "${YOCTO_BUILD_DIR}/conf" "${MNC_BUILD_TARGET}" "${MACHINE}" <<'PYCONF'
import sys
from pathlib import Path
conf = Path(sys.argv[1])
context = conf / "mnc-target.conf"
text = ('MNC_BUILD_TARGET = "' + sys.argv[2] + '"\n'
        'MNC_RUNTIME_DIR = "${TOPDIR}/../../runtime-generated/${MNC_BUILD_TARGET}"\n'
        'XILINX_DFX_ARTIFACT_DIR = "${MNC_RUNTIME_DIR}/bin_file"\n')
if (conf / 'machine' / (sys.argv[3] + '.conf')).is_file():
    text += 'MACHINE = "' + sys.argv[3] + '"\n'
if not context.exists() or context.read_text() != text:
    context.write_text(text)
local = conf / "local.conf"
include = '\nrequire conf/mnc-target.conf\n'
text = local.read_text()
if 'require conf/mnc-target.conf' not in text:
    local.write_text(text + include)
PYCONF
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
    source ./setupSDK --product "${PRODUCT}" "${YOCTO_BUILD_DIR}" >/dev/null
    write_yocto_target_context
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

# Status snapshots deliberately bypass build locks and tool initialization.
# Parse a separate, small option set so build flags cannot be silently ignored.
run_artifact_status() {
    local stage="$1"
    shift
    WORKSPACE_ROOT="$(default_workspace_root)"
    local requested=""
    while (($# > 0)); do
        case "$1" in
            --status) shift ;;
            --workspace) WORKSPACE_ROOT="$2"; shift 2 ;;
            --workspace=*) WORKSPACE_ROOT="${1#*=}"; shift ;;
            --product) requested="$2"; shift 2 ;;
            --product=*) requested="${1#*=}"; shift ;;
            *) die "Unsupported status option: $1 (status does not accept build options)" ;;
        esac
    done
    WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
    load_product_profile "${requested}" true
    if ! (require_target_stage "${stage}") 2>/dev/null; then
        printf '%s_STATUS_TARGET=%s\n%s_STATUS_VERDICT=unsupported for this target; enabled stages: %s\n' \
            "${stage^^}" "${MNC_BUILD_TARGET}" "${stage^^}" "${MNC_SUPPORTED_STAGES}"
        return 0
    fi
    local -a args=(--stage "${stage,,}" --product "${PRODUCT}"
        --target "${MNC_BUILD_TARGET:-}" --machine "${MACHINE}"
        --bin-dir "${BIN_FILE_DIR}" --xsa "${XSA_PATH}"
        --yocto-build "${YOCTO_BUILD_DIR}")
    if [[ -n "${OPENAMP_CONTRACT_REL:-}" ]]; then
        args+=(--contract "${BUILD_TOOLKIT_DIR}/${OPENAMP_CONTRACT_REL}")
    fi
    PYTHONDONTWRITEBYTECODE=1 python3 "${BUILD_TOOLKIT_DIR}/stage_status.py" "${args[@]}"
}
