#!/bin/sh
# Rebuild the native Linux test build of AvP: Extinction from scratch.
# Needs: /home/claude/avpe (this repo, with disc/ extracted), xboxrecomp at v0.13.1
# (193e299) plus recomp/0001-*.patch, and the apt packages listed in
# xboxrecomp/tools/linux/install_deps.sh plus xvfb.
set -e
AVPE=/home/claude/avpe; XR=/home/claude/xboxrecomp; OUT=/home/claude/avpe-recomp
mkdir -p $OUT/game_files $OUT/obj && cp $AVPE/disc/default.xbe $OUT/game_files/
cd $XR
python3 -m tools.xbe_parser $OUT/game_files/default.xbe --json $OUT/game_files/avpe_analysis.json >/dev/null
python3 -m tools.disasm $OUT/game_files/default.xbe --text-only >/dev/null
python3 -m tools.func_id $OUT/game_files/default.xbe >/dev/null
python3 -m tools.abi_analysis $OUT/game_files/default.xbe >/dev/null
rm -f tools/disasm/output/functions.json.orig
python3 -I $AVPE/tools/apply_names.py $AVPE/symbols/xbox_symbols.csv tools/disasm/output/functions.json
[ -d $OUT/project ] || cp -r templates/new-game $OUT/project
cp $AVPE/recomp/main_linux.c $OUT/project/src/
python3 -m tools.recomp $OUT/game_files/default.xbe --all --split 250 --gen-dir $OUT/project/src/recomp/gen
cmake -S . -B /home/claude/xr-build -DCMAKE_BUILD_TYPE=Release >/dev/null && cmake --build /home/claude/xr-build -j2
cd $OUT/project
INC="-I src -I src/recomp -I src/recomp/gen -I $XR/include $(find $XR/src -type d -printf '-I %p ')"
ls src/recomp/gen/*.c | xargs -P2 -I{} sh -c "gcc -std=gnu11 -O1 -w -c {} $INC -o $OUT/obj/\$(basename {} .c).o"
gcc -std=gnu11 -O1 -g -w -c src/main_linux.c $INC -o $OUT/obj/main_linux.o
gcc -std=gnu11 -O1 -w -include stddef.h -c src/recomp_manual.c $INC -o $OUT/obj/recomp_manual.o
gcc -o $OUT/avpe_recomp $OUT/obj/*.o -Wl,--start-group $(find /home/claude/xr-build -name '*.a') -Wl,--end-group \
    $(pkg-config --libs sdl2 epoxy gl libavcodec libavformat libavutil libswscale openssl) -lpthread -lm -ldl -rdynamic
ln -sfn $AVPE/disc $OUT/game
echo "built $OUT/avpe_recomp; run: cd $OUT && DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1 ./avpe_recomp"
