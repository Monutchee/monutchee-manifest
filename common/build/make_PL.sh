#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_PL.sh [OPTIONS]

Build the Vivado PL design, export the raw XSA, generate the SDT, and package
only the SDTGen output.

Options:
  --workspace DIR   Product workspace root
  --product NAME    zudemo or kr260demo
  --jobs COUNT      Vivado implementation jobs (default: VIVADO_JOBS or nproc)
  --artifact FILE   SDTGen artifact output path
  -h, --help        Show this help
EOF
}

WORKSPACE_ROOT="$(default_workspace_root)"
REQUESTED_PRODUCT=""
JOBS="${VIVADO_JOBS:-}"
ARTIFACT=""

while (($# > 0)); do
    case "$1" in
        --workspace) WORKSPACE_ROOT="$2"; shift 2 ;;
        --workspace=*) WORKSPACE_ROOT="${1#*=}"; shift ;;
        --product) REQUESTED_PRODUCT="$2"; shift 2 ;;
        --product=*) REQUESTED_PRODUCT="${1#*=}"; shift ;;
        --jobs) JOBS="$2"; shift 2 ;;
        --jobs=*) JOBS="${1#*=}"; shift ;;
        --artifact) ARTIFACT="$2"; shift 2 ;;
        --artifact=*) ARTIFACT="${1#*=}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile "${REQUESTED_PRODUCT}"
load_xilinx_environment

VIVADO="${VIVADO:-vivado}"
SDTGEN="${SDTGEN:-sdtgen}"
require_command "${VIVADO}"
require_command "${SDTGEN}"
require_command python3
require_command unzip

[[ -n "${JOBS}" ]] || JOBS="$(nproc)"
[[ "${JOBS}" =~ ^[1-9][0-9]*$ ]] || die "--jobs must be a positive integer"
ARTIFACT="${ARTIFACT:-${BIN_FILE_DIR}/${PRODUCT}_pl_sdtgen.tar.gz}"

XPR_PATH="${PL_ROOT}/${PL_XPR_REL}"
require_file "${XPR_PATH}" "Vivado project"
mkdir -p -- "${BIN_FILE_DIR}"

log "Building ${PRODUCT} PL design"
"${VIVADO}" -mode batch -nolog -nojournal \
    -source "${SCRIPT_DIR}/export_xsa.tcl" \
    -tclargs "${XPR_PATH}" "${XSA_PATH}" "${PL_IMPL_RUN}" "${JOBS}" "${PL_BOARD_PART}"

require_file "${XSA_PATH}" "generated XSA"
unzip -tqq "${XSA_PATH}" || die "Generated XSA is not a valid archive: ${XSA_PATH}"

rm -rf -- "${SDT_DIR}"
mkdir -p -- "${SDT_DIR}"
case "${SDT_MODE}" in
    user_dts)
        SDT_VALUE="${WORKSPACE_ROOT}/${SDT_VALUE_REL}"
        require_file "${SDT_VALUE}" "SDT user DTS"
        "${SDTGEN}" -xsa "${XSA_PATH}" -dir "${SDT_DIR}" -user_dts "${SDT_VALUE}"
        ;;
    board_dts)
        "${SDTGEN}" -xsa "${XSA_PATH}" -dir "${SDT_DIR}" -board_dts "${SDT_VALUE_REL}"
        ;;
    *) die "Unsupported SDT mode in product profile: ${SDT_MODE}" ;;
esac

require_file "${SDT_DIR}/system-top.dts" "SDT system-top.dts"
require_file "${SDT_DIR}/${PROJECT_PREFIX}_PL.bit" "SDT bitstream"
require_file "${SDT_DIR}/psu_init.c" "SDT PSU initialization source"

STAGING="$(new_temp_dir pl-sdtgen)"
trap 'rm -rf -- "${STAGING}"' EXIT
mkdir -p -- "${STAGING}/payload/vivado_SDT_out"
cp -a -- "${SDT_DIR}/." "${STAGING}/payload/vivado_SDT_out/"

artifact_create pl_sdtgen "${STAGING}/payload" "${ARTIFACT}" \
    --metadata "xsa_name=${PL_XSA_BASENAME}" \
    --metadata "xsa_sha256=$(sha256sum "${XSA_PATH}" | awk '{print $1}')" \
    --metadata "vivado_version=$("${VIVADO}" -version 2>/dev/null | head -1)"

log "Raw XSA: ${XSA_PATH}"
log "SDTGen artifact: ${ARTIFACT}"

