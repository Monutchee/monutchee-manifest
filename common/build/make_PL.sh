#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_PL.sh [OPTIONS]

Generate the SDT from an existing, user-exported XSA and package only the
SDTGen output. This command never opens Vivado or compiles the PL design.

Options:
  --workspace DIR   Product workspace root
  --product NAME    Product profile: zudemo, kr260demo, or msap1
  --xsa FILE        Input XSA exported from Vivado
  --artifact FILE   SDTGen artifact output path
  -h, --help        Show this help
EOF
}

WORKSPACE_ROOT="$(default_workspace_root)"
REQUESTED_PRODUCT=""
ARTIFACT=""
XSA_INPUT=""

while (($# > 0)); do
    case "$1" in
        --workspace) WORKSPACE_ROOT="$2"; shift 2 ;;
        --workspace=*) WORKSPACE_ROOT="${1#*=}"; shift ;;
        --product) REQUESTED_PRODUCT="$2"; shift 2 ;;
        --product=*) REQUESTED_PRODUCT="${1#*=}"; shift ;;
        --xsa) XSA_INPUT="$2"; shift 2 ;;
        --xsa=*) XSA_INPUT="${1#*=}"; shift ;;
        --artifact) ARTIFACT="$2"; shift 2 ;;
        --artifact=*) ARTIFACT="${1#*=}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile "${REQUESTED_PRODUCT}"

SDTGEN="${SDTGEN:-sdtgen}"
load_xilinx_environment "${SDTGEN}"
require_command "${SDTGEN}"
require_command python3
require_command unzip

ARTIFACT="${ARTIFACT:-${BIN_FILE_DIR}/${PRODUCT}_pl_sdtgen.tar.gz}"
if [[ -n "${XSA_INPUT}" ]]; then
    XSA_INPUT="$(canonical_path "${XSA_INPUT}")"
else
    XSA_INPUT="${XSA_PATH}"
fi

require_file "${XSA_INPUT}" "bitstream-inclusive XSA exported from Vivado"
mkdir -p -- "${BIN_FILE_DIR}"

log "Generating ${PRODUCT} SDT from user-exported XSA: ${XSA_INPUT}"
unzip -tqq "${XSA_INPUT}" || die "Input XSA is not a valid archive: ${XSA_INPUT}"

rm -rf -- "${SDT_DIR}"
mkdir -p -- "${SDT_DIR}"
case "${SDT_MODE}" in
    user_dts)
        SDT_VALUE="${WORKSPACE_ROOT}/${SDT_VALUE_REL}"
        require_file "${SDT_VALUE}" "SDT user DTS"
        "${SDTGEN}" -xsa "${XSA_INPUT}" -dir "${SDT_DIR}" -user_dts "${SDT_VALUE}"
        ;;
    board_dts)
        "${SDTGEN}" -xsa "${XSA_INPUT}" -dir "${SDT_DIR}" -board_dts "${SDT_VALUE_REL}"
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
    --metadata "xsa_name=$(basename -- "${XSA_INPUT}")" \
    --metadata "xsa_sha256=$(sha256sum "${XSA_INPUT}" | awk '{print $1}')"

log "Input XSA: ${XSA_INPUT}"
log "SDTGen artifact: ${ARTIFACT}"
