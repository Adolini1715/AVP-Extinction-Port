#!/bin/sh
# publish_decomp.sh [clone dir]: copy the decompilation's public part into the
# AVP-Extinction-Decomp clone (default /home/claude/avp-extinction-decomp), ready to commit.
# Public: src/ (minus files whose replacements are all still pending), include/, platform/,
# tools/ (scripts only: no art sheets), verified.json, verified_paths.json, README, NOTICE.
# Private, never copied: notes/, probes/, sweep/ logs, art sheets in tools/.
set -e
D=/home/claude/avpe-decomp; A=/home/claude/avpe; P=${1:-/home/claude/avp-extinction-decomp}
[ -z "$(git -C $D status --porcelain --untracked-files=no)" ] || { echo "$D has uncommitted changes: commit first"; exit 1; }
[ -d "$P/.git" ] || { echo "no clone at $P"; exit 1; }
for d in src include platform tools; do rm -rf "$P/$d"; done
git -C $D archive HEAD src include platform tools verified.json verified_paths.json | tar -x -C "$P"
rm -rf "$P/tools/controls_art" "$P/tools/font_art"/*.png
find "$P/tools" -name '*.png' -delete
for f in "$P"/src/*.cpp; do
  grep -q '^void pending_' "$f" && ! grep -q '^void sub_' "$f" && grep -q '_gen(void)' "$f" && rm -f "$f" && echo "left out (pending): $(basename "$f")"
done
cp $A/release/decomp/README.md $A/release/decomp/NOTICE.md "$P/"
git -C $D rev-parse HEAD > "$P/.source-commit"
echo "ready in $P (source commit $(cut -c1-10 "$P/.source-commit"))"
