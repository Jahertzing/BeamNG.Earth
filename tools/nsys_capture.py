#!/usr/bin/env python3
"""An Nsight Systems capture of a drive: the D3D12 API calls and the WDDM GPU queues with the CPU threads sampled, for
the stalls neither the mod's nor the engine's timers see (2026-10-29: the FramePresent-led 50-100 ms frames, 45 % of
Jeff's stutters, and whatever is left in a block swap frame). Jeff agreed to the install; the nsys CLI runs unattended.

nsys can only launch a process (no attach), so the game is started under it in a session, collection is started when
the lap begins and stopped after it, and the game lives on:

  python tools\\nsys_capture.py launch                     start the game under nsys (the world server already running:
                                                          launcher.py --no-game, or the builder's usual start)
  python tools\\nsys_capture.py start --minutes 4         begin collecting (teleported, driving)
  python tools\\nsys_capture.py stop --out C:\\bng\\lap1    end it, write lap1.nsys-rep, then the stats and the SQLite
  python tools\\nsys_capture.py report C:\\bng\\lap1.nsys-rep   the stats and the SQLite of an existing capture

  python tools\\nsys_capture.py profile --minutes 6 --out C:\\bng\\lap1    one shot: launch, collect, kill at the end

What it asks nsys for: --trace=dx12,wddm,nvtx (the D3D12 and DXGI calls with their GPU work, the WDDM queue packets:
when the GPU ran what, and how long a Present waited), --sample=process-tree and --cpuctxsw=process-tree (the game's
threads sampled; the context switches need an admin shell on Windows: without one nsys says so and the sampling is
dropped). The report step runs every nsys stats report that applies (nsys stats --help-reports lists them) and
exports the SQLite that tools/nsys_report.py reads.

The game's folder comes from the launcher (launcher.windows_game_dir), the nsys binary from Program Files
(NSYS_EXE overrides). Windows only.
"""
import argparse
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SESSION = "bngearth"
TRACE = "dx12,wddm,nvtx"


def nsys_exe():
    if os.environ.get("NSYS_EXE"):
        return os.environ["NSYS_EXE"]
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"):
        hits = sorted(glob.glob(os.path.join(pf, "NVIDIA Corporation", "Nsight Systems*", "target-windows-x64", "nsys.exe")))
        if hits:
            return hits[-1]
    found = __import__("shutil").which("nsys")
    if found:
        return found
    raise SystemExit("nsys.exe not found: install Nsight Systems (developer.nvidia.com/nsight-systems) or set NSYS_EXE")


def game_dir():
    import launcher
    d = launcher.windows_game_dir()
    if not d:
        raise SystemExit("the game's folder was not found (launcher.windows_game_dir)")
    return d


def run(cmd, cwd=None, check=True):
    print("> " + " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    p = subprocess.run(cmd, cwd=cwd)
    if check and p.returncode != 0:
        raise SystemExit(f"nsys returned {p.returncode}")
    return p.returncode


def cmd_launch(args):
    nsys, d = nsys_exe(), game_dir()
    exe = os.path.join(d, "BeamNG.drive.exe")
    cmd = [nsys, "launch", f"--session-new={args.session}", f"--trace={TRACE}", "--sample=process-tree",
           "--cpuctxsw=process-tree", exe] + args.game_args
    run(cmd, cwd=d)
    print("the game is starting under nsys; when the lap begins: nsys_capture.py start")


def cmd_start(args):
    nsys = nsys_exe()
    cmd = [nsys, "start", f"--session={args.session}"]
    if args.minutes:
        cmd += [f"--duration={int(args.minutes * 60)}"]
    run(cmd)
    print("collecting" + (f" for {args.minutes:g} min" if args.minutes else " until nsys_capture.py stop"))


def cmd_stop(args):
    nsys = nsys_exe()
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    run([nsys, "stop", f"--session={args.session}", "--force-overwrite=true", f"--output={out}"])
    rep = out if out.endswith(".nsys-rep") else out + ".nsys-rep"
    report(nsys, rep)


def cmd_shutdown(args):
    run([nsys_exe(), "shutdown", f"--session={args.session}", "--kill=none"], check=False)


def cmd_profile(args):
    """One shot: launch the game under nsys, collect for --minutes from the start, kill it at the end."""
    nsys, d = nsys_exe(), game_dir()
    exe = os.path.join(d, "BeamNG.drive.exe")
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    cmd = [nsys, "profile", f"--trace={TRACE}", "--sample=process-tree", "--cpuctxsw=process-tree",
           f"--duration={int(args.minutes * 60)}", "--wait=all", "--force-overwrite=true", f"--output={out}",
           exe] + args.game_args
    run(cmd, cwd=d)
    rep = out if out.endswith(".nsys-rep") else out + ".nsys-rep"
    report(nsys, rep)


def available_reports(nsys):
    p = subprocess.run([nsys, "stats", "--help-reports"], capture_output=True, text=True, errors="replace")
    names = []
    for line in (p.stdout or "").splitlines():
        line = line.strip()
        if line and not line.startswith("-") and " " in line and line.split()[0].replace("_", "").isalnum():
            names.append(line.split()[0])
    return names


WANT = ("dx12", "wddm", "osrt", "nvtx", "gpu", "sampl", "cpu")


def report(nsys, rep):
    """The stats reports that apply to this capture (CSV beside it) and the SQLite export."""
    if not os.path.isfile(rep):
        raise SystemExit(f"no capture at {rep}")
    base = rep[:-len(".nsys-rep")] if rep.endswith(".nsys-rep") else rep
    names = [n for n in available_reports(nsys) if any(w in n.lower() for w in WANT)]
    if names:
        run([nsys, "stats", "--report=" + ",".join(names), "--format=csv", "--force-overwrite=true",
             f"--output={base}", rep], check=False)
    else:
        print("nsys stats --help-reports listed no report to run; the SQLite export alone")
    sqlite = base + ".sqlite"
    run([nsys, "export", "--type=sqlite", "--force-overwrite=true", f"--output={sqlite}", rep], check=False)
    if os.path.isfile(sqlite):
        try:
            import nsys_report
            nsys_report.summarize(sqlite, base + ".summary.json")
        except Exception as e:      # the export's tables differ by version: the summary is best effort
            print(f"nsys_report: {e}")
    print(f"capture: {rep}\nstats: {base}_*.csv\nsqlite: {sqlite}")


def cmd_report(args):
    report(nsys_exe(), os.path.abspath(args.rep))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("launch", "start", "stop", "shutdown", "profile", "report"):
        s = sub.add_parser(name)
        s.add_argument("--session", default=SESSION)
        if name in ("launch", "profile"):
            s.add_argument("game_args", nargs="*", help="the game's own options (after --)")
        if name in ("start", "profile"):
            s.add_argument("--minutes", type=float, default=0 if name == "start" else 6)
        if name in ("stop", "profile"):
            s.add_argument("--out", required=True, help="the capture's path (.nsys-rep added)")
        if name == "report":
            s.add_argument("rep", help="an existing .nsys-rep")
    args = ap.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Windows only")
    {"launch": cmd_launch, "start": cmd_start, "stop": cmd_stop, "shutdown": cmd_shutdown, "profile": cmd_profile,
     "report": cmd_report}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
