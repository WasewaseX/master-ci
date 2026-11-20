#!/usr/bin/env bash
# Materialize the diff base ref for the new-code gates: a shallow
# checkout lacks other branches, so a remote-tracking name must become
# a real ref before `git diff` resolves. Usage: fetch_diff_base.sh <ref>
set -euo pipefail

DIFF_BASE="${1:?usage: fetch_diff_base.sh <ref>}"

if git rev-parse --verify --quiet "$DIFF_BASE" >/dev/null; then
  echo "diff base $DIFF_BASE already resolvable"
  exit 0
fi

case "$DIFF_BASE" in
origin/*)
  branch="${DIFF_BASE#origin/}"
  git fetch --depth=1 origin "+refs/heads/$branch:refs/remotes/origin/$branch"
  ;;
refs/*)
  git fetch --depth=1 origin "+$DIFF_BASE:$DIFF_BASE"
  ;;
*)
  git fetch --depth=1 origin "+refs/heads/$DIFF_BASE:refs/remotes/origin/$DIFF_BASE" ||
    git fetch --depth=1 origin "+refs/tags/$DIFF_BASE:refs/tags/$DIFF_BASE" ||
    git fetch --depth=1 origin "$DIFF_BASE"
  ;;
esac
git rev-parse --verify "$DIFF_BASE" >/dev/null
