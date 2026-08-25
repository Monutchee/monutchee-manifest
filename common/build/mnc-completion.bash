# Tab completion for the mnc build command, for bash and zsh.
#
# Nothing is installed system-wide and no package is required: source this file
# in the shell you are working in.
#
#   source .monutchee-build/mnc-completion.bash
#
# Add that line to ~/.bashrc or ~/.zshrc to keep it across shells.
#
# Registered for "mnc", which also completes "./mnc": bash looks up a compspec
# for the command word as typed and, when it contains a slash, falls back to the
# portion after the final slash.
#
# Everything is discovered at completion time from the workspace the command
# word points at, so a new stage script or a new stage option completes with no
# change here, and two workspaces on one machine each complete their own
# targets, chain, and options. Deliberately avoids the bash-completion
# package's helpers (_init_completion and friends) so it works on a bare shell.

# Where this file was sourced from, used only when the command word cannot be
# resolved to a toolkit (an alias, say).
_MNC_COMPLETION_SOURCE="${BASH_SOURCE[0]:-$0}"
if [[ -f "${_MNC_COMPLETION_SOURCE}" ]]; then
    _MNC_COMPLETION_DIR="$(cd -- "$(dirname -- "${_MNC_COMPLETION_SOURCE}")" \
        && pwd -P)"
fi

# The installed toolkit directory behind the typed command word.
_mnc_toolkit() {
    local word="${1-}" real

    if [[ -n "${word}" && "${word}" != */* ]]; then
        word="$(command -v -- "${word}" 2>/dev/null)"
    fi
    if [[ -z "${word}" ]]; then
        word="${_MNC_COMPLETION_DIR:-}/mnc.sh"
    fi
    real="$(readlink -f -- "${word}" 2>/dev/null)"
    if [[ -z "${real}" || ! -f "${real}" ]]; then
        return 1
    fi
    dirname -- "${real}"
}

# Targets, from the installed stage scripts, exactly as mnc discovers them.
_mnc_targets() {
    local script name

    for script in "${1}"/make_*.sh; do
        [[ -f "${script}" ]] || continue
        name="${script##*/make_}"
        printf '%s\n' "${name%.sh}"
    done
}

# The target's canonical spelling, matched case-insensitively as mnc matches it,
# so "pl" finds make_PL.sh.
_mnc_resolve_target() {
    local toolkit="${1}" requested name
    requested="$(printf '%s' "${2}" | tr '[:upper:]' '[:lower:]')"

    for name in $(_mnc_targets "${toolkit}"); do
        if [[ "$(printf '%s' "${name}" | tr '[:upper:]' '[:lower:]')" == "${requested}" ]]; then
            printf '%s\n' "${name}"
            return 0
        fi
    done
    return 1
}

# The long options a stage script defines, read from its own argument parser so
# a new option completes the day it is added.
#
# --workspace and --product are withheld: mnc always injects them, so offering
# them only invites a valueless duplicate, and a stage script meeting one runs
# "shift 2" on a single argument and dies with no diagnostic.
_mnc_stage_options() {
    local script="${1}/make_${2}.sh"

    [[ -f "${script}" ]] || return 0
    sed -n 's/^[[:space:]]*\(--[a-z][a-z0-9-]*\)).*/\1/p' "${script}" \
        | grep -vxE -- '--(workspace|product)' | sort -u
}

# This workspace's product, by the same marker precedence libbuild.sh uses.
_mnc_product() {
    local toolkit="${1}" marker

    for marker in "${toolkit}/../.monutchee-workspace" "${toolkit}/.product" \
                  "${toolkit}/../yocto-build/.mncos-product"; do
        if [[ -r "${marker}" ]]; then
            tr -d '[:space:]' < "${marker}"
            return 0
        fi
    done
}

# The product's MNC_CHAIN, for --from/--to values.
_mnc_chain() {
    local toolkit="${1}" product profile line

    product="$(_mnc_product "${toolkit}")"
    [[ -n "${product}" ]] || return 0
    profile="${toolkit}/products/${product}.conf"
    [[ -r "${profile}" ]] || return 0
    line="$(grep '^MNC_CHAIN=' "${profile}" | head -1)"
    line="${line#MNC_CHAIN=}"
    printf '%s\n' "${line}" | tr -d '"' | tr ' ' '\n' 
}

_mnc() {
    local current previous toolkit word target command lowered
    local -a options=(--list --dry-run --tui --cli --from --to -h --help)
    local index=1 skip_value=false

    current="${COMP_WORDS[COMP_CWORD]}"
    previous=""
    if ((COMP_CWORD > 0)); then
        previous="${COMP_WORDS[COMP_CWORD - 1]}"
    fi
    toolkit="$(_mnc_toolkit "${COMP_WORDS[0]}")" || return 0

    # A value for --from/--to is a chain stage.
    if [[ "${previous}" == "--from" || "${previous}" == "--to" ]]; then
        COMPREPLY=($(compgen -W "$(_mnc_chain "${toolkit}")" -- "${current}"))
        return 0
    fi

    # Walk the words before the cursor to find the target and its command.
    # mnc stops interpreting options once it has the target, so anything after
    # the command belongs to the stage script.
    target=""
    command=""
    while ((index < COMP_CWORD)); do
        word="${COMP_WORDS[index]}"
        if [[ "${skip_value}" == true ]]; then
            skip_value=false
        elif [[ -z "${target}" && ("${word}" == "--from" || "${word}" == "--to") ]]; then
            skip_value=true
        elif [[ -z "${target}" && "${word}" == -* ]]; then
            :
        elif [[ -z "${target}" ]]; then
            target="${word}"
        elif [[ -z "${command}" ]]; then
            command="${word}"
        fi
        ((index++))
    done

    if [[ -z "${target}" ]]; then
        COMPREPLY=($(compgen -W "$(_mnc_targets "${toolkit}") all ${options[*]}" \
            -- "${current}"))
        return 0
    fi

    lowered="$(printf '%s' "${target}" | tr '[:upper:]' '[:lower:]')"
    if [[ "${lowered}" != "all" ]]; then
        target="$(_mnc_resolve_target "${toolkit}" "${target}")" || return 0
    fi
    if [[ -z "${command}" ]]; then
        # "all" only runs the chain; a stage takes any of its own options as a
        # command, with the leading dashes dropped.
        if [[ "${lowered}" == "all" ]]; then
            COMPREPLY=($(compgen -W "build help" -- "${current}"))
        elif [[ "${lowered}" == "deploy" ]]; then
            COMPREPLY=($(compgen -W "jtag build help" -- "${current}"))
        else
            COMPREPLY=($(compgen -W "build help $(_mnc_stage_options \
                "${toolkit}" "${target}" | sed 's/^--//')" -- "${current}"))
        fi
        return 0
    fi

    # Past the command: the stage script's own options, plus mnc's separator.
    if [[ "${lowered}" == "all" ]]; then
        return 0
    fi
    COMPREPLY=($(compgen -W "--args $(_mnc_stage_options "${toolkit}" \
        "${target}")" -- "${current}"))
}

# zsh runs a bash completion function through bashcompinit, which supplies
# "complete" and calls the function under ksh emulation so COMP_WORDS is
# 0-indexed as the function expects. bashcompinit needs zsh's completion system
# initialized first, which an interactive zsh normally does in its rc file.
if [[ -n "${ZSH_VERSION:-}" ]]; then
    autoload -U +X bashcompinit 2>/dev/null && bashcompinit 2>/dev/null
fi

# Say so rather than failing silently: otherwise the shell looks like it has
# completion and simply does nothing on TAB.
if ! complete -o default -F _mnc mnc 2>/dev/null; then
    printf 'mnc completion was NOT registered: this shell has no "complete".\n' >&2
    if [[ -n "${ZSH_VERSION:-}" ]]; then
        printf 'Initialize zsh completion first, then source this file again:\n' >&2
        printf '  autoload -Uz compinit && compinit\n' >&2
        printf '  autoload -U bashcompinit && bashcompinit\n' >&2
    fi
    return 1 2>/dev/null || exit 1
fi
