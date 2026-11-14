"""AvP: Extinction PC: build the game from your own Xbox disc, on Windows.

Install.bat runs this with a private copy of Python (installer/bootstrap.ps1).
Every download is pinned by SHA-256 (or by git commit) and checked before it is
used. Nothing from the game is downloaded: the recompiler reads your default.xbe
and the game reads your disc files.

Steps (each is skipped when a previous run finished it):
  1. find your copy of the game: an Xbox disc image (.iso) or an extracted disc folder,
     in "PUT YOUR GAME HERE" and its subfolders, or an image next to Install.bat
     (nothing else on the PC is searched)
  2. download the build tools (GCC with CMake and Ninja, MinGit, SDL2, FFmpeg, capstone)
  3. fetch the recompiler toolkit and the decompilation (a tester package carries both)
  4. recompile default.xbe into C (the long step)
  5. compile avpe.exe
  6. put the game in "AvP Extinction PC" inside this folder, add a "Play AvP Extinction"
     shortcut here and open the launcher. Everything stays in this one folder.

Updates (install.py --update-from FOLDER, what the launcher runs when a newer package's
folder, or its zip, is dropped on it): the new package's files are copied over this
folder's (the game files, the build tools, saves, settings and session recordings
stay), then the steps above run again: no downloads, the game rebuilt from source.
If the rebuild fails the game already installed is left as it was.

Privacy: only "PUT YOUR GAME HERE" (and images next to Install.bat) is read; the user name, PC name and home folder are
taken out of everything shown and logged.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import xdvdfs  # noqa: E402

GAME_IN = os.path.join(REPO, "PUT YOUR GAME HERE")
OUT = os.path.join(REPO, "AvP Extinction PC")     # inside this folder: nothing is written outside it

XBE_RUN = "7c052c5abad0469ee2a7192ec7b0f59bbc9914a5"          # USA retail, as the port runs it
XBE_DISC = "fcd7aa1964c6180883a756513bb89868321832ed"         # the same as on the disc
XBE_SHA1S = {XBE_RUN, XBE_DISC}
# The disc's default.xbe and the one the port is built from differ in one byte: at file offset
# 0x132A29 (VA 0x142A29, XAPI start-up, after the media check at 0x1427F1) the disc has jge
# (7D), a hard-disk copy jmp (EB), so the game starts without the disc in a DVD drive.
MEDIA_PATCH = (0x132A29, 0x7D, 0xEB)


def normalize_xbe(path):
    """turn the disc's default.xbe into the one the port is built from (the media-check byte)"""
    if sha1(path) != XBE_DISC:
        return
    off, old, new = MEDIA_PATCH
    with open(path, "r+b") as f:
        f.seek(off)
        if f.read(1) != bytes([old]):
            return
        f.seek(off)
        f.write(bytes([new]))
    if sha1(path) == XBE_RUN:
        say("  (the disc's media check set to the hard-disk form the port is built from)")
SKIP_FILES = {"cerbera.tvr"}                                     # 200 MB of disc padding
URL = os.environ.get("AVPE_REPO_URL", "https://github.com/Adolini1715/AVP-Extinction-Port")
DECOMP_URL = os.environ.get("AVPE_DECOMP_URL", "https://github.com/Adolini1715/AVP-Extinction-Decomp")
XR_REV = os.environ.get("AVPE_XR_REV", "6874c242e6d7c1ddfc50e01b3598b40bda3518d9")          # toolkit-fork
DECOMP_REV = os.environ.get("AVPE_DECOMP_REV", "e899f70018f8d0f5b40bb7042fa60ee02111bc07")  # AVP-Extinction-Decomp main
_rev = os.path.join(REPO, "avpe-decomp", "REVISION")              # an offline copy records its commit
if os.path.isfile(_rev) and "AVPE_DECOMP_REV" not in os.environ:
    DECOMP_REV = open(_rev).read().strip()
SDL_VER = "2.30.9"

DOWNLOADS = {
    # name: (url, sha256, size)
    "gcc": ("https://github.com/brechtsanders/winlibs_mingw/releases/download/13.2.0posix-17.0.6-11.0.1-msvcrt-r5/"
            "winlibs-x86_64-posix-seh-gcc-13.2.0-mingw-w64msvcrt-11.0.1-r5.zip",
            "675f79b6fba8d3c8165bd9b5fb8e96ab73370dd8611f13e520cb185357d3dafc", 222828150),
    "git": ("https://github.com/git-for-windows/git/releases/download/v2.47.0.windows.2/MinGit-2.47.0.2-64-bit.zip",
            "c4a5d3a2adda98b25fe59349733fca56b3843360b962dba5535282a9d8120b31", 47222137),
    "sdl2": (f"https://github.com/libsdl-org/SDL/releases/download/release-{SDL_VER}/SDL2-devel-{SDL_VER}-mingw.tar.gz",
             "b188d165dc4a0372042bff3d0c18e9bb90772581e92a7b8b47b521b47714f75c", None),
    "capstone": ("https://files.pythonhosted.org/packages/py3/c/capstone/capstone-5.0.9-py3-none-win_amd64.whl",
                 "732cedbbb56d42e723f14d7af6387f1454194a820b4b96b56d1e53f865ef85d0", None),
    # the launcher's picture and the icon (launcher_art.py), made from the player's own title screen
    "pillow": ("https://files.pythonhosted.org/packages/57/97/73f756c338c1d86bb802ee88c3cab015ad7ce4b838f8a24f16b676b1ac7c/"
               "pillow-11.0.0-cp312-cp312-win_amd64.whl",
               "8ec4a89295cd6cd4d1058a5e6aec6bf51e0eaaf9714774e1bfac7cfc9051db47", 2567533),
    "numpy": ("https://files.pythonhosted.org/packages/a6/84/fa11dad3404b7634aaab50733581ce11e5350383311ea7a7010f464c0170/"
              "numpy-2.1.3-cp312-cp312-win_amd64.whl",
              "0d30c543f02e84e92c4b1f415b7c6b5326cbe45ee7882b6b77db7195fb971e3a", 12566858),
    # FFmpeg n7.1, LGPL shared build (movies): published with this port's release
    "ffmpeg": (os.environ.get("AVPE_FFMPEG_URL", URL + "/releases/download/ffmpeg-n7.1/avpe-ffmpeg-n7.1-win64.zip"),
               os.environ.get("AVPE_FFMPEG_SHA256", "efb6f667d32f58ce33edf25eac89a7f8dfc18889c9afd8b8f4648a183bc28a52"), None),
}

LOG = None


def _private():
    """the player's home folder, user name and PC name: never written to the log or the screen"""
    out = []
    home = os.path.expanduser("~")
    if home and home not in ("~", "/", "\\"):
        out.append((home, "<home>"))
        out.append((home.replace("\\", "/"), "<home>"))
    for var, tag in (("USERNAME", "<user>"), ("USER", "<user>"), ("COMPUTERNAME", "<pc>")):
        v = os.environ.get(var, "")
        if len(v) >= 3:
            out.append((v, tag))
    return out


PRIVATE = _private()


def scrub(text):
    import re
    for v, tag in PRIVATE:
        text = re.sub(re.escape(v), tag, text, flags=re.I)
    return text


def rmtree(path):
    """remove a folder, read-only files included (a git checkout's objects are read-only on
    Windows, and shutil.rmtree(ignore_errors=True) silently leaves them: the copy that follows
    then failed with 'file already exists' when a package was dropped on a GitHub install)"""
    def clear(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    if os.path.lexists(path):
        shutil.rmtree(path, onerror=clear)
    if os.path.lexists(path):
        fail(f"could not remove {os.path.basename(path)} in the build folder. Close anything using it and run again.")


def say(msg=""):
    msg = scrub(msg)
    print(msg, flush=True)
    if LOG:
        LOG.write(msg + "\n")
        LOG.flush()


def fail(msg):
    say("")
    say("PROBLEM: " + msg)
    say("Nothing was changed outside this folder and the game folder. Fix the problem and run")
    say("Install.bat again; it carries on where it stopped. The full log is install-log.txt.")
    sys.exit(1)


def long_path(p):
    p = os.path.abspath(p)
    return "\\\\?\\" + p if os.name == "nt" and not p.startswith("\\\\?\\") else p


def run(cmd, cwd=None, env=None, quiet=False, show=None):
    if LOG:
        LOG.write(scrub("  > " + " ".join(cmd)) + "\n")
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace")
    tail = []
    for line in p.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-40:]
        if LOG:
            LOG.write(scrub(line) + "\n")
        if not quiet and (show is None or show(line)):
            print("    " + scrub(line)[:150], flush=True)
    if p.wait():
        print("    (last lines of its output:)")
        for line in tail:
            print("    " + scrub(line)[:150])
        fail(f"{os.path.basename(cmd[0])} stopped with exit code {p.returncode}.")
    if LOG:
        LOG.flush()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(name, dl_dir):
    url, want, size = DOWNLOADS[name]
    dst = os.path.join(dl_dir, os.path.basename(url))
    local = os.path.join(REPO, os.path.basename(url))        # an offline copy next to Install.bat wins
    if os.path.isfile(local) and sha256(local) == want:
        return local
    if os.path.isfile(dst) and sha256(dst) == want:
        return dst
    say(f"  downloading {os.path.basename(url)}" + (f" ({size // 1000000} MB)" if size else ""))
    tmp = dst + ".part"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got, last = 0, 0
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
                    got += len(b)
                    if total and time.time() - last > 2:
                        last = time.time()
                        print(f"    {got * 100 // total}%", end="\r", flush=True)
            print("         ", end="\r", flush=True)
            break
        except OSError as e:
            say(f"    download failed ({e}), retrying")
            time.sleep(3)
    else:
        fail(f"could not download {url}. Check the internet connection.")
    got = sha256(tmp)
    if got != want:
        os.remove(tmp)
        fail(f"{os.path.basename(url)} does not match its checksum (got {got}). "
             "It was deleted; run Install.bat again to download it again.")
    os.replace(tmp, dst)
    return dst


def unzip(path, dst):
    with zipfile.ZipFile(path) as z:
        for m in z.infolist():
            target = os.path.join(dst, *m.filename.split("/"))
            if not os.path.abspath(target).startswith(os.path.abspath(dst) + os.sep):
                fail(f"{path} has an entry outside its folder: {m.filename}")
            if m.is_dir():
                os.makedirs(long_path(target), exist_ok=True)
                continue
            os.makedirs(long_path(os.path.dirname(target)), exist_ok=True)
            with z.open(m) as src, open(long_path(target), "wb") as out:
                shutil.copyfileobj(src, out)


def cp(src, dst):
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    with open(src, "rb") as f, open(dst, "wb") as g:
        shutil.copyfileobj(f, g, 1 << 20)
    shutil.copystat(src, dst)
    return dst


# ---- finding the game -------------------------------------------------------------------

def disc_kind(path):
    """'xbox' for an Xbox disc image (XDVDFS, trimmed or a full dump), 'ps2' for a PlayStation 2
    image (ISO 9660 with SYSTEM.CNF; AvP: Extinction also came out there), else 'other'."""
    try:
        with open(path, "rb") as f:
            for base in (0, 0x18300000, 0x30600000, 0x2080000, 0xFD90000):
                f.seek(base + 32 * 2048)
                if f.read(20) == xdvdfs.MAGIC:
                    return "xbox"
            for off in (16 * 2048, 16 * 2352 + 16, 16 * 2352 + 24):   # .iso, and raw .bin sectors
                f.seek(off)
                if f.read(6)[1:6] == b"CD001":
                    f.seek(0)
                    head = f.read(8 << 20)
                    if b"SYSTEM.CNF" in head or b"BOOT2" in head:
                        return "ps2"
    except OSError:
        pass
    return "other"


def image_xbe_sha1(path):
    """sha1 of default.xbe inside an Xbox image, read in place (nothing extracted)."""
    try:
        f, base, files = xdvdfs.listing(path)
    except SystemExit:
        return None
    try:
        for p, s, sz in files:
            if p.lower() == "/default.xbe":
                f.seek(base + s * 2048)
                return hashlib.sha1(f.read(sz)).hexdigest()
    finally:
        f.close()
    return None


def find_xbe(folder, depth=3):
    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.lower() == "default.xbe":
                return os.path.join(root, f)
        if root[len(folder):].count(os.sep) >= depth:
            dirs[:] = []
    return None


def search_places():
    """only "PUT YOUR GAME HERE", as in the Concrete Jungle installer: nothing else on the PC is read"""
    return [GAME_IN] if os.path.isdir(GAME_IN) else []


def describe_xbe(h):
    return {XBE_RUN: "USA retail (hard-disk form)", XBE_DISC: "USA retail"}.get(h, "not the USA retail release")


def candidates(depth=3):
    """(kind, path): Xbox images and extracted folders that hold this game, nearest first; also the
    files that looked like the game but were turned down, with the reason (shown if none fit)."""
    found, ps2, rejected, seen = [], [], [], set()
    skip_dirs = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
                 "system volume information", "appdata", os.path.basename(OUT).lower(), "_build"}

    def look(root, files):
        for f in files:
            p = os.path.join(root, f)
            k = os.path.normcase(os.path.abspath(p))
            if k in seen:
                continue
            seen.add(k)
            low = f.lower()
            try:
                if low.endswith((".iso", ".xiso")):
                    kind = disc_kind(p)
                    if kind == "xbox":
                        h = image_xbe_sha1(p)
                        if h in XBE_SHA1S:
                            found.append(("image", p))
                        elif h is None:
                            rejected.append((p, "an Xbox image, but it has no default.xbe (or it is incomplete)", None))
                        else:
                            rejected.append((p, "an Xbox image, but its default.xbe is not the USA retail release", h))
                    elif kind == "ps2":
                        ps2.append(p)
                    else:
                        size = os.path.getsize(p)
                        shown = f"{size / 2**30:.2f} GB" if size >= 1 << 30 else f"{size >> 20} MB"
                        rejected.append((p, f"not an Xbox disc image this installer can read ({shown}, "
                                            "no Xbox file system found); the download may be incomplete", None))
                elif low.endswith(".bin") and os.path.getsize(p) > 100 << 20 and disc_kind(p) == "ps2":
                    ps2.append(p)
                elif low == "default.xbe":
                    h = sha1(p)
                    if h in XBE_SHA1S and os.path.isdir(os.path.join(root, "tbd")):
                        found.append(("folder", root))
                    elif h in XBE_SHA1S:
                        rejected.append((p, "the USA default.xbe, but its folder has no tbd folder next to it", h))
                    else:
                        rejected.append((p, "a default.xbe that is not the USA retail release", h))
            except OSError as e:
                rejected.append((p, f"could not be read ({e.strerror or e})", None))

    for place in search_places():
        for root, dirs, files in os.walk(place):
            dirs[:] = [d for d in dirs if d.lower() not in skip_dirs and not d.startswith(".")]
            if root[len(place):].count(os.sep) >= depth:
                dirs[:] = []
            look(root, files)
    # an image left next to Install.bat (only that folder's own files, no subfolders)
    look(REPO, [f for f in os.listdir(REPO) if f.lower().endswith((".iso", ".xiso"))
                and os.path.isfile(os.path.join(REPO, f))])
    return found, ps2, rejected


def extract_image(iso, dest):
    f, base, files = xdvdfs.listing(iso)
    total = sum(sz for p, s, sz in files if os.path.basename(p).lower() not in SKIP_FILES)
    done, last = 0, 0
    try:
        for p, s, sz in files:
            rel = p.lstrip("/")
            if os.path.basename(rel).lower() in SKIP_FILES:
                continue
            parts = rel.replace("\\", "/").split("/")
            if any(x in ("", ".", "..") or ":" in x for x in parts):
                fail(f"the disc image has a file with an unsafe name: {p}")
            o = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(o), exist_ok=True)
            f.seek(base + s * 2048)
            left = sz
            with open(o, "wb") as w:
                while left:
                    b = f.read(min(left, 1 << 24))
                    if not b:
                        fail("the disc image ends early: it is incomplete. Make a new image of the disc.")
                    w.write(b)
                    left -= len(b)
                    done += len(b)
                    if time.time() - last > 2:
                        last = time.time()
                        print(f"    {done * 100 // max(1, total)}%", end="\r", flush=True)
    finally:
        f.close()
    print("         ", end="\r", flush=True)


def step_game():
    say("\n[1/6] Looking for your copy of the game")
    game_out = os.path.join(OUT, "game")
    xbe = os.path.join(game_out, "default.xbe")
    if os.path.isfile(xbe) and sha1(xbe) == XBE_RUN and os.path.isdir(os.path.join(game_out, "tbd")):
        say("  already in place in the game folder")
        return xbe
    if os.path.isdir(GAME_IN) and any(f.lower().endswith((".zip", ".7z", ".rar")) for f in os.listdir(GAME_IN)) \
            and not any(f.lower().endswith((".iso", ".xiso")) for f in os.listdir(GAME_IN)):
        fail("the game in \"PUT YOUR GAME HERE\" is still packed (.zip, .7z or .rar). Unpack it there first.")
    say("  looking in \"PUT YOUR GAME HERE\"")
    found, ps2, rejected = candidates()
    if not found:
        if rejected:
            say("  These files were looked at and turned down:")
            for p, why, h in rejected:
                say(f"    {os.path.relpath(p, REPO)}")
                say(f"      {why}")
                if h:
                    say(f"      default.xbe SHA-1: {h}")
            say(f"  The port needs the USA retail Xbox release. Its default.xbe SHA-1 is {XBE_DISC}")
            say(f"  (or {XBE_RUN} once the installer has prepared it).")
            fail("no usable copy of the game: see the list above. If yours is the USA Xbox release and still "
                 "turned down, send the lines above (no file of the game) with your report.")
        if ps2:
            fail("the only copy found is the PlayStation 2 release (" + os.path.basename(ps2[0]) + "). This port "
                 "is built from the Xbox release: put an image of the Xbox disc in \"PUT YOUR GAME HERE\".")
        fail("there is no copy of the Xbox game in the \"PUT YOUR GAME HERE\" folder. Put your disc image (.iso) "
             "there, or the folder you extracted the disc into (the one with default.xbe and tbd), then run Install.bat again.")
    kind, path = found[0]
    say(f"  found: {os.path.relpath(path, REPO)} ({'disc image' if kind == 'image' else 'extracted folder'}, USA retail)")
    if len(found) > 1:
        say(f"  ({len(found) - 1} more copies found; using the first. Put the one you want in \"PUT YOUR GAME HERE\".)")
    free = shutil.disk_usage(REPO).free
    if free < (1 << 30):
        fail(f"not enough free disk space: 1 GB needed for the game files, {free >> 20} MB free.")
    shutil.rmtree(game_out, ignore_errors=True)
    os.makedirs(game_out, exist_ok=True)
    if kind == "image":
        say("  copying the game files out of the image (a minute or two)")
        extract_image(path, game_out)
    else:
        say("  copying the game files")
        for root, dirs, files in os.walk(path):
            rel = os.path.relpath(root, path)
            os.makedirs(os.path.join(game_out, rel), exist_ok=True)
            for f in files:
                if f.lower() not in SKIP_FILES:
                    cp(os.path.join(root, f), os.path.join(game_out, rel, f))
    if os.path.isfile(xbe):
        normalize_xbe(xbe)
    if not (os.path.isfile(xbe) and sha1(xbe) == XBE_RUN):
        fail("the game files were copied but default.xbe is not the USA retail release.")
    say("  OK: USA retail default.xbe")
    return xbe


# ---- the rest ---------------------------------------------------------------------------

def git_env(work):
    empty = os.path.join(work, "gitconfig-empty")
    open(empty, "a").close()
    return dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=empty,
                GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")


def fetch_commit(git, env, url, rev, dst):
    if os.path.isdir(os.path.join(dst, ".git")):
        r = subprocess.run([git, "-C", dst, "rev-parse", "HEAD"], env=env, capture_output=True, text=True)
        if r.stdout.strip() == rev:
            return False
        shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst, exist_ok=True)
    say(f"  fetching {rev[:10]}")
    run([git, "-C", dst, "init", "-q"], env=env, quiet=True)
    run([git, "-C", dst, "fetch", "-q", "--depth", "1", url, rev], env=env, quiet=True)
    run([git, "-C", dst, "checkout", "-q", "FETCH_HEAD"], env=env, quiet=True)
    r = subprocess.run([git, "-C", dst, "rev-parse", "HEAD"], env=env, capture_output=True, text=True)
    if r.stdout.strip() != rev:
        fail(f"{url} did not give the expected commit {rev}.")
    return True


def lift(py, xr, avpe, decomp, xbe, gen, work, env):
    """The same steps as relift.sh and rebuild.sh, from default.xbe and this repository's data."""
    seeds = []
    for s in ("seeds_structural.json", "seeds_observed.json", "seeds_pointers.json"):
        seeds += ["--seed-functions", os.path.join(avpe, "recomp", s)]
    show = lambda l: l.startswith(("  Total functions", "Wrote"))   # noqa: E731
    say("  reading default.xbe")
    run([py, "-m", "tools.xbe_parser", xbe, "--json", os.path.join(os.path.dirname(xbe), "default_analysis.json")],
        cwd=xr, env=env, quiet=True)
    say("  finding functions")
    run([py, "-m", "tools.disasm", xbe] + seeds + ["--force"], cwd=xr, env=env, show=show)
    run([py, "-m", "tools.func_id", xbe], cwd=xr, env=env, quiet=True)
    run([py, "-m", "tools.abi_analysis", xbe], cwd=xr, env=env, quiet=True)
    funcs = os.path.join(xr, "tools", "disasm", "output", "functions.json")
    if os.path.exists(funcs + ".orig"):
        os.remove(funcs + ".orig")
    run([py, "-I", os.path.join(avpe, "tools", "apply_names.py"), os.path.join(avpe, "symbols", "xbox_symbols.csv"), funcs],
        env=env, quiet=True)
    info = os.path.join(work, "xbe_info.json")
    run([py, os.path.join(avpe, "tools", "xbe_info.py"), xbe, info], env=env, quiet=True)
    fenv = dict(env, AVPE_FUNCS_JSON=funcs, AVPE_XBE=xbe, AVPE_XBE_INFO=info)
    run([py, os.path.join(avpe, "tools", "false_starts.py"), os.path.join(avpe, "recomp", "false_starts.json")],
        env=fenv, quiet=True)
    # the functions the decompilation replaces stay out of the generated C
    import re
    pat = re.compile(rb'^void (sub|avpe_dec)_[0-9A-Fa-f]{8}\(void\)', re.M)
    scan = open(os.path.join(avpe, "recomp", "avpe_bridge.c"), "rb").read()
    for sub in ("src", "platform"):
        d = os.path.join(decomp, sub)
        for f in sorted(os.listdir(d)):
            if f.endswith(".cpp"):
                b = open(os.path.join(d, f), "rb").read()
                if pat.search(b):
                    scan += b"\n" + b
    scan = re.sub(rb"AVPE-INDEX BEGIN.*?AVPE-INDEX END", b"", scan, flags=re.S)
    manual = os.path.join(work, "manual_scan_all.c")
    with open(manual, "wb") as f:
        f.write(scan)
    say("  translating to C")
    rmtree(gen)
    run([py, "-m", "tools.recomp", xbe, "--all", "--split", "250", "--gen-dir", gen, "--exclude-manual", manual,
         "--manual-functions", os.path.join(avpe, "recomp", "manual_functions.json")], cwd=xr, env=env, quiet=True)


def write_bmp32(path, img):
    """a 32-bit BMP with its alpha, the header pc_hd.cpp's loader (SDL) reads alpha from (BITMAPV5)"""
    import struct
    w, h = img.size
    raw = img.tobytes("raw", "BGRA", 0, -1)                  # bottom-up rows
    v5 = struct.pack("<IiiHHIIiiII", 124, w, h, 1, 32, 3, len(raw), 2835, 2835, 0, 0)
    v5 = (v5 + struct.pack("<IIII", 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000) + b"BGRs" + b"\0" * 80)[:124]
    with open(path, "wb") as f:
        f.write(b"BM" + struct.pack("<IHHI", 14 + 124 + len(raw), 0, 0, 14 + 124) + v5 + raw)


def shortcut(target, workdir, icon=None):
    lnk = os.path.join(REPO, "Play AvP Extinction.lnk")          # in this folder, not on the desktop
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:AVPE_LNK);"
          "$s.TargetPath=$env:AVPE_TARGET;$s.WorkingDirectory=$env:AVPE_DIR;"
          "if($env:AVPE_ICON){$s.IconLocation=$env:AVPE_ICON};$s.Save()")
    env = dict(os.environ, AVPE_LNK=lnk, AVPE_TARGET=target, AVPE_DIR=workdir, AVPE_ICON=icon or "")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True)
    except OSError:
        return None
    return lnk if r.returncode == 0 else None


def short_path(p):
    """the 8.3 form of a Windows path (no spaces), or p when the drive has no short names"""
    if os.name != "nt":
        return p
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(p, buf, len(buf))
    return buf.value if 0 < n < len(buf) else p


def build_root():
    """the build stays in this folder; GCC's tools trip over spaces, so a path with spaces is
    reached by its short (8.3) name, which names the same folder"""
    def plain(p):
        return " " not in p and p.isascii() and len(p) < 100
    os.makedirs(os.path.join(REPO, "_build"), exist_ok=True)
    root = os.path.join(short_path(REPO), "_build")
    if plain(root):
        return root
    root = short_path(os.path.join(REPO, "_build"))
    if plain(root):
        return root
    if os.name != "nt":                                 # test mode on Linux: no short names
        return "/tmp/AVPE-build"
    fail("this folder's path has spaces or non-English letters and Windows has no short name for it. "
         "Move this folder to a plain path such as C:\\Games\\AvPE and run Install.bat again.")


def main():
    global LOG
    os.environ["PYTHONUTF8"] = "1"
    os.makedirs(OUT, exist_ok=True)
    LOG = open(os.path.join(REPO, "install-log.txt"), "a", encoding="utf-8")
    t0 = time.time()
    say("=" * 70)
    say("AvP: Extinction PC installer  " + time.strftime("%Y-%m-%d %H:%M"))
    say("=" * 70)
    win = os.name == "nt"
    if not win:
        say("(not Windows: test mode, cross-building with MinGW-w64 from this machine's PATH)")
    root = build_root()
    tmp = os.path.join(root, "tmp")                     # compilers' temporary files stay in this folder too
    os.makedirs(tmp, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = os.environ["TMPDIR"] = tmp
    src = os.path.join(root, "src")
    tools = os.path.join(root, "tools")
    dl = os.path.join(root, "downloads")
    work = os.path.join(root, "work")
    for d in (src, tools, dl, work):
        os.makedirs(d, exist_ok=True)

    xbe_game = step_game()

    say("\n[2/6] Build tools")
    py = sys.executable
    if win:
        gcc_dir = os.path.join(tools, "mingw64")
        if not os.path.isfile(os.path.join(gcc_dir, "bin", "gcc.exe")):
            z = download("gcc", dl)
            say("  unpacking GCC (a few minutes)")
            unzip(z, tools)
        git_dir = os.path.join(tools, "git")
        if not os.path.isfile(os.path.join(git_dir, "cmd", "git.exe")):
            unzip(download("git", dl), git_dir)
        git = os.path.join(git_dir, "cmd", "git.exe")
        os.environ["PATH"] = (os.path.join(gcc_dir, "bin") + os.pathsep + os.path.join(git_dir, "cmd") + os.pathsep +
                              os.path.dirname(py) + os.pathsep + os.environ.get("PATH", ""))
        cross = ""
    else:
        git = shutil.which("git")
        cross = "x86_64-w64-mingw32-"
    env = dict(os.environ)
    for mod, names in (("capstone", ["capstone"]), ("PIL", ["pillow"]), ("numpy", ["numpy"])):
        try:
            __import__(mod)
        except ImportError:
            if not win:
                fail(f"pip install {names[0]} first (test mode)")
            run([py, "-m", "pip", "install", "-q", "--no-index", "--no-deps", "--no-warn-script-location"] +
                [download(n, dl) for n in names], quiet=True)
    deps = os.path.join(root, "win-deps")
    sdl = os.path.join(deps, "sdl2")
    if not os.path.isfile(os.path.join(sdl, "lib", "libSDL2.dll.a")):
        t = download("sdl2", dl)
        with tarfile.open(t) as tf:
            tf.extractall(dl, filter="data")
        rmtree(sdl)
        shutil.copytree(os.path.join(dl, f"SDL2-{SDL_VER}", "x86_64-w64-mingw32"), sdl, copy_function=cp)
    ff = os.path.join(deps, "ffmpeg")
    if not os.path.isfile(os.path.join(ff, "lib", "libavcodec.dll.a")):
        z = download("ffmpeg", dl)
        rmtree(ff)
        unzip(z, ff)
    gitenv = dict(git_env(root), PATH=env["PATH"])

    say("\n[3/6] Sources")
    xr = os.path.join(src, "xboxrecomp")
    local_xr = os.path.join(REPO, "xboxrecomp")       # an offline copy next to Install.bat wins
    if os.path.isdir(os.path.join(local_xr, "tools")):
        say("  using the recompiler toolkit in the xboxrecomp folder")
        rmtree(xr)
        shutil.copytree(local_xr, xr, ignore=shutil.ignore_patterns(".git"))
    else:
        fetch_commit(git, gitenv, URL, XR_REV, xr)
    decomp = os.path.join(src, "avpe-decomp")
    local = os.path.join(REPO, "avpe-decomp")       # an offline copy next to Install.bat wins
    if os.path.isdir(os.path.join(local, "platform")):
        say("  using the decompilation in the avpe-decomp folder")
        rmtree(decomp)
        shutil.copytree(local, decomp, ignore=shutil.ignore_patterns(".git"))
    else:
        fetch_commit(git, gitenv, DECOMP_URL, DECOMP_REV, decomp)
    xbe = os.path.join(work, "default.xbe")
    cp(xbe_game, xbe)

    say("\n[4/6] Recompiling default.xbe into C")
    gen = os.path.join(work, "gen")
    done = os.path.join(work, ".lifted")
    stamp = f"{XR_REV} {DECOMP_REV} {sha1(xbe)} {sha1(os.path.join(REPO, 'recomp', 'avpe_bridge.c'))}"
    if not (os.path.isfile(done) and open(done).read() == stamp):
        say("  about 5 to 15 minutes, depending on the PC")
        lift(py, xr, REPO, decomp, xbe, gen, work, env)
        with open(done, "w") as f:
            f.write(stamp)
    else:
        say("  already done")

    say("\n[5/6] Compiling avpe.exe (5 to 20 minutes)")
    bout = os.path.join(work, "dist")
    cmd = [py, os.path.join(HERE, "build.py"), "--gen", gen, "--xr", xr, "--avpe", REPO, "--decomp", decomp,
           "--sdl", sdl, "--ff", ff, "--work", os.path.join(work, "build"), "--out", bout,
           "--build-id", f"installer {time.strftime('%Y-%m-%d')} decomp {DECOMP_REV[:7]}"]
    if cross:
        cmd += ["--cross", cross]
    run(cmd, env=env, show=lambda l: l.startswith("[build]") or "error" in l.lower())

    say("\n[6/6] Putting it together")
    for f in os.listdir(bout):
        cp(os.path.join(bout, f), OUT)
    pkg = os.path.join(REPO, "recomp", "windows")
    if not os.path.isfile(os.path.join(OUT, "avpe.ini")):
        cp(os.path.join(pkg, "avpe.ini"), OUT)
    for f in ("README.txt", "Pack session.bat"):
        if os.path.isfile(os.path.join(pkg, f)):
            cp(os.path.join(pkg, f), OUT)
    # the sharp font pages: our letters only; the game fills the rest from your disc (pc_hd.cpp)
    from PIL import Image
    hd = os.path.join(OUT, "pc", "hd")
    os.makedirs(hd, exist_ok=True)
    for f in sorted(os.listdir(os.path.join(REPO, "pc", "hd"))):
        if f.endswith(".ovl.png"):
            write_bmp32(os.path.join(hd, f[:-4] + ".bmp"), Image.open(os.path.join(REPO, "pc", "hd", f)).convert("RGBA"))
    # the key and mouse prompt icons and the controls pages' pictures (keyboard and mouse,
    # Xbox, PlayStation, Switch pads): our own art, drawn for the port
    for f in sorted(os.listdir(os.path.join(REPO, "pc", "art"))):
        if f.endswith(".png"):
            write_bmp32(os.path.join(OUT, "pc", f[:-4] + ".bmp"), Image.open(os.path.join(REPO, "pc", "art", f)).convert("RGBA"))
    # the launcher: its fonts (Barlow, OFL), then its picture and the icon from your title screen
    lf = os.path.join(OUT, "pc", "launcher", "fonts")
    os.makedirs(lf, exist_ok=True)
    fsrc = os.path.join(decomp, "platform", "launcher_fonts")
    for f in os.listdir(fsrc):
        cp(os.path.join(fsrc, f), lf)
    title_dir = os.path.join(work, "title")
    os.makedirs(title_dir, exist_ok=True)
    title = os.path.join(title_dir, "title.bmp")
    if os.environ.get("AVPE_TEST_TITLE"):                       # test mode: a capture made elsewhere
        cp(os.environ["AVPE_TEST_TITLE"], title)
    elif win and not os.path.isfile(title):
        say("  making the launcher picture and the icon from your title screen (the game opens for a moment)")
        cenv = dict(os.environ, AVPE_PC_TITLEART=title_dir, AVPE_MOVIES="0", AVPE_NO_SETTINGS="1", AVPE_RECORD="0",
                    AVPE_PC_FULLSCREEN="0", AVPE_PC_SIZE="640x480", AVPE_PC_RES="640x480")
        r = subprocess.run([os.path.join(OUT, "avpe.exe")], cwd=OUT, env=cenv, timeout=300)
        if r.returncode != 0 or not os.path.isfile(title):
            say("  (the title screen could not be captured: the launcher uses a plain background)")
    icon = None
    if os.path.isfile(title):
        run([py, os.path.join(HERE, "launcher_art.py"), title, os.path.join(OUT, "pc", "launcher")], env=env, quiet=True)
        icon = os.path.join(OUT, "pc", "launcher", "avpe.ico")
        say("  icon and launcher picture made")
        run(cmd + ["--icon", icon], env=env, show=lambda l: "error" in l.lower())   # relinks with the icon
        cp(os.path.join(bout, "avpe.exe"), OUT)
    lnk = shortcut(os.path.join(OUT, "avpe.exe"), OUT, icon) if win else None
    mins = (time.time() - t0) / 60
    say("\n" + "=" * 70)
    say(f"Done in {mins:.0f} minutes. The game is in the \"{os.path.basename(OUT)}\" folder in this folder.")
    if lnk:
        say("Start it next time with \"Play AvP Extinction\" in this folder.")
    say("Opening the launcher now.")
    say("=" * 70)
    if win:
        try:
            subprocess.Popen([os.path.join(OUT, "avpe.exe")], cwd=OUT)       # the launcher comes up first
        except OSError:
            pass


# ---- updates ------------------------------------------------------------------------------
# what an update never touches: your game files, the build tools, the game folder (saves,
# settings, recordings), the log and the shortcut
KEEP = {"PUT YOUR GAME HERE", "_build", os.path.basename(OUT), "install-log.txt", "Play AvP Extinction.lnk",
        "old builds", ".git"}


def version(root):
    """the package's VERSION.txt line, or (unknown)"""
    try:
        with open(os.path.join(root, "VERSION.txt"), encoding="utf-8") as f:
            return f.readline().strip() or "(unknown)"
    except OSError:
        return "(unknown)"


def package_root(path, scratch):
    """the dropped folder (or zip) as a package: the folder with Install.bat and installer/install.py,
    the dropped one or one level in (an unzipped download often has its folder inside)"""
    path = os.path.abspath(path)
    if os.path.isfile(path) and path.lower().endswith(".zip"):
        shutil.rmtree(scratch, ignore_errors=True)
        os.makedirs(scratch, exist_ok=True)
        unzip(path, scratch)
        path = scratch
    if not os.path.isdir(path):
        return None
    cands = [path] + [os.path.join(path, d) for d in sorted(os.listdir(path)) if os.path.isdir(os.path.join(path, d))]
    for c in cands:
        if os.path.isfile(os.path.join(c, "Install.bat")) and os.path.isfile(os.path.join(c, "installer", "install.py")):
            return c
    return None


def no_quick_edit():
    """a click in a Windows console window selects text and pauses the program until a key is
    pressed (QuickEdit): off for this window, so a stray click does not stop the update"""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)                         # the console's input
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x40) | 0x80)   # QuickEdit off (ENABLE_EXTENDED_FLAGS on)
    except Exception:
        pass


def update_from(path):
    global LOG
    no_quick_edit()
    LOG = open(os.path.join(REPO, "install-log.txt"), "a", encoding="utf-8")
    say("=" * 70)
    say("AvP: Extinction PC update  " + time.strftime("%Y-%m-%d %H:%M"))
    say("=" * 70)
    new = package_root(path, os.path.join(build_root(), "update"))
    if not new:
        fail("that is not an AvP: Extinction PC package (a folder with Install.bat and an installer folder).")
    if os.path.normcase(os.path.abspath(new)) == os.path.normcase(os.path.abspath(REPO)):
        fail("that is this folder: drop the folder of the new download instead.")
    say(f"  installed: {version(REPO)}")
    say(f"  update:    {version(new)}")
    exe = os.path.join(OUT, "avpe.exe")
    for _ in range(60):                                  # the launcher closes itself to let the game be replaced
        try:
            if os.path.isfile(exe):
                with open(exe, "ab"):
                    pass
            break
        except OSError:
            time.sleep(1)
    else:
        fail("the game is still running: close it and drop the update on the launcher again.")
    say("  copying the update's files (your game files, saves and settings stay)")
    for name in sorted(os.listdir(new)):
        if name in KEEP:
            continue
        s, d = os.path.join(new, name), os.path.join(REPO, name)
        say(f"    {name}")
        if name == "installer" and os.path.isdir(s):     # (its private Python stays)
            for f in os.listdir(s):
                if f == "python":
                    continue
                fs, fd = os.path.join(s, f), os.path.join(d, f)
                if os.path.isdir(fs):
                    shutil.rmtree(fd, ignore_errors=True)
                    shutil.copytree(fs, fd, copy_function=cp)
                else:
                    cp(fs, fd)
        elif os.path.isdir(s):
            shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(s, d, copy_function=cp)
        else:
            cp(s, d)
    say("  rebuilding the game from the updated source (no downloads: a few minutes)")
    LOG.close()
    LOG = None
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "install.py")], cwd=REPO)
    return r.returncode


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--update-from":
        rc = 1
        try:
            rc = update_from(sys.argv[2])
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        except KeyboardInterrupt:
            rc = 1
        print("\nThe update is done: the launcher is opening." if rc == 0 else
              "\nThe update stopped (see above, or install-log.txt). The game you had still runs.")
        if os.name == "nt" and sys.stdin and sys.stdin.isatty():
            try:
                input("Press Enter to close this window.")
            except EOFError:
                pass
        sys.exit(rc)
    try:
        main()
    except KeyboardInterrupt:
        fail("stopped.")
