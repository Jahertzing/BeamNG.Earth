#!/usr/bin/env python3
"""Windows: keep BeamNG.drive's grass when the physics TerrainBlock moves (tools/gcpatch's Windows counterpart).

BeamNG.Earth moves its hidden physics block under the player every ~128 m. The engine's GroundCover terrain-update
handler then frees every grass cell on the block and the mod regenerates them all in that frame
(groundcover.afterBlockChange), one slow frame per move that grows with the grass density and radius. On Linux,
tools/gcpatch (an LD_PRELOAD library) changes one byte of that handler so height / layer updates free nothing. Windows
has no LD_PRELOAD, so the launcher does the same from outside: it finds the game's process, checks the handler's bytes
and writes the byte with WriteProcessMemory. Nothing on disk changes; the patch ends with the game.

The handler in the Windows build (BeamNG.drive 0.40.3, BeamNG.drive.x64.exe, MSVC; `this` in rcx, the update flags in
edx):
    test dl, 1        ; the lightmap update:
    je   +rel8        ;   (not set: on)
    <epilogue>        ;   set: free every cell (a tail jump)
    jmp  rel32
    test dl, 0Eh      ; the height / layer / empty update bits
    je   rel32        ; none: return
    ...               ; free the cells over the updated rectangle
The 0Eh becomes 00h, so `test dl, 0` always jumps to the return; the lightmap path stays. The handler is found by
those bytes in the executable's code (one match only, the jump distances and the epilogue masked), so a game update
that keeps them works as is, and one that changes them leaves the game stock (the status file says so).

Only while the BeamNG.Earth level runs: the mod creates temp/beamngearth_gcpatch_on in the game's user folder when its
level starts and removes it when it ends (groundcover.lua); this looks for it every POLL_S and writes the byte back and
forth, so other maps (a terrain edited in the World Editor) keep the stock behaviour. Status line, as the Linux
library's: <user folder>/temp/beamngEarth_gcpatch.txt.

    python gcpatchwin.py --user-dir "%LOCALAPPDATA%\\BeamNG\\BeamNG.drive\\current"     (a game started by hand)
    python gcpatchwin.py --find PATH\\TO\\BeamNG.drive.x64.exe                          (where the byte is, no game)

The launcher runs it in a thread (launcher.py start_gcpatch); --no-gcpatch or BNGEARTH_GCPATCH_OFF turn it off.
Unsupported by BeamNG, as the Linux library.
"""
import argparse
import os
import re
import struct
import sys
import threading
import time

GAME_EXE = "BeamNG.drive.x64.exe"
FLAG = "beamngearth_gcpatch_on"
STATUS = "beamngEarth_gcpatch.txt"
STOCK = 0x0E
PATCHED = 0x00
POLL_S = 0.25
ATTACH_S = 1.0          # a game process looked for this often
# test dl,1 ; je rel8 ; (the lightmap path: up to 28 bytes of epilogue and its jump) ; test dl,0Eh ; je rel32
SIGNATURE = re.compile(rb"\xf6\xc2\x01\x74[\x00-\x7f].{0,28}?\xf6\xc2\x0e\x0f\x84", re.S)
IMM_FROM_END = 3        # the 0Eh: the match ends `0E 0F 84`
SCN_EXECUTE = 0x20000000


def find_imm(image):
    """The handler's 0Eh in a PE image (the executable's bytes): (rva, context bytes, context rva) or (None, why).
    The context is the whole match, compared with the running game's memory before anything is written."""
    try:
        if image[:2] != b"MZ":
            return None, "not a Windows executable"
        pe = struct.unpack_from("<I", image, 0x3C)[0]
        if image[pe:pe + 4] != b"PE\0\0":
            return None, "not a Windows executable"
        nsec = struct.unpack_from("<H", image, pe + 6)[0]
        opt = struct.unpack_from("<H", image, pe + 20)[0]
        sec = pe + 24 + opt
        found = []
        for i in range(nsec):
            _name, vsize, va, rsize, raw, _a, _b, _c, _d, chars = struct.unpack_from("<8sIIIIIIHHI", image, sec + 40 * i)
            if not chars & SCN_EXECUTE:
                continue
            code = image[raw:raw + min(rsize, vsize or rsize)]
            for m in SIGNATURE.finditer(code):
                found.append((va + m.start(), m.group(0)))
    except struct.error:
        return None, "a damaged executable"
    if len(found) != 1:
        return None, f"the handler was not found in this build ({len(found)} matches)"
    ctx_rva, ctx = found[0]
    if ctx[-IMM_FROM_END] not in (STOCK, PATCHED):
        return None, "unexpected byte (another build)"
    return (ctx_rva + len(ctx) - IMM_FROM_END, ctx, ctx_rva), None


def report(user_dir, pid, msg):
    """One line for the player / the mod's diagnostics: the console and <user>/temp/beamngEarth_gcpatch.txt."""
    print(f"[bngearth-gcpatch] {msg}", flush=True)
    if not user_dir:
        return
    try:
        os.makedirs(os.path.join(user_dir, "temp"), exist_ok=True)
        with open(os.path.join(user_dir, "temp", STATUS), "w", encoding="utf-8") as f:
            f.write(f"{int(time.time())} pid {pid}: {msg}\n")
    except OSError:
        pass


# --- the game's process (Windows only) --------------------------------------------------------------------------

class _Win:
    """The few kernel32 calls, made on first use (this module imports on any OS: the tests run find_imm)."""
    PROCESS_ALL = 0x0008 | 0x0010 | 0x0020 | 0x0400 | 0x00100000   # VM_OPERATION, VM_READ, VM_WRITE, QUERY_INFO, SYNC
    PAGE_EXECUTE_READWRITE = 0x40

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.c, self.w = ctypes, w
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k = k

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                        ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                        ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", w.LONG), ("dwFlags", w.DWORD),
                        ("szExeFile", w.WCHAR * 260)]

        class MODULEENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", w.DWORD), ("th32ModuleID", w.DWORD), ("th32ProcessID", w.DWORD),
                        ("GlblcntUsage", w.DWORD), ("ProccntUsage", w.DWORD), ("modBaseAddr", ctypes.c_void_p),
                        ("modBaseSize", w.DWORD), ("hModule", w.HMODULE), ("szModule", w.WCHAR * 256),
                        ("szExePath", w.WCHAR * 260)]

        self.PE, self.ME = PROCESSENTRY32W, MODULEENTRY32W
        k.CreateToolhelp32Snapshot.restype = w.HANDLE
        k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k.Module32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        k.OpenProcess.restype = w.HANDLE
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.CloseHandle.argtypes = [w.HANDLE]
        k.ReadProcessMemory.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                        ctypes.POINTER(ctypes.c_size_t)]
        k.WriteProcessMemory.argtypes = k.ReadProcessMemory.argtypes
        k.VirtualProtectEx.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD, ctypes.POINTER(w.DWORD)]
        k.FlushInstructionCache.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
        k.WaitForSingleObject.restype = w.DWORD
        k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]

    def _snapshot(self, flags, pid):
        h = self.k.CreateToolhelp32Snapshot(flags, pid)
        return None if h in (None, self.c.c_void_p(-1).value) else h

    def pids(self, name):
        h = self._snapshot(0x2, 0)                  # TH32CS_SNAPPROCESS
        if not h:
            return []
        out = []
        try:
            e = self.PE()
            e.dwSize = self.c.sizeof(e)
            ok = self.k.Process32FirstW(h, self.c.byref(e))
            while ok:
                if e.szExeFile.lower() == name.lower():
                    out.append(e.th32ProcessID)
                ok = self.k.Process32NextW(h, self.c.byref(e))
        finally:
            self.k.CloseHandle(h)
        return out

    def main_module(self, pid):
        """(base address, the executable's path) of the process's main module, or None (still starting)."""
        h = self._snapshot(0x8 | 0x10, pid)         # TH32CS_SNAPMODULE | SNAPMODULE32
        if not h:
            return None
        try:
            e = self.ME()
            e.dwSize = self.c.sizeof(e)
            if not self.k.Module32FirstW(h, self.c.byref(e)):
                return None
            return e.modBaseAddr, e.szExePath
        finally:
            self.k.CloseHandle(h)

    def open(self, pid):
        return self.k.OpenProcess(self.PROCESS_ALL, False, pid) or None

    def alive(self, h):
        return self.k.WaitForSingleObject(h, 0) == 0x102     # WAIT_TIMEOUT: still running

    def read(self, h, addr, n):
        buf = self.c.create_string_buffer(n)
        got = self.c.c_size_t(0)
        if not self.k.ReadProcessMemory(h, self.c.c_void_p(addr), buf, n, self.c.byref(got)) or got.value != n:
            return None
        return buf.raw

    def write_byte(self, h, addr, value):
        old = self.w.DWORD(0)
        if not self.k.VirtualProtectEx(h, self.c.c_void_p(addr), 1, self.PAGE_EXECUTE_READWRITE, self.c.byref(old)):
            return False
        try:
            b = self.c.c_ubyte(value)
            n = self.c.c_size_t(0)
            ok = self.k.WriteProcessMemory(h, self.c.c_void_p(addr), self.c.byref(b), 1, self.c.byref(n))
        finally:
            self.k.VirtualProtectEx(h, self.c.c_void_p(addr), 1, old.value, self.c.byref(self.w.DWORD(0)))
        self.k.FlushInstructionCache(h, self.c.c_void_p(addr), 1)
        return bool(ok) and n.value == 1

    def close(self, h):
        self.k.CloseHandle(h)


def _attach(win, pid, user_dir, cache):
    """The game process's handle and the byte's address, after checking its bytes; None when it cannot be patched."""
    mod = None
    for _ in range(30):                             # the module list exists only once the process has loaded it
        mod = win.main_module(pid)
        if mod:
            break
        time.sleep(0.5)
    if not mod:
        report(user_dir, pid, "not applied: the game's code was not found")
        return None
    base, exe = mod
    if exe not in cache:
        try:
            with open(exe, "rb") as f:
                cache[exe] = find_imm(f.read())
        except OSError as e:
            cache[exe] = (None, f"the executable could not be read ({e})")
    found, why = cache[exe]
    if not found:
        report(user_dir, pid, f"not applied: {why}")
        return None
    imm_rva, ctx, ctx_rva = found
    h = win.open(pid)
    if not h:
        report(user_dir, pid, "not applied: the game's process could not be opened")
        return None
    mem = win.read(h, base + ctx_rva, len(ctx))
    i = len(ctx) - IMM_FROM_END
    if mem is None or mem[:i] != ctx[:i] or mem[i + 1:] != ctx[i + 1:] or mem[i] not in (STOCK, PATCHED):
        win.close(h)
        report(user_dir, pid, "not applied: the game's code in memory differs from its executable")
        return None
    return h, base + imm_rva, mem[i] == PATCHED


def run(user_dir, stop=None, win=None):
    """Until stop is set: for each game process, the byte patched while the level flag exists, stock otherwise."""
    win = win or _Win()
    stop = stop or threading.Event()
    flag = os.path.join(user_dir, "temp", FLAG)
    cache = {}
    done = set()                                    # processes already dealt with (patched, or not patchable)
    while not stop.is_set():
        pid = next((p for p in win.pids(GAME_EXE) if p not in done), None)
        if pid is None:
            stop.wait(ATTACH_S)
            continue
        done.add(pid)
        got = _attach(win, pid, user_dir, cache)
        if not got:
            continue
        h, addr, on = got
        report(user_dir, pid, "ready: applied while the BeamNG.Earth level runs")
        try:
            while not stop.is_set() and win.alive(h):
                want = os.path.exists(flag)
                if want != on:
                    if not win.write_byte(h, addr, PATCHED if want else STOCK):
                        report(user_dir, pid, "stopped: the game's memory could not be written")
                        break
                    on = want
                    report(user_dir, pid, "applied: grass kept across terrain block moves (BeamNG.Earth level)" if on
                           else "stock: another level (the grass follows terrain edits)")
                stop.wait(POLL_S)
        finally:
            if on and win.alive(h):                 # the launcher stopping first: the game left stock
                win.write_byte(h, addr, STOCK)
            win.close(h)


def start(user_dir):
    """The launcher's thread (a daemon: it ends with the launcher), or None when off or not Windows."""
    if os.name != "nt" or os.environ.get("BNGEARTH_GCPATCH_OFF") or not user_dir:
        return None
    t = threading.Thread(target=_run_quietly, args=(user_dir,), name="gcpatch", daemon=True)
    t.start()
    return t


def _run_quietly(user_dir):
    try:
        run(user_dir)
    except Exception as e:                          # never take the launcher down
        report(user_dir, 0, f"stopped: {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--user-dir", help="the game's user folder (…\\BeamNG.drive\\current)")
    ap.add_argument("--find", metavar="EXE", help="only report where the byte is in this executable")
    a = ap.parse_args()
    if a.find:
        with open(a.find, "rb") as f:
            found, why = find_imm(f.read())
        print(f"rva 0x{found[0]:x}, byte 0x{found[1][-IMM_FROM_END]:02x}" if found else why)
        return 0 if found else 1
    if os.name != "nt" or not a.user_dir:
        ap.error("Windows only, with --user-dir")
    try:
        run(a.user_dir)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
