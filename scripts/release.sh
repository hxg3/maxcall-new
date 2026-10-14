#!/usr/bin/env bash
#
# MaxCall-NEW auto-release (ACTIVE - WebView2 Shell)
# ----------------------------------------------------
# النسخة الجديدة قيد التطوير (maxcall-new).
# هذه هي النسخة الافتراضية للعمل اليومي.
#
# Saves your changes, pushes main, and then publishes depending on who the
# build is for. Pushing main already starts the GitHub Action; pushing a
# version tag is what makes the result reach every user.
#
#   1) Beta - commit and push main only. The build lands on the beta channel
#             and only machines set to "Beta builds" ever see it.
#   2) All  - the same, plus a vX.Y.Z tag, which publishes to every user on
#             the stable channel.
#
# Just run it, it asks everything through numbered menus:
#   ./release.sh
#
# Or pick the audience up front:
#   ./release.sh beta
#   ./release.sh all
#
# Fully unattended (the commit message must be introduced by --msg):
#   ./release.sh beta --msg "fix(call): drop the echo" --no-watch
#   ./release.sh all  --msg "release" --no-watch
#
set -euo pipefail

REPO_DIR="/home/red/VirtualBox VMs/VitalPBX-MaxCare/maxcall"
BETA_CHANNEL_TAG="beta"
MANIFEST_BETA_TAG="manifest-beta"
MANIFEST_STABLE_TAG="manifest-stable"
DIST="dist"

# Where the signed manifests are copied for clients that cannot reach github.com.
# Point this at the folder your web server already publishes, or leave the default
# and point the server at scripts/mirror. The address is baked into the binary
# (const.h), so nothing about the mirror ever reaches the user.
MIRROR_DIR="${MAXCALL_MIRROR_DIR:-$(cd "$(dirname "$0")" && pwd)/mirror}"

# Where the mirror actually lives on the network. Clients fetch update.json, the
# signatures and the payload from this host over plain HTTP, because the office
# network will not complete TLS with github.com. Publishing is over SSH with a
# key, so no password is stored anywhere and no step is left to do by hand.
MIRROR_HOST="${MAXCALL_MIRROR_HOST:-192.168.1.32}"
MIRROR_USER="${MAXCALL_MIRROR_USER:-root}"
MIRROR_REMOTE="${MAXCALL_MIRROR_REMOTE:-/opt/maxcall-updates}"

MSG=""
CHANNEL=""
WATCH=true

command -v gh >/dev/null || { echo "ERROR: gh tool is missing. Install GitHub CLI first."; exit 1; }
command -v git >/dev/null || { echo "ERROR: git tool is missing."; exit 1; }

cd "$REPO_DIR"

if [ -t 1 ]; then
  B=$'\033[1m'; DIM=$'\033[2m'; GRN=$'\033[32m'; YEL=$'\033[33m'; OFF=$'\033[0m'
else
  B=""; DIM=""; GRN=""; YEL=""; OFF=""
fi
ok()   { printf '  %sOK%s    %s\n'   "$GRN" "$OFF" "$*"; }
warn() { printf '  %sWARN%s  %s\n'   "$YEL" "$OFF" "$*"; }
die()  { printf '  ERROR  %s\n' "$*"; exit 1; }
title(){ printf '\n%s%s%s\n' "$B" "$*" "$OFF"; }

# Reads a line; re-asks until the answer is one of the allowed values.
ask_choice() {
  local prompt="$1" default="$2"; shift 2
  local reply a
  CHOICE=""
  while :; do
    printf "%s" "$prompt"
    if ! IFS= read -r reply; then printf '\n'; exit 0; fi
    reply="${reply:-$default}"
    for a in "$@"; do
      if [ "$reply" = "$a" ]; then
        CHOICE="$reply"
        return 0
      fi
    done
    printf '  %senter one of: %s%s\n' "$YEL" "$*" "$OFF"
  done
}

ask_text() {
  local prompt="$1" fallback="$2" __var="$3" reply
  printf "%s" "$prompt"
  if ! IFS= read -r reply; then printf '\n'; exit 0; fi
  [ -z "$reply" ] && reply="$fallback"
  printf -v "$__var" '%s' "$reply"
}

# ---------------------------------------------------------------------------
# First question: who is this build for? It decides what can be published.
# ---------------------------------------------------------------------------
choose_channel() {
  title "Who is this build for?"
  cat <<EOF

  ${B}1)${OFF} Beta  - developer preview. Only machines set to
                     "Beta builds" in the tray menu will ever see it.
  ${B}2)${OFF} All   - published to every user on the stable channel.

EOF
  ask_choice "  Choose [1]: " 1 1 2
  if [ "$CHOICE" = "1" ]; then
    CHANNEL="beta"
    printf '  %sbeta%s - no tag is created, so nothing reaches users.\n' "$GRN" "$OFF"
  else
    CHANNEL="all"
    printf '  %sall%s - a version tag will be published to every user.\n' "$GRN" "$OFF"
  fi
}

# ---------------------------------------------------------------------------
# Second question: the commit message.
# ---------------------------------------------------------------------------
choose_message() {
  if [ -n "$MSG" ]; then
    printf '  %scommit message%s: %s\n' "$DIM" "$OFF" "$MSG"
    return 0
  fi
  if git status --short | grep -q .; then
    echo "Changed files:"
    git status --short
    echo ""
  else
    echo "No file changes found. Only the version tag will be created."
    echo ""
  fi
  title "What changed?"
  cat <<EOF

  ${B}1)${OFF} Write my own message
  ${B}2)${OFF} Use the default ("update")

EOF
  ask_choice "  Choose [2]: " 2 1 2
  if [ "$CHOICE" = "1" ]; then
    ask_text "  Message: " "update" MSG
  else
    MSG="update"
  fi
}

# ---------------------------------------------------------------------------
# Third question: follow the build.
# ---------------------------------------------------------------------------
choose_watch() {
  if [ "$WATCH" = "false" ]; then return 0; fi
  title "Watch the build until it finishes?"
  cat <<EOF

  ${B}1)${OFF} Yes
  ${B}2)${OFF} No

EOF
  ask_choice "  Choose [1]: " 1 1 2
  [ "$CHOICE" = "1" ] || WATCH=false
}

# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
step_save() {
  git add -A
  if git diff --cached --quiet; then
    echo "Step: nothing to save, skipping."
  else
    git commit -m "$MSG"
    echo "Step: changes saved."
  fi
}

step_push_main() {
  git push origin main
  echo "Step: main branch pushed."
}

# The newest tag is not necessarily the newest release: a tag can exist without
# a release behind it when a publish was refused. Walking down until a release
# actually exists keeps the version we propose honest, and surfaces the gap
# instead of silently skipping a number.
next_version() {
  git fetch origin --tags --quiet
  local repo t published="" beta_base="" best=""

  repo="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo "")"
  if [ -n "$repo" ]; then
    # Highest stable tag that actually has a release behind it. A tag left by a
    # refused publish is skipped and reported, rather than silently trusted.
    for t in $(git tag --list 'v[0-9]*' --sort=-v:refname); do
      if gh release view "$t" --repo "$repo" >/dev/null 2>&1; then
        published="${t#v}"
        break
      fi
      # stderr on purpose: this runs inside $(...) and must not pollute the value
      warn "tag $t has no published release - ignoring it when picking the next version" >&2
    done

    # Beta builds carry their own counter and have no tag, so the number in use
    # is whatever the beta manifest currently advertises.
    if mkdir -p "$DIST" \
       && gh release download "$MANIFEST_BETA_TAG" --repo "$repo" \
            --pattern 'update.json' --dir "$DIST" --clobber >/dev/null 2>&1; then
      beta_base=$(sed -n 's/.*"version":"\([^"]*\)".*/\1/p' "$DIST/update.json" | head -n1)
      beta_base="${beta_base%%-*}"
    fi
  fi

  for c in "$published" "$beta_base"; do
    [ -z "$c" ] && continue
    [ -z "$best" ] && { best="$c"; continue; }
    # Numeric compare per component so 6.1.9 never beats 6.1.10.
    if [ "$(printf '%s\n%s\n' "$best" "$c" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n1)" = "$c" ]; then
      best="$c"
    fi
  done
  echo "${best:-0.0.0}"
}

# Keeps const.h in step with the release so the repo never advertises a
# version that is older than what has shipped.
bump_const_h() {
  local version="$1"
  # Defensive: const.h feeds the resource compiler, so a stray "v" here produces
  # "_GLOBAL_VERSION_MAJOR v6" and breaks the whole build.
  version="${version#v}"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "refusing to write a bad version into const.h: $version"
  python3 - "$version" <<'PY'
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

confirm_version() {
  # $1 is the highest base version already in use (bare, e.g. 6.1.6).
  local base="$1" custom="" maj min pat next
  IFS=. read -r maj min pat <<< "$base"
  next="${maj}.${min}.$((pat + 1))"
  title "Release version"
  cat <<EOF

  ${DIM}highest version in use${OFF} : $base
  ${DIM}const.h says           ${OFF} : $(grep -oP '(?<=^#define _GLOBAL_VERSION ")[^"]+' const.h)

  ${B}1)${OFF} Release $next to everyone
  ${B}2)${OFF} Type a different version
  ${B}3)${OFF} Cancel - push main only

EOF
  ask_choice "  Choose [1]: " 1 1 2 3
  case "$CHOICE" in
    1) RELEASED="$next" ;;
    2) ask_text "  Version (x.y.z): " "$next" custom
        custom="${custom#v}"
        if ! [[ "$custom" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
          die "version must look like 6.1.7"
        fi
        RELEASED="$custom" ;;
    3) RELEASED="" ;;
  esac
  printf '  will publish as tag: v%s\n' "${RELEASED:-<none>}"
}

# $1 is a BARE version (6.1.7); the "v" exists only on the tag itself.
do_release_tag() {
  local version="$1" tag="v$1"
  local declared
  declared="$(grep -oP '(?<=^#define _GLOBAL_VERSION ")[^"]+' const.h)"
  if [ "$declared" != "$version" ]; then
    bump_const_h "$version"
    git add const.h
    git commit -m "release: MaxCall $version"
    ok "const.h $declared -> $version"
  fi

  if git rev-parse "$tag" >/dev/null 2>&1; then
    die "tag $tag already exists - delete it first, or pick a different version"
  fi

  git tag -a "$tag" -m "MaxCall $tag"
  git push origin "$tag"
  echo "Step: version $tag pushed."
}

# Copies the signed manifests onto the internal mirror, automatically after every
# publish. On a network that cannot complete TLS with github.com this is the only
# route a client has to an update, so it must never be a separate manual step.
publish_mirror() {
  local channel="$1" tag="$2" repo payload
  repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

  # Each channel gets its own payload file. The stable and beta manifests point
  # at different builds with different hashes, so one file cannot serve both.
  if [ "$channel" = "beta" ]; then payload="MaxCall-beta.exe"; else payload="MaxCall.exe"; fi

  mkdir -p "$DIST" "$MIRROR_DIR"
  if ! gh release download "$tag" --repo "$repo" --pattern 'update.json' \
       --dir "$DIST" --clobber >/dev/null 2>&1; then
    warn "no $channel manifest published yet - skipping the mirror"
    return 0
  fi
  gh release download "$tag" --repo "$repo" --pattern 'update.json.sig' \
    --dir "$DIST" --clobber >/dev/null 2>&1

  local release_tag
  if [ "$channel" = "beta" ]; then
    release_tag="$BETA_CHANNEL_TAG"
  else
    release_tag="$(gh release list --repo "$repo" --limit 20 --json tagName,isPrerelease \
      -q '[.[] | select(.isPrerelease == false and (.tagName | startswith("v")))][0].tagName // ""')"
  fi
  [ -n "$release_tag" ] || { warn "no release for the $channel payload - manifests only"; return 0; }

  if ! gh release download "$release_tag" --repo "$repo" --pattern 'MaxCall.exe' \
       --dir "$DIST" --clobber >/dev/null 2>&1; then
    warn "no payload at $release_tag - manifests only"
    return 0
  fi

  if [ "$channel" = "beta" ]; then
    cp -f "$DIST/update.json"     "$MIRROR_DIR/update.json.beta"
    cp -f "$DIST/update.json.sig" "$MIRROR_DIR/update.json.beta.sig"
  else
    cp -f "$DIST/update.json"     "$MIRROR_DIR/update.json"
    cp -f "$DIST/update.json.sig" "$MIRROR_DIR/update.json.sig"
  fi
  cp -f "$DIST/MaxCall.exe" "$MIRROR_DIR/$payload"
  ok "mirror staged: manifests + $payload"

  # Ship it. Without this the client has nothing to read and the update check
  # can only fail, so a failure here has to stop the release, not be ignored.
  if ! scp -q -o ConnectTimeout=15 \
       "$MIRROR_DIR/update.json" "$MIRROR_DIR/update.json.sig" \
       "$MIRROR_DIR/update.json.beta" "$MIRROR_DIR/update.json.beta.sig" \
       "$MIRROR_DIR/MaxCall.exe" "$MIRROR_DIR/MaxCall-beta.exe" \
       "$MIRROR_USER@$MIRROR_HOST:$MIRROR_REMOTE/" 2>/dev/null; then
    die "mirror upload to $MIRROR_HOST failed - the release would be invisible to clients"
  fi
  ssh -o ConnectTimeout=15 "$MIRROR_USER@$MIRROR_HOST" \
    "chmod 644 $MIRROR_REMOTE/* && rm -f $MIRROR_REMOTE/*.part" 2>/dev/null

  # A manifest that is readable but whose payload does not match its hash would
  # fail in the client's hands, so confirm the pair actually agrees.
  local want got
  want="$(python3 -c "import json;print(json.load(open('$MIRROR_DIR/update.json.beta'))['sha256'])" 2>/dev/null)"
  got="$(sha256sum "$MIRROR_DIR/MaxCall-beta.exe" 2>/dev/null | cut -d' ' -f1)"
  [ -n "$want" ] && [ "$want" != "$got" ] && die "beta payload does not match the signed sha256"
  want="$(python3 -c "import json;print(json.load(open('$MIRROR_DIR/update.json'))['sha256'])" 2>/dev/null)"
  got="$(sha256sum "$MIRROR_DIR/MaxCall.exe" 2>/dev/null | cut -d' ' -f1)"
  [ -n "$want" ] && [ "$want" != "$got" ] && die "stable payload does not match the signed sha256"

  ok "mirror live at http://$MIRROR_HOST:3001/updates"
}

wait_build() {
  local repo run_id
  repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
  $WATCH || return 0

  echo ""
  echo "Waiting for the build to start..."
  local before
  before="$(gh run list --repo "$repo" --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || echo "")"
  run_id=""
  for _ in $(seq 1 12); do
    sleep 3
    run_id="$(gh run list --repo "$repo" --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || echo "")"
    [ -n "$run_id" ] && [ "$run_id" != "$before" ] && break
    run_id=""
  done
  [ -n "$run_id" ] || { warn "the build did not start, check the Actions tab"; return 0; }

  echo "Run $run_id"
  if gh run watch "$run_id" --repo "$repo" --exit-status --interval 15; then
    ok "build succeeded"
    echo ""
    echo "Release page: https://github.com/$repo/releases"
  else
    warn "the build failed"
    gh run view "$run_id" --repo "$repo" --log-failed 2>/dev/null | tail -n 40 || true
  fi
}

# ---------------------------------------------------------------------------
# Read-only helpers, same numbered-menu style
# ---------------------------------------------------------------------------
do_status() {
  title "Status"
  local repo
  repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
  printf '  %-18s %s\n' "repo"       "$repo"
  printf '  %-18s %s\n' "branch"     "$(git rev-parse --abbrev-ref HEAD)"
  printf '  %-18s %s\n' "const.h"    "$(grep -oP '(?<=^#define _GLOBAL_VERSION ")[^"]+' const.h)"
  printf '  %-18s %s\n' "uncommitted" "$(git status --porcelain | wc -l)"
  echo ""
  printf '  %srecent tags%s\n' "$DIM" "$OFF"
  git tag --sort=-creatordate | head -n 6 | sed 's/^/    /'
  echo ""
  printf '  %schannels%s\n' "$DIM" "$OFF"
  local row
  for row in "$BETA_CHANNEL_TAG:beta build" "$MANIFEST_BETA_TAG:beta manifest" "$MANIFEST_STABLE_TAG:stable manifest"; do
    local tag="${row%%:*}" label="${row##*:}" v
    v="$(gh release view "$tag" --repo "$repo" --json publishedAt -q '.publishedAt // "-"' 2>/dev/null || echo '-')"
    printf '    %-16s %s\n' "$label" "$v"
  done
}

do_fetch() {
  local repo tag="$BETA_CHANNEL_TAG"
  repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
  [ "$CHANNEL" = "all" ] && tag="$(gh release list --repo "$repo" --limit 20 --json tagName,isPrerelease \
      -q '[.[] | select(.isPrerelease == false and (.tagName | startswith("v")))][0].tagName // ""')"
  [ -n "$tag" ] || die "no stable release yet"
  mkdir -p "$DIST"
  gh release download "$tag" --repo "$repo" --pattern 'MaxCall.exe' --dir "$DIST" --clobber
  ok "saved to $DIST/MaxCall.exe"

  printf '  %sPut it in a folder named MaxCall and run it. That is the whole install.%s\n' "$DIM" "$OFF"
}

do_verify() {
  local repo
  repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
  title "Verify"
  mkdir -p "$DIST"
  local manifest
  for row in "$MANIFEST_BETA_TAG:beta" "$MANIFEST_STABLE_TAG:stable"; do
    local tag="${row%%:*}" label="${row##*:}"
    manifest="$(gh release download "$tag" --repo "$repo" --pattern 'update.json' --dir "$DIST" --clobber >/dev/null 2>&1 && cat "$DIST/update.json" || echo "")"
    [ -n "$manifest" ] && ok "$label: $manifest" || warn "$label manifest not published yet"
  done
  echo ""
  printf '  %sReleases a person opens should list exactly one asset%s\n' "$DIM" "$OFF"
  local tags t assets
  tags="$(gh release list --repo "$repo" --limit 8 --json tagName -q '.[].tagName' 2>/dev/null | grep -v '^manifest-' || true)"
  for t in $tags; do
    assets="$(gh release view "$t" --repo "$repo" --json assets -q '.assets[].name' 2>/dev/null | tr '\n' ' ')"
    if [ "$assets" = "MaxCall.exe " ]; then
      ok "$t -> single asset: MaxCall.exe"
    else
      warn "$t -> ${assets:-no assets}"
    fi
  done
}

main_menu() {
  title "MaxCall release tool"
  cat <<EOF

  ${B}1)${OFF} Publish    - commit, push, and build
  ${B}2)${OFF} Download   - fetch MaxCall.exe
  ${B}3)${OFF} Status     - versions, tags, channels
  ${B}4)${OFF} Verify     - check the published channels
  ${B}5)${OFF} Quit

EOF
  ask_choice "  Choose [1]: " 1 1 2 3 4 5
}

usage() {
  sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# Argument handling: a commit message must be introduced by --msg, otherwise a
# stray number ("--beta 2") would silently become the commit message.
while [ $# -gt 0 ]; do
  case "$1" in
    beta|--beta)   CHANNEL="beta" ;;
    all|--all)     CHANNEL="all" ;;
    --no-watch)    WATCH=false ;;
    --msg)         shift; MSG="${1:-}" ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "unknown argument '$1' - use --msg \"your message\"" ;;
  esac
  shift
done

echo "=== MaxCall-NEW auto-release ==="

if [ -z "$CHANNEL" ]; then
  choose_channel
  echo ""
  choose_message
  echo ""
  choose_watch
else
  CHANNEL="${CHANNEL#--}"
  echo ""
  choose_message
  echo ""
  choose_watch
fi

step_save
step_push_main

RELEASED=""
if [ "$CHANNEL" = "all" ]; then
  confirm_version "$(next_version)"
  [ -n "$RELEASED" ] && do_release_tag "$RELEASED"
else
  title "Beta channel"
  cat <<EOF

  No version tag was created, so nothing reaches users.
  The build is on the ${B}$BETA_CHANNEL_TAG${OFF} channel only.

  To receive it on a test machine, do this once:

    1. Right click the MaxCall tray icon > Update Channel > Beta builds
    2. Right click the tray icon again > Check for Updates

EOF
fi

wait_build

if [ "$CHANNEL" = "beta" ]; then
  publish_mirror beta "$MANIFEST_BETA_TAG"
else
  publish_mirror all "$MANIFEST_STABLE_TAG"
fi

if [ "$CHANNEL" = "beta" ]; then
  echo ""
  echo "  To download it: run ./release.sh and pick ${B}1 (Beta)${OFF} then ${B}2 (Download)${OFF}"
fi