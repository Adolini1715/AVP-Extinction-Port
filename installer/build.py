"""Compile avpe.exe from lifted C, the toolkit and the decompilation (Python port of build_windows.sh).

    build.py --gen DIR --xr DIR --avpe DIR --decomp DIR --sdl DIR --ff DIR --work DIR --out DIR
             [--icon FILE.ico] [--cross x86_64-w64-mingw32-] [--jobs N] [--build-id TEXT]

Runs on Windows with WinLibs GCC (gcc, g++, windres, cmake and ninja on PATH), or on Linux
with MinGW-w64 (--cross) for testing. Incremental: an object is rebuilt when its source, the
shared headers or its flags change. Nothing here reads anything outside the folders given.

  --gen     the lifted C (tools.recomp --gen-dir)
  --xr      the toolkit source (xboxrecomp), built here with CMake into WORK/xr-build
  --avpe    this repository (recomp/avpe_bridge.c, recomp/main_windows.c)
  --decomp  the decompilation (src/*.cpp, platform/*.cpp, include/)
  --sdl     SDL2's MinGW development folder (include/SDL2, lib, bin)
  --ff      the FFmpeg shared build (include, lib, bin)
"""
import argparse
import concurrent.futures as cf
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys

DEC_RE = re.compile(rb'^void (sub|avpe_dec)_[0-9A-Fa-f]{8}\(void\)', re.M)
XR_LIBS = ["xbox_kernel", "xbox_d3d8", "xbox_dsound", "xbox_input", "xbox_usb", "xbox_apu", "xbox_nv2a", "xbox_video"]
LINK_LIBS = ("-lavformat -lavcodec -lswscale -lavutil -lopengl32 -ld3dcompiler -ld3d11 -ldxgi -ldsound -lxinput "
             "-lmfplat -lmfreadwrite -lmfuuid -lmf -lws2_32 -lwinmm -lole32 -loleaut32 -luuid -lsetupapi -limm32 "
             "-lversion -lbcrypt -ladvapi32 -luser32 -lgdi32 -lshell32 -lkernel32").split()


def sha1_bytes(*parts):
    h = hashlib.sha1()
    for p in parts:
        h.update(p if isinstance(p, bytes) else p.encode())
    return h.hexdigest()


def file_sha1(path):
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def run(cmd, cwd=None, quiet=False):
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    if r.returncode:
        sys.stdout.write(r.stdout[-6000:])
        raise SystemExit(f"build: {os.path.basename(cmd[0])} failed (exit {r.returncode})")
    if not quiet and r.stdout.strip():
        sys.stdout.write(r.stdout)


def build_toolkit(xr, work, cross, jobs):
    bdir = os.path.join(work, "xr-build")
    libs = {n: None for n in XR_LIBS}
    if not os.path.isfile(os.path.join(bdir, "CMakeCache.txt")):
        cfg = ["cmake", "-S", xr, "-B", bdir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release"]
        if cross:
            cfg.append("-DCMAKE_TOOLCHAIN_FILE=" + os.path.join(xr, "cmake", "mingw-w64-x86_64.cmake"))
        else:
            cfg += ["-DCMAKE_C_COMPILER=gcc", "-DCMAKE_CXX_COMPILER=g++"]
        run(cfg, quiet=True)
    print("[build] toolkit libraries", flush=True)
    run(["cmake", "--build", bdir, "-j", str(jobs), "--target"] + XR_LIBS, quiet=True)
    for n in XR_LIBS:
        hits = glob.glob(os.path.join(bdir, "**", "lib" + n + ".a"), recursive=True)
        if not hits:
            raise SystemExit(f"build: toolkit library {n} missing after the build")
        libs[n] = hits[0]
    return list(libs.values())


def main():
    ap = argparse.ArgumentParser()
    for k in ("gen", "xr", "avpe", "decomp", "sdl", "ff", "work", "out"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--icon")
    ap.add_argument("--cross", default="")
    ap.add_argument("--jobs", type=int, default=max(1, os.cpu_count() or 2))
    ap.add_argument("--build-id", default="installer")
    a = ap.parse_args()
    for k in ("gen", "xr", "avpe", "decomp", "sdl", "ff", "work", "out"):
        setattr(a, k, os.path.abspath(getattr(a, k)))
    cc, cxx, windres = a.cross + "gcc", a.cross + "g++", a.cross + "windres"
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)
    obj = os.path.join(a.work, "obj")
    sums = os.path.join(obj, "sums")
    os.makedirs(sums, exist_ok=True)

    xr_libs = build_toolkit(a.xr, a.work, a.cross, a.jobs)

    # the project folder the generated C expects: src/recomp/gen beside the bridge
    proj = os.path.join(a.work, "project", "src")
    os.makedirs(os.path.join(proj, "recomp"), exist_ok=True)
    gen_link = os.path.join(proj, "recomp", "gen")
    if os.path.abspath(a.gen) != gen_link:
        if os.path.isdir(gen_link):
            shutil.rmtree(gen_link)
        shutil.copytree(a.gen, gen_link)
    shutil.copyfile(os.path.join(a.avpe, "recomp", "avpe_bridge.c"), os.path.join(proj, "avpe_bridge.c"))
    shutil.copyfile(os.path.join(a.xr, "templates", "new-game", "src", "recomp_manual.c"), os.path.join(proj, "recomp_manual.c"))

    xr_inc = ["-I", os.path.join(a.xr, "include")]
    for root, dirs, _ in os.walk(os.path.join(a.xr, "src")):
        xr_inc += ["-I", root]
    inc = ["-I", proj, "-I", os.path.join(proj, "recomp"), "-I", gen_link] + xr_inc + \
          ["-I", os.path.join(a.avpe, "recomp")]        # what avpe_bridge.c includes (avpe_replay.c, abi_pops.inc, noreturn.inc)
    dinc = ["-I", os.path.join(a.decomp, "include"), "-I", os.path.join(a.decomp, "platform"),
            "-I", os.path.join(a.decomp, "platform", "glad", "include"),
            "-I", os.path.join(a.sdl, "include", "SDL2"), "-I", os.path.join(a.ff, "include")]

    hdrs = b""
    for h in sorted(glob.glob(os.path.join(a.decomp, "include", "avpe", "*.h")) +
                    glob.glob(os.path.join(a.decomp, "platform", "*.h"))):
        hdrs += open(h, "rb").read()
    hsum = sha1_bytes(hdrs)

    jobs = []   # (src, obj name, cmd)
    for c in sorted(glob.glob(os.path.join(gen_link, "*.c"))):
        b = os.path.splitext(os.path.basename(c))[0]
        jobs.append((c, b, [cc, "-std=gnu11", "-O1", "-w", "-c", c] + inc))
    for c, b in ((os.path.join(proj, "recomp_manual.c"), "recomp_manual"),
                 (os.path.join(proj, "avpe_bridge.c"), "avpe_bridge")):
        jobs.append((c, b, [cc, "-std=gnu11", "-O1", "-w", "-include", "stddef.h", "-c", c] + inc))
    mw = os.path.join(a.avpe, "recomp", "main_windows.c")
    jobs.append((mw, "main_windows", [cc, "-std=gnu11", "-O1", "-w", f'-DAVPE_BUILD_ID="{a.build_id}"', "-c", mw] + inc))
    dsrc = sorted(glob.glob(os.path.join(a.decomp, "src", "*.cpp")))
    for f in dsrc + sorted(glob.glob(os.path.join(a.decomp, "platform", "*.cpp"))):
        if f in dsrc and not DEC_RE.search(open(f, "rb").read()):
            continue                          # a src file joins once it defines a replacement
        b = "decomp_" + os.path.splitext(os.path.basename(f))[0]
        jobs.append((f, b, [cxx, "-std=gnu++17", "-O1", "-w", "-fno-strict-aliasing", "-c", f] + dinc + inc))
    gl = os.path.join(a.decomp, "platform", "glad", "src", "gl.c")
    jobs.append((gl, "glad_gl", [cc, "-O1", "-c", gl, "-I", os.path.join(a.decomp, "platform", "glad", "include")]))

    names = {b for _, b, _ in jobs}
    for o in glob.glob(os.path.join(obj, "*.o")):     # objects of sources that left the build
        b = os.path.splitext(os.path.basename(o))[0]
        if b not in names:
            os.remove(o)
            if os.path.exists(os.path.join(sums, b)):
                os.remove(os.path.join(sums, b))

    todo = []
    for src, b, cmd in jobs:
        s = sha1_bytes(open(src, "rb").read(), hsum, " ".join(cmd))
        o = os.path.join(obj, b + ".o")
        sf = os.path.join(sums, b)
        if not (os.path.isfile(o) and os.path.isfile(sf) and open(sf).read() == s):
            todo.append((b, cmd + ["-o", o], s, sf))
    print(f"[build] compiling {len(todo)} of {len(jobs)} files", flush=True)

    def one(t):
        b, cmd, s, sf = t
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        if r.returncode == 0:
            with open(sf, "w") as f:
                f.write(s)
        return b, r.returncode, r.stdout

    failed = []
    done = 0
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        for b, rc, out in ex.map(one, todo):
            done += 1
            if rc:
                failed.append((b, out))
            if done % 25 == 0 or done == len(todo):
                print(f"[build] {done}/{len(todo)}", flush=True)
    if failed:
        for b, out in failed[:3]:
            print(f"--- {b}\n{out[-3000:]}")
        raise SystemExit(f"build: {len(failed)} file(s) failed to compile")

    objs = [os.path.join(obj, b + ".o") for _, b, _ in jobs]
    if a.icon:
        rc = os.path.join(a.work, "avpe_icon.rc")
        with open(rc, "w") as f:
            f.write('1 ICON "%s"\n' % os.path.abspath(a.icon).replace("\\", "/"))
        res = os.path.join(a.work, "avpe_res.o")
        run([windres, rc, "-O", "coff", "-o", res])
        objs.append(res)
    exe = os.path.join(a.out, "avpe.exe")
    print("[build] linking avpe.exe", flush=True)
    # the object list goes in a response file: Windows caps a command line at 32,767 characters
    rsp = os.path.join(a.work, "link_objs.rsp")
    with open(rsp, "w") as f:
        for o in objs:
            f.write('"%s"\n' % os.path.abspath(o).replace("\\", "/"))
    run([cxx, "-o", exe, "@" + rsp] + ["-Wl,--start-group"] + xr_libs + ["-Wl,--end-group",
        "-L" + os.path.join(a.sdl, "lib"), "-lSDL2", "-L" + os.path.join(a.ff, "lib")] + LINK_LIBS +
        ["-static-libgcc", "-static-libstdc++", "-Wl,-Bstatic", "-lwinpthread", "-Wl,-Bdynamic", "-mwindows", "-s"])
    for d in glob.glob(os.path.join(a.sdl, "bin", "SDL2.dll")) + glob.glob(os.path.join(a.ff, "bin", "*.dll")):
        shutil.copyfile(d, os.path.join(a.out, os.path.basename(d)))
    print("[build] built " + exe, flush=True)


if __name__ == "__main__":
    main()
