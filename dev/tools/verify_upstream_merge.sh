#!/usr/bin/env bash
# Mechanical gate for upstream-merge commits.
#
# A merge of incoai/splash into this fork may contain only three things:
# upstream's changes, conflict resolutions, and compatibility fixes named in the
# commit body. This turns that rule into a PASS/FAIL list so the result does not
# depend on how carefully the merging agent read the checklist.
#
# Usage:
#   bash dev/tools/verify_upstream_merge.sh [<rev>]
#       Verify an existing merge commit (default HEAD).
#   bash dev/tools/verify_upstream_merge.sh --staged "<subject>" [<body-file>]
#       Verify an in-progress merge BEFORE committing it, via a throwaway commit
#       object built from the index (git commit-tree; no ref moves).
#
# Exit codes: 0 = all checks pass, 1 = at least one FAIL, 2 = usage/environment error.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 2

FORK_REPO="pcvelz/splaude"
UPSTREAM_REF="${UPSTREAM_REF:-upstream/main}"
FAILS=0

blob() { git show "$1:$2" 2>/dev/null; }
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; FAILS=$((FAILS + 1)); }
die() { echo "verify_upstream_merge: $*" >&2; exit 2; }

git rev-parse -q --verify "$UPSTREAM_REF^{commit}" >/dev/null \
    || die "$UPSTREAM_REF not found (git remote add upstream https://github.com/incoai/splash.git && git fetch --no-tags upstream)"

# ---------------------------------------------------------------- target commit
if [[ "${1:-}" == "--staged" ]]; then
    SUBJECT="${2:-}"
    [[ -n "$SUBJECT" ]] || die "--staged needs the commit subject you are about to use"
    BODY=""
    if [[ -n "${3:-}" ]]; then
        [[ -f "$3" ]] || die "body file $3 not found"
        BODY=$(cat "$3")
    fi
    git rev-parse -q --verify MERGE_HEAD >/dev/null || die "no merge in progress (MERGE_HEAD missing)"
    UNMERGED=$(git diff --name-only --diff-filter=U)
    if [[ -n "$UNMERGED" ]]; then
        echo "FAIL  unresolved conflicts - resolve and 'git add <file>' each one:"
        sed 's/^/        /' <<< "$UNMERGED"
        exit 1
    fi
    UNSTAGED=$(git diff --name-only)
    if [[ -n "$UNSTAGED" ]]; then
        echo "FAIL  unstaged edits exist - 'git add <file>' each one you intend to commit, or 'git restore <file>':"
        sed 's/^/        /' <<< "$UNSTAGED"
        exit 1
    fi
    TREE=$(git write-tree) || die "git write-tree failed"
    REV=$(printf '%s\n\n%s\n' "$SUBJECT" "$BODY" | git commit-tree "$TREE" -p HEAD -p MERGE_HEAD) \
        || die "git commit-tree failed"
    echo "verify_upstream_merge: staged merge (throwaway commit ${REV:0:12}, no ref moved)"
else
    REV=$(git rev-parse -q --verify "${1:-HEAD}^{commit}") || die "unknown rev ${1:-HEAD}"
    echo "verify_upstream_merge: commit ${REV:0:12}"
fi

# ---------------------------------------------------------------- 1. shape
PARENTS=$(git rev-list --parents -n 1 "$REV" | wc -w | tr -d ' ')
if [[ "$PARENTS" -ne 3 ]]; then
    fail "commit is not a two-parent merge (has $((PARENTS - 1)) parent(s))"
    echo "verify_upstream_merge: $FAILS check(s) failed"
    exit 1
fi
P1=$(git rev-parse "$REV^1")
P2=$(git rev-parse "$REV^2")
if git merge-base --is-ancestor "$P2" "$UPSTREAM_REF"; then
    pass "second parent ${P2:0:12} is upstream history"
else
    fail "second parent ${P2:0:12} is not in $UPSTREAM_REF - only merge upstream/main, never a local branch"
fi

# ---------------------------------------------------------------- 2. message
SUBJ=$(git log -1 --format=%s "$REV")
MERGE_BASE=$(git merge-base "$P1" "$P2")
N=$(git rev-list --count "$MERGE_BASE..$P2")
if [[ "$SUBJ" == "chore: merge upstream ($N commits)" || ( "$N" -eq 1 && "$SUBJ" == "chore: merge upstream (1 commit)" ) ]]; then
    pass "subject is 'chore: merge upstream ($N commits)'"
else
    fail "subject must be exactly 'chore: merge upstream ($N commits)', got: $SUBJ"
fi
MSG=$(git log -1 --format=%B "$REV")
if grep -qE '\bv?[0-9]+\.[0-9]+\.[0-9]+\b' <<< "$MSG"; then
    fail "commit message contains a version number - versions belong in the release commit only"
else
    pass "no version number in the commit message"
fi

# ---------------------------------------------------------------- 3. conflict markers
CHANGED=()
while IFS= read -r path; do
    [[ -n "$path" ]] && CHANGED+=("$path")
done < <(git diff --name-only "$P1" "$REV")
MARKERS=""
if [[ ${#CHANGED[@]} -gt 0 ]]; then
    MARKERS=$(git grep -nIE '^(<<<<<<<|>>>>>>>)( |$)|^\|\|\|\|\|\|\| |^=======$' "$REV" -- "${CHANGED[@]}" 2>/dev/null || true)
fi
if [[ -n "$MARKERS" ]]; then
    fail "conflict markers left in the tree:"
    sed 's/^/        /' <<< "$MARKERS"
else
    pass "no conflict markers in changed files"
fi

# ---------------------------------------------------------------- 4. fork identity survived
# The merge resolves package.py's REPOSITORY, the formula/cask names and the
# README. A clean-looking merge that took upstream's side publishes to incoai.
PKG=$(blob "$REV" dev/tools/package.py)
if grep -qE "^REPOSITORY *= *\"$FORK_REPO\"" <<< "$PKG"; then
    pass "dev/tools/package.py REPOSITORY is $FORK_REPO"
else
    fail "dev/tools/package.py REPOSITORY is not $FORK_REPO - keep the fork's value when resolving the conflict"
fi
if grep -qE '^class Splaude < Formula' <<< "$PKG"; then
    pass "formula class is Splaude"
else
    fail "formula class is not Splaude - the merge took upstream's formula"
fi
REL=$(blob "$REV" dev/tools/release_app.py)
if grep -qE '^TAP *= *"pcvelz/tap"' <<< "$REL" && grep -qE '^FORMULA *= *"splaude"' <<< "$REL"; then
    pass "release_app.py publishes to pcvelz/tap as splaude"
else
    fail "release_app.py TAP/FORMULA are not pcvelz/tap + splaude - the merge would publish to upstream's tap"
fi
README=$(blob "$REV" README.md)
if grep -qi 'fork of' <<< "$README" && grep -q 'brew install pcvelz/tap/splaude' <<< "$README"; then
    pass "README keeps the fork-of line and the pcvelz/tap install command"
else
    fail "README lost the fork-of line or the 'brew install pcvelz/tap/splaude' command - README: keep ours"
fi

# ---------------------------------------------------------------- 5. upstream identity in the merged tree
if IDENT=$(bash dev/tools/check_fork_identity.sh "$REV" 2>&1); then
    pass "no upstream release repo, tap or install command in the merged tree"
else
    fail "upstream identity in the merged tree:"
    sed 's/^/        /' <<< "$IDENT"
fi

# ---------------------------------------------------------------- 6. added files
BODY_TEXT=$(git log -1 --format=%b "$REV")
FOREIGN=""
while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    git cat-file -e "$P2:$path" 2>/dev/null && continue
    grep -qF -- "$path" <<< "$BODY_TEXT" || FOREIGN+="$path"$'\n'
done < <(git diff --diff-filter=A --name-only "$P1" "$REV")
if [[ -n "$FOREIGN" ]]; then
    fail "files added by the merge that did not come from upstream (blanket 'git add'?). Remove them, or name each in the commit body with the reason:"
    printf '%s' "$FOREIGN" | sed 's/^/        /'
else
    pass "every added file came from upstream"
fi

# ---------------------------------------------------------------- 7. hand edits outside conflicts
REMERGE=$(git show --remerge-diff --format= "$REV")
REMERGE_FILES=$(sed -nE 's|^diff --git a/(.*) b/.*$|\1|p' <<< "$REMERGE" | sort -u)
CONFLICTED=$( { sed -nE 's/^remerge CONFLICT .*Merge conflict in (.*)$/\1/p' <<< "$REMERGE"
                sed -nE 's/^remerge CONFLICT \([^)]*\): ([^ ]+) .*$/\1/p' <<< "$REMERGE"; } | sort -u)
UNEXPLAINED=""
while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    grep -qxF -- "$path" <<< "$CONFLICTED" && continue
    grep -qF -- "$path" <<< "$BODY_TEXT" || UNEXPLAINED+="$path"$'\n'
done <<< "$REMERGE_FILES"
if [[ -n "$UNEXPLAINED" ]]; then
    fail "hand edits to files that merged cleanly, not named in the commit body. Name each with its reason, or revert the edit:"
    printf '%s' "$UNEXPLAINED" | sed 's/^/        /'
else
    pass "hand edits are limited to conflicts or files named in the body"
fi

# ---------------------------------------------------------------- 8. harness untouched
HARNESS=$(git diff --name-only "$P1" "$REV" -- \
    dev/tools/verify_upstream_merge.sh dev/tools/check_fork_identity.sh dev/tools/last_release_tag.sh .githooks)
if [[ -n "$HARNESS" ]]; then
    fail "the merge edits the harness that grades it - revert these (git checkout HEAD -- <file>), report the gate problem instead:"
    sed 's/^/        /' <<< "$HARNESS"
else
    pass "merge leaves the fork gate scripts and .githooks untouched"
fi

# ---------------------------------------------------------------- 9. local git config
TAGOPT=$(git config --get remote.upstream.tagOpt || true)
if [[ "$TAGOPT" == "--no-tags" ]]; then
    pass "remote.upstream.tagOpt is --no-tags"
else
    fail "upstream tags are fetched into the local tag namespace - run: git config remote.upstream.tagOpt --no-tags"
fi

echo ""
if [[ $FAILS -gt 0 ]]; then
    echo "verify_upstream_merge: $FAILS check(s) FAILED - fix each FAIL, then re-run. Do not push until this exits 0."
    exit 1
fi
echo "verify_upstream_merge: all checks passed"
exit 0
