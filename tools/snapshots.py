#!/usr/bin/env python3
"""The drive snapshot catalog: a local web page to go through the snapshots after a drive (snapshot.lua: Ctrl+Alt+S,
the Stream Deck's Snapshot key), add notes, mark regions on the screenshot and issues, and hand them to a thread.

  python tools/snapshots.py                 the catalog at http://127.0.0.1:47710/ (opens the browser)
  python tools/snapshots.py scan            only copy the game's new snapshots to the snapshots folder
  python tools/snapshots.py brief ID        a snapshot's brief (Markdown for a Claude thread): printed, saved as brief.md
  python tools/snapshots.py list            one line per snapshot, newest first

  --home DIR       the snapshots folder (default: C:\\beamng-earth\\snapshots, beside the checkout: server/snapshots.py)
  --user-dir DIR   the game's user folder (default: %LOCALAPPDATA%\\BeamNG\\BeamNG.drive\\current, older layouts too)
  --server-log F   the world server's log, for snapshots the server never saw (default: the launcher's server.log)
  --port N         the page's port (default 47710); --no-open: do not open the browser

The world server copies each snapshot to the folder the moment it is taken (with its log lines); the catalog looks for
new ones every SCAN_S seconds too. In a snapshot's folder the catalog writes review.json (title, notes, status,
regions drawn on the screenshot, issues), annotated.png (the screenshot with the regions) and brief.md.
"""
import argparse
import base64
import glob
import json
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import snapshots as snaps  # noqa: E402

PORT = 47710
SCAN_S = 5.0
WEB = os.path.join(HERE, "snapshots_web")
MAX_BODY = 24 << 20
SERVE = {"screenshot.jpg": "image/jpeg", "annotated.png": "image/png"}
TEXTS = ("beamng.log.txt", "earthd.log.txt")
STATUSES = ("new", "look", "issue", "ok", "handed")
FILES = (("snapshot.json", "all the numbers"), ("screenshot.jpg", "what the player saw"),
         ("annotated.png", "the regions marked"), ("beamng.log.txt", "the game's log, the minute before and 5 s after"),
         ("earthd.log.txt", "the world server's log, the same window"), ("build.json", "the server's commit"),
         ("review.json", "the notes and issues"))


def default_user_dirs():
    out = []
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        out.append(os.path.join(base, "BeamNG", "BeamNG.drive", "current"))
        out += sorted(glob.glob(os.path.join(base, "BeamNG.drive", "*")), reverse=True)
    else:
        out.append(os.path.join(os.path.expanduser("~"), ".local", "share", "BeamNG", "BeamNG.drive", "current"))
    return [d for d in out if os.path.isdir(d)]


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_atomic(path, data, mode="w"):
    tmp = path + ".tmp"
    with open(tmp, mode, **({"encoding": "utf-8", "newline": "\n"} if mode == "w" else {})) as f:
        f.write(data)
    os.replace(tmp, path)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def summary(home, sid):
    """The list's row for one snapshot (the page's cards)."""
    d = os.path.join(home, sid)
    s = read_json(os.path.join(d, "snapshot.json"), {}) or {}
    r = read_json(os.path.join(d, "review.json"), {}) or {}
    veh = s.get("vehicle") if isinstance(s.get("vehicle"), dict) else {}
    fr = s.get("frames") if isinstance(s.get("frames"), dict) else {}
    where = s.get("where") if isinstance(s.get("where"), dict) else {}
    traffic = s.get("traffic") if isinstance(s.get("traffic"), dict) else {}
    tod = s.get("tod") if isinstance(s.get("tod"), dict) else {}
    issues = r.get("issues") if isinstance(r.get("issues"), list) else []
    return {
        "id": sid, "time": s.get("time"), "unix": _num(s.get("unix")), "lat": _num(s.get("lat")),
        "lon": _num(s.get("lon")), "street": where.get("street"), "place": where.get("place"),
        "speedMph": _num(veh.get("speedMph")), "headingDeg": _num(veh.get("headingDeg")),
        "fps": _num(fr.get("fps")), "p99": _num(fr.get("p99")), "maxMs": _num(fr.get("maxMs")),
        "hitches": len(fr.get("hitches") or []) if isinstance(fr.get("hitches"), list) else 0,
        "cars": len(traffic.get("cars") or []) if isinstance(traffic.get("cars"), list) else 0,
        "clock": tod.get("clock") or (tod.get("sky") if isinstance(tod.get("sky"), dict) else {}).get("localTime"),
        "gameNote": s.get("note") or "", "title": r.get("title") or "", "notes": r.get("notes") or "",
        "status": r.get("status") or "new", "tags": r.get("tags") or [],
        "issues": len(issues), "openIssues": sum(1 for i in issues if isinstance(i, dict) and not i.get("done")),
        "shapes": len(r.get("shapes") or []) if isinstance(r.get("shapes"), list) else 0,
        "screenshot": os.path.isfile(os.path.join(d, "screenshot.jpg")),
        "annotated": os.path.isfile(os.path.join(d, "annotated.png")),
        "folder": d.replace("\\", "/"),
    }


def ids(home):
    try:
        names = os.listdir(home)
    except OSError:
        return []
    return sorted((n for n in names if snaps.ID_RE.match(n) and os.path.isfile(os.path.join(home, n, "snapshot.json"))),
                  key=lambda n: (read_json(os.path.join(home, n, "snapshot.json"), {}) or {}).get("unix") or 0,
                  reverse=True)


def detail(home, sid):
    d = os.path.join(home, sid)
    out = {"summary": summary(home, sid), "snapshot": read_json(os.path.join(d, "snapshot.json"), {}),
           "review": read_json(os.path.join(d, "review.json"), {}) or {},
           "build": read_json(os.path.join(d, "build.json"), None), "logs": {}}
    for name in TEXTS:
        try:
            with open(os.path.join(d, name), encoding="utf-8", errors="replace") as f:
                out["logs"][name] = f.read()
        except OSError:
            out["logs"][name] = None
    return out


def _fmt(v, spec, unit=""):
    return (format(v, spec) + unit) if isinstance(v, (int, float)) else "?"


def brief(home, sid):
    """Markdown about one snapshot for a Claude thread: what the user wrote and marked, the numbers, the files."""
    det = detail(home, sid)
    s, r, sm, b = det["snapshot"] or {}, det["review"], det["summary"], det["build"] or {}
    veh = s.get("vehicle") or {}
    fr = s.get("frames") or {}
    lines = [f"# Drive snapshot {sid}" + (f": {r.get('title')}" if r.get("title") else ""), ""]
    lines.append(f"- **When:** {s.get('time')} (UTC{s.get('utcOffset') or '?'}), in-game time {sm.get('clock') or '?'}")
    place = ", ".join(x for x in (sm.get("street"), sm.get("place")) if x)
    if sm.get("lat") is not None:
        lines.append(f"- **Where:** {place + ' ' if place else ''}({sm['lat']:.6f}, {sm['lon']:.6f}), "
                     f"game position {_pos(veh.get('pos'))}, heading {_fmt(veh.get('headingDeg'), '.0f', ' deg')}, "
                     f"{_fmt(veh.get('speedMph'), '.0f', ' mph')}")
    lines.append(f"- **Frames (last {_fmt(fr.get('seconds'), '.0f', ' s')}):** {_fmt(fr.get('fps'), '.0f')} fps, "
                 f"p50 {_fmt(fr.get('p50'), '.1f')} / p95 {_fmt(fr.get('p95'), '.1f')} / p99 {_fmt(fr.get('p99'), '.1f')}"
                 f" / max {_fmt(fr.get('maxMs'), '.0f')} ms, {sm['hitches']} hitch{'' if sm['hitches'] == 1 else 'es'} of {fr.get('hitchMs') or 40} ms or more")
    nav = ((s.get("traffic") or {}).get("navgraph")) or {}
    if nav:
        keys = ("nodes", "edges", "routesKept", "routesDropped", "cutRespawns", "aheadRespawns", "mergeAssists")
        lines.append("- **Navgraph:** " + ", ".join(f"{k} {nav[k]}" for k in keys if k in nav))
    cars = (s.get("traffic") or {}).get("cars") or []
    if cars:
        lines.append(f"- **Traffic near ({len(cars)}):** " + "; ".join(
            f"#{c.get('id')} {_fmt(c.get('distM'), '.0f', ' m')} {_fmt(c.get('speedMph'), '.0f', ' mph')} "
            f"{(c.get('ai') or {}).get('mode') or c.get('state') or ''}".strip() for c in cars[:8]))
    if b:
        lines.append(f"- **Build:** {b.get('branch') or '?'} @ {b.get('short') or '?'}"
                     f"{' (uncommitted changes)' if b.get('dirty') else ''}, mod {s.get('modVersion') or '?'}")
    lines.append(f"- **Status:** {r.get('status') or 'new'}" + (f", tags {', '.join(r['tags'])}" if r.get("tags") else ""))
    if s.get("note"):
        lines += ["", "**Note in the game:** " + s["note"]]
    if r.get("notes"):
        lines += ["", "**Notes:**", "", r["notes"]]
    issues = [i for i in (r.get("issues") or []) if isinstance(i, dict)]
    if issues:
        lines += ["", "**Issues:**", ""]
        shapes = {sh.get("id"): n for n, sh in enumerate(r.get("shapes") or [], 1) if isinstance(sh, dict)}
        for i in issues:
            mark = f" (region {shapes[i['shape']]})" if i.get("shape") in shapes else ""
            lines.append(f"- [{'x' if i.get('done') else ' '}] **{i.get('area') or 'issue'}**"
                         f"{' / ' + i['severity'] if i.get('severity') else ''}: {i.get('title') or ''}{mark}"
                         + (f" -> {i['thread']}" if i.get("thread") else ""))
    folder = sm["folder"]
    have = [f"{f} ({why})" for f, why in FILES if os.path.isfile(os.path.join(home, sid, f))]
    lines += ["", f"Files in `{folder}/`: " + ", ".join(have) + "."]
    return "\n".join(lines) + "\n"


def _pos(p):
    if isinstance(p, list) and len(p) >= 3 and all(isinstance(x, (int, float)) for x in p[:3]):
        return "(%.1f, %.1f, %.1f)" % tuple(p[:3])
    return "?"


def clean_review(r):
    """Only the review's known fields, of the right types (the page sends it)."""
    if not isinstance(r, dict):
        raise ValueError("not an object")
    out = {"title": str(r.get("title") or "")[:300], "notes": str(r.get("notes") or "")[:20000],
           "status": r.get("status") if r.get("status") in STATUSES else "new",
           "tags": [str(t)[:40] for t in (r.get("tags") or []) if isinstance(t, str)][:30],
           "shapes": [], "issues": [], "updated": time.time()}
    for sh in (r.get("shapes") or [])[:200]:
        if isinstance(sh, dict) and sh.get("kind") in ("rect", "ellipse", "arrow", "pen", "highlight"):
            pts = [[float(p[0]), float(p[1])] for p in (sh.get("points") or [])[:2000]
                   if isinstance(p, list) and len(p) == 2 and all(isinstance(x, (int, float)) for x in p)]
            out["shapes"].append({"id": str(sh.get("id") or "")[:40], "kind": sh["kind"], "points": pts,
                                  "color": str(sh.get("color") or "#ff3b30")[:20]})
    for i in (r.get("issues") or [])[:100]:
        if isinstance(i, dict):
            out["issues"].append({"id": str(i.get("id") or "")[:40], "title": str(i.get("title") or "")[:500],
                                  "area": str(i.get("area") or "")[:30], "severity": str(i.get("severity") or "")[:10],
                                  "shape": str(i.get("shape") or "")[:40] or None, "thread": str(i.get("thread") or "")[:300],
                                  "done": bool(i.get("done"))})
    return out


class Catalog:
    def __init__(self, home, user_dirs, server_log):
        self.home, self.user_dirs, self.server_log = home, user_dirs, server_log
        self.build = snaps.build_info()
        self.version = 0            # goes up when a scan brings something new (the page reloads its list)
        self.last_scan = None

    def scan(self):
        try:
            got = snaps.scan(self.user_dirs, dest_root=self.home, server_log=self.server_log, build=None)
        except OSError as e:
            print("scan:", e)
            got = []
        self.last_scan = time.time()
        if got:
            self.version += 1
            print("new or changed:", ", ".join(got))
        return got

    def loop(self):
        while True:
            self.scan()
            time.sleep(SCAN_S)


def make_handler(cat):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400" if ctype == "image/jpeg" else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def sid(self, s):
            s = unquote(s)
            if not snaps.ID_RE.match(s) or not os.path.isdir(os.path.join(cat.home, s)):
                return None
            return s

        def local_only(self):
            # (a page on another site cannot post here: the browser sends its Origin)
            origin = self.headers.get("Origin")
            if origin and not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", origin):
                self.send(403, {"error": "not from this page"})
                return False
            return True

        def do_GET(self):
            path = urlparse(self.path).path
            parts = [p for p in path.split("/") if p]
            if not parts or parts == ["index.html"]:
                with open(os.path.join(WEB, "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html; charset=utf-8")
            if parts == ["api", "snapshots"]:
                return self.send(200, {"home": cat.home.replace("\\", "/"), "version": cat.version,
                                       "lastScan": cat.last_scan, "userDirs": cat.user_dirs,
                                       "snapshots": [summary(cat.home, i) for i in ids(cat.home)]})
            if parts == ["api", "version"]:
                return self.send(200, {"version": cat.version})
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "snapshots":
                sid = self.sid(parts[2])
                return self.send(200, detail(cat.home, sid)) if sid else self.send(404, {"error": "no such snapshot"})
            if len(parts) == 4 and parts[:2] == ["api", "snapshots"] and parts[3] == "brief":
                sid = self.sid(parts[2])
                return self.send(200, brief(cat.home, sid), "text/markdown; charset=utf-8") if sid else self.send(404, "")
            if len(parts) == 3 and parts[0] == "files" and parts[2] in SERVE:
                sid = self.sid(parts[1])
                p = sid and os.path.join(cat.home, sid, parts[2])
                if p and os.path.isfile(p):
                    with open(p, "rb") as f:
                        return self.send(200, f.read(), SERVE[parts[2]])
            return self.send(404, {"error": "not found"})

        def do_POST(self):
            if not self.local_only():
                return
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self.send(413, {"error": "too big"})
            body = self.rfile.read(n)
            if parts == ["api", "scan"]:
                return self.send(200, {"new": cat.scan(), "version": cat.version})
            if len(parts) != 4 or parts[:2] != ["api", "snapshots"]:
                return self.send(404, {"error": "not found"})
            sid = self.sid(parts[2])
            if not sid:
                return self.send(404, {"error": "no such snapshot"})
            d = os.path.join(cat.home, sid)
            try:
                if parts[3] == "review":
                    r = clean_review(json.loads(body or b"{}"))
                    write_atomic(os.path.join(d, "review.json"), json.dumps(r, indent=1))
                    return self.send(200, {"ok": True, "updated": r["updated"], "summary": summary(cat.home, sid)})
                if parts[3] == "annotated":
                    m = re.match(rb"^data:image/png;base64,(.*)$", body, re.S)
                    if not m:
                        return self.send(400, {"error": "a PNG data URL"})
                    write_atomic(os.path.join(d, "annotated.png"), base64.b64decode(m.group(1)), "wb")
                    return self.send(200, {"ok": True})
                if parts[3] == "brief":
                    text = brief(cat.home, sid)
                    write_atomic(os.path.join(d, "brief.md"), text)
                    return self.send(200, {"ok": True, "text": text, "path": os.path.join(d, "brief.md").replace("\\", "/")})
                if parts[3] == "open":
                    if hasattr(os, "startfile"):
                        os.startfile(d)  # noqa: S606 (the user's own folder, on this machine)
                    return self.send(200, {"ok": True})
            except (ValueError, OSError) as e:
                return self.send(400, {"error": str(e)})
            return self.send(404, {"error": "not found"})
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", nargs="?", default="serve", choices=("serve", "scan", "brief", "list"))
    ap.add_argument("id", nargs="?")
    ap.add_argument("--home", default=snaps.home())
    ap.add_argument("--user-dir", action="append")
    ap.add_argument("--server-log", default=snaps.default_server_log())
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.home, exist_ok=True)
    cat = Catalog(a.home, a.user_dir or default_user_dirs(), a.server_log if os.path.isfile(a.server_log) else None)
    if a.cmd == "scan":
        got = cat.scan()
        print(f"{len(got)} new or changed in {a.home}")
        return 0
    if a.cmd == "list":
        for i in ids(a.home):
            s = summary(a.home, i)
            print(f"{i}  {s['status']:<6} {s['street'] or ''} {s['place'] or ''}  {s['title'] or s['gameNote']}")
        return 0
    if a.cmd == "brief":
        sid = a.id or (ids(a.home) or [None])[0]
        if not sid or not os.path.isdir(os.path.join(a.home, sid)):
            print("no such snapshot:", sid)
            return 1
        text = brief(a.home, sid)
        write_atomic(os.path.join(a.home, sid, "brief.md"), text)
        print(text)
        return 0
    cat.scan()
    threading.Thread(target=cat.loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(cat))
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Snapshot catalog: {url}  (folder {a.home}; game snapshots from {', '.join(cat.user_dirs) or 'nowhere'})")
    if not a.no_open:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
