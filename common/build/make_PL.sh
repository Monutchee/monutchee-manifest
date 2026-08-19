#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

usage() {
    cat <<'EOF'
Usage: make_PL.sh [OPTIONS]

Build the PL design stage by stage and package the SDTGen output. With no
stage option every build stage runs, in this order:

  --build-bd       Validate the block design and generate its output products
  --compile-synth  Top-level synthesis (resets and relaunches synth_1)
  --compile-impl   Place and route (resets impl_1, runs to route_design)
  --compile-bit    write_bitstream on the routed implementation
  --gen-xsa        Export the bitstream-inclusive XSA
  --sdtgen         Generate the SDT from the XSA and publish the artifact

Stage options combine, and the stages always execute in the order above
whatever order they were given in. The exit status is zero only when every
selected stage succeeded, so invocations chain with &&.

These read-only queries report on the project instead of building it. They
are opt-in, run after any build stage in the same invocation, and exit zero
whenever the report was produced -- the verdict is in the output:

  --status         Per-run status, progress, and out-of-date flags, plus the
                   block design, trailing IP, bitstream, XSA, and artifact
  --summary        Vivado's own run statistics: timing, failed routes, power
  --report [NAME]  Index the stage reports and logs, or print one of them

Each Vivado stage is one Tcl script in the PL repository's
SourceData/Script, so a failing stage can be rerun and debugged directly:

  build_bd.tcl  build_synth.tcl  build_impl.tcl  build_bitstream.tcl
  export_xsa.tcl  report_status.tcl  report_summary.tcl

Vivado does not lock projects and a live session saves its own in-memory
state over any batch edit, so the build stages refuse to run while a Vivado
session of this user is open; source the stage script in that session's Tcl
console instead. --status and --summary open the project read-only and
--report reads no project at all, so the queries always run, as does
--sdtgen.

Options:
  --workspace DIR   Product workspace root
  --product NAME    Product profile: zudemo, kr260demo, or msap1
  --xsa FILE        XSA path: --gen-xsa writes it, --sdtgen reads it
  --artifact FILE   SDTGen artifact basename; _<sha256[:6]> is appended
  --jobs N          Vivado -jobs value for the compile stages, or "auto"
                    (default: VIVADO_JOBS, else auto)
  --ignore-vivado-session
                    Run the build stages although a session is open
                    (unsafe: the live session overwrites batch edits)
  -h, --help        Show this help

A block design contributes one out-of-context synthesis run per IP, and each
concurrent run is a separate Vivado process holding a couple of gigabytes, so
-jobs is a memory setting more than a CPU one: on a design with a dozen IPs a
core-count default asks a 32 GB machine for more memory than it has, and the
kernel resolves that by thrashing swap and then OOM-killing the desktop.

"auto" therefore sizes the job count from MemAvailable as well as core count,
and reports what it picked. An explicit --jobs or VIVADO_JOBS is always obeyed,
with a warning when it exceeds what the machine can currently afford. The
estimate's inputs are environment overrides, since the per-run cost depends on
the design:

  PL_JOB_MEM_MB      memory budgeted per concurrent run   (default 3072)
  PL_RESERVE_MEM_MB  memory left for everything else      (default 4096)
  PL_RESERVE_CPUS    cores left for everything else       (default 4)
  PL_MAX_JOBS        ceiling whatever the machine reports (default 16)
EOF
}

WORKSPACE_ROOT="$(default_workspace_root)"
REQUESTED_PRODUCT=""
ARTIFACT=""
XSA_FILE=""
JOBS=""
REPORT_NAME=""
IGNORE_VIVADO_SESSION=false
STAGE_BD=false
QUERY_STATUS=false
QUERY_SUMMARY=false
QUERY_REPORT=false
STAGE_SYNTH=false
STAGE_IMPL=false
STAGE_BIT=false
STAGE_XSA=false
STAGE_SDT=false
STAGE_SELECTED=false

while (($# > 0)); do
    case "$1" in
        --workspace) WORKSPACE_ROOT="$2"; shift 2 ;;
        --workspace=*) WORKSPACE_ROOT="${1#*=}"; shift ;;
        --product) REQUESTED_PRODUCT="$2"; shift 2 ;;
        --product=*) REQUESTED_PRODUCT="${1#*=}"; shift ;;
        --xsa) XSA_FILE="$2"; shift 2 ;;
        --xsa=*) XSA_FILE="${1#*=}"; shift ;;
        --artifact) ARTIFACT="$2"; shift 2 ;;
        --artifact=*) ARTIFACT="${1#*=}"; shift ;;
        --jobs) JOBS="$2"; shift 2 ;;
        --jobs=*) JOBS="${1#*=}"; shift ;;
        --build-bd) STAGE_BD=true; STAGE_SELECTED=true; shift ;;
        --status) QUERY_STATUS=true; STAGE_SELECTED=true; shift ;;
        --summary) QUERY_SUMMARY=true; STAGE_SELECTED=true; shift ;;
        --report)
            QUERY_REPORT=true
            STAGE_SELECTED=true
            # Optional value: a following argument that is not another option.
            if (($# > 1)) && [[ "$2" != -* ]]; then
                REPORT_NAME="$2"
                shift 2
            else
                shift
            fi
            ;;
        --report=*) QUERY_REPORT=true; STAGE_SELECTED=true; REPORT_NAME="${1#*=}"; shift ;;
        --compile-synth) STAGE_SYNTH=true; STAGE_SELECTED=true; shift ;;
        --compile-impl) STAGE_IMPL=true; STAGE_SELECTED=true; shift ;;
        --compile-bit) STAGE_BIT=true; STAGE_SELECTED=true; shift ;;
        --gen-xsa) STAGE_XSA=true; STAGE_SELECTED=true; shift ;;
        --sdtgen) STAGE_SDT=true; STAGE_SELECTED=true; shift ;;
        --ignore-vivado-session) IGNORE_VIVADO_SESSION=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

if [[ "${STAGE_SELECTED}" != true ]]; then
    STAGE_BD=true
    STAGE_SYNTH=true
    STAGE_IMPL=true
    STAGE_BIT=true
    STAGE_XSA=true
    STAGE_SDT=true
fi

WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile "${REQUESTED_PRODUCT}"

# Vivado's -jobs value bounds how many runs go at once, and each concurrent run
# is a separate Vivado process holding a couple of gigabytes. A block design
# contributes one out-of-context synthesis run per IP, so on this design -jobs
# is really a process count in the teens, and a core-count default becomes a
# memory overcommit: thirteen runs at ~2.5 GB want more than a 32 GB machine
# has. The kernel answers that by thrashing swap and then invoking the OOM
# killer, which chooses victims by badness score rather than by who caused the
# problem -- in practice the desktop session dies and the build survives. So the
# default is sized from free memory as well as core count, and the memory term
# is normally the one that binds.
#
# The per-run figure is design-dependent, so every input is an override:
#   PL_JOB_MEM_MB      memory to budget per concurrent run (default 3072)
#   PL_RESERVE_MEM_MB  memory left for everything else (default 4096)
#   PL_RESERVE_CPUS    cores left for everything else (default 4)
#   PL_MAX_JOBS        ceiling whatever the machine reports (default 16)
PL_AUTO_JOBS=0
PL_AUTO_JOBS_NOTE=""

# MemAvailable rather than MemFree: it is the kernel's own estimate of what can
# be allocated without pushing the machine into swap, which is exactly the
# question being asked. Swap is deliberately left out of the budget -- filling
# swap is the failure being avoided, not headroom to spend.
pl_available_memory_mb() {
    [[ -r /proc/meminfo ]] || return 1
    awk '/^MemAvailable:/ {printf "%d\n", $2 / 1024; found = 1; exit}
         END {exit !found}' /proc/meminfo
}

pl_compute_auto_jobs() {
    local job_mem="${PL_JOB_MEM_MB:-6144}"
    local reserve_mem="${PL_RESERVE_MEM_MB:-4096}"
    local reserve_cpus="${PL_RESERVE_CPUS:-4}"
    local max_jobs="${PL_MAX_JOBS:-16}"
    local cpus available usable by_cpu by_mem jobs

    [[ "${job_mem}" =~ ^[1-9][0-9]*$ ]] || die "Invalid PL_JOB_MEM_MB: ${job_mem}"
    [[ "${max_jobs}" =~ ^[1-9][0-9]*$ ]] || die "Invalid PL_MAX_JOBS: ${max_jobs}"
    [[ "${reserve_mem}" =~ ^[0-9]+$ ]] || die "Invalid PL_RESERVE_MEM_MB: ${reserve_mem}"
    [[ "${reserve_cpus}" =~ ^[0-9]+$ ]] || die "Invalid PL_RESERVE_CPUS: ${reserve_cpus}"

    cpus="$(nproc 2>/dev/null || printf '4')"
    by_cpu=$((cpus - reserve_cpus))
    if ((by_cpu < 1)); then
        by_cpu=1
    fi

    if ! available="$(pl_available_memory_mb)"; then
        # Nothing to size against. Two runs is slow, but it cannot be the reason
        # a workstation dies, which is the right way to be wrong here.
        PL_AUTO_JOBS=2
        PL_AUTO_JOBS_NOTE="no MemAvailable to read, so assuming a small budget"
        return 0
    fi

    usable=$((available - reserve_mem))
    if ((usable < job_mem)); then
        # Not even one run's worth is free. One still has to be allowed, since
        # refusing to build would be worse, but say so plainly: this build will
        # swap, and the machine is already short of memory.
        by_mem=1
        usable=0
        warn "only ${available} MB of memory is available and ${reserve_mem} MB is reserved,"
        warn "which is short of one ${job_mem} MB run; close something before building, or"
        warn "lower PL_RESERVE_MEM_MB / PL_JOB_MEM_MB if that per-run estimate is too high"
    else
        by_mem=$((usable / job_mem))
    fi

    jobs="${by_cpu}"
    if ((by_mem < jobs)); then
        jobs="${by_mem}"
    fi
    if ((jobs > max_jobs)); then
        jobs="${max_jobs}"
    fi

    PL_AUTO_JOBS="${jobs}"
    PL_AUTO_JOBS_NOTE="memory ${available} MB free - ${reserve_mem} MB reserved"
    PL_AUTO_JOBS_NOTE+=" = ${usable} MB / ${job_mem} MB per run -> ${by_mem}"
    PL_AUTO_JOBS_NOTE+="; cpu ${cpus} - ${reserve_cpus} -> ${by_cpu}"
    PL_AUTO_JOBS_NOTE+="; cap ${max_jobs}"
}

# An explicit value is obeyed, including one this machine cannot afford: the
# caller may know something the estimate does not, and a build command that
# quietly does something other than what it was told is worse than a slow one.
# It does get warned about, because the symptom otherwise looks like a hang.
JOBS_SOURCE="--jobs"
if [[ -z "${JOBS}" ]]; then
    JOBS="${VIVADO_JOBS:-}"
    JOBS_SOURCE="VIVADO_JOBS"
fi
if [[ -z "${JOBS}" || "${JOBS}" == auto ]]; then
    JOBS_SOURCE="auto"
    pl_compute_auto_jobs
    JOBS="${PL_AUTO_JOBS}"
else
    [[ "${JOBS}" =~ ^[1-9][0-9]*$ ]] || die "Invalid Vivado job count: ${JOBS}"
fi

# Only the compile stages pass -jobs, so only they have a reason to report it.
if [[ "${STAGE_SYNTH}" == true || "${STAGE_IMPL}" == true \
      || "${STAGE_BIT}" == true ]]; then
    if [[ "${JOBS_SOURCE}" == "auto" ]]; then
        log "PL jobs: ${JOBS} (auto: ${PL_AUTO_JOBS_NOTE})"
    else
        pl_compute_auto_jobs
        if ((JOBS > PL_AUTO_JOBS)); then
            warn "PL jobs: ${JOBS} from ${JOBS_SOURCE} is above the ${PL_AUTO_JOBS} this"
            warn "machine can currently afford (${PL_AUTO_JOBS_NOTE});"
            warn "building as asked, but expect swap pressure and possibly an OOM kill"
        else
            log "PL jobs: ${JOBS} (${JOBS_SOURCE}; auto would pick ${PL_AUTO_JOBS})"
        fi
    fi
fi

# One resolved path for both directions: --gen-xsa writes it and --sdtgen
# reads it, so the two stages cannot disagree about the handoff file.
if [[ -n "${XSA_FILE}" ]]; then
    require_dir "$(dirname -- "${XSA_FILE}")" "parent directory of --xsa"
    XSA_FILE="$(canonical_path "${XSA_FILE}")"
else
    XSA_FILE="${XSA_PATH}"
fi

# Stages that edit the project, versus queries that only read it. Only the
# former can lose work to a live GUI session, so only the former are guarded.
VIVADO_WRITE_STAGES=false
if [[ "${STAGE_BD}" == true || "${STAGE_SYNTH}" == true || \
      "${STAGE_IMPL}" == true || "${STAGE_BIT}" == true || \
      "${STAGE_XSA}" == true ]]; then
    VIVADO_WRITE_STAGES=true
fi
VIVADO_READ_STAGES=false
if [[ "${QUERY_STATUS}" == true || "${QUERY_SUMMARY}" == true ]]; then
    VIVADO_READ_STAGES=true
fi

PL_SCRIPT_DIR="${PL_ROOT}/SourceData/Script"
PL_PROJECT_FILE="${PL_ROOT}/vivado_gen/${PL_XSA_BASENAME%.xsa}.xpr"
PL_LOG_DIR="${PL_ROOT}/vivado_gen/logs"
PL_REPORT_DIR="${PL_ROOT}/vivado_gen/reports"
VIVADO="${VIVADO:-vivado}"

if [[ "${VIVADO_WRITE_STAGES}" == true || "${VIVADO_READ_STAGES}" == true ]]; then
    load_xilinx_environment "${VIVADO}"
    require_command "${VIVADO}"
    require_dir "${PL_ROOT}" "PL repository"
    require_file "${PL_PROJECT_FILE}" "PL Vivado project"
fi

if [[ "${VIVADO_WRITE_STAGES}" == true ]] && vivado_session_running; then
    if [[ "${IGNORE_VIVADO_SESSION}" == true ]]; then
        warn "A Vivado session is open; continuing as requested by --ignore-vivado-session"
    else
        warn "Close that session, or source the stage script in its Tcl console, e.g."
        warn "  source ${PL_SCRIPT_DIR}/build_synth.tcl"
        warn "--status, --summary, and --report stay available while it is open."
        die "A Vivado session of this user is open; refusing to drive ${PL_PROJECT_FILE} in batch"
    fi
fi

# Each stage gets its own log and journal so a failure is diagnosable after
# the next stage runs, instead of being overwritten in the repository root.
run_vivado_stage() {
    local stage="$1"
    local script="${PL_SCRIPT_DIR}/$2"
    shift 2
    local -a tclargs=()

    if (($# > 0)); then
        tclargs=(-tclargs "$@")
    fi
    require_file "${script}" "PL ${stage} stage script"
    mkdir -p -- "${PL_LOG_DIR}"
    log "PL stage ${stage}: ${VIVADO} -mode batch -source ${script} ${*:-}"
    if ! (
        cd "${PL_ROOT}"
        "${VIVADO}" -mode batch -notrace \
            -log "${PL_LOG_DIR}/${stage}.log" \
            -journal "${PL_LOG_DIR}/${stage}.jou" \
            -source "${script}" \
            ${tclargs[@]+"${tclargs[@]}"}
    ); then
        die "PL stage ${stage} failed; see ${PL_LOG_DIR}/${stage}.log"
    fi
    log "PL stage ${stage} completed"
}

# Batch Vivado prints a few hundred board-file and IP-repository scan lines
# before a sourced script gets a word in, which would bury a short report.
# Print only what the script bracketed, and leave the scan in the stage log.
run_vivado_query() {
    local stage="$1"
    local script="${PL_SCRIPT_DIR}/$2"
    local output

    require_file "${script}" "PL ${stage} query script"
    mkdir -p -- "${PL_LOG_DIR}"
    if ! output="$(
        cd "${PL_ROOT}"
        "${VIVADO}" -mode batch -notrace \
            -log "${PL_LOG_DIR}/${stage}.log" \
            -journal "${PL_LOG_DIR}/${stage}.jou" \
            -source "${script}" 2>&1
    )"; then
        printf '%s\n' "${output}" | tail -n 20 >&2
        die "PL ${stage} query failed; see ${PL_LOG_DIR}/${stage}.log"
    fi
    printf '%s\n' "${output}" \
        | awk '/^PL_REPORT_END$/ {inside = 0} inside; /^PL_REPORT_BEGIN$/ {inside = 1}'
    log "Full ${stage} output: ${PL_LOG_DIR}/${stage}.log"
}

if [[ "${STAGE_BD}" == true ]]; then
    run_vivado_stage bd build_bd.tcl
fi

if [[ "${STAGE_SYNTH}" == true ]]; then
    run_vivado_stage synth build_synth.tcl "${JOBS}"
fi

if [[ "${STAGE_IMPL}" == true ]]; then
    run_vivado_stage impl build_impl.tcl "${JOBS}"
fi

if [[ "${STAGE_BIT}" == true ]]; then
    run_vivado_stage bitstream build_bitstream.tcl "${JOBS}"
fi

if [[ "${STAGE_XSA}" == true ]]; then
    run_vivado_stage xsa export_xsa.tcl "${XSA_FILE}"
    require_file "${XSA_FILE}" "exported XSA"
    log "Exported XSA: ${XSA_FILE}"
fi

if [[ "${STAGE_SDT}" == true ]]; then
    SDTGEN="${SDTGEN:-sdtgen}"
    load_xilinx_environment "${SDTGEN}"
    require_command "${SDTGEN}"
    require_command python3
    require_command unzip

    ARTIFACT_BASE="${ARTIFACT:-${BIN_FILE_DIR}/${PRODUCT}_pl_sdtgen.tar.gz}"

    require_file "${XSA_FILE}" "bitstream-inclusive XSA exported from Vivado"
    mkdir -p -- "${BIN_FILE_DIR}"

    log "Generating ${PRODUCT} SDT from XSA: ${XSA_FILE}"
    unzip -tqq "${XSA_FILE}" || die "Input XSA is not a valid archive: ${XSA_FILE}"

    rm -rf -- "${SDT_DIR}"
    mkdir -p -- "${SDT_DIR}"
    case "${SDT_MODE}" in
        user_dts)
            SDT_VALUE="${WORKSPACE_ROOT}/${SDT_VALUE_REL}"
            require_file "${SDT_VALUE}" "SDT user DTS"
            "${SDTGEN}" -xsa "${XSA_FILE}" -dir "${SDT_DIR}" -user_dts "${SDT_VALUE}"
            ;;
        board_dts)
            "${SDTGEN}" -xsa "${XSA_FILE}" -dir "${SDT_DIR}" -board_dts "${SDT_VALUE_REL}"
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

    ARTIFACT="$(artifact_create_hashed pl_sdtgen "${STAGING}/payload" "${ARTIFACT_BASE}" \
        --metadata "xsa_name=$(basename -- "${XSA_FILE}")" \
        --metadata "xsa_sha256=$(sha256sum "${XSA_FILE}" | awk '{print $1}')")"
    artifact_finalize_hashed pl_sdtgen "${ARTIFACT_BASE}" "${ARTIFACT}" \
        || die "Failed to finalize the SDTGen artifact: ${ARTIFACT}"

    log "Input XSA: ${XSA_FILE}"
    log "SDTGen artifact: ${ARTIFACT}"
fi

# The handoff chain, which Vivado cannot see: the exported XSA against the
# bitstream it should have come from, and the published SDT artifact against
# the XSA it records. Answers "is the artifact I would ship current?".
report_handoff_status() {
    local -a bitstreams=("${PL_ROOT}"/vivado_gen/*.runs/impl_1/*.bit)
    local bitstream="${bitstreams[0]}"
    local artifact recorded actual

    if [[ -f "${XSA_FILE}" ]]; then
        log "XSA: ${XSA_FILE} ($(date -r "${XSA_FILE}" '+%Y-%m-%d %H:%M:%S'))"
        if [[ -f "${bitstream}" && "${bitstream}" -nt "${XSA_FILE}" ]]; then
            log "XSA: older than ${bitstream}; rerun --gen-xsa"
        fi
    else
        log "XSA: missing (${XSA_FILE}); run --gen-xsa"
    fi

    artifact="$(artifact_select_latest "${PRODUCT}_pl_sdtgen_*.tar.gz" 2>/dev/null || true)"
    if [[ -z "${artifact}" || ! -f "${artifact}" ]]; then
        log "SDT artifact: none published; run --sdtgen"
        return 0
    fi
    log "SDT artifact: ${artifact}"
    if [[ ! -f "${XSA_FILE}" ]]; then
        return 0
    fi
    recorded="$(artifact_metadata pl_sdtgen "${artifact}" xsa_sha256 2>/dev/null || true)"
    actual="$(sha256sum "${XSA_FILE}" | awk '{print $1}')"
    if [[ -n "${recorded}" && "${recorded}" == "${actual}" ]]; then
        log "SDT artifact: built from the current XSA"
    else
        log "SDT artifact: built from a different XSA; rerun --sdtgen"
    fi
}

# Reports are files the stages already wrote, so this needs no Vivado at all.
print_report_entry() {
    printf '  %-30s %8s  %s\n' \
        "$(basename -- "$1")" \
        "$(numfmt --to=iec -- "$(stat -c %s -- "$1")")" \
        "$(date -r "$1" '+%Y-%m-%d %H:%M')"
}

print_report_index() {
    local file
    local shown=0

    log "Stage reports (${PL_REPORT_DIR}):"
    for file in "${PL_REPORT_DIR}"/*.rpt; do
        [[ -f "${file}" ]] || continue
        print_report_entry "${file}"
        shown=$((shown + 1))
    done
    ((shown > 0)) || log "  none yet; run --compile-synth or --compile-impl"

    # Vivado rotates an existing log to <name>_<pid>.backup.log; the current
    # log is the one worth offering. A backup is still printable by name.
    shown=0
    log "Stage logs (${PL_LOG_DIR}):"
    for file in "${PL_LOG_DIR}"/*.log; do
        [[ -f "${file}" ]] || continue
        if [[ "${file}" == *.backup.log ]]; then
            continue
        fi
        print_report_entry "${file}"
        shown=$((shown + 1))
    done
    ((shown > 0)) || log "  none yet"
    log "Print one with: mnc PL report NAME (e.g. impl_timing_summary)"
}

print_one_report() {
    local name="$1"
    local candidate

    [[ "${name}" != */* ]] || die "Report name must not contain a path: ${name}"
    for candidate in \
        "${PL_REPORT_DIR}/${name}" \
        "${PL_REPORT_DIR}/${name}.rpt" \
        "${PL_LOG_DIR}/${name}" \
        "${PL_LOG_DIR}/${name}.log"; do
        if [[ -f "${candidate}" ]]; then
            log "Report: ${candidate}"
            cat -- "${candidate}"
            return 0
        fi
    done
    warn "List the available reports with: mnc PL report"
    die "No such PL report: ${name}"
}

if [[ "${QUERY_STATUS}" == true ]]; then
    run_vivado_query status report_status.tcl
    report_handoff_status
fi

if [[ "${QUERY_SUMMARY}" == true ]]; then
    run_vivado_query summary report_summary.tcl
fi

if [[ "${QUERY_REPORT}" == true ]]; then
    # Without this, a wrong --workspace reads as "no reports yet" instead of
    # as a missing repository.
    require_dir "${PL_ROOT}" "PL repository"
    if [[ -n "${REPORT_NAME}" ]]; then
        print_one_report "${REPORT_NAME}"
    else
        print_report_index
    fi
fi
