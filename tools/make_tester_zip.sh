#!/bin/sh
# make_tester_zip.sh [out.zip]: the tester package. Install.bat plus everything it would
# otherwise fetch from GitHub (the recompiler toolkit, the decompilation at its current
# commit, the FFmpeg build), so the repositories can stay private. Only the build tools
# (GCC, MinGit, SDL2, Python packages) are downloaded by the installer, all SHA-256 pinned.
# Contains no game data: the player's own disc is translated on their PC.
# Left out: recomp/avpe.ico (box art), decompiled files whose functions are still pending
# (not yet through the replay gates), the decompilation's notes, probes and sweep logs.
set -e
# make_tester_zip.sh --tree DIR: only lay the package out in DIR (no FFmpeg zip, no READ ME FIRST),
# the content of the public repository.
if [ "$1" = "--tree" ]; then tree=$2; out=""; else tree=""; out=${1:-/home/claude/rel/AvP-Extinction-PC-tester.zip}; fi
A=/home/claude/avpe; D=/home/claude/avpe-decomp; X=/home/claude/xboxrecomp
# the decompilation is taken from a named branch, not from whatever is checked out: rc2 and rc3
# went out with the development branch (unverified heap code on) because the build tree was on it
DREF=${AVPE_DECOMP_REF:-release-v0.1}
git -C $D rev-parse --verify -q "$DREF" > /dev/null || { echo "no branch $DREF in $D"; exit 1; }
echo "decompilation from $DREF ($(git -C $D rev-parse --short "$DREF"))"
FF=/home/claude/rel/avpe-ffmpeg-n7.1-win64.zip
[ -s "$FF" ] || { echo "missing $FF"; exit 1; }
for r in $A $D $X; do
  [ -z "$(git -C $r status --porcelain --untracked-files=no)" ] || { echo "$r has uncommitted changes: commit first"; exit 1; }
done
if [ -n "$tree" ]; then top=$tree; rm -rf "$top"; else st=$(mktemp -d); top="$st/AvPE-PC"; fi
mkdir -p "$top"
git -C $A archive HEAD | tar -x -C "$top"
rm -f "$top/recomp/avpe.ico"
rm -rf "$top/stats"                      # unit stats decoded from the game's data files
rm -rf "$top/tools/replay/fe_seed1"      # a save disc for the front-end tests: it holds the game's own title and save pictures
mkdir -p "$top/avpe-decomp"
git -C $D archive "$DREF" src platform include README.md | tar -x -C "$top/avpe-decomp"
git -C $D rev-parse "$DREF" > "$top/avpe-decomp/REVISION"
# a file whose replacements are all still pending is not part of the game yet
for f in "$top"/avpe-decomp/src/*.cpp; do
  grep -q '^void pending_' "$f" && ! grep -q '^void sub_' "$f" && grep -q '_gen(void)' "$f" && rm -f "$f" && echo "left out (pending): $(basename "$f")"
done
mkdir -p "$top/xboxrecomp"
git -C $X archive HEAD | tar -x -C "$top/xboxrecomp"
rm -rf "$top/release"
if [ -n "$tree" ]; then                 # the public repository: its README, licence, notice and security note
  rm -rf "$top/avpe-decomp"              # the installer fetches it from AVP-Extinction-Decomp at a pinned commit
  cp $A/release/README.md $A/release/LICENSE $A/release/NOTICE.md $A/release/SECURITY.md "$top/"
  echo "v$(cat $A/release/VERSION)" > "$top/VERSION.txt"
  echo "tree in $top"; exit 0
fi
cp "$FF" "$top/"
# the package's version, which the launcher shows when an update is dropped on it
echo "$(date -u +%Y-%m-%d) port $(git -C $A rev-parse --short HEAD) decomp $(git -C $D rev-parse --short "$DREF")" > "$top/VERSION.txt"
# "Source code": the two repositories as they are published, for testers who want to read them
# (the installer does not use this folder; it builds from avpe-decomp/ and xboxrecomp/ above)
src="$top/Source code"; mkdir -p "$src/AVP-Extinction-PC" "$src/AVP-Extinction-Decomp"
sh "$A/tools/make_tester_zip.sh" --tree "$src/AVP-Extinction-PC" > /dev/null
rm -rf "$src/AVP-Extinction-PC/PUT YOUR GAME HERE"      # (one place for the game: the top folder's)
P="$src/AVP-Extinction-Decomp"
git -C $D archive "$DREF" src include platform tools verified.json verified_paths.json | tar -x -C "$P"
rm -rf "$P/tools/controls_art" "$P/tools/font_art"/*.png
find "$P/tools" -name '*.png' -delete
for f in "$P"/src/*.cpp; do
  grep -q '^void pending_' "$f" && ! grep -q '^void sub_' "$f" && grep -q '_gen(void)' "$f" && rm -f "$f"
done
cp $A/release/decomp/README.md $A/release/decomp/NOTICE.md "$P/"
git -C $D rev-parse "$DREF" > "$P/.source-commit"
cat > "$top/READ ME FIRST.txt" <<'EOF'
AvP: Extinction PC - tester package
===================================
This builds the PC version of Aliens versus Predator: Extinction from your own
Xbox copy of the game. Nothing from the game is in this package.

You need: Windows 10 or 11 (64-bit), an internet connection for the first run
(about 300 MB of build tools), about 6 GB of free space, and the USA Xbox
release of the game as a disc image (.iso or .xiso) or an extracted disc folder.
The Europe and PlayStation 2 releases do not work; if the installer turns your
copy down it lists the file and why, with its default.xbe checksum.

1. Unzip this whole folder somewhere (not inside Program Files).
2. Put your disc image or disc folder in "PUT YOUR GAME HERE".
   The installer reads only that folder (and its subfolders); it never
   searches your PC.
3. Double-click Install.bat. The first run takes 20 to 40 minutes; when it is
   done the launcher opens. Press Play.
4. Next time, start the game with "Play AvP Extinction" in this folder.
Everything stays in this one folder: the build tools, the game and your saves.
Nothing is installed on Windows and nothing goes on the desktop. To remove it,
delete the folder.

The game's README.txt (in the "AvP Extinction PC" folder inside this one) has the controls
and a short note for testers: how to mark a problem (Ctrl+F12) and send a
session ("Pack session.bat"). Session recordings stay in that folder and hold
no user name, PC name or paths; nothing is sent anywhere unless you send it.
If the installer stops, send install-log.txt from this folder.

Updates: unzip the new download, open the game's launcher and drop the
unzipped folder on it. Your game files, saves and settings stay; the game is
rebuilt from the update's source in a few minutes.

The "Source code" folder has the two repositories as they stand for this
build (the PC port and the decompilation), to read; the installer does not
use it.
EOF
rm -f "$out"
(cd "$st" && zip -qr -9 "$out" "AvPE-PC")
rm -rf "$st"
ls -l "$out"
