#!/bin/sh
# Source in bash or zsh. Stores only the selected key in this terminal's environment.
# No API request, file, shell-history entry containing the key, or printed key value.
if [ -n "${ZSH_VERSION:-}" ]; then
    case "$ZSH_EVAL_CONTEXT" in
        *:file) ;;
        *) printf '%s\n' 'Source this file: source ./keys.sh openai or source ./keys.sh anthropic' >&2
           exit 2 ;;
    esac
elif [ -n "${BASH_VERSION:-}" ]; then
    if [ "${BASH_SOURCE[0]}" = "$0" ]; then
        printf '%s\n' 'Source this file: source ./keys.sh openai or source ./keys.sh anthropic' >&2
        exit 2
    fi
else
    printf '%s\n' 'Use bash or zsh: source ./keys.sh openai|anthropic' >&2
    return 2 2>/dev/null || exit 2
fi

_hanoi_register_key() {
    local hanoi_provider hanoi_key_name hanoi_new_key hanoi_existing hanoi_trace hanoi_result
    local LC_ALL=C
    hanoi_trace=0
    case $- in *x*) hanoi_trace=1; set +x ;; esac
    hanoi_result=0
    hanoi_provider=${1:-}
    case "$hanoi_provider" in
        openai) hanoi_key_name=OPENAI_API_KEY; hanoi_existing=${OPENAI_API_KEY:-} ;;
        anthropic) hanoi_key_name=ANTHROPIC_API_KEY; hanoi_existing=${ANTHROPIC_API_KEY:-} ;;
        *) printf '%s\n' 'Usage: source ./keys.sh openai|anthropic [--replace]' >&2; hanoi_result=2 ;;
    esac
    if [ "$#" -gt 2 ] || { [ "$#" -eq 2 ] && [ "$2" != '--replace' ]; }; then
        printf '%s\n' 'Usage: source ./keys.sh openai|anthropic [--replace]' >&2
        hanoi_result=2
    fi
    if [ "$hanoi_result" -eq 0 ]; then
        if [ -n "$hanoi_existing" ] && [ "${2:-}" != '--replace' ]; then
            export "$hanoi_key_name"
            printf '%s\n' "$hanoi_key_name: using the key already set in this terminal. run.py will check authentication."
        elif [ ! -t 0 ]; then
            printf '%s\n' 'Enter the key in an interactive terminal. The existing key was preserved.' >&2
            hanoi_result=2
        else
            printf '%s' "Enter $hanoi_key_name (input hidden): " >&2
            if IFS= read -r -s hanoi_new_key; then
                printf '\n' >&2
                case "$hanoi_new_key" in
                    ''|*[!\!-\~]*)
                        printf '%s\n' 'Empty key or invalid character (including whitespace). The existing key was preserved.' >&2
                        hanoi_result=2 ;;
                    *)
                        if [ "$hanoi_provider" = openai ]; then
                            export OPENAI_API_KEY="$hanoi_new_key"
                        else
                            export ANTHROPIC_API_KEY="$hanoi_new_key"
                        fi
                        printf '%s\n' "$hanoi_key_name: set in this terminal. run.py will check authentication."
                        ;;
                esac
            else
                printf '\n%s\n' 'Could not read the key. The existing key was preserved.' >&2
                hanoi_result=2
            fi
        fi
    fi
    unset hanoi_new_key hanoi_existing
    if [ "$hanoi_trace" -eq 1 ]; then set -x; fi
    return "$hanoi_result"
}

_hanoi_register_key "$@"
