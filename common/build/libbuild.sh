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

    marker="${WORKSPACE_ROOT}/yocto-build/.mncos-product"
    if [[ -r "${marker}" ]]; then
        normalize_product "$(tr -d '[:space:]' < "${marker}")"
        return
    fi

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
    YOCTO_ROOT="${WORKSPACE_ROOT}/yocto-build"
    YOCTO_BUILD_DIR="${YOCTO_ROOT}/build"
    RPU_ROOT="${WORKSPACE_ROOT}/${RPU_REPO_DIR}"
    PL_ROOT="${WORKSPACE_ROOT}/${PL_REPO_DIR}"
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
