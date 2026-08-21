#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_HLS.sh [OPTIONS]

Rebuild every Vitis HLS component under the PL repository's
SourceData/HLS_DesignFile tree through the Vitis Python CLI (no GUI).
Components are discovered from their vitis-comp.json descriptors; the
gitignored _ide workspace metadata is recreated automatically on a fresh
clone. Each component runs C simulation, C synthesis, C/RTL co-simulation,
and IP packaging, then its packaged IP is unpacked into the
SourceData/HLS_DesignFile/ip_repo Vivado IP repository.

Afterwards the Vivado project is refreshed (update_ip_catalog -rebuild
plus upgrading stale HLS IP customizations) so the next synthesis or
bitstream run consumes the newest packaged output with no manual steps.
Vivado does not lock projects: if a Vivado session is running, the
refresh is NOT applied from here -- source the printed script in that
session's Tcl console instead.

Run this before 'mnc PL build' whenever HLS sources changed or on a fresh
checkout (the repository content is generated output and is not tracked).

Options:
  --workspace DIR    Product workspace root
  --product NAME     Product profile: zudemo, kr260demo, or msap1
  --component NAME   Rebuild only this component (repeatable)
  --skip-csim        Skip C simulation (verification escape hatch)
  --skip-cosim       Skip C/RTL co-simulation (verification escape hatch)
  -h, --help         Show this help
EOF
}

WORKSPACE_ROOT="$(default_workspace_root)"
REQUESTED_PRODUCT=""
COMPONENTS=""
SKIP_CSIM=false
SKIP_COSIM=false

while (($# > 0)); do
    case "$1" in
        --workspace) WORKSPACE_ROOT="$2"; shift 2 ;;
        --workspace=*) WORKSPACE_ROOT="${1#*=}"; shift ;;
        --product) REQUESTED_PRODUCT="$2"; shift 2 ;;
        --product=*) REQUESTED_PRODUCT="${1#*=}"; shift ;;
        --component) COMPONENTS="${COMPONENTS:+${COMPONENTS},}$2"; shift 2 ;;
        --component=*) COMPONENTS="${COMPONENTS:+${COMPONENTS},}${1#*=}"; shift ;;
        --skip-csim) SKIP_CSIM=true; shift ;;
        --skip-cosim) SKIP_COSIM=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile "${REQUESTED_PRODUCT}"

VITIS="${VITIS:-vitis}"
load_xilinx_environment "${VITIS}"
require_command "${VITIS}"

HLS_ROOT="${PL_ROOT}/SourceData/HLS_DesignFile"
require_dir "${PL_ROOT}" "PL repository"
require_dir "${HLS_ROOT}" "PL HLS component tree"

BUILD_SCRIPT="${SCRIPT_DIR}/build_hls_components.py"
require_file "${BUILD_SCRIPT}" "Vitis HLS component builder"

if ! find "${HLS_ROOT}" -name vitis-comp.json -not -path '*/_ide/*' \
        -not -path '*/build/*' -print -quit | grep -q .; then
    die "No HLS components (vitis-comp.json) found below ${HLS_ROOT}"
fi

log "HLS inputs: root=${HLS_ROOT} components=${COMPONENTS:-all} csim=$([[ "${SKIP_CSIM}" == true ]] && printf skipped || printf on) cosim=$([[ "${SKIP_COSIM}" == true ]] && printf skipped || printf on)"
build_progress 0 "discovering and building HLS components"

export XILINX_VITIS_DATA_DIR="${XILINX_VITIS_DATA_DIR:-${RUNTIME_DIR}/vitis-data}"
mkdir -p -- "${XILINX_VITIS_DATA_DIR}"

BUILD_ARGS=(--workspace "${HLS_ROOT}")
[[ -z "${COMPONENTS}" ]] || BUILD_ARGS+=(--components "${COMPONENTS}")
[[ "${SKIP_CSIM}" != true ]] || BUILD_ARGS+=(--skip-csim)
[[ "${SKIP_COSIM}" != true ]] || BUILD_ARGS+=(--skip-cosim)

(
    cd "${HLS_ROOT}"
    "${VITIS}" -s "${BUILD_SCRIPT}" -- "${BUILD_ARGS[@]}"
)
log "HLS IP repository is current: ${HLS_ROOT}/ip_repo"
build_progress 90 "refreshing the Vivado IP catalog"

# Vivado-side refresh: rebuild the IP catalog and upgrade stale HLS IP
# customizations so the next synthesis consumes the newest packaged output.
# Vivado does not lock projects, and a live GUI session saves its own state
# over batch edits, so never touch the project while any Vivado runs.
REFRESH_SCRIPT="${PL_ROOT}/SourceData/Script/refresh_hls_ip.tcl"
REGISTER_SCRIPT="${PL_ROOT}/SourceData/Script/register_hls_components.tcl"
VIVADO="${VIVADO:-vivado}"
if [[ ! -f "${REFRESH_SCRIPT}" ]]; then
    log "No ${REFRESH_SCRIPT}; skipping the Vivado catalog refresh"
elif vivado_session_running; then
    # Loud, because the consequence is delayed: packaging stamped a new core
    # revision, so every customization of it is now locked, and a locked IP
    # fails synthesis minutes later with an error that names neither HLS nor
    # the revision. The PL build stages repair it themselves, so this is a
    # warning rather than a failure.
    warn "A Vivado session of this user is running, so the IP catalog was NOT refreshed."
    warn "The packaged revision changed, which leaves every customization of it locked."
    warn "Apply it in that session's Tcl console:"
    warn "  source ${REFRESH_SCRIPT}"
    warn "Otherwise the next 'mnc PL build' repairs it before synthesizing."
elif ! command -v "${VIVADO}" >/dev/null 2>&1; then
    log "vivado is not available; apply the refresh later with:"
    log "  vivado -mode batch -source ${REFRESH_SCRIPT}"
else
    log "Refreshing the Vivado IP catalog"
    (
        cd "${PL_ROOT}"
        "${VIVADO}" -mode batch -nojournal -nolog -source "${REFRESH_SCRIPT}"
    )
fi
if [[ -f "${REGISTER_SCRIPT}" ]]; then
    log "Added a NEW HLS component? Register it with the project once:"
    log "  source ${REGISTER_SCRIPT}   (Vivado Tcl console; or vivado -mode batch -source ...)"
fi

log "HLS components are ready; run 'mnc PL build' for the XSA/bitstream"
build_progress 100 "HLS components ready"
build_summary "HLS components=${COMPONENTS:-all}; csim=$([[ "${SKIP_CSIM}" == true ]] && printf skipped || printf enabled); cosim=$([[ "${SKIP_COSIM}" == true ]] && printf skipped || printf enabled); ip_repo=${HLS_ROOT}/ip_repo"
