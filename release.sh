#!/usr/bin/env bash
#
# release.sh - publishing helper for MaxCall.
#
# Everything is driven from a numbered menu. The first question is who the
# build is for, because that decides what the tool will let you publish:
#
#   1) Beta - developer preview. Builds from main, lands on the beta channel,
#             visible only to machines switched to Beta builds.
#   2) All  - published to every user on the stable channel.
#
# MaxCall ships as a single MaxCall.exe. The updater metadata lives on its own
# machine-only releases, so a release page never shows more than one asset.
#
# Non-interactive use still works:  ./release.sh --channel beta test
#
set -euo pipefail

REPO="${MAXCALL_REPO:-hxg3/maxcall-new}"
REMOTE="${MAXCALL_REMOTE:-origin}"
BETA_TAG="beta"
BETA_MANIFEST_TAG="manifest-beta"
STABLE_MANIFEST_TAG="manifest-stable"
DIST="dist"
CHANNEL=""

cd "$(git rev-parse --show-toplevel)"

if [ -t 1 ]; then
  B=$'\033[1m'; DIM=$'\033[2m'; OK=$'\033[32m'; WARN=$'\033[33m'
  ERR=$'\033[31m'; OFF=$'\033[0m'
else
  B=""; DIM=""; OK=""; WARN=""; ERR=""; OFF=""
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '  %sOK%s    %s\n'   "$OK" "$OFF" "$*"; }
warn() { printf '  %sWARN%s  %s\n' "$WARN" "$OFF" "$*"; }
die()  { printf '  %sFAIL%s  %s\n'  "$ERR" "$OFF" "$*" >&2; exit 1; }
head_() { printf '\n%s%s%s\n' "$B" "$*" "$OFF"; }

# Reads one line, tolerating a non-interactive stdin.
ask() {
  local __var="$1"; shift
  local __reply
  printf '%s' "$*"
  if ! IFS= read -r __reply; then
    printf '\n'
    return 1
  fi
  printf -v "$__var" '%s' "$__reply"
}

# ask_choice <prompt> <default> <allowed...> -> sets CHOICE
ask_choice() {
  local prompt="$1" default="$2"; shift 2
  local reply
  while :; do
    ask CHOICE "$prompt" || return 1
    CHOICE="${CHOICE:-$default}"
    local a
    for a in "$@"; do
      [ "$CHOICE" = "$a" ] && { printf '\n'; return 0; }
    done
    warn "enter one of: $*"
  done
}

confirm() {
  local reply
  ask CHOICE "$1 [y/N]: " || return 1
  printf '\n'
  case "${CHOICE,,}" in y|yes) return 0 ;; *) return 1 ;; esac
}

require_repo() {
  git remote get-url "$REMOTE" >/dev/null 2>&1 || die "no git remote '$REMOTE'"
  command -v gh >/dev/null 2>&1 || die "gh is required (https://cli.github.com)"
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated"
}

current_version() {
  grep -oP '(?<=^#define _GLOBAL_VERSION ")[^"]+' const.h
}

branch() { git rev-parse --abbrev-ref HEAD; }

require_clean() {
  [ -z "$(git status --porcelain)" ] || die "you have uncommitted changes - commit them first"
}

# ---------------------------------------------------------------------------
# beta: publish a developer preview
# ---------------------------------------------------------------------------
do_test() {
  require_repo
  head_ "Publishing a developer preview"

  git fetch --quiet "$REMOTE" || true
  git pull --ff-only --quiet "$REMOTE" "$(branch)" || die "working tree is behind; resolve it first"
  require_clean

  say "  ${DIM}branch${OFF}   : $(branch)"
  say "  ${DIM}const.h${OFF}  : $(current_version)"
  say ""
  git push "$REMOTE" HEAD
  ok "pushed to $(branch)"

  say ""
  say "  This build lands on the ${B}${BETA_TAG}${OFF} channel only. Machines on the"
  say "  stable channel will not see it."
  say ""
  say "  To receive it, switch the client once:"
  say "    ${B}tray icon > Update Channel > Beta builds${OFF}"
  say ""
  wait_for_pipeline
}

# ---------------------------------------------------------------------------
# all: publish to every user
# ---------------------------------------------------------------------------
do_release() {
  require_repo
  local version="${1:-}"
  head_ "Publishing to every user"

  if [ -z "$version" ]; then
    local last declared
    last="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
    declared="$(current_version)"
    say "  ${DIM}latest tag${OFF}  : ${last:-none}"
    say "  ${DIM}const.h${OFF}    : $declared"
    say ""
    local suggested="$declared"
    if [ -n "$last" ] && [ "${last#v}" != "$declared" ]; then
      IFS=. read -r mj mn pt <<< "${last#v}"
      suggested="${mj}.${mn}.$((pt + 1))"
      say "  suggested next version: ${B}${suggested}${OFF}"
    fi
    ask version "  Version to release (x.y.z) [$suggested]: " || die "cancelled"
    version="${version:-$suggested}"
  fi
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like 6.1.6"

  git fetch --quiet "$REMOTE" --tags || true
  git pull --ff-only --quiet "$REMOTE" "$(branch)" || die "working tree is behind; resolve it first"

  if git rev-parse "v$version" >/dev/null 2>&1; then
    die "tag v$version already exists - pick a different version"
  fi
  require_clean

  local current
  current="$(current_version)"
  say "  ${DIM}const.h${OFF} : $current  ->  $version"

  set_version() {
    python3 - "$1" <<'PY'
import re, sys
version = sys.argv[1]
major, minor, patch = version.split('.')
path = 'const.h'
text = open(path, encoding='utf-8').read()
text = re.sub(r'(?m)^#define _GLOBAL_VERSION\s+"[^"]*"', '#define _GLOBAL_VERSION "%s"' % version, text)
text = re.sub(r'(?m)^#define _GLOBAL_VERSION_MAJOR\s+\d+', '#define _GLOBAL_VERSION_MAJOR %s' % major, text)
text = re.sub(r'(?m)^#define _GLOBAL_VERSION_MINOR\s+\d+', '#define _GLOBAL_VERSION_MINOR %s' % minor, text)
text = re.sub(r'(?m)^#define _GLOBAL_VERSION_PATCH\s+\d+', '#define _GLOBAL_VERSION_PATCH %s' % patch, text)
text = re.sub(r'(?m)^#define _GLOBAL_VERSION_COMMA\s+[0-9,]+', '#define _GLOBAL_VERSION_COMMA %s,%s,%s,0' % (major, minor, patch), text)
open(path, 'w', encoding='utf-8').write(text)
PY
  }
  set_version "$version"
  ok "const.h now declares $version"

  git add const.h
  git commit -m "release: MaxCall $version"
  git tag -a "v$version" -m "MaxCall $version"
  git push "$REMOTE" HEAD
  git push "$REMOTE" "v$version"
  ok "pushed commit and tag v$version"

  say ""
  say "  Everyone on the stable channel is offered this version within"
  say "  6 hours of their last check."
  say ""
  wait_for_pipeline
}

# ---------------------------------------------------------------------------
# pipeline following
# ---------------------------------------------------------------------------
wait_for_pipeline() {
  local before run_id="" st
  before="$(gh run list --repo "$REPO" --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || echo "")"
  head_ "Waiting for the pipeline"
  for _ in $(seq 1 12); do
    sleep 3
    run_id="$(gh run list --repo "$REPO" --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || echo "")"
    [ -n "$run_id" ] && [ "$run_id" != "$before" ] && break
    run_id=""
  done
  [ -n "$run_id" ] || die "the pipeline did not start - check the Actions tab"

  say "  run $run_id"
  if gh run watch "$run_id" --repo "$REPO" --exit-status --interval 15; then
    ok "pipeline succeeded"
    return 0
  fi
  warn "the pipeline failed"
  say ""
  gh run view "$run_id" --repo "$REPO" --log-failed 2>/dev/null | tail -n 40 || true
  return 1
}

# ---------------------------------------------------------------------------
# read-only helpers
# ---------------------------------------------------------------------------
do_how() {
  if [ -n "$CHANNEL" ] && [ "$CHANNEL" != "beta" ]; then
    say "  ${DIM}(the client check happens on the Windows machine)${OFF}"
    say "  ${DIM}tray icon > Check for Updates${OFF}"
    return 0
  fi
  say "  The client checks on its own every 6 hours."
  say "  To force it now, on the Windows machine:"
  say "    ${B}tray icon > Check for Updates${OFF}"
  say "    ${B}or: MaxCall.exe /${OFF}  then pick Check for Updates"
}

do_fetch() {
  require_repo
  local tag="$BETA_TAG"
  [ "$CHANNEL" = "all" ] && tag="$(latest_stable_tag)"
  head_ "Downloading $tag"
  [ -n "$tag" ] || die "no stable release yet"
  mkdir -p "$DIST"
  gh release download "$tag" --repo "$REPO" --pattern 'MaxCall.exe' --dir "$DIST" --clobber
  ok "saved to $DIST/MaxCall.exe"
  say ""
  say "  Put it in a folder named MaxCall and run it. That is the whole install."
}

latest_stable_tag() {
  gh release list --repo "$REPO" --limit 20 --json tagName,isPrerelease \
    --jq '[.[] | select(.isPrerelease == false and (.tagName | startswith("v")))][0].tagName // ""' 2>/dev/null || echo ""
}

do_status() {
  say "${B}MaxCall release status${OFF}"
  say "  repo            : $REPO"
  say "  branch          : $(branch)"
  say "  const.h version : $(current_version)"
  say "  audience        : $([ "$CHANNEL" = "all" ] && echo "all users (stable)" || echo "developer preview (beta)")"
  say "  working tree    : $(git status --porcelain | wc -l) changed file(s)"
  say ""
  say "  ${DIM}recent tags${OFF}"
  git tag --sort=-creatordate | head -n 6 | sed 's/^/    /'
  if gh auth status >/dev/null 2>&1; then
    say ""
    say "  ${DIM}recent pipelines${OFF}"
    gh run list --repo "$REPO" --limit 5 \
      --json conclusion,headBranch,displayTitle \
      --jq '.[] | "    \(.conclusion // "running")\t\(.headBranch)\t\(.displayTitle[0:48])"' 2>/dev/null || true
    say ""
    say "  ${DIM}channels${OFF}"
    local row
    for row in "$BETA_TAG:beta build" "$STABLE_MANIFEST_TAG:stable manifest" \
               "$BETA_MANIFEST_TAG:beta manifest"; do
      local tag="${row%%:*}" label="${row##*:}" v
      v="$(gh release view "$tag" --repo "$REPO" --json publishedAt --jq '.publishedAt // "-"' 2>/dev/null || echo "-")"
      say "    $label ($tag): ${v:--}"
    done
  fi
}

do_verify() {
  require_repo
  head_ "Verifying the published channels"
  mkdir -p "$DIST"

  local manifest
  for pair in "$STABLE_MANIFEST_TAG:stable" "$BETA_MANIFEST_TAG:beta"; do
    local tag="${pair%%:*}" label="${pair##*:}"
    manifest="$(gh release download "$tag" --repo "$REPO" --pattern 'update.json' \
      --dir "$DIST" --clobber >/dev/null 2>&1 && cat "$DIST/update.json" || echo "")"
    if [ -n "$manifest" ]; then
      ok "$label manifest: $manifest"
    else
      warn "$label manifest not published yet"
    fi
  done

  say ""
  say "  ${DIM}Releases a person opens should list exactly one asset${OFF}"
  local tags t assets
  tags="$(gh release list --repo "$REPO" --limit 8 --json tagName --jq '.[].tagName' 2>/dev/null \
    | grep -v '^manifest-' || true)"
  for t in $tags; do
    assets="$(gh release view "$t" --repo "$REPO" --json assets --jq '.assets[].name' 2>/dev/null | tr '\n' ' ')"
    if [ "$assets" = "MaxCall.exe " ]; then
      ok "$t -> single asset: MaxCall.exe"
    else
      warn "$t -> assets: ${assets:-none}"
    fi
  done

  say ""
  say "  ${DIM}Machine-only manifest releases${OFF}"
  for t in "$BETA_MANIFEST_TAG" "$STABLE_MANIFEST_TAG"; do
    assets="$(gh release view "$t" --repo "$REPO" --json assets --jq '.assets[].name' 2>/dev/null | tr '\n' ' ')"
    [ -n "$assets" ] && ok "$t -> $assets" || warn "$t -> not published yet"
  done
}

# ---------------------------------------------------------------------------
# menus
# ---------------------------------------------------------------------------
banner() {
  cat <<EOF

${B}  MaxCall release tool${OFF}
  ${DIM}repo       ${OFF} $REPO
  ${DIM}branch     ${OFF} $(branch)
  ${DIM}const.h    ${OFF} $(current_version)
  ${DIM}changes    ${OFF} $(git status --porcelain | wc -l) uncommitted

EOF
}

choose_channel() {
  cat <<EOF
${B}Who is this build for?${OFF}

  ${B}1)${OFF} Beta  - developer preview, only on machines set to Beta builds
  ${B}2)${OFF} All   - published to every user on the stable channel

EOF
  ask_choice "  Choose [1]: " 1 1 2 || die "cancelled"
  if [ "$CHOICE" = "1" ]; then
    CHANNEL="beta"
    say "  ${OK}beta${OFF} - builds land on the $BETA_TAG channel, invisible to users"
  else
    CHANNEL="all"
    say "  ${OK}all${OFF} - releases reach every user on the stable channel"
  fi
}

menu_beta() {
  head_ "Beta - developer preview"
  cat <<EOF
  ${B}1)${OFF} Publish a test build      (push to main, beta channel only)
  ${B}2)${OFF} Download the latest test build
  ${B}3)${OFF} Status
  ${B}4)${OFF} Verify the channels
  ${B}5)${OFF} How clients receive updates
  ${B}0)${OFF} Quit

EOF
  ask_choice "  Choose [0]: " 0 0 1 2 3 4 5 || return 0
  case "$CHOICE" in
    1) do_test ;;
    2) do_fetch ;;
    3) do_status ;;
    4) do_verify ;;
    5) do_how ;;
  esac
}

menu_all() {
  head_ "All users - stable channel"
  cat <<EOF
  ${B}1)${OFF} Release a version to everyone
  ${B}2)${OFF} Download the latest stable build
  ${B}3)${OFF} Status
  ${B}4)${OFF} Verify the channels
  ${B}5)${OFF} How clients receive updates
  ${B}0)${OFF} Quit

EOF
  ask_choice "  Choose [0]: " 0 0 1 2 3 4 5 || return 0
  case "$CHOICE" in
    1) do_release ;;
    2) do_fetch ;;
    3) do_status ;;
    4) do_verify ;;
    5) do_how ;;
  esac
}

menu_loop() {
  while :; do
    banner
    if [ "$CHANNEL" = "all" ]; then menu_all; else menu_beta; fi
    say ""
    ask CHOICE "  Press Enter to go back, or q to quit: " || return 0
    [ "${CHOICE,,}" = "q" ] && return 0
    say ""
  done
}

usage() {
  sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
}

# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
case "${1:-}" in
  ""|-i|interactive)
    banner
    require_repo
    choose_channel
    menu_loop
    ;;
  --channel)
    shift
    case "${1:-}" in
      beta) CHANNEL="beta" ;;
      all)  CHANNEL="all" ;;
      *)    die "--channel takes beta or all" ;;
    esac
    shift
    "${1:-}" 2>/dev/null || { usage; exit 1; }
    ;;
  beta|test)   CHANNEL="beta"; do_test ;;
  all|release) shift; CHANNEL="all"; do_release "$@" ;;
  fetch|download) shift; CHANNEL="${CHANNEL:-beta}"; do_fetch "$@" ;;
  status)      CHANNEL="${CHANNEL:-beta}"; do_status ;;
  verify)      CHANNEL="${CHANNEL:-beta}"; do_verify ;;
  -h|--help|help) usage ;;
  *)           die "unknown command '$1' - run ./release.sh for the menu" ;;
esac