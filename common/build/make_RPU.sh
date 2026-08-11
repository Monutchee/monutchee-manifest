#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_RPU.sh [OPTIONS]

Generate the RPU OpenAMP contract headers, build both R5 applications, and
package the two ELFs. By default, recreate the Vitis platform directly from
the XSA before building the applications.

Options:
  --workspace DIR        Product workspace root
  --product NAME         Product profile: zudemo, kr260demo, or msap1
  --xsa FILE             Bitstream-inclusive XSA exported from Vivado
  --openamp-contract FILE
                         OpenAMP contract override (MSAP1)
  --mconf-artifact FILE  Input artifact from make_mconf.sh
                         (legacy products only)
  --artifact FILE        RPU artifact basename; _<sha256[:6]> is appended
  --elf-only             Reuse the existing platform and only build/package ELFs
  -h, --help             Show this help
EOF
}

WORKSPACE_ROOT="$(default_workspace_root)"
REQUESTED_PRODUCT=""
MCONF_ARTIFACT=""
ARTIFACT=""
XSA_OVERRIDE=""
OPENAMP_CONTRACT_OVERRIDE=""
ELF_ONLY=false

while (($# > 0)); do
    case "$1" in
        --workspace) WORKSPACE_ROOT="$2"; shift 2 ;;
        --workspace=*) WORKSPACE_ROOT="${1#*=}"; shift ;;
        --product) REQUESTED_PRODUCT="$2"; shift 2 ;;
        --product=*) REQUESTED_PRODUCT="${1#*=}"; shift ;;
        --xsa) XSA_OVERRIDE="$2"; shift 2 ;;
        --xsa=*) XSA_OVERRIDE="${1#*=}"; shift ;;
        --openamp-contract) OPENAMP_CONTRACT_OVERRIDE="$2"; shift 2 ;;
        --openamp-contract=*) OPENAMP_CONTRACT_OVERRIDE="${1#*=}"; shift ;;
        --mconf-artifact) MCONF_ARTIFACT="$2"; shift 2 ;;
        --mconf-artifact=*) MCONF_ARTIFACT="${1#*=}"; shift ;;
        --artifact) ARTIFACT="$2"; shift 2 ;;
        --artifact=*) ARTIFACT="${1#*=}"; shift ;;
        --elf-only) ELF_ONLY=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile "${REQUESTED_PRODUCT}"

VITIS="${VITIS:-vitis}"
load_xilinx_environment "${VITIS}"
require_command "${VITIS}"
require_command python3

ARTIFACT_BASE="${ARTIFACT:-${BIN_FILE_DIR}/${PRODUCT}_rpu.tar.gz}"
require_dir "${RPU_ROOT}" "RPU repository"

CONTRACT_MODE=false
CONTRACT_FILE=""
CONTRACT_SHA256=""
CONTRACT_TOOL="${SCRIPT_DIR}/openamp_contract.py"
MCONF_SHA256=""
MCONF_XSA_SHA256=""
if [[ -n "${OPENAMP_CONTRACT_REL:-}" ]]; then
    CONTRACT_MODE=true
    CONTRACT_FILE="${OPENAMP_CONTRACT_OVERRIDE:-${SCRIPT_DIR}/${OPENAMP_CONTRACT_REL}}"
    CONTRACT_FILE="$(canonical_path "${CONTRACT_FILE}")"
    require_file "${CONTRACT_TOOL}" "OpenAMP contract tool"
    require_file "${CONTRACT_FILE}" "OpenAMP contract"
    python3 "${CONTRACT_TOOL}" validate-contract --contract "${CONTRACT_FILE}"
    CONTRACT_SHA256="$(
        python3 "${CONTRACT_TOOL}" contract-digest --contract "${CONTRACT_FILE}"
    )"
    if [[ -n "${MCONF_ARTIFACT}" ]]; then
        die "--mconf-artifact is not used by the ${PRODUCT} contract-based RPU flow"
    fi
else
    if [[ -n "${OPENAMP_CONTRACT_OVERRIDE}" ]]; then
        die "--openamp-contract is not supported by product '${PRODUCT}'"
    fi
    if [[ -z "${MCONF_ARTIFACT}" ]]; then
        MCONF_ARTIFACT="$(
            artifact_select_latest "${PRODUCT}_mconf_*.tar.gz"
        )"
    fi
    MCONF_SHA256="$(sha256sum "${MCONF_ARTIFACT}" | awk '{print $1}')"
    MCONF_XSA_SHA256="$(
        artifact_metadata mconf "${MCONF_ARTIFACT}" xsa_sha256
    )"
fi
PLATFORM_RECEIPT="${RPU_ROOT}/platform/.monutchee-provenance"
if [[ "${CONTRACT_MODE}" == true ]]; then
    log "RPU inputs: contract=$(basename -- "${CONTRACT_FILE}") openamp_contract_sha256=${CONTRACT_SHA256} mode=$([[ "${ELF_ONLY}" == true ]] && printf elf-only || printf full)"
else
    log "RPU inputs: mconf=$(basename -- "${MCONF_ARTIFACT}") mconf_sha256=${MCONF_SHA256} xsa_sha256=${MCONF_XSA_SHA256} mode=$([[ "${ELF_ONLY}" == true ]] && printf elf-only || printf full)"
fi

platform_receipt_value() {
    local key="$1"
    local value

    value="$(
        awk -F= -v requested="${key}" \
            '$1 == requested { print substr($0, index($0, "=") + 1); exit }' \
            "${PLATFORM_RECEIPT}"
    )"
    [[ -n "${value}" ]] || \
        die "Vitis platform provenance is missing '${key}': ${PLATFORM_RECEIPT}"
    printf '%s\n' "${value}"
}

verify_contract_rpu_sources() {
    local core
    local config
    local helper_platform

    for core in r5c0 r5c1; do
        config="${RPU_ROOT}/${core^}/src/UserConfig.cmake"
        require_file "${config}" "${core} Vitis user configuration"
        grep -Fq 'MNC_OPENAMP_CONTRACT' "${config}" || \
            die "${config} does not enable MNC_OPENAMP_CONTRACT; switch MSAP1_RPU to the contract-compatible branch"
        grep -Fq "openamp_contract/${core}" "${config}" || \
            die "${config} does not include the ${core} generated OpenAMP contract directory"
    done

    helper_platform="${RPU_ROOT}/libs/openamp-helper/machine/zynqmp_r5/platform_info.h"
    require_file "${helper_platform}" "OpenAMP helper platform interface"
    grep -Fq '#ifdef MNC_OPENAMP_CONTRACT' "${helper_platform}" || \
        die "${helper_platform} does not support MNC_OPENAMP_CONTRACT; update the pinned OpenAMP helper submodule"
    grep -Fq '#include "openamp_contract.h"' "${helper_platform}" || \
        die "${helper_platform} still expects amd_platform_info.h; update the pinned OpenAMP helper submodule"
}

if [[ "${ELF_ONLY}" == true ]]; then
    require_dir "${RPU_ROOT}/platform" "existing Vitis platform"
    require_file "${PLATFORM_RECEIPT}" "Vitis platform provenance receipt"

    PLATFORM_SCHEMA="$(platform_receipt_value schema)"
    PLATFORM_PRODUCT="$(platform_receipt_value product)"
    PLATFORM_XSA_SHA256="$(platform_receipt_value xsa_sha256)"

    if [[ "${PLATFORM_PRODUCT}" != "${PRODUCT}" ]]; then
        die "Vitis platform product '${PLATFORM_PRODUCT}' does not match '${PRODUCT}'"
    fi
    if [[ "${CONTRACT_MODE}" == true ]]; then
        if [[ "${PLATFORM_SCHEMA}" != "monutchee-platform-provenance-v2" ]]; then
            die "The existing Vitis platform uses legacy provenance; run one full 'mnc RPU build' to migrate it"
        fi
        PLATFORM_CONTRACT_SHA256="$(
            platform_receipt_value openamp_contract_sha256
        )"
        if [[ "${PLATFORM_CONTRACT_SHA256}" != "${CONTRACT_SHA256}" ]]; then
            die "The OpenAMP contract changed after the existing Vitis platform was built; run a full 'mnc RPU build'"
        fi
        [[ -z "${XSA_OVERRIDE}" ]] || \
            XSA_PATH="$(canonical_path "${XSA_OVERRIDE}")"
        require_file "${XSA_PATH}" "raw PL XSA"
        CURRENT_XSA_SHA256="$(sha256sum "${XSA_PATH}" | awk '{print $1}')"
        if [[ "${PLATFORM_XSA_SHA256}" != "${CURRENT_XSA_SHA256}" ]]; then
            die "The XSA changed after the existing Vitis platform was built; run a full 'mnc RPU build'"
        fi
    else
        if [[ "${PLATFORM_SCHEMA}" != "monutchee-platform-provenance-v1" ]]; then
            die "Unsupported Vitis platform provenance schema '${PLATFORM_SCHEMA}'"
        fi
        PLATFORM_MCONF_SHA256="$(platform_receipt_value mconf_sha256)"
        if [[ "${PLATFORM_MCONF_SHA256}" != "${MCONF_SHA256}" ]]; then
            die "Selected mconf artifact differs from the mconf used to build the existing Vitis platform; run a full 'mnc RPU build'"
        fi
        if [[ "${PLATFORM_XSA_SHA256}" != "${MCONF_XSA_SHA256}" ]]; then
            die "Existing Vitis platform XSA does not match the XSA used by the selected mconf artifact; run a full 'mnc RPU build'"
        fi
    fi
    XSA_SHA256="${PLATFORM_XSA_SHA256}"
else
    [[ -z "${XSA_OVERRIDE}" ]] || XSA_PATH="$(canonical_path "${XSA_OVERRIDE}")"
    require_file "${XSA_PATH}" "raw PL XSA"
    XSA_SHA256="$(sha256sum "${XSA_PATH}" | awk '{print $1}')"
    if [[ "${CONTRACT_MODE}" != true && \
          "${XSA_SHA256}" != "${MCONF_XSA_SHA256}" ]]; then
        die "Raw XSA does not match the XSA used by the selected mconf artifact"
    fi
fi

STAGING="$(new_temp_dir rpu)"
RUNTIME_BRIDGE="${APPLICATIONS_ROOT}/runtime-generated"
RUNTIME_BRIDGE_CREATED=false
cleanup() {
    if [[ "${RUNTIME_BRIDGE_CREATED}" == true ]]; then
        rm -f -- "${RUNTIME_BRIDGE}"
    fi
    rm -rf -- "${STAGING}"
}
trap cleanup EXIT
mkdir -p -- "${STAGING}/payload"
if [[ "${CONTRACT_MODE}" == true ]]; then
    CONTRACT_OUTPUT="${RUNTIME_DIR}/openamp_contract"
    rm -rf -- "${CONTRACT_OUTPUT}"
    for core in r5c0 r5c1; do
        python3 "${CONTRACT_TOOL}" generate-header \
            --contract "${CONTRACT_FILE}" \
            --core "${core}" \
            --output "${CONTRACT_OUTPUT}/${core}/openamp_contract.h"
        require_file \
            "${CONTRACT_OUTPUT}/${core}/openamp_contract.h" \
            "${core} OpenAMP contract header"
    done
    verify_contract_rpu_sources
else
    mkdir -p -- "${STAGING}/mconf"
    artifact_extract mconf "${MCONF_ARTIFACT}" "${STAGING}/mconf"
    require_file "${STAGING}/mconf/openamp_gen/psu_cortexr5_0/amd_platform_info.h" "mconf R5c0 OpenAMP header"
    require_file "${STAGING}/mconf/openamp_gen/psu_cortexr5_1/amd_platform_info.h" "mconf R5c1 OpenAMP header"
    copy_tree_fresh "${STAGING}/mconf/openamp_gen" "${RUNTIME_DIR}/openamp_gen"
    require_file "${RUNTIME_DIR}/openamp_gen/psu_cortexr5_0/amd_platform_info.h" "R5c0 OpenAMP header"
    require_file "${RUNTIME_DIR}/openamp_gen/psu_cortexr5_1/amd_platform_info.h" "R5c1 OpenAMP header"
fi

# Existing RPU components reference ../../../runtime-generated relative to
# <RPU>/R5c*/src. With repositories nested below applications/, that resolves
# to applications/runtime-generated instead of the workspace-root directory.
# Provide a build-only bridge and remove it on exit; never replace an existing
# path because it may contain user data.
if [[ -L "${RUNTIME_BRIDGE}" ]]; then
    if [[ "$(readlink -f -- "${RUNTIME_BRIDGE}")" != "${RUNTIME_DIR}" ]]; then
        die "Existing runtime bridge points to the wrong directory: ${RUNTIME_BRIDGE}"
    fi
elif [[ -e "${RUNTIME_BRIDGE}" ]]; then
    die "Cannot create runtime bridge because this path already exists: ${RUNTIME_BRIDGE}"
else
    ln -s -- "../runtime-generated" "${RUNTIME_BRIDGE}"
    RUNTIME_BRIDGE_CREATED=true
fi

if [[ -f "${RPU_ROOT}/.gitmodules" ]]; then
    RPU_SUBMODULE_STATUS="$(
        git -C "${RPU_ROOT}" submodule status --recursive
    )"
    if grep -q '^-' <<<"${RPU_SUBMODULE_STATUS}"; then
        die "RPU git submodules are not initialized; run git submodule update --init --recursive"
    fi
    if grep -Eq '^[+U]' <<<"${RPU_SUBMODULE_STATUS}"; then
        die "RPU git submodule checkout does not match the commit pinned by this RPU branch; run git submodule update --init --recursive"
    fi
fi

export XILINX_VITIS_DATA_DIR="${XILINX_VITIS_DATA_DIR:-${RUNTIME_DIR}/vitis-data}"
mkdir -p -- "${XILINX_VITIS_DATA_DIR}"

# Vitis may report a failed component build as a return status instead of an
# exception. Remove old build products first so a failed invocation can never
# pass the post-build ELF checks or publish stale firmware.
for core in R5c0 R5c1; do
    rm -f -- "${RPU_ROOT}/${core}/build/${core}.elf"
done

WRITE_PLATFORM_RECEIPT=false
if [[ "${ELF_ONLY}" == true ]]; then
    APP_BUILD_SCRIPT="${SCRIPT_DIR}/build_r5_apps.py"
    require_file "${APP_BUILD_SCRIPT}" "Vitis R5 application builder"
    require_dir "${RPU_ROOT}/R5c0" "R5c0 Vitis component"
    require_dir "${RPU_ROOT}/R5c1" "R5c1 Vitis component"
    (
        cd "${RPU_ROOT}"
        "${VITIS}" -s "${APP_BUILD_SCRIPT}" -- \
            --workspace "${RPU_ROOT}"
    )
else
    PLATFORM_SCRIPT="${RPU_ROOT}/${RPU_PLATFORM_SCRIPT_REL}"
    require_file "${PLATFORM_SCRIPT}" "Vitis platform generator"
    VITIS_INSTALL="${XILINX_VITIS:-/opt/Xilinx/${XILINX_VERSION:-2025.2}/Vitis}"
    (
        cd "${RPU_ROOT}"
        "${VITIS}" -s "${PLATFORM_SCRIPT}" -- \
            --xsa "${XSA_PATH}" \
            --workspace "${RPU_ROOT}" \
            --vitis-install "${VITIS_INSTALL}" \
            --force
    )
    require_dir "${RPU_ROOT}/platform" "generated Vitis platform"
    WRITE_PLATFORM_RECEIPT=true
fi

require_command readelf
for core in R5c0 R5c1; do
    ELF="${RPU_ROOT}/${core}/build/${core}.elf"
    require_file "${ELF}" "${core} firmware"
    readelf -h "${ELF}" | grep -q 'Class:.*ELF32' || die "${ELF} is not ELF32"
    readelf -h "${ELF}" | grep -q 'Machine:.*ARM' || die "${ELF} is not an ARM ELF"
    readelf -h "${ELF}" | grep -q 'Entry point address:.*0x0' || die "${ELF} entry point is not 0x0"
    readelf -S "${ELF}" | grep -q '\.resource_table' || die "${ELF} lacks .resource_table"
done

if [[ "${WRITE_PLATFORM_RECEIPT}" == true ]]; then
    PLATFORM_RECEIPT_TMP="${PLATFORM_RECEIPT}.tmp"
    {
        if [[ "${CONTRACT_MODE}" == true ]]; then
            printf 'schema=monutchee-platform-provenance-v2\n'
        else
            printf 'schema=monutchee-platform-provenance-v1\n'
        fi
        printf 'product=%s\n' "${PRODUCT}"
        if [[ "${CONTRACT_MODE}" == true ]]; then
            printf 'openamp_contract_sha256=%s\n' "${CONTRACT_SHA256}"
        else
            printf 'mconf_sha256=%s\n' "${MCONF_SHA256}"
        fi
        printf 'xsa_sha256=%s\n' "${XSA_SHA256}"
        printf 'xilinx_version=%s\n' "${XILINX_VERSION:-2025.2}"
    } > "${PLATFORM_RECEIPT_TMP}"
    mv -f -- "${PLATFORM_RECEIPT_TMP}" "${PLATFORM_RECEIPT}"
fi

for core in R5c0 R5c1; do
    ELF="${RPU_ROOT}/${core}/build/${core}.elf"
    cp -a -- "${ELF}" "${BIN_FILE_DIR}/${core}.elf"
    cp -a -- "${ELF}" "${STAGING}/payload/${core}.elf"
done

ARTIFACT_METADATA=(--metadata "xsa_sha256=${XSA_SHA256}")
if [[ "${CONTRACT_MODE}" == true ]]; then
    ARTIFACT_METADATA+=(
        --metadata "openamp_contract_sha256=${CONTRACT_SHA256}"
    )
else
    ARTIFACT_METADATA+=(--metadata "mconf_sha256=${MCONF_SHA256}")
fi
if [[ "${ELF_ONLY}" == true ]]; then
    ARTIFACT_METADATA+=(--metadata "build_mode=elf-only")
else
    ARTIFACT_METADATA+=(--metadata "build_mode=full")
fi
ARTIFACT="$(artifact_create_hashed rpu "${STAGING}/payload" "${ARTIFACT_BASE}" \
    "${ARTIFACT_METADATA[@]}")"
artifact_finalize_hashed rpu "${ARTIFACT_BASE}" "${ARTIFACT}"

log "RPU artifact: ${ARTIFACT}"
