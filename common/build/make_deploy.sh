#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_deploy.sh [OPTIONS]

Deploy the exported Yocto image to a target. The only deployment type
currently supported is JTAG.

Options:
  --workspace DIR              Product workspace root
  --product NAME               Product profile
  --type jtag                   Deployment type
  --jtag                        Alias for --type jtag
  --xilinx-hw-server-ip IP     Machine running Xilinx hw_server
  --tftp-machine-ip IP         Machine serving the TFTP boot files
  -h, --help                    Show this help

Normally these values come from MncBuildPreset.yaml, so deployment is simply:

  mnc deploy

Command-line values override the preset:

  mnc deploy jtag --xilinx-hw-server-ip 172.30.19.20 \
                  --tftp-machine-ip 172.30.19.19
EOF
}

require_option_value() {
    local option="$1"
    shift
    (($# > 0)) && [[ -n "$1" ]] || die "${option} requires a value"
}

validate_ipv4() {
    local label="$1" value="$2" octet
    local -a octets=()

    IFS=. read -ra octets <<< "${value}"
    ((${#octets[@]} == 4)) || die "${label} is not an IPv4 address: ${value}"
    for octet in "${octets[@]}"; do
        [[ "${octet}" =~ ^[0-9]+$ ]] && ((10#${octet} <= 255)) || \
            die "${label} is not an IPv4 address: ${value}"
    done
}

WORKSPACE_ROOT="$(default_workspace_root)"
REQUESTED_PRODUCT=""
DEPLOY_TYPE=""
XILINX_HW_SERVER_IP=""
TFTP_MACHINE_IP=""

while (($# > 0)); do
    case "$1" in
        --workspace)
            require_option_value --workspace "${@:2:1}"
            WORKSPACE_ROOT="$2"; shift 2 ;;
        --workspace=*) WORKSPACE_ROOT="${1#*=}"; shift ;;
        --product)
            require_option_value --product "${@:2:1}"
            REQUESTED_PRODUCT="$2"; shift 2 ;;
        --product=*) REQUESTED_PRODUCT="${1#*=}"; shift ;;
        --type)
            require_option_value --type "${@:2:1}"
            DEPLOY_TYPE="$2"; shift 2 ;;
        --type=*) DEPLOY_TYPE="${1#*=}"; shift ;;
        --jtag) DEPLOY_TYPE=jtag; shift ;;
        --xilinx-hw-server-ip)
            require_option_value --xilinx-hw-server-ip "${@:2:1}"
            XILINX_HW_SERVER_IP="$2"; shift 2 ;;
        --xilinx-hw-server-ip=*) XILINX_HW_SERVER_IP="${1#*=}"; shift ;;
        --tftp-machine-ip)
            require_option_value --tftp-machine-ip "${@:2:1}"
            TFTP_MACHINE_IP="$2"; shift 2 ;;
        --tftp-machine-ip=*) TFTP_MACHINE_IP="${1#*=}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown deploy option: $1" ;;
    esac
done

WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile "${REQUESTED_PRODUCT}"

[[ -n "${DEPLOY_TYPE}" ]] || die "No deploy type configured; set stages.deploy.type to jtag"
[[ "${DEPLOY_TYPE}" == jtag ]] || \
    die "Unsupported deploy type '${DEPLOY_TYPE}'; currently only jtag is available"
[[ -n "${XILINX_HW_SERVER_IP}" ]] || \
    die "JTAG deploy needs --xilinx-hw-server-ip or stages.deploy.xilinx_hw_server_ip"
[[ -n "${TFTP_MACHINE_IP}" ]] || \
    die "JTAG deploy needs --tftp-machine-ip or stages.deploy.tftp_machine_ip"
validate_ipv4 "Xilinx hw_server IP" "${XILINX_HW_SERVER_IP}"
validate_ipv4 "TFTP machine IP" "${TFTP_MACHINE_IP}"

TFTP_DIR="${YOCTO_BUILD_DIR}/export/tftpboot"
LOADER="${TFTP_DIR}/load-jtag-image.tcl"
require_dir "${TFTP_DIR}" "Yocto TFTP export directory"
require_file "${LOADER}" "JTAG image loader"

XSDB="${XSDB:-xsdb}"
load_xilinx_environment "${XSDB}"
require_command "${XSDB}"

log "Deploying ${PRODUCT} through JTAG"
log "Xilinx hw_server: ${XILINX_HW_SERVER_IP}"
log "TFTP machine:     ${TFTP_MACHINE_IP}"
log "JTAG bundle:      ${TFTP_DIR}"
(
    cd "${TFTP_DIR}"
    "${XSDB}" ./load-jtag-image.tcl \
        "${XILINX_HW_SERVER_IP}" "${TFTP_MACHINE_IP}"
)
log "JTAG deployment completed"
