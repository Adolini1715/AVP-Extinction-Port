#!/bin/sh
# Bridge-only change (no new or removed overrides): recompile the bridge and
# relink, without re-lifting. Use rebuild.sh when overrides change.
set -e
XR=/home/claude/xboxrecomp; OUT=/home/claude/avpe-recomp; P=$OUT/project
cp /home/claude/avpe/recomp/avpe_bridge.c $P/src/avpe_bridge.c
cd $P
INC="-I src -I src/recomp -I src/recomp/gen -I $XR/include $(find $XR/src -type d -printf '-I %p ')"
gcc -std=gnu11 -O1 -g -w -c src/avpe_bridge.c $INC -o $OUT/obj/avpe_bridge.o
gcc -o $OUT/avpe_recomp $OUT/obj/*.o -Wl,--start-group $(find /home/claude/xr-build -name '*.a') -Wl,--end-group \
    $(pkg-config --libs sdl2 epoxy gl libavcodec libavformat libavutil libswscale openssl) -lpthread -lm -ldl -lstdc++ -rdynamic
echo "linked $OUT/avpe_recomp"
