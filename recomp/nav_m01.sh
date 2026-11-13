#!/bin/sh
# Drive the native build from boot to Marine mission 1 with the virtual pad,
# on a fresh save directory (no profile): title START, New Profile, Create,
# Continue, Start Campaign, Marine Campaign, Marine 1, briefing START.
# Usage: nav_m01.sh <log> <padfile>   (run alongside avpe_recomp with
#        AVPE_PAD=<padfile> and a fresh AVPE_SAVE_DIR; prints when in mission)
log=$1; pad=$2
waitfor() { while ! grep -q "$1" "$log" 2>/dev/null; do sleep 1; done; }
press() { echo "$1" >> "$pad"; sleep ${2:-4}; }
waitfor "mainmenu.tbd"; sleep 8
press start:300 6
for i in 1 2 3 4 5 6; do press a:300 5; done     # profile x3, campaign, marine, mission 1
waitfor "\[BRIEFING\] mission goals up"; sleep 3
press start:400 3
waitfor "\[BRIEFING\] closed"
echo "in mission"
