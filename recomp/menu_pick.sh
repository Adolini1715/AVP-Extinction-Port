#!/bin/sh
# Move menu focus to the item whose text is <text> and press A, using the
# [MENU] focus trace. Tries down presses, then up, then right/left.
# Usage: menu_pick.sh <log> <padfile> "<text>" [max presses per direction]
log=$1; pad=$2; want=$3; max=${4:-12}
focus() { grep '\[MENU\] focus' "$log" | tail -1 | sed 's/.*item=[0-9A-F]* "\(.*\)" (frame.*/\1/'; }
press() { echo "$1" >> "$pad"; sleep ${2:-1.2}; }
for dir in down up right left; do
  i=0
  while [ "$(focus)" != "$want" ] && [ $i -lt $max ]; do
    before=$(grep -c '\[MENU\] focus' "$log"); press $dir:200
    [ $(grep -c '\[MENU\] focus' "$log") -eq $before ] && break   # edge of the menu
    i=$((i+1))
  done
  [ "$(focus)" = "$want" ] && { press a:250 2.5; echo "picked '$want'"; exit 0; }
done
echo "not found: '$want' (focus '$(focus)')"; exit 1
