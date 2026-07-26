#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_yocto.sh [OPTIONS] [-- BITBAKE_ARGS...]

Consume the machine-config and RPU artifacts, install their payloads into the
Yocto workspace, run BitBake, and package selected deploy outputs.

Options:
  --workspace DIR        Product workspace root
  --product NAME         Product profile: zudemo, kr260demo, or msap1
  --mconf-artifact FILE  Input artifact from make_mconf.sh
  --rpu-artifact FILE    Input artifact from make_RPU.sh
  --image-target TARGET  Image whose deploy files enter the output artifact
  --artifact FILE        Yocto artifact basename; _<sha256[:6]> is appended
  --prepare-only         Install inputs without invoking BitBake
  -h, --help             Show this help

With no BITBAKE_ARGS, the product's default image target is built.
EOF
}

WORKSPACE_ROOT="$(default_workspace_root)"
REQUESTED_PRODUCT=""
MCONF_ARTIFACT=""
RPU_ARTIFACT=""
ARTIFACT=""
IMAGE_TARGET=""
PREPARE_ONLY=false
BITBAKE_ARGS=()

while (($# > 0)); do
    case "$1" in
        --workspace) WORKSPACE_ROOT="$2"; shift 2 ;;
        --workspace=*) WORKSPACE_ROOT="${1#*=}"; shift ;;
        --product) REQUESTED_PRODUCT="$2"; shift 2 ;;
        --product=*) REQUESTED_PRODUCT="${1#*=}"; shift ;;
        --mconf-artifact) MCONF_ARTIFACT="$2"; shift 2 ;;
        --mconf-artifact=*) MCONF_ARTIFACT="${1#*=}"; shift ;;
        --rpu-artifact) RPU_ARTIFACT="$2"; shift 2 ;;
        --rpu-artifact=*) RPU_ARTIFACT="${1#*=}"; shift ;;
        --image-target) IMAGE_TARGET="$2"; shift 2 ;;
        --image-target=*) IMAGE_TARGET="${1#*=}"; shift ;;
        --artifact) ARTIFACT="$2"; shift 2 ;;
        --artifact=*) ARTIFACT="${1#*=}"; shift ;;
        --prepare-only) PREPARE_ONLY=true; shift ;;
        -h|--help) usage; exit 0 ;;
        --) shift; BITBAKE_ARGS=("$@"); break ;;
        *) die "Unknown workflow option '$1'; put BitBake arguments after --" ;;
    esac
done

WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile "${REQUESTED_PRODUCT}"
require_command python3

if [[ -z "${MCONF_ARTIFACT}" ]]; then
    MCONF_ARTIFACT="$(
        artifact_select_latest "${PRODUCT}_mconf_*.tar.gz"
    )"
fi
if [[ -z "${RPU_ARTIFACT}" ]]; then
    RPU_ARTIFACT="$(
        artifact_select_latest "${PRODUCT}_rpu_*.tar.gz"
    )"
fi
ARTIFACT_BASE="${ARTIFACT:-${BIN_FILE_DIR}/${PRODUCT}_yocto.tar.gz}"
IMAGE_TARGET="${IMAGE_TARGET:-${DEFAULT_IMAGE_TARGET}}"

MCONF_SHA256="$(sha256sum "${MCONF_ARTIFACT}" | awk '{print $1}')"
MCONF_XSA_SHA256="$(
    artifact_metadata mconf "${MCONF_ARTIFACT}" xsa_sha256
)"
MCONF_PL_SDTGEN_SHA256="$(
    artifact_metadata mconf "${MCONF_ARTIFACT}" pl_sdtgen_sha256
)"
RPU_MCONF_SHA256="$(
    artifact_metadata rpu "${RPU_ARTIFACT}" mconf_sha256
)"
RPU_XSA_SHA256="$(
    artifact_metadata rpu "${RPU_ARTIFACT}" xsa_sha256
)"
if [[ "${MCONF_SHA256}" != "${RPU_MCONF_SHA256}" ]]; then
    die "Selected RPU artifact was not built from the selected mconf artifact"
fi
if [[ "${MCONF_XSA_SHA256}" != "${RPU_XSA_SHA256}" ]]; then
    die "Selected RPU artifact and mconf artifact were not built from the same XSA"
fi
log "Yocto inputs: mconf=$(basename -- "${MCONF_ARTIFACT}") mconf_sha256=${MCONF_SHA256} rpu=$(basename -- "${RPU_ARTIFACT}") rpu_sha256=$(sha256sum "${RPU_ARTIFACT}" | awk '{print $1}') xsa_sha256=${MCONF_XSA_SHA256}"

STAGING="$(new_temp_dir yocto)"
trap 'rm -rf -- "${STAGING}"' EXIT
mkdir -p -- "${STAGING}/mconf" "${STAGING}/rpu" "${STAGING}/payload"

# Product templates may still contain the former workspace-root locations.
# A BitBake postread file is parsed after local.conf, so these absolute paths
# reliably select repositories below applications/ without editing generated
# configuration or relying on environment-variable precedence.
LOCAL_SOURCE_PATHS="${STAGING}/local-source-paths.conf"
write_local_source_path() {
    local variable="$1"
    local value="$2"

    case "${variable}" in
        ""|*[!A-Z0-9_]*)
            die "Invalid BitBake local-directory variable: ${variable}"
            ;;
    esac
    if [[ "${value}" == *$'\n'* ]]; then
        die "Local source path contains a newline: ${value}"
    fi
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '%s = "%s"\n' "${variable}" "${value}" >> "${LOCAL_SOURCE_PATHS}"
}

: > "${LOCAL_SOURCE_PATHS}"
write_local_source_path "${APU_LOCAL_DIR_VARIABLE}" "${APU_ROOT}"
if [[ -n "${WEB_LOCAL_DIR_VARIABLE:-}" ]]; then
    write_local_source_path "${WEB_LOCAL_DIR_VARIABLE}" "${WEB_ROOT}"
fi

artifact_extract mconf "${MCONF_ARTIFACT}" "${STAGING}/mconf"
artifact_extract rpu "${RPU_ARTIFACT}" "${STAGING}/rpu"

copy_tree_fresh "${STAGING}/mconf/vivado_SDT_out" "${SDT_DIR}"
require_file "${STAGING}/rpu/R5c0.elf" "R5c0 artifact ELF"
require_file "${STAGING}/rpu/R5c1.elf" "R5c1 artifact ELF"
cp -a -- "${STAGING}/rpu/R5c0.elf" "${BIN_FILE_DIR}/R5c0.elf"
cp -a -- "${STAGING}/rpu/R5c1.elf" "${BIN_FILE_DIR}/R5c1.elf"

(
    source_yocto_sdk
    install_machine_conf_payload "${STAGING}/mconf/yocto-conf"
)

if [[ "${PREPARE_ONLY}" == true ]]; then
    log "Prepared ${YOCTO_BUILD_DIR}/conf; BitBake was not run"
    exit 0
fi

if ((${#BITBAKE_ARGS[@]} == 0)); then
    BITBAKE_ARGS=("${IMAGE_TARGET}")
fi

(
    source_yocto_sdk
    BITBAKE="${BITBAKE:-bitbake}"
    require_command "${BITBAKE}"
    MACHINE="${MACHINE}" "${BITBAKE}" \
        --postread "${LOCAL_SOURCE_PATHS}" \
        "${BITBAKE_ARGS[@]}"
)

DEPLOY_DIR="${YOCTO_BUILD_DIR}/tmp/deploy/images/${MACHINE}"
TFTP_DIR="${YOCTO_BUILD_DIR}/export/tftpboot"
DELIVERY="${STAGING}/payload/${PRODUCT}_yocto"
mkdir -p -- "${DELIVERY}/disk" "${DELIVERY}/boot" "${DELIVERY}/jtag" "${DELIVERY}/metadata"

copy_regular() {
    local source="$1"
    local destination="$2"
    local resolved base
    require_file "${source}" "Yocto deploy output"
    resolved="$(readlink -f -- "${source}")"
    base="$(readlink -f -- "${3}")"
    [[ "${resolved}" == "${base}/"* ]] || die "Deploy symlink escapes expected directory: ${source}"
    cp -L -- "${source}" "${destination}"
    chmod 0644 "${destination}"
}

copy_regular "${DEPLOY_DIR}/${IMAGE_TARGET}-${MACHINE}.rootfs.wic.xz" \
    "${DELIVERY}/disk/${IMAGE_TARGET}-${MACHINE}.rootfs.wic.xz" "${DEPLOY_DIR}"
copy_regular "${DEPLOY_DIR}/boot.bin" "${DELIVERY}/boot/boot.bin" "${DEPLOY_DIR}"
copy_regular "${DEPLOY_DIR}/boot.bin.manifest.json" \
    "${DELIVERY}/boot/boot.bin.manifest.json" "${DEPLOY_DIR}"
copy_regular "${DEPLOY_DIR}/${IMAGE_TARGET}-${MACHINE}.rootfs.manifest" \
    "${DELIVERY}/metadata/${IMAGE_TARGET}-${MACHINE}.rootfs.manifest" "${DEPLOY_DIR}"

for file in Image boot.scr fsbl.elf load-jtag-image.tcl pmufw.elf \
            rootfs.cpio.gz.u-boot system.dtb tfa.elf u-boot.elf; do
    copy_regular "${TFTP_DIR}/${file}" "${DELIVERY}/jtag/${file}" "${TFTP_DIR}"
done
chmod 0755 "${DELIVERY}/jtag/load-jtag-image.tcl"

ARTIFACT="$(artifact_create_hashed yocto "${STAGING}/payload" "${ARTIFACT_BASE}" \
    --metadata "mconf_sha256=${MCONF_SHA256}" \
    --metadata "rpu_sha256=$(sha256sum "${RPU_ARTIFACT}" | awk '{print $1}')" \
    --metadata "pl_sdtgen_sha256=${MCONF_PL_SDTGEN_SHA256}" \
    --metadata "xsa_sha256=${MCONF_XSA_SHA256}" \
    --metadata "image_target=${IMAGE_TARGET}")"

log "Yocto artifact: ${ARTIFACT}"
