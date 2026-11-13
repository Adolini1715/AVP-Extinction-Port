#!/bin/sh
# In a mission: pause, enter the cheat code (R L R R L R L L R on the
# triggers, the sequence GPauseMenu checks against "RLRRLRLLX" at 0x241D08),
# then Options > Cheats: Player Invulnerable, Clear Fog Of War, Enable All
# Levels; then resume. Each cheat closes the Cheats menu back to Options.
# Cheats menu layout (two columns): left Unit Spying, $10000, Invulnerable,
# Clear FOW; right Cheat Win, Show LZ's, Enable All Levels (bottom).
# Usage: cheat.sh <log> <padfile> [extra picks: win]
log=$1; pad=$2; shift 2
press() { echo "$1" >> "$pad"; sleep ${2:-1.3}; }
press start:300 4
for k in R L R R L R L L R; do [ $k = R ] && press rt:250 0.7 || press lt:250 0.7; done
sleep 2
grep -q UNLOCKED "$log" || { echo "cheat entry not confirmed"; exit 1; }
for i in 1 2 3 4; do press down:200; done          # Resume Save Load Bestiary -> Options
press a:250 2.5                                    # Options, focus on Cheats
pick() { press a:250 2.5; for m in "$@"; do press $m:200; done; press a:250 2.5; }
pick down down                                     # Player Invulnerable
pick down down down                                # Clear Fog Of War
pick down down down right                          # Enable All Levels
for x in "$@"; do [ "$x" = win ] && pick right; done   # Cheat Win (top right)
press b:250 2; press b:250 2
grep '\[CHEAT\] menu picked' "$log" | tail -4
