#!/usr/bin/env python3
"""Assign branch colors so that branches that sit next to each other in the ball stay distinguishable.

usage: assign_colors.py VIEWER_DATA.json PAIRS.json OUT.json

PAIRS.json is pair_matrix.mjs output: pass/fail of each color pair under the dataviz
validator's all-pairs checks on the dark scene surface. The 8 categorical colors pass
on an adjacent pairlist but not on all pairs, so the assignment uses a spatial pairlist:
two branches are neighbors when some of their classes lie within NEIGHBOR_DEG of each
other as seen from the root. The permutation with no failing neighbor pair and the
least reordering is kept. Remaining branches are neutral (other, meta).
"""
import itertools
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "loom"))
from loomlog import log  # noqa: E402

PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
COLORED = ["Person", "Place", "Creative work", "Organization", "Event", "Taxon", "Intangible", "Product"]
META = {"Wikimedia page", "Unclassified"}
OTHER_HEX, META_HEX = "#c3c2b7", "#898781"
NEIGHBOR_DEG = 20.0


def positions(n, spread):
    N = len(n["label"])

    def rz(a):
        c, s = math.cos(a), math.sin(a); m = np.eye(4); m[0, 0] = c; m[0, 1] = -s; m[1, 0] = s; m[1, 1] = c; return m

    def ry(a):
        c, s = math.cos(a), math.sin(a); m = np.eye(4); m[0, 0] = c; m[0, 2] = s; m[2, 0] = -s; m[2, 2] = c; return m

    def bz(x):
        m = np.eye(4); m[2, 2] = m[3, 3] = math.cosh(x); m[2, 3] = m[3, 2] = math.sinh(x); return m
    F = [None] * N
    P = np.zeros((N, 3))
    for i in range(N):
        F[i] = np.eye(4) if i == 0 else F[n["parent"][i]] @ rz(n["az"][i]) @ ry(n["lat"][i]) @ bz(spread * n["d"][i])
        X = F[i][:, 3]
        P[i] = X[:3] / (1 + X[3])
    return P


def main(data_path, pairs_path, out_path):
    d = json.load(open(data_path))
    n = d["nodes"]
    P = positions(n, d["meta"]["spread_default"])
    U = P[1:] / np.linalg.norm(P[1:], axis=1)[:, None]
    br = np.array(n["branch"][1:])
    tops = [i for i, p in enumerate(n["parent"]) if p == 0]
    by_label = {n["label"][i]: i for i in tops}
    colored = [by_label[x] for x in COLORED]
    ang = {}
    for a, b in itertools.combinations(range(8), 2):
        A, B = U[br == colored[a]], U[br == colored[b]]
        ang[(a, b)] = float(np.degrees(np.arccos(np.clip((A @ B.T).max(), -1, 1))))
    nb = [k for k, v in ang.items() if v < NEIGHBOR_DEG]
    ok = {}
    for p in json.load(open(pairs_path)):
        ok[(p["i"], p["j"])] = ok[(p["j"], p["i"])] = bool(p["pass"])
    best = None
    for perm in itertools.permutations(range(8)):
        bad = [(a, b) for a, b in nb if not ok[(perm[a], perm[b])]]
        score = (len(bad), sum(abs(perm[k] - k) for k in range(8)))
        if best is None or score < best[0]:
            best = (score, perm, bad)
    (violations, _), perm, bad = best
    colors = {}
    for i in tops:
        lab = n["label"][i]
        if lab in COLORED:
            colors[str(i)] = PALETTE[perm[COLORED.index(lab)]]
        else:
            colors[str(i)] = META_HEX if lab in META else OTHER_HEX
    out = {
        "branch_colors": colors,
        "legend": [{"label": n["label"][i], "hex": colors[str(i)],
                    "group": "type" if n["label"][i] in COLORED else ("meta" if n["label"][i] in META else "other")} for i in tops],
        "neighbor_deg": NEIGHBOR_DEG,
        "neighbor_pairs": [[COLORED[a], COLORED[b], round(ang[(a, b)], 1)] for a, b in nb],
        "failing_neighbor_pairs": [[COLORED[a], COLORED[b]] for a, b in bad],
        "surface": "#0d0d0d",
    }
    json.dump(out, open(out_path, "w"), indent=1)
    log("verdict", f"Palette witness: {len(nb)} spatially neighboring branch pairs (< {NEIGHBOR_DEG} deg), "
        f"{violations} fail the dataviz validator's pair checks after assignment", mark="C",
        failing=out["failing_neighbor_pairs"], out=os.path.basename(out_path))
    print(json.dumps(out, indent=1))
    if violations:
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
