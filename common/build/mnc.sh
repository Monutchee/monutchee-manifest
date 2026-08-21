#!/usr/bin/env bash

# Single entry point for the product build stages. Installed by setupWorkspace
# as a "mnc" symlink in the workspace root pointing at this file, so the
# workspace root holds one command instead of five generated wrappers.

# Sourcing this file registers TAB completion in the shell that sourced it and
# does nothing else:
#
#   source ./mnc      # completion, here, now
#   ./mnc <target> <command>
#
# Executing it cannot register completion, because a child process cannot
# change its parent's shell. Sourcing runs in the caller's own shell, which
# is the whole point -- but it also means the rest of this file must not run:
# "set -Eeuo pipefail" would persist in an interactive shell, die() would exit
# it, and a stage runs with exec, which would replace it. So the check comes
# before anything else, and returns.
if [ -n "${ZSH_VERSION:-}" ]; then
    case "${ZSH_EVAL_CONTEXT:-}" in
        *:file) _mnc_sourced_as="$0" ;;
        *) _mnc_sourced_as="" ;;
    esac
elif [ -n "${BASH_VERSION:-}" ] && [ "${BASH_SOURCE[0]}" != "$0" ]; then
    _mnc_sourced_as="${BASH_SOURCE[0]}"
else
    _mnc_sourced_as=""
fi
if [ -n "${_mnc_sourced_as}" ]; then
    _mnc_sourced_dir="$(dirname -- "$(readlink -f -- "${_mnc_sourced_as}")")"
    if [ -f "${_mnc_sourced_dir}/mnc-completion.bash" ]; then
        . "${_mnc_sourced_dir}/mnc-completion.bash"
    else
        printf 'mnc: no mnc-completion.bash beside %s\n' \
            "${_mnc_sourced_dir}" >&2
    fi
    unset _mnc_sourced_as _mnc_sourced_dir
    return 0
fi
unset _mnc_sourced_as

set -Eeuo pipefail

MNC_ORIGINAL_ARGS=("$@")

# Resolve through the symlink: BASH_SOURCE is the "mnc" link in the workspace
# root, and the toolkit (libbuild.sh, the stage scripts) sits beside the real
# file. libbuild.sh derives the workspace root from its own location.
MNC_REAL_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname -- "${MNC_REAL_PATH}")"
# shellcheck source=libbuild.sh
source "${SCRIPT_DIR}/libbuild.sh"

MNC_PRESET_HELPER="${SCRIPT_DIR}/preset.py"
MNC_PRESET_TEMPLATE="${SCRIPT_DIR}/templates/MncBuildPreset.yaml"
MNC_TUI_HELPER="${SCRIPT_DIR}/mnc_tui.py"

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

MNC_COMPLETION_FILE="${SCRIPT_DIR}/mnc-completion.bash"

# A child process cannot register completion in the shell that ran it, so the
# closest thing to "it just works" is a one-line hook in the shell's rc file,
# added once on a run from a terminal. One line serves every workspace: the
# completion function resolves the toolkit from the command word being typed,
# so a second workspace needs no second line -- hence the check is for the
# file name, not this path.
#
# Appended, never rewritten, and guarded with a -f test so deleting the
# workspace cannot break shell startup. Set MNC_NO_COMPLETION_INSTALL=1 to
# decline, or use "mnc --completion" and source it yourself.
mnc_install_completion() {
    local rc=""

    [[ -f "${MNC_COMPLETION_FILE}" ]] || return 0
    [[ -z "${MNC_NO_COMPLETION_INSTALL:-}" ]] || return 0
    # Only for a human at a terminal: never touch an rc file from a script,
    # a pipeline, or CI.
    [[ -t 1 ]] || return 0

    case "$(basename -- "${SHELL:-}")" in
        zsh) rc="${HOME}/.zshrc" ;;
        bash) rc="${HOME}/.bashrc" ;;
        *) return 0 ;;
    esac
    if [[ -r "${rc}" ]] && grep -q 'mnc-completion\.bash' "${rc}"; then
        return 0
    fi
    if ! {
        printf '\n# Monutchee mnc TAB completion, added by mnc on first run.\n'
        printf '# One line serves every workspace; remove it to opt out.\n'
        # An "if" rather than "&&": with the workspace deleted the line must
        # still succeed, or it leaves a non-zero status at the first prompt.
        printf 'if [ -f %q ]; then source %q; fi\n' \
            "${MNC_COMPLETION_FILE}" "${MNC_COMPLETION_FILE}"
    } >> "${rc}"; then
        warn "Could not add TAB completion to ${rc}"
        return 0
    fi
    log "TAB completion added to ${rc}: new shells will have it."
    log "This shell: source ${MNC_COMPLETION_FILE}"
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
       mnc [OPTIONS] deploy [jtag] [DEPLOY_OPTIONS...]

One command for every build stage. Run it from anywhere in the workspace root.

Targets (case-insensitive), discovered from the installed stage scripts:
  HLS PL RPU mconf yocto   one stage
  all                      every stage of this product's chain, in order

The chain order is declared per product, because it differs between them; run
"mnc --list" to see this workspace's.

Commands:
  build              run the stage with no extra option
  deploy             special target: "mnc deploy" uses the preset
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
  mnc --tui all build                  live console plus build summary pane
  mnc deploy                           deploy using MncBuildPreset.yaml
  mnc deploy jtag                      select the JTAG deploy type explicitly
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

TAB completion, in the shell you are in right now:

  source ./mnc          registers completion and does nothing else

Executing mnc cannot do that: a child process cannot change its parent's
shell. Sourcing runs in your shell, so it can. The first run from a terminal
also adds one line to your shell rc, so new shells have it without this.

  eval "$(./mnc --completion)"     equivalent, for scripts
  MNC_NO_COMPLETION_INSTALL=1      decline the rc entry

Options:
  --completion      Print the completion script (for eval in this shell)
  --list            Show the targets, their scripts, and the chain order
  --dry-run         Print what would run, run nothing
  --tui             Interactive console with a toggleable stage summary pane
  --from TARGET     "all" only: start the chain at TARGET
  --to TARGET       "all" only: stop the chain after TARGET
  -h, --help        Show this help

The exit status is the stage's own, so invocations chain with &&. "all" stops at
the first failing stage and prints the command that resumes from it.

Build settings come from MncBuildPreset.yaml in the workspace root. Build
commands also write runtime-generated/buildLog/build_YYYYMMDD_HHMMSS.log.
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
    build_elapsed "$1"
}

mnc_list() {
    local name script

    log "Workspace: ${WORKSPACE_ROOT}"
    log "Product:   ${PRODUCT}"
    log "Preset:    ${WORKSPACE_ROOT}/MncBuildPreset.yaml"
    log "Targets:"
    while IFS= read -r name; do
        script="$(mnc_script_for "${name}")"
        printf '  %-8s %s\n' "${name}" "${script}"
    done < <(mnc_targets)
    printf '  %-8s %s\n' all "${MNC_CHAIN:-(not declared for ${PRODUCT})}"
}

mnc_ensure_preset() {
    MNC_PRESET_FILE="${WORKSPACE_ROOT}/MncBuildPreset.yaml"
    if [[ -e "${MNC_PRESET_FILE}" ]]; then
        [[ -f "${MNC_PRESET_FILE}" ]] || \
            die "Build preset is not a regular file: ${MNC_PRESET_FILE}"
        return 0
    fi
    require_file "${MNC_PRESET_TEMPLATE}" "default build preset template"
    if ! cp -- "${MNC_PRESET_TEMPLATE}" "${MNC_PRESET_FILE}"; then
        die "Unable to create default build preset: ${MNC_PRESET_FILE}"
    fi
    chmod 0644 -- "${MNC_PRESET_FILE}"
    log "Created default build preset: ${MNC_PRESET_FILE}"
}

mnc_preset_helper_arguments() {
    local name
    MNC_PRESET_HELPER_ARGS=(--preset "${MNC_PRESET_FILE}")
    while IFS= read -r name; do
        MNC_PRESET_HELPER_ARGS+=(--known-stage "${name}")
    done < <(mnc_targets)
}

mnc_validate_preset() {
    require_file "${MNC_PRESET_HELPER}" "build preset parser"
    require_command python3
    mnc_preset_helper_arguments
    python3 "${MNC_PRESET_HELPER}" validate "${MNC_PRESET_HELPER_ARGS[@]}" || \
        die "Invalid build preset: ${MNC_PRESET_FILE}"
}

mnc_preset_arguments() {
    local stage="$1"
    local output

    MNC_PRESET_ARGS=()
    mnc_preset_helper_arguments
    mkdir -p -- "${RUNTIME_DIR}/.work"
    output="$(mktemp "${RUNTIME_DIR}/.work/preset.XXXXXX")"
    if ! python3 "${MNC_PRESET_HELPER}" args \
        "${MNC_PRESET_HELPER_ARGS[@]}" --stage "${stage}" > "${output}"; then
        rm -f -- "${output}"
        die "Unable to read build preset arguments for ${stage}"
    fi
    mapfile -d '' -t MNC_PRESET_ARGS < "${output}" || true
    rm -f -- "${output}"
}

mnc_new_report_file() {
    local directory="${RUNTIME_DIR}/buildLog"
    local timestamp candidate

    mkdir -p -- "${directory}"
    while true; do
        timestamp="$(date '+%Y%m%d_%H%M%S')"
        candidate="${directory}/build_${timestamp}.log"
        if (set -o noclobber; : > "${candidate}") 2>/dev/null; then
            printf '%s\n' "${candidate}"
            return 0
        fi
        sleep 1
    done
}

mnc_start_report_wrapper() {
    local report status

    report="$(mnc_new_report_file)"
    set +e
    MNC_REPORT_ACTIVE=1 MNC_REPORT_FILE="${report}" \
        bash "${MNC_REAL_PATH}" "${MNC_ORIGINAL_ARGS[@]}" \
        > >(tee -a "${report}") \
        2> >(tee -a "${report}" >&2)
    status=$?
    wait
    set -e
    exit "${status}"
}

mnc_summary_work_file() {
    local stage="$1"
    mkdir -p -- "${RUNTIME_DIR}/.work"
    mktemp "${RUNTIME_DIR}/.work/mnc-${stage}.summary.XXXXXX"
}

mnc_print_stage_summary() {
    local file="$1" prefix="${2:-      }"
    local line

    [[ -f "${file}" ]] || return 0
    while IFS= read -r line; do
        [[ -z "${line}" ]] || printf '%s%s\n' "${prefix}" "${line}"
    done < "${file}"
}

mnc_tui_supported_terminal() {
    [[ -t 0 && -t 1 && "${TERM:-dumb}" != dumb ]] || return 1
    [[ -f "${MNC_TUI_HELPER}" ]] || return 1
    command -v python3 >/dev/null 2>&1 || return 1
    python3 "${MNC_TUI_HELPER}" --check >/dev/null 2>&1
}

mnc_launch_tui() {
    local argument
    local -a arguments=()

    if ! mnc_tui_supported_terminal; then
        warn "--tui needs an interactive terminal; continuing with the normal build"
        return 1
    fi
    for argument in "${MNC_ORIGINAL_ARGS[@]}"; do
        [[ "${argument}" == --tui ]] || arguments+=("${argument}")
    done
    exec python3 "${MNC_TUI_HELPER}" --mnc "${MNC_REAL_PATH}" -- \
        "${arguments[@]}"
}

# One stage. Build commands stay wrapped long enough to record timing and a
# summary; non-build stage commands retain the old exec behavior.
mnc_run_stage() {
    local target="$1"
    shift
    local script started status elapsed summary_file
    local -a preset_args=()

    script="$(mnc_script_for "${target}")"
    require_file "${script}" "${target} stage script"
    if [[ "${DRY_RUN}" == true ]]; then
        log "would run: bash ${script} --workspace ${WORKSPACE_ROOT} --product ${PRODUCT} $*"
        return 0
    fi

    if [[ "${IS_BUILD_COMMAND}" != true && "${IS_DEPLOY_COMMAND}" != true ]]; then
        exec bash "${script}" \
            --workspace "${WORKSPACE_ROOT}" \
            --product "${PRODUCT}" \
            "$@"
    fi

    mnc_preset_arguments "${target}"
    preset_args=("${MNC_PRESET_ARGS[@]}")
    if [[ "${IS_DEPLOY_COMMAND}" == true ]]; then
        exec bash "${script}" \
            --workspace "${WORKSPACE_ROOT}" \
            --product "${PRODUCT}" \
            "${preset_args[@]}" "$@"
    fi
    summary_file="$(mnc_summary_work_file "${target}")"
    started=${SECONDS}
    mnc_event build_start "" "" "${target}"
    mnc_event stage_start "${target}" "0" "starting"
    log "=== mnc ${target} build ==="
    if MNC_STAGE_NAME="${target}" MNC_STAGE_SUMMARY_FILE="${summary_file}" \
        bash "${script}" \
        --workspace "${WORKSPACE_ROOT}" \
        --product "${PRODUCT}" \
        "${preset_args[@]}" "$@"; then
        status=0
    else
        status=$?
    fi
    elapsed=$((SECONDS - started))
    if ((status == 0)); then
        mnc_event stage_end "${target}" "100" "success"
    else
        mnc_event stage_end "${target}" "" "failed"
    fi
    mnc_event build_end "" "" "${status}"

    log "Build summary:"
    printf '  %-8s %-9s %s\n' "${target}" \
        "$([[ ${status} -eq 0 ]] && printf SUCCESS || printf FAILED)" \
        "$(mnc_elapsed "${elapsed}")"
    mnc_print_stage_summary "${summary_file}"
    printf '  %-18s %s\n' "Total build time" "$(mnc_elapsed "${elapsed}")"
    printf '  %-18s %s\n' "Exit status" "${status}"
    [[ -z "${MNC_REPORT_FILE:-}" ]] || \
        printf '  %-18s %s\n' "Build report" "${MNC_REPORT_FILE}"
    rm -f -- "${summary_file}"
    return "${status}"
}

# The whole chain. Stage scripts reject options they do not define, so one
# stage's flag would kill another: "all" takes no passthrough arguments.
mnc_run_chain() {
    local command="$1"
    shift
    local -a chain=()
    local -a stages=()
    local -a elapsed=()
    local -a results=()
    local -a summary_files=()
    local -a preset_args=()
    local stage script index=0 started chain_started status=0 failed_index=-1
    local selecting=true summary_file resume=""

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
    mnc_event build_start "" "" "${stages[*]}"
    chain_started=${SECONDS}
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
        mnc_preset_arguments "${stage}"
        preset_args=("${MNC_PRESET_ARGS[@]}")
        summary_file="$(mnc_summary_work_file "${stage}")"
        summary_files+=("${summary_file}")
        mnc_event stage_start "${stage}" "0" "starting"
        if MNC_STAGE_NAME="${stage}" MNC_STAGE_SUMMARY_FILE="${summary_file}" \
            bash "${script}" \
            --workspace "${WORKSPACE_ROOT}" \
            --product "${PRODUCT}" \
            "${preset_args[@]}"; then
            results+=(SUCCESS)
            mnc_event stage_end "${stage}" "100" "success"
        else
            status=$?
            results+=(FAILED)
            mnc_event stage_end "${stage}" "" "failed"
            elapsed+=("$(mnc_elapsed $((SECONDS - started)))")
            failed_index=$((index - 1))
            warn "Chain stopped at ${stage} after $(mnc_elapsed $((SECONDS - started)))"
            warn "Resume once it is fixed with: mnc --from ${stage} all build"
            resume="mnc --from ${stage} all build"
            break
        fi
        elapsed+=("$(mnc_elapsed $((SECONDS - started)))")
    done

    if [[ "${DRY_RUN}" == true ]]; then
        return 0
    fi
    if ((status == 0)); then
        log "Chain complete in $(mnc_elapsed $((SECONDS - chain_started)))"
    fi
    log "Build summary:"
    for ((index=0; index<${#stages[@]}; index++)); do
        stage="${stages[index]}"
        if ((index < ${#results[@]})); then
            printf '  %-8s %-9s %s\n' \
                "${stage}" "${results[index]}" "${elapsed[index]}"
            mnc_print_stage_summary "${summary_files[index]}"
        else
            printf '  %-8s %-9s %s\n' "${stage}" "NOT-RUN" "--:--:--"
        fi
    done
    printf '  %-18s %s\n' "Total build time" \
        "$(mnc_elapsed $((SECONDS - chain_started)))"
    printf '  %-18s %s\n' "Exit status" "${status}"
    [[ -z "${resume}" ]] || printf '  %-18s %s\n' "Resume command" "${resume}"
    [[ -z "${MNC_REPORT_FILE:-}" ]] || \
        printf '  %-18s %s\n' "Build report" "${MNC_REPORT_FILE}"
    mnc_event build_end "" "" "${status}"
    for summary_file in "${summary_files[@]}"; do
        rm -f -- "${summary_file}"
    done
    if ((status != 0)); then
        warn "mnc ${stages[failed_index]} build failed"
    fi
    return "${status}"
}

DRY_RUN=false
DO_LIST=false
DO_COMPLETION=false
TUI_REQUESTED=false
FROM_TARGET=""
TO_TARGET=""

# mnc's own options, before the target only. Once the target is seen every
# remaining argument belongs to the stage script.
while (($# > 0)); do
    case "$1" in
        --list) DO_LIST=true; shift ;;
        --completion) DO_COMPLETION=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --tui) TUI_REQUESTED=true; shift ;;
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

if [[ "${DO_COMPLETION}" == true ]]; then
    require_file "${MNC_COMPLETION_FILE}" "mnc completion script"
    cat -- "${MNC_COMPLETION_FILE}"
    exit 0
fi

mnc_install_completion

WORKSPACE_ROOT="$(default_workspace_root)"
WORKSPACE_ROOT="$(canonical_path "${WORKSPACE_ROOT}")"
load_product_profile ""

IS_BUILD_COMMAND=false
IS_DEPLOY_COMMAND=false
if (($# >= 1)) && [[ "${1,,}" == deploy ]]; then
    if (($# == 1)) || [[ "${2:-}" == build || "${2:-}" == jtag ]]; then
        IS_DEPLOY_COMMAND=true
    fi
elif (($# >= 2)) && [[ "$2" == build ]]; then
    IS_BUILD_COMMAND=true
fi

if [[ "${TUI_REQUESTED}" == true ]]; then
    [[ "${IS_BUILD_COMMAND}" == true ]] || \
        die "--tui only supports '<target> build' commands"
    [[ "${DRY_RUN}" != true ]] || die "--tui cannot be combined with --dry-run"
    if [[ -z "${MNC_TUI_CHILD:-}" ]]; then
        mnc_launch_tui || TUI_REQUESTED=false
    fi
fi

if [[ "${DO_LIST}" == true ]]; then
    [[ "${TUI_REQUESTED}" != true ]] || die "--tui cannot be used with --list"
    mnc_list
    exit 0
fi

if (($# == 0)); then
    usage >&2
    die "No target given; expected one of: $(mnc_known_targets)"
fi

if [[ "${IS_BUILD_COMMAND}" == true || "${IS_DEPLOY_COMMAND}" == true ]]; then
    mnc_ensure_preset
    if [[ "${IS_BUILD_COMMAND}" == true && "${DRY_RUN}" != true && \
          -z "${MNC_REPORT_ACTIVE:-}" ]]; then
        mnc_start_report_wrapper
    fi
    mnc_validate_preset
    [[ -z "${MNC_REPORT_FILE:-}" ]] || log "Build report: ${MNC_REPORT_FILE}"
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

if [[ "${TARGET,,}" == deploy ]]; then
    if (($# == 0)); then
        MNC_STAGE_ARGS=()
    elif [[ "$1" == jtag ]]; then
        shift
        MNC_STAGE_ARGS=(--type jtag "$@")
    else
        mnc_stage_arguments "$@"
    fi
    mnc_run_stage "${TARGET}" ${MNC_STAGE_ARGS[@]+"${MNC_STAGE_ARGS[@]}"}
    exit $?
fi

if (($# == 0)); then
    usage >&2
    die "No command given for ${TARGET}; 'mnc ${TARGET} build' runs the stage"
fi

mnc_stage_arguments "$@"
mnc_run_stage "${TARGET}" ${MNC_STAGE_ARGS[@]+"${MNC_STAGE_ARGS[@]}"}
