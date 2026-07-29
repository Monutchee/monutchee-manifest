#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_mconf.sh [OPTIONS]

Consume the PL SDTGen artifact, generate portable Yocto machine configuration,
and package those outputs with the SDTGen data.

Options:
  --workspace DIR             Product workspace root
  --product NAME              Product profile: zudemo, kr260demo, or msap1
  --pl-sdtgen-artifact FILE   Input artifact from make_PL.sh
  --artifact FILE             Artifact basename; _<sha256[:6]> is appended
  -h, --help                  Show this help
EOF
}

WORKSPACE_ROOT="$(default_workspace_root)"
REQUESTED_PRODUCT=""
PL_SDTGEN_ARTIFACT=""
ARTIFACT=""

while (($# > 0)); do
    case "$1" in
        --workspace) WORKSPACE_ROOT="$2"; shift 2 ;;
        --workspace=*) WORKSPACE_ROOT="${1#*=}"; shift ;;
        --product) REQUESTED_PRODUCT="$2"; shift 2 ;;
        --product=*) REQUESTED_PRODUCT="${1#*=}"; shift ;;
        --pl-sdtgen-artifact) PL_SDTGEN_ARTIFACT="$2"; shift 2 ;;
        --pl-sdtgen-artifact=*) PL_SDTGEN_ARTIFACT="${1#*=}"; shift ;;
        --artifact) ARTIFACT="$2"; shift 2 ;;
        --artifact=*) ARTIFACT="${1#*=}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile "${REQUESTED_PRODUCT}"
require_command python3

if [[ -z "${PL_SDTGEN_ARTIFACT}" ]]; then
    PL_SDTGEN_ARTIFACT="$(
        artifact_select_latest "${PRODUCT}_pl_sdtgen_*.tar.gz"
    )"
fi
ARTIFACT_BASE="${ARTIFACT:-${BIN_FILE_DIR}/${PRODUCT}_mconf.tar.gz}"

STAGING="$(new_temp_dir mconf)"
BOOTSTRAP_RPU_FILES=()
cleanup() {
    local file
    for file in "${BOOTSTRAP_RPU_FILES[@]}"; do
        rm -f -- "${file}"
    done
    rm -rf -- "${STAGING}"
}
trap cleanup EXIT
mkdir -p -- "${STAGING}/input" "${STAGING}/generated-conf" "${STAGING}/work" "${STAGING}/payload"
artifact_extract pl_sdtgen "${PL_SDTGEN_ARTIFACT}" "${STAGING}/input"
require_file "${STAGING}/input/vivado_SDT_out/system-top.dts" "SDTGen system-top.dts"
copy_tree_fresh "${STAGING}/input/vivado_SDT_out" "${SDT_DIR}"

CONTRACT_MODE=false
CONTRACT_FILE=""
CONTRACT_SHA256=""
CONTRACT_TOOL="${SCRIPT_DIR}/openamp_contract.py"
if [[ -n "${OPENAMP_CONTRACT_REL:-}" ]]; then
    CONTRACT_MODE=true
    CONTRACT_FILE="${SCRIPT_DIR}/${OPENAMP_CONTRACT_REL}"
    require_file "${CONTRACT_TOOL}" "OpenAMP contract tool"
    require_file "${CONTRACT_FILE}" "OpenAMP contract"
    python3 "${CONTRACT_TOOL}" validate-contract --contract "${CONTRACT_FILE}"
    CONTRACT_SHA256="$(
        python3 "${CONTRACT_TOOL}" contract-digest --contract "${CONTRACT_FILE}"
    )"
    log "OpenAMP contract input: ${CONTRACT_FILE} openamp_contract_sha256=${CONTRACT_SHA256}"
    DOMAIN_FILE="${STAGING}/work/openamp-domain.yaml"
    python3 "${CONTRACT_TOOL}" generate-domain \
        --contract "${CONTRACT_FILE}" \
        --output "${DOMAIN_FILE}"
    python3 "${CONTRACT_TOOL}" verify-domain \
        --contract "${CONTRACT_FILE}" \
        --domain "${DOMAIN_FILE}"
    DOMAIN_SHA256="$(sha256sum "${DOMAIN_FILE}" | awk '{print $1}')"
    log "OpenAMP domain generated: ${DOMAIN_FILE} domain_sha256=${DOMAIN_SHA256}"
else
    DOMAIN_FILE="${WORKSPACE_ROOT}/${MCONF_DOMAIN_REL}"
fi
require_file "${DOMAIN_FILE}" "OpenAMP machine-conf domain file"
if [[ -n "${MCONF_TEMPLATE_REL}" ]]; then
    TEMPLATE_FILE="${WORKSPACE_ROOT}/${MCONF_TEMPLATE_REL}"
    require_file "${TEMPLATE_FILE}" "machine-conf template"
else
    TEMPLATE_FILE=""
fi

# BitBake validates every local file:// URI while gen-machineconf bootstraps its
# native tools. The real RPU ELFs are deliberately produced by the next stage,
# so provide parse-only placeholders and remove only the files created here.
for core in R5c0 R5c1; do
    if [[ ! -e "${BIN_FILE_DIR}/${core}.elf" ]]; then
        : > "${BIN_FILE_DIR}/${core}.elf"
        BOOTSTRAP_RPU_FILES+=("${BIN_FILE_DIR}/${core}.elf")
    fi
done

(
    source_yocto_sdk
    GEN_MACHINECONF="${GEN_MACHINECONF:-gen-machineconf}"
    require_command "${GEN_MACHINECONF}"
    ARGS=(
        parse-sdt
        --hw-description "${SDT_DIR}"
        --config-dir "${STAGING}/generated-conf"
        --output "${STAGING}/work"
        --machine-name "${MACHINE}"
        --gen-pl-overlay full
        --add-config CONFIG_YOCTO_BBMC_CORTEXR5_0_FREERTOS=y
        --add-config CONFIG_YOCTO_BBMC_CORTEXR5_1_FREERTOS=y
        --domain-file "${DOMAIN_FILE}"
    )
    [[ -z "${TEMPLATE_FILE}" ]] || ARGS+=(--template "${TEMPLATE_FILE}")
    "${GEN_MACHINECONF}" "${ARGS[@]}"
)

MACHINE_CONF="${STAGING}/generated-conf/machine/${MACHINE}.conf"
require_file "${MACHINE_CONF}" "generated machine configuration"
require_dir "${STAGING}/generated-conf/dts/${MACHINE}" "generated machine DTS"
require_dir "${STAGING}/generated-conf/machine/include/${MACHINE}" "generated machine includes"

python3 - "${MACHINE_CONF}" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()
text, uri_count = re.subn(
    r'^SDT_URI\s*=.*$',
    'SDT_URI = "file://${TOPDIR}/../../runtime-generated/vivado_SDT_out"',
    text,
    flags=re.MULTILINE,
)
text, source_count = re.subn(
    r'^SDT_URI\[S\]\s*=.*$',
    'SDT_URI[S] = "${WORKDIR}${TOPDIR}/../../runtime-generated/vivado_SDT_out"',
    text,
    flags=re.MULTILINE,
)
if uri_count != 1 or source_count != 1:
    raise SystemExit(
        f"expected one SDT_URI and SDT_URI[S], replaced {uri_count} and {source_count}"
    )
path.write_text(text)
PY

if grep -R -F -- "${WORKSPACE_ROOT}" "${STAGING}/generated-conf" >/dev/null 2>&1; then
    die "Generated machine configuration still contains producer workspace paths"
fi

if [[ "${CONTRACT_MODE}" == true ]]; then
    python3 "${CONTRACT_TOOL}" verify-generated \
        --contract "${CONTRACT_FILE}" \
        --directory "${STAGING}/generated-conf/dts/${MACHINE}"
else
    HEADER_SCRIPT="${RPU_ROOT}/${RPU_HEADER_SCRIPT_REL}"
    require_file "${HEADER_SCRIPT}" "OpenAMP header generator"
    OPENAMP_WORK="${RUNTIME_DIR}/openamp_gen"
    rm -rf -- "${OPENAMP_WORK}"

    # Legacy products still use their RPU repository helper and the
    # gen-machineconf-provided native Lopper environment.
    LOPPER_SYSROOT="${LOPPER_SYSROOT:-}"
    if [[ -z "${LOPPER_SYSROOT}" ]]; then
        LOPPER_BIN="$(
            find "${YOCTO_BUILD_DIR}/tmp/work" \
                -path '*/esw-conf-native/*/recipe-sysroot-native/usr/bin/lopper' \
                -type f -print -quit
        )"
        if [[ -z "${LOPPER_BIN}" ]]; then
            die "Unable to locate the esw-conf-native Lopper sysroot below ${YOCTO_BUILD_DIR}/tmp/work"
        fi
        LOPPER_SYSROOT="${LOPPER_BIN%/usr/bin/lopper}"
    fi
    require_file "${LOPPER_SYSROOT}/usr/bin/lopper" "esw-conf-native Lopper"
    MACHINE="${MACHINE}" \
    LOPPER_SYSROOT="${LOPPER_SYSROOT}" \
    OPENAMP_DTS_DIR="${YOCTO_BUILD_DIR}/conf/dts/${MACHINE}" \
    OPENAMP_OUT_ROOT="${OPENAMP_WORK}" \
    bash "${HEADER_SCRIPT}"

    OPENAMP_REQUIRED_DEFINES=(
        IPI_IRQ_VECT_ID
        POLL_BASE_ADDR
        IPI_CHN_BITMASK
        SHARED_MEM_PA
        SHARED_MEM_SIZE
        SHARED_BUF_OFFSET
    )
    for core in 0 1; do
        HEADER="${OPENAMP_WORK}/psu_cortexr5_${core}/amd_platform_info.h"
        require_file "${HEADER}" "R5c${core} OpenAMP header"
        for symbol in "${OPENAMP_REQUIRED_DEFINES[@]}"; do
            grep -Eq "^[[:space:]]*#define[[:space:]]+${symbol}[[:space:]]+" "${HEADER}" || \
                die "R5c${core} OpenAMP header is missing ${symbol}: ${HEADER}"
        done
    done
fi
install_machine_conf_payload "${STAGING}/generated-conf"

mkdir -p -- \
    "${STAGING}/payload/yocto-conf" \
    "${STAGING}/payload/vivado_SDT_out"
cp -a -- "${STAGING}/generated-conf/machine" "${STAGING}/payload/yocto-conf/"
cp -a -- "${STAGING}/generated-conf/dts" "${STAGING}/payload/yocto-conf/"
if [[ -d "${STAGING}/generated-conf/multiconfig" ]]; then
    cp -a -- "${STAGING}/generated-conf/multiconfig" "${STAGING}/payload/yocto-conf/"
fi
cp -a -- "${SDT_DIR}/." "${STAGING}/payload/vivado_SDT_out/"
if [[ "${CONTRACT_MODE}" == true ]]; then
    # Keep both representations in the handoff archive. The JSON document is
    # the canonical, human-maintained contract. The YAML document is the exact
    # generated domain supplied to gen-machineconf for this artifact.
    mkdir -p -- "${STAGING}/payload/openamp"
    cp -a -- \
        "${CONTRACT_FILE}" \
        "${STAGING}/payload/openamp/openamp-contract.json"
    cp -a -- \
        "${DOMAIN_FILE}" \
        "${STAGING}/payload/openamp/openamp-domain.yaml"

    PACKAGED_CONTRACT_SHA256="$(
        python3 "${CONTRACT_TOOL}" contract-digest \
            --contract "${STAGING}/payload/openamp/openamp-contract.json"
    )"
    [[ "${PACKAGED_CONTRACT_SHA256}" == "${CONTRACT_SHA256}" ]] || \
        die "Packaged OpenAMP contract digest changed during mconf assembly"
    python3 "${CONTRACT_TOOL}" verify-domain \
        --contract "${STAGING}/payload/openamp/openamp-contract.json" \
        --domain "${STAGING}/payload/openamp/openamp-domain.yaml"
    PACKAGED_DOMAIN_SHA256="$(
        sha256sum "${STAGING}/payload/openamp/openamp-domain.yaml" | awk '{print $1}'
    )"
    [[ "${PACKAGED_DOMAIN_SHA256}" == "${DOMAIN_SHA256}" ]] || \
        die "Packaged OpenAMP domain digest changed during mconf assembly"
else
    mkdir -p -- \
        "${STAGING}/payload/openamp_gen/psu_cortexr5_0" \
        "${STAGING}/payload/openamp_gen/psu_cortexr5_1"
    for core in 0 1; do
        cp -a -- \
            "${OPENAMP_WORK}/psu_cortexr5_${core}/amd_platform_info.h" \
            "${STAGING}/payload/openamp_gen/psu_cortexr5_${core}/"
    done
fi

PL_SDTGEN_SHA256="$(sha256sum "${PL_SDTGEN_ARTIFACT}" | awk '{print $1}')"
XSA_SHA256="$(
    artifact_metadata pl_sdtgen "${PL_SDTGEN_ARTIFACT}" xsa_sha256
)"
DOMAIN_SHA256="${DOMAIN_SHA256:-$(sha256sum "${DOMAIN_FILE}" | awk '{print $1}')}"
ARTIFACT_METADATA=(
    --metadata "pl_sdtgen_sha256=${PL_SDTGEN_SHA256}" \
    --metadata "xsa_sha256=${XSA_SHA256}" \
    --metadata "domain_sha256=${DOMAIN_SHA256}" \
    --metadata "machine=${MACHINE}"
)
if [[ "${CONTRACT_MODE}" == true ]]; then
    ARTIFACT_METADATA+=(
        --metadata "openamp_contract_sha256=${CONTRACT_SHA256}"
    )
else
    ARTIFACT_METADATA+=(
        --metadata "openamp_header_generator_sha256=$(sha256sum "${HEADER_SCRIPT}" | awk '{print $1}')"
    )
fi
ARTIFACT="$(artifact_create_hashed mconf "${STAGING}/payload" "${ARTIFACT_BASE}" \
    "${ARTIFACT_METADATA[@]}")"
artifact_finalize_hashed mconf "${ARTIFACT_BASE}" "${ARTIFACT}"

log "Machine-config artifact: ${ARTIFACT}"
