"""Drive snapshots kept for good: the mod's one-press snapshots (snapshot.lua) with the logs of that moment.

The game writes <user folder>/beamngEarth/snapshots/<id>/ (snapshot.json, screenshot.jpg) and tells the server (message
"snapshot"). ingest() copies the folder to the snapshots home (home(): C:\\beamng-earth\\snapshots on the user's PC,
beside the checkout) and adds what only this side can see:

  earthd.log.txt   the world server's log lines from BEFORE_S before the moment to AFTER_S after (LogRing: kept in
                   memory, wherever the log itself goes; the catalog reads the log file instead: server_log_window)
  beamng.log.txt   the game's log lines of the same window (beamng.log's times are seconds since the game started;
                   its first lines carry the start's wall time; older sessions are beamng.1.log, .2.log, ...)
  build.json       the server checkout's commit, branch and whether it had uncommitted changes, the version

review.json in a snapshot is the catalog's (tools/snapshots.py: notes, regions drawn on the screenshot, issues) and is
never written here.
"""
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque

log = logging.getLogger("snapshots")

BEFORE_S = 60.0
AFTER_S = 5.0
RING = 6000                 # server log lines kept in memory (a busy minute is ~1000)
SCREENSHOT_WAIT_S = 6.0     # the game writes the screenshot a frame or more after the snapshot
TAIL_BYTES = 24 << 20       # at most this much of a log is read (the end)
GAME_SUBDIR = os.path.join("beamngEarth", "snapshots")
CATALOG_FILES = {"review.json", "annotated.png", "brief.md"}
ID_RE = re.compile(r"^[0-9A-Za-z_\-]{1,80}$")
FILE_RE = re.compile(r"^[0-9A-Za-z_\-]{1,60}\.(json|jpg|png|txt|md)$")
NO_LINES = "(no lines of %s from this moment: that log was gone or the server was not running)\n"
_lock = threading.Lock()


def home():
    """The snapshots folder: BNGE_SNAPSHOTS_DIR, else 'snapshots' beside a git checkout (C:\\beamng-earth\\snapshots for
    C:\\beamng-earth\\BeamNG.Earth or any worktree next to it), else the app's data folder."""
    env = os.environ.get("BNGE_SNAPSHOTS_DIR")
    if env:
        return env
    checkout = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(checkout, ".git")):
        return os.path.join(os.path.dirname(checkout), "snapshots")
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "beamng-earth", "snapshots")
    return os.path.join(os.path.expanduser("~"), ".cache", "beamng-earth", "snapshots")


class LogRing(logging.Handler):
    """The last RING log lines with their times (the server's own log, for a snapshot's earthd.log.txt)."""

    def __init__(self, n=RING):
        super().__init__()
        self.lines = deque(maxlen=n)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record):
        try:
            self.lines.append((record.created, self.format(record)))
        except Exception:       # a log handler never raises
            pass

    def window(self, t, before=BEFORE_S, after=AFTER_S):
        return [s for at, s in list(self.lines) if t - before <= at <= t + after]


_ring = None
_build = None


def install(server_version=None):
    """At the server's start: the log ring on the root logger, and the build (the checkout as it was at the start,
    which is the code running)."""
    global _ring, _build
    if _ring is None:
        _ring = LogRing()
        logging.getLogger().addHandler(_ring)
    if _build is None:
        _build = build_info(server_version=server_version)
    return _ring


def build_info(checkout=None, server_version=None):
    """{checkout, commit, short, branch, subject, dirty, version, serverVersion}: what git can tell (fields left out
    when it cannot)."""
    checkout = checkout or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    info = {"checkout": checkout.replace("\\", "/"), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if server_version:
        info["serverVersion"] = server_version

    def git(*a):
        try:
            r = subprocess.run(["git", "-C", checkout, *a], capture_output=True, text=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    if commit:
        info["commit"], info["short"] = commit, commit[:8]
        info["branch"] = git("rev-parse", "--abbrev-ref", "HEAD")
        info["subject"] = git("log", "-1", "--format=%s")
        dirty = git("status", "--porcelain", "--untracked-files=no")
        if dirty is not None:
            info["dirty"] = bool(dirty)
    try:
        with open(os.path.join(checkout, "VERSION"), encoding="utf-8") as f:
            info["version"] = f.read().strip()
    except OSError:
        pass
    return info


def _tail(path, max_bytes=TAIL_BYTES):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()                    # (a partial line)
        return f.read().decode("utf-8", "replace").splitlines()


BNG_START = re.compile(r"Log started .*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ([+-]\d{4})")
BNG_LINE = re.compile(r"^\s*(\d+\.\d+)\|")


def game_log_start(path):
    """The wall time (unix s) a beamng.log's session started, from its first lines; None if not found."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096).decode("utf-8", "replace")
    except OSError:
        return None
    m = BNG_START.search(head)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S %z").timestamp()
    except ValueError:
        return None


def game_log_window(user_dir, t, before=BEFORE_S, after=AFTER_S):
    """beamng.log's lines from t - before to t + after (t: unix s), from the log of the game session running at t
    (beamng.log or an older beamng.N.log); [] when none has it. A line without a time (a stack trace's) goes with the
    line before it."""
    best = None
    try:
        names = os.listdir(user_dir)
    except OSError:
        return []
    for name in names:
        if re.fullmatch(r"beamng(\.\d+)?\.log", name):
            p = os.path.join(user_dir, name)
            s = game_log_start(p)
            if s is not None and s <= t + 1 and (best is None or s > best[0]):
                best = (s, p)
    if best is None:
        return []
    start, path = best
    lo, hi = t - before - start, t + after - start
    stamp = datetime.datetime.fromtimestamp(start).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    out, keep = [f"({os.path.basename(path)}: the game started {stamp}; the snapshot is at {t - start:.1f} s)"], False
    for line in _tail(path):
        m = BNG_LINE.match(line)
        if m:
            ts = float(m.group(1))
            if ts > hi:
                break
            keep = ts >= lo
        if keep:
            out.append(line)
    return out if len(out) > 1 else []


SERVER_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) ")


def server_log_window(path, t, before=BEFORE_S, after=AFTER_S):
    """A server log file's lines (logging's asctime, local time) from t - before to t + after."""
    try:
        lines = _tail(path)
    except OSError:
        return []
    out, keep = [], False
    for line in lines:
        m = SERVER_LINE.match(line)
        if m:
            try:
                at = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp() + int(m.group(2)) / 1000
            except ValueError:
                at = None
            if at is not None:
                if at > t + after:
                    break
                keep = at >= t - before
        if keep:
            out.append(line)
    return out


def default_server_log():
    """The launcher's server log (launcher.py: <data folder>/logs/server.log)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "beamng-earth", "logs", "server.log")
    return os.path.join(os.path.expanduser("~"), ".cache", "beamng-earth", "logs", "server.log")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def ingest(src, dest_root=None, server_lines=None, server_log=None, build=None, wait=False):
    """Copy one game snapshot folder to the snapshots home (again: only what changed, the note added later); add the
    log lines of its moment once they are all written (AFTER_S after it) and the build. Returns the destination
    folder, or None if src is not a snapshot.

    server_lines: the server's own lines (the running server's LogRing); else server_log, a log file, is read.
    wait: the server, right after the game's message: wait for the screenshot and for the AFTER_S of log lines."""
    src = os.path.normpath(src)
    name = os.path.basename(src)
    snap = _read_json(os.path.join(src, "snapshot.json"))
    if not ID_RE.match(name) or not isinstance(snap, dict):
        return None
    t = snap.get("unix") if isinstance(snap.get("unix"), (int, float)) else None
    if wait:
        deadline = time.time() + SCREENSHOT_WAIT_S
        while snap.get("screenshot") and not os.path.isfile(os.path.join(src, "screenshot.jpg")) \
                and time.time() < deadline:
            time.sleep(0.25)
        if t is not None:
            time.sleep(max(0.0, min(AFTER_S + 1.0, t + AFTER_S + 1.0 - time.time())))
    dest = os.path.join(dest_root or home(), name)
    with _lock:
        os.makedirs(dest, exist_ok=True)
        for f in os.listdir(src):
            s, d = os.path.join(src, f), os.path.join(dest, f)
            if f in CATALOG_FILES or not FILE_RE.match(f) or not os.path.isfile(s):
                continue
            try:
                if not os.path.exists(d) or os.path.getsize(s) != os.path.getsize(d) \
                        or os.path.getmtime(s) > os.path.getmtime(d) + 0.5:
                    shutil.copy2(s, d)
            except OSError as e:            # (the game still writing the screenshot: the next ingest copies it)
                log.info("snapshot %s: %s not copied yet (%s)", name, f, e)
        complete = t is not None and time.time() >= t + AFTER_S
        if t is not None and complete:
            user_dir = snap.get("userDir")
            game_path = os.path.join(dest, "beamng.log.txt")
            if user_dir and not os.path.exists(game_path):
                lines = game_log_window(user_dir, t)
                _write_text(game_path, "\n".join(lines) + "\n" if lines else NO_LINES % "beamng.log")
            srv_path = os.path.join(dest, "earthd.log.txt")
            if not os.path.exists(srv_path) and (server_lines is not None or server_log):
                lines = server_lines if server_lines is not None else server_log_window(server_log, t)
                _write_text(srv_path, "\n".join(lines) + "\n" if lines else NO_LINES % "the server's log")
        build_path = os.path.join(dest, "build.json")
        if build and not os.path.exists(build_path):
            _write_text(build_path, json.dumps(build, indent=1))
    return dest


def ingest_message(msg, ring=None, build=None, dest_root=None):
    """The server's "snapshot" message: {folder, id, unix, userDir}. Blocking (a thread): waits for the screenshot
    and the log lines after the moment."""
    folder = msg.get("folder")
    if not isinstance(folder, str) or not folder:
        return None
    parent = os.path.normpath(os.path.dirname(os.path.normpath(folder)))
    if not parent.replace("\\", "/").lower().endswith("beamngearth/snapshots"):
        log.info("snapshot: not a snapshot folder: %r", folder)
        return None
    t = msg.get("unix") if isinstance(msg.get("unix"), (int, float)) else time.time()
    ring = ring or _ring
    if ring is not None:
        time.sleep(max(0.0, min(AFTER_S + 1.0, t + AFTER_S + 1.0 - time.time())))
    lines = ring.window(t) if ring is not None else None
    dest = ingest(folder, dest_root=dest_root, server_lines=lines, build=build or _build, wait=True)
    if dest:
        log.info("snapshot kept: %s", dest)
    return dest


def game_dirs(user_dirs):
    """The game's snapshot folders in these user folders (newest first)."""
    out = []
    for u in user_dirs:
        root = os.path.join(u, GAME_SUBDIR)
        try:
            names = os.listdir(root)
        except OSError:
            continue
        out.extend(os.path.join(root, n) for n in names if ID_RE.match(n) and os.path.isdir(os.path.join(root, n)))
    return sorted(out, key=os.path.basename, reverse=True)


def scan(user_dirs, dest_root=None, server_log=None, build=None):
    """Ingest every game snapshot that is new or changed since its copy (the catalog, for the ones the server never
    saw or that got a note later). Returns the ids ingested."""
    dest_root = dest_root or home()
    done = []
    for src in game_dirs(user_dirs):
        name = os.path.basename(src)
        dest = os.path.join(dest_root, name)
        stale = not os.path.isdir(dest)
        if not stale:
            for f in os.listdir(src):
                s, d = os.path.join(src, f), os.path.join(dest, f)
                if FILE_RE.match(f) and f not in CATALOG_FILES and os.path.isfile(s) and (
                        not os.path.exists(d) or os.path.getmtime(s) > os.path.getmtime(d) + 0.5):
                    stale = True
                    break
            if not stale and not os.path.exists(os.path.join(dest, "beamng.log.txt")):
                stale = True            # (its logs not added yet: the moment was too recent)
        if stale and ingest(src, dest_root=dest_root, server_log=server_log, build=build):
            done.append(name)
    return done
