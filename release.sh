#!/usr/bin/env bash
#
# release.sh - publishing helper for MaxCall.
#
# The application ships as ONE file. Everything the auto-updater needs is
# machine-only metadata that the pipeline publishes on its own hidden releases,
# so a release page never shows more than MaxCall.exe.
#
# Usage:
#   ./release.sh test              build a developer preview (beta channel only)
#   ./release.sh release <x.y.z>    publish a stable build to all users
#   ./release.sh status            show version, tags and channel state
#   ./release.sh fetch             download the latest build to ./dist
#   ./release.sh watch [run-id]    follow the running pipeline until it finishes
#   ./release.sh verify            check that a published build is self-consistent
#
set -euo pipefail

REPO="${MAXCALL_REPO:-hxg3/maxcall-new}"
REMOTE="${MAXCALL_REMOTE:-origin}"
BETA_TAG="beta"
BETA_MANIFEST_TAG="manifest-beta"
STABLE_MANIFEST_TAG="manifest-stable"
DIST="dist"

cd "$(git rev-parse --show-toplevel)"

c_dim=$'\033[2m'; c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_err=$'\033[31m'; c_off=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s  OK%s   %s\n' "$c_ok" "$c_off" "$*"; }
warn() { printf '%s  WARN%s %s\n' "$c_warn" "$c_off" "$*"; }
die()  { printf '%s  FAIL%s %s\n' "$c_err" "$c_off" "$*" >&2; exit 1; }
step() { printf '\n%s==>%s %s\n' "$c_ok" "$c_off" "$*"; }

require_repo() {
  git remote get-url "$REMOTE" >/dev/null 2>&1 || die "no git remote '$REMOTE'"
  command -v gh >/dev/null 2>&1 || die "gh is required (https://cli.github.com)"
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated"
}

current_version() {
  grep -oP '(?<=^#define _GLOBAL_VERSION ")[^"]+' const.h
}

# ---------------------------------------------------------------------------
# test: developer preview, visible only on the beta channel
# ---------------------------------------------------------------------------
cmd_test() {
  require_repo
  step "Publishing a developer preview"

  git fetch --quiet "$REMOTE" || true
  git pull --ff-only --quiet "$REMOTE" "$(git rev-parse --abbrev-ref HEAD)" \
    || die "working tree is behind; resolve it first"

  if ! git diff --quiet; then
    die "you have uncommitted changes - commit them before publishing"
  fi

  git push "$REMOTE" HEAD
  ok "pushed to $(git rev-parse --abbrev-ref HEAD)"

  say ""
  say "  This build lands on the $BETA_TAG channel only. Users on the stable"
  say "  channel will not see it."
  say ""
  say "  To receive it, switch the client to the beta channel:"
  say "    tray icon > Update Channel > Beta builds"
  say ""
  step "Waiting for the pipeline"
  watch ""
}

# ---------------------------------------------------------------------------
# release: stable build for every user
# ---------------------------------------------------------------------------
cmd_release() {
  require_repo
  local version="${1:-}"
  [[ -n "$version" ]] || die "usage: ./release.sh release <x.y.z>"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like 6.1.6"

  step "Preparing release $version"

  git fetch --quiet "$REMOTE" --tags || true
  git pull --ff-only --quiet "$REMOTE" "$(git rev-parse --abbrev-ref HEAD)" \
    || die "working tree is behind; resolve it first"

  if git rev-parse "v$version" >/dev/null 2>&1; then
    die "tag v$version already exists"
  fi

  local current
  current="$(current_version)"
  say "  const.h currently declares $current"

  # The pipeline refuses a tag that does not match const.h, so write it here.
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
  ok "const.h now declares $version"

  git add const.h
  git commit -m "release: MaxCall $version"
  ok "committed"

  git tag -a "v$version" -m "MaxCall $version"
  git push "$REMOTE" HEAD
  git push "$REMOTE" "v$version"
  ok "pushed commit and tag v$version"

  say ""
  say "  Users on the stable channel will be offered this version within"
  say "  $_interval hours of their last check."
  say ""
  step "Waiting for the pipeline"
  watch ""
}

# ---------------------------------------------------------------------------
# watch: follow the newest pipeline until it settles
# ---------------------------------------------------------------------------
cmd_watch() {
  local run_id="${1:-}"
  local before=""

  if [[ -z "$run_id" ]]; then
    before="$(gh run list --repo "$REPO" --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || echo "")"
    # Give the push a moment to register.
    for _ in $(seq 1 12); do
      sleep 3
      local now
      now="$(gh run list --repo "$REPO" --limit 1 --json databaseId,headBranch,status --jq '.[0].databaseId' 2>/dev/null || echo "")"
      if [[ -n "$now" && "$now" != "$before" ]]; then
        run_id="$now"
        break
      fi
    done
    [[ -n "$run_id" ]] || die "could not find the new pipeline run"
  fi

  say "  following run $run_id"
  local failed=0
  if ! gh run watch "$run_id" --repo "$REPO" --exit-status --interval 15; then
    failed=1
  fi

  if [[ "$failed" -ne 0 ]]; then
    warn "the pipeline failed"
    say ""
    gh run view "$run_id" --repo "$REPO" --log-failed 2>/dev/null | tail -n 60 || true
    return 1
  fi
  ok "pipeline succeeded"
  return 0
}

# ---------------------------------------------------------------------------
# fetch: download the latest build
# ---------------------------------------------------------------------------
cmd_fetch() {
  require_repo
  local tag="${1:-$BETA_TAG}"
  mkdir -p "$DIST"
  say "  downloading MaxCall.exe from tag $tag"
  gh release download "$tag" --repo "$REPO" --pattern 'MaxCall.exe' \
    --dir "$DIST" --clobber
  ok "saved to $DIST/MaxCall.exe"
  verify
}

# ---------------------------------------------------------------------------
# verify: confirm a published build is self-consistent
# ---------------------------------------------------------------------------
cmd_verify() {
  require_repo
  step "Verifying the published channels"

  local stable_manifest beta_manifest
  stable_manifest="$(gh release download "$STABLE_MANIFEST_TAG" --repo "$REPO" \
    --pattern 'update.json' --dir "$DIST" --clobber >/dev/null 2>&1 \
    && cat "$DIST/update.json" || echo "")"
  beta_manifest="$(gh release download "$BETA_MANIFEST_TAG" --repo "$REPO" \
    --pattern 'update.json' --dir "$DIST" --clobber >/dev/null 2>&1 \
    && cat "$DIST/update.json" || echo "")"

  [[ -n "$stable_manifest" ]] && ok "stable manifest reachable: $stable_manifest" \
    || warn "no stable manifest yet (publish a tagged release first)"
  [[ -n "$beta_manifest" ]] && ok "beta manifest reachable: $beta_manifest" \
    || warn "no beta manifest yet (push to main first)"

  say ""
  say "  Releases a user opens should list exactly one asset:"
  local tags
  tags="$(gh release list --repo "$REPO" --limit 8 --json tagName --jq '.[].tagName' 2>/dev/null \
    | grep -v '^manifest-' || true)"
  for t in $tags; do
    local assets
    assets="$(gh release view "$t" --repo "$REPO" --json assets --jq '.assets[].name' 2>/dev/null | tr '\n' ' ')"
    if [[ "$assets" == "MaxCall.exe " ]]; then
      ok "$t -> single asset: MaxCall.exe"
    else
      warn "$t -> assets: ${assets:-none}"
    fi
  done

  say ""
  say "  Manifest-only releases (machine, never for humans):"
  for t in "$BETA_MANIFEST_TAG" "$STABLE_MANIFEST_TAG"; do
    local assets
    assets="$(gh release view "$t" --repo "$REPO" --json assets --jq '.assets[].name' 2>/dev/null | tr '\n' ' ')"
    [[ -n "$assets" ]] && ok "$t -> $assets" || warn "$t -> not published yet"
  done
}

# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
cmd_status() {
  say "MaxCall release status"
  say "  repo            : $REPO"
  say "  branch          : $(git rev-parse --abbrev-ref HEAD)"
  say "  const.h version : $(current_version)"
  say "  working tree    : $(git status --porcelain | wc -l) changed file(s)"
  say ""
  say "  recent tags"
  git tag --sort=-creatordate | head -n 6 | sed 's/^/    /'
  say ""
  if gh auth status >/dev/null 2>&1; then
    say "  recent pipelines"
    gh run list --repo "$REPO" --limit 5 \
      --json displayTitle,headBranch,conclusion,createdAt \
      --jq '.[] | "    \(.conclusion // "running")\t\(.headBranch)\t\(.displayTitle)"' 2>/dev/null || true
    say ""
    say "  channels"
    for pair in "$BETA_TAG:beta" "$STABLE_MANIFEST_TAG:stable manifest" "$BETA_MANIFEST_TAG:beta manifest"; do
      local tag="${pair%%:*}" label="${pair##*:}"
      local v
      v="$(gh release view "$tag" --repo "$REPO" --json publishedAt --jq '.publishedAt' 2>/dev/null || echo "-")"
      say "    $label ($tag): ${v:-not published}"
    done
  fi
}

_interval=6
case "${1:-}" in
  test|beta)          cmd_test ;;
  release|publish)    shift; cmd_release "$@" ;;
  watch)              shift; cmd_watch "$@" ;;
  fetch|download)     shift; cmd_fetch "$@" ;;
  verify)             cmd_verify ;;
  status)             cmd_status ;;
  ""|-h|--help|help)  sed -n '3,18p' "$0" | sed 's/^# \{0,1\}//' ; exit 0 ;;
  *)                  die "unknown command '$1' (try ./release.sh help)" ;;
esac