#!/usr/bin/env bash
# Re-render the diagrams from their HTML sources.
#
# The PNGs are what a reader sees, and they carry claims — table names, step counts, what the
# grader checks. Without this script the geometry lived in whoever last took the screenshot,
# so a label could go stale and no review would catch it: nobody diffs a PNG. One of them did
# go stale, which is why this exists.
#
# 1560x812 at device scale 2 gives the shipped 3120x1624. The HTML fixes that box in CSS, so
# changing it here alone will crop.
#
# Re-rendering is byte-stable on the same browser, but not across browser versions: the
# committed how-it-works.png predates this script and re-renders a few KB different for no
# visible reason. Render the diagram you actually changed rather than both, so a caption fix
# does not arrive as two modified binaries.
#
# Usage:  docs/render.sh          # both diagrams
#         docs/render.sh architecture
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
WIDTH=1560
HEIGHT=812

if [ ! -x "$CHROME" ]; then
  echo "No Chrome at $CHROME. Set CHROME to a Chromium-based browser binary." >&2
  exit 1
fi

render() {
  local name="$1"
  local src="$HERE/$name.html"
  local out="$HERE/$name.png"
  [ -f "$src" ] || { echo "no such diagram: $src" >&2; return 1; }

  local profile
  profile="$(mktemp -d)"
  rm -f "$out"

  # Chrome does not reliably exit after --screenshot in headless, so it is backgrounded and
  # we wait for the file to stop growing rather than for the process.
  "$CHROME" --headless --disable-gpu --hide-scrollbars \
    --user-data-dir="$profile" \
    --force-device-scale-factor=2 --window-size="$WIDTH,$HEIGHT" \
    --screenshot="$out" "file://$src" >/dev/null 2>&1 &
  local pid=$!

  local last=0 size=0
  for _ in $(seq 1 60); do
    sleep 1
    [ -f "$out" ] || continue
    size="$(wc -c <"$out" | tr -d ' ')"
    [ "$size" -gt 0 ] && [ "$size" -eq "$last" ] && break
    last="$size"
  done

  kill "$pid" >/dev/null 2>&1 || true
  rm -rf "$profile" >/dev/null 2>&1 || true

  [ -s "$out" ] || { echo "render produced nothing for $name" >&2; return 1; }
  echo "$name.png  $(wc -c <"$out" | tr -d ' ') bytes"
}

names=("$@")
if [ "${#names[@]}" -eq 0 ]; then
  names=(architecture how-it-works)
fi
for name in "${names[@]}"; do
  render "$name"
done
