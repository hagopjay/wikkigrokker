#!/usr/bin/env python3
"""Export data for the hyperbolic viewer and compute its H3-style layout.

usage: build_hyperball.py SPINE_DIR VITAL_ARTICLES.parquet TRAFFIC_DIR ENWIKI_PARQUET_DIR OUT_DIR

Inputs
  SPINE_DIR        type spine build (classes, edges, article_type parquet)
  VITAL_ARTICLES   dev-slice articles.parquet (Vital Articles with page ids and topics)
  TRAFFIC_DIR      clickstream_traffic.py output (article_traffic, link_flows)
  ENWIKI_PARQUET   page.parquet for article titles

Output (OUT_DIR, renamed from OUT_DIR.tmp only after every check passes)
  viewer_data.json  shown classes with counts, traffic, top articles, Vital topic vectors,
                    alternate parents, reader flows and the layout
  report.json       counts and checks

Layout (after Munzner's H3): every visible class gets a hemisphere radius R from the
areas of its children's discs, bottom up; children are packed in latitude bands on
that hemisphere, largest first at the pole; each child sits at hyperbolic distance R
from its parent. The viewer rebuilds positions from (lat, az, d) per node, relative
to whichever class is in focus, so precision stays high near the focus.
"""
import json
import math
import os
import re
import shutil
import sys
import time
from collections import defaultdict

import duckdb
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "loom"))
from loomlog import log  # noqa: E402

PACK = 1.7      # packing slack on each hemisphere
R_MIN = 0.35    # shortest parent-child distance
SPREAD = 0.55   # default global distance scale shown in the viewer (adjustable there)
TOP_K = 5


def pretty(name, label):
    if label and not re.match(r"^(schema|wm|yago):", label):
        return label
    base = (label or name).split(":", 1)[-1]
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base).split()
    return " ".join([words[0]] + [w.lower() if not w.isupper() else w for w in words[1:]]) if words else base


def lorentz_rot_z(a):
    c, s = math.cos(a), math.sin(a)
    m = np.eye(4); m[0, 0] = c; m[0, 1] = -s; m[1, 0] = s; m[1, 1] = c
    return m


def lorentz_rot_y(a):
    c, s = math.cos(a), math.sin(a)
    m = np.eye(4); m[0, 0] = c; m[0, 2] = s; m[2, 0] = -s; m[2, 2] = c
    return m


def lorentz_boost_z(d):
    m = np.eye(4); ch, sh = math.cosh(d), math.sinh(d)
    m[2, 2] = ch; m[2, 3] = sh; m[3, 2] = sh; m[3, 3] = ch
    return m


def band_pack(thetas, full):
    """Directions (lat, az) for caps of angular radius thetas on a sphere (full) or hemisphere."""
    order = sorted(range(len(thetas)), key=lambda i: -thetas[i])
    out = [None] * len(thetas)
    first = order[0]
    out[first] = (0.0, 0.0)
    edge = thetas[first]
    rest = order[1:]
    band = 0
    golden = math.pi * (3 - math.sqrt(5))
    while rest:
        tmax = thetas[rest[0]]
        lat = edge + tmax
        circ = 2 * math.pi * max(math.sin(min(lat, math.pi - 1e-3)), 1e-3)
        members, used = [], 0.0
        for j in rest:
            need = 2 * thetas[j]
            if members and used + need > circ:
                break
            members.append(j)
            used += need
        span = max(used, circ)
        cum, off = 0.0, band * golden
        for j in members:
            need = 2 * thetas[j]
            out[j] = (lat, off + 2 * math.pi * (cum + need / 2) / span)
            cum += need
        edge = lat + tmax
        rest = rest[len(members):]
        band += 1
    limit = math.pi * (0.97 if full else 0.62)
    top = max(lat for lat, _ in out)
    if top > limit:  # squeeze bands to stay off the parent's side
        out = [(lat * limit / top, az) for lat, az in out]
    return out


def main(spine, vital_pq, traffic_dir, pq_dir, out_dir):
    t0 = time.time()
    tmp = out_dir + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    con = duckdb.connect()
    con.execute("SET memory_limit='5GB'; SET threads=2")
    C, A, E = f"'{spine}/classes.parquet'", f"'{spine}/article_type.parquet'", f"'{spine}/edges.parquet'"
    T, F = f"'{traffic_dir}/article_traffic.parquet'", f"'{traffic_dir}/link_flows.parquet'"
    checks = {}

    # ---- visible classes, ordered so parents come before children ----
    rows = con.execute(f"SELECT id, name, label, kind, parent, depth, primary_count FROM {C} WHERE NOT folded").fetchall()
    allcls = con.execute(f"SELECT id, parent, folded FROM {C}").fetchall()
    shown = {r[0]: r for r in rows}
    kids = defaultdict(list)
    root = None
    for r in rows:
        if r[4] == 0 or r[4] not in shown:
            root = r[0] if r[4] == 0 else root
            continue
        kids[r[4]].append(r[0])
    for k in kids:
        kids[k].sort(key=lambda c: -shown[c][6])
    order, stack = [], [root]
    while stack:
        v = stack.pop()
        order.append(v)
        stack.extend(reversed(kids[v]))
    idx = {cid: i for i, cid in enumerate(order)}
    N = len(order)
    checks["every visible class reachable from the root"] = N == len(rows)
    parent_i = [-1 if v == root else idx[shown[v][4]] for v in order]

    # representative visible class for every class (folded classes roll up)
    par = {c: p for c, p, _ in allcls}
    folded = {c: f for c, _, f in allcls}
    rep = {}

    def rep_of(c):
        path = []
        while c not in rep and folded.get(c, False):
            path.append(c)
            c = par[c]
        r = rep.get(c, c)
        for x in path:
            rep[x] = r
        rep[c] = r
        return r

    nfold = np.zeros(N, dtype=np.int64)
    for c, _, f in allcls:
        if f:
            nfold[idx[rep_of(c)]] += 1
        else:
            rep[c] = c

    # ---- article counts and traffic ----
    own = np.zeros(N, dtype=np.int64)
    views_own = np.zeros(N, dtype=np.float64)
    for dc, n, v in con.execute(f"""SELECT a.display_class, count(*), coalesce(sum(t.views_in), 0)
                                     FROM {A} a LEFT JOIN {T} t USING (page_id)
                                     WHERE a.display_class IS NOT NULL GROUP BY 1""").fetchall():
        own[idx[dc]] = n
        views_own[idx[dc]] = v
    n_sub = own.copy()
    v_sub = views_own.copy()
    for i in range(N - 1, 0, -1):
        n_sub[parent_i[i]] += n_sub[i]
        v_sub[parent_i[i]] += v_sub[i]
    checks["display classes are all visible"] = con.execute(
        f"SELECT count(*) FROM {A} a JOIN {C} c ON c.id = a.display_class WHERE c.folded").fetchone()[0] == 0
    checks["subtree counts equal primary_count for every visible class (additivity)"] = all(
        int(n_sub[idx[v]]) == shown[v][6] for v in order)
    typed_views = con.execute(f"""SELECT coalesce(sum(t.views_in), 0) FROM {A} a JOIN {T} t USING (page_id)
                                  WHERE a.display_class IS NOT NULL""").fetchone()[0]
    all_views = con.execute(f"SELECT sum(views_in) FROM {T}").fetchone()[0]
    checks["traffic rolls up without loss"] = abs(v_sub[0] - typed_views) < 0.5

    # ---- top articles per class (own set), merged up the tree ----
    tops = [[] for _ in range(N)]
    for dc, title, v in con.execute(f"""
            WITH x AS (SELECT a.display_class dc, a.page_id, t.views_in v,
                              row_number() OVER (PARTITION BY a.display_class ORDER BY t.views_in DESC, a.page_id) rn
                       FROM {A} a JOIN {T} t USING (page_id) WHERE a.display_class IS NOT NULL)
            SELECT x.dc, decode(p.page_title), x.v FROM x JOIN '{pq_dir}/page.parquet' p ON p.page_id = x.page_id
            WHERE x.rn <= {TOP_K}""").fetchall():
        tops[idx[dc]].append((title.replace("_", " "), int(v)))
    for i in range(N - 1, -1, -1):
        tops[i] = sorted(tops[i], key=lambda t: -t[1])[:TOP_K]
        if i:
            tops[parent_i[i]] = sorted(tops[parent_i[i]] + tops[i], key=lambda t: -t[1])[:TOP_K]

    # ---- Vital Articles topic shell ----
    vit = con.execute(f"""SELECT v.title, v.topic, coalesce(v.sublist, 'General') sub, v.section, v.level,
                                 a.display_class, coalesce(t.views_in, 0)
                          FROM '{vital_pq}' v LEFT JOIN {A} a ON a.page_id = v.page_id
                          LEFT JOIN {T} t ON t.page_id = v.page_id
                          WHERE v.topic IS NOT NULL""").fetchall()
    topic_n = defaultdict(int)
    for r in vit:
        topic_n[r[1]] += 1
    topics = sorted(topic_n, key=lambda t: -topic_n[t])
    sub_n = defaultdict(int)
    for r in vit:
        sub_n[(r[1], r[2])] += 1
    subl = []
    for tp in topics:
        subs = sorted([s for (t, s) in sub_n if t == tp], key=lambda s: (s == "General", -sub_n[(tp, s)]))
        subl += [(tp, s) for s in subs]
    sidx = {k: i for i, k in enumerate(subl)}
    S = len(subl)
    vec = np.zeros((N, S), dtype=np.int64)
    sub_views = np.zeros(S)
    sub_sections = [defaultdict(int) for _ in range(S)]
    sub_top = [[] for _ in range(S)]
    vital_typed = 0
    for title, tp, s, sec, lvl, dc, v in vit:
        j = sidx[(tp, s)]
        sub_views[j] += v
        if sec:
            sub_sections[j][sec] += 1
        sub_top[j].append((title, int(v), idx[dc] if dc is not None else -1))
        if dc is not None:
            vec[idx[dc], j] += 1
            vital_typed += 1
    for i in range(N - 1, 0, -1):
        vec[parent_i[i]] += vec[i]
    checks["Vital articles roll up to the root without loss"] = int(vec[0].sum()) == vital_typed

    # shell layout: topics take azimuth spans by count, sublists stack in latitude
    gap_az, lat_lo, lat_hi = 0.03, -0.62, 0.62
    total = sum(topic_n.values())
    shell, az = [], 0.0
    usable = 2 * math.pi - gap_az * len(topics)
    for ti, tp in enumerate(topics):
        span = usable * topic_n[tp] / total
        members = [k for k in subl if k[0] == tp]
        lat = lat_hi
        for k in members:
            h = (lat_hi - lat_lo) * sub_n[k] / topic_n[tp]
            shell.append({"topic": ti, "name": k[1], "az0": round(az, 5), "az1": round(az + span, 5),
                          "lat0": round(lat - h, 5), "lat1": round(lat, 5)})
            lat -= h
        az += span + gap_az

    # ---- alternate parents (the DAG behind the tree) ----
    anc_sets = {}

    def ancestors(i):
        if i not in anc_sets:
            s, j = set(), parent_i[i]
            while j >= 0:
                s.add(j)
                j = parent_i[j]
            anc_sets[i] = s
        return anc_sets[i]

    alt = defaultdict(lambda: defaultdict(int))
    alt_edges_seen = alt_kept = 0
    for c, p in con.execute(f"SELECT child, parent FROM {E} WHERE role = 'alternate'").fetchall():
        if c not in par or p not in par:
            continue
        alt_edges_seen += 1
        a, b = idx[rep_of(c)], idx[rep_of(p)]
        if a == b or b in ancestors(a) or a in ancestors(b):
            continue
        alt[a][b] += 1
        alt_kept += 1
    alts = {str(a): sorted(([b, n] for b, n in d.items()), key=lambda x: -x[1])[:12] for a, d in alt.items()}

    # ---- reader flows between visible classes ----
    con.execute(f"""CREATE TABLE fl AS SELECT s.display_class sc, d.display_class dc, sum(f.n) n
                    FROM {F} f JOIN {A} s ON s.page_id = f.src_page_id JOIN {A} d ON d.page_id = f.dst_page_id
                    WHERE s.display_class IS NOT NULL AND d.display_class IS NOT NULL GROUP BY 1, 2""")
    flow_total, flow_self = con.execute("SELECT sum(n), sum(CASE WHEN sc = dc THEN n ELSE 0 END) FROM fl").fetchone()
    flows = [[idx[a], idx[b], int(n)] for a, b, n in con.execute(
        "SELECT sc, dc, n FROM fl WHERE sc <> dc ORDER BY n DESC LIMIT 1500").fetchall()]

    # ---- H3 layout ----
    leaf_r = 0.1 + 0.1 * np.log10(1 + n_sub)  # heavy classes claim more room on their parent's hemisphere
    R = np.zeros(N)
    thetas_of = {}
    for i in range(N - 1, -1, -1):
        ch = [idx[c] for c in kids[order[i]]]
        if not ch:
            R[i] = leaf_r[i]
            continue
        disc = [4 * math.pi * math.sinh(R[c] / 2) ** 2 for c in ch]
        k = 4 * math.pi if i == 0 else 2 * math.pi
        R[i] = max(math.asinh(math.sqrt(PACK * sum(disc) / k)), R_MIN, leaf_r[i])
        sh2 = math.sinh(R[i]) ** 2
        thetas_of[i] = [math.acos(max(-1.0, 1 - 2 * math.sinh(R[c] / 2) ** 2 / sh2)) for c in ch]
    lat = np.zeros(N); azi = np.zeros(N); dist = np.zeros(N)
    for i, v in enumerate(order):
        ch = [idx[c] for c in kids[v]]
        if not ch:
            continue
        for c, (la, a) in zip(ch, band_pack(thetas_of[i], full=(i == 0))):
            lat[c], azi[c], dist[c] = la, a, R[i]
    cap = 0.34 * np.minimum(R, np.where(dist > 0, dist, R))
    cap[0] = 0.22  # the root is a scaffold placeholder: keep it a small seed at the centre
    size_n = np.clip(0.3 + 0.7 * np.log10(1 + n_sub) / math.log10(1 + n_sub.max()), 0.3, 1.0)
    rho = cap * size_n

    # positions with the root in focus, at the default spread (for the static scene and checks)
    frames = [None] * N
    pos = np.zeros((N, 3))
    for i in range(N):
        if i == 0:
            frames[0] = np.eye(4)
        else:
            frames[i] = frames[parent_i[i]] @ lorentz_rot_z(azi[i]) @ lorentz_rot_y(lat[i]) @ lorentz_boost_z(SPREAD * dist[i])
        X = frames[i][:, 3]
        pos[i] = X[:3] / (1 + X[3])
    r = np.linalg.norm(pos, axis=1)
    checks["layout is finite and inside the unit ball"] = bool(np.isfinite(pos).all() and (r < 1).all())
    dup = len({tuple(np.round(p, 9)) for p in pos}) != N
    checks["no two classes share a position"] = not dup

    branch = [0] * N
    for i in range(1, N):
        branch[i] = i if parent_i[i] == 0 else branch[parent_i[i]]

    nodes = {
        "qid": [shown[v][1] if re.match(r"^Q\d+$", shown[v][1] or "") else "" for v in order],
        "label": [pretty(shown[v][1], shown[v][2]) for v in order],
        "kind": [shown[v][3] for v in order],
        "parent": parent_i,
        "depth": [int(shown[v][5]) for v in order],
        "n": [int(x) for x in n_sub],
        "own": [int(x) for x in own],
        "views": [int(x) for x in v_sub],
        "nfold": [int(x) for x in nfold],
        "branch": branch,
        "lat": [round(float(x), 6) for x in lat],
        "az": [round(float(x), 6) for x in azi],
        "d": [round(float(x), 6) for x in dist],
        "rho": [round(float(x), 6) for x in rho],
        "top": tops,
    }
    vital_vec = {str(i): [[int(j), int(vec[i, j])] for j in np.nonzero(vec[i])[0]] for i in range(N) if vec[i].any()}
    shell_out = []
    for j, (tp, s) in enumerate(subl):
        secs = sorted(sub_sections[j].items(), key=lambda x: -x[1])[:5]
        top = sorted(sub_top[j], key=lambda x: -x[1])[:TOP_K]
        shell_out.append({**shell[j], "n": int(sub_n[(tp, s)]), "views": int(sub_views[j]),
                          "sections": [s for s, _ in secs], "top": top})
    meta = {
        "snapshot": "enwiki 2026-10-01 dumps; Wikidata via QLever; clickstream 2026-10",
        "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "articles_total": int(con.execute(f"SELECT count(*) FROM {A}").fetchone()[0]),
        "articles_typed": int(n_sub[0]),
        "classes_total": len(allcls),
        "classes_shown": N,
        "alternate_edges": alt_edges_seen,
        "alternate_links_between_visible": len([1 for d in alt.values() for _ in d]),
        "views_total": int(all_views),
        "views_typed": int(typed_views),
        "flow_clicks_between_typed": int(flow_total),
        "flow_clicks_within_class": int(flow_self),
        "vital_total": len(vit),
        "vital_typed": vital_typed,
        "topics": topics,
        "spread_default": SPREAD,
    }
    data = {"meta": meta, "nodes": nodes, "shell": shell_out, "vital": vital_vec, "alts": alts, "flows": flows}
    with open(os.path.join(tmp, "viewer_data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    checks = {k: bool(v) for k, v in checks.items()}
    report = {"meta": meta, "checks": checks,
              "layout": {"R_root": float(R[0]), "max_dist_from_root": float(np.max([math.acosh(max(1.0, fr[3, 3])) for fr in frames])),
                         "r_max": float(r.max()), "r_median": float(np.median(r))},
              "not_established": ["that the layout has no visual overlaps (needs the Eyes witness)",
                                  "that Vital Articles represent all articles' topics (they cover 49,872 of 7.2M)",
                                  "that the alternates shown are the most meaningful; they are ranked by edge count"],
              "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(tmp, "report.json"), "w") as f:
        json.dump(report, f, indent=1)
    print(json.dumps(report, indent=1))
    if not all(checks.values()):
        log("verdict", "hyperball export FAILED a check; left in .tmp", mark="C", checks=checks)
        sys.exit(1)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.rename(tmp, out_dir)
    log("build", f"hyperball export: {N:,} visible classes, {S} Vital sublists, {len(flows)} flows, "
        f"{meta['alternate_links_between_visible']:,} alternate links", mark="C", checks=checks, out=out_dir)


if __name__ == "__main__":
    if len(sys.argv) != 6:
        sys.exit(__doc__)
    main(*sys.argv[1:])
