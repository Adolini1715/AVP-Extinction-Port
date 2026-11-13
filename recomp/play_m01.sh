#!/bin/sh
# After nav_m01.sh reaches the mission: exercise the pointer and command code
# with the virtual pad. Usage: play_m01.sh <padfile>
pad=$1
p() { echo "$1" >> "$pad"; sleep ${2:-3}; }
for round in 1 2 3; do
  p lright:800; p a:200; p lup:600; p a:200            # move the pointer, select / order
  p ldown:900; p x:200; p lleft:700; p a:200
  p rright:600; p rup:600; p rleft:600; p rdown:600     # camera
  p y:200; p b:200; p a+lright:600; p rt:400; p lt:400  # other commands, triggers
  p up:200; p down:200; p left:200; p right:200         # d-pad
  p white:200; p black:200; p back:300
done
p start:300 4; p down:200; p up:200; p start:300 4      # pause menu and back
