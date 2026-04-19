#!/usr/bin/env bash
# Bump the project version in lockstep across pyproject.toml and webapp/package.json.
# Usage: scripts/bump-version.sh <new-version>    e.g. scripts/bump-version.sh 0.7.1
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <new-version>" >&2
    exit 2
fi

new="$1"
if ! [[ "$new" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "error: '$new' is not a semver string (X.Y.Z[-suffix])" >&2
    exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="$root/pyproject.toml"
npm="$root/webapp/package.json"

for f in "$py" "$npm"; do
    [[ -f "$f" ]] || { echo "error: $f not found" >&2; exit 1; }
done

old=$(grep -E '^version = "' "$py" | head -1 | sed -E 's/^version = "([^"]+)"/\1/')
if [[ -z "$old" ]]; then
    echo "error: couldn't read current version from $py" >&2
    exit 1
fi

sed -i -E 's/^version = "[^"]+"/version = "'"$new"'"/' "$py"
sed -i -E '0,/"version": "[^"]+"/s//"version": "'"$new"'"/' "$npm"

echo "bumped $old -> $new"
echo "  $py"
echo "  $npm"
echo
echo "next:"
echo "  git add pyproject.toml webapp/package.json"
echo "  git commit -m 'chore: bump version to $new'"
echo "  git tag v$new"
