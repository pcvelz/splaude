#!/usr/bin/env bash
# Prints the last splaude release tag reachable from a commit.
#
# Plain `git describe --tags` is NOT safe in this fork. Upstream tags releases
# x.y.z, the same bare form we use, so `git fetch upstream` (without --no-tags)
# fills our tag namespace with upstream's releases, and after an upstream merge
# they are reachable via the merge's second parent. Anything built on that, the
# release notes' base or the next version, would silently point at upstream's
# release instead of ours.
#
# This resolver lists the tags reachable from <rev>, newest first, and refuses
# any that is not a fork release: a fork release is a tag whose
# dev/tools/package.py names this fork's repository, which upstream's never do.
#
# Usage:
#   bash dev/tools/last_release_tag.sh [<rev>]    # newest fork tag on <rev>'s first-parent line (default HEAD)
#
# Exit codes: 0 = tag printed, 2 = no valid fork tag found (the first fork release has not happened yet).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

FORK_REPO="pcvelz/splaude"
REV="${1:-HEAD}"

die() { echo "last_release_tag: $*" >&2; exit 2; }

is_fork_tag() {
    # Capture first: `git show | grep -q` under pipefail fails on SIGPIPE.
    local src
    src=$(git show "$1:dev/tools/package.py" 2>/dev/null || true)
    grep -qE "^REPOSITORY *= *\"$FORK_REPO\"" <<< "$src"
}

# Candidates newest-first along the first-parent line.
while IFS= read -r TAG; do
    [[ -z "$TAG" ]] && continue
    git merge-base --is-ancestor "$TAG" "$REV" || continue
    if is_fork_tag "$TAG"; then echo "$TAG"; exit 0; fi
done < <(git tag --merged "$REV" --list '[0-9]*.[0-9]*.[0-9]*' --sort=-version:refname)

die "no $FORK_REPO release tag on $REV (upstream's x.y.z tags do not count) - this is the first fork release"
