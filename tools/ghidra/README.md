# Ghidra Function ID pass (2026-10-27)

Names Microsoft C runtime functions in default.xbe by Ghidra's Function ID (vsOlder_x86.fidbf).
Ghidra 12.1.4 in /home/claude/tools (downloaded on the author's PC, the workspace cannot reach GitHub releases).

    python3 ../decompiler/mkelf.py                     # writes /home/claude/decompiler/avpe.elf
    # starts.txt: every function start the lifter knows (functions.json), one per line
    ghidra_12.1.4_PUBLIC/support/analyzeHeadless gproj avpe2 -import /home/claude/decompiler/avpe.elf \
        -overwrite -scriptPath <this dir> -preScript SeedStarts.java -postScript DumpNames.java

Seeding the starts matters: without them Ghidra finds 3,395 functions and names 65 runtime ones;
with them 10,553 and 138. Result: symbols/ghidra_fid.tsv (library functions only).
Function ID knows the desktop CRT, not XAPI: XAPI names come from XbSymbolDatabase and kernel-call fingerprints.
