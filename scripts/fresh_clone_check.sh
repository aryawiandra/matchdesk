#!/usr/bin/env bash
# Clone the repo into a new temp directory and run the README quickstart verbatim.
# Usage: scripts/fresh_clone_check.sh <repo-url-or-local-path>
set -euo pipefail
src="${1:?usage: fresh_clone_check.sh <repo-url-or-path>}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
start=$(date +%s)
git clone --quiet "$src" "$work/repo"
cd "$work/repo"
uv sync --locked --python 3.12
uv run --python 3.12 pytest -q -p no:cacheprovider
for f in data/published/*.jsonl; do shasum -a 256 "$f"; done
echo "fresh clone check passed in $(( $(date +%s) - start )) s"
