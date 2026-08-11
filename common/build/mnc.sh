#!/usr/bin/env bash

# Single entry point for the product build stages. Installed by setupWorkspace
# as a "mnc" symlink in the workspace root pointing at this file, so the
# workspace root holds one command instead of five generated wrappers.

set -Eeuo pipefail

# Resolve through the symlink: BASH_SOURCE is the "mnc" link in the workspace
# root, and the toolkit (libbuild.sh, the stage scripts) sits beside the real
# file. libbuild.sh derives the workspace root from its own location.
MNC_REAL_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname -- "${MNC_REAL_PATH}")"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

# The chain for "all" is declared per product as MNC_CHAIN in
# products/<product>.conf, because dependency order genuinely differs: with
# RPU_DEPENDS_ON_MCONF true, make_RPU.sh consumes the mconf artifact and
# publishing mconf afterwards would prune the rpu artifact, so mconf must come
# first. mnc never guesses an order.
#
# Read in the caller's own shell: a die() inside a process substitution or a
# pipeline cannot stop mnc, and execution would continue with an empty chain.
mnc_require_chain() {
    if [[ -z "${MNC_CHAIN:-}" ]]; then
        die "product ${PRODUCT} declares no MNC_CHAIN; add it to $(basename -- "${SCRIPT_DIR}")/products/${PRODUCT}.conf"
    fi
}

# Options that take a value: reject a missing or empty one instead of letting
# "shift 2" fail, which under set -e would exit with no diagnostic at all.
# Checks rather than returns, so die() runs in mnc's own shell -- inside a
# command substitution it would only exit the subshell.
mnc_require_value() {
    local option="$1"
    shift

    if (($# == 0)) || [[ -z "$1" ]]; then
        die "${option} needs a TARGET, e.g. mnc ${option} RPU all build"
    fi
}

usage() {
    cat <<'EOF'
Usage: mnc [OPTIONS] <target> <command> [--args] [ARGUMENTS...]

One command for every build stage. Run it from anywhere in the workspace root.

Targets (case-insensitive), discovered from the installed stage scripts:
  HLS PL RPU mconf yocto   one stage
  all                      every stage of this product's chain, in order

The chain order is declared per product, because it differs between them; run
"mnc --list" to see this workspace's.

Commands:
  build              run the stage with no extra option
  help               the stage script's own --help
  <anything else>    passed to the stage as --<anything else>, so every stage
                     option is reachable as a command

Everything after <command> goes to the stage script untouched. "--args" is an
optional explicit separator that mnc drops; "--" is never special to mnc, so
it reaches the stage script (make_yocto.sh takes BitBake arguments after it).
mnc's own options must come before <target>, so a stage option can never be
mistaken for one of mnc's.

Examples:
  mnc all build                        the full chain from a fresh clone
  mnc HLS build                        make_HLS.sh
  mnc PL build --sdtgen                make_PL.sh --sdtgen
  mnc PL sdtgen                        the same, as a command
  mnc PL status                        make_PL.sh --status
  mnc PL report impl_timing_summary    make_PL.sh --report impl_timing_summary
  mnc RPU elf-only                     make_RPU.sh --elf-only
  mnc yocto build -- -c cleanall       make_yocto.sh -- -c cleanall
  mnc PL --args --help                 make_PL.sh --help
  mnc --from RPU all build             resume the chain at RPU
  mnc --dry-run all build              print the chain without running it

Options:
  --list            Show the targets, their scripts, and the chain order
  --dry-run         Print what would run, run nothing
  --from TARGET     "all" only: start the chain at TARGET
  --to TARGET       "all" only: stop the chain after TARGET
  -h, --help        Show this help

The exit status is the stage's own, so invocations chain with &&. "all" stops at
the first failing stage and prints the command that resumes from it.
EOF
}

# Targets come from the installed stage scripts rather than a table here, so a
# new make_<name>.sh is usable through mnc with no change to this file. Returns
# the target's canonical spelling (the one its filename uses).
mnc_targets() {
    local script name

    for script in "${SCRIPT_DIR}"/make_*.sh; do
        [[ -f "${script}" ]] || continue
        name="$(basename -- "${script}")"
        name="${name#make_}"
        printf '%s\n' "${name%.sh}"
    done
}

mnc_resolve_target() {
    local requested="${1,,}"
    local name

    while IFS= read -r name; do
        if [[ "${name,,}" == "${requested}" ]]; then
            printf '%s\n' "${name}"
            return 0
        fi
    done < <(mnc_targets)
    return 1
}

mnc_script_for() {
    printf '%s/make_%s.sh\n' "${SCRIPT_DIR}" "$1"
}

mnc_known_targets() {
    local -a names=()
    local name

    while IFS= read -r name; do
        names+=("${name}")
    done < <(mnc_targets)
    printf '%s\n' "${names[*]} all"
}

# Translate <command> plus the passthrough tail into the stage script's
# arguments. "build" adds nothing, "help" becomes --help, anything else becomes
# --<command>; a command that already starts with "-" is passthrough itself, so
# "mnc PL --status" works as well as "mnc PL status".
mnc_stage_arguments() {
    local command="$1"
    shift

    MNC_STAGE_ARGS=()
    case "${command}" in
        --args) ;;
        -*) MNC_STAGE_ARGS+=("${command}") ;;
        build) ;;
        help) MNC_STAGE_ARGS+=(--help) ;;
        *) MNC_STAGE_ARGS+=("--${command}") ;;
    esac
    if (($# > 0)) && [[ "$1" == "--args" ]]; then
        shift
    fi
    MNC_STAGE_ARGS+=("$@")
}

mnc_elapsed() {
    local total="$1"

    printf '%02d:%02d:%02d' \
        $((total / 3600)) $(((total % 3600) / 60)) $((total % 60))
}

mnc_list() {
    local name script

    log "Workspace: ${WORKSPACE_ROOT}"
    log "Product:   ${PRODUCT}"
    log "Targets:"
    while IFS= read -r name; do
        script="$(mnc_script_for "${name}")"
        printf '  %-8s %s\n' "${name}" "${script}"
    done < <(mnc_targets)
    printf '  %-8s %s\n' all "${MNC_CHAIN:-(not declared for ${PRODUCT})}"
}

# One stage. exec replaces this shell so the stage script owns the terminal and
# its exit status is mnc's, with no wrapper frame to lose signals in.
mnc_run_stage() {
    local target="$1"
    shift
    local script

    script="$(mnc_script_for "${target}")"
    require_file "${script}" "${target} stage script"
    if [[ "${DRY_RUN}" == true ]]; then
        log "would run: bash ${script} --workspace ${WORKSPACE_ROOT} --product ${PRODUCT} $*"
        return 0
    fi
    exec bash "${script}" \
        --workspace "${WORKSPACE_ROOT}" \
        --product "${PRODUCT}" \
        "$@"
}

# The whole chain. Stage scripts reject options they do not define, so one
# stage's flag would kill another: "all" takes no passthrough arguments.
mnc_run_chain() {
    local command="$1"
    shift
    local -a chain=()
    local -a stages=()
    local -a elapsed=()
    local stage script index=0 started total=0 selecting=true

    case "${command}" in
        build) ;;
        help) usage; return 0 ;;
        *) die "'all' only supports the build command; run 'mnc <target> ${command}' for one stage" ;;
    esac
    if (($# > 0)); then
        warn "Each stage rejects options it does not define, so a chain cannot forward them."
        die "'all build' takes no stage arguments; run that stage on its own instead: ${*}"
    fi

    mnc_require_chain
    read -ra chain <<< "${MNC_CHAIN}"

    [[ -z "${FROM_TARGET}" ]] && selecting=false
    for stage in "${chain[@]}"; do
        if [[ "${selecting}" == true ]]; then
            if [[ "${stage,,}" == "${FROM_TARGET,,}" ]]; then
                selecting=false
            else
                continue
            fi
        fi
        stages+=("${stage}")
        if [[ -n "${TO_TARGET}" && "${stage,,}" == "${TO_TARGET,,}" ]]; then
            break
        fi
    done
    if [[ "${selecting}" == true ]]; then
        die "--from ${FROM_TARGET} is not in the chain: ${MNC_CHAIN}"
    fi
    if [[ -n "${TO_TARGET}" ]] && \
       [[ "${stages[${#stages[@]} - 1],,}" != "${TO_TARGET,,}" ]]; then
        die "--to ${TO_TARGET} is not in the chain at or after the starting stage"
    fi
    if ((${#stages[@]} == 0)); then
        die "no stages selected; --from/--to leave the chain empty"
    fi

    log "Chain: ${stages[*]}"
    for stage in "${stages[@]}"; do
        index=$((index + 1))
        script="$(mnc_script_for "${stage}")"
        require_file "${script}" "${stage} stage script"
        if [[ "${DRY_RUN}" == true ]]; then
            log "would run (${index}/${#stages[@]}): bash ${script} --workspace ${WORKSPACE_ROOT} --product ${PRODUCT}"
            continue
        fi

        log "=== mnc ${stage} build (${index}/${#stages[@]}) ==="
        started=${SECONDS}
        if ! bash "${script}" \
            --workspace "${WORKSPACE_ROOT}" \
            --product "${PRODUCT}"; then
            warn "Chain stopped at ${stage} after $(mnc_elapsed $((SECONDS - started)))"
            warn "Resume once it is fixed with: mnc --from ${stage} all build"
            die "mnc ${stage} build failed"
        fi
        elapsed+=("$(mnc_elapsed $((SECONDS - started)))")
        total=${SECONDS}
    done

    if [[ "${DRY_RUN}" == true ]]; then
        return 0
    fi
    log "Chain complete in $(mnc_elapsed "${total}"):"
    index=0
    for stage in "${stages[@]}"; do
        printf '  %-8s %s\n' "${stage}" "${elapsed[${index}]}"
        index=$((index + 1))
    done
}

DRY_RUN=false
DO_LIST=false
FROM_TARGET=""
TO_TARGET=""

# mnc's own options, before the target only. Once the target is seen every
# remaining argument belongs to the stage script.
while (($# > 0)); do
    case "$1" in
        --list) DO_LIST=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --from)
            mnc_require_value --from "${@:2:1}"
            FROM_TARGET="$2"; shift 2 ;;
        --from=*)
            mnc_require_value --from "${1#*=}"
            FROM_TARGET="${1#*=}"; shift ;;
        --to)
            mnc_require_value --to "${@:2:1}"
            TO_TARGET="$2"; shift 2 ;;
        --to=*)
            mnc_require_value --to "${1#*=}"
            TO_TARGET="${1#*=}"; shift ;;
        -h|--help) usage; exit 0 ;;
        --) shift; break ;;
        -*) usage >&2; die "Unknown mnc option: $1" ;;
        *) break ;;
    esac
done

WORKSPACE_ROOT="$(default_workspace_root)"
WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile ""

if [[ "${DO_LIST}" == true ]]; then
    mnc_list
    exit 0
fi

if (($# == 0)); then
    usage >&2
    die "No target given; expected one of: $(mnc_known_targets)"
fi

REQUESTED_TARGET="$1"
shift

if [[ "${REQUESTED_TARGET,,}" == all ]]; then
    # Mandatory, for the same reason a single stage's is: "mnc all" is the most
    # expensive thing here, and must never start from one typed word.
    if (($# == 0)); then
        usage >&2
        die "No command given for all; 'mnc all build' runs the whole chain"
    fi
    mnc_run_chain "$@"
    exit 0
fi

if [[ -n "${FROM_TARGET}" || -n "${TO_TARGET}" ]]; then
    die "--from and --to apply to the 'all' target only"
fi

TARGET="$(mnc_resolve_target "${REQUESTED_TARGET}")" || \
    die "Unknown target '${REQUESTED_TARGET}'; expected one of: $(mnc_known_targets)"

if (($# == 0)); then
    usage >&2
    die "No command given for ${TARGET}; 'mnc ${TARGET} build' runs the stage"
fi

mnc_stage_arguments "$@"
mnc_run_stage "${TARGET}" ${MNC_STAGE_ARGS[@]+"${MNC_STAGE_ARGS[@]}"}
