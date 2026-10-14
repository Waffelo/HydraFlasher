#!/usr/bin/env bash
# Publish wiki/*.md to the GitHub wiki (a separate repo: <repo>.wiki.git).
# The pages in this folder are the source of truth: pages deleted here are
# deleted from the wiki too.
#
# First time only: GitHub creates the wiki repo when its first page is saved
# in the browser. Open the repo's "Wiki" tab, click "Create the first page"
# and save it (any content; this script overwrites it).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
origin="$(git -C "$here" remote get-url origin)"
wiki_url="${WIKI_URL:-${origin%.git}.wiki.git}"  # WIKI_URL: e.g. an SSH URL

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if ! git clone --quiet "$wiki_url" "$tmp/wiki"; then
    echo "Could not clone $wiki_url" >&2
    echo "Create the first wiki page on GitHub once (Wiki tab -> Create the first page), then rerun." >&2
    exit 1
fi

find "$tmp/wiki" -maxdepth 1 -name '*.md' -delete
cp "$here"/*.md "$tmp/wiki/"

cd "$tmp/wiki"
git add -A
if git diff --cached --quiet; then
    echo "Wiki already up to date."
    exit 0
fi
rev="$(git -C "$here" rev-parse --short HEAD)"
git commit --quiet -m "Update wiki from HydraFlasher@$rev"
git push --quiet
echo "Wiki published: ${origin%.git}/wiki"
