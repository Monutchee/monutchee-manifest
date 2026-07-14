#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_RPU.sh [OPTIONS]

Install the OpenAMP headers from the machine-config artifact, create the Vitis
platform from the XSA, build both R5 applications, and package the two ELFs.

Options:
  --workspace DIR        Product workspace root
  --product NAME         Product profile: zudemo, kr260demo, or msap1
  --xsa FILE             Bitstream-inclusive XSA exported from Vivado
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

VITIS="${VITIS:-vitis}"
load_xilinx_environment "${VITIS}"
require_command "${VITIS}"
require_command python3

MCONF_ARTIFACT="${MCONF_ARTIFACT:-${BIN_FILE_DIR}/${PRODUCT}_mconf.tar.gz}"
ARTIFACT="${ARTIFACT:-${BIN_FILE_DIR}/${PRODUCT}_rpu.tar.gz}"
[[ -z "${XSA_OVERRIDE}" ]] || XSA_PATH="$(canonical_path "${XSA_OVERRIDE}")"
require_file "${XSA_PATH}" "raw PL XSA"
require_dir "${RPU_ROOT}" "RPU repository"

STAGING="$(new_temp_dir rpu)"
trap 'rm -rf -- "${STAGING}"' EXIT
mkdir -p -- "${STAGING}/mconf" "${STAGING}/payload"
artifact_extract mconf "${MCONF_ARTIFACT}" "${STAGING}/mconf"
require_file "${STAGING}/mconf/openamp_gen/psu_cortexr5_0/amd_platform_info.h" "mconf R5c0 OpenAMP header"
require_file "${STAGING}/mconf/openamp_gen/psu_cortexr5_1/amd_platform_info.h" "mconf R5c1 OpenAMP header"
copy_tree_fresh "${STAGING}/mconf/openamp_gen" "${RUNTIME_DIR}/openamp_gen"
require_file "${RUNTIME_DIR}/openamp_gen/psu_cortexr5_0/amd_platform_info.h" "R5c0 OpenAMP header"
require_file "${RUNTIME_DIR}/openamp_gen/psu_cortexr5_1/amd_platform_info.h" "R5c1 OpenAMP header"

PLATFORM_SCRIPT="${RPU_ROOT}/${RPU_PLATFORM_SCRIPT_REL}"
require_file "${PLATFORM_SCRIPT}" "Vitis platform generator"

if [[ -f "${RPU_ROOT}/.gitmodules" ]] && \
   git -C "${RPU_ROOT}" submodule status | grep -q '^-' ; then
    die "RPU git submodules are not initialized; run git submodule update --init --recursive"
fi

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
