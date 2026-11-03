#!/usr/bin/env bash
# Flags upstream identity where the fork's own is meant.
#
# This fork publishes pcvelz/splaude (GitHub), pcvelz/tap (Homebrew) and the
# formula splaude. Code, docs or release artifacts that name upstream's
# release repo, tap, or install command silently send users, the updater and
# `brew` to incoai instead of us. Mentions that are only credit stay legal: the
# README's "fork of" line, links to upstream pull requests, and the incoai/<Model>
# Hugging Face repositories the runtime downloads. The pattern matches only the
# forms that would publish or install from upstream.
#
# Usage:
#   bash dev/tools/check_fork_identity.sh [REV]            # tracked files at REV (default HEAD)
#   bash dev/tools/check_fork_identity.sh --worktree       # the working tree instead
#   bash dev/tools/check_fork_identity.sh --artifacts DIR  # release output (formula, cask, appcast) in DIR
#   bash dev/tools/check_fork_identity.sh --config         # local git config that keeps upstream out of ours
#
# Exit codes: 0 = clean, 1 = upstream identity found, 2 = usage/git error.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

FORK_REPO="pcvelz/splaude"
FORK_TAP="pcvelz/tap"
FORMULA_CLASS="Splaude"

# Tracked code and docs: only the forms that publish or install from upstream.
CODE_PATTERN='incoai/(homebrew-tap|tap)([^A-Za-z0-9_-]|$)|incoai/splash(\.git|/releases)|incoai\.github\.io|brew +(install|tap|upgrade|audit|reinstall|info)[^|]*incoai/'
# Release artifacts ship to users: ANY mention of upstream's repo or tap fails.
ARTIFACT_PATTERN='incoai/(splash|homebrew-tap|tap)([^A-Za-z0-9_-]|$)|incoai\.github\.io'
# Tests keep upstream strings as fixtures on purpose; the notices credit upstream;
# the guards themselves name upstream in order to refuse it.
PATHS=(. ':!dev/tests/**' ':!THIRD_PARTY_NOTICES' ':!dev/tools/check_fork_identity.sh' ':!dev/tools/verify_upstream_merge.sh' ':!.githooks/pre-push')

FAILS=0
fail() { echo "BLOCKED: $*"; FAILS=$((FAILS + 1)); }

scan_tracked() {
    local rc=0 hits
    if [[ "$1" == "--worktree" ]]; then
        hits=$(git grep -nEI "$CODE_PATTERN" -- "${PATHS[@]}") || rc=$?
    else
        hits=$(git grep -nEI "$CODE_PATTERN" "$1" -- "${PATHS[@]}") || rc=$?
    fi
    if (( rc > 1 )); then echo "check_fork_identity: git grep failed (exit $rc)" >&2; exit 2; fi
    if [[ -n "$hits" ]]; then
        fail "upstream release repo, tap or install command where the fork's is meant:"
        sed 's/^/    /' <<< "$hits"
    else
        echo "OK: no upstream identity in tracked files"
    fi
}

scan_artifacts() {
    local dir="$1" hits files f
    [[ -d "$dir" ]] || { echo "check_fork_identity: $dir is not a directory" >&2; exit 2; }
    hits=$(grep -rnEI "$ARTIFACT_PATTERN" "$dir" 2>/dev/null || true)
    if [[ -n "$hits" ]]; then
        fail "release artifacts name upstream (formula, cask, appcast or notes would publish to incoai):"
        sed 's/^/    /' <<< "$hits"
    fi
    files=$(find "$dir" -type f \( -name '*.rb' \) 2>/dev/null)
    if [[ -z "$files" ]]; then
        fail "no formula or cask (*.rb) found in $dir - nothing to verify"
    fi
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        grep -q "homepage \"https://github.com/$FORK_REPO\"" "$f" \
            || fail "$f: homepage is not https://github.com/$FORK_REPO"
        grep -qE "^class (Splash|SplashApp) " "$f" \
            && fail "$f: class name is upstream's (Splash/SplashApp) - the fork's formula is $FORMULA_CLASS"
        if grep -q "^class $FORMULA_CLASS < Formula" "$f"; then
            grep -q 'bin/"splaude"' "$f" \
                || fail "$f: formula does not install the fork's splaude command"
            if grep -q '(bin/"splash")' "$f"; then
                fail "$f: formula still installs upstream's splash command"
            fi
            grep -q "github.com/$FORK_REPO/releases/download/" "$f" \
                || fail "$f: url is not a $FORK_REPO release asset"
        fi
    done <<< "$files"
    [[ $FAILS -eq 0 ]] && echo "OK: release artifacts in $dir carry the fork's identity"
}

check_config() {
    local origin upstream tagopt
    origin=$(git remote get-url origin 2>/dev/null || true)
    upstream=$(git remote get-url upstream 2>/dev/null || true)
    tagopt=$(git config --get remote.upstream.tagOpt || true)
    grep -q "$FORK_REPO" <<< "$origin" || fail "origin is not $FORK_REPO ($origin) - never release from or push to upstream"
    if [[ -n "$upstream" ]] && ! grep -q 'incoai/splash' <<< "$upstream"; then
        fail "upstream remote is not incoai/splash ($upstream)"
    fi
    [[ "$tagopt" == "--no-tags" ]] \
        || fail "upstream's tags share our x.y.z namespace and are fetched into it - run: git config remote.upstream.tagOpt --no-tags"
    [[ $FAILS -eq 0 ]] && echo "OK: origin is $FORK_REPO, upstream tags are not fetched"
}

case "${1:-}" in
    --artifacts) [[ -n "${2:-}" ]] || { echo "usage: --artifacts DIR" >&2; exit 2; }; scan_artifacts "$2" ;;
    --config)    check_config ;;
    --worktree)  scan_tracked --worktree ;;
    *)           scan_tracked "${1:-HEAD}" ;;
esac

[[ $FAILS -eq 0 ]] || exit 1
exit 0
