#!/usr/bin/env python3
"""Where the game's main thread spends a stall, seen from outside the game (2026-10-29: the 4.6-8.8 s freezes and the
50-100 ms FramePresent stutters on Jeff's drive sat "mostly outside the mod and engine timers", and neither the mod nor
the engine's own metrics can say whether such a frame went to the engine, the D3D12 runtime, the NVIDIA driver or a
wait in the kernel).

It samples BeamNG.drive.x64.exe's main thread (the game window's thread) HZ times a second: SuspendThread,
GetThreadContext (the instruction pointer), ResumeThread, ~50 us together, and attributes the address to its module
(BeamNG.drive.x64.exe, d3d12.dll, dxgi.dll, nvwgf2umx.dll = the NVIDIA user-mode driver, ntdll.dll / KernelBase.dll =
waits and system calls). Every STACK_EVERY-th sample, and once more when the thread has sat in one place for STALL_MS,
it walks the stack while the thread is still suspended (dbghelp's StackWalk64 over the modules' own unwind tables: no
symbols needed, no PDBs exist), so a wait says who waited: dxgi (the present), nvwgf2umx (the driver's fence) or the
exe (the engine's own synchronisation), and a long run in one module says which.

  python tools/stallwatch.py --minutes 10 --out C:\\beamng-earth\\stallwatch\\lap1
  python tools/stallwatch.py --pid 36920 --hz 250 --seconds 30

Writes <out>/seconds.jsonl (one line a second: the module shares, the wait share, the samples), <out>/stalls.jsonl
(every run of STALL_MS or more in one module or one wait: when (wall clock, to match server.log's perf lines), how
long, the module, the stack as module+offset) and <out>/summary.json (the shares over the run, the stalls by module,
the longest ones). Nothing is installed and no admin rights are needed (the same user's process). The sampled thread
loses ~1 % of its time at 250 Hz. Stops with the game, with Ctrl-C, or after --minutes / --seconds. Windows only.
Unsupported by BeamNG, as gcpatchwin.py: a debugger-style attach, nothing in the game changes.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import datetime
import json
import os
import sys
import time

GAME_EXE = "BeamNG.drive.x64.exe"
HZ = 250
STACK_EVERY = 25            # a stack every this many samples (10 a second at 250 Hz) ...
STALL_MS = 50               # ... and when the thread has been in one place this long
STALL_WINDOW = 1 << 20      # "one place": the same module and within this many bytes (a long loop, a driver compile)
MAX_FRAMES = 24
WAIT_MODULES = ("ntdll.dll", "kernelbase.dll", "kernel32.dll", "win32u.dll", "user32.dll")

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
THREAD_SUSPEND_RESUME = 0x0002
THREAD_GET_CONTEXT = 0x0008
THREAD_QUERY_INFORMATION = 0x0040
CONTEXT_AMD64 = 0x00100000
CONTEXT_CONTROL = CONTEXT_AMD64 | 0x0001
CONTEXT_INTEGER = CONTEXT_AMD64 | 0x0002
IMAGE_FILE_MACHINE_AMD64 = 0x8664
LIST_MODULES_ALL = 0x03
ADDR_MODE_FLAT = 3

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
dbghelp = ctypes.WinDLL("dbghelp", use_last_error=True)

HANDLE, DWORD, BOOL = wt.HANDLE, wt.DWORD, wt.BOOL
ULONG64, PVOID = ctypes.c_ulonglong, ctypes.c_void_p


# ---------------------------------------------------------------------------------------------------- structures
class M128A(ctypes.Structure):
    _fields_ = [("Low", ctypes.c_ulonglong), ("High", ctypes.c_longlong)]


class CONTEXT(ctypes.Structure):
    """winnt.h CONTEXT for AMD64 (1232 bytes, 16-byte aligned: allocated through aligned_context)."""
    _fields_ = [
        ("P1Home", ULONG64), ("P2Home", ULONG64), ("P3Home", ULONG64), ("P4Home", ULONG64), ("P5Home", ULONG64),
        ("P6Home", ULONG64),
        ("ContextFlags", DWORD), ("MxCsr", DWORD),
        ("SegCs", wt.WORD), ("SegDs", wt.WORD), ("SegEs", wt.WORD), ("SegFs", wt.WORD), ("SegGs", wt.WORD),
        ("SegSs", wt.WORD), ("EFlags", DWORD),
        ("Dr0", ULONG64), ("Dr1", ULONG64), ("Dr2", ULONG64), ("Dr3", ULONG64), ("Dr6", ULONG64), ("Dr7", ULONG64),
        ("Rax", ULONG64), ("Rcx", ULONG64), ("Rdx", ULONG64), ("Rbx", ULONG64), ("Rsp", ULONG64), ("Rbp", ULONG64),
        ("Rsi", ULONG64), ("Rdi", ULONG64), ("R8", ULONG64), ("R9", ULONG64), ("R10", ULONG64), ("R11", ULONG64),
        ("R12", ULONG64), ("R13", ULONG64), ("R14", ULONG64), ("R15", ULONG64),
        ("Rip", ULONG64),
        ("FltSave", ctypes.c_byte * 512),
        ("VectorRegister", M128A * 26),
        ("VectorControl", ULONG64), ("DebugControl", ULONG64), ("LastBranchToRip", ULONG64),
        ("LastBranchFromRip", ULONG64), ("LastExceptionToRip", ULONG64), ("LastExceptionFromRip", ULONG64),
    ]


assert ctypes.sizeof(CONTEXT) == 1232, ctypes.sizeof(CONTEXT)


def aligned_context():
    """A CONTEXT at a 16-byte aligned address (GetThreadContext needs that), with the buffer that keeps it alive."""
    buf = ctypes.create_string_buffer(ctypes.sizeof(CONTEXT) + 16)
    addr = ctypes.addressof(buf)
    off = (-addr) % 16
    return CONTEXT.from_buffer(buf, off), buf


class ADDRESS64(ctypes.Structure):
    _fields_ = [("Offset", ULONG64), ("Segment", wt.WORD), ("Mode", DWORD)]


class KDHELP64(ctypes.Structure):
    _fields_ = [("Thread", ULONG64), ("ThCallbackStack", DWORD), ("ThCallbackBStore", DWORD), ("NextCallback", DWORD),
                ("FramePointer", DWORD), ("KiCallUserMode", ULONG64), ("KeUserCallbackDispatcher", ULONG64),
                ("SystemRangeStart", ULONG64), ("KiUserExceptionDispatcher", ULONG64), ("StackBase", ULONG64),
                ("StackLimit", ULONG64), ("BuildVersion", DWORD), ("RetpolineStubFunctionTableSize", DWORD),
                ("RetpolineStubFunctionTable", ULONG64), ("RetpolineStubOffset", DWORD), ("RetpolineStubSize", DWORD),
                ("Reserved0", ULONG64 * 2)]


class STACKFRAME64(ctypes.Structure):
    _fields_ = [("AddrPC", ADDRESS64), ("AddrReturn", ADDRESS64), ("AddrFrame", ADDRESS64), ("AddrStack", ADDRESS64),
                ("AddrBStore", ADDRESS64), ("FuncTableEntry", PVOID), ("Params", ULONG64 * 4), ("Far", BOOL),
                ("Virtual", BOOL), ("Reserved", ULONG64 * 3), ("KdHelp", KDHELP64)]


class MODULEINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", PVOID), ("SizeOfImage", DWORD), ("EntryPoint", PVOID)]


# prototypes
kernel32.OpenProcess.restype = HANDLE
kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
kernel32.OpenThread.restype = HANDLE
kernel32.OpenThread.argtypes = [DWORD, BOOL, DWORD]
kernel32.SuspendThread.restype = DWORD
kernel32.SuspendThread.argtypes = [HANDLE]
kernel32.ResumeThread.restype = DWORD
kernel32.ResumeThread.argtypes = [HANDLE]
kernel32.GetThreadContext.restype = BOOL
kernel32.GetThreadContext.argtypes = [HANDLE, ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
psapi.EnumProcessModulesEx.restype = BOOL
psapi.EnumProcessModulesEx.argtypes = [HANDLE, ctypes.POINTER(HANDLE), DWORD, ctypes.POINTER(DWORD), DWORD]
psapi.GetModuleFileNameExW.restype = DWORD
psapi.GetModuleFileNameExW.argtypes = [HANDLE, HANDLE, wt.LPWSTR, DWORD]
psapi.GetModuleInformation.restype = BOOL
psapi.GetModuleInformation.argtypes = [HANDLE, HANDLE, ctypes.POINTER(MODULEINFO), DWORD]
EnumWindowsProc = ctypes.WINFUNCTYPE(BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wt.LPARAM]
user32.GetWindowThreadProcessId.restype = DWORD
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(DWORD)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
dbghelp.SymInitialize.restype = BOOL
dbghelp.SymInitialize.argtypes = [HANDLE, ctypes.c_wchar_p, BOOL]
dbghelp.SymCleanup.argtypes = [HANDLE]
dbghelp.SymSetOptions.restype = DWORD
dbghelp.SymSetOptions.argtypes = [DWORD]
dbghelp.StackWalk64.restype = BOOL
dbghelp.StackWalk64.argtypes = [DWORD, HANDLE, HANDLE, ctypes.POINTER(STACKFRAME64), ctypes.c_void_p, PVOID, PVOID, PVOID,
                                PVOID]
SYMOPT_DEFERRED_LOADS = 0x4
SYMOPT_UNDNAME = 0x2


# ---------------------------------------------------------------------------------------------------- the process
def find_game(pid=None):
    """(pid, name) of the game's main process: --pid, else the largest BeamNG.drive.x64.exe (the CEF helpers are small)."""
    import psutil
    if pid:
        return pid, psutil.Process(pid).name()
    best = None
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            if p.info["name"] and p.info["name"].lower() == GAME_EXE.lower():
                rss = p.info["memory_info"].rss if p.info["memory_info"] else 0
                if best is None or rss > best[1]:
                    best = (p.info["pid"], rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if best is None:
        return None, None
    return best[0], GAME_EXE


def window_thread(pid):
    """The thread id of the process's visible, titled window (the game's main thread), or None."""
    found = []

    def cb(hwnd, _):
        wpid = DWORD(0)
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(tid)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found[0] if found else None


def first_thread(pid):
    """The process's oldest thread (its main thread when it has no window yet), by creation time (psutil)."""
    import psutil
    try:
        ths = psutil.Process(pid).threads()
    except psutil.Error:
        return None
    return ths[0].id if ths else None


class Modules:
    """The process's loaded modules (base, size, name), refreshed now and then (DLLs load during a drive)."""

    def __init__(self, hproc):
        self.hproc = hproc
        self.mods = []      # sorted by base: (base, end, name)
        self.at = 0
        self.refresh()

    def refresh(self):
        needed = DWORD(0)
        arr = (HANDLE * 1024)()
        if not psapi.EnumProcessModulesEx(self.hproc, arr, ctypes.sizeof(arr), ctypes.byref(needed), LIST_MODULES_ALL):
            return
        n = min(needed.value // ctypes.sizeof(HANDLE), 1024)
        mods = []
        name = ctypes.create_unicode_buffer(1024)
        info = MODULEINFO()
        for i in range(n):
            h = arr[i]
            if psapi.GetModuleFileNameExW(self.hproc, h, name, 1024) and \
                    psapi.GetModuleInformation(self.hproc, h, ctypes.byref(info), ctypes.sizeof(info)):
                base = info.lpBaseOfDll or 0
                mods.append((base, base + info.SizeOfImage, os.path.basename(name.value)))
        mods.sort()
        self.mods, self.at = mods, time.time()

    def of(self, addr):
        """(name, offset) of an address, ('?', addr) when in no module."""
        mods = self.mods
        lo, hi = 0, len(mods) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            base, end, name = mods[mid]
            if addr < base:
                hi = mid - 1
            elif addr >= end:
                lo = mid + 1
            else:
                return name, addr - base
        return "?", addr


# ------------------------------------------------------------------------------------------------------ sampling
class Sampler:
    def __init__(self, pid, tid, out, hz, stall_ms, stack_every):
        self.pid, self.tid, self.out = pid, tid, out
        self.period = 1.0 / hz
        self.stall_samples = max(2, int(stall_ms * hz / 1000))
        self.stack_every = stack_every
        self.hproc = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not self.hproc:
            raise OSError(f"OpenProcess({pid}): error {ctypes.get_last_error()}")
        self.hthread = kernel32.OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, False, tid)
        if not self.hthread:
            raise OSError(f"OpenThread({tid}): error {ctypes.get_last_error()}")
        dbghelp.SymSetOptions(SYMOPT_DEFERRED_LOADS | SYMOPT_UNDNAME)
        self.sym = bool(dbghelp.SymInitialize(self.hproc, None, True))
        self.mods = Modules(self.hproc)
        self.ctx, self._ctxbuf = aligned_context()
        self.walkctx, self._walkbuf = aligned_context()
        os.makedirs(out, exist_ok=True)
        self.f_sec = open(os.path.join(out, "seconds.jsonl"), "w", encoding="utf-8")
        self.f_stall = open(os.path.join(out, "stalls.jsonl"), "w", encoding="utf-8")
        self.total = {}             # module -> samples over the run
        self.total_n = 0
        self.wait_n = 0
        self.stalls = []            # (ms, module, wait) of every stall
        self.stall_by_mod = {}
        self.suspend_fail = 0

    def close(self):
        if self.sym:
            dbghelp.SymCleanup(self.hproc)
        kernel32.CloseHandle(self.hthread)
        kernel32.CloseHandle(self.hproc)
        self.f_sec.close()
        self.f_stall.close()

    def alive(self):
        code = DWORD(0)
        return kernel32.GetExitCodeProcess(self.hproc, ctypes.byref(code)) and code.value == 259   # STILL_ACTIVE

    def sample(self, walk):
        """One sample: (rip, stack or None). The thread is suspended only for the read (and the walk when asked)."""
        if kernel32.SuspendThread(self.hthread) == 0xFFFFFFFF:
            self.suspend_fail += 1
            return None, None
        try:
            self.ctx.ContextFlags = CONTEXT_CONTROL | CONTEXT_INTEGER
            if not kernel32.GetThreadContext(self.hthread, ctypes.byref(self.ctx)):
                return None, None
            rip = self.ctx.Rip
            stack = self.walk() if walk else None
        finally:
            kernel32.ResumeThread(self.hthread)
        return rip, stack

    def walk(self):
        """The suspended thread's stack as [(module, offset)], the top first (StackWalk64 on a copy of the context)."""
        if not self.sym:
            return None
        ctypes.memmove(ctypes.byref(self.walkctx), ctypes.byref(self.ctx), ctypes.sizeof(CONTEXT))
        fr = STACKFRAME64()
        fr.AddrPC.Offset, fr.AddrPC.Mode = self.walkctx.Rip, ADDR_MODE_FLAT
        fr.AddrFrame.Offset, fr.AddrFrame.Mode = self.walkctx.Rbp, ADDR_MODE_FLAT
        fr.AddrStack.Offset, fr.AddrStack.Mode = self.walkctx.Rsp, ADDR_MODE_FLAT
        out = []
        fta = ctypes.cast(dbghelp.SymFunctionTableAccess64, PVOID)
        gmb = ctypes.cast(dbghelp.SymGetModuleBase64, PVOID)
        for _ in range(MAX_FRAMES):
            if not dbghelp.StackWalk64(IMAGE_FILE_MACHINE_AMD64, self.hproc, self.hthread, ctypes.byref(fr),
                                       ctypes.byref(self.walkctx), None, fta, gmb, None):
                break
            if fr.AddrPC.Offset == 0:
                break
            out.append(self.mods.of(fr.AddrPC.Offset))
        return out

    @staticmethod
    def fmt(stack):
        return [f"{m}+0x{o:x}" for m, o in stack] if stack else []

    def run(self, seconds):
        t_end = time.time() + seconds if seconds else None
        sec_counts, sec_wait, sec_n = {}, 0, 0
        sec_t0 = time.time()
        run_mod, run_lo, run_hi, run_n, run_t0, run_stack, run_wait = None, 0, 0, 0, 0.0, None, False
        i = 0
        next_t = time.perf_counter()
        last_refresh = time.time()
        while True:
            now = time.perf_counter()
            if now < next_t:
                time.sleep(min(self.period, next_t - now))
                continue
            next_t += self.period
            if t_end and time.time() >= t_end:
                break
            if time.time() - last_refresh > 10:
                last_refresh = time.time()
                if not self.alive():
                    break
                self.mods.refresh()
            i += 1
            walk = (i % self.stack_every == 0)
            rip, stack = self.sample(walk)
            if rip is None:
                continue
            mod, off = self.mods.of(rip)
            waiting = mod.lower() in WAIT_MODULES
            # the second's shares
            sec_counts[mod] = sec_counts.get(mod, 0) + 1
            sec_n += 1
            if waiting:
                sec_wait += 1
            self.total[mod] = self.total.get(mod, 0) + 1
            self.total_n += 1
            if waiting:
                self.wait_n += 1
            # a run in one place: the same module within STALL_WINDOW (a wait: the same waiter, by the stack's first
            # frame outside the wait modules when a stack was taken)
            same = run_mod == mod and (waiting or (run_lo - STALL_WINDOW <= off <= run_hi + STALL_WINDOW))
            if same:
                run_n += 1
                run_lo, run_hi = min(run_lo, off), max(run_hi, off)
                if stack and not run_stack:
                    run_stack = stack
                if run_n == self.stall_samples and not run_stack:     # the stall just became one: a stack for it
                    _, run_stack = self.sample(True)
            else:
                if run_n >= self.stall_samples:
                    self.note_stall(run_t0, run_n, run_mod, run_wait, run_stack)
                run_mod, run_lo, run_hi, run_n, run_t0, run_stack, run_wait = mod, off, off, 1, time.time(), stack, waiting
            if time.time() - sec_t0 >= 1.0:
                shares = {k: round(v / sec_n, 3) for k, v in sorted(sec_counts.items(), key=lambda kv: -kv[1])}
                self.f_sec.write(json.dumps({"t": datetime.datetime.now().isoformat(timespec="milliseconds"),
                                             "n": sec_n, "wait": round(sec_wait / max(1, sec_n), 3),
                                             "shares": shares}) + "\n")
                self.f_sec.flush()
                sec_counts, sec_wait, sec_n, sec_t0 = {}, 0, 0, time.time()
        if run_n >= self.stall_samples:
            self.note_stall(run_t0, run_n, run_mod, run_wait, run_stack)

    def note_stall(self, t0, n, mod, waiting, stack):
        ms = round(n * self.period * 1000)
        waiter = None
        if waiting and stack:
            for m, _ in stack:
                if m.lower() not in WAIT_MODULES:
                    waiter = m
                    break
        rec = {"t": datetime.datetime.fromtimestamp(t0).isoformat(timespec="milliseconds"), "ms": ms, "module": mod,
               "wait": waiting, "waiter": waiter, "stack": self.fmt(stack)}
        self.f_stall.write(json.dumps(rec) + "\n")
        self.f_stall.flush()
        key = f"wait in {waiter or mod}" if waiting else mod
        self.stalls.append((ms, key))
        s = self.stall_by_mod.setdefault(key, {"n": 0, "ms": 0, "max": 0})
        s["n"], s["ms"], s["max"] = s["n"] + 1, s["ms"] + ms, max(s["max"], ms)

    def summary(self, seconds):
        shares = {k: round(v / max(1, self.total_n), 4) for k, v in sorted(self.total.items(), key=lambda kv: -kv[1])}
        longest = sorted(self.stalls, reverse=True)[:12]
        return {"pid": self.pid, "tid": self.tid, "seconds": round(seconds, 1), "samples": self.total_n,
                "hz": round(1 / self.period), "wait_share": round(self.wait_n / max(1, self.total_n), 4),
                "shares": shares, "stalls": self.stall_by_mod,
                "longest": [{"ms": ms, "where": k} for ms, k in longest], "suspend_failures": self.suspend_fail,
                "stack_walks": self.sym}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pid", type=int, help="the game's process (default: the largest BeamNG.drive.x64.exe)")
    ap.add_argument("--tid", type=int, help="the thread to sample (default: the game window's)")
    ap.add_argument("--out", default=os.path.join("build", "stallwatch"), help="output folder")
    ap.add_argument("--hz", type=int, default=HZ)
    ap.add_argument("--minutes", type=float, default=0, help="stop after this long (0: with the game or Ctrl-C)")
    ap.add_argument("--seconds", type=float, default=0)
    ap.add_argument("--stall-ms", type=int, default=STALL_MS)
    ap.add_argument("--stack-every", type=int, default=STACK_EVERY)
    args = ap.parse_args()
    if sys.platform != "win32":
        print("Windows only (SuspendThread / GetThreadContext / dbghelp)")
        return 2
    pid, name = find_game(args.pid)
    if not pid:
        print(f"no {GAME_EXE} running")
        return 1
    tid = args.tid or window_thread(pid) or first_thread(pid)
    if not tid:
        print(f"no thread found for pid {pid}")
        return 1
    seconds = args.seconds or args.minutes * 60
    print(f"sampling {name} pid {pid} thread {tid} at {args.hz} Hz"
          f"{f' for {seconds:.0f} s' if seconds else ' until the game ends or Ctrl-C'} -> {args.out}", flush=True)
    s = Sampler(pid, tid, args.out, args.hz, args.stall_ms, args.stack_every)
    t0 = time.time()
    try:
        s.run(seconds)
    except KeyboardInterrupt:
        pass
    finally:
        summary = s.summary(time.time() - t0)
        s.close()
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
