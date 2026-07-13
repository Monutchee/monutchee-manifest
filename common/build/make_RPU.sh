#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_RPU.sh [OPTIONS]

Install generated machine data, generate OpenAMP headers, create the Vitis
platform, build both R5 applications, and package only the two ELF files.

Options:
  --workspace DIR        Product workspace root
  --product NAME         zudemo or kr260demo
  --xsa FILE             Raw XSA from make_PL.sh
  --mconf-artifact FILE  Input artifact from make_mconf.sh
  --artifact FILE        RPU artifact output path
  -h, --help             Show this help
EOF
}

WORKSPACE_ROOT="$(default_workspace_root)"
REQUESTED_PRODUCT=""
MCONF_ARTIFACT=""
ARTIFACT=""
XSA_OVERRIDE=""

while (($# > 0)); do
    case "$1" in
        --workspace) WORKSPACE_ROOT="$2"; shift 2 ;;
        --workspace=*) WORKSPACE_ROOT="${1#*=}"; shift ;;
        --product) REQUESTED_PRODUCT="$2"; shift 2 ;;
        --product=*) REQUESTED_PRODUCT="${1#*=}"; shift ;;
        --xsa) XSA_OVERRIDE="$2"; shift 2 ;;
        --xsa=*) XSA_OVERRIDE="${1#*=}"; shift ;;
        --mconf-artifact) MCONF_ARTIFACT="$2"; shift 2 ;;
        --mconf-artifact=*) MCONF_ARTIFACT="${1#*=}"; shift ;;
        --artifact) ARTIFACT="$2"; shift 2 ;;
        --artifact=*) ARTIFACT="${1#*=}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile "${REQUESTED_PRODUCT}"
load_xilinx_environment

MCONF_ARTIFACT="${MCONF_ARTIFACT:-${BIN_FILE_DIR}/${PRODUCT}_mconf.tar.gz}"
ARTIFACT="${ARTIFACT:-${BIN_FILE_DIR}/${PRODUCT}_rpu.tar.gz}"
[[ -z "${XSA_OVERRIDE}" ]] || XSA_PATH="$(canonical_path "${XSA_OVERRIDE}")"
require_file "${XSA_PATH}" "raw PL XSA"
require_dir "${RPU_ROOT}" "RPU repository"

STAGING="$(new_temp_dir rpu)"
BOOTSTRAP_RPU_FILES=()
cleanup() {
    local file
    for file in "${BOOTSTRAP_RPU_FILES[@]}"; do
        rm -f -- "${file}"
    done
    rm -rf -- "${STAGING}"
}
trap cleanup EXIT
mkdir -p -- "${STAGING}/mconf" "${STAGING}/payload"
artifact_extract mconf "${MCONF_ARTIFACT}" "${STAGING}/mconf"
copy_tree_fresh "${STAGING}/mconf/vivado_SDT_out" "${SDT_DIR}"

# esw-conf-native is built before the real RPU firmware. Supply parse-only
# placeholders for the local firmware recipe, then remove them immediately.
for core in R5c0 R5c1; do
    if [[ ! -e "${BIN_FILE_DIR}/${core}.elf" ]]; then
        : > "${BIN_FILE_DIR}/${core}.elf"
        BOOTSTRAP_RPU_FILES+=("${BIN_FILE_DIR}/${core}.elf")
    fi
done

(
    source_yocto_sdk
    install_machine_conf_payload "${STAGING}/mconf/yocto-conf"
    BITBAKE="${BITBAKE:-bitbake}"
    require_command "${BITBAKE}"
    MACHINE="${MACHINE}" "${BITBAKE}" esw-conf-native
)
for file in "${BOOTSTRAP_RPU_FILES[@]}"; do
    rm -f -- "${file}"
done
BOOTSTRAP_RPU_FILES=()

HEADER_SCRIPT="${RPU_ROOT}/${RPU_HEADER_SCRIPT_REL}"
PLATFORM_SCRIPT="${RPU_ROOT}/${RPU_PLATFORM_SCRIPT_REL}"
require_file "${HEADER_SCRIPT}" "OpenAMP header generator"
require_file "${PLATFORM_SCRIPT}" "Vitis platform generator"

if [[ -f "${RPU_ROOT}/.gitmodules" ]] && \
   git -C "${RPU_ROOT}" submodule status | grep -q '^-' ; then
    die "RPU git submodules are not initialized; run git submodule update --init --recursive"
fi

MACHINE="${MACHINE}" bash "${HEADER_SCRIPT}"
require_file "${RUNTIME_DIR}/openamp_gen/psu_cortexr5_0/amd_platform_info.h" "R5c0 OpenAMP header"
require_file "${RUNTIME_DIR}/openamp_gen/psu_cortexr5_1/amd_platform_info.h" "R5c1 OpenAMP header"

VITIS="${VITIS:-vitis}"
require_command "${VITIS}"
VITIS_INSTALL="${XILINX_VITIS:-/opt/Xilinx/${XILINX_VERSION:-2025.2}/Vitis}"
export XILINX_VITIS_DATA_DIR="${XILINX_VITIS_DATA_DIR:-${RUNTIME_DIR}/vitis-data}"
mkdir -p -- "${XILINX_VITIS_DATA_DIR}"

(
    cd "${RPU_ROOT}"
    "${VITIS}" -s "${PLATFORM_SCRIPT}" -- \
        --xsa "${XSA_PATH}" \
        --workspace "${RPU_ROOT}" \
        --vitis-install "${VITIS_INSTALL}" \
        --force
)

require_command readelf
for core in R5c0 R5c1; do
    ELF="${RPU_ROOT}/${core}/build/${core}.elf"
    require_file "${ELF}" "${core} firmware"
    readelf -h "${ELF}" | grep -q 'Class:.*ELF32' || die "${ELF} is not ELF32"
    readelf -h "${ELF}" | grep -q 'Machine:.*ARM' || die "${ELF} is not an ARM ELF"
    readelf -h "${ELF}" | grep -q 'Entry point address:.*0x0' || die "${ELF} entry point is not 0x0"
    readelf -S "${ELF}" | grep -q '\.resource_table' || die "${ELF} lacks .resource_table"
    cp -a -- "${ELF}" "${BIN_FILE_DIR}/${core}.elf"
    cp -a -- "${ELF}" "${STAGING}/payload/${core}.elf"
done

artifact_create rpu "${STAGING}/payload" "${ARTIFACT}" \
    --metadata "xsa_sha256=$(sha256sum "${XSA_PATH}" | awk '{print $1}')" \
    --metadata "mconf_sha256=$(sha256sum "${MCONF_ARTIFACT}" | awk '{print $1}')"

log "RPU artifact: ${ARTIFACT}"

