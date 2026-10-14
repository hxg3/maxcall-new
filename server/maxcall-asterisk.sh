#!/usr/bin/env bash
# Allowlisted Asterisk CLI proxy for the maxcall dashboard (runs via sudo).
# Usage: maxcall-asterisk <asterisk -rx arguments...>
set -u
Q='maxcall-q'
fail() { echo "denied: $*" >&2; exit 2; }
[[ $# -ge 1 ]] || fail "empty"
case "$1" in
  pjsip)
    [[ "${2:-}" == "show" && "${3:-}" == "contacts" && $# -eq 3 ]] || fail "$*"
    ;;
  queue)
    case "${2:-}" in
      show)
        [[ "${3:-}" =~ ^[A-Za-z0-9_-]+$ && $# -eq 3 ]] || fail "$*"
        ;;
      pause|unpause)
        [[ "${3:-}" == "member" && "${4:-}" =~ ^PJSIP/[0-9]{3,4}$ && "${5:-}" == "queue" && "${6:-}" == "$Q" && $# -eq 6 ]] || fail "$*"
        ;;
      reload)
        [[ "${3:-}" == "all" && $# -eq 3 ]] || fail "$*"
        ;;
      *) fail "$*" ;;
    esac
    ;;
  module)
    [[ "${2:-}" == "reload" && "${3:-}" == "res_pjsip.so" && $# -eq 3 ]] || fail "$*"
    ;;
  channel)
    [[ "${2:-}" == "originate" && "${3:-}" =~ ^PJSIP/[0-9]{3,4}$ && "${4:-}" == "extension" && "${5:-}" =~ ^[0-9+]{4,16}@cos-all$ && $# -eq 5 ]] || fail "$*"
    ;;
  *) fail "$*" ;;
esac
exec /usr/sbin/asterisk -rx "$*"
