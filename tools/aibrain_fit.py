#!/usr/bin/env python3
"""Fit the simplest learned driver: steer, throttle and brake as linear functions of aibrain.features(), from a
recording of the stock AI (or you) driving (aibrain.py --policy stock --record ...). Behaviour cloning in a page:
the point is the loop (record, fit, drive with --policy linear:<model>), not the model. Swap in anything that maps
the same observation to the same three outputs.

  python tools/aibrain_fit.py runs/i25.jsonl -o runs/model.json [--ridge 1.0]
"""
import argparse
import json
import sys

import numpy as np

import aibrain

OUTPUTS = ("steer", "throttle", "brake")


def samples(lines):
    """(X, Y) from recorded JSONL lines: cars on the graph with their inputs."""
    X, Y = [], []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        car = rec.get("car") or {}
        inp = car.get("inputs")
        if not inp or not car.get("road"):
            continue
        X.append(aibrain.features(car))
        Y.append([float(inp.get(k) or 0.0) for k in OUTPUTS])
    return np.array(X, float), np.array(Y, float)


def fit(X, Y, ridge=1.0):
    """{"features", "mean", "std", "weights": {output: [w per feature..., bias]}, "n", "r2"} by ridge regression on
    standardised features."""
    mean, std = X.mean(0), X.std(0)
    std[std < 1e-9] = 1.0
    Z = np.hstack([(X - mean) / std, np.ones((len(X), 1))])
    reg = ridge * np.eye(Z.shape[1])
    reg[-1, -1] = 0.0                       # (no penalty on the bias)
    W = np.linalg.solve(Z.T @ Z + reg, Z.T @ Y)
    pred = Z @ W
    ss = ((Y - Y.mean(0)) ** 2).sum(0)
    r2 = 1 - ((Y - pred) ** 2).sum(0) / np.where(ss > 1e-12, ss, 1.0)
    return {"features": list(aibrain.FEATURES), "mean": mean.tolist(), "std": std.tolist(),
            "weights": {k: W[:, i].tolist() for i, k in enumerate(OUTPUTS)}, "n": int(len(X)),
            "r2": {k: round(float(r2[i]), 3) for i, k in enumerate(OUTPUTS)}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("recording")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--ridge", type=float, default=1.0)
    a = ap.parse_args(argv)
    with open(a.recording, encoding="utf-8") as f:
        X, Y = samples(f)
    if len(X) < 50:
        sys.exit(f"only {len(X)} usable samples in {a.recording}: record longer (10 Hz per car)")
    m = fit(X, Y, a.ridge)
    with open(a.out, "w") as f:
        json.dump(m, f, indent=1)
    print(f"{m['n']} samples, R^2 {m['r2']} -> {a.out}")


if __name__ == "__main__":
    main()
