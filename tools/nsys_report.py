#!/usr/bin/env python3
"""What an Nsight Systems capture of a drive says about the stalls, from its SQLite export (tools/nsys_capture.py):
the longest GPU queue packets (WDDM: the GPU busy on one submission), the longest D3D12 / DXGI calls on any thread
(a Present that waited, a pipeline state created, a resource mapped), the busiest frames by API time, and the CPU
sampling's module shares for the busiest thread, when the capture has them. The export's tables differ between
Nsight versions, so every part looks for its tables first and says what it did not find.

  python tools\\nsys_report.py C:\\bng\\lap1.sqlite             prints the summary, writes lap1.summary.json
  python tools\\nsys_report.py C:\\bng\\lap1.sqlite --tables    the tables and their row counts (what this version exports)
"""
import argparse
import json
import os
import sqlite3
import sys

LONG_NS = 10_000_000        # a call or packet of 10 ms or more is listed


def tables(con):
    return {r[0]: None for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


def columns(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info('{table}')")]


def strings(con):
    """StringIds -> text, when the export has them."""
    if "StringIds" not in tables(con):
        return {}
    return {r[0]: r[1] for r in con.execute("SELECT id, value FROM StringIds")}


def longest_spans(con, table, label_col, n=20):
    """The n longest rows of a table with start/end columns: (ms, label, tid)."""
    cols = columns(con, table)
    if "start" not in cols or "end" not in cols:
        return []
    tid = "globalTid" if "globalTid" in cols else ("tid" if "tid" in cols else "NULL")
    lab = label_col if label_col in cols else "NULL"
    rows = con.execute(f"SELECT (end - start) AS d, {lab}, {tid} FROM '{table}' WHERE end > start "
                       f"ORDER BY d DESC LIMIT {n}").fetchall()
    return [(r[0] / 1e6, r[1], r[2]) for r in rows]


def count_over(con, table, ns):
    cols = columns(con, table)
    if "start" not in cols or "end" not in cols:
        return 0
    return con.execute(f"SELECT COUNT(*) FROM '{table}' WHERE end - start >= {ns}").fetchone()[0]


def summarize(sqlite, out_json=None):
    con = sqlite3.connect(sqlite)
    names = tables(con)
    ids = strings(con)
    rep = {"sqlite": sqlite, "tables": {}, "gpu_packets": None, "api_calls": None, "sampling": None, "missing": []}
    for t in names:
        try:
            rep["tables"][t] = con.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
        except sqlite3.Error:
            rep["tables"][t] = None
    # WDDM queue packets: the GPU's work per submission
    wddm = [t for t in names if "WDDM" in t.upper() and ("PACKET" in t.upper() or "QUEUE" in t.upper())]
    if wddm:
        packets = {}
        for t in wddm:
            spans = longest_spans(con, t, "packetType" if "packetType" in columns(con, t) else "context")
            packets[t] = {"over10ms": count_over(con, t, LONG_NS),
                          "longest": [{"ms": round(ms, 1), "label": str(lab), "tid": tid} for ms, lab, tid in spans]}
        rep["gpu_packets"] = packets
    else:
        rep["missing"].append("WDDM queue packets (no WDDM_* table: was --trace=wddm on, and is the capture a GPU frame or more?)")
    # D3D12 / DXGI API calls
    api = [t for t in names if "DX12" in t.upper() or "DXGI" in t.upper() or "D3D12" in t.upper()]
    if api:
        calls = {}
        for t in api:
            cols = columns(con, t)
            lab = "nameId" if "nameId" in cols else ("name" if "name" in cols else None)
            spans = longest_spans(con, t, lab or "NULL")
            calls[t] = {"over10ms": count_over(con, t, LONG_NS),
                        "longest": [{"ms": round(ms, 1), "call": ids.get(lab_v, lab_v) if lab == "nameId" else lab_v,
                                     "tid": tid} for ms, lab_v, tid in spans]}
            # the calls by name over 10 ms
            if lab:
                rows = con.execute(f"SELECT {lab}, COUNT(*), MAX(end - start), SUM(end - start) FROM '{t}' "
                                   f"WHERE end - start >= {LONG_NS} GROUP BY {lab} ORDER BY SUM(end - start) DESC LIMIT 15").fetchall()
                calls[t]["byCallOver10ms"] = [{"call": ids.get(r[0], r[0]) if lab == "nameId" else r[0], "n": r[1],
                                               "maxMs": round(r[2] / 1e6, 1), "totalMs": round(r[3] / 1e6, 1)} for r in rows]
        rep["api_calls"] = calls
    else:
        rep["missing"].append("D3D12 API calls (no DX12_* table: was --trace=dx12 on, and did nsys launch the game itself?)")
    # CPU sampling: the busiest thread's modules
    samp = [t for t in names if "SAMPL" in t.upper() and "CALLCHAIN" not in t.upper()]
    if samp and "ThreadNames" in names or samp:
        t = samp[0]
        cols = columns(con, t)
        tid = "globalTid" if "globalTid" in cols else ("tid" if "tid" in cols else None)
        if tid:
            rows = con.execute(f"SELECT {tid}, COUNT(*) FROM '{t}' GROUP BY {tid} ORDER BY COUNT(*) DESC LIMIT 5").fetchall()
            rep["sampling"] = {"table": t, "threads": [{"tid": r[0], "samples": r[1]} for r in rows]}
            # the busiest thread's modules, when the samples name an instruction pointer and the export has the modules
            if "ip" in cols and "ModuleInfo" in names or ("ip" in cols and any("MODULE" in n.upper() for n in names)):
                rep["sampling"]["note"] = "per-module shares: see the nsys GUI's Top-Down view, or tools/stallwatch.py"
    else:
        rep["missing"].append("CPU sampling (no SAMPLING_* table: --sample=process-tree may need an admin shell on Windows)")
    con.close()
    if out_json:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, default=str)
    # the printed summary
    print(f"{os.path.basename(sqlite)}: {len(names)} tables")
    if rep["gpu_packets"]:
        for t, v in rep["gpu_packets"].items():
            print(f"  {t}: {v['over10ms']} packets of 10 ms or more; longest " +
                  ", ".join(f"{x['ms']} ms ({x['label']})" for x in v["longest"][:6]))
    if rep["api_calls"]:
        for t, v in rep["api_calls"].items():
            print(f"  {t}: {v['over10ms']} calls of 10 ms or more; by call: " +
                  ", ".join(f"{x['call']} x{x['n']} (max {x['maxMs']} ms)" for x in v.get("byCallOver10ms", [])[:6]))
    if rep["sampling"]:
        print(f"  sampling: {rep['sampling']['table']}, busiest threads " +
              ", ".join(f"{x['tid']} ({x['samples']})" for x in rep["sampling"]["threads"]))
    for m in rep["missing"]:
        print("  missing: " + m)
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sqlite")
    ap.add_argument("--tables", action="store_true", help="the tables and their row counts only")
    args = ap.parse_args()
    if args.tables:
        con = sqlite3.connect(args.sqlite)
        for t in tables(con):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
            except sqlite3.Error:
                n = "?"
            print(f"{n:>10}  {t}: {', '.join(columns(con, t)[:12])}")
        return 0
    base = args.sqlite[:-len(".sqlite")] if args.sqlite.endswith(".sqlite") else args.sqlite
    summarize(args.sqlite, base + ".summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
