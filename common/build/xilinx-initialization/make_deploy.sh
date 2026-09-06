#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_deploy.sh [OPTIONS]

Deploy a Yocto Station artifact to a target. The first supported deployment
type is Xilinx JTAG boot through a local Provisioning Station agent.

Options:
  --workspace DIR                  Product workspace root
  --product NAME                   Product profile
  --type jtag                      Deployment type
  --jtag                            Alias for --type jtag
  --station-url URL                Local Station HTTP API
  --station-token-file FILE        File containing the Station API token
  --artifact FILE                  Station artifact (.tar.gz)
  --xilinx-hw-server-url URL       tcp:<host>:<port> for Xilinx hw_server
  --xilinx-target-id ID            Current XSDB PSU target ID
  --xilinx-target-serial SERIAL    Stable JTAG cable serial (recommended)
  --tftp-server-ip IP              IPv4 address of the Station machine
  --board-ip IP                    Optional target IPv4 override

Compatibility options:
  --xilinx-hw-server-ip IP         Maps to tcp:<IP>:3121
  --tftp-machine-ip IP             Alias for --tftp-server-ip
  -h, --help                       Show this help

Normally these values come from MncBuildPreset.yaml, so deployment is simply:

  mnc deploy

Command-line values override the preset:

  mnc deploy jtag --xilinx-hw-server-url tcp:172.30.19.20:3121 \
                  --tftp-server-ip 172.30.19.19

MncBuildPreset.yaml may provide station_token or station_token_file when the
agent requires authentication. MNC_STATION_TOKEN and MNC_STATION_TOKEN_FILE
remain available as environment overrides.
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

deploy_legacy_jtag() {
    local tftp_dir loader xsdb
    local -a arguments

    if [[ -z "${XILINX_HW_SERVER_IP}" && \
          "${XILINX_HW_SERVER_URL}" =~ ^tcp:([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+):3121$ ]]; then
        XILINX_HW_SERVER_IP="${BASH_REMATCH[1]}"
    fi
    [[ -n "${XILINX_HW_SERVER_IP}" ]] || \
        die "Legacy JTAG deploy requires --xilinx-hw-server-ip; this product has no Station artifact recipe yet"
    validate_ipv4 "Xilinx hw_server IP" "${XILINX_HW_SERVER_IP}"

    tftp_dir="${YOCTO_BUILD_DIR}/export/tftpboot"
    loader="${tftp_dir}/load-jtag-image.tcl"
    require_dir "${tftp_dir}" "Yocto TFTP export directory"
    require_file "${loader}" "JTAG image loader"
    xsdb="${XSDB:-xsdb}"
    load_xilinx_environment "${xsdb}"
    require_command "${xsdb}"

    warn "${PRODUCT} has no Station artifact profile; using the legacy direct XSDB flow"
    (
        cd "${tftp_dir}"
        arguments=(./load-jtag-image.tcl "${XILINX_HW_SERVER_IP}" "${TFTP_SERVER_IP}")
        [[ -z "${BOARD_IP}" ]] || arguments+=("${BOARD_IP}")
        "${xsdb}" "${arguments[@]}"
    )
}

WORKSPACE_ROOT="$(default_workspace_root)"
REQUESTED_PRODUCT=""
DEPLOY_TYPE=""
STATION_URL="${MNC_STATION_URL:-http://127.0.0.1:8042}"
STATION_TOKEN_FILE="${MNC_STATION_TOKEN_FILE:-}"
JTAG_ARTIFACT=""
XILINX_HW_SERVER_URL=""
XILINX_HW_SERVER_IP=""
XILINX_TARGET_ID=""
XILINX_TARGET_SERIAL=""
TFTP_SERVER_IP=""
BOARD_IP=""

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
        --station-url)
            require_option_value --station-url "${@:2:1}"
            STATION_URL="$2"; shift 2 ;;
        --station-url=*) STATION_URL="${1#*=}"; shift ;;
        --station-token-file)
            require_option_value --station-token-file "${@:2:1}"
            STATION_TOKEN_FILE="$2"; shift 2 ;;
        --station-token-file=*) STATION_TOKEN_FILE="${1#*=}"; shift ;;
        --artifact)
            require_option_value --artifact "${@:2:1}"
            JTAG_ARTIFACT="$2"; shift 2 ;;
        --artifact=*) JTAG_ARTIFACT="${1#*=}"; shift ;;
        --xilinx-hw-server-url)
            require_option_value --xilinx-hw-server-url "${@:2:1}"
            XILINX_HW_SERVER_URL="$2"
            XILINX_HW_SERVER_IP=""
            shift 2 ;;
        --xilinx-hw-server-url=*)
            XILINX_HW_SERVER_URL="${1#*=}"
            XILINX_HW_SERVER_IP=""
            shift ;;
        --xilinx-hw-server-ip)
            require_option_value --xilinx-hw-server-ip "${@:2:1}"
            XILINX_HW_SERVER_IP="$2"
            XILINX_HW_SERVER_URL="tcp:$2:3121"
            shift 2 ;;
        --xilinx-hw-server-ip=*)
            XILINX_HW_SERVER_IP="${1#*=}"
            XILINX_HW_SERVER_URL="tcp:${1#*=}:3121"
            shift ;;
        --xilinx-target-id)
            require_option_value --xilinx-target-id "${@:2:1}"
            XILINX_TARGET_ID="$2"; shift 2 ;;
        --xilinx-target-id=*) XILINX_TARGET_ID="${1#*=}"; shift ;;
        --xilinx-target-serial)
            require_option_value --xilinx-target-serial "${@:2:1}"
            XILINX_TARGET_SERIAL="$2"; shift 2 ;;
        --xilinx-target-serial=*) XILINX_TARGET_SERIAL="${1#*=}"; shift ;;
        --tftp-server-ip)
            require_option_value --tftp-server-ip "${@:2:1}"
            TFTP_SERVER_IP="$2"; shift 2 ;;
        --tftp-server-ip=*) TFTP_SERVER_IP="${1#*=}"; shift ;;
        --tftp-machine-ip)
            require_option_value --tftp-machine-ip "${@:2:1}"
            TFTP_SERVER_IP="$2"; shift 2 ;;
        --tftp-machine-ip=*) TFTP_SERVER_IP="${1#*=}"; shift ;;
        --board-ip)
            require_option_value --board-ip "${@:2:1}"
            BOARD_IP="$2"; shift 2 ;;
        --board-ip=*) BOARD_IP="${1#*=}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown deploy option: $1" ;;
    esac
done

WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile "${REQUESTED_PRODUCT}"

[[ -n "${DEPLOY_TYPE}" ]] || die "No deploy type configured; set stages.deploy.type to jtag"
[[ "${DEPLOY_TYPE}" == jtag ]] || \
    die "Unsupported deploy type '${DEPLOY_TYPE}'; currently only jtag is available"
[[ -n "${XILINX_HW_SERVER_URL}" ]] || \
    die "JTAG deploy needs --xilinx-hw-server-url or stages.deploy.xilinx_hw_server_url"
[[ -n "${TFTP_SERVER_IP}" ]] || \
    die "JTAG deploy needs --tftp-server-ip or stages.deploy.tftp_server_ip"
[[ -z "${XILINX_HW_SERVER_IP}" ]] || \
    validate_ipv4 "Xilinx hw_server IP" "${XILINX_HW_SERVER_IP}"
[[ -z "${XILINX_TARGET_ID}" || "${XILINX_TARGET_ID}" =~ ^[1-9][0-9]*$ ]] || \
    die "Xilinx target ID must be a positive decimal integer: ${XILINX_TARGET_ID}"
[[ -z "${XILINX_TARGET_ID}" || -z "${XILINX_TARGET_SERIAL}" ]] || \
    die "Set either --xilinx-target-id or --xilinx-target-serial, not both"
validate_ipv4 "TFTP server IP" "${TFTP_SERVER_IP}"
[[ -z "${BOARD_IP}" ]] || validate_ipv4 "Board IP" "${BOARD_IP}"

if [[ -z "${JTAG_ARTIFACT_NAME:-}" ]]; then
    deploy_legacy_jtag
    log "JTAG deployment completed"
    exit 0
fi

if [[ -z "${JTAG_ARTIFACT}" ]]; then
    JTAG_ARTIFACT="${YOCTO_BUILD_DIR}/export/provision-image/${JTAG_ARTIFACT_NAME}"
fi
require_file "${JTAG_ARTIFACT}" "Station JTAG artifact"
JTAG_ARTIFACT="$(canonical_path "${JTAG_ARTIFACT}")"
require_file "${SCRIPT_DIR}/station_client.py" "Station deploy client"
require_command python3

log "Deploying ${PRODUCT} through the Provisioning Station"
log "Station API:       ${STATION_URL}"
log "Xilinx hw_server:  ${XILINX_HW_SERVER_URL}"
log "TFTP server IP:    ${TFTP_SERVER_IP}"
log "Station artifact:  ${JTAG_ARTIFACT}"

declare -a arguments
arguments=(
    "${SCRIPT_DIR}/station_client.py"
    --station-url "${STATION_URL}"
    --artifact "${JTAG_ARTIFACT}"
    --hw-server-url "${XILINX_HW_SERVER_URL}"
    --tftp-server-ip "${TFTP_SERVER_IP}"
)
[[ -z "${STATION_TOKEN_FILE}" ]] || \
    arguments+=(--token-file "${STATION_TOKEN_FILE}")
[[ -z "${XILINX_TARGET_ID}" ]] || \
    arguments+=(--target-id "${XILINX_TARGET_ID}")
[[ -z "${XILINX_TARGET_SERIAL}" ]] || \
    arguments+=(--target-serial "${XILINX_TARGET_SERIAL}")
[[ -z "${BOARD_IP}" ]] || arguments+=(--board-ip "${BOARD_IP}")
python3 "${arguments[@]}"
log "JTAG deployment completed"
